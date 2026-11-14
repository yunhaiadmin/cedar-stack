"""
管理后台 API · 经济、邀请与统计

从 `admin.py` 拆出的「后段」：邀请与返利、积分台账、经济系统设置、经济统计，
以及 v2.4.0 补的用户详情 / 趋势 / 订阅总览。

**这里的端点一律写成同步 ``def``**：项目用的是同步 SQLAlchemy，``async def``
端点会在事件循环上直接跑查询，一个管理员点开「经济统计」就会把全站请求按住。
``def`` 端点由 FastAPI 自动丢到线程池，语义不变（HTTP 契约、依赖注入、返回类型
都一样），但不再占住事件循环。唯一保留 ``async def`` 的是
``economy_adjust_points``——它真的要 ``await notify_admin_event``。

路由对象与其它后台模块共用 ``admin_core.admin_router``，导入即注册。
"""
from datetime import datetime, timedelta
import json
from typing import List, Optional

from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend import models
from backend.member_level import ensure_member_levels_seeded
from backend.api.admin_core import (
    _audit,
    admin_router,
    get_current_admin,
)
from backend.database import get_db
from backend.notifications import notify_admin_event_quiet


# ---------- 邀请与返利管理 ----------

@admin_router.get("/economy/invitations")
def economy_list_invitations(
    limit: int = 100,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """邀请记录列表（谁邀请了谁、奖励多少）"""
    records = db.query(models.InvitationRecord).order_by(
        models.InvitationRecord.created_at.desc()
    ).limit(min(limit, 200)).all()

    user_ids = {r.inviter_id for r in records} | {r.invitee_id for r in records}
    users = {u.id: u.username for u in db.query(models.WebUser).filter(
        models.WebUser.id.in_(user_ids)).all()} if user_ids else {}

    return {"records": [
        {
            "id": r.id,
            "inviter": users.get(r.inviter_id, "未知"),
            "invitee": users.get(r.invitee_id, "未知"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]}


# ---------- 全员赠送能量（批次） ----------
# 2026-10-31 能力化：2026-10-28 的全站送 50 原是一次性手工数据操作，无端点
# 无入口。此处只建批＋预览＋查历史：参数（金额/范围/备注）全由管理员建批时
# 给，发放由 worker 分块执行（backend/gift_all.py），端点不同步发分。


# ---------- 经济系统设置 ----------

ECONOMY_CONFIG_KEYS = {
    # 签到/活力/观影能量等货币配置键已随货币于 2026-11-13 下线（签到、
    # 活力、watch_points 模块均已删除，键留库行无害、面板不再展示）。
    "media_seek_daily_limit": "int",  # 用户每日求片上限（用户端硬性校验）
    "media_seek_daily_vote_limit": "int",  # Step 1：用户每日附议上限（默认 20 次，按当日现存附议行计）
    "media_seek_auto_approve_enabled": "bool",  # 求片减负：自动批准（默认关，开后新求片落单即已批准）
    "media_seek_fulfill_interval_minutes": "int",  # 求片减负：到货盯梢间隔分钟（默认 5）
    "welfare_request_monthly": "int",  # 公益服每月求片上限（2026-10-23 去重 #5 与日限同卡）
    "paid_request_monthly": "int",  # 付费服每月求片上限（此前无后台入口，本轮补登记）
    "code_default_plan_id": "int",  # r40：卡码授予天数所用默认套餐（codes._resolve_plan 读取）
    "subscription_purchase_enabled": "bool",
    # 钱包（批 1，2026-11-14）：默认值见 backend/wallet.WALLET_CONFIG_DEFAULTS
    "wallet_enabled": "bool",  # 钱包总开关（默认开）
    "wallet_min_topup_amount": "int",  # 最低充值额（分，默认 1000＝¥10）
    "wallet_topup_fee_pct": "int",  # 充值手续费比例 %（默认 0，批 3 再议）
    "wallet_invite_rebate_percent": "int",  # 邀请返利比例 %（默认 5，批 2 计提）
    "wallet_invite_rebate_cap": "int",  # 单人返利上限（分，默认 0＝不限）
    "traffic_balance_price_per_100gb": "int",  # 余额购流量单价（分/100GB，默认 300＝¥3，批 2 启用）
    "wallet_refund_to_balance": "bool",  # 退款回钱包（默认开，批 2 落地）
    "wallet_auto_renew_enabled": "bool",  # 余额自动续订（默认关，不开）
    "payment_gateway_url": "str", "payment_partner_id": "str",
    "payment_partner_key": "secret", "payment_qqpay_enabled": "bool",
    "site_url": "str",
    # 付费墙：是否要求有效订阅才能播放 + 自定义拦截文案
    "subscription_required": "bool", "subscription_gate_message": "str",
    # 下载开关与设备/日志风控（借鉴 twilight-kotomi 的运营策略）
    "allow_download": "bool",
    "device_limit_per_user": "int", "device_limit_auto_evict": "bool",
    "login_log_retention_days": "int",
    "max_pending_orders_per_user": "int",
    # 推广奖励（v2.44.0）：总开关缺省开（天数为 0 时不发放），留在面板落库；
    # 余额（积分）型奖励与金额键已随货币下线，promotion 只发会员天数。
    "promotion_reward_enabled": "bool",
    "promotion_reward_type": "str",
    "promotion_reward_days": "int",
    # 流媒体加速（v2.49.0）：总开关缺省开，留在面板落库；域名点保存即生效
    "stream_accel_enabled": "bool",
    "stream_accel_domain": "str",
    # C3 流水审计开关（points_audit_enabled）与 Q3 同剧合并三开关
    # （series_merge_*）均已于 2026-10-21 摘面板（env 治理，缺省开）；
    # 此处只剩同剧合并的间隔/组数上限两个数值（间隔下限 3600 秒，
    # 上限 604800 秒；每轮组数上限 10~100000），读取侧见
    # merge_versions_worker.series_merge_config。
    "series_merge_interval_sec": "int", "series_merge_batch_limit": "int",
}


class EconomySettingsRequest(BaseModel):
    settings: dict


@admin_router.get("/economy/settings")
def economy_get_settings(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    from backend.promotion import CONFIG_ENABLED as _promo_enabled
    from backend.promotion import DEFAULTS as _promo_defaults

    # 缺行时这几个开关回代码缺省（开）。其余键仍回空串，由各读取方自己兜底。
    missing_on = {
        _promo_enabled: _promo_defaults[_promo_enabled],
        "stream_accel_enabled": "true",
        # 钱包批 1：这两个开关代码默认也是开（见 wallet.WALLET_CONFIG_DEFAULTS），
        # 面板必须如实显示，不能因为没落库行就显示成关。
        "wallet_enabled": "true",
        "wallet_refund_to_balance": "true",
    }
    result = {}
    for key, value_type in ECONOMY_CONFIG_KEYS.items():
        config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
        if value_type == "secret":
            result[key] = "******" if (config and config.value) else ""
        elif config is not None:
            result[key] = config.value
        elif key in missing_on:
            result[key] = missing_on[key]
        else:
            result[key] = ""
    return {"settings": result}


@admin_router.put("/economy/settings")
def economy_update_settings(
    request: EconomySettingsRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """更新经济系统配置（secret 值为 ****** 时保持不变）"""
    # 流媒体加速：开开关必须配合法域名，否则域名守卫会把所有请求 403 掉。
    # 先按"请求值优先、DB 现有值兜底"算出最终状态，非法直接 400（此时尚未写库）。
    if "stream_accel_enabled" in request.settings or "stream_accel_domain" in request.settings:
        from backend.emby_server import stream_accel as _sa

        def _final(key: str) -> str:
            if key in request.settings:
                return str(request.settings[key] or "")
            row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
            return row.value if row and row.value else ""

        if "stream_accel_enabled" in request.settings:
            _enabled = _final("stream_accel_enabled").strip().lower() in ("1", "true", "yes", "on")
        else:
            # 开关留在面板：请求没带时按库里的生效值（无行则缺省开）判定域名联动
            from backend.integrations import store as _store
            _enabled = _store.get_value(db, "stream_accel_enabled", "true").strip().lower() in ("1", "true", "yes", "on")
        if _enabled:
            try:
                _sa.validate_domain(_final("stream_accel_domain"))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
    # 配置瘦身第 4 批 4-6：落库走统一写入通道 config_service.save_config
    # （校验→批量落库→写时失效）。以下口径与旧代码逐字一致：
    # - 未知键静默忽略；secret 传空/"******" = 保留原行、不进落库；
    # - int/bool 键的空串 = 删行回退默认；str/secret 键原样存（空串也存行）；
    # - 复合校验（stream_accel 联动、签到、活力值）仍在端点层、落库前 400。
    # 失效缺口补齐：旧代码只手动失效 stream_accel_*/vitality_*，其余键保存后
    # 60 秒内可能读到旧值；统一通道对全部落库键 + 删行键写时失效。
    from backend import config_service

    values: dict = {}
    is_secret: dict = {}
    for key, value in request.settings.items():
        if key not in ECONOMY_CONFIG_KEYS:
            continue  # 未知键：今天就是静默忽略
        if ECONOMY_CONFIG_KEYS[key] == "secret" and (not value or value == "******"):
            continue  # 掩码/空：保留原行
        values[key] = value
        is_secret[key] = ECONOMY_CONFIG_KEYS[key] == "secret"

    normalized: dict = {}

    def _capture(_schema: str, norm: dict) -> None:
        normalized.update(norm)

    saved: list = []
    if values:
        # 校验失败抛 ConfigValueError，由 main.py 的统一 handler 转 400
        saved = config_service.save_config(
            db, values, schema="economy", audit=_capture)

    db.commit()
    # （写时失效已由 store.write_values 承接，失效时机从提交后移到写时，
    # 这是正统通道的既定语义，4-1/4-3 起各家族迁移统一如此。）
    changed = {}
    for key in saved:
        if is_secret[key]:
            changed[key] = "(已更新)"  # secret 不进审计日志，只记"改了"
        else:
            changed[key] = normalized.get(key) or ""  # 删行键记 ""，与旧代码一致
    _audit(db, current_admin, "economy_update_settings", "system", None, changed)
    db.commit()
    return {"success": True, "changed": list(changed.keys())}


# ---------- 防盗链（播放签名 + 来路检查，见 backend/emby_server/play_sign.py） ----------

class HotlinkConfigRequest(BaseModel):
    play_sign_ttl: int = 900
    # 配置瘦身第 3c 批：Referer 白名单（逗号分隔域名）；None=老前端没传=不改
    allowed_referers: Optional[str] = None


@admin_router.get("/hotlink-config")
def hotlink_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """防盗链配置读取：总开关 + 播放签名 TTL（含默认值）。"""
    from backend.emby_server import play_sign

    return play_sign.config_payload(db)


@admin_router.put("/hotlink-config")
def hotlink_update_config(
    request: HotlinkConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """防盗链配置写入：范围非法时 400（中文信息），写完即失效热缓存。"""
    from backend.emby_server import play_sign

    try:
        # 配置瘦身第 3c 批：白名单走同一张卡的既有保存通道（归一化+落库在
        # write_allowed_referers 里），None=前端没传=不碰原值；先写它，
        # 随后的 write_config 回包 config_payload 才能带上最新白名单
        if request.allowed_referers is not None:
            play_sign.write_allowed_referers(db, request.allowed_referers)
        result = play_sign.write_config(
            db,
            play_sign_ttl=request.play_sign_ttl,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # A-008：审计只记真实写过的键。enabled 已改走 env、不再经本端点写，
    # allowed_referers 没传=没改，两者都不许进审计，避免「记为已改、实际未改」。
    _audit_details = {"play_sign_ttl": request.play_sign_ttl}
    if request.allowed_referers is not None:
        _audit_details["allowed_referers"] = request.allowed_referers
    _audit(db, current_admin, "hotlink_update_config", "system", None,
           _audit_details)
    db.commit()
    return result


# ---------- 列表去重缓存（见 backend/emby_server/items_dedup_cache.py） ----------

class ItemsDedupCacheConfigRequest(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 60
    max_entries: int = 2000


@admin_router.get("/items-dedup-cache-config")
def items_dedup_cache_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """列表去重缓存配置读取：总开关 + 条目 TTL + 条目上限（含默认值）。"""
    from backend.emby_server import items_dedup_cache

    return items_dedup_cache.config_payload(db)


@admin_router.put("/items-dedup-cache-config")
def items_dedup_cache_update_config(
    request: ItemsDedupCacheConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """列表去重缓存配置写入：范围非法时 400（中文信息），写完即失效热缓存。"""
    from backend.emby_server import items_dedup_cache

    try:
        result = items_dedup_cache.write_config(
            db,
            enabled=request.enabled,
            ttl_seconds=request.ttl_seconds,
            max_entries=request.max_entries,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "items_dedup_cache_update_config", "system", None,
           {"enabled": request.enabled, "ttl_seconds": request.ttl_seconds,
            "max_entries": request.max_entries})
    db.commit()
    return result


# ---------- 转存（见 backend/emby_server/transfer_variant.py） ----------

class TransferConfigRequest(BaseModel):
    cross_provider_transfer_enabled: bool = True
    transfer_cross_lane_concurrency: int = 2
    # 剧集预热（2026-11-01 期四）：总开关默认开；搬字节预热独立
    # 日帽默认 2（与 prefetch 帽分开计）；候选集数默认 3。范围钳在
    # config_schema 的 transfer 家族（日帽 1~16、集数 1~10）。
    transfer_series_preheat_enabled: bool = True
    transfer_series_preheat_daily_max: int = 2
    transfer_series_preheat_next_count: int = 3
    transfer_cross_aging_sec: int = 1800
    transfer_fast_lane_concurrency: int = 8
    transfer_per_owner_inflight: int = 2
    transfer_rebind_waiver_window_days: int = 7
    transfer_rebind_waiver_max_uses: int = 1
    # 换代线地基记账（2026-11-01）：交付状态落盘＋块哈希清单，
    # 纯记账 fail-open，默认开；校验在 config_schema 的
    # transfer 家族里。
    transfer_manifest_accounting_enabled: bool = True
    # 换代线 step 4 执行面接管（2026-11-01）：落盘交付行驱动
    # 续传恢复＋清单 sha1 采信＋同内容调度合并；须与记账开关
    # 联动才生效，默认开。
    transfer_manifest_takeover_enabled: bool = True
    # 类别级熔断（2026-11-01 预见层 v2）：暂停规则 JSON 数组文本
    # [{provider, error_kind}]，error_kind="*" 为该 provider 全部
    # 类别；默认 "[]" = 不暂停、现行行为不变。结构校验在
    # transfer_variant.coerce_paused_categories（保存侧 strict）。
    transfer_paused_categories: str = "[]"
    # 指纹沉淀（2026-11-01 转存等待压缩期三）：总开关默认开；
    # 需求/追剧档默认开，热门/新入库档默认关（本期只有开关位、
    # 不选片）；日预算默认 200 GiB；fused 是转化率熔断运行时
    # 状态（默认未熔断，管理员清回 False 即恢复沉淀）。校验与
    # 读写实现在 transfer_sediment + config_schema transfer 家族。
    transfer_sediment_enabled: bool = True
    transfer_sediment_tier_demand_enabled: bool = True
    transfer_sediment_tier_watching_enabled: bool = True
    transfer_sediment_tier_hot_enabled: bool = False
    transfer_sediment_tier_new_enabled: bool = False
    transfer_sediment_daily_budget_gib: int = 200
    transfer_sediment_interval_sec: int = 1800
    transfer_sediment_max_items_per_round: int = 20
    transfer_sediment_fuse_window_days: int = 30
    transfer_sediment_fuse_unconsumed_ratio: float = 0.7
    # 晚高峰静默窗（上海整点，起含止不含）：默认 19→23，
    # 起==止 = 关闭；窗内沉淀整轮跳过，不与播放抢 Drive 额度。
    transfer_sediment_quiet_start_hour: int = 19
    transfer_sediment_quiet_end_hour: int = 23
    transfer_sediment_fused: bool = False


@admin_router.get("/transfer-config")
def transfer_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """转存配置读取：跨 provider 总开关 + 三个并发上限 + 115 秒传/分片上传开关（含默认值）。"""
    from backend.emby_server import transfer_sediment, transfer_variant

    payload = transfer_variant.transfer_config_payload(db)
    payload.update(transfer_sediment.sediment_config_payload(db))
    return payload


@admin_router.put("/transfer-config")
def transfer_update_config(
    request: TransferConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """转存配置写入：范围非法时 400（中文信息），写完即失效热缓存。"""
    from backend.emby_server import transfer_sediment, transfer_variant

    try:
        # 沉淀段先写：其校验先行，非法时整段不落库且不牵动其余
        # 转存键；两段各自走 config_service 整批校验。
        sediment_payload = transfer_sediment.write_sediment_config(
            db,
            transfer_sediment_enabled=request.transfer_sediment_enabled,
            transfer_sediment_tier_demand_enabled=(
                request.transfer_sediment_tier_demand_enabled),
            transfer_sediment_tier_watching_enabled=(
                request.transfer_sediment_tier_watching_enabled),
            transfer_sediment_tier_hot_enabled=(
                request.transfer_sediment_tier_hot_enabled),
            transfer_sediment_tier_new_enabled=(
                request.transfer_sediment_tier_new_enabled),
            transfer_sediment_daily_budget_gib=(
                request.transfer_sediment_daily_budget_gib),
            transfer_sediment_interval_sec=(
                request.transfer_sediment_interval_sec),
            transfer_sediment_max_items_per_round=(
                request.transfer_sediment_max_items_per_round),
            transfer_sediment_fuse_window_days=(
                request.transfer_sediment_fuse_window_days),
            transfer_sediment_fuse_unconsumed_ratio=(
                request.transfer_sediment_fuse_unconsumed_ratio),
            transfer_sediment_quiet_start_hour=(
                request.transfer_sediment_quiet_start_hour),
            transfer_sediment_quiet_end_hour=(
                request.transfer_sediment_quiet_end_hour),
            transfer_sediment_fused=request.transfer_sediment_fused,
        )
        result = transfer_variant.write_transfer_config(
            db,
            cross_provider_transfer_enabled=(
                request.cross_provider_transfer_enabled),
            transfer_cross_lane_concurrency=(
                request.transfer_cross_lane_concurrency),
            transfer_series_preheat_enabled=(
                request.transfer_series_preheat_enabled),
            transfer_series_preheat_daily_max=(
                request.transfer_series_preheat_daily_max),
            transfer_series_preheat_next_count=(
                request.transfer_series_preheat_next_count),
            transfer_cross_aging_sec=(
                request.transfer_cross_aging_sec),
            transfer_fast_lane_concurrency=(
                request.transfer_fast_lane_concurrency),
            transfer_per_owner_inflight=request.transfer_per_owner_inflight,
            transfer_rebind_waiver_window_days=(
                request.transfer_rebind_waiver_window_days),
            transfer_rebind_waiver_max_uses=(
                request.transfer_rebind_waiver_max_uses),
            transfer_manifest_accounting_enabled=(
                request.transfer_manifest_accounting_enabled),
            transfer_manifest_takeover_enabled=(
                request.transfer_manifest_takeover_enabled),
            transfer_paused_categories=request.transfer_paused_categories,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "transfer_update_config", "system", None,
           {"cross_provider_transfer_enabled":
            request.cross_provider_transfer_enabled,
            "transfer_cross_lane_concurrency":
            request.transfer_cross_lane_concurrency,
            "transfer_series_preheat_enabled":
            request.transfer_series_preheat_enabled,
            "transfer_series_preheat_daily_max":
            request.transfer_series_preheat_daily_max,
            "transfer_series_preheat_next_count":
            request.transfer_series_preheat_next_count,
            "transfer_cross_aging_sec":
            request.transfer_cross_aging_sec,
            "transfer_fast_lane_concurrency":
            request.transfer_fast_lane_concurrency,
            "transfer_per_owner_inflight":
            request.transfer_per_owner_inflight,
            "transfer_rebind_waiver_window_days":
            request.transfer_rebind_waiver_window_days,
            "transfer_rebind_waiver_max_uses":
            request.transfer_rebind_waiver_max_uses,
            "transfer_paused_categories":
            request.transfer_paused_categories,
            "transfer_sediment_enabled":
            request.transfer_sediment_enabled,
            "transfer_sediment_tier_demand_enabled":
            request.transfer_sediment_tier_demand_enabled,
            "transfer_sediment_tier_watching_enabled":
            request.transfer_sediment_tier_watching_enabled,
            "transfer_sediment_tier_hot_enabled":
            request.transfer_sediment_tier_hot_enabled,
            "transfer_sediment_tier_new_enabled":
            request.transfer_sediment_tier_new_enabled,
            "transfer_sediment_daily_budget_gib":
            request.transfer_sediment_daily_budget_gib,
            "transfer_sediment_interval_sec":
            request.transfer_sediment_interval_sec,
            "transfer_sediment_max_items_per_round":
            request.transfer_sediment_max_items_per_round,
            "transfer_sediment_fuse_window_days":
            request.transfer_sediment_fuse_window_days,
            "transfer_sediment_fuse_unconsumed_ratio":
            request.transfer_sediment_fuse_unconsumed_ratio,
            "transfer_sediment_fused":
            request.transfer_sediment_fused})
    result.update(sediment_payload)
    db.commit()
    return result


# ---------- 挂载直读出流（见 backend/emby_server/mount_serving.py） ----------

class MountServingConfigRequest(BaseModel):
    enabled: bool = True
    mount_ids: list = []
    local_roots: str = ""


@admin_router.get("/mount-serving-config")
def mount_serving_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """挂载直读配置读取：总开关 + 白名单 + 本地根映射（含默认值）。"""
    from backend.emby_server import mount_serving

    return mount_serving.config_payload(db)


@admin_router.put("/mount-serving-config")
def mount_serving_update_config(
    request: MountServingConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """挂载直读配置写入：白名单/根映射非法时 400（中文信息），保存即生效。"""
    from backend.emby_server import mount_serving

    try:
        result = mount_serving.write_config(
            db,
            enabled=request.enabled,
            mount_ids=request.mount_ids,
            local_roots=request.local_roots,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "mount_serving_update_config", "system", None,
           {"enabled": request.enabled, "mount_ids": request.mount_ids,
            "local_roots": request.local_roots})
    db.commit()
    return result


# ---------- 播放交付编排器（见 backend/emby_server/delivery_plan.py） ----------

class DeliveryPlanConfigRequest(BaseModel):
    enabled: bool = True
    ttl_sec: int = 120


@admin_router.get("/delivery-plan-config")
def delivery_plan_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """交付计划配置读取：编排器开关 + 计划 TTL（含默认值）。"""
    from backend.emby_server import delivery_plan

    return delivery_plan.config_payload(db)


@admin_router.put("/delivery-plan-config")
def delivery_plan_update_config(
    request: DeliveryPlanConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """交付计划配置写入：TTL 越界时 400（中文信息），保存即生效。"""
    from backend.emby_server import delivery_plan

    try:
        result = delivery_plan.write_config(
            db, enabled=request.enabled, ttl_sec=request.ttl_sec)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "delivery_plan_update_config", "system",
           None, {"enabled": request.enabled, "ttl_sec": request.ttl_sec})
    db.commit()
    return result


# ---------- Drive API 全局限速（见 backend/emby_server/drive_changes.py） ----------

class DriveApiConfigRequest(BaseModel):
    qps: float = 8.0
    # 谷歌 Drive 绑定开关：None = 本次不改开关（老前端只传 qps 时
    # 不许顺手把开关写成某个默认值）；True/False 才落库。
    binding_enabled: bool | None = None


def _drive_api_payload(db: Session) -> dict:
    """Drive API 配置卡片的完整读数：限速（drive_changes）+ Drive
    绑定开关现值（user_bindings.drive_binding_enabled，默认关）。"""
    from backend.emby_server import drive_changes, user_bindings

    payload = drive_changes.config_payload(db)
    payload["binding_enabled"] = user_bindings.drive_binding_enabled(db)
    payload["defaults"]["binding_enabled"] = False
    return payload


@admin_router.get("/drive-api-config")
def drive_api_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Drive API 配置读取：全局令牌桶 QPS + Drive 绑定开关（含默认值）。"""
    return _drive_api_payload(db)


@admin_router.put("/drive-api-config")
def drive_api_update_config(
    request: DriveApiConfigRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Drive API 配置写入：QPS 超 [1, 20] 时 400（中文信息），保存即生效
    （本进程强刷；api/ea/worker 其余进程有界延迟内跟上，见
    drive_changes.refresh_api_qps）。binding_enabled 传了才改绑定
    开关（走 config_service 的 drive_api 家族校验，写时失效、本
    进程立即生效）；不传则开关原样不动。"""
    from backend import config_service
    from backend.emby_server import drive_changes

    try:
        drive_changes.write_config(db, qps=request.qps)
        if request.binding_enabled is not None:
            config_service.save_config(
                db, {"drive_binding_enabled": request.binding_enabled},
                schema="drive_api")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    details = {"qps": request.qps}
    if request.binding_enabled is not None:
        details["binding_enabled"] = request.binding_enabled
    _audit(db, current_admin, "drive_api_update_config", "system", None,
           details)
    db.commit()
    return _drive_api_payload(db)


# ---------- 经济统计 ----------


@admin_router.get("/economy/stats")
def economy_stats(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """经济系统概览：订单/兑换/邀请（积分存量已随货币下线）"""
    from sqlalchemy import func as _func

    pending_recharge = db.query(models.RechargeOrder).filter(
        models.RechargeOrder.status == "pending").count()
    paid_recharge = db.query(_func.coalesce(_func.sum(models.RechargeOrder.price), 0)).filter(
        models.RechargeOrder.status == "paid").scalar() or 0
    paid_sub = db.query(_func.coalesce(_func.sum(models.SubscriptionOrder.amount), 0)).filter(
        models.SubscriptionOrder.status == "paid").scalar() or 0

    return {
        "orders": {"pending": pending_recharge,
                   "revenue": round(float(paid_recharge) + float(paid_sub), 2)},
        "exchange_codes": {
            "total": db.query(models.ExchangeCode).count(),
            "used": db.query(models.ExchangeCode).filter(
                models.ExchangeCode.use_count > 0).count(),
        },
        "invitations": db.query(models.InvitationRecord).count(),
    }


# ==================== 用户详情 / 趋势 / 订阅总览（v2.4.0 补齐） ====================


def _days_left(end_date: Optional[datetime], now: datetime) -> int:
    if not end_date:
        return 0
    return max(0, (end_date - now).days)


@admin_router.get("/users/{user_id}")
def get_user_detail(
    user_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """用户 360° 详情：资料 / 订阅 / 积分 / 订单 / 邀请 / 签到 / 观看"""
    from backend.emby_server import models as em

    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    now = datetime.now()

    # ---------- 订阅 ----------
    subs = (
        db.query(models.UserSubscription)
        .filter(models.UserSubscription.user_id == user_id)
        .order_by(models.UserSubscription.end_date.desc())
        .limit(10)
        .all()
    )
    active_sub = next(
        (s for s in subs if s.status == "active" and s.end_date and s.end_date > now), None
    )

    def _sub_row(s: models.UserSubscription) -> dict:
        return {
            "id": s.id,
            "plan_name": _plan_names.get(s.plan_id, f"套餐 #{s.plan_id}"),
            "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "days_left": _days_left(s.end_date, now),
            "status": s.status,
        }

    # ---------- 订单 ----------
    recharge_orders = (
        db.query(models.RechargeOrder)
        .filter(models.RechargeOrder.user_id == user_id)
        .order_by(models.RechargeOrder.created_at.desc())
        .limit(10)
        .all()
    )
    sub_orders = (
        db.query(models.SubscriptionOrder)
        .filter(models.SubscriptionOrder.user_id == user_id)
        .order_by(models.SubscriptionOrder.created_at.desc())
        .limit(10)
        .all()
    )
    # P1（r49）：订阅/订单的 plan/package 名批量一次查完。此前 _sub_row 与订单行
    # 里 s.plan.name / o.package.name / o.plan.name 都是 lazy relationship 逐行回查
    # （详情页一次最多约 30 次额外查询），与邀请部分的 invitee_names 同一写法。
    # 注意：必须放在 sub_orders / recharge_orders 取数之后（曾放错位置导致
    # UnboundLocalError，被 smoke_test_admin_v240 抓到）。
    _plan_ids = ({s.plan_id for s in subs} | {o.plan_id for o in sub_orders}) - {None}
    _package_ids = {o.package_id for o in recharge_orders} - {None}
    _plan_names = {
        p.id: p.name
        for p in db.query(models.SubscriptionPlan.id, models.SubscriptionPlan.name)
        .filter(models.SubscriptionPlan.id.in_(list(_plan_ids) or [-1])).all()
    } if _plan_ids else {}
    _package_names = {
        p.id: p.name
        for p in db.query(models.RechargePackage.id, models.RechargePackage.name)
        .filter(models.RechargePackage.id.in_(list(_package_ids) or [-1])).all()
    } if _package_ids else {}
    paid_total = float(
        db.query(func.coalesce(func.sum(models.RechargeOrder.price), 0)).filter(
            models.RechargeOrder.user_id == user_id,
            models.RechargeOrder.status == "paid",
        ).scalar() or 0
    ) + float(
        db.query(func.coalesce(func.sum(models.SubscriptionOrder.amount), 0)).filter(
            models.SubscriptionOrder.user_id == user_id,
            models.SubscriptionOrder.status == "paid",
        ).scalar() or 0
    )

    # ---------- 邀请 ----------
    invite_records = (
        db.query(models.InvitationRecord)
        .filter(models.InvitationRecord.inviter_id == user_id)
        .order_by(models.InvitationRecord.created_at.desc())
        .limit(10)
        .all()
    )
    invitee_ids = [r.invitee_id for r in invite_records]
    invitee_names = {
        u.id: u.username
        for u in db.query(models.WebUser).filter(models.WebUser.id.in_(invitee_ids)).all()
    } if invitee_ids else {}
    # ---------- 观看 ----------
    plays = db.query(em.PlaybackSession).filter(em.PlaybackSession.user_id == user_id).count()
    watched_items = db.query(em.UserMediaData).filter(
        em.UserMediaData.user_id == user_id,
        em.UserMediaData.played == True,  # noqa: E712
    ).count()

    return {
        "profile": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "emby_username": user.emby_username,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "subscription": {
            "active": _sub_row(active_sub) if active_sub else None,
            "history": [_sub_row(s) for s in subs],
        },
        "orders": {
            "paid_total": round(paid_total, 2),
            "recharge": [
                {
                    "order_id": o.order_id,
                    "item_name": _package_names.get(o.package_id, f"套餐 #{o.package_id}"),
                    "amount": float(o.price),
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in recharge_orders
            ],
            "subscription": [
                {
                    "order_id": o.order_id,
                    "item_name": o.item_name or _plan_names.get(o.plan_id, f"套餐 #{o.plan_id}"),
                    "amount": float(o.amount),
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in sub_orders
            ],
        },
        "invitation": {
            "count": db.query(models.InvitationRecord).filter(
                models.InvitationRecord.inviter_id == user_id
            ).count(),
            "invitees": [
                {
                    "username": invitee_names.get(r.invitee_id, "未知"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in invite_records
            ],
        },
        "watch": {
            "plays": plays,
            "watched_items": watched_items,
        },
    }


@admin_router.get("/stats/trend")
def get_stats_trend(
    days: int = 14,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """趋势统计（默认近 14 天）：新增用户 / 播放次数 / 新增入库 / 营收

    单次聚合查询后按日补零，保证折线图连续。另附 ``hourly``：最近 24 个
    上海整点桶的播放/新增入库序列（与按日同源同定义，只换分桶粒度）。
    """
    from backend.emby_server import models as em

    days = max(1, min(days, 90))
    # r43：label 轴按上海自然日。入库时间戳是 UTC-naive，此前用服务器本地
    # （生产 UTC）的 today 生成 UTC 日期序列，签到系列经 _checkin_day_key
    # +8h 还原为上海日期 → 每天 00:00–08:00 CST 的签到在图上消失。
    # tzdata 由镜像显式安装（Dockerfile.backend），这里仍 try/except 回退。
    from zoneinfo import ZoneInfo as _ZoneInfo
    try:
        _now = datetime.now(_ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        _now = datetime.now()
    today = _now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_day = today - timedelta(days=days - 1)
    # 过滤起点再往前推 8h，覆盖上海窗口左边界（D 日 00:00 CST = D-1 日 16:00 UTC）。
    start = start_day - timedelta(hours=8)

    def _series(rows) -> dict:
        # r43：SQL 只按原始 UTC-naive 时间戳分组取值，Python 侧逐行 +8h 再按
        # 上海自然日聚合（此前用 func.date 按 UTC 切日、bucket 午夜 +8h 仍是同一天，
        # 属 no-op：每天 00:00–08:00 CST 的事件被算到前一天 label。方言无关，
        # SQLite/PG 的 interval 写法不同，不在 SQL 里做时区换算）。
        out: dict = {}
        for ts, value in rows:
            try:
                key = (ts + timedelta(hours=8)).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                key = str(ts)[:10]
            out[key] = out.get(key, 0) + (value or 0)
        return out

    user_rows = db.query(
        models.WebUser.created_at, func.count(models.WebUser.id)
    ).filter(models.WebUser.created_at >= start).group_by(
        models.WebUser.created_at
    ).all()

    play_rows = db.query(
        em.PlaybackSession.start_time, func.count(em.PlaybackSession.id)
    ).filter(em.PlaybackSession.start_time >= start).group_by(
        em.PlaybackSession.start_time
    ).all()

    # 新增入库（2026-11-03 仪表盘补口径，#1805 占位替换的真口径）：
    # 入库时间 = MediaItem.date_added（UTC-naive 入库帧，与 plays 同帧），
    # 与 _series 同一 +8h 上海日界聚合。可见性口径与全站一致：软删除行
    # （deleted_at 非空）与合并副本（merged_into_id 非空）不计——聚合查询
    # 上全局软删钩子下不到子查询，必须显式带条件（count_visible 同教训）。
    # 时间范围过滤走 idx_item_added，不拉全表。
    item_rows = db.query(
        em.MediaItem.date_added, func.count(em.MediaItem.id)
    ).filter(
        em.MediaItem.date_added >= start,
        em.MediaItem.deleted_at.is_(None),
        em.MediaItem.merged_into_id.is_(None),
    ).group_by(em.MediaItem.date_added).all()

    recharge_rows = db.query(
        models.RechargeOrder.paid_at, func.coalesce(func.sum(models.RechargeOrder.price), 0)
    ).filter(
        models.RechargeOrder.status == "paid", models.RechargeOrder.paid_at >= start
    ).group_by(models.RechargeOrder.paid_at).all()

    sub_rows = db.query(
        models.SubscriptionOrder.paid_at, func.coalesce(func.sum(models.SubscriptionOrder.amount), 0)
    ).filter(
        models.SubscriptionOrder.status == "paid", models.SubscriptionOrder.paid_at >= start
    ).group_by(models.SubscriptionOrder.paid_at).all()

    users_map = _series(user_rows)
    plays_map = _series(play_rows)
    items_map = _series(item_rows)
    recharge_map = _series(recharge_rows)
    sub_map = _series(sub_rows)

    series = []
    for i in range(days):
        day = start_day + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        series.append({
            "date": key,
            "new_users": int(users_map.get(key, 0)),
            "plays": int(plays_map.get(key, 0)),
            "new_items": int(items_map.get(key, 0)),
            "revenue": round(float(recharge_map.get(key, 0)) + float(sub_map.get(key, 0)), 2),
        })

    # 24 小时序列（2026-11-03 仪表盘补口径）：与按日同源同定义，只换
    # 分桶粒度——播放仍数 PlaybackSession.start_time、入库仍数
    # MediaItem.date_added（同样的软删/副本排除），窗口为最近 24 个
    # 上海整点桶（含当前未走完的整点），缺桶补零。时间戳是 UTC-naive，
    # +8h 即上海帧；查询只取窗口内行（范围过滤），不全表分桶。
    hour_cursor = _now.replace(minute=0, second=0, microsecond=0)
    first_hour = hour_cursor - timedelta(hours=23)
    win_start = first_hour - timedelta(hours=8)
    win_end = hour_cursor + timedelta(hours=1) - timedelta(hours=8)

    def _hourly(rows) -> dict:
        out: dict = {}
        for ts, value in rows:
            try:
                key = (ts + timedelta(hours=8)).replace(
                    minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00")
            except Exception:  # noqa: BLE001
                continue
            out[key] = out.get(key, 0) + (value or 0)
        return out

    play_hour_rows = db.query(
        em.PlaybackSession.start_time, func.count(em.PlaybackSession.id)
    ).filter(
        em.PlaybackSession.start_time >= win_start,
        em.PlaybackSession.start_time < win_end,
    ).group_by(em.PlaybackSession.start_time).all()

    item_hour_rows = db.query(
        em.MediaItem.date_added, func.count(em.MediaItem.id)
    ).filter(
        em.MediaItem.date_added >= win_start,
        em.MediaItem.date_added < win_end,
        em.MediaItem.deleted_at.is_(None),
        em.MediaItem.merged_into_id.is_(None),
    ).group_by(em.MediaItem.date_added).all()

    play_hours = _hourly(play_hour_rows)
    item_hours = _hourly(item_hour_rows)
    hourly_series = []
    for i in range(24):
        hour = first_hour + timedelta(hours=i)
        key = hour.strftime("%Y-%m-%d %H:00")
        hourly_series.append({
            "hour": key,
            "plays": int(play_hours.get(key, 0)),
            "new_items": int(item_hours.get(key, 0)),
        })

    return {
        "days": days,
        "series": series,
        "totals": {
            "new_users": sum(p["new_users"] for p in series),
            "plays": sum(p["plays"] for p in series),
            "new_items": sum(p["new_items"] for p in series),
            "revenue": round(sum(p["revenue"] for p in series), 2),
        },
        "hourly": {
            "hours": 24,
            "series": hourly_series,
        },
    }


@admin_router.get("/economy/subscriptions")
def economy_list_subscriptions(
    status_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """订阅总览：全部订阅记录 + 状态筛选 + 到期概览

    status_filter: active（生效中）/ expiring（7 天内到期）/ expired（已过期）
    """
    now = datetime.now()
    week_later = now + timedelta(days=7)

    q = db.query(models.UserSubscription)
    if status_filter == "active":
        q = q.filter(models.UserSubscription.status == "active",
                     models.UserSubscription.end_date > now)
    elif status_filter == "expiring":
        q = q.filter(models.UserSubscription.status == "active",
                     models.UserSubscription.end_date > now,
                     models.UserSubscription.end_date <= week_later)
    elif status_filter == "expired":
        q = q.filter(or_(models.UserSubscription.status == "expired",
                         models.UserSubscription.end_date <= now))

    total = q.count()
    rows = q.order_by(models.UserSubscription.end_date.asc()).offset(offset).limit(min(limit, 200)).all()

    user_ids = {r.user_id for r in rows}
    users = {
        u.id: u.username
        for u in db.query(models.WebUser).filter(models.WebUser.id.in_(user_ids)).all()
    } if user_ids else {}

    active_count = db.query(models.UserSubscription).filter(
        models.UserSubscription.status == "active", models.UserSubscription.end_date > now
    ).count()
    expiring_count = db.query(models.UserSubscription).filter(
        models.UserSubscription.status == "active",
        models.UserSubscription.end_date > now,
        models.UserSubscription.end_date <= week_later,
    ).count()
    expired_count = db.query(models.UserSubscription).filter(
        or_(models.UserSubscription.status == "expired",
            models.UserSubscription.end_date <= now)
    ).count()

    return {
        "total": total,
        "summary": {
            "active": active_count,
            "expiring_7d": expiring_count,
            "expired": expired_count,
        },
        "subscriptions": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": users.get(r.user_id, "未知"),
                "plan_name": r.plan.name if r.plan else f"套餐 #{r.plan_id}",
                "start_date": r.start_date.isoformat() if r.start_date else None,
                "end_date": r.end_date.isoformat() if r.end_date else None,
                "days_left": _days_left(r.end_date, now),
                "status": "active" if (r.status == "active" and r.end_date and r.end_date > now) else "expired",
            }
            for r in rows
        ],
    }


# ========== 会员等级管理（P1 统一货币体系） ==========

def _level_to_dict(lv: models.MemberLevel) -> dict:
    """MemberLevel 转 dict（benefits_json 容错解析为数组）"""
    benefits = []
    if lv.benefits_json:
        try:
            parsed = json.loads(lv.benefits_json)
            if isinstance(parsed, list):
                benefits = parsed
        except (ValueError, TypeError):
            benefits = []
    return {
        "id": lv.id,
        "level": lv.level,
        "name": lv.name,
        "xp_threshold": lv.xp_threshold,
        "discount_pct": lv.discount_pct or 0,
        "benefits": benefits,
        "badge_icon": lv.badge_icon,
        "badge_color": lv.badge_color,
        "is_active": lv.is_active,
    }


class MemberLevelUpdate(BaseModel):
    name: Optional[str] = None
    xp_threshold: Optional[int] = None
    discount_pct: Optional[int] = Field(default=None, ge=0, le=100)
    benefits: Optional[List[str]] = None
    badge_icon: Optional[str] = None
    badge_color: Optional[str] = None


@admin_router.get("/member-levels")
def list_member_levels(current_admin: models.WebUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    # 确保种子等级存在
    ensure_member_levels_seeded(db)
    levels = db.query(models.MemberLevel).order_by(models.MemberLevel.level.asc()).all()
    return {"levels": [_level_to_dict(lv) for lv in levels]}


@admin_router.put("/member-levels/{level_id}")
def update_member_level(level_id: int, payload: MemberLevelUpdate, current_admin: models.WebUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    lv = db.query(models.MemberLevel).filter(models.MemberLevel.id == level_id).first()
    if not lv:
        raise HTTPException(status_code=404, detail="等级不存在")
    update_data = {}
    if payload.name is not None:
        lv.name = payload.name
        update_data["name"] = payload.name
    if payload.xp_threshold is not None:
        if payload.xp_threshold < 0:
            raise HTTPException(status_code=400, detail="经验阈值不能为负数")
        lv.xp_threshold = payload.xp_threshold
        update_data["xp_threshold"] = payload.xp_threshold
    if payload.discount_pct is not None:
        lv.discount_pct = payload.discount_pct
        update_data["discount_pct"] = payload.discount_pct
    if payload.benefits is not None:
        # 数组转 JSON 存储
        lv.benefits_json = json.dumps(payload.benefits, ensure_ascii=False)
        update_data["benefits"] = payload.benefits
    if payload.badge_icon is not None:
        lv.badge_icon = payload.badge_icon
        update_data["badge_icon"] = payload.badge_icon
    if payload.badge_color is not None:
        lv.badge_color = payload.badge_color
        update_data["badge_color"] = payload.badge_color
    db.commit()
    db.refresh(lv)
    _audit(db, current_admin.id, "member_level_update", "member_level", lv.id, update_data)
    return {"level": _level_to_dict(lv)}


@admin_router.delete("/member-levels/{level_id}")
def delete_member_level(level_id: int, current_admin: models.WebUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    lv = db.query(models.MemberLevel).filter(models.MemberLevel.id == level_id).first()
    if not lv:
        raise HTTPException(status_code=404, detail="等级不存在")
    # 删除前校验：删除启用等级时至少保留 1 个启用等级。
    # R49：检查与删除必须同事务串行化——不加锁时两名管理员并发删最后两个
    # 启用等级可同时读到数量 1 并双双通过，留下 0 启用等级。与
    # update_user 的 _locked_loginable_super_count（F-P1-6）同一写法。
    if lv.is_active:
        locked = db.query(models.MemberLevel).with_for_update().all()
        active_count = sum(1 for r in locked if r.is_active and r.id != level_id)
        if active_count < 1:
            db.rollback()
            raise HTTPException(status_code=400, detail="至少保留 1 个启用等级")
    level_num = lv.level
    db.delete(lv)
    db.commit()
    _audit(db, current_admin.id, "member_level_delete", "member_level", level_id, {"level": level_num})
    return {"deleted": True}


@admin_router.put("/member-levels/{level_id}/toggle")
def toggle_member_level(level_id: int, current_admin: models.WebUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    lv = db.query(models.MemberLevel).filter(models.MemberLevel.id == level_id).first()
    if not lv:
        raise HTTPException(status_code=404, detail="等级不存在")
    # 关闭时需保证至少保留 1 个启用等级（R49：与 delete 同口径，行锁串行化，
    # 防两名管理员并发关掉最后两个启用等级留下 0 启用）
    if lv.is_active:
        locked = db.query(models.MemberLevel).with_for_update().all()
        active_count = sum(1 for r in locked if r.is_active and r.id != level_id)
        if active_count < 1:
            db.rollback()
            raise HTTPException(status_code=400, detail="至少保留 1 个启用等级")
    lv.is_active = not lv.is_active
    db.commit()
    db.refresh(lv)
    _audit(db, current_admin.id, "member_level_toggle", "member_level", lv.id, {"is_active": lv.is_active})
    return {"level": _level_to_dict(lv)}
