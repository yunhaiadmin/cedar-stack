# -*- coding: utf-8 -*-
"""机器人配置管理后台 API（历史文件名 welfare_admin）

- GET/PUT /welfare/config          配置读写（机器人键）

公益用户/资格发放端点已随公益资格整族于 2026-11-14 删除；
群抽奖端点（/welfare/lottery/*）已随群抽奖整族于 2026-11-23 下线删除，
抽奖配置键与模型随后清扫（数据表由部署后 DDL 收口）。
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import admin_router, get_current_admin, _audit
from backend.database import get_db


# ==================== 配置（机器人） ====================

WELFARE_CONFIG_KEYS = {
    # 公益资格族键已随公益资格整族于 2026-11-14 删除；注册送查看
    # 凭证两键已随「注册不送体验」（2026-11-15）失效删除；抽奖族键
    # 已随群抽奖整族于 2026-11-23 删除。本字典只剩机器人等在用键
    # （字典名与端点路径为历史名，不动以免断现行调用）。
    # P0 统一货币体系：充值比例
    "recharge_ratio": "1.2",
    # C4 商店改造：快捷金额（2026-11-15 币种改美刀：USD 档位）
    "recharge_quick_amounts": "5,10,20,50,100",
    # B4 TG Bot 总控：总开关、群白名单、限流、各命令开关
    "bot_group_ids": "",
    "bot_rate_limit_seconds": "3",
    "bot_group_rate_limit": "20",
    # 观影榜海报日推：总开关默认开（名次发分已随积分删除摘除，旧键已删）
    "bot_rankpush_enabled": "true",
    # 更新入库播报：总开关默认开 / 攒批间隔默认 30 分钟 / 发送目标默认空（发群）
    "bot_update_digest_enabled": "true",
    "bot_update_digest_interval_minutes": "30",
    "bot_update_digest_chat_id": "",
    # 每日播放排行榜：总开关默认关 / 发送时间默认 21:30 /
    # 每榜默认 5 条 / 三榜分开关默认开 / 海报图默认开
    "bot_playrank_enabled": "false",
    "bot_playrank_time": "21:30",
    "bot_playrank_top_n": "5",
    "bot_playrank_users_enabled": "true",
    "bot_playrank_series_enabled": "true",
    "bot_playrank_movies_enabled": "true",
    "bot_playrank_poster_enabled": "true",
    # 主动私聊提醒：求片到货 / 转存终态，默认开（2026-11-08 私聊通报
    # 优化批）；同一人合并窗口默认 60 秒。新求片提醒管理员默认开。
    "tg_notify_store_arrival_enabled": "true",
    "tg_notify_transfer_done_enabled": "true",
    "tg_notify_new_request_enabled": "true",
    "tg_notify_merge_window_sec": "60",
    # 许愿到货群报喜（tg_bot.group_cheer）：默认开，关了只发私聊不发群。
    "tg_notify_group_arrival_enabled": "true",
    # 新贡献者群欢迎（tg_bot.group_cheer）：审核通过时发，默认开。
    "tg_notify_group_pool_welcome_enabled": "true",
    # 频道马甲治理＋退群处置（2026-10-30 借鉴 embyboss 第 1 项；读侧
    # tg_bot.policy，缺省与 policy.DEFAULTS 逐字对齐）
    "bot_channel_guard_enabled": "true",
    "bot_channel_whitelist": "",
    "bot_leave_action": "none",
    # 注册后 TG 绑定引导页总开关
    # 求片 v2：总开关 / 附议 / 附议者入库通知（默认全开）
    # 发言积分/积分兑换/积分转账键已随货币于 2026-11-13 下线（原
    # points_chat_*、points_redeem_*、points_transfer_* 与 points.py 一并删除）。
}


@admin_router.get("/welfare/config")
def welfare_config_get(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取配置（机器人键）"""
    result = {}
    for key, default in WELFARE_CONFIG_KEYS.items():
        cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
        result[key] = cfg.value if cfg else default
    return result


@admin_router.put("/welfare/config")
def welfare_config_set(
    data: dict,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存配置（数值/开关键按 config_schema 校验，非法 400；空字符串=未设置，回退默认值）"""
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="配置必须是对象")
    # 配置瘦身第 4 批 4-4：白名单过滤保留在端点层（未知键静默忽略，行为不变），
    # 校验→落库→写时失效收敛到 config_service。bool 风格（digit/word）已登记
    # 进 WELFARE_CONFIG_SCHEMA 的 spec，normalize 口径与今天逐字一致。
    values = {key: value for key, value in data.items() if key in WELFARE_CONFIG_KEYS}
    if values:
        # 校验失败抛 ConfigValueError，由 main.py 的统一 handler 转 400
        config_service.save_config(db, values, schema="welfare")
    updated = list(values)
    _audit(db, current_admin.id, "welfare_config_update", "system", None, {"updated": updated})
    db.commit()
    return {"success": True, "updated": updated}
