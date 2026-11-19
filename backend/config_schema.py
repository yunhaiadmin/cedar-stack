"""SystemConfig 数值/开关类配置的类型校验（公益配置 / 经济系统设置共用）。

管理端保存配置原先 str(value) 直接落库，任何类型都收：手续费比例写 -5、
上限写 "abc" 都能存进去，读取侧要么悄悄回退默认值、要么按负数参与计算。
这里给已知数值/开关键声明类型与范围，非法值由调用方转成 400。

约定：空字符串 / None = 未设置（调用方删除该行，读取侧回退默认值）。
"""
from __future__ import annotations

import copy
import json

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")
_BIG = 10**9


class ConfigValueError(ValueError):
    pass


def normalize(key: str, spec: tuple, value: Any, bool_style: str = "word") -> Optional[str]:
    """按 spec 校验并返回要落库的字符串；None 表示「未设置」。

    spec: ("int", lo, hi) | ("float", lo, hi) | ("float", lo, hi, places) | ("bool",) | ("bool", "digit"|"word")
        | ("int_list", lo, hi) | ("str",) | ("enum", (allowed...)) | ("raw",)
    bool_style: "word" → true/false，"digit" → 1/0；Python bool、int 0/1 与
    "1"/"yes"/"on" 等字符串输入一律按风格转换落库（PA-034：原样返回会让
    word 键被严格读口径误读为关）。
    spec 里显式带风格位（("bool", "digit")）时，spec 优先于 bool_style 参数——
    同一个家族里不同键可能用不同存储风格（如告警总开关存 1/0），风格必须跟键走。
    ("str",): 自由字符串，不做类型校验（空串/None → None = 未设置）；
    ("enum", allowed): 白名单枚举，命中才收。
    ("raw",): 原样存库的字符串：str(value) 直接落库，**空串/None 也照存**——
    与 ("str",) 的"空串=未设置（删行）"不同。专为 economy 家族的 str/secret
    键保留：历史行为就是 str(value) 原样存（含空串存行），迁移时一字不变。
    """
    kind = spec[0]
    if kind == "raw":
        # 注意：必须在下面的"空串/None → None"早退之前处理——raw 的空串是要存库的。
        return str(value)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if kind == "bool":
        _style = spec[1] if len(spec) > 1 else bool_style
        if isinstance(value, bool):
            if _style == "digit":
                return "1" if value else "0"
            return "true" if value else "false"
        if isinstance(value, int) and value in (0, 1):
            # PA-034：int 必须按风格转换，原样 str() 会让 word 键落库 "1"、
            # 被严格读口径（只认 "true"）误读为关。
            if _style == "digit":
                return "1" if value == 1 else "0"
            return "true" if value == 1 else "false"
        s = str(value).strip().lower()
        if s in _BOOL_TRUE:
            # PA-034：字符串同样按风格转换，"yes"/"on"/"1" 在 word 键下是开。
            if _style == "digit":
                return "1"
            return "true"
        if s in _BOOL_FALSE:
            if _style == "digit":
                return "0"
            return "false"
        raise ConfigValueError(f"{key} 必须是开关值（true/false 或 1/0）")
    if kind == "str":
        return str(value)
    if kind == "enum":
        allowed = spec[1]
        s = str(value).strip()
        if s in allowed:
            return s
        raise ConfigValueError(f"{key} 必须是 {allowed} 之一")
    if kind == "json_rules":
        # 结构化 JSON 规则（如播放器黑名单）：入参可为 JSON 文本或已解析的
        # list，归一化为 canonical JSON 文本落库；结构非法直接拒绝。
        return normalize_player_blacklist_rules(value)
    if kind == "json_drawer":
        # 官网菜单抽屉的整份结构（导航/渠道/底按钮）：入参可为 JSON 文本
        # 或已解析的 dict，归一化为 canonical JSON 文本落库。
        return normalize_site_drawer_config(value)
    if kind == "json_landing":
        # 官网落地页的整份文案结构（首屏/合作方/设备/展示/卖点）：入参
        # 可为 JSON 文本或已解析的 dict，归一化为 canonical JSON 文本落库。
        return normalize_landing_content(value)
    lo, hi = spec[1], spec[2]
    if kind == "int":
        if isinstance(value, bool):
            raise ConfigValueError(f"{key} 必须是整数")
        if isinstance(value, float):
            if not value.is_integer():
                raise ConfigValueError(f"{key} 必须是整数")
            value = int(value)
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            raise ConfigValueError(f"{key} 必须是整数")
        if not lo <= n <= hi:
            raise ConfigValueError(f"{key} 需在 {lo}~{hi} 之间")
        return str(n)
    if kind == "float":
        if isinstance(value, bool):
            raise ConfigValueError(f"{key} 必须是数字")
        try:
            f = float(str(value).strip())
        except (TypeError, ValueError):
            raise ConfigValueError(f"{key} 必须是数字")
        if f != f or not lo <= f <= hi:  # NaN 也拒绝
            raise ConfigValueError(f"{key} 需在 {lo}~{hi} 之间")
        if len(spec) > 3:
            # 可选第 4 位 places：小数位数上限（发言每条奖励 0.01 级精度用）。
            # 命中时按 Decimal 校验位数并归一化落库（"0.10"→"0.1"、"2.00"→"2"），
            # 读侧按字符串解析 Decimal，不经 float 中转、无二进制浮点误差。
            places = spec[3]
            try:
                d = Decimal(str(value).strip())
            except (InvalidOperation, ValueError):
                raise ConfigValueError(f"{key} 必须是数字")
            if -d.as_tuple().exponent > places:
                raise ConfigValueError(f"{key} 最多 {places} 位小数")
            s = format(d, "f")
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return s or "0"
        return str(value).strip()
    if kind == "int_list":
        parts = [p.strip() for p in str(value).replace("，", ",").split(",") if p.strip()]
        if not parts:
            return None
        out = []
        for p in parts:
            try:
                n = int(p)
            except ValueError:
                raise ConfigValueError(f"{key} 必须是逗号分隔的整数")
            if not lo <= n <= hi:
                raise ConfigValueError(f"{key} 每项需在 {lo}~{hi} 之间")
            out.append(str(n))
        return ",".join(out)
    raise ConfigValueError(f"{key} 类型未知")


