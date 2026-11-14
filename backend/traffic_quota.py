# -*- coding: utf-8 -*-
"""流量额度（消费方）：套餐月额度 + 当月已用 + 购流量·时间换流量。

形态照 UHDNow：订阅是「时间＋每月流量」双额度——套餐含每月流量
（如 500GB），按订阅周期重置；概览页展示剩余/已用/重置日；流量
不够用时可以「购流量」：花余额抵扣已用或扣订阅时长换抵扣额度。
购流量不扩套餐总额度、不延到期日（时间换流量反而是**缩短**到期
日换额度）。时间换流量按 GB 计（2026-11-14 用户亲令，照 UHDNow
实测口径，旧「15 天抵 50%/30 天抵 100%」比例口径作废）：每
10 GB 流量需 7 天套餐剩余时长；兑换后总流量（套餐月额度＋本
周期已兑换抵扣）上限 5 TB，超出上限的部分按超出前比例折算应
扣时长（只扣与实际到账字节成比例的那部分时长）。

数据分工（不许另起一套）：
- 额度声明：套餐权益键 ``monthly_traffic_bytes``（backend/
  entitlements.py 单口读取）；套餐没声明时回退 SystemConfig
  ``traffic_default_monthly_bytes``；两者都没有 = 该用户不启用
  流量区（概览整段不展示，绝不拿 0 额度吓人）；
- 已用计量：emby_server/traffic_meter 落的日账
  ``user_traffic_usage_daily``（中继/本机文件两腿真实出字节；
  302 直链只有次数没有字节，见该模块 docstring 的覆盖口径）；
- 抵扣额度：``user_traffic_exchange_logs`` 按周期求和，不另立
  余额表。

周期口径：锚点日＝当前有效订阅的 start_date 的「日」——周期是
[本周期锚点日, 下周期锚点日)，锚点日钳到当月最后一天（如 1 月
31 日锚点在 2 月落 28 日）。续订顺延只动 end_date、不动
start_date，所以锚点跨续订稳定。无有效订阅时回退自然月（仅供
计量归账展示，额度仍按无声明处理）。

本期边界（用户 2026-11-13 定）：额度用尽**只展示与提醒，不拦
播放**——本模块没有任何判定往播放链路里接，can_play 等门禁函数
不许来读这里。
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import entitlements, models, subscriptions
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

#: 套餐未声明月额度时的全局回退（SystemConfig，字节数；
#: 空/0/非法 = 不启用流量区）。
CONFIG_DEFAULT_QUOTA = "traffic_default_monthly_bytes"

#: 时间换流量规则（2026-11-14 照友站实测定版）：每 10 GB 需
#: 7 天订阅剩余时长；兑换后总流量上限 5 TB。
EXCHANGE_GB_PER_UNIT = 10
EXCHANGE_DAYS_PER_UNIT = 7
#: 弹窗快捷档位（GB）；任意 10 的整倍数 GB 均可兑换，快捷档只是
#: 前端预置按钮，与余额购流量共用同一组档位。
EXCHANGE_QUICK_GB = (100, 200, 500, 1000)
#: 兑换后总流量上限（套餐月额度 + 本周期全部抵扣，含余额购买）。
EXCHANGE_TOTAL_CAP_BYTES = 5 * 1024 ** 4

ENTITLEMENT_KEY = "monthly_traffic_bytes"


class TrafficError(ValueError):
    """购流量业务拒绝：``code`` 给前端分流，``str()`` 是人话文案。"""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def _config_int(db: Session, key: str) -> Optional[int]:
    """SystemConfig 整数热读：无行/非法/异常一律 None。"""
    try:
        raw = store.get_value(db, key, "")
    except Exception:  # noqa: BLE001
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def exchange_days_for_gb(gb: int) -> int:
    """兑换 gb 流量名义应扣的订阅天数：每 10 GB 需 7 天。"""
    return gb * EXCHANGE_DAYS_PER_UNIT // EXCHANGE_GB_PER_UNIT


def period_credit_bytes(db: Session, user_id: int, period_start) -> int:
    """本周期已到账的抵扣字节总和（时间兑换＋余额购买同一口径）。"""
    rows = (db.query(em.TrafficExchangeLog)
            .filter(em.TrafficExchangeLog.user_id == user_id,
                    em.TrafficExchangeLog.period_start == period_start)
            .all())
    return sum(int(r.bytes_credited or 0) for r in rows)


def exchange_rule_view(db: Session, user_id: int) -> dict:
    """给前端的时间换流量规则视图：单位口径（10GB/7 天）、快捷
    档位、5 TB 总上限与该用户当前还可兑换的字节余量。未启用
    用户 quota 为 None——前端整段不渲染，成交端点也会以
    quota_not_enabled 拒绝。"""
    quota = quota_bytes_for(db, user_id)
    start, _end, _sub = current_period(db, user_id)
    credited = period_credit_bytes(db, user_id, start) if quota else 0
    room = (max(0, EXCHANGE_TOTAL_CAP_BYTES - quota - credited)
            if quota else 0)
    return {
        "unit_gb": EXCHANGE_GB_PER_UNIT,
        "days_per_unit": EXCHANGE_DAYS_PER_UNIT,
        "quick_gb": list(EXCHANGE_QUICK_GB),
        "cap_bytes": EXCHANGE_TOTAL_CAP_BYTES,
        "quota_bytes": quota,
        "credited_bytes": credited,
        "room_bytes": room,
    }


def quota_bytes_for(db: Session, user_id: int) -> Optional[int]:
    """该用户的月流量额度（字节）；None = 不启用流量区。

    顺序：套餐权益声明 → SystemConfig 全局默认 → None。
    """
    declared = entitlements.get_user_entitlement(
        db, user_id, ENTITLEMENT_KEY)
    if declared is not None:
        return declared
    return _config_int(db, CONFIG_DEFAULT_QUOTA)


# ---------------------------------------------------------------------------
# 周期
# ---------------------------------------------------------------------------

def _anchor_in_month(anchor_day: int, year: int, month: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor_day, last))


def period_bounds(anchor_day: int, ref: date) -> tuple[date, date]:
    """包含 ref 的周期 [start, end)：锚点日逐月滚动、钳月末。"""
    cur = _anchor_in_month(anchor_day, ref.year, ref.month)
    if cur > ref:
        year, month = ((ref.year - 1, 12) if ref.month == 1
                       else (ref.year, ref.month - 1))
        cur = _anchor_in_month(anchor_day, year, month)
    year, month = ((cur.year + 1, 1) if cur.month == 12
                   else (cur.year, cur.month + 1))
    return cur, _anchor_in_month(anchor_day, year, month)


def current_period(db: Session, user_id: int,
                   now: Optional[datetime] = None):
    """当前流量周期 (start, end, active_subscription|None)。

    锚点取当前有效订阅的 start_date 之日；无有效订阅回退自然月
    （锚点日 = 1），sub 返回 None。
    """
    now = now or datetime.now()
    sub = subscriptions.active_subscription(db, user_id)
    anchor_day = sub.start_date.day if sub is not None else 1
    start, end = period_bounds(anchor_day, now.date())
    return start, end, sub


# ---------------------------------------------------------------------------
# 汇总（概览流量区）
# ---------------------------------------------------------------------------

def usage_summary(db: Session, user_id: int,
                  now: Optional[datetime] = None) -> dict:
    """概览流量区数据（纯读，不写库）。

    ``enabled=False`` 时 quota/remaining 为 None，前端整段不展示；
    计量数字照常给出（已用是事实，不因没额度就藏）。
    抵扣口径：有效已用 = max(0, 毛已用 − 本周期抵扣)，多出的
    抵扣自动留给本周期后续用量（读时现算，不回写）。
    """
    now = now or datetime.now()
    quota = quota_bytes_for(db, user_id)
    start, end, _sub = current_period(db, user_id, now)
    rows = (db.query(em.TrafficUsageDaily)
            .filter(em.TrafficUsageDaily.user_id == user_id,
                    em.TrafficUsageDaily.day >= start,
                    em.TrafficUsageDaily.day < end)
            .all())
    relay = sum(int(r.relay_bytes or 0) for r in rows)
    local = sum(int(r.local_bytes or 0) for r in rows)
    plays = sum(int(r.direct_plays or 0) for r in rows)
    credit = (db.query(em.TrafficExchangeLog)
              .filter(em.TrafficExchangeLog.user_id == user_id,
                      em.TrafficExchangeLog.period_start == start)
              .all())
    credit_bytes = sum(int(r.bytes_credited or 0) for r in credit)
    gross_used = relay + local
    credit_applied = min(credit_bytes, gross_used)
    used = gross_used - credit_applied
    enabled = quota is not None
    remaining = max(0, quota - used) if enabled else None
    return {
        "enabled": enabled,
        "quota_bytes": quota,
        "used_bytes": used,
        "gross_used_bytes": gross_used,
        "relay_bytes": relay,
        "local_bytes": local,
        "direct_plays": plays,
        "credit_bytes": credit_bytes,
        "credit_applied_bytes": credit_applied,
        "remaining_bytes": remaining,
        "used_pct": (round(used * 100.0 / quota, 1)
                     if enabled and quota else 0.0),
        "exhausted": bool(enabled and used >= quota),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "reset_date": end.isoformat(),
    }


# ---------------------------------------------------------------------------
# 购流量 · 时间换流量（成交走同步事务，不经计量模块）
# ---------------------------------------------------------------------------

def exchange_time_for_traffic(db: Session, user_id: int, gb: int,
                              idempotency_key: str,
                              now: Optional[datetime] = None) -> dict:
    """扣订阅时长换本周期抵扣流量（GB 口径，2026-11-14 定版）。

    规则：每 10 GB 需 7 天；兑换后总流量（套餐月额度＋本周期已
    抵扣，含余额购买部分）不得超过 5 TB——请求量超出剩余空间时
    只按空间到账，扣减时长按「实际到账字节 ÷ 请求字节」的超出前
    比例折算（整数秒向下取整，不多扣）。留痕记实际到账字节与
    实际扣减秒数（sub_end_before/after 精确对账；days_deducted
    存折算整天数供展示）。

    - 幂等：同 idempotency_key 已成交 → 原样返回那笔（replayed
      =True），不重复扣时长；键被别人占用 → 拒绝。
    - 资格：用户须启用流量区（有额度）且有有效订阅；兑换后订阅
      必须仍然有效（end_date − 扣减 > now），不许把订阅扣穿。
    - 扣减对象：当前有效订阅行（行锁 with_for_update 后复核），
      只动 end_date——锚点（start_date）不动，周期不漂移。
    返回 {"log": {...}, "replayed": bool, "summary": usage_summary}。
    """
    now = now or datetime.now()
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 64:
        raise TrafficError("缺少有效的确认标识，请重新打开弹窗再试",
                           "bad_idempotency_key")
    existing = (db.query(em.TrafficExchangeLog)
                .filter(em.TrafficExchangeLog.idempotency_key == key)
                .first())
    if existing is not None:
        if existing.user_id != user_id:
            raise TrafficError("确认标识已被占用，请重新打开弹窗再试",
                               "bad_idempotency_key")
        return {"log": _log_dict(existing), "replayed": True,
                "summary": usage_summary(db, user_id, now)}
    quota = quota_bytes_for(db, user_id)
    if quota is None:
        raise TrafficError("当前套餐没有流量额度，暂不能购买流量",
                           "quota_not_enabled")
    try:
        gb = int(gb)
    except (TypeError, ValueError):
        raise TrafficError("兑换流量必须是 10GB 的整倍数", "bad_option")
    if gb < EXCHANGE_GB_PER_UNIT or gb % EXCHANGE_GB_PER_UNIT != 0:
        raise TrafficError("兑换流量必须是 10GB 的整倍数", "bad_option")
    start, end, _ = current_period(db, user_id, now)
    room = (EXCHANGE_TOTAL_CAP_BYTES - quota
            - period_credit_bytes(db, user_id, start))
    if room <= 0:
        raise TrafficError("总流量已达 5TB 上限，不能再兑换了",
                           "exchange_cap_reached")
    requested_bytes = gb * (1024 ** 3)
    credited = min(requested_bytes, room)
    nominal_seconds = exchange_days_for_gb(gb) * 86400
    deduct_seconds = nominal_seconds * credited // requested_bytes
    sub = subscriptions.active_subscription(db, user_id)
    if sub is None:
        raise TrafficError("没有生效中的订阅，无法用时长兑换",
                           "no_active_subscription")
    locked = (db.query(models.UserSubscription)
              .filter(models.UserSubscription.id == sub.id)
              .with_for_update()
              .first())
    if locked is None or locked.status != "active":
        raise TrafficError("订阅状态已变化，请刷新后再试",
                           "no_active_subscription")
    before = locked.end_date
    after = before - timedelta(seconds=deduct_seconds)
    if after <= now:
        raise TrafficError("订阅剩余时长不足，扣完就过期了，不能兑换",
                           "insufficient_days")
    locked.end_date = after
    log = em.TrafficExchangeLog(
        user_id=user_id, kind="time_exchange",
        days_deducted=int(round(deduct_seconds / 86400)),
        bytes_credited=credited,
        quota_base_bytes=quota, quota_pct=None,
        subscription_id=locked.id, sub_end_before=before,
        sub_end_after=after, period_start=start, period_end=end,
        idempotency_key=key)
    db.add(log)
    try:
        db.commit()
    except IntegrityError:
        # 并发同键双击：唯一约束兜底——回滚后按已成交回放。
        db.rollback()
        existing = (db.query(em.TrafficExchangeLog)
                    .filter(em.TrafficExchangeLog.idempotency_key == key)
                    .first())
        if existing is not None and existing.user_id == user_id:
            return {"log": _log_dict(existing), "replayed": True,
                    "summary": usage_summary(db, user_id, now)}
        raise
    entitlements.invalidate(user_id)
    return {"log": _log_dict(log), "replayed": False,
            "summary": usage_summary(db, user_id, now)}


def _log_dict(log: em.TrafficExchangeLog) -> dict:
    return {
        "id": log.id,
        "kind": log.kind,
        "days_deducted": log.days_deducted,
        "bytes_credited": log.bytes_credited,
        "quota_base_bytes": log.quota_base_bytes,
        "quota_pct": log.quota_pct,
        "subscription_id": log.subscription_id,
        "sub_end_before": (log.sub_end_before.isoformat()
                           if log.sub_end_before else None),
        "sub_end_after": (log.sub_end_after.isoformat()
                          if log.sub_end_after else None),
        "period_start": (log.period_start.isoformat()
                         if log.period_start else None),
        "period_end": (log.period_end.isoformat()
                       if log.period_end else None),
        "created_at": (log.created_at.isoformat()
                       if log.created_at else None),
    }


#: 余额购流量档位（GB）：与前端弹窗四档一致；只许 100 的整倍数，
#: 单次上限 1000GB（防手误多买；改价走配置键，不改档）。
BALANCE_PURCHASE_GB_OPTIONS = (100, 200, 500, 1000)


def balance_price_yuan(db: Session, gb: int):
    """余额购流量价（元）：单价键 ``traffic_balance_price_per_100gb``
    （分/100GB，默认 300＝¥3，钱包方案 §10 定版），按 100GB 为单位计价。

    单价未配/≤0 时拒绝成交（不许按 0 元白送）。
    """
    from decimal import Decimal

    from backend import wallet as wallet_service
    from backend.agents import money

    per_100gb_fen = int(
        wallet_service.wallet_config(db).get("traffic_balance_price_per_100gb") or 0)
    if per_100gb_fen <= 0:
        raise TrafficError("余额购流量暂未定价，请联系管理员",
                           "balance_price_unset")
    return money(Decimal(per_100gb_fen) * Decimal(gb // 100) / Decimal(100))


def buy_traffic_with_balance(db: Session, user_id: int, gb: int,
                             idempotency_key: str,
                             now: Optional[datetime] = None) -> dict:
    """余额购流量：按配置单价扣钱包余额，换本周期抵扣额度。

    抵扣量 = 档位 GB（1024 进制字节）；购流量不扩套餐总额度、不延
    到期日——抵扣只冲本周期已用（与时间换流量同一消费口径，
    usage_summary 按周期求和现算）。

    - 幂等：同 idempotency_key 已成交 → 原样返回那笔（replayed
      =True），不重复扣款；键被别人占用 → 拒绝。
    - 资格：用户须启用流量区（有额度）；余额不足时钱包条件 UPDATE
      拒绝，整笔回滚（订单/留痕都不落）。
    - 顺序：先查留痕（重放）→ 扣款 → 写留痕 → 提交；扣款在留痕前，
      并发同键撞钱包幂等唯一约束时 _apply 内部回滚只冲掉本次事务
      已有的写（此时还没写留痕），回放到的已扣行继续走留痕补齐。
    返回 {"log", "replayed", "summary", "paid_amount",
    "balance_after", "gb"}。
    """
    from backend import wallet as wallet_service
    from backend.agents import money

    now = now or datetime.now()
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 64:
        raise TrafficError("缺少有效的确认标识，请重新打开弹窗再试",
                           "bad_idempotency_key")
    try:
        gb = int(gb)
    except (TypeError, ValueError):
        raise TrafficError("没有这个购买档位", "bad_option")
    if gb not in BALANCE_PURCHASE_GB_OPTIONS:
        raise TrafficError("没有这个购买档位", "bad_option")

    existing = (db.query(em.TrafficExchangeLog)
                .filter(em.TrafficExchangeLog.idempotency_key == key)
                .first())
    if existing is not None:
        if existing.user_id != user_id:
            raise TrafficError("确认标识已被占用，请重新打开弹窗再试",
                               "bad_idempotency_key")
        paid_row = (db.query(models.WalletLedger)
                    .filter(models.WalletLedger.idempotency_key == key)
                    .first())
        return {"log": _log_dict(existing), "replayed": True,
                "summary": usage_summary(db, user_id, now),
                "paid_amount": (f"{money(-paid_row.amount):.2f}"
                                if paid_row is not None else "0.00"),
                "balance_after": (f"{money(paid_row.balance_after):.2f}"
                                  if paid_row is not None else
                                  f"{wallet_service.get_balance(db, user_id):.2f}"),
                "gb": int(existing.bytes_credited or 0) // (1024 ** 3)}
    quota = quota_bytes_for(db, user_id)
    if quota is None:
        raise TrafficError("当前套餐没有流量额度，暂不能购买流量",
                           "quota_not_enabled")
    price = balance_price_yuan(db, gb)
    entry, _replayed = wallet_service.debit(
        db, user_id, price, kind="traffic_purchase",
        ref_id=f"traffic:{key}", note=f"余额购买流量 {gb}GB",
        idempotency_key=key)
    # 回放到的已扣行：留痕可能已落（上一笔在扣款后、留痕前崩了才没有），
    # 查一次再决定补写还是直接返回。
    existing = (db.query(em.TrafficExchangeLog)
                .filter(em.TrafficExchangeLog.idempotency_key == key)
                .first())
    if existing is not None:
        if existing.user_id != user_id:
            raise TrafficError("确认标识已被占用，请重新打开弹窗再试",
                               "bad_idempotency_key")
        return {"log": _log_dict(existing), "replayed": True,
                "summary": usage_summary(db, user_id, now),
                "paid_amount": f"{money(price):.2f}",
                "balance_after": f"{money(entry.balance_after):.2f}",
                "gb": int(existing.bytes_credited or 0) // (1024 ** 3)}
    start, end, _ = current_period(db, user_id, now)
    log = em.TrafficExchangeLog(
        user_id=user_id, kind="balance_purchase",
        days_deducted=0, bytes_credited=gb * (1024 ** 3),
        quota_base_bytes=None, quota_pct=None,
        subscription_id=None, sub_end_before=None, sub_end_after=None,
        period_start=start, period_end=end, idempotency_key=key)
    db.add(log)
    try:
        db.commit()
    except IntegrityError:
        # 并发同键双击：唯一约束兜底——回滚后按已成交回放。
        db.rollback()
        existing = (db.query(em.TrafficExchangeLog)
                    .filter(em.TrafficExchangeLog.idempotency_key == key)
                    .first())
        if existing is not None and existing.user_id == user_id:
            return {"log": _log_dict(existing), "replayed": True,
                    "summary": usage_summary(db, user_id, now),
                    "paid_amount": f"{money(price):.2f}",
                    "balance_after": f"{money(entry.balance_after):.2f}",
                    "gb": int(existing.bytes_credited or 0) // (1024 ** 3)}
        raise
    return {"log": _log_dict(log), "replayed": False,
            "summary": usage_summary(db, user_id, now),
            "paid_amount": f"{money(price):.2f}",
            "balance_after": f"{money(entry.balance_after):.2f}",
            "gb": gb}
