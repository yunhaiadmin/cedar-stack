# -*- coding: utf-8 -*-
"""福利邀请：送好友试用（被邀请人见面礼），口径映射自友站福利邀请。

友站机制（2026-11-13 对账报告实查）：福利邀请 = 送试用、每月 2 次、
邀请人充值 > $10 才有资格。映射到我方（人民币口径、全走配置键）：

- 被邀请人经邀请注册成功 → 得 ``invite_trial_days`` 天试用订阅
  （体验权益、不可变现，与返利「只挂真实付费」不冲突）；
- 邀请人资格门槛：累计实付（已支付订阅订单额合计）≥
  ``invite_welfare_min_paid_fen``（默认 1000 分 = ¥10，友站 $10 的
  人民币占位映射，管理端可改；0 = 无门槛）；
- 次数上限：每位邀请人每月最多送出 ``invite_welfare_monthly_limit``
  次（默认 2；0 = 福利试用关闭）；
- 总开关 ``invite_trial_enabled``（默认开）+ 天数默认 0 —— 与推广
  奖励同一出厂口径（开关开、值为 0 = 不实际发放），管理员在管理端
  「邀请管理」页把天数调大即生效，不许在代码里写死天数。

发放留痕走 ``invite_trial_grants``（被邀请人唯一索引，防重复领取），
与邀请关系同一事务落盘；失败由调用方（apply_invitation）用保存点
隔离，绝不阻塞注册。防刷的其余层（注册 IP 限流、验证码）在注册
端点已有；设备指纹类风控本站尚无，见邀请单报告的缺口清单。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.agents import money
from backend.integrations import store

logger = logging.getLogger(__name__)

CONFIG_ENABLED = "invite_trial_enabled"
CONFIG_DAYS = "invite_trial_days"
CONFIG_MONTHLY_LIMIT = "invite_welfare_monthly_limit"
CONFIG_MIN_PAID_FEN = "invite_welfare_min_paid_fen"

#: 出厂默认：开关开、天数 0（不发）、月 2 次、邀请人实付门槛 ¥10
DEFAULTS: dict[str, str] = {
    CONFIG_ENABLED: "true",
    CONFIG_DAYS: "0",
    CONFIG_MONTHLY_LIMIT: "2",
    CONFIG_MIN_PAID_FEN: "1000",
}

DESCRIPTIONS = {
    CONFIG_ENABLED: "福利邀请（送好友试用）总开关",
    CONFIG_DAYS: "福利邀请试用天数（被邀请人注册得试用订阅；0 = 不发）",
    CONFIG_MONTHLY_LIMIT: "福利邀请每月次数上限（每位邀请人；0 = 关闭福利试用）",
    CONFIG_MIN_PAID_FEN: "福利邀请资格门槛（邀请人累计实付，分；0 = 无门槛）",
}

DAYS_MIN, DAYS_MAX = 0, 3650
LIMIT_MIN, LIMIT_MAX = 0, 1000
PAID_MIN, PAID_MAX = 0, 100_000_000


def _raw(db: Session, key: str) -> str:
    return (store.get_value(db, key, DEFAULTS.get(key, ""), ttl=0) or "").strip()


def _int(db: Session, key: str, low: int, high: int) -> int:
    try:
        number = int(float(_raw(db, key) or DEFAULTS[key]))
    except (TypeError, ValueError):
        number = int(DEFAULTS[key])
    return max(low, min(high, number))


def config(db: Session) -> dict:
    return {
        "enabled": _raw(db, CONFIG_ENABLED).lower() in ("1", "true", "yes", "on"),
        "days": _int(db, CONFIG_DAYS, DAYS_MIN, DAYS_MAX),
        "monthly_limit": _int(db, CONFIG_MONTHLY_LIMIT, LIMIT_MIN, LIMIT_MAX),
        "min_paid_fen": _int(db, CONFIG_MIN_PAID_FEN, PAID_MIN, PAID_MAX),
    }


def policy_payload(db: Session) -> dict:
    cfg = config(db)
    return {**cfg, "active": bool(
        cfg["enabled"] and cfg["days"] > 0 and cfg["monthly_limit"] > 0)}


def write_policy(db: Session, values: dict) -> dict:
    """写回配置（只认白名单键；非法值保持原值不动）。不提交。"""
    applied: dict = {}
    for key, value in (values or {}).items():
        if key not in DEFAULTS:
            continue
        if key == CONFIG_ENABLED:
            text = "true" if str(value).lower() in ("1", "true", "yes", "on") else "false"
        elif key == CONFIG_DAYS:
            low, high = DAYS_MIN, DAYS_MAX
            try:
                text = str(max(low, min(high, int(float(str(value).strip() or "0")))))
            except (TypeError, ValueError):
                continue
        elif key == CONFIG_MONTHLY_LIMIT:
            low, high = LIMIT_MIN, LIMIT_MAX
            try:
                text = str(max(low, min(high, int(float(str(value).strip() or "0")))))
            except (TypeError, ValueError):
                continue
        else:  # CONFIG_MIN_PAID_FEN
            low, high = PAID_MIN, PAID_MAX
            try:
                text = str(max(low, min(high, int(float(str(value).strip() or "0")))))
            except (TypeError, ValueError):
                continue
        applied[key] = text
    if applied:
        store.write_values(db, applied, DESCRIPTIONS)
        store.invalidate(*applied.keys())
    return applied


# ==================== 资格与用量 ====================

def inviter_paid_total_fen(db: Session, inviter_id: int) -> int:
    """邀请人累计实付（分）：已支付订阅订单 amount 合计（元 → 分）。"""
    total = (
        db.query(func.coalesce(func.sum(models.SubscriptionOrder.amount), 0))
        .filter(models.SubscriptionOrder.user_id == inviter_id,
                models.SubscriptionOrder.status == "paid")
        .scalar()
    )
    return int(money(total or 0) * 100)


def welfare_used_this_month(db: Session, inviter_id: int,
                            now: Optional[datetime] = None) -> int:
    """本月已送出的福利试用次数（按自然月，服务器本地时间口径）。"""
    now = now or datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(models.InviteTrialGrant)
        .filter(models.InviteTrialGrant.inviter_id == inviter_id,
                models.InviteTrialGrant.created_at >= month_start)
        .count()
    )


def welfare_status(db: Session, inviter: models.WebUser) -> dict:
    """福利邀请页要展示的资格全貌（用户页与管理端共用口径）。"""
    cfg = config(db)
    paid_fen = inviter_paid_total_fen(db, inviter.id)
    used = welfare_used_this_month(db, inviter.id)
    eligible = bool(
        cfg["enabled"] and cfg["days"] > 0 and cfg["monthly_limit"] > 0
        and used < cfg["monthly_limit"] and paid_fen >= cfg["min_paid_fen"]
    )
    return {
        **cfg,
        "used_this_month": used,
        "inviter_paid_fen": paid_fen,
        "eligible": eligible,
    }


# ==================== 发放 ====================

def grant_invitee_trial(db: Session, inviter: models.WebUser,
                        invitee: models.WebUser) -> Optional[models.InviteTrialGrant]:
    """给被邀请人发试用订阅并留痕；不满足任一条件返回 None（只跳过）。

    **不提交、会 flush**（调用方在保存点里调，失败回滚保存点即可）：
    天数发放（codes.grant_membership_days）与留痕行同事务落盘，
    被邀请人唯一索引是防重复领取的最后一道门。
    """
    cfg = config(db)
    if not cfg["enabled"] or cfg["days"] <= 0 or cfg["monthly_limit"] <= 0:
        return None

    existing = db.query(models.InviteTrialGrant).filter(
        models.InviteTrialGrant.invitee_id == invitee.id).first()
    if existing:
        return None

    if welfare_used_this_month(db, inviter.id) >= cfg["monthly_limit"]:
        logger.info("福利邀请本月次数已用完，跳过试用: inviter=%s", inviter.id)
        return None
    if inviter_paid_total_fen(db, inviter.id) < cfg["min_paid_fen"]:
        logger.info("福利邀请资格门槛未达，跳过试用: inviter=%s", inviter.id)
        return None

    from backend import codes

    subscription = codes.grant_membership_days(db, invitee, cfg["days"], None)
    row = models.InviteTrialGrant(
        inviter_id=inviter.id,
        invitee_id=invitee.id,
        days=cfg["days"],
        realm_id=subscription.realm_id,
    )
    db.add(row)
    db.flush()
    logger.info("福利邀请试用已发放: inviter=%s invitee=%s days=%s",
                inviter.id, invitee.id, cfg["days"])
    return row


__all__ = [
    "CONFIG_DAYS", "CONFIG_ENABLED", "CONFIG_MIN_PAID_FEN",
    "CONFIG_MONTHLY_LIMIT", "DEFAULTS", "DESCRIPTIONS",
    "config", "grant_invitee_trial", "inviter_paid_total_fen",
    "policy_payload", "welfare_status", "welfare_used_this_month",
    "write_policy",
]