# 配置（welfare_admin.WELFARE_CONFIG_KEYS，历史名）里的数值/开关键；未列出的（群 id 列表等）按字符串保存
WELFARE_CONFIG_SCHEMA: dict[str, tuple] = {
    # 公益资格族键（资格码配额/到期保留/活力保号/福利申请/注册送资格）
    # 已随公益资格整族于 2026-11-14 删除；注册送查看凭证两键
    # （register_gift_view_days / register_gift_view_enabled）已随
    # 「注册不送体验」（2026-11-15）删除发放逻辑、就此失效，不再登记。
    "recharge_ratio": ("float", 0.0001, 1000000),
    "recharge_quick_amounts": ("int_list", 1, 1000000),
    "bot_rate_limit_seconds": ("int", 0, 3600),
    "bot_group_rate_limit": ("int", 0, 100000),
    # 频道马甲治理开关：word 风格 ("bool",) 存 "true"/"false"
    # （读侧 tg_bot.policy._is_true 只认 "true"，同 welfare_vitality_renew 口径）；
    # 白名单是逗号分隔的频道 id 列表，原文直存（同 bot_group_ids）；
    # 退群动作是封闭枚举，非法值在端点层 400，不许落库后靠读侧猜。
    "bot_channel_guard_enabled": ("bool",),
    "bot_channel_whitelist": ("str",),
    "bot_leave_action": ("enum", ("none",)),  # cancel_welfare 已随福利服役族于 2026-11-14 删除
    # 2026-10-17 第 4 批 4-4：以下 9 键在 WELFARE_CONFIG_KEYS 里的默认值是 "1"/"0"，
    # 端点现行的 normalize 按 ("bool", "digit") 存 "1"/"0"。风格必须跟键走，
    # 注册为 digit 才能与今天逐字一致（读侧 lottery.py 等是宽容口径，本应无感，
    # 但存字节必须相同）。
    "welfare_require_tg_bind": ("bool", "digit"),
    "lottery_draw_interval_sec": ("int", 30, 86400),
    # chat_points_* 已于 2026-10-23 去重退役（机器人那套群发言积分，
    # 与 points_chat_* 重复），此处不再登记
    # 2026-10-17 第 4 批 4-2：补上 WELFARE_CONFIG_KEYS 里漏登记的 3 个
    # （读侧 lottery.py 有明确口径：bool 默认开、int max(0,·) 钳制）
    "lottery_require_group_member": ("bool", "digit"),
    "lottery_min_account_age_days": ("int", 0, _BIG),
    "lottery_max_joins_per_day": ("int", 0, _BIG),
    # 公告根治（2026-10-31）：置顶开关 digit 风格（读侧 lottery.py 宽容口径，
    # 默认开）；提醒台阶/提前分钟/编辑节流为非负整数，0=关（读侧同口径钳制）。
    "lottery_announce_pin_enabled": ("bool", "digit"),
    "lottery_reminder_step": ("int", 0, _BIG),
    "lottery_final_call_minutes": ("int", 0, _BIG),
    "lottery_announce_edit_min_interval_sec": ("int", 0, 3600),
    # 2026-10-17 第 4 批 4-4：白名单里但此前无 spec 的群 id 列表键（端点现行按原文
    # 存字符串）。注册为 ("str",) 与今天逐字一致：原文直存，None/空串→删行
    # （读侧 _get_str_config 缺行/空行都回默认 ""，等价）。
    "bot_group_ids": ("str",),
    # 观影榜海报日推（tg_bot.rankpush 读侧宽容解析）：开关 word 风格存 true/false
    "bot_rankpush_enabled": ("bool",),
    # 更新入库播报（tg_bot.updatedigest 读侧宽容解析）：总开关 + 攒批间隔分钟
    # + 发送目标（空=发群，填频道 @用户名或 -100 ID 则发频道，原文直存）
    "bot_update_digest_enabled": ("bool",),
    "bot_update_digest_interval_minutes": ("int", 1, 1440),
    "bot_update_digest_chat_id": ("str",),
    # 每日播放排行榜（tg_bot.playrank 读侧宽容解析）：总开关默认关；
    # 发送时间是原文（读侧校验 HH:MM、非法回落 21:30，照
    # bot_rain_schedule 先例）；每榜条数 1~20；三榜分开关默认开；
    # 海报图开关默认开。
    "bot_playrank_enabled": ("bool",),
    "bot_playrank_time": ("str",),
    "bot_playrank_top_n": ("int", 1, 20),
    "bot_playrank_users_enabled": ("bool",),
    "bot_playrank_series_enabled": ("bool",),
    "bot_playrank_movies_enabled": ("bool",),
    "bot_playrank_poster_enabled": ("bool",),
    # 公共池贡献者每日小结（tg_bot.pool_digest 读侧宽容解析）：
    # 总开关默认开；发送时间原文存储、读侧校验 HH:MM 非法回落 21:00。
    "bot_pool_digest_enabled": ("bool",),
    "bot_pool_digest_time": ("str",),
    # 公共池群播报（tg_bot.pool_report 读侧宽容解析）：每晚战报
    # 默认 21:20、每周英雄榜默认周日 20:30，都发群不置顶。
    "bot_pool_report_enabled": ("bool",),
    "bot_pool_report_time": ("str",),
    "bot_pool_hero_enabled": ("bool",),
    "bot_pool_hero_time": ("str",),
    # 主动私聊提醒（tg_bot.proactive）：两个开关默认开（2026-11-08），
    # 合并窗口秒数默认 60。word 风格 ("bool",) 存 true/false。
    # 关时不发、不写已推记录。新求片提醒管理员（tg_bot.features）默认开。
    "tg_notify_store_arrival_enabled": ("bool",),
    "tg_notify_transfer_done_enabled": ("bool",),
    "tg_notify_new_request_enabled": ("bool",),
    "tg_notify_merge_window_sec": ("int", 0, 3600),
    # 许愿到货群报喜（tg_bot.group_cheer）：默认开；关了只发私聊不发群。
    "tg_notify_group_arrival_enabled": ("bool",),
    # 新贡献者群欢迎（tg_bot.group_cheer）：池成员审核通过时发群，
    # 默认开；驳回不发。审核私聊本身走 proactive，无独立总开关
    # （只受「贡献回响」用户偏好门控）。
    "tg_notify_group_pool_welcome_enabled": ("bool",),

    "lottery_group_ids": ("str",),
}

# 告警设置（admin_alerts.update_alert_settings 的 4 个写键）。
# 注意：alert_check_interval_sec 的钳制（60~86400）保留在端点层（业务行为不变），
# 注册表范围与钳制边界对齐；save_config 只是纵深防御。
ALERTS_CONFIG_SCHEMA: dict[str, tuple] = {
    "system_alerts_enabled": ("bool", "digit"),  # 默认关的功能开关保留面板（2026-10-21）
    "alert_check_interval_sec": ("int", 60, 86400),
    "webhook_alert_url": ("str",),
    "webhook_alert_secret": ("str",),
}

# 到期提醒（reminders_admin.REMINDER_CONFIG_KEYS）。
# 2026-10-17 第 4 批 4-4 裁决：expiry_reminder_days 今天的端点**原文存储、从不拒绝**
# （非法档位靠读侧 reminders.py 逐个跳过、空则回落默认 7/3/1）。严格的
# ("int_list", 1, 365) 会把今天能存的值变成 400，是行为变化；宽容归一又会改动
# 存值。零行为变化要求原文直存，故注册为 ("str",)。
REMINDERS_CONFIG_SCHEMA: dict[str, tuple] = {
    "expiry_reminder_days": ("str",),
}

# 优惠券（coupons_admin.update_coupon_settings；reserve_hours 端点层已有
# Pydantic Field(ge=0, le=720)，注册表范围与之一致；0=不自动清理）
COUPONS_CONFIG_SCHEMA: dict[str, tuple] = {
    "coupon_reserve_hours": ("int", 0, 720),
}

# 注册模式（admin.set_registration_mode；mode 只剩 open/closed 两档——
# 注册码已于 2026-11-15 下线，存量 code 值由端点层归一为 open 后才落库，
# 本表枚举不再认 code；registration_code_hint_* 两键随之失效、不再登记；
# ratelimit_max≥1、ratelimit_window≥60 是端点现有校验，上限沿用 _BIG 口径；
# A-112：register_ratelimit_enabled 恢复登记（bool word 风格存 true/false，
# 与 _get_register_ratelimit 的 =="true" 判定一致；env 显式设置时仍接管））
REGISTRATION_CONFIG_SCHEMA: dict[str, tuple] = {
    "registration_mode": ("enum", ("open", "closed")),
    "registration_closed_message": ("str",),
    "register_ratelimit_enabled": ("bool",),
    "register_ratelimit_max": ("int", 1, _BIG),
    "register_ratelimit_window": ("int", 60, _BIG),
}

# 登录失败锁定（F4 黑盒审计修复）：门户 / Emby / 管理端三处密码登录共用
# backend.ratelimit 的 LoginLockoutGuard 执法，阈值全走这五个键。
# 默认值（enabled 开、账号 10 次、IP 30 次、窗口 900s、锁 900s）写在
# ratelimit.login_lockout_config 的回退口径里，本表只管保存时的类型/范围。
LOGIN_LOCKOUT_CONFIG_SCHEMA: dict[str, tuple] = {
    "login_lockout_enabled": ("bool",),
    "login_lockout_account_max_failures": ("int", 1, _BIG),
    "login_lockout_ip_max_failures": ("int", 1, _BIG),
    "login_lockout_window_sec": ("int", 60, _BIG),
    "login_lockout_lock_sec": ("int", 60, _BIG),
}

# Emby 服务器显示名（backend.servers.server_display_name 唯一解析口径）：
# 客户端 System/Info 的 ServerName 与用户端账号卡 server_name 都读它。
# 空 = 回落 env EMBY_SERVER_NAME，再回落内置默认；只改显示名，
# 不参与 node_key 认领与任何按 id 之外的关联。
EMBY_DISPLAY_CONFIG_SCHEMA: dict[str, tuple] = {
    "emby_server_name": ("str",),
}
# 防盗链（r36：play_sign.write_config / write_allowed_referers 迁移到
# config_service 统一编排层。用户 2026-10-17 18:29 拍板：防盗链保留，
# 此前搁置的 play_sign config_service 迁移恢复排期。）
# - play_sign_ttl_seconds 范围 60~86400 与 play_sign.PLAY_TTL_MIN/MAX 同口径；
# - hotlink_protection_enabled 已于 2026-10-21 摘面板（env 治理），不在本表；
# - play_allowed_referers 用 ("raw",)：逗号拼接的归一化域名串，空串也存行
#   （旧 write_allowed_referers 同口径：空白名单落库 ""，读侧空=不检查）。
HOTLINK_CONFIG_SCHEMA: dict[str, tuple] = {
    "play_sign_ttl_seconds": ("int", 60, 86400),
    "play_allowed_referers": ("raw",),
}

# 挂载直读出流（2026-10-30 立项）：总开关默认开、白名单与根映射默认空
# （空＝行为与现行完全一致）；ids/roots 的内容校验在
# mount_serving.write_config（schema 层只管类型），开关 word 风格
# ("bool",) 存 "true"/"false"（同 welfare 口径）。
# probe_budget_ms（2026-11-17）：PlaybackInfo 回落段挂载探测预算，
# 读取唯一实现 mount_serving.probe_budget_ms（热读、默认 2000、
# 读取侧钳制与本 schema 同口径）。纯 SystemConfig 键，不进
# env.example（#1527 先例）。
MOUNT_SERVING_CONFIG_SCHEMA: dict[str, tuple] = {
    "mount_serving_enabled": ("bool",),
    "mount_serving_mount_ids": ("raw",),
    "mount_serving_local_roots": ("raw",),
    "mount_serving_probe_budget_ms": ("int", 100, 10000),
}

