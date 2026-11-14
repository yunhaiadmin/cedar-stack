# -*- coding: utf-8 -*-
"""用户端钱包 API（钱包批 1：只入账，不扣款）。

- GET  /api/user/wallet：当前余额 + 钱包开关 + 最近流水（第一页）。
- GET  /api/user/wallet/ledger：自己的流水分页（可按 kind 筛选）。
- POST /api/user/wallet/redeem：余额兑换码入账——只认
  reward_type=balance 的卡码，核销主体复用 codes.redeem_code
  （claim_code 原子占位 + 蜜罐 + 指名 + 每人每码一次），非余额码
  在占位前分流拒绝、不烧码。

批 1 不提供任何扣款/充值端点：订阅购买、购流量、在线充值分别是
批 2 / 批 2 / 批 3，不许在这里假接。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import codes, models
from backend import wallet as wallet_service
from backend.api.user import get_current_user
from backend.database import get_db
from backend.db_retry import commit_with_retry
from backend.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)

wallet_router = APIRouter(prefix="/api/user", tags=["用户端·钱包"])


@wallet_router.get("/wallet")
def get_wallet(current_user: models.WebUser = Depends(get_current_user),
               db: Session = Depends(get_db)):
    balance = wallet_service.get_balance(db, current_user.id)
    rows, total = wallet_service.ledger_query(
        db, user_id=current_user.id, page=1, page_size=10)
    return {
        "balance": f"{balance:.2f}",
        "wallet_enabled": wallet_service.wallet_enabled(db),
        "ledger": [wallet_service.ledger_dict(r) for r in rows],
        "ledger_total": total,
    }


@wallet_router.get("/wallet/ledger")
def get_wallet_ledger(kind: str = "", page: int = 1, page_size: int = 20,
                      current_user: models.WebUser = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rows, total = wallet_service.ledger_query(
        db, user_id=current_user.id, kind=kind or None,
        page=page, page_size=page_size)
    return {
        "items": [wallet_service.ledger_dict(r) for r in rows],
        "total": total,
        "page": max(int(page or 1), 1),
        "page_size": min(max(int(page_size or 20), 1), 100),
    }


class WalletRedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


class WalletPaySubscriptionRequest(BaseModel):
    plan_id: int = Field(..., gt=0)
    coupon_code: str = Field(default="", max_length=64)
    # 客户端每次确认生成一个新键：重试/双击只成交一次（订单号即由此键
    # 派生，幂等的第一道门；钱包扣款的幂等键是第二道）。
    idempotency_key: str = Field(..., min_length=1, max_length=60)


def _pay_subscription_core(db: Session, user: models.WebUser,
                           req: WalletPaySubscriptionRequest) -> tuple[dict, list]:
    """余额买订阅的同步事务核：建单 → 占券/占权益 → 扣余额 → 履约 → 补返利。

    返回 (result, pending_notifications)。成功结尾自行 commit；
    失败抛 HTTPException，调用方回滚。通知由 async 包装在提交后发
    （与 economy.payment_notify 同一写法：履约事务未提交时另开会话
    写站内信会撞 SQLite 写锁）。

    幂等：订单号由确认键派生（``WB`` + key），重放直接返回原订单，
    不重复扣款、不重复发订阅；并发同键撞唯一约束时回滚后按重放返回。
    实付为 0（全额券）时跳过扣款，仍凭订单号幂等。
    """
    from sqlalchemy.orm import joinedload

    from backend import coupons
    from backend import invite_rebate as _invite_rebate
    from backend.api import economy as economy_api
    from backend.agents import money

    if not wallet_service.wallet_enabled(db):
        raise HTTPException(status_code=403, detail="钱包功能暂未开启")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")

    key = (req.idempotency_key or "").strip()
    order_id = f"WB{key}"

    def _replay_response(order: models.SubscriptionOrder) -> tuple[dict, list]:
        sub = None
        if order.subscription_id:
            sub = db.query(models.UserSubscription).filter(
                models.UserSubscription.id == order.subscription_id).first()
        return {
            "ok": True,
            "replayed": True,
            "order_id": order.order_id,
            "plan_name": order.item_name,
            "paid_amount": f"{money(order.amount):.2f}",
            "list_price": f"{money(order.list_price or 0):.2f}",
            "discount_amount": f"{money(order.discount_amount or 0):.2f}",
            "balance_after": f"{wallet_service.get_balance(db, user.id):.2f}",
            "subscription_id": order.subscription_id,
            "end_date": sub.end_date.isoformat() if sub else None,
        }, []

    existing = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == order_id).first()
    if existing is not None:
        if existing.user_id != user.id:
            raise HTTPException(status_code=400, detail="确认标识已被占用，请重新操作")
        return _replay_response(existing)

    plan = db.query(models.SubscriptionPlan).options(
        joinedload(models.SubscriptionPlan.realm)
    ).filter(
        models.SubscriptionPlan.id == req.plan_id,
        models.SubscriptionPlan.is_active == True,  # noqa: E712
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="套餐不存在")

    # 计价走公共函数（与网关下单同一套口径：等级折扣 → 优惠券/折扣权益）。
    price = economy_api._price_subscription_purchase(
        db, user, plan, req.coupon_code)
    paid = price["paid_amount"]
    balance = wallet_service.get_balance(db, user.id)
    if paid > balance:
        shortfall = money(paid - balance)
        raise HTTPException(
            status_code=400,
            detail=f"钱包余额不足，还差 ¥{shortfall:.2f}，请先充值或更换套餐")

    # 扣款先于建单：并发同键撞上钱包幂等唯一约束时，_apply 内部的
    # db.rollback() 只会冲掉本次事务里已有的写——扣款在最前、其余写
    # 都还没发生，回滚无损失；回放到的已扣行直接继续走建单履约。
    # 扣款与订单同一事务：后续任一步失败，扣款一起回滚，不留半笔账。
    debit_entry = None
    if paid > 0:
        debit_entry, _replayed = wallet_service.debit(
            db, user.id, paid, kind="subscription_purchase",
            ref_id=order_id, note=f"余额购买订阅「{plan.name}」",
            idempotency_key=key)
        if _replayed:
            # 同键已扣过款但订单还没落（上一笔在扣款后、建单前崩了）：
            # 订单号已派生且唯一，继续建单履约即补齐，不重复扣款。
            done = db.query(models.SubscriptionOrder).filter(
                models.SubscriptionOrder.order_id == order_id).first()
            if done is not None:
                if done.user_id != user.id:
                    raise HTTPException(status_code=400, detail="确认标识已被占用，请重新操作")
                return _replay_response(done)

    order = models.SubscriptionOrder(
        order_id=order_id, user_id=user.id, plan_id=plan.id,
        item_name=plan.name, amount=paid,
        list_price=price["list_price"], discount_amount=price["discount_amount"],
        payment_method="wallet", status="pending")
    db.add(order)
    # 先 flush 拿 id：_fulfill_order 里的 _claim_order 按 id 做条件
    # UPDATE 抢单——id 还是 None 时过滤条件退化成 id IS NULL，永远
    # 抢不到，履约静默跳过（钱已扣、订阅没发、订单永久 pending）。
    # 有优惠券时 reserve 的查询会顺带 autoflush 掩盖此问题，无券
    # 路径必现（test_wallet_batch2 回归覆盖）。
    db.flush()
    credit = price["credit"]
    if credit is not None:
        # 兑换码折扣权益：下单即核销（一张只用一次），与网关下单同一写法。
        claimed = (
            db.query(models.ExchangeDiscountCredit)
            .filter(
                models.ExchangeDiscountCredit.id == credit.id,
                models.ExchangeDiscountCredit.status == "unused",
            )
            .update(
                {"status": "used", "used_order_id": order_id},
                synchronize_session=False,
            )
        )
        if not claimed:
            db.rollback()
            raise HTTPException(status_code=409, detail="折扣权益已被使用，请刷新后重试")
    if price["preview"] is not None:
        coupon = db.query(models.CouponCode).filter(
            models.CouponCode.id == price["preview"]["coupon_id"]).first()
        if coupon is None:
            db.rollback()
            raise HTTPException(status_code=400, detail="优惠码不存在")
        usage = coupons.reserve(
            db, coupon=coupon, user=user, order_id=order_id,
            kind="subscription", list_price=price["coupon_base"],
            discount=price["discount_amount"] - price["member_discount_amount"],
            paid=paid)
        order.coupon_usage_id = usage.id

    try:
        pending = economy_api._fulfill_order(db, subscription_order=order)
    except Exception:
        db.rollback()
        raise

    # 返利补入账（#175 计提、钱包批 1 挂账）：钱包在位时历史 pending
    # 计提在这里一并结算——余额购买本身的新返利已由 _fulfill_order
    # 内的计提钩子同事务入账，这里只补更早窗口挂账的旧笔。
    try:
        _invite_rebate.settle_pending_rebates(db)
    except Exception:  # noqa: BLE001 — 补入账失败不许拖垮本次购买
        logger.exception("返利补入账失败（订单 %s）", order_id)

    try:
        commit_with_retry(db, label="余额购买订阅")
    except Exception:
        # 并发同键：对方已先成交，本事务回滚后按重放返回。
        db.rollback()
        done = db.query(models.SubscriptionOrder).filter(
            models.SubscriptionOrder.order_id == order_id).first()
        if done is not None and done.user_id == user.id:
            return _replay_response(done)
        raise
    sub = None
    if order.subscription_id:
        sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.id == order.subscription_id).first()
    return {
        "ok": True,
        "replayed": False,
        "order_id": order_id,
        "plan_name": plan.name,
        "paid_amount": f"{money(paid):.2f}",
        "list_price": f"{money(price['list_price']):.2f}",
        "discount_amount": f"{money(price['discount_amount']):.2f}",
        "balance_after": (f"{money(debit_entry.balance_after):.2f}"
                          if debit_entry is not None
                          else f"{balance:.2f}"),
        "subscription_id": order.subscription_id,
        "end_date": sub.end_date.isoformat() if sub else None,
    }, pending


@wallet_router.post("/wallet/redeem")
def redeem_wallet_code(req: WalletRedeemRequest,
                       current_user: models.WebUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """余额码兑换：只成交 reward_type=balance 的码，其余不烧码分流。"""
    allowed, _ = check_rate_limit(f"wallet_redeem:{current_user.id}", 10, 60)
    if not allowed:
        invalid = codes.classify_invalid_code(db, current_user, req.code)
        if invalid is not None:
            return JSONResponse(
                status_code=400,
                content={"detail": invalid["message"],
                         "error_code": invalid["error_code"]})
        return JSONResponse(status_code=429,
                            content={"detail": "操作过于频繁，请稍后再试"})

    if not current_user.is_active:
        return JSONResponse(status_code=403,
                            content={"detail": "账号已被禁用，请联系管理员"})

    if not wallet_service.wallet_enabled(db):
        return JSONResponse(status_code=403,
                            content={"detail": "钱包功能暂未开启"})

    # 分流：找得到且不是蜜罐的码，reward_type 不是 balance 一律不核销
    # （不占位、不烧码），提示去对应入口；蜜罐与不存在交给 redeem_code
    # 的既有口径处理（蜜罐必须先触发陷阱，不能被分流短路）。
    found = codes.find_reg_code(db, req.code.strip())
    if found is not None and not codes.is_honeypot(found):
        reward_type = getattr(found, "reward_type", "subscription") \
            or "subscription"
        if reward_type != codes.REWARD_BALANCE:
            return JSONResponse(
                status_code=400,
                content={"detail": "这张卡码不是余额码，请到会员页兑换",
                         "error_code": "not_balance_code"})

    result = codes.redeem_code(db, current_user, req.code)
    if not result.get("success"):
        db.rollback()
        content = {"detail": result.get("message") or "卡码无效"}
        if result.get("error_code"):
            content["error_code"] = result["error_code"]
        return JSONResponse(status_code=400, content=content)
    # 占位与钱包入账由这里统一提交（与会员核销同一口径）
    commit_with_retry(db, label="钱包兑换码入账")
    return {
        "ok": True,
        "message": result.get("message"),
        "balance_value": result.get("balance_value"),
        "balance": result.get("balance_after"),
    }


def _pay_subscription_guarded(db: Session, user: models.WebUser,
                              req: WalletPaySubscriptionRequest):
    """同步包装（供线程池调用）：跑核心，失败时在**同一线程**内回滚。

    async 路由本体不许碰同步 DB（阻塞路由契约，第 14 步）：回滚也
    是同步 DB 操作，放进这个同步函数里随核心一起下放线程池。
    """
    try:
        return _pay_subscription_core(db, user, req)
    except (HTTPException, wallet_service.WalletError):
        db.rollback()
        raise


@wallet_router.post("/wallet/pay-subscription")
async def pay_subscription_with_wallet(
        req: WalletPaySubscriptionRequest,
        current_user: models.WebUser = Depends(get_current_user),
        db: Session = Depends(get_db)):
    """余额买订阅：扣余额 + 发订阅 + 返利计提，同一事务（async 包装）。

    网关购买开关（subscription_purchase_enabled）关着也不拦——余额
    是独立购买通道，门控只看钱包总开关（wallet_enabled）。计价与
    网关下单同一套口径（等级折扣 → 优惠券/自动折扣权益）；余额不足
    明示差额，不静默跳网关。履约通知在提交后发（与 payment_notify
    同一写法）。同步核 + 回滚全在线程池内，路由本体不碰 DB。
    """
    from fastapi.concurrency import run_in_threadpool

    from backend.api import economy as economy_api

    allowed, _ = check_rate_limit(
        f"wallet_pay_sub:{current_user.id}", 10, 60)
    if not allowed:
        return JSONResponse(status_code=429,
                            content={"detail": "操作过于频繁，请稍后再试"})
    try:
        result, pending = await run_in_threadpool(
            _pay_subscription_guarded, db, current_user, req)
    except HTTPException:
        raise
    except wallet_service.WalletError as exc:
        return JSONResponse(status_code=400,
                            content={"detail": str(exc), "code": exc.code})
    if pending:
        await economy_api.send_fulfill_notifications(pending)
    return result
