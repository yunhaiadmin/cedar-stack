# -*- coding: utf-8 -*-
"""流量额度（消费方）：套餐月额度 + 当月已用 + 购流量·时间换流量。

形态照 UHDNow：订阅是「时间＋每月流量」双额度——套餐含每月流量
（如 500GB），按订阅周期重置；概览页展示剩余/已用/重置日；流量
不够用时可以「购流量」：花余额抵扣已用（钱包未建，本期不做）或
扣订阅天数换抵扣额度。购流量不扩套餐总额度、不延到期日（时间换
流量反而是**缩短**到期日换额度）。时间换流量的量按**比例**
（2026-11-13 23:26 定版，照 UHDNow 实测）：扣 15 天抵当月额度的
50%、扣 30 天抵 100%——成交时按该用户当月额度×比例现算，不是
固定字节数；留痕记额度基数与比例，事后可对账。

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
import json
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

#: 时间换流量选项（SystemConfig，JSON 数组 [{"days":15,
#: "quota_pct":50}, ...]，quota_pct = 抵扣当月额度的百分比）；
#: 未配/坏值回退内置默认。旧固定字节形态（{"bytes": ...}）在
#: 按比例口径下不算合法项——生产从未启用本功能、无存量配置，
#: 混入旧形态的配置整体回退默认，不许拿旧固定值继续成交。
CONFIG_EXCHANGE_OPTIONS = "traffic_time_exchange_options"

#: 内置默认选项（照 UHDNow 实测口径：−15 天抵当月额度 50%、
#: −30 天抵 100%）。
DEFAULT_EXCHANGE_OPTIONS = [
    {"days": 15, "quota_pct": 50},
    {"days": 30, "quota_pct": 100},
]

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


def exchange_options(db: Session) -> list[dict]:
    """时间换流量选项（配置优先，坏配置回退默认并打 warning）。

    每项 {"days": 扣减天数, "quota_pct": 抵扣当月额度的百分比}，
    按 days 升序。百分比须为 1–100 的整数；不合此形态的项（含旧
    固定字节 {"bytes": ...} 形态）整项作废，无合法项则回退默认。
    """
    try:
        raw = store.get_value(db, CONFIG_EXCHANGE_OPTIONS, "")
    except Exception:  # noqa: BLE001
        raw = ""
    if raw and str(raw).strip():
        try:
            data = json.loads(raw)
            out = []
            for item in data:
                days = int(item["days"])
                pct = int(item["quota_pct"])
                if 1 <= days <= 365 and 1 <= pct <= 100:
                    out.append({"days": days, "quota_pct": pct})
            if out:
                return sorted(out, key=lambda x: x["days"])
            logger.warning("traffic_time_exchange_options 无合法项，回退默认")
        except (TypeError, ValueError, KeyError, AttributeError):
            logger.warning("traffic_time_exchange_options 解析失败，回退默认",
                           exc_info=True)
    return [dict(o) for o in DEFAULT_EXCHANGE_OPTIONS]


def exchange_credit_bytes(quota: int, quota_pct: int) -> int:
    """按比例折算抵扣字节：月额度 × 百分比，整数乘除天然向下
    取整到整字节，不许四舍五入多给。"""
    return quota * quota_pct // 100


def exchange_options_view(db: Session, user_id: int) -> list[dict]:
    """给前端的选项视图：比例（配置口径）＋按该用户当月额度
    现折算的抵扣字节。未启用用户 quota 为 None、bytes 给 0——
    前端整段不渲染，成交端点也会以 quota_not_enabled 拒绝。"""
    quota = quota_bytes_for(db, user_id)
    return [{
        "days": o["days"],
        "quota_pct": o["quota_pct"],
        "bytes": (exchange_credit_bytes(quota, o["quota_pct"])
                  if quota else 0),
    } for o in exchange_options(db)]


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

def exchange_time_for_traffic(db: Session, user_id: int, days: int,
                              idempotency_key: str,
                              now: Optional[datetime] = None) -> dict:
    """扣订阅 ``days`` 天，换本周期按比例抵扣额度。全程留痕。

    抵扣量 = 成交时该用户月额度 × 选项比例（向下取整到整字节）；
    留痕同时记额度基数（quota_base_bytes）与比例（quota_pct），
    与实际抵扣字节三者可互相复算对账。

    - 幂等：同 idempotency_key 已成交 → 原样返回那笔（replayed
      =True），不重复扣天数；键被别人占用 → 拒绝。
    - 资格：用户须启用流量区（有额度）且有有效订阅；兑换后订阅
      必须仍然有效（end_date − days > now），不许把订阅扣穿。
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
    option = next((o for o in exchange_options(db)
                   if o["days"] == days), None)
    if option is None:
        raise TrafficError("没有这个兑换选项", "bad_option")
    credited = exchange_credit_bytes(quota, option["quota_pct"])
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
    after = before - timedelta(days=option["days"])
    if after <= now:
        raise TrafficError("订阅剩余天数不足，扣完就过期了，不能兑换",
                           "insufficient_days")
    start, end, _ = current_period(db, user_id, now)
    locked.end_date = after
    log = em.TrafficExchangeLog(
        user_id=user_id, kind="time_exchange",
        days_deducted=option["days"], bytes_credited=credited,
        quota_base_bytes=quota, quota_pct=option["quota_pct"],
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