# EA 图片流量观测（F5 前置观测，2026-11-18）：只一个总开关，
# 默认开；读取唯一实现 emby_server.image_obs（flush 线程直查 DB
# 刷新，关 = 计数空转、零观测开销）。纯 SystemConfig 键，不进
# env.example（#1527 先例）；观测期满随观测层一并退役。
IMAGE_OBS_CONFIG_SCHEMA: dict[str, tuple] = {
    "emby_image_obs_enabled": ("bool",),
}

# 团队（2026-11-18 重建批 1，用户拍板：定价＝友站三分之一、全部可配）：
# 读取唯一实现 backend/teams.team_config（热读 ttl=0、带代码默认回退）。
# 纯 SystemConfig 键，不进 env.example（#1527 先例）。
# - team_global_price_anchors 用 ("raw",)：JSON 锚点表字符串
#   [{"gb":…,"per_1000gb_fen":…},…]，内容结构校验在 teams.validate_anchors
#   （schema 层只管是字符串），非法表在管理端保存口整批拒绝、不落库。
TEAM_CONFIG_SCHEMA: dict[str, tuple] = {
    "team_purchase_enabled": ("bool",),
    "team_premium_enabled": ("bool",),
    "team_global_min_traffic_gb": ("int", 1, 10**6),
    "team_premium_min_traffic_gb": ("int", 1, 10**6),
    "team_max_traffic_gb": ("int", 1, 10**7),
    "team_premium_price_per_1000gb_fen": ("int", 0, _BIG),
    "team_global_price_anchors": ("raw",),
    "team_sub_account_divisor": ("int", 1, 10**6),
}

# 播放交付编排器（换代线 step 5，2026-11-01）：(用户, 条目)
# 交付计划预计算开关 + 计划 TTL。读取唯一实现 delivery_plan
# .delivery_plan_enabled / plan_ttl_sec（热读、默认开/TTL 默认
# 120 秒，读取侧钳制与本 schema 同口径）；关 = 不建计划、热
# 路径不读计划，选路与现行逐字一致。纯 SystemConfig 键，不
# 进 env.example（#1527 先例）。
DELIVERY_PLAN_CONFIG_SCHEMA: dict[str, tuple] = {
    "delivery_plan_enabled": ("bool",),
    "delivery_plan_ttl_sec": ("int", 30, 3600),
}

# 列表去重缓存（2026-10-18）：Emby 列表接口 dropped 集合的服务端缓存。
# enabled 默认开（纯性能优化、零行为变化）；ttl=0 或关 = 旧路径。
ITEMS_CACHE_CONFIG_SCHEMA: dict[str, tuple] = {
    "items_dedup_cache_ttl_seconds": ("int", 10, 3600),
    "items_dedup_cache_max_entries": ("int", 100, 20000),
}

# 转存调度（2026-10-30）：跨 provider 总开关 + 跨单在途上限 +
# 跨单 aging 提权阈值 + 执行道并发 + 单用户在飞上限 +
# 换绑豁免限频两键。读取唯一实现 transfer_variant
# 的 cross_provider_enabled
# / cross_lane_concurrency / cross_aging_sec / lane_concurrency /
# per_owner_inflight /
# rebind_waiver_window_days / rebind_waiver_max_uses，
# 范围与读取侧钳制同口径。
TRANSFER_CONFIG_SCHEMA: dict[str, tuple] = {
    "cross_provider_transfer_enabled": ("bool",),
    "transfer_cross_lane_concurrency": ("int", 1, 8),
    # 剧集预热（2026-11-01 转存等待压缩 期四）：总开关默认开
    # （站里没用户口径；读取默认兜底在 transfer_variant.
    # series_preheat_enabled，无行=True）；搬字节预热独立日帽
    # 默认 2、钳 [1, 16]（只数 trigger=series_preheat_byte 的行，
    # 与 prefetch 帽分开）；候选集数默认 3、钳 [1, 10]。范围与
    # 读取侧钳制同口径。纯 SystemConfig 键，不进 env.example。
    "transfer_series_preheat_enabled": ("bool",),
    "transfer_series_preheat_daily_max": ("int", 1, 16),
    "transfer_series_preheat_next_count": ("int", 1, 10),
    "transfer_cross_aging_sec": ("int", 60, 86400),
    "transfer_fast_lane_concurrency": ("int", 1, 32),
    "transfer_per_owner_inflight": ("int", 1, 16),
    "transfer_rebind_waiver_window_days": ("int", 1, 365),
    "transfer_rebind_waiver_max_uses": ("int", 1, 20),
    # 换代线地基记账开关（2026-11-01）：交付状态落盘＋块哈希
    # 清单记账，纯记账 fail-open；默认开由 transfer_variant
    # .manifest_accounting_enabled 的读取默认兜底（无行=True），
    # 关时执行面不接线、行为与上线前逐字一致。纯 SystemConfig
    # 键，不进 env.example（#1527 先例）。
    "transfer_manifest_accounting_enabled": ("bool",),
    # 换代线 step 4 执行面接管开关（2026-11-01）：开 = 115 续传
    # 恢复以落盘交付行为准、清单 complete 的整文件 sha1 直接
    # 采信、同内容在跑单调度合并；须与记账开关联动（记账关时
    # 本开关无数据可依、整步回落）。默认开由 transfer_variant
    # .manifest_takeover_enabled 的读取默认兜底。
    "transfer_manifest_takeover_enabled": ("bool",),
    # 类别级熔断（2026-11-01 预见层 v2 第一层）：JSON 规则数组
    # [{provider, error_kind}] 的 canonical 文本。结构校验在
    # transfer_variant.coerce_paused_categories（保存侧 strict、
    # 非法 400），注册为 ("str",) 只管落库形态；空/缺行 = 不暂停。
    "transfer_paused_categories": ("str",),
    # 用户副本额度探测 ok 裁决复用 TTL（2026-11-08，秒）：判定点
    # （PlaybackInfo / video_stream）对健康副本探得 ok 后，裁决落
    # Redis 跨进程复用本秒数，期内同副本再判不再重探（省每次 2 个
    # usercontent 请求）；quota / error 裁决不复用。读取唯一实现
    # transfer_variant._usercopy_probe_ok_reuse_ttl（热读 fail-safe、
    # 无行回默认 300），范围与读取侧钳制 [0, 3600] 同口径，0 = 关。
    "usercopy_probe_ok_reuse_ttl_sec": ("int", 0, 3600),
}

# Drive API 全局限速（2026-10-30）：扫描列举与 Changes 增量共用的
# 全局令牌桶 QPS 上限。读取唯一实现 drive_changes.configured_api_qps
# （热读 + 周期复检），范围与限速器自身钳制 [1, 20] 同口径；
# 无行时读取侧回退默认 8。
DRIVE_API_CONFIG_SCHEMA: dict[str, tuple] = {
    "drive_api_qps": ("float", 1, 20),
    # 谷歌 Drive 绑定开关（2026-11-04 站主亲令：绑定能力保留、现置
    # 关）：word 风格 ("bool",) 存 "true"/"false"；读侧唯一实现
    # user_bindings.drive_binding_enabled——无行/非法一律关，只有
    # "true" 才开。只管新建绑定，已有绑定不受影响。
    "drive_binding_enabled": ("bool",),
}

