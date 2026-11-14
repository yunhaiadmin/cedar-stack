# -*- coding: utf-8 -*-
"""钱包余额账本服务（钱包批 1：地基）。

全站唯一货币 = 现金余额（人民币元，Numeric(12,2)，绝不 float 运算）。
模式照抄已验证的两套现成账本：

- 代理余额（backend/agents.py）：条件 UPDATE 防扣负（``balance >= amount``
  才扣，rowcount==0 即余额不足），写完重读余额记 ``balance_after``；
- 流量时间换流量（backend/traffic_quota.py）：``idempotency_key`` 唯一 +
  先查后写 + 唯一约束兜底，重复提交原样回放、只成交一次。

流水只增不改（错账用冲正行纠），按用户串 prev_hash/record_hash 哈希链
（照 PointsLog 的 C3 审计语义：链头 prev_hash 为空字符串）。

批 1 只入账、不接扣款端点：``debit`` 是写好的扣款原语（条件 UPDATE +
幂等），供批 2 的订阅/流量购买调用，本批没有任何 HTTP 入口能走到它。
本模块只写账本与流水，**不提交**——提交由调用方端点统一负责。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.agents import money

logger = logging.getLogger(__name__)

#: 流水 kind 全集（与 models.WalletLedger 的口径一致，新 kind 先加这里）
LEDGER_KINDS = {
    "recharge",               # 在线充值（批 3）
    "admin_adjust",           # 管理员手工增减
    "gift_code",              # 兑换码 / 礼品码入账
    "rebate",                 # 邀请返利（批 2 计提）
    "subscription_purchase",  # 订阅购买扣款（批 2）
    "traffic_purchase",       # 购流量扣款（批 2）
    "refund",                 # 退款回余额（批 2）
    "rebate_reversal",        # 返利撤回（批 2）
}

#: 钱包配置默认值（§10 定版口径；DB 无行时按此生效，管理端可改）
WALLET_CONFIG_DEFAULTS = {
    "wallet_enabled": True,                    # 钱包总开关
    "wallet_min_topup_amount": 1000,           # 最低充值额（分）＝¥10
    "wallet_topup_fee_pct": 0,                 # 充值手续费比例（批 3 再议）
    "wallet_invite_rebate_percent": 5,         # 邀请返利比例（批 2 计提）
    "wallet_invite_rebate_cap": 0,             # 返利上限（分），0=不限
    # 返利分档三键与 #175 invite_rebate 的 DEFAULT_* 逐字一致（同文件
    # rebate_mode/tier_threshold_fen/tier_percent 的出厂默认）：
    "wallet_invite_rebate_mode": "tiered",     # 返利模式：tiered 分档 / flat 统一比例
    "wallet_invite_rebate_tier_threshold": 35000,  # 分档门槛（分）＝¥350，按每位好友累计付费计
    "wallet_invite_rebate_tier_percent": 1,    # 二档比例 %（门槛以上部分）
    "traffic_balance_price_per_100gb": 300,    # 余额购流量单价（分/100GB）＝¥3
    "wallet_refund_to_balance": True,          # 退款回钱包（批 2 落地）
    "wallet_auto_renew_enabled": False,        # 余额自动续订：不开
}

_BOOL_KEYS = {"wallet_enabled", "wallet_refund_to_balance",
              "wallet_auto_renew_enabled"}


class WalletError(ValueError):
    """钱包业务拒绝：``code`` 给前端分流，``str()`` 是人话文案。"""

    def __init__(self, message: str, code: str = "wallet_error"):
        super().__init__(message)
        self.code = code


class InsufficientWalletBalance(WalletError):
    """余额不足以完成扣减（条件 UPDATE 没扣动）。"""

    def __init__(self, message: str = "钱包余额不足"):
        super().__init__(message, "insufficient_balance")


# ==================== 配置读取 ====================

def _config_raw(db: Session, key: str) -> str:
    # ttl=0 直查 DB：钱相关配置不吃 60 秒缓存，管理端保存即生效，
    # 也避免测试间进程缓存串扰。读法仍走全站唯一带缓存的 store 通道。
    from backend.integrations import store

    default = WALLET_CONFIG_DEFAULTS[key]
    if isinstance(default, bool):
        default = "true" if default else "false"
    else:
        default = str(default)
    return store.get_value(db, key, default, ttl=0)


def wallet_config(db: Session) -> dict:
    """钱包 11 键的生效值（DB 行优先，缺行/坏值回代码默认）。"""
    result = {}
    for key, default in WALLET_CONFIG_DEFAULTS.items():
        raw = _config_raw(db, key)
        if key in _BOOL_KEYS:
            result[key] = str(raw).strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, str):
            # 字符串键（如返利模式）：原样取库值，空/缺行回默认
            result[key] = str(raw).strip() or default
        else:
            try:
                result[key] = int(str(raw).strip())
            except (TypeError, ValueError):
                result[key] = default
    return result


def wallet_enabled(db: Session) -> bool:
    return bool(wallet_config(db)["wallet_enabled"])


# ==================== 余额与流水查询 ====================

def get_balance(db: Session, user_id: int) -> Decimal:
    """当前余额；无账本行即 0（不自动建行）。"""
    row = db.query(models.UserWalletBalance.balance).filter(
        models.UserWalletBalance.user_id == user_id).first()
    return money(row[0]) if row else Decimal("0")


def _fresh_balance(db: Session, user_id: int) -> Decimal:
    row = (db.query(models.UserWalletBalance)
           .filter(models.UserWalletBalance.user_id == user_id)
           .populate_existing().first())
    return money(row.balance) if row else Decimal("0")


def ledger_query(db: Session, *, user_id: Optional[int] = None,
                 kind: Optional[str] = None, page: int = 1,
                 page_size: int = 20):
    """流水分页（按 id 倒序）；返回 (rows, total)。"""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    q = db.query(models.WalletLedger)
    if user_id is not None:
        q = q.filter(models.WalletLedger.user_id == user_id)
    if kind:
        q = q.filter(models.WalletLedger.kind == kind)
    total = q.count()
    rows = (q.order_by(models.WalletLedger.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return rows, total


def ledger_dict(row: models.WalletLedger) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "amount": f"{money(row.amount):.2f}",
        "balance_after": f"{money(row.balance_after):.2f}",
        "ref_id": row.ref_id,
        "note": row.note,
        "operator_id": row.operator_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ==================== 哈希链 ====================

def _record_hash(user_id: int, kind: str, amount: Decimal,
                 balance_after: Decimal, ref_id: Optional[str],
                 prev_hash: str) -> str:
    payload = "|".join([
        str(user_id), kind, f"{amount:.2f}", f"{balance_after:.2f}",
        ref_id or "", prev_hash or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_chain_hash(db: Session, user_id: int) -> str:
    row = (db.query(models.WalletLedger.record_hash)
           .filter(models.WalletLedger.user_id == user_id)
           .order_by(models.WalletLedger.id.desc()).first())
    return (row[0] or "") if row else ""


def verify_chain(db: Session, user_id: int) -> bool:
    """按 id 顺序重算该用户整条哈希链，任一环对不上即 False（审计/测试用）。"""
    rows = (db.query(models.WalletLedger)
            .filter(models.WalletLedger.user_id == user_id)
            .order_by(models.WalletLedger.id.asc()).all())
    prev = ""
    for row in rows:
        if (row.prev_hash or "") != prev:
            return False
        expect = _record_hash(row.user_id, row.kind, money(row.amount),
                              money(row.balance_after), row.ref_id, prev)
        if row.record_hash != expect:
            return False
        prev = row.record_hash
    return True


# ==================== 写账（入账 / 扣款原语 / 调账） ====================

def _normalize_key(idempotency_key: Optional[str]) -> Optional[str]:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    if len(key) > 64:
        raise WalletError("确认标识过长，请重新操作", "bad_idempotency_key")
    return key


def _replay(db: Session, key: Optional[str], user_id: int):
    """幂等回放前查：命中返回 (row, True)；键被他人占用直接拒绝。"""
    if key is None:
        return None
    existing = (db.query(models.WalletLedger)
                .filter(models.WalletLedger.idempotency_key == key).first())
    if existing is None:
        return None
    if existing.user_id != user_id:
        raise WalletError("确认标识已被占用，请重新操作",
                          "bad_idempotency_key")
    return existing, True


def _apply(db: Session, user_id: int, signed_amount: Decimal, *,
           kind: str, ref_id: Optional[str], note: Optional[str],
           operator_id: Optional[int], idempotency_key: Optional[str]):
    """账本变动的唯一写入口：锁用户行 → 条件 UPDATE → 重读余额 → 串链写流水。

    signed_amount 为带符号金额（正入账 / 负出账）。不提交。
    返回 (ledger_row, replayed)。
    """
    if kind not in LEDGER_KINDS:
        raise WalletError(f"未知流水类型：{kind}", "bad_kind")
    replay = _replay(db, idempotency_key, user_id)
    if replay is not None:
        return replay

    # 串行化同一用户的「读-判-写」：并发两笔都读到旧余额时，后一笔在
    # 用户行锁上等前者提交，链与余额都不会交错（原语见 economy.lock_user_row，
    # 与 codes 的懒导入复用方式一致，避顶层循环导入）。
    from backend.api.economy import lock_user_row

    lock_user_row(db, user_id)

    row = (db.query(models.UserWalletBalance)
           .filter(models.UserWalletBalance.user_id == user_id).first())
    if row is None:
        row = models.UserWalletBalance(user_id=user_id,
                                       balance=Decimal("0"))
        db.add(row)
        db.flush()

    if signed_amount > 0:
        updated = (
            db.query(models.UserWalletBalance)
            .filter(models.UserWalletBalance.user_id == user_id)
            .update({models.UserWalletBalance.balance:
                     models.UserWalletBalance.balance + signed_amount,
                     models.UserWalletBalance.updated_at: datetime.now()},
                    synchronize_session=False)
        )
    else:
        updated = (
            db.query(models.UserWalletBalance)
            .filter(models.UserWalletBalance.user_id == user_id,
                    models.UserWalletBalance.balance >= -signed_amount)
            .update({models.UserWalletBalance.balance:
                     models.UserWalletBalance.balance + signed_amount,
                     models.UserWalletBalance.updated_at: datetime.now()},
                    synchronize_session=False)
        )
    if not updated:
        raise InsufficientWalletBalance()
    balance_after = _fresh_balance(db, user_id)

    prev_hash = _last_chain_hash(db, user_id)
    entry = models.WalletLedger(
        user_id=user_id, kind=kind, amount=signed_amount,
        balance_after=balance_after, ref_id=ref_id,
        idempotency_key=idempotency_key, note=note,
        operator_id=operator_id, prev_hash=prev_hash,
        record_hash=_record_hash(user_id, kind, signed_amount,
                                 balance_after, ref_id, prev_hash),
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        # 幂等键唯一约束兜底（并发同键）：整事务回滚后按回放返回。
        db.rollback()
        replay = _replay(db, idempotency_key, user_id)
        if replay is not None:
            return replay
        raise
    return entry, False


def credit(db: Session, user_id: int, amount, *, kind: str,
           ref_id: Optional[str] = None, note: Optional[str] = None,
           operator_id: Optional[int] = None,
           idempotency_key: Optional[str] = None):
    """入账（金额为正数，元）。返回 (ledger_row, replayed)。不提交。"""
    amount = money(amount)
    if amount <= 0:
        raise WalletError("入账金额必须大于 0", "bad_amount")
    return _apply(db, user_id, amount, kind=kind, ref_id=ref_id,
                  note=note, operator_id=operator_id,
                  idempotency_key=_normalize_key(idempotency_key))


def debit(db: Session, user_id: int, amount, *, kind: str,
          ref_id: Optional[str] = None, note: Optional[str] = None,
          operator_id: Optional[int] = None,
          idempotency_key: Optional[str] = None):
    """扣款原语（金额为正数，元；落账为负数行）。

    条件 UPDATE 防扣负：余额不足抛 InsufficientWalletBalance，余额与
    流水都不动。批 1 不接任何扣款端点，供批 2 调用。不提交。
    """
    amount = money(amount)
    if amount <= 0:
        raise WalletError("扣款金额必须大于 0", "bad_amount")
    return _apply(db, user_id, -amount, kind=kind, ref_id=ref_id,
                  note=note, operator_id=operator_id,
                  idempotency_key=_normalize_key(idempotency_key))


def admin_adjust(db: Session, user_id: int, amount, *, note: str,
                 operator_id: int):
    """管理员手工调账：amount 带符号（正增 / 负减），原因必填、记操作人。

    扣减同样走条件 UPDATE，余额不足整笔拒绝。不提交。
    """
    amount = money(amount)
    if amount == 0:
        raise WalletError("调整金额不能为 0", "bad_amount")
    if not (note or "").strip():
        raise WalletError("调账必须填写原因", "bad_note")
    if not operator_id:
        raise WalletError("调账必须记录操作人", "bad_operator")
    return _apply(db, user_id, amount, kind="admin_adjust", ref_id=None,
                  note=note.strip(), operator_id=operator_id,
                  idempotency_key=None)
