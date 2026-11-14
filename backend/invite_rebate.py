# -*- coding: utf-8 -*-
"""邀请返利计提（钱包时代）：被邀请人真实付费 → 按比例给邀请人计提返利。

口径（2026-11-14 用户定版，全走配置键、不写死）：

- 返利模式 ``wallet_invite_rebate_mode`` 两档可切换（默认 tiered，
  2026-11-14 09:06 用户拍板照友站分档；flat 保留可切换）：
  - flat（一律比例）：返利 = 被邀请人**实付金额** ×
    ``wallet_invite_rebate_percent``（默认 5，钱包方案 §8 的键，钱包
    批 1 已登记进配置注册表与管理端 Settings）；
  - tiered（分档，友站形态的人民币映射）：按**每位被邀请人累计付费**
    计——累计前 ``wallet_invite_rebate_tier_threshold``（分，默认
    35000 = ¥350，用户 2026-11-14 拍板的占位默认值）部分按
    ``wallet_invite_rebate_percent`` 返，超出部分按
    ``wallet_invite_rebate_tier_percent``（默认 1）返。
  两种模式切换只改配置、不改代码。
- ``wallet_invite_rebate_cap`` = 邀请人累计返利上限（分，0 = 不限、无门槛），
  两档模式都生效（先按模式算，再按剩余额度截断）；
- 注册即得的可变现奖励一律不发（邀请刷号事故教训）——返利只从真实
  付费产生，被邀请人的体验权益（试用天数）走 invite_trial，不经本模块。

金额纪律：库里只存人民币元 ``Numeric(12,2)``；比例计算先落到分再四舍
五入（``agents.money`` 的 ROUND_HALF_UP 口径，与全站金额列一致），
即 rebate = money(paid × percent / 100)，例：实付 ¥33.33 × 5% = ¥1.67。

与钱包的接合（不另造余额账）：

- 计提先写 ``invite_rebate_accruals``（source + source_ref 唯一，一笔
  付费只计提一次），状态 pending；
- 钱包模块（钱包批 1 的 ``backend.wallet``）在运行时可用且总开关开着
  时，立即经 ``wallet.credit(kind="rebate", ref_id="rebate:<订单号>")``
  入账（与钱包方案 §6.1 的 ref 格式一致），状态转 credited 并回填
  钱包流水 id；钱包不在（批 1 未合并）或被关停时保持 pending 挂账，
  由 ``settle_pending_rebates`` 在钱包就绪后补入账——账一笔不丢。
- 本模块**不提交**：计提与调用方的履约事务同生共死（订单回滚则返利
  一起回滚）；钱包入账若在同一事务里失败，调用方应回滚到保存点后
  只保留 pending 计提（见 economy._fulfill_order 的接法）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.agents import money
from backend.integrations import store

logger = logging.getLogger(__name__)

CONFIG_PERCENT = "wallet_invite_rebate_percent"
CONFIG_CAP = "wallet_invite_rebate_cap"
CONFIG_MODE = "wallet_invite_rebate_mode"
CONFIG_TIER_THRESHOLD = "wallet_invite_rebate_tier_threshold"
CONFIG_TIER_PERCENT = "wallet_invite_rebate_tier_percent"

#: 出厂默认：比例 5%（与钱包方案 §8、钱包批 1 WALLET_CONFIG_DEFAULTS
#: 一致），上限 0 分 = 不限。模式默认 tiered（2026-11-14 09:06 用户
#: 拍板照友站分档）：阈值 ¥350（约等值友站 50 USD，配置可改）、
#: 超出部分 1%。flat 保留为配置可切换的模式。
MODE_FLAT = "flat"
MODE_TIERED = "tiered"
DEFAULT_PERCENT = 5
DEFAULT_CAP_FEN = 0
DEFAULT_MODE = MODE_TIERED
DEFAULT_TIER_THRESHOLD_FEN = 35000
DEFAULT_TIER_PERCENT = 1

SOURCE_SUBSCRIPTION_ORDER = "subscription_order"
SOURCE_WALLET_TOPUP = "wallet_topup"        # 钱包批 3（在线充值）预留
SOURCE_WALLET_PURCHASE = "wallet_purchase"  # 钱包批 2（余额购买）预留

STATUS_PENDING = "pending"
STATUS_CREDITED = "credited"


# ==================== 配置 ====================

def _int_config(db: Session, key: str, default: int) -> int:
    raw = store.get_value(db, key, str(default), ttl=0)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def rebate_percent(db: Session) -> int:
    """生效返利比例（%）：坏值/越界回默认 5，负数按 0（不返）。"""
    value = _int_config(db, CONFIG_PERCENT, DEFAULT_PERCENT)
    return max(0, min(100, value))


def rebate_cap_fen(db: Session) -> int:
    """生效返利上限（分）：0 = 不限；坏值回 0（不限）。"""
    value = _int_config(db, CONFIG_CAP, DEFAULT_CAP_FEN)
    return max(0, value)


def rebate_mode(db: Session) -> str:
    """生效返利模式：flat / tiered；坏值回默认 tiered。"""
    raw = store.get_value(db, CONFIG_MODE, DEFAULT_MODE, ttl=0)
    value = str(raw or "").strip().lower()
    return value if value in (MODE_FLAT, MODE_TIERED) else DEFAULT_MODE


def tier_threshold_fen(db: Session) -> int:
    """分档阈值（分）：每位被邀请人累计付费的前段上限；坏值回默认。"""
    value = _int_config(db, CONFIG_TIER_THRESHOLD, DEFAULT_TIER_THRESHOLD_FEN)
    return max(0, value)


def tier_percent(db: Session) -> int:
    """分档超出部分比例（%）：坏值/越界回默认 1。"""
    value = _int_config(db, CONFIG_TIER_PERCENT, DEFAULT_TIER_PERCENT)
    return max(0, min(100, value))


def config_snapshot(db: Session) -> dict:
    return {
        "mode": rebate_mode(db),
        "percent": rebate_percent(db),
        "cap_fen": rebate_cap_fen(db),
        "tier_threshold_fen": tier_threshold_fen(db),
        "tier_percent": tier_percent(db),
    }


# ==================== 计算 ====================

def compute_rebate(paid_amount, percent: int) -> Decimal:
    """返利金额（元）：实付 × 比例%，按分四舍五入（ROUND_HALF_UP）。

    例：¥45.00 × 5% = ¥2.25；¥33.33 × 5% = ¥1.6665 → ¥1.67；
    ¥10.01 × 5% = ¥0.5005 → ¥0.50。比例 0 或实付 ≤0 时为 0。
    """
    paid = money(paid_amount)
    if paid <= 0 or percent <= 0:
        return Decimal("0")
    return money(paid * Decimal(percent) / Decimal(100))


def compute_rebate_tiered(paid_amount, base_percent: int, tier_percent_: int,
                          prior_paid, threshold) -> Decimal:
    """分档返利（元）：按该被邀请人累计付费拆段计算，整笔最后按分取整一次。

    - ``prior_paid``：这笔之前该被邀请人已累计付费（元）；
    - 累计未达 ``threshold`` 的部分按 ``base_percent`` 返，超出部分按
      ``tier_percent_`` 返；一笔付费正好跨过阈值时按段拆算。
    例（阈值 ¥350、5%/1%）：首付 ¥400 → 350×5% + 50×1% = ¥18.00；
    已付 ¥350 后再付 ¥100 → 全部按 1% = ¥1.00；
    已付 ¥300 后付 ¥50（恰达阈值）→ 全部按 5% = ¥2.50。
    """
    paid = money(paid_amount)
    if paid <= 0:
        return Decimal("0")
    prior = money(prior_paid)
    limit = money(threshold)
    base_room = max(limit - prior, Decimal("0"))
    base_part = min(paid, base_room)
    tier_part = paid - base_part
    raw = (base_part * Decimal(base_percent) / Decimal(100)
           + tier_part * Decimal(tier_percent_) / Decimal(100))
    return money(raw)


def _accrued_total(db: Session, inviter_id: int) -> Decimal:
    """邀请人已计提返利总额（pending + credited，上限口径按已占额度算）。"""
    total = (
        db.query(func.coalesce(func.sum(models.InviteRebateAccrual.rebate_amount), 0))
        .filter(models.InviteRebateAccrual.inviter_id == inviter_id)
        .scalar()
    )
    return money(total or 0)


def _invitee_prior_paid(db: Session, inviter_id: int, invitee_id: int) -> Decimal:
    """某位被邀请人已累计付费（元）：按已计提行的 paid_amount 求和。

    分档口径锚在计提台账上：每笔产生返利的付费都有一行，累计值与
    返利明细天然一致；比例被配成 0 的时期不产生行，那段付费不计入
    累计（口径如实，报告中注明）。
    """
    total = (
        db.query(func.coalesce(func.sum(models.InviteRebateAccrual.paid_amount), 0))
        .filter(models.InviteRebateAccrual.inviter_id == inviter_id,
                models.InviteRebateAccrual.invitee_id == invitee_id)
        .scalar()
    )
    return money(total or 0)


# ==================== 计提与入账 ====================

def _try_credit(db: Session, accrual: models.InviteRebateAccrual) -> bool:
    """把一笔 pending 计提入邀请人钱包；钱包不可用/被关停返回 False。

    懒导入 ``backend.wallet``（钱包批 1）：模块不在（未合并）就只挂账。
    入账幂等键 ``rebate:<source>:<source_ref>`` 与唯一约束双保险：
    同一笔计提重复执行只入账一次。成功时回填状态/流水 id/时间并
    flush（不提交，提交归调用方）。失败抛错由调用方决定回滚粒度。
    """
    try:
        from backend import wallet
    except Exception:  # noqa: BLE001 — 钱包未就绪是预期状态，不是错误
        return False
    if not wallet.wallet_enabled(db):
        return False
    entry, _replayed = wallet.credit(
        db, accrual.inviter_id, accrual.rebate_amount,
        kind="rebate",
        ref_id=f"rebate:{accrual.source_ref}",
        note="邀请返利（好友付费）",
        idempotency_key=f"rebate:{accrual.source}:{accrual.source_ref}",
    )
    accrual.status = STATUS_CREDITED
    accrual.wallet_ledger_id = entry.id
    accrual.credited_at = datetime.now()
    db.flush()
    return True


def record_payment_rebate(
    db: Session, payer: models.WebUser, *, source: str, source_ref: str,
    paid_amount,
) -> Optional[models.InviteRebateAccrual]:
    """一笔真实付费的返利计提入口：查邀请关系 → 算返利 → 落计提 → 试入账。

    返回计提行；下列情况返回 None（不计提）：付款人没有邀请人、比例
    为 0、返利算出来 ≤ 0、邀请人返利上限已用满。同一 (source,
    source_ref) 重复调用返回既有行（幂等，支付回调重放安全）。

    **不提交**。钱包入账失败会向上抛——调用方（如履约）应在本函数外层
    套保存点：入账失败回滚保存点后，另起一次只落 pending 计提的调用，
    保证「计提不丢、入账可补」（见 settle_pending_rebates）。
    """
    existing = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.source == source,
                models.InviteRebateAccrual.source_ref == source_ref)
        .first()
    )
    if existing is not None:
        return existing

    relation = (
        db.query(models.InvitationRecord)
        .filter(models.InvitationRecord.invitee_id == payer.id)
        .first()
    )
    if relation is None:
        return None

    percent = rebate_percent(db)
    if rebate_mode(db) == MODE_TIERED:
        prior = _invitee_prior_paid(db, relation.inviter_id, payer.id)
        rebate = compute_rebate_tiered(
            paid_amount, percent, tier_percent(db), prior,
            Decimal(tier_threshold_fen(db)) / Decimal(100))
    else:
        rebate = compute_rebate(paid_amount, percent)
    if rebate <= 0:
        return None

    cap_fen = rebate_cap_fen(db)
    if cap_fen > 0:
        remaining = Decimal(cap_fen) / Decimal(100) - _accrued_total(db, relation.inviter_id)
        if remaining <= 0:
            logger.info("邀请返利上限已用满，跳过计提: inviter=%s", relation.inviter_id)
            return None
        rebate = min(rebate, money(remaining))

    accrual = models.InviteRebateAccrual(
        inviter_id=relation.inviter_id,
        invitee_id=payer.id,
        source=source,
        source_ref=source_ref,
        paid_amount=money(paid_amount),
        rebate_amount=rebate,
        percent=percent,
        status=STATUS_PENDING,
    )
    try:
        # 保存点隔离：并发同单撞唯一约束时只回滚本行插入，不伤调用方事务
        with db.begin_nested():
            db.add(accrual)
            db.flush()
    except IntegrityError:
        return (
            db.query(models.InviteRebateAccrual)
            .filter(models.InviteRebateAccrual.source == source,
                    models.InviteRebateAccrual.source_ref == source_ref)
            .first()
        )

    try:
        # 保存点隔离：入账的锁行/写流水若失败，只回滚入账尝试，
        # 计提行保持 pending 挂账（settle 时补），绝不拖垮调用方事务。
        with db.begin_nested():
            _try_credit(db, accrual)
    except Exception:  # noqa: BLE001 — 入账失败不许拖垮计提/履约，挂 pending
        logger.exception("返利入账失败，保持待入账: accrual=%s", accrual.id)
    return accrual


def settle_pending_rebates(db: Session, limit: int = 200) -> int:
    """把 pending 计提逐笔补入账（钱包就绪后用）；返回成功入账笔数。

    单笔失败只跳过该笔（保持 pending），不阻塞其余。**不提交**，
    调用方统一提交。
    """
    rows = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.status == STATUS_PENDING)
        .order_by(models.InviteRebateAccrual.id.asc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    settled = 0
    for row in rows:
        try:
            with db.begin_nested():
                if _try_credit(db, row):
                    settled += 1
        except Exception:  # noqa: BLE001
            logger.exception("返利补入账失败，跳过: accrual=%s", row.id)
    return settled


# ==================== 查询（用户页 / 管理端共用） ====================

def totals_for_inviter(db: Session, inviter_id: int) -> dict:
    """某邀请人的返利汇总（元）：已到账 / 待到账 / 笔数。"""
    rows = (
        db.query(models.InviteRebateAccrual.status,
                 func.coalesce(func.sum(models.InviteRebateAccrual.rebate_amount), 0),
                 func.count(models.InviteRebateAccrual.id))
        .filter(models.InviteRebateAccrual.inviter_id == inviter_id)
        .group_by(models.InviteRebateAccrual.status)
        .all()
    )
    result = {"credited": Decimal("0"), "pending": Decimal("0"), "count": 0}
    for status, total, count in rows:
        result["count"] += int(count or 0)
        if status == STATUS_CREDITED:
            result["credited"] = money(total or 0)
        elif status == STATUS_PENDING:
            result["pending"] = money(total or 0)
    return result


def totals_by_invitee(db: Session, inviter_id: int, invitee_ids: list[int]) -> dict[int, Decimal]:
    """按被邀请人归集返利总额（元，pending + credited），邀请记录页用。"""
    if not invitee_ids:
        return {}
    rows = (
        db.query(models.InviteRebateAccrual.invitee_id,
                 func.coalesce(func.sum(models.InviteRebateAccrual.rebate_amount), 0))
        .filter(models.InviteRebateAccrual.inviter_id == inviter_id,
                models.InviteRebateAccrual.invitee_id.in_(invitee_ids))
        .group_by(models.InviteRebateAccrual.invitee_id)
        .all()
    )
    return {iid: money(total or 0) for iid, total in rows}


def accrual_dto(row: models.InviteRebateAccrual, invitee_name: str = "") -> dict:
    return {
        "id": row.id,
        "inviter_id": row.inviter_id,
        "invitee_id": row.invitee_id,
        "invitee_username": invitee_name,
        "source": row.source,
        "source_ref": row.source_ref,
        "paid_amount": f"{money(row.paid_amount):.2f}",
        "rebate_amount": f"{money(row.rebate_amount):.2f}",
        "amount": f"{money(row.rebate_amount):.2f}",
        "percent": int(row.percent or 0),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "credited_at": row.credited_at.isoformat() if row.credited_at else None,
    }


__all__ = [
    "CONFIG_CAP", "CONFIG_MODE", "CONFIG_PERCENT", "CONFIG_TIER_PERCENT",
    "CONFIG_TIER_THRESHOLD",
    "DEFAULT_CAP_FEN", "DEFAULT_MODE", "DEFAULT_PERCENT",
    "DEFAULT_TIER_PERCENT", "DEFAULT_TIER_THRESHOLD_FEN",
    "MODE_FLAT", "MODE_TIERED",
    "SOURCE_SUBSCRIPTION_ORDER", "SOURCE_WALLET_PURCHASE", "SOURCE_WALLET_TOPUP",
    "STATUS_CREDITED", "STATUS_PENDING",
    "accrual_dto", "compute_rebate", "compute_rebate_tiered",
    "config_snapshot", "rebate_cap_fen", "rebate_mode", "rebate_percent",
    "record_payment_rebate", "settle_pending_rebates", "tier_percent",
    "tier_threshold_fen", "totals_by_invitee", "totals_for_inviter",
]