# 公共贡献池（goal-33 PR-1 地基，2026-11-07）：27 键，读取唯一实现
# 在 emby_server.pool_gate（POOL_CONFIG_DEFAULTS 与本表同口径）；
# 默认值按 25TB 补充节修正值（单盘容量 4.5TB、保底空闲 300GiB、
# 日转存 600GiB），不是 design §3 的旧默认（0 不限 / 20GiB）。
# 字节类上限给到 10**15：4.5TB 已超 _BIG（10**9）量级。
# pool_enabled 默认关由读侧兜底（通审决议 1：分阶段接线期默认关，
# 正式开池由管理员开闸）；激励子家族 pool_reward_* 随 PR-6 注册
# 为独立家族 POOL_REWARD_CONFIG_SCHEMA（本表键集已被 PR-1 测试
# 钉死，扩键原则上另立家族；本体扩键均用户亲批：入池门槛键
# pool_join_min_capacity_bytes（2026-11-10，16 → 17）、大文件
# 分流两键 pool_large_file_* 与选盘 v2 的 8 个权重/扶持键
# （2026-11-10 同日，17 → 27）；校验/存储仍走同一体系）。
POOL_CONFIG_SCHEMA: dict[str, tuple] = {
    "pool_enabled": ("bool",),
    "pool_audience": ("enum", ("all", "whitelist", "subscribers")),
    "pool_join_mode": ("enum", ("apply_review", "admin_assign", "open")),
    "pool_enabled_providers": ("str",),
    "pool_member_capacity_bytes": ("int", 0, 10**15),
    "pool_join_min_capacity_bytes": ("int", 0, 10**15),
    "pool_member_reserve_free_bytes": ("int", 0, 10**15),
    "pool_member_daily_transfer_bytes": ("int", 0, 10**15),
    "pool_member_daily_transfer_jobs": ("int", 0, _BIG),
    "pool_item_max_copies": ("int", 1, 8),
    "pool_copy_idle_evict_days": ("int", 0, 3650),
    "pool_global_concurrency": ("int", 1, 16),
    "pool_enqueue_jitter_sec": ("int", 0, 600),
    "pool_breaker_quota_hits_per_day": ("int", 0, _BIG),
    "pool_breaker_cooldown_sec": ("int", 60, 604800),
    "pool_pick_top_k": ("int", 1, 8),
    # 池候选/成员的个人绑定上限（通审决议 2）：管理员预批资格先行，
    # 见候选/成员资格时改走本键，个人上限（env，默认 3）不变。
    "pool_member_max_bindings_per_user": ("int", 1, 100),
    # 大文件分流（用户 2026-11-10 亲点）：已绑盘用户播大文件且
    # 池有就绪副本时直接播池副本；开关默认开，阈值默认 15GB
    # （十进制字节，下限 1 防 0 阈值把小文件也卷进来）。
    "pool_large_file_routing_enabled": ("bool",),
    "pool_large_file_bytes": ("int", 1, 10**15),
    # 选盘打分 v2 权重（百分比，口径见 pool_gate._score_member）：
    # 五项合计不必为 100，打分按 /100 折算、排序只看相对值。
    "pool_pick_weight_capacity": ("int", 0, 100),
    "pool_pick_weight_daily": ("int", 0, 100),
    "pool_pick_weight_load": ("int", 0, 100),
    "pool_pick_weight_health": ("int", 0, 100),
    "pool_pick_weight_new": ("int", 0, 100),
    # 新盘扶持窗口（天）：0 = 关闭扶持。
    "pool_pick_new_member_boost_days": ("int", 0, 90),
    # 播出选副本打分 v2 权重（百分比，见 pool_gate._serve_score）。
    "pool_serve_weight_capacity": ("int", 0, 100),
    "pool_serve_weight_load": ("int", 0, 100),
}

# 激励子家族 pool_reward_*（goal-33 PR-6，design §11.3）：计量、
# 单价、门槛全进配置；默认值在 pool_gate
# .POOL_REWARD_CONFIG_DEFAULTS（与下表同口径）。独立家族的理由
# 见 POOL_CONFIG_SCHEMA 上方注释（本体键集已钉死）。
POOL_REWARD_CONFIG_SCHEMA: dict[str, tuple] = {
    "pool_reward_enabled": ("bool",),
    # 真发开关（PR-6 任务书新增）：关＝结算只算账落影子行不发钱，
    # 开＝按明细真入账钱包余额；与总开关分开，先影子跑对账再开闸。
    "pool_reward_payout_enabled": ("bool",),
    "pool_reward_period": ("enum", ("monthly", "weekly")),
    # 美金额费率（2026-11-15 改发美金）：float-places 校验小数
    # 位数并归一化落库字符串，读侧 Decimal 解析、无浮点误差。
    "pool_reward_usd_per_play": ("float", 0, 10000, 4),
    "pool_reward_usd_per_active_day": ("float", 0, 10000, 4),
    # 月封顶比例：上限 = 本人当前套餐月价 × 本值（默认 1）。
    "pool_reward_cap_ratio": ("float", 0, 100, 4),
    "pool_reward_min_active_days": ("int", 0, 366),
    "pool_reward_min_counted_plays": ("int", 0, _BIG),
    "pool_reward_play_dedupe_window_sec": ("int", 0, 604800),
    "pool_reward_daily_play_freeze": ("int", 0, _BIG),
    "pool_reward_xp_per_play": ("int", 0, _BIG),
    "pool_reward_xp_cap": ("int", 0, _BIG),
    # 订阅延期：一期恒关（二期能力），键位按 design 留。
    "pool_reward_subscription_enabled": ("bool",),
    "pool_reward_title_enabled": ("bool",),
}

# 巡检子家族（闲置主动清理开关，用户 2026-11-09 亲点）：本体
# POOL_CONFIG_SCHEMA 的键集已被 PR-1 测试钉死，扩键另立
# 家族（同 pool_reward 先例）；默认值在 pool_gate
# .POOL_JANITOR_CONFIG_DEFAULTS，唯一消费方是 pool_janitor。
POOL_JANITOR_CONFIG_SCHEMA: dict[str, tuple] = {
    "pool_idle_evict_proactive_enabled": ("bool",),
    # 热门预热每轮补位上限（等待优化第二层）：0 = 关预热补位，
    # 上限 200 防误配成单轮洪水。
    "pool_refill_max_per_round": ("int", 0, 200),
    # 热门预热目标份数（等待优化第二层）：1–8，与
    # pool_item_max_copies 同上限。
    "pool_refill_prefetch_target_copies": ("int", 1, 8),
}

# 重试子家族（copy_forbidden 换盘重试，2026-11-10 生产定案）：
# 本体 POOL_CONFIG_SCHEMA 键集已钉死，另立家族（同 pool_janitor
# 先例）；默认值在 pool_gate.POOL_RETRY_CONFIG_DEFAULTS，消费方
# 是 pool_gate 门禁/换盘与 transfer_variant 池执行器。
POOL_RETRY_CONFIG_SCHEMA: dict[str, tuple] = {
    # copy_forbidden 终态冷却（秒）：默认 3600（1 小时），不再
    # 无条件 24h；下限 60 防误配成高频重试，上限 86400 不许比
    # 旧口径更长。
    "pool_copy_forbidden_cooldown_sec": ("int", 60, 86400),
    # 单条目换盘总尝试上限：0 = 以可用成员盘试完为界；>0 达到
    # 即终态。上限 64 防误配成无界重试。
    "pool_copy_forbidden_max_attempts": ("int", 0, 64),
    # 条目级源熔断（2026-11-12）：窗口内 forbidden 失败数阈值，
    # 0 = 关阈值条件（只按全员试过触发）；上限 1000 防误配。
    "pool_source_block_max_failures": ("int", 0, 1000),
    # 源熔断计数窗口（秒）：下限 300 防窗口小到数不齐一轮，
    # 上限 7 天。
    "pool_source_block_window_sec": ("int", 300, 604800),
    # 源熔断阻断冷却（秒）：下限 300 防误配成高频重开，上限
    # 7 天防把条目锁死太久（到期有半开探测兜底）。
    "pool_source_block_cooldown_sec": ("int", 300, 604800),
    # 源自愈开关（2026-11-12）：池单 copy_forbidden 时先经 SA
    # 给源文件补 anyone-reader 再重试一次；默认开（代码默认）。
    "pool_source_heal_enabled": ("bool",),
    # 换源上限（2026-11-19）：单条目 copy_forbidden 换盘穷尽后
    # 最多再换几个同作品替代源重试；0 = 关换源（回旧口径）。
    # 上限 16 防同作品重复行很多时无界换源。
    "pool_copy_forbidden_source_max_swaps": ("int", 0, 16),
    # 成员×源盘失败记忆 TTL（天，等待优化第二层）：下限 1 防
    # 0 天记忆当天即过期失去意义，上限 90 防误配成长锁。
    "pool_bad_combo_ttl_days": ("int", 1, 90),
}

# 缺集补集通道（2026-11-11 B 方案）：绕开 MP 订阅判存，按缺集
# 台账走 MP 搜索＋手动下载接口补集。默认值在 mp_episode_fill
# .MP_FILL_CONFIG_DEFAULTS，消费方是 emby_server.mp_episode_fill
# 工人；纯 SystemConfig 键，不进 env.example（#1527 先例）。
MP_EPISODE_FILL_CONFIG_SCHEMA: dict[str, tuple] = {
    # MP（MoviePilot）服务地址（不带 /api/v1，客户端自拼）。
    "mp_download_base": ("str",),
    # MP API token（密钥：只进请求头，绝不进日志/响应明文）。
    "mp_download_token": ("str",),
    # 补集总开关：默认关，试点由管理员在管理端手动开。
    "mp_episode_fill_enabled": ("bool",),
    # 每日最多发起下载数：0 = 不发起（等同关）；上限 500 防
    # 误配成无界打扰 MP。
    "mp_episode_fill_daily_cap": ("int", 0, 500),
    # 同剧同季冷却（小时）：任一结果落账后冷却期内不重复发起。
    "mp_episode_fill_cooldown_hours": ("int", 1, 720),
}




