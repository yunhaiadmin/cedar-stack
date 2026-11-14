"""
管理后台 · 订单关单与退款

补上一个此前完全不存在的运营口子：订单只能「标记已支付」，**不能关、也不能退**。
线下多转了一笔、用户点错套餐、支付回调把用户搞重复了——后台当时一点办法都没有，
只能去改数据库。这里把两件事做成一等公民：

- **关单**：把未支付的订单作废（`pending` → `closed`）。不碰任何权益，用户也没付钱，
  所以不打扰用户（只留审计）。
- **退款**：把已支付的订单冲正（`paid` → `refunded`），并且**按账本精确回滚**：

  订阅订单：按履约时记下的 `subscription_id` / `days_granted` **精确回滚天数**
  （老订单没有这两个字段时，才退回“该用户在该服的生效订阅”这一近似），
  回滚后已过期则把订阅置为 `cancelled`——这是真实撤销，与自然到期不是一回事。
  同时撤回邀请人拿到的那笔返利——返利也是这笔订单产生的，
  只退买家不退返利，就等于让站点为一次退款付两遍钱。

钱包扣回的通用原则：**不悄悄把余额扣成负数**。邀请人余额不够时返利先欠着
（计提行置 owed），由管理员显式选择 `allow_negative=true` 才许扣负。

老库自动补列（`refunded_at` / `refund_reason` / `closed_at` / `subscription_id` / `days_granted`），
没有这些列的库不会因为升级而报错。

v2.10.0：关单 / 退款同时**释放优惠券预订的额度**（`coupons.release`）。
券的额度是预订制的（下单就占），如果订单作废/退了而不还额度，码会被白白烧掉。

单独成文件：`backend/api/admin.py` 已经很大，订单相关的新端点集中在这里维护。
鉴权口径完全一致（复用 `get_current_admin` + JWT + `_audit`）。
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import coupons, models
from backend.api.admin_core import _audit, get_current_admin
from backend.database import get_db
from backend.db_retry import commit_with_retry

logger = logging.getLogger(__name__)

admin_orders_router = APIRouter(prefix="/api/admin", tags=["管理后台·订单"])

# 订单状态：pending 待支付 / paid 已支付 / refunded 已退款 / closed 已关闭
CLOSEABLE_STATUSES = ("pending",)
REFUNDABLE_STATUSES = ("paid",)


class RefundRequest(BaseModel):
    reason: str = ""
    # 是否回滚权益（会员天数）。缺省回滚：退款不退权益 = 白送。
    revoke_entitlement: bool = True
    # 余额不够时是否允许扣成负数（客服先退钱后追账的场景）
    allow_negative: bool = False
    # 是否同时撤回邀请人的返利
    reverse_rebate: bool = True


def _find_order(db: Session, order_id: str):
    """按订单号找订单（充值订单已随积分货币下线全清，现只有订阅订单）"""
    subscription = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == order_id).first()
    if subscription:
        return subscription, "subscription"
    return None, None


def _rollback_subscription(db: Session, order: models.SubscriptionOrder,
                           plan: Optional[models.SubscriptionPlan], now: datetime):
    """回滚这笔订单开出的会员天数，返回 (订阅, 回滚天数, 是否已撤销)"""
    days = int(order.days_granted or 0)
    # 履约时没记天数（老订单）：退回套餐当前时长，这是唯一可用的近似
    if days <= 0 and plan is not None:
        days = int(plan.duration_days or 0)

    subscription = None
    if order.subscription_id:
        subscription = db.query(models.UserSubscription).filter(
            models.UserSubscription.id == order.subscription_id).first()
    if subscription is None:
        # 老订单没有 subscription_id：退回到「该用户在该服生效中的订阅」
        target_realm = plan.realm_id if plan is not None else None
        q = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == order.user_id,
            models.UserSubscription.end_date > now,
        )
        if target_realm is not None:
            q = q.filter(models.UserSubscription.realm_id == target_realm)
        subscription = q.order_by(models.UserSubscription.end_date.desc()).first()
    if subscription is None or days <= 0:
        return subscription, 0, False

    subscription.end_date = subscription.end_date - timedelta(days=days)
    cancelled = False
    if subscription.end_date <= now:
        # 真实撤销（不是自然到期）：这里应当翻状态，避免"退了款还显示生效中"
        subscription.status = "cancelled"
        cancelled = True
    subscription.updated_at = now
    # 订阅分档权益（PR-1）：到期时间/状态变了，清该用户的权益解析缓存
    #（见 backend/entitlements.py；跨进程靠 60 秒 TTL 兜底）
    from backend import entitlements as _entitlements

    _entitlements.invalidate(user_id=subscription.user_id)
    return subscription, days, cancelled


@admin_orders_router.post("/economy/orders/{order_id}/close")
def close_order(
    order_id: str,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """关闭未支付订单（作废）：不碰权益，也不打扰用户（他并没有付钱）"""
    order, kind = _find_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in CLOSEABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"只有待支付订单可以关闭（当前状态：{order.status}）；已支付订单请用退款。",
        )

    # 原子认领：读-查-写之间支付回调（economy._claim_order）可能把 pending
    # 原子推进到 paid 并履约；无条件 UPDATE 会把已支付订单覆盖成 closed，
    # 钱已付、权益已发，订单却显示作废——与 refund 的条件 UPDATE 同一写法，
    # 抢不到的行说明状态已变，报 409 让管理员刷新。
    model = models.SubscriptionOrder
    claimed = (
        db.query(model)
        .filter(model.id == order.id, model.status.in_(tuple(CLOSEABLE_STATUSES)))
        .update({"status": "closed", "closed_at": datetime.now()},
                synchronize_session=False)
    )
    if not claimed:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="订单状态已变化（可能已被支付或处理），请刷新后重试",
        )

    # 优惠券用的是预订制额度：订单作废了，把占用的次数还回去（不还就等于把码烧了）
    coupons.release(db, order.coupon_usage_id)
    # 兑换码折扣权益同样是「下单即占」：订单作废后还回未用（与上面优惠券同一道理，
    # 否则用户一次没付成的下单会永久烧掉这张权益）
    _restore_discount_credit(db, order_id)
    db.commit()

    _audit(db, current_admin, "economy_close_order", "order", None,
           {"order_id": order_id, "kind": kind})
    db.commit()
    return {"success": True, "order_id": order_id, "kind": kind, "status": "closed"}


@admin_orders_router.post("/economy/orders/{order_id}/refund")
async def refund_order(
    order_id: str,
    request: RefundRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """退款：冲正订单并按账本回滚权益（订阅回滚天数）

    查单 / 原子占位 / 冲正 / 审计整段是同步 SQLAlchemy，下放线程池执行；
    通知要走 WebSocket，必须留在事件循环上 await。
    """
    from backend.notifications import AdminEvent, notify_admin_event_quiet

    admin_id = current_admin.id

    def _refund() -> dict:
        order, kind = _find_order(db, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        if order.status == "refunded":
            raise HTTPException(status_code=400, detail="该订单已经退款，不能重复退款")
        if order.status not in REFUNDABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"只有已支付订单可以退款（当前状态：{order.status}）；未支付订单请用关闭。",
            )

        now = datetime.now()
        user = db.query(models.WebUser).filter(models.WebUser.id == order.user_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail="订单用户不存在")

        result = {
            "success": True, "order_id": order_id, "kind": kind,
            "status": "refunded", "revoked_points": 0, "revoked_days": 0,
            "revoked_xp": 0,
            "rebate_reversed": 0, "rebate_reversal_failed": False,
            "cancelled": False, "coupon_released": False,
        }

        # 原子占位：并发点两次「退款」时只有一个请求能拿到这一单。
        # 上面两句状态检查与这里之间隔着整段冲正（回滚会员天数，不是幂等的）：
        # 读-改-写会让两个请求都读到 paid，于是各扣一次（双重回滚）。
        # 条件 UPDATE 的 rowcount 就是判据（与 economy._claim_order 同一套写法）——
        # 抢不到的那一边什么都不做，直接告诉前端「已处理」。
        model = models.SubscriptionOrder
        claimed = (
            db.query(model)
            .filter(model.id == order.id, model.status.in_(tuple(REFUNDABLE_STATUSES)))
            .update({"status": "refunded", "refunded_at": now}, synchronize_session=False)
        )
        if not claimed:
            db.rollback()
            logger.info("退款请求被并发的那一次抢先（订单 %s，%s）", order_id, kind)
            return {**result, "already_processed": True,
                    "message": "该订单已处理（并发退款只有一次生效）"}

        if request.revoke_entitlement:
            plan = db.query(models.SubscriptionPlan).filter(
                models.SubscriptionPlan.id == order.plan_id).first()
            subscription, days, cancelled = _rollback_subscription(db, order, plan, now)
            result["revoked_days"] = days
            result["cancelled"] = cancelled
            if subscription is not None and days > 0:
                result["subscription_end"] = (
                    subscription.end_date.isoformat() if subscription.end_date else None
                )
            # 积分购订阅的历史订单：积分已随货币下线（2026-11-13），
            # 退款只回滚天数、不再退回积分（原查台账退积分腿已随积分台账全清）。
            # 钱包批 2：订阅单同样产生邀请返利（_fulfill_order 的计提
            # 钩子，source=subscription_order）——退款必须撤回，
            # 否则「只退买家不退返利，等于让站点为一次退款付两遍钱」。
            if request.reverse_rebate:
                reversed_amount, rebate_owed = _reverse_rebate(
                    db, order_id, request.allow_negative)
                result["rebate_reversed"] = reversed_amount
                # 欠账必须如实告诉前端：rebate_reversed=0 可能是"本来就没返利"，
                # 也可能是"邀请人余额不足、返利没撤回来"，两种情况界面不能长一个样
                result["rebate_reversal_failed"] = rebate_owed

            # 会员经验已随等级体系于 2026-11-14 删除：履约不再发经验，
            # 退款也无经验可撤。revoked_xp 字段保留出参兼容、恒为 0。
            result["revoked_xp"] = 0

        # 钱包批 2：退款回余额（与是否撤权益无关——revoke_entitlement
        # =False 是「只退钱不撤权益」，钱照样回钱包）。
        # 金额按订单实付（订阅 order.amount），
        # 与履约时的返利/经验口径一致；幂等键 refund:<订单号>，重复
        # 退款尝试不重复入账（refund_order 本身也 400 拒重复退款，
        # 这里是第二道门）。配置 wallet_refund_to_balance 关了则
        # 不入账（走人工/原路，另一单）；入账失败整单回滚——绝不
        # 出现「订单已标 refunded、钱没回来」的半笔账。
        result["refund_to_wallet"] = False
        result["refund_amount"] = "0.00"
        from backend import wallet as _wallet

        if _wallet.wallet_config(db).get("wallet_refund_to_balance", True):
            paid_amount = order.amount
            from backend.agents import money as _money

            refund_money = _money(paid_amount or 0)
            if refund_money > 0:
                try:
                    entry, _replayed = _wallet.credit(
                        db, user.id, refund_money, kind="refund",
                        ref_id=f"refund:{order_id}",
                        note=f"订单退款（订单 {order_id}）",
                        idempotency_key=f"refund:{order_id}")
                except _wallet.WalletError as exc:
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail=f"退款入账钱包失败，请稍后重试（{exc}）")
                result["refund_to_wallet"] = True
                result["refund_amount"] = f"{refund_money:.2f}"
                result["wallet_balance_after"] = f"{entry.balance_after:.2f}"

        # 状态与时间已由上面那次条件 UPDATE 原子写入，这里让当前事务里的 ORM 对象跟上
        order.status = "refunded"
        order.refunded_at = now
        order.refund_reason = (request.reason or "").strip()[:255]
        # 退款同样释放优惠券额度（用户没付钱买成这笔，次数不该算用掉）
        coupons.release(db, order.coupon_usage_id)
        result["coupon_released"] = bool(order.coupon_usage_id)
        # 兑换码折扣权益同样还回未用（与优惠券同一道理；是否过期由下次选用时判定）
        result["credit_restored"] = _restore_discount_credit(db, order_id)
        commit_with_retry(db, label="订单退款")

        _audit(db, admin_id, "economy_refund_order", "order", None,
               {"order_id": order_id, "kind": kind, "reason": order.refund_reason,
                "revoked_points": result["revoked_points"],
                "revoked_days": result["revoked_days"],
                "revoked_xp": result["revoked_xp"],
                "rebate_reversed": str(result["rebate_reversed"]),
                "refund_to_wallet": result.get("refund_to_wallet", False),
                "refund_amount": result.get("refund_amount", "0.00"),
                "coupon_released": result.get("coupon_released", False),
                "allow_negative": request.allow_negative})
        db.commit()
        logger.info("订单退款完成: %s kind=%s %s", order_id, kind, result)

        # 通知要用到的都取成纯值：上面提交过，回读 ORM 属性 = 一次隐式回查
        detail_bits = []
        if result["revoked_points"]:
            detail_bits.append(f"已扣回 {result['revoked_points']} 积分")
        if result["revoked_days"]:
            detail_bits.append(f"已回滚 {result['revoked_days']} 天会员")
        if result.get("refund_to_wallet"):
            detail_bits.append(f"已退回钱包 ¥{result['refund_amount']}")
        if result.get("rebate_reversal_failed"):
            detail_bits.append("邀请返利未能撤回（邀请人余额不足，已挂欠账）")
        result["_notify"] = {"user_id": user.id, "reason": order.refund_reason or "",
                             "details": detail_bits}
        return result

    info = await run_in_threadpool(_refund)
    notify_ctx = info.pop("_notify", None)
    if notify_ctx is None:
        return info      # 并发抢不到的那一次：订单已被处理，不重复通知

    # 先提交再通知：履约事务未提交时另开会话写站内信会撞 SQLite 写锁，通知会被静默丢掉
    # r46：内联 try/except 并入统一 quiet 守卫（横切只许一套）。
    reason_text = f"\n原因：{notify_ctx['reason']}" if notify_ctx["reason"] else ""
    await notify_admin_event_quiet(
        event_type=AdminEvent.ORDER_REFUNDED,
        log_ctx=f"refund order_id={order_id}",
        user_id=notify_ctx["user_id"],
        title="↩️ 订单已退款",
        content=(f"订单 {order_id} 已退款。"
                 + ("；".join(notify_ctx["details"]) + "。" if notify_ctx["details"] else "")
                 + reason_text),
        related_id=None,
    )

    return info


def _restore_discount_credit(db: Session, order_id: str) -> bool:
    """把这笔订单占用的兑换码折扣权益还回未用（关单/退款时调用）

    兑换码折扣权益是「下单即占」（``create_payment_order`` 里条件 UPDATE 置 used），
    与优惠券预订同一道理：订单没付成（关单）或钱退了（退款），权益不该被烧掉。
    是否过期由下次选用时按 ``expires_at`` 判定，这里只管还。
    """
    restored = (
        db.query(models.ExchangeDiscountCredit)
        .filter(
            models.ExchangeDiscountCredit.used_order_id == order_id,
            models.ExchangeDiscountCredit.status == "used",
        )
        .update({"status": "unused", "used_order_id": None},
                synchronize_session=False)
    )
    return bool(restored)


# 会员经验已随等级体系于 2026-11-14 删除（backend/member_level.py
# 已删，履约不再发经验）：退款无经验可撤，revoked_xp 恒为 0（见上
# 文 refund_order）。批 2 的 _reverse_xp 已随之删去，不带回。
def _reverse_rebate(db: Session, order_id: str, allow_negative: bool) -> tuple[object, bool]:
    """撤回这笔订单带给邀请人的返利（按账本引用精确找，不重算比例）

    返回 (撤回金额, 是否欠账)：邀请人余额不足时主退款照做、返利欠着，
    调用方必须把欠账如实告诉前端——否则界面上的 rebate_reversed=0
    既可能是"本来就没返利"，也可能是"返利没撤回来"，无法区分。

    账本只有钱包一条（invite_rebate_accruals，source=subscription_order）：
    返利已入邀请人钱包（status=credited）时从钱包扣回
    （kind=rebate_reversal，幂等键 rebate-reversal:<订单号>），
    成功则计提行转 reversed；余额不足则计提行置 reversal_owed
    （仍是 credited，待管理员重试/豁免），主退款照做、返利欠着。
    撤回金额是人民币元 Decimal；没计提/未入账/已撤回时为 0。
    积分时代的返利台账与欠账表已随积分全清，不再有第二条腿。
    """
    from backend import invite_rebate as _invite_rebate
    from backend import wallet as _wallet

    accrual = db.query(models.InviteRebateAccrual).filter(
        models.InviteRebateAccrual.source ==
        _invite_rebate.SOURCE_SUBSCRIPTION_ORDER,
        models.InviteRebateAccrual.source_ref == order_id,
    ).first()
    if accrual is not None:
        if accrual.status == _invite_rebate.STATUS_CREDITED:
            inviter = db.query(models.WebUser).filter(
                models.WebUser.id == accrual.inviter_id).first()
            if inviter is None:
                return 0, False
            try:
                _wallet.debit(
                    db, inviter.id, accrual.rebate_amount,
                    kind="rebate_reversal", ref_id=f"rebate:{order_id}",
                    note=f"订单退款撤回返利（订单 {order_id}）",
                    idempotency_key=f"rebate-reversal:{order_id}",
                    allow_negative=allow_negative)
            except _wallet.WalletError as exc:
                # 余额不足（或其他钱包拒绝）：不因为"退不了返利"就整单
                # 退不了款——主退款照做，返利欠着（计提行置 owed 标记，
                # 管理员可在钱包管理端重试/豁免）。
                logger.warning("撤回返利失败（订单 %s）: %s", order_id, exc)
                accrual.reversal_owed = True
                return 0, True
            accrual.status = _invite_rebate.STATUS_REVERSED
            accrual.reversal_owed = False
            accrual.reversed_at = datetime.now()
            return accrual.rebate_amount, False
        # pending（从未入账）/ reversed（已扣回）：无返利可撤。
        return 0, False
    # 没有钱包计提行：这笔订单没产生过钱包返利，无可撤。
    return 0, False


@admin_orders_router.get("/economy/refunds")
def list_recent_refunds(
    limit: int = 20,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """最近的退款 / 关单记录（带原因）——对账与客服复盘用

    订单列表里只有状态标签，说不清「为什么退的」；这里把原因和时间一并给出。
    """
    limit = max(1, min(int(limit or 20), 100))
    rows: list[tuple[object, str]] = []
    for model, kind in ((models.SubscriptionOrder, "subscription"),):
        for o in db.query(model).filter(
            model.status.in_(("refunded", "closed"))
        ).order_by(model.created_at.desc()).limit(limit).all():
            rows.append((o, kind))

    # 订单表不带用户名，这里补上——否则对账要一条条去查用户
    user_ids = {o.user_id for o, _ in rows}
    users = {u.id: u.username for u in db.query(models.WebUser).filter(
        models.WebUser.id.in_(user_ids)).all()} if user_ids else {}

    out = [
        {
            "order_id": o.order_id,
            "kind": kind,
            "status": o.status,
            "username": users.get(o.user_id, f"用户 {o.user_id}"),
            "reason": o.refund_reason or "",
            "at": (o.refunded_at or o.closed_at).isoformat()
                  if (o.refunded_at or o.closed_at) else None,
        }
        for o, kind in rows
    ]
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return {"records": out[:limit]}


__all__ = ["admin_orders_router"]
