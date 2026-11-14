"""余额自动续费引擎（2026-11-14 订阅照友站重做）。

口径（友站语义：到期前自动从余额扣款续费，请保持余额充足）：
- 总闸：钱包配置 ``wallet_auto_renew_enabled``（默认开）且钱包开启；
- 用户侧开关：``UserSubscription.auto_renew``（默认关，用户在概览
  套餐区打开，随时可关）；
- 窗口：到期前 24 小时起跑；已过期 3 天内仍可补续，超窗不再扣；
- 续期规格：当前套餐 + 上次购买月数（取该订阅行名下最近一笔 paid
  订单的 days_granted 折算月数；查不到订单回退套餐基准月数）；
- 价格：按月口径标价（年付 11×月价），不叠加优惠券/抵扣；
- 余额不足：不扣款，落一笔 closed 订单（订阅记录可见）+ 站内信
  提醒充值，开关保持开着，用户充完值下一轮自动补续（窗口内）；
- 去重：订单号 ``AR{订阅 id}-{到期分钟}`` 唯一，引擎重复跑、
  多进程并发都不会重复扣款（钱包扣款另有同名幂等键兜底）；
- 每条订阅独立事务：一条失败只回滚自己，不拖垮整轮。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend import models
from backend import wallet as wallet_service

logger = logging.getLogger(__name__)

RENEW_WINDOW_BEFORE = timedelta(hours=24)
RENEW_GRACE_AFTER = timedelta(days=3)


def _last_purchase_months(db: Session, sub: models.UserSubscription,
                          plan: models.SubscriptionPlan) -> int:
    """上次购买月数：该订阅行名下最近一笔 paid 订单的 days_granted
    按 365 天=12 个月折算；没有订单回退套餐基准月数。"""
    from backend.api.economy import plan_base_months

    order = (
        db.query(models.SubscriptionOrder)
        .filter(
            models.SubscriptionOrder.subscription_id == sub.id,
            models.SubscriptionOrder.status == "paid",
        )
        .order_by(models.SubscriptionOrder.id.desc())
        .first()
    )
    if order is not None and order.days_granted:
        months = round(int(order.days_granted) * 12 / 365)
        return min(max(months, 1), 12)
    return plan_base_months(plan)


def _renew_one(db: Session, sub: models.UserSubscription,
               now: datetime) -> tuple[str, list]:
    """处理一条订阅的自动续费。返回 (结果, 待发通知)。

    结果：renewed / failed（余额不足）/ skipped（缺料/已处理）。
    本函数自己 commit/rollback，调用方不要再动事务。
    """
    from backend.api import economy as economy_api

    plan = db.query(models.SubscriptionPlan).filter(
        models.SubscriptionPlan.id == sub.plan_id).first()
    user = db.query(models.WebUser).filter(
        models.WebUser.id == sub.user_id).first()
    if plan is None or user is None or not user.is_active:
        return "skipped", []

    order_id = f"AR{sub.id}-{sub.end_date.strftime('%Y%m%d%H%M')}"
    existing = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == order_id).first()
    if existing is not None:
        return "skipped", []

    months = _last_purchase_months(db, sub, plan)
    price = economy_api.months_list_price(plan, months)
    days = economy_api.months_grant_days(months)
    balance = wallet_service.get_balance(db, user.id)

    if price > 0 and balance < price:
        # 余额不足：落 closed 单留痕（订阅记录页的「最近动态」能看到），
        # 发站内信提醒；不关用户的自动续费开关。
        order = models.SubscriptionOrder(
            order_id=order_id, user_id=user.id, plan_id=plan.id,
            item_name=plan.name, amount=price, list_price=price,
            discount_amount=0, days_granted=days,
            payment_method="auto_renew", status="closed")
        db.add(order)
        db.commit()
        return "failed", [dict(
            event_type="economy.subscription_auto_renew_failed",
            user_id=user.id,
            title="⚠️ 自动续费失败：余额不足",
            content=(f"「{plan.name}」自动续费需要 ¥{price:.2f}，"
                     f"当前余额 ¥{balance:.2f}，请充值后等待下一次自动续费"
                     "或手动续费。"),
            related_id=sub.id,
        )]

    if price > 0:
        wallet_service.debit(
            db, user.id, price, kind="subscription_purchase",
            ref_id=order_id,
            note=f"自动续费「{plan.name}」{months} 个月",
            idempotency_key=f"auto-renew-{order_id}")
    order = models.SubscriptionOrder(
        order_id=order_id, user_id=user.id, plan_id=plan.id,
        item_name=plan.name, amount=price, list_price=price,
        discount_amount=0, days_granted=days,
        payment_method="auto_renew", status="pending")
    db.add(order)
    db.flush()
    pending = economy_api._fulfill_order(db, subscription_order=order)
    # 履约可能另起新行（旧行已过期时）：自动续费开关跟到新行上，
    # 否则续完这一期下一期就再也不续了。
    if order.subscription_id and order.subscription_id != sub.id:
        new_sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.id == order.subscription_id).first()
        if new_sub is not None:
            new_sub.auto_renew = True
    db.commit()
    return "renewed", pending


def process_auto_renewals(db: Session, now: datetime | None = None) -> dict:
    """跑一轮自动续费。返回统计 + 待发通知（调用方在事务外发送）。"""
    result = {"renewed": 0, "failed": 0, "skipped": 0,
              "pending_notifications": []}
    cfg = wallet_service.wallet_config(db)
    if not cfg.get("wallet_enabled") or not cfg.get("wallet_auto_renew_enabled"):
        return result
    now = now or datetime.now()
    candidates = (
        db.query(models.UserSubscription)
        .filter(
            models.UserSubscription.auto_renew == True,  # noqa: E712
            models.UserSubscription.status == "active",
            models.UserSubscription.end_date <= now + RENEW_WINDOW_BEFORE,
            models.UserSubscription.end_date >= now - RENEW_GRACE_AFTER,
        )
        .order_by(models.UserSubscription.end_date.asc())
        .all()
    )
    for sub in candidates:
        try:
            outcome, pending = _renew_one(db, sub, now)
        except Exception:  # noqa: BLE001 — 单条失败不拖垮整轮
            logger.exception("自动续费单条失败: sub=%s", sub.id)
            db.rollback()
            result["skipped"] += 1
            continue
        result[outcome] = result.get(outcome, 0) + 1
        result["pending_notifications"].extend(pending)
    return result


async def run_auto_renewals_async(db: Session) -> dict:
    """调度入口：跑一轮并在事务提交后统一发站内信。"""
    from backend.api.economy import send_fulfill_notifications

    result = process_auto_renewals(db)
    await send_fulfill_notifications(result["pending_notifications"])
    if result["renewed"] or result["failed"]:
        logger.info("自动续费周期完成: 成功 %s / 余额不足 %s / 跳过 %s",
                    result["renewed"], result["failed"], result["skipped"])
    return result


__all__ = ["process_auto_renewals", "run_auto_renewals_async"]