def coerce_player_blacklist_rules(value: Any, *, strict: bool) -> list[dict]:
    """播放器黑名单规则结构归一：[{label, match[], exclude[]}]。

    模式串去首尾空白并转小写（匹配侧大小写不敏感，存小写即所见即所得）。
    strict=True（保存校验）：任一结构问题抛 ConfigValueError；
    strict=False（运行时读已存值）：跳过坏规则/坏项，尽量不因一条坏配置
    把匹配整体打挂。
    """
    if not isinstance(value, list):
        if strict:
            raise ConfigValueError("player_login_blacklist 必须是规则数组")
        return []
    rules: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            if strict:
                raise ConfigValueError("player_login_blacklist 每条规则必须是对象")
            continue

        def _patterns(field: str) -> list[str]:
            raw = item.get(field)
            if raw is None:
                return []
            if not isinstance(raw, list) or any(not isinstance(p, str) for p in raw):
                if strict:
                    raise ConfigValueError(
                        f"player_login_blacklist 规则的 {field} 必须是字符串数组")
                return []
            return [p.strip().lower() for p in raw if p.strip()]

        label = item.get("label")
        rules.append({
            "label": str(label) if label is not None else "",
            "match": _patterns("match"),
            "exclude": _patterns("exclude"),
        })
    return rules


def normalize_player_blacklist_rules(value: Any) -> str:
    """保存侧校验入口：JSON 文本或已解析 list → canonical JSON 文本。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ConfigValueError("player_login_blacklist 必须是合法的 JSON 数组")
    rules = coerce_player_blacklist_rules(value, strict=True)
    return json.dumps(rules, ensure_ascii=False)


# 播放器登录黑名单（2026-10-30）：规则数组整体走 json_rules 校验，
# 结构为 [{label, match[], exclude[]}]（backend/emby_server/player_blacklist.py）。
# 空串/None = 删行回代码级默认名单；空数组 [] 存行 = 管理员主动清空（功能停用）。
PLAYER_BLACKLIST_CONFIG_SCHEMA: dict[str, tuple] = {
    "player_login_blacklist": ("json_rules",),
}


# ---------------------------------------------------------------------------
# 官网菜单抽屉（2026-11-18）：整个抽屉一份 JSON 存 site_drawer_config：
# {nav_items[], channels_title, channels[], cta{}}。导航/渠道的顺序即
# 数组顺序（后台编辑器上下移就是改数组），条目各自带 enabled 开关。
# 图标名与前端线性图标组件一一对应，未知值保存侧拒绝、读侧回落。
# ---------------------------------------------------------------------------
SITE_DRAWER_ICONS: tuple[str, ...] = (
    "home", "tag", "book", "bell", "users", "bot", "message", "send",
    "globe", "login",
)
SITE_DRAWER_MAX_ITEMS = 12
SITE_DRAWER_DEFAULT_CHANNELS_TITLE = "社区渠道"
SITE_DRAWER_NAV_ICON_FALLBACK = "home"
SITE_DRAWER_CHANNEL_ICON_FALLBACK = "message"


def site_drawer_url_ok(url: str, *, allow_internal: bool) -> bool:
    """抽屉链接形状：http(s) 外链恒可；站内路径须单斜杠开头（防 // 协议
    相对地址绕到外站）、不含反斜杠与空白。空串不经此函数判定（调用方
    按各自语义处理：导航/渠道=不下发，底按钮=内置入口行为）。"""
    if url.startswith("https://") or url.startswith("http://"):
        return True
    if not allow_internal:
        return False
    return (
        url.startswith("/")
        and not url.startswith("//")
        and "\\" not in url
        and not any(ch.isspace() for ch in url)
    )


def _drawer_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
    return default


def _drawer_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _drawer_items(raw: Any, *, strict: bool, field: str) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        if strict:
            raise ConfigValueError(f"site_drawer_config.{field} 必须是数组")
        return []
    if len(raw) > SITE_DRAWER_MAX_ITEMS:
        if strict:
            raise ConfigValueError(
                f"site_drawer_config.{field} 最多 {SITE_DRAWER_MAX_ITEMS} 条")
        raw = raw[:SITE_DRAWER_MAX_ITEMS]
    return raw


def _drawer_link_item(item: Any, *, strict: bool, field: str,
                      label_field: str, icon_fallback: str,
                      allow_internal: bool) -> Optional[dict]:
    """导航/渠道条目归一；strict 时坏条目抛错，读侧（非 strict）跳过坏条目。
    链接为空串保留条目（管理员可先存草稿），下发侧再按空链接过滤。"""
    if not isinstance(item, dict):
        if strict:
            raise ConfigValueError(f"site_drawer_config.{field} 每条必须是对象")
        return None
    label = _drawer_text(item.get(label_field), 40)
    if not label:
        if strict:
            raise ConfigValueError(
                f"site_drawer_config.{field} 的{label_field}不能为空")
        return None
    url = _drawer_text(item.get("url"), 500)
    if url and not site_drawer_url_ok(url, allow_internal=allow_internal):
        if strict:
            raise ConfigValueError(
                f"site_drawer_config.{field}「{label}」的链接不合法"
                "（只接受 http(s):// 或站内 / 路径）")
        # 读侧保留条目与原文，下发侧 public_info 再按同一口径过滤，
        # 保证管理员在后台能看到并修掉这条坏链接。
    icon = _drawer_text(item.get("icon"), 20)
    if icon not in SITE_DRAWER_ICONS:
        if strict:
            raise ConfigValueError(
                f"site_drawer_config.{field}「{label}」的图标必须是 "
                f"{SITE_DRAWER_ICONS} 之一")
        icon = icon_fallback
    out = {
        "icon": icon,
        label_field: label,
        "url": url,
        "enabled": _drawer_bool(item.get("enabled"), True),
    }
    if field == "nav_items":
        out["external"] = _drawer_bool(item.get("external"), False)
    else:
        out["subtitle"] = _drawer_text(item.get("subtitle"), 80)
    return out


def coerce_site_drawer_config(value: Any, *, strict: bool) -> Optional[dict]:
    """抽屉整份结构归一。strict=True（保存校验）：任一结构问题抛
    ConfigValueError；strict=False（运行时读已存值）：整体坏掉（非对象/
    JSON 解析失败）返回 None 由调用方回落默认，单条坏条目跳过或回落
    图标，尽量不让一条坏配置把整个抽屉打挂。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            if strict:
                raise ConfigValueError("site_drawer_config 必须是合法的 JSON")
            return None
    if not isinstance(value, dict):
        if strict:
            raise ConfigValueError("site_drawer_config 必须是对象")
        return None

    nav_items: list[dict] = []
    for item in _drawer_items(value.get("nav_items"), strict=strict,
                              field="nav_items"):
        coerced = _drawer_link_item(
            item, strict=strict, field="nav_items", label_field="label",
            icon_fallback=SITE_DRAWER_NAV_ICON_FALLBACK, allow_internal=True)
        if coerced is not None:
            nav_items.append({
                "icon": coerced["icon"], "label": coerced["label"],
                "url": coerced["url"], "external": coerced["external"],
                "enabled": coerced["enabled"],
            })

    channels: list[dict] = []
    for item in _drawer_items(value.get("channels"), strict=strict,
                              field="channels"):
        coerced = _drawer_link_item(
            item, strict=strict, field="channels", label_field="title",
            icon_fallback=SITE_DRAWER_CHANNEL_ICON_FALLBACK,
            allow_internal=False)
        if coerced is not None:
            channels.append({
                "icon": coerced["icon"], "title": coerced["title"],
                "subtitle": coerced["subtitle"], "url": coerced["url"],
                "enabled": coerced["enabled"],
            })

    raw_title = value.get("channels_title")
    channels_title = (
        SITE_DRAWER_DEFAULT_CHANNELS_TITLE if raw_title is None
        else _drawer_text(raw_title, 20)
    )

    raw_cta = value.get("cta")
    if raw_cta is None:
        raw_cta = {}
    if not isinstance(raw_cta, dict):
        if strict:
            raise ConfigValueError("site_drawer_config.cta 必须是对象")
        raw_cta = {}
    cta_url = _drawer_text(raw_cta.get("url"), 500)
    if cta_url and not site_drawer_url_ok(cta_url, allow_internal=True):
        if strict:
            raise ConfigValueError(
                "site_drawer_config.cta 的链接不合法"
                "（只接受 http(s):// 或站内 / 路径，留空 = 内置入口）")
        cta_url = ""
    cta = {
        "label": _drawer_text(raw_cta.get("label"), 40) or "进入后台",
        "url": cta_url,
        "enabled": _drawer_bool(raw_cta.get("enabled"), True),
    }

    return {
        "nav_items": nav_items,
        "channels_title": channels_title,
        "channels": channels,
        "cta": cta,
    }


