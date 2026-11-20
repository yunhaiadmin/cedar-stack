# -*- coding: utf-8 -*-
"""福利邀请：送好友试用（生成试用码机制），口径映射自友站福利邀请。

友站机制（2026-11-13 对账报告实查、2026-11-15 逐帧复核）：福利邀请
= 邀请人主动「生成试用邀请码」送好友、每月 2 次、邀请人充值 > $10
才有资格。映射到我方（美元口径、全走配置键；2026-11-15 币种改美刀，
门槛直接用友站原值 $10）：

- 邀请人在福利邀请页**主动生成**福利试用码（``invite_trial_codes``），
  生成即占当月次数：本月口径 = 当月生成数（待使用/已使用都算，
  过期不返还）；好友经分享链接注册后**凭码领取** ``invite_trial_days``
  天试用订阅（体验权益、不可变现，与返利「只挂真实付费」不冲突）；
- 邀请人资格门槛：累计成功充值（已支付钱包充值订单入账额
  credit_usd_fen 合计）≥ ``invite_welfare_min_paid_fen``
  （默认 1000 美分 = $10，友站原值，
  管理端可改；0 = 无门槛）；
- 次数上限：每位邀请人每月最多生成 ``invite_welfare_monthly_limit``
  个码（默认 2；0 = 福利试用关闭）；
- 总开关 ``invite_trial_enabled``（默认开）+ 天数默认 0 —— 与推广
  奖励同一出厂口径（开关开、值为 0 = 不实际发放），管理员在管理端
  「邀请管理」页把天数调大即生效，不许在代码里写死天数。
- 码面快照：生成时把当时的天数与试用专属套餐 ID 快照进码行，领取
  按快照发放（管理员事后改配置不影响已发出的码）；码生成后 30 天
  未用自动过期（推导口径：友站未公开码有效期，取与月度周期同量级
  的 30 天），过期不返还当月次数。
- 试用订阅默认建在卡码口径的默认套餐上（权益跟随该套餐声明）；
  管理员可另建一个隐藏的试用专属套餐（如只声明 30G 月流量），把
  其 ID 写进 ``invite_trial_plan_id``，试用便建在该套餐上、流量等
  权益按该套餐算。0/未配 = 不指定；指定套餐不存在或属于别的服时
  回退默认口径并发放照常（见 codes 侧校验）。

历史口径（2026-11-15 改版退役）：此前是「被邀请人经邀请注册成功
→ 自动发试用」，从未真实发放过（无人累计实付满 $10），无存量；
改版后邀请注册只建关系/记返利，**不再自动发试用**，发放只走
凭码领取。发放留痕仍走 ``invite_trial_grants``（被邀请人唯一索引，
防重复领取；新增 ``code_id`` 关联来源码）。防刷的其余层（注册 IP
限流、验证码）在注册端点已有；设备指纹类风控本站尚无。
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.integrations import store

logger = logging.getLogger(__name__)

CONFIG_ENABLED = "invite_trial_enabled"
CONFIG_DAYS = "invite_trial_days"
CONFIG_MONTHLY_LIMIT = "invite_welfare_monthly_limit"
CONFIG_MIN_PAID_FEN = "invite_welfare_min_paid_fen"
CONFIG_PLAN_ID = "invite_trial_plan_id"

#: 出厂默认：开关开、天数 0（不发）、月 2 次、邀请人实付门槛 $10、
#: 试用专属套餐不指定（0 = 沿用卡码默认套餐口径）
DEFAULTS: dict[str, str] = {
    CONFIG_ENABLED: "true",
    CONFIG_DAYS: "0",
    CONFIG_MONTHLY_LIMIT: "2",
    CONFIG_MIN_PAID_FEN: "1000",
    CONFIG_PLAN_ID: "0",
}

DESCRIPTIONS = {
    CONFIG_ENABLED: "福利邀请（送好友试用）总开关",
    CONFIG_DAYS: "福利邀请试用天数（被邀请人注册得试用订阅；0 = 不发）",
    CONFIG_MONTHLY_LIMIT: "福利邀请每月次数上限（每位邀请人；0 = 关闭福利试用）",
    CONFIG_MIN_PAID_FEN: "福利邀请资格门槛（邀请人累计实付，美分；0 = 无门槛）",
    CONFIG_PLAN_ID: "福利邀请试用专属套餐 ID（0 = 不指定、沿用默认套餐口径；"
                    "指定后试用订阅建在该套餐上，流量等权益按该套餐声明）",
}

DAYS_MIN, DAYS_MAX = 0, 3650
LIMIT_MIN, LIMIT_MAX = 0, 1000
PAID_MIN, PAID_MAX = 0, 100_000_000
PLAN_ID_MIN, PLAN_ID_MAX = 0, 100_000_000


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
        "plan_id": _int(db, CONFIG_PLAN_ID, PLAN_ID_MIN, PLAN_ID_MAX),
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
        elif key == CONFIG_PLAN_ID:
            low, high = PLAN_ID_MIN, PLAN_ID_MAX
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
    """邀请人累计成功充值（美分）：已支付钱包充值订单入账额
    credit_usd_fen 合计（status == "paid" 才算，pending 不计）。"""
    total = (
        db.query(func.coalesce(func.sum(models.WalletTopupOrder.credit_usd_fen), 0))
        .filter(models.WalletTopupOrder.user_id == inviter_id,
                models.WalletTopupOrder.status == "paid")
        .scalar()
    )
    return int(total or 0)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def welfare_generated_this_month(db: Session, inviter_id: int,
                                 now: Optional[datetime] = None) -> int:
    """本月已生成的福利码数（按自然月，服务器本地时间口径）。

    生成即占次数：待使用/已使用/已过期都计入，过期不返还——这是
    改版后的唯一月度计数口径（``welfare_status`` 与生成校验共用）。
    """
    now = now or datetime.now()
    return (
        db.query(models.InviteTrialCode)
        .filter(models.InviteTrialCode.creator_id == inviter_id,
                models.InviteTrialCode.created_at >= _month_start(now))
        .count()
    )


def welfare_code_split_this_month(db: Session, inviter_id: int,
                                  now: Optional[datetime] = None) -> dict:
    """本月生成码的状态拆分：待使用（pending）/ 已使用（used）。

    已过期码在生成时就占了次数，计入总数但不进这两个分项
    （总数 − 待使用 − 已使用 = 本月已过期数）。
    """
    now = now or datetime.now()
    rows = (
        db.query(models.InviteTrialCode.status, func.count(models.InviteTrialCode.id))
        .filter(models.InviteTrialCode.creator_id == inviter_id,
                models.InviteTrialCode.created_at >= _month_start(now))
        .group_by(models.InviteTrialCode.status)
        .all()
    )
    split = {"pending": 0, "used": 0}
    for status_, count in rows:
        if status_ in split:
            split[status_] = int(count or 0)
    return split


def welfare_used_this_month(db: Session, inviter_id: int,
                            now: Optional[datetime] = None) -> int:
    """本月已占用的福利次数 —— 改版后 = 本月生成码数（见
    ``welfare_generated_this_month``）。函数名保留：``welfare_status``
    的 ``used_this_month`` 字段与既有调用方都按这个口径读。"""
    return welfare_generated_this_month(db, inviter_id, now)


def trial_plan_summary(db: Session, cfg: Optional[dict] = None) -> dict:
    """试用选卡要展示的套餐摘要：配置指定了专属套餐且套餐存在时给
    名称与月流量；未指定/套餐不存在时名称回退「试用套餐」、流量为
    None（前端只展示天数）。"""
    cfg = cfg or config(db)
    plan = None
    if cfg["plan_id"]:
        plan = db.query(models.SubscriptionPlan).filter(
            models.SubscriptionPlan.id == cfg["plan_id"]).first()
    if plan is None:
        return {"plan_id": cfg["plan_id"] or None, "plan_name": "试用套餐",
                "monthly_traffic_bytes": None}
    entitlements = plan.entitlements or {}
    traffic = entitlements.get("monthly_traffic_bytes")
    return {
        "plan_id": plan.id,
        "plan_name": plan.name,
        "monthly_traffic_bytes": int(traffic) if traffic else None,
    }


def welfare_status(db: Session, inviter: models.WebUser) -> dict:
    """福利邀请页要展示的资格全貌（用户页与管理端共用口径）。"""
    cfg = config(db)
    paid_fen = inviter_paid_total_fen(db, inviter.id)
    generated = welfare_generated_this_month(db, inviter.id)
    split = welfare_code_split_this_month(db, inviter.id)
    eligible = bool(
        cfg["enabled"] and cfg["days"] > 0 and cfg["monthly_limit"] > 0
        and generated < cfg["monthly_limit"] and paid_fen >= cfg["min_paid_fen"]
    )
    return {
        **cfg,
        "used_this_month": generated,
        "pending_this_month": split["pending"],
        "used_codes_this_month": split["used"],
        "remaining_this_month": max(0, cfg["monthly_limit"] - generated),
        "inviter_paid_fen": paid_fen,
        "eligible": eligible,
        "plan": trial_plan_summary(db, cfg),
    }


# ==================== 福利码：生成 / 领取 ====================

#: 码有效期（推导口径，模块头已注明）：生成后 30 天未用自动过期。
CODE_TTL_DAYS = 30

#: 码面：10 位大写字母+数字（与邀请码同字母表、位数不同以便区分）。
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LENGTH = 10

STATUS_PENDING = "pending"
STATUS_USED = "used"
STATUS_EXPIRED = "expired"


class TrialCodeError(Exception):
    """福利码生成/领取的业务拒绝：``code`` 是稳定类别键，API 层据此
    映射状态码与文案（不许把内部细节直接抛给前端）。"""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _generate_code_str(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not db.query(models.InviteTrialCode).filter(
                models.InviteTrialCode.code == code).first():
            return code


def effective_status(row: models.InviteTrialCode,
                     now: Optional[datetime] = None) -> str:
    """码的实际状态：pending 且已过 expires_at 的按 expired 读
    （惰性过期——落库推进由 ``refresh_code_status`` 在写路径上做，
    读路径只按这个口径展示，不写库）。"""
    now = now or datetime.now()
    if (row.status == STATUS_PENDING and row.expires_at is not None
            and row.expires_at <= now):
        return STATUS_EXPIRED
    return row.status


def refresh_code_status(db: Session, row: models.InviteTrialCode,
                        now: Optional[datetime] = None) -> str:
    """把惰性过期的码落库为 expired（只推进状态，不提交）。"""
    status = effective_status(row, now)
    if status != row.status:
        row.status = status
        db.flush()
    return status


def generate_trial_code(db: Session, creator: models.WebUser,
                        now: Optional[datetime] = None) -> models.InviteTrialCode:
    """邀请人主动生成一张福利试用码（生成即占当月次数）。

    **不提交、会 flush**：资格（开关/天数/月次数/实付门槛）任一不满足
    抛 ``TrialCodeError``；天数与套餐按生成时配置快照进码行，领取
    按快照发放。撞号（唯一索引）时重生成一次。
    """
    now = now or datetime.now()
    cfg = config(db)
    if not cfg["enabled"] or cfg["days"] <= 0 or cfg["monthly_limit"] <= 0:
        raise TrialCodeError("福利试用暂未开启", "trial_inactive")
    if welfare_generated_this_month(db, creator.id, now) >= cfg["monthly_limit"]:
        raise TrialCodeError("本月福利次数已用完，下个月再来", "monthly_limit")
    if inviter_paid_total_fen(db, creator.id) < cfg["min_paid_fen"]:
        raise TrialCodeError("累计付费未达到福利邀请资格门槛", "below_threshold")

    row = None
    for _attempt in range(2):
        row = models.InviteTrialCode(
            code=_generate_code_str(db),
            creator_id=creator.id,
            plan_id=cfg["plan_id"] or None,
            days=cfg["days"],
            status=STATUS_PENDING,
            created_at=now,
            expires_at=now + timedelta(days=CODE_TTL_DAYS),
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            logger.info("福利码并发撞号，已重生成重试: creator=%s", creator.id)
            row = None
            continue
        break
    if row is None or row.id is None:
        raise TrialCodeError("福利码生成失败，请重试", "generate_failed")
    logger.info("福利试用码已生成: creator=%s code_id=%s days=%s",
                creator.id, row.id, row.days)
    return row


def claim_trial_code(db: Session, code_str: str,
                     claimer: models.WebUser) -> models.InviteTrialGrant:
    """好友凭码领取试用订阅：码置 used + 按码上快照发放 + 留痕，同事务。

    **不提交、会 flush**；任一条件不满足抛 ``TrialCodeError``：
    码不存在/已使用/已过期、自己领自己的码、领取人已领过试用
    （``invite_trial_grants`` 的 invitee 唯一索引兜底）。已发出的码
    按快照兑现——管理员事后关开关/改天数不追溯已生成码。
    并发防重：先用条件 UPDATE 原子占位（pending → used），抢不到
    直接拒绝，不会双发（与邀请码 ``_claim_invitation_use`` 同写法）。
    """
    code = (code_str or "").strip().upper()
    row = db.query(models.InviteTrialCode).filter(
        models.InviteTrialCode.code == code).first()
    if row is None:
        raise TrialCodeError("福利码不存在", "not_found")
    if row.creator_id == claimer.id:
        raise TrialCodeError("不能领取自己生成的福利码", "self_claim")
    status = refresh_code_status(db, row)
    if status == STATUS_USED:
        raise TrialCodeError("这个福利码已经被领取过了", "already_used")
    if status == STATUS_EXPIRED:
        raise TrialCodeError("这个福利码已过期", "expired")
    existing = db.query(models.InviteTrialGrant).filter(
        models.InviteTrialGrant.invitee_id == claimer.id).first()
    if existing:
        raise TrialCodeError("你已经领取过试用了", "already_granted")

    now = datetime.now()
    claimed = (
        db.query(models.InviteTrialCode)
        .filter(models.InviteTrialCode.id == row.id,
                models.InviteTrialCode.status == STATUS_PENDING)
        .update({models.InviteTrialCode.status: STATUS_USED,
                 models.InviteTrialCode.used_by: claimer.id,
                 models.InviteTrialCode.used_at: now},
                synchronize_session=False)
    )
    if not claimed:
        db.rollback()
        raise TrialCodeError("这个福利码已经被领取过了", "already_used")
    # 同步会话内实例（bulk UPDATE 不自动同步），后续 DTO 直接读 row。
    db.refresh(row)

    from backend import codes

    # 按码上快照发放：套餐不存在/跨服的校验与回退在 codes 侧
    # （记 warning、照常发放），plan_id 为空走现行默认套餐口径。
    subscription = codes.grant_membership_days(
        db, claimer, row.days, None, plan_id=row.plan_id or None)
    grant = models.InviteTrialGrant(
        inviter_id=row.creator_id,
        invitee_id=claimer.id,
        days=row.days,
        realm_id=subscription.realm_id,
        code_id=row.id,
    )
    db.add(grant)
    db.flush()
    logger.info("福利试用码已领取: code_id=%s creator=%s claimer=%s days=%s",
                row.id, row.creator_id, claimer.id, row.days)
    return grant


def trial_code_dto(row: models.InviteTrialCode,
                   creator_name: Optional[str] = None,
                   claimer_name: Optional[str] = None) -> dict:
    """福利码行 → 前端 DTO（状态按惰性过期口径下发）。"""
    return {
        "id": row.id,
        "code": row.code,
        "creator_id": row.creator_id,
        "creator_username": creator_name,
        "plan_id": row.plan_id,
        "days": row.days,
        "status": effective_status(row),
        "used_by": row.used_by,
        "used_by_username": claimer_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "used_at": row.used_at.isoformat() if row.used_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def my_trial_codes(db: Session, creator_id: int, limit: int = 50) -> list:
    """我生成的福利码（最新在前），给福利邀请页「我的福利码」区。"""
    rows = (
        db.query(models.InviteTrialCode)
        .filter(models.InviteTrialCode.creator_id == creator_id)
        .order_by(models.InviteTrialCode.id.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    claimer_names = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_({r.used_by for r in rows if r.used_by}))
        .all()
    } if rows else {}
    return [trial_code_dto(r, claimer_name=claimer_names.get(r.used_by))
            for r in rows]


__all__ = [
    "CODE_TTL_DAYS", "CONFIG_DAYS", "CONFIG_ENABLED", "CONFIG_MIN_PAID_FEN",
    "CONFIG_MONTHLY_LIMIT", "CONFIG_PLAN_ID", "DEFAULTS", "DESCRIPTIONS",
    "STATUS_EXPIRED", "STATUS_PENDING", "STATUS_USED", "TrialCodeError",
    "claim_trial_code", "config", "effective_status", "generate_trial_code",
    "inviter_paid_total_fen", "my_trial_codes", "policy_payload",
    "refresh_code_status", "trial_code_dto", "trial_plan_summary",
    "welfare_code_split_this_month", "welfare_generated_this_month",
    "welfare_status", "welfare_used_this_month", "write_policy",
]