def normalize_site_drawer_config(value: Any) -> str:
    """保存侧校验入口：JSON 文本或已解析 dict → canonical JSON 文本。"""
    coerced = coerce_site_drawer_config(value, strict=True)
    return json.dumps(coerced, ensure_ascii=False)


# 官网菜单抽屉（2026-11-18）：整抽屉一份 JSON（导航/社区渠道/底按钮），
# 后台「系统设置 → 官网菜单抽屉」卡片编辑，公开端点 /api/site/drawer
# 只下发启用且链接合法的项。纯 SystemConfig 键，不进 env.example。
SITE_DRAWER_CONFIG_SCHEMA: dict[str, tuple] = {
    "site_drawer_config": ("json_drawer",),
}

# 隐私政策正文（2026-11-19 二期缺口项 8）：整份 Markdown 存单键
# privacy_policy_md，后台「系统设置 → 隐私政策」卡片编辑，公开端点
# /api/site/privacy-policy 下发；("str",) 口径——空串/None = 未设置
# （删行），用户端回落现行写死正文。纯 SystemConfig 键，不进
# env.example（#1527 先例）。
PRIVACY_POLICY_CONFIG_SCHEMA: dict[str, tuple] = {
    "privacy_policy_md": ("str",),
}


# ---------------------------------------------------------------------------
# 官网落地页文案（2026-11-19 二期部分缺）：落地页全部可编辑文本一份
# JSON 存 landing_content，结构与 user_frontend LandingView 的渲染
# 形状一一对应：hero（主标题行/轮换副文案/滚动提示/规格 chip）、
# partners（节眉题/平台名单）、devices（节头/跑马灯词表）、showcase
# （影院/音频两块）、essentials（节头/卖点三卡）。
#
# 版式骨架不是文案、不进配置：卖点卡恒 3 张（宽卡＋两卡的栅格与各自
# 的插图/数据行位置写死在前端），卡内设备示意/时间轴标注是插图家具，
# 数据行走 /api/site/library-stats 实时统计——都不在这份结构里。
# LANDING_DEFAULT_CONTENT 是现行写死文案的逐字转写：未存配置时后端
# 默认下发与前端内置回落都用它，改文案只能经后台保存，不许改默认值
# 偷换现行文案。
# ---------------------------------------------------------------------------
LANDING_CARD_COUNT = 3
LANDING_MAX_TITLE_LINES = 3
LANDING_MAX_SUBS = 8
LANDING_MAX_SPECS = 4
LANDING_MAX_PARTNERS = 10
LANDING_MAX_MARQUEE = 24
LANDING_MAX_CHIPS = 8
LANDING_MAX_POINTS = 5

LANDING_DEFAULT_CONTENT: dict[str, Any] = {
    "hero": {
        "title_lines": ["你的私人影院", "随时随地开场"],
        "subs": [
            "无广告干扰，沉浸观影从按下播放开始",
            "手机、电视、电脑，观看进度多端同步",
            "新片自动入库，追剧更新不用催",
        ],
        "scroll_hint": "↓ 向下探索更多精彩",
        "specs": [
            {"value": "4K", "sub": "超高清"},
            {"value": "HDR10+", "sub": "高动态范围"},
            {"value": "Dolby Vision", "sub": "杜比视界"},
            {"value": "无广告干扰", "sub": "沉浸体验"},
        ],
    },
    "partners": {
        "eyebrow": "STREAMING PARTNERS",
        "names": ["Netflix", "Disney+", "HBO Max"],
    },
    "devices": {
        "eyebrow": "CLIENTS & SPECS",
        "title_lines": ["在你顺手的设备上，", "以它最好的规格播放"],
        "sub": "播放交给专业客户端，服务器只做整理与供给——手机、电视、电脑，打开就能看。",
        "marquee_clients": [
            "Emby", "Infuse", "Kodi", "Apple TV", "Android TV",
            "Samsung TV", "LG webOS", "Windows", "macOS", "iOS", "Android",
        ],
        "marquee_specs": [
            "4K Ultra HD", "HDR10+", "Dolby Vision", "Dolby Atmos",
            "DTS:X", "H.265 / HEVC", "Hi-Res Audio", "空间音频",
        ],
    },
    "showcase": {
        "cinema": {
            "eyebrow": "CINEMA EXPERIENCE",
            "title": "影院级画面",
            "sub": "4K Ultra HD · HDR10+ · Dolby Vision",
        },
        "audio": {
            "eyebrow": "AUDIO EXPERIENCE",
            "title": "影院级声音",
            "sub": "Dolby Atmos · Hi-Res Audio · 空间音频 · DTS:X 全兼容",
        },
    },
    "essentials": {
        "eyebrow": "SERVICE ESSENTIALS",
        "title": "一份订阅，全部搞定",
        "sub": "一个账号打通所有设备与服务，不用为每块屏幕单独折腾。",
        "cards": [
            {
                "eyebrow": "MULTI-DEVICE SYNC",
                "title_lines": ["一部片子，", "所有屏幕接着看"],
                "desc": "在电视上看到一半，出门用手机继续。播放进度、收藏与观看记录，在你的所有设备间自动同步。",
                "chips": ["TV", "Computer", "Tablet", "手机"],
                "points": [],
            },
            {
                "eyebrow": "ZERO INTERRUPTION",
                "title_lines": ["没有广告，", "只有电影"],
                "desc": "从按下播放到片尾字幕，全程无插播、无贴片，沉浸感不被打断。",
                "chips": [],
                "points": [],
            },
            {
                "eyebrow": "EVER-GROWING LIBRARY",
                "title_lines": ["片库自己长大"],
                "desc": "新片自动扫描入库、刮削补齐海报与资料；追的剧更新了，自动出现在你的片库里。",
                "chips": [],
                "points": [
                    {"title": "自动入库", "text": "新片到盘，自动扫描进片库"},
                    {"title": "资料补齐", "text": "海报、简介与评分自动刮削归位"},
                    {"title": "追剧更新", "text": "追的剧一出新集，自动跟上"},
                ],
            },
        ],
    },
}

def _lc_section(raw: Any, *, strict: bool, field: str) -> dict:
    """分区对象归一：缺失 = 空对象（字段级再回落默认）；类型不对
    strict 抛错、读侧按空对象处理。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        if strict:
            raise ConfigValueError(f"landing_content.{field} 必须是对象")
        return {}
    return raw


def _lc_text(raw: dict, key: str, default: str, limit: int, *,
             strict: bool, field: str, required: bool = False) -> str:
    """单段文本归一。缺失（None）回落默认；显式空串在非必填时保留
    （管理员可主动清空眉题这类装饰文本）；必填空串 strict 抛错、
    读侧回落默认。超长 strict 抛错、读侧回落默认（不截断成半句话）。"""
    value = raw.get(key)
    if value is None:
        if required and strict:
            raise ConfigValueError(f"landing_content.{field} 不能为空")
        return default
    if not isinstance(value, str):
        if strict:
            raise ConfigValueError(f"landing_content.{field} 必须是文本")
        return default
    text = value.strip()
    if not text:
        if required:
            if strict:
                raise ConfigValueError(f"landing_content.{field} 不能为空")
            return default
        return ""
    if len(text) > limit:
        if strict:
            raise ConfigValueError(f"landing_content.{field} 最多 {limit} 字")
        return default
    return text


def _lc_lines(raw: dict, key: str, default: list, *, strict: bool,
              field: str, limit_each: int, max_items: int,
              min_items: int = 0) -> list:
    """短文本数组归一（标题行/轮换副文案/名单/词表/chip）：空条目
    两侧都丢弃；条数越界 strict 抛错、读侧截断或整表回落默认。"""
    value = raw.get(key)
    if value is None:
        if min_items and strict:
            raise ConfigValueError(
                f"landing_content.{field} 至少 {min_items} 条")
        return copy.deepcopy(default)
    if not isinstance(value, list):
        if strict:
            raise ConfigValueError(f"landing_content.{field} 必须是数组")
        return copy.deepcopy(default)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            if strict:
                raise ConfigValueError(
                    f"landing_content.{field} 每条必须是文本")
            continue
        text = item.strip()
        if not text:
            continue
        if len(text) > limit_each:
            if strict:
                raise ConfigValueError(
                    f"landing_content.{field} 每条最多 {limit_each} 字")
            continue
        out.append(text)
    if len(out) > max_items:
        if strict:
            raise ConfigValueError(
                f"landing_content.{field} 最多 {max_items} 条")
        out = out[:max_items]
    if len(out) < min_items:
        if strict:
            raise ConfigValueError(
                f"landing_content.{field} 至少 {min_items} 条")
        return copy.deepcopy(default)
    return out


def _lc_pairs(raw: dict, key: str, default: list, *, strict: bool,
              field: str, keys: tuple[str, str], limit_each: int,
              max_items: int) -> list:
    """双字段对象数组归一（hero 规格 chip 的 value/sub、卖点卡要点的
    title/text）：两字段都必填非空，坏条目 strict 抛错、读侧跳过。"""
    value = raw.get(key)
    if value is None:
        return copy.deepcopy(default)
    if not isinstance(value, list):
        if strict:
            raise ConfigValueError(f"landing_content.{field} 必须是数组")
        return copy.deepcopy(default)
    if len(value) > max_items:
        if strict:
            raise ConfigValueError(
                f"landing_content.{field} 最多 {max_items} 条")
        value = value[:max_items]
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            if strict:
                raise ConfigValueError(
                    f"landing_content.{field} 每条必须是对象")
            continue
        pair: dict[str, str] = {}
        bad = False
        for k in keys:
            v = item.get(k)
            if not isinstance(v, str) or not v.strip():
                if strict:
                    raise ConfigValueError(
                        f"landing_content.{field} 每条的 {k} 不能为空")
                bad = True
                break
            if len(v.strip()) > limit_each:
                if strict:
                    raise ConfigValueError(
                        f"landing_content.{field} 每条的 {k} 最多 {limit_each} 字")
                bad = True
                break
            pair[k] = v.strip()
        if not bad:
            out.append(pair)
    return out


def _lc_showcase_block(raw: Any, default: dict, *, strict: bool,
                       field: str) -> dict:
    block = _lc_section(raw, strict=strict, field=field)
    return {
        "eyebrow": _lc_text(block, "eyebrow", default["eyebrow"], 40,
                            strict=strict, field=f"{field}.eyebrow"),
        "title": _lc_text(block, "title", default["title"], 30,
                          strict=strict, field=f"{field}.title",
                          required=True),
        "sub": _lc_text(block, "sub", default["sub"], 80,
                        strict=strict, field=f"{field}.sub"),
    }


def _lc_card(raw: Any, default: dict, *, strict: bool, field: str) -> dict:
    if not isinstance(raw, dict):
        if strict:
            raise ConfigValueError(f"landing_content.{field} 必须是对象")
        return copy.deepcopy(default)
    return {
        "eyebrow": _lc_text(raw, "eyebrow", default["eyebrow"], 40,
                            strict=strict, field=f"{field}.eyebrow",
                            required=True),
        "title_lines": _lc_lines(
            raw, "title_lines", default["title_lines"], strict=strict,
            field=f"{field}.title_lines", limit_each=40,
            max_items=LANDING_MAX_TITLE_LINES, min_items=1),
        "desc": _lc_text(raw, "desc", default["desc"], 200,
                         strict=strict, field=f"{field}.desc",
                         required=True),
        "chips": _lc_lines(
            raw, "chips", default["chips"], strict=strict,
            field=f"{field}.chips", limit_each=24,
            max_items=LANDING_MAX_CHIPS),
        "points": _lc_pairs(
            raw, "points", default["points"], strict=strict,
            field=f"{field}.points", keys=("title", "text"),
            limit_each=80, max_items=LANDING_MAX_POINTS),
    }


def coerce_landing_content(value: Any, *, strict: bool) -> Optional[dict]:
    """落地页文案整份结构归一。strict=True（保存校验）：结构问题抛
    ConfigValueError；strict=False（运行时读已存值）：整体坏掉返回
    None 由调用方回落默认，单字段坏掉回落该字段的现行默认文案——
    一条坏配置不许让官网开天窗。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            if strict:
                raise ConfigValueError("landing_content 必须是合法的 JSON")
            return None
    if not isinstance(value, dict):
        if strict:
            raise ConfigValueError("landing_content 必须是对象")
        return None

    d = LANDING_DEFAULT_CONTENT

    hero_raw = _lc_section(value.get("hero"), strict=strict, field="hero")
    hero = {
        "title_lines": _lc_lines(
            hero_raw, "title_lines", d["hero"]["title_lines"],
            strict=strict, field="hero.title_lines", limit_each=40,
            max_items=LANDING_MAX_TITLE_LINES, min_items=1),
        "subs": _lc_lines(
            hero_raw, "subs", d["hero"]["subs"], strict=strict,
            field="hero.subs", limit_each=80,
            max_items=LANDING_MAX_SUBS, min_items=1),
        "scroll_hint": _lc_text(
            hero_raw, "scroll_hint", d["hero"]["scroll_hint"], 40,
            strict=strict, field="hero.scroll_hint"),
        "specs": _lc_pairs(
            hero_raw, "specs", d["hero"]["specs"], strict=strict,
            field="hero.specs", keys=("value", "sub"), limit_each=24,
            max_items=LANDING_MAX_SPECS),
    }

    partners_raw = _lc_section(
        value.get("partners"), strict=strict, field="partners")
    partners = {
        "eyebrow": _lc_text(
            partners_raw, "eyebrow", d["partners"]["eyebrow"], 40,
            strict=strict, field="partners.eyebrow"),
        "names": _lc_lines(
            partners_raw, "names", d["partners"]["names"], strict=strict,
            field="partners.names", limit_each=32,
            max_items=LANDING_MAX_PARTNERS),
    }

    devices_raw = _lc_section(
        value.get("devices"), strict=strict, field="devices")
    devices = {
        "eyebrow": _lc_text(
            devices_raw, "eyebrow", d["devices"]["eyebrow"], 40,
            strict=strict, field="devices.eyebrow"),
        "title_lines": _lc_lines(
            devices_raw, "title_lines", d["devices"]["title_lines"],
            strict=strict, field="devices.title_lines", limit_each=40,
            max_items=LANDING_MAX_TITLE_LINES, min_items=1),
        "sub": _lc_text(
            devices_raw, "sub", d["devices"]["sub"], 160,
            strict=strict, field="devices.sub"),
        "marquee_clients": _lc_lines(
            devices_raw, "marquee_clients", d["devices"]["marquee_clients"],
            strict=strict, field="devices.marquee_clients", limit_each=32,
            max_items=LANDING_MAX_MARQUEE),
        "marquee_specs": _lc_lines(
            devices_raw, "marquee_specs", d["devices"]["marquee_specs"],
            strict=strict, field="devices.marquee_specs", limit_each=32,
            max_items=LANDING_MAX_MARQUEE),
    }

    showcase_raw = _lc_section(
        value.get("showcase"), strict=strict, field="showcase")
    showcase = {
        "cinema": _lc_showcase_block(
            showcase_raw.get("cinema"), d["showcase"]["cinema"],
            strict=strict, field="showcase.cinema"),
        "audio": _lc_showcase_block(
            showcase_raw.get("audio"), d["showcase"]["audio"],
            strict=strict, field="showcase.audio"),
    }

    ess_raw = _lc_section(
        value.get("essentials"), strict=strict, field="essentials")
    cards_raw = ess_raw.get("cards")
    card_defaults = d["essentials"]["cards"]
    if not isinstance(cards_raw, list) or len(cards_raw) != LANDING_CARD_COUNT:
        if strict:
            raise ConfigValueError(
                f"landing_content.essentials.cards 必须恰好 {LANDING_CARD_COUNT} 张"
                "（版式固定三卡，只改文案）")
        cards = copy.deepcopy(card_defaults)
    else:
        cards = [
            _lc_card(item, card_defaults[i], strict=strict,
                     field=f"essentials.cards[{i}]")
            for i, item in enumerate(cards_raw)
        ]
    essentials = {
        "eyebrow": _lc_text(
            ess_raw, "eyebrow", d["essentials"]["eyebrow"], 40,
            strict=strict, field="essentials.eyebrow"),
        "title": _lc_text(
            ess_raw, "title", d["essentials"]["title"], 40,
            strict=strict, field="essentials.title", required=True),
        "sub": _lc_text(
            ess_raw, "sub", d["essentials"]["sub"], 160,
            strict=strict, field="essentials.sub"),
        "cards": cards,
    }

    return {
        "hero": hero,
        "partners": partners,
        "devices": devices,
        "showcase": showcase,
        "essentials": essentials,
    }


def normalize_landing_content(value: Any) -> str:
    """保存侧校验入口：JSON 文本或已解析 dict → canonical JSON 文本。"""
    coerced = coerce_landing_content(value, strict=True)
    return json.dumps(coerced, ensure_ascii=False)


# 官网落地页文案（2026-11-19 二期）：整页可编辑文本一份 JSON
# （首屏/合作方/设备/展示/卖点），后台「官网内容」页编辑，公开端点
# /api/site/landing 下发整份（未存时下发默认 = 现行写死文案）。
# 纯 SystemConfig 键，不进 env.example。
LANDING_CONTENT_SCHEMA: dict[str, tuple] = {
    "landing_content": ("json_landing",),
}

# 网页软解开关（2026-11-14 批 2）：浏览器硬解失败时是否用站内自编
# LGPL wasm 解码器兜底。默认关（无行 = 关），纯 SystemConfig 键，
# 不进 env.example（#1527 先例）。开关只影响网页播放路径。
WEB_SOFTDECODE_CONFIG_SCHEMA: dict[str, tuple] = {
    # word 风格 ("bool",) 存 "true"/"false"（同 welfare 口径）。
    "web_softdecode_enabled": ("bool",),
}

# 站内弹幕（2026-11-15）：网页播放器的站内自发弹幕。全部可调项走
# SystemConfig、后台「弹幕设置」卡片可改，代码只留默认值：
# 开关默认关（无行 = 关）；敏感词表逗号分隔、默认空 = 不过滤；
# 最小发送间隔默认 5 秒；单条字数上限默认 100（上限 500 与落库列宽一致）。
DANMAKU_CONFIG_SCHEMA: dict[str, tuple] = {
    # word 风格 ("bool",) 存 "true"/"false"（同 welfare 口径）。
    "danmaku_enabled": ("bool",),
    "danmaku_blocked_words": ("str",),
    "danmaku_min_interval_seconds": ("int", 0, 3600),
    "danmaku_max_text_length": ("int", 1, 500),
}

# 弹幕 API（2026-11-18 能力单）：给第三方播放器填的专职弹幕地址，
# 与站内弹幕（danmaku 家族）是两个功能、另立家族。线路表整份
# JSON 存一个键（[{id,name,note,host}]，空 = 代码默认两条），
# 逐项形状校验在端点层（同 install_app 先例）；外部弹幕源开关
# 默认关（无行 = 关），上游地址键默认空 = 未配置（2026-11-19
# 接 LogVar 弹幕 API，地址含 token 路径、只存后台）。
DANMU_API_CONFIG_SCHEMA: dict[str, tuple] = {
    "danmu_api_lines": ("str",),
    # word 风格 ("bool",) 存 "true"/"false"（同 danmaku 口径）。
    "danmu_api_external_enabled": ("bool",),
    "danmu_api_external_url": ("str",),
    # 外部源单步请求超时（秒）：上游 match 一步实测就要 8 秒上下，
    # 旧固定 4 秒让外部弹幕永远超时；默认 15，保存侧范围 3–60，
    # 读侧同范围钳制（防脏值让请求必死或无限等）。
    "danmu_api_external_timeout_seconds": ("int", 3, 60),
}

# 安装应用页（2026-11-13）：6 个平台的官方客户端下载链接，全部 ("str",)。
# 没有代码级默认链接：空串/None = 删行取消配置，未配的平台用户端整行
# 不显示。链接的 http(s) 形状校验在端点层（保存侧纵深防御只管类型，
# 与其它 str 家族口径一致）。
INSTALL_APP_CONFIG_SCHEMA: dict[str, tuple] = {
    "install_app_ios_url": ("str",),
    "install_app_android_url": ("str",),
    "install_app_android_tv_url": ("str",),
    "install_app_apple_tv_url": ("str",),
    "install_app_windows_url": ("str",),
    "install_app_macos_url": ("str",),
}

# （原 ECONOMY_INT_RANGES / economy_spec 已并入下面的 ECONOMY_CONFIG_SCHEMA，
# 4-6 迁移后不再有任何调用方，此处删除。）


# 经济系统设置（admin_economy.ECONOMY_CONFIG_KEYS，共 49 键，2026-10-17 实查）。
# spec 口径与迁移前的 economy_spec() 历史行为逐字一致（4-6 已将其并入此表删除）：
# - int：原 ECONOMY_INT_RANGES 里的 4 个用其范围，其余 (0, _BIG)；
# - bool：("bool",)（word 风格；save_config 默认口径与端点旧调用的默认 bool_style="word" 一致）；
# - str/secret：("raw",) 原样存库——历史行为是 str(value) 直接存（含空串存行，
#   而不是删行）；复合校验（stream_accel 开关+域名联动、签到上下限、活力阈值）
#   保留在端点层，注册表只表达"类型+范围"。
ECONOMY_CONFIG_SCHEMA: dict[str, tuple] = {
    # 签到/活力/观影能量/能量购订阅/邀请返利等货币键已随货币于 2026-11-13
    # 下线（checkin_*、vitality_*、watch_points_*、recharge_enabled、
    # subscription_points_purchase_enabled、invitation_invitee_reward_points、
    # invitation_rebate_percent、promotion_reward_amount 均已删除）。
    "media_seek_daily_limit": ("int", 0, _BIG),
    "media_seek_daily_vote_limit": ("int", 0, _BIG),
    # 求片减负（2026-11-10）：自动批准默认关（word 风格）；到货盯梢
    # 间隔分钟默认 5、钳制 1~1440（media_seek.fulfill_interval_minutes
    # 读侧同口径钳制，schema 是保存侧的纵深防御）
    "media_seek_auto_approve_enabled": ("bool",),
    "media_seek_fulfill_interval_minutes": ("int", 1, 1440),
    # 2026-10-23 去重 #5：月限与日限同卡（运营参数）后需在此登记才可
    # 从该页保存；paid_request_monthly 此前无任何登记。
    # （welfare_request_monthly 已随福利服役族于 2026-11-14 删除。）
    "paid_request_monthly": ("int", 0, 10000),
    # r40：卡码授予天数所用默认套餐（codes._resolve_plan 读取；无行=按服自动推导）
    "code_default_plan_id": ("int", 0, _BIG),
    "subscription_purchase_enabled": ("bool",),
    # 订阅线路展示名（订阅页分组/对比表用；按服配置，空 = 回退服名；
    # 2026-11-15 美元目录单：服名与线路名分开，首页服务器卡仍读服名）
    "subscription_line_name": ("str",),
    # 钱包（批 1，2026-11-14）：默认值在 backend/wallet.WALLET_CONFIG_DEFAULTS，
    # 这里只登记类型与范围；金额类键单位均为美分（2026-11-15 币种改美刀，
    # *_fen 类键名为历史命名、语义即美分）。
    "wallet_enabled": ("bool",),
    "wallet_min_topup_amount": ("int", 0, _BIG),
    "wallet_topup_max_amount": ("int", 0, _BIG),
    "wallet_topup_fee_pct": ("int", 0, 100),
    # 在线充值汇率（钱包批 3）：手填价 float（自动抓价的兜底/覆盖值，
    # 解析顺序见 backend/wallet_topup）；缓冲 %；手填强制覆盖开关。
    "wallet_topup_cny_rate": ("float", 0.0001, 1000000),
    "wallet_topup_rate_buffer_pct": ("int", 0, 100),
    "wallet_topup_rate_manual_override": ("bool",),
    "wallet_invite_rebate_percent": ("int", 0, 100),
    "wallet_invite_rebate_cap": ("int", 0, _BIG),
    # 返利分档三键（#175 invite_rebate 读取，2026-11-14 用户分档定版）：
    # mode 只收 tiered/flat 两值（invite_rebate.rebate_mode 的合法集），
    # threshold 按每位好友累计付费计、单位美分，tier_percent 是门槛以上比例。
    "wallet_invite_rebate_mode": ("enum", ("tiered", "flat")),
    "wallet_invite_rebate_tier_threshold": ("int", 0, _BIG),
    "wallet_invite_rebate_tier_percent": ("int", 0, 100),
    "traffic_balance_price_per_100gb": ("int", 0, _BIG),
    # 流量额度真拦截总开关（2026-11-15 用户亲点）：默认开（无行=开），
    # 读侧 backend/traffic_gate.enforcement_enabled；关掉全部放行。
    "traffic_enforce_enabled": ("bool",),
    # 时间换流量包表（JSON 数组，读侧 traffic_quota.exchange_packages
    # 校验后下发；缺省/非法回退默认半月包/整月包），raw 整串保存。
    "traffic_exchange_packages": ("raw",),
    "wallet_refund_to_balance": ("bool",),
    "wallet_auto_renew_enabled": ("bool",),
    "payment_gateway_url": ("raw",), "payment_partner_id": ("raw",),
    "payment_partner_key": ("raw",), "payment_qqpay_enabled": ("bool",),
    "site_url": ("raw",),
    "subscription_required": ("bool",), "subscription_gate_message": ("raw",),
    "allow_download": ("bool",),
    "device_limit_per_user": ("int", 0, _BIG), "device_limit_auto_evict": ("bool",),
    "login_log_retention_days": ("int", 0, _BIG),
    "max_pending_orders_per_user": ("int", 0, _BIG),
    "promotion_reward_enabled": ("bool",),
    "promotion_reward_type": ("raw",),
    "promotion_reward_days": ("int", 0, _BIG),
    "stream_accel_enabled": ("bool",),
    "stream_accel_domain": ("raw",),
    "series_merge_interval_sec": ("int", 3600, 604800),
    "series_merge_batch_limit": ("int", 10, 100000),
}

