# -*- coding: utf-8 -*-
"""用户端钱包 API（钱包批 1：只入账，不扣款）。

- GET  /api/user/wallet：当前余额 + 钱包开关 + 最近流水（第一页）。
- GET  /api/user/wallet/ledger：自己的流水分页（可按 kind 筛选）。
- POST /api/user/wallet/redeem：余额兑换码入账——只认
  reward_type=balance 的卡码，核销主体复用 codes.redeem_code
  （claim_code 原子占位 + 蜜罐 + 指名 + 每人每码一次），非余额码
  在占位前分流拒绝、不烧码。
- POST /api/user/wallet/gift-codes：把余额做成礼品卡——扣款 + 建
  source='gift' 的 balance 兑换码，同一事务（钱包页 ax 重做，
  2026-11-15）。
- GET  /api/user/wallet/gift-codes：我生成的礼品卡列表；查询时
  惰性退款（过期未用且未退过的，面值退回并停用码，只退一次）。

- POST /api/user/wallet/topup/orders + GET|POST
  /api/user/wallet/topup/notify：在线充值（钱包批 3）——易支付收
  人民币、回调验签后按订单汇率快照入账美元余额，签名/验签复用
  economy 的唯一实现，汇率走 backend/wallet_topup（自动抓价）。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import codes, models, sub_accounts
from backend import wallet as wallet_service
from backend import wallet_topup
from backend.api.user import get_current_user
from backend.database import get_db
from backend.db_retry import commit_with_retry
from backend.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)

wallet_router = APIRouter(prefix="/api/user", tags=["用户端·钱包"])


def _fen_to_usd_text(fen: int) -> str:
    """美分 → 美元展示串（100 → "1"，7000 → "70"，150 → "1.5"）。"""
    return format((Decimal(int(fen)) / Decimal(100)).normalize(), "f")


def _topup_info(db: Session) -> dict:
    """充值信息（随钱包信息接口下发，口径对应友站 pay/info）。

    enabled = 钱包开 且 易支付三键齐全；usd2cny 是含缓冲的生效汇率
    （读时懒刷新，口径见 backend/wallet_topup；抓价失败不许拖垮
    钱包页，给 None、前端不展示预览）；fee_rate 是手续费率现值；
    min/max 是单笔限额（美元口径与美分口径各一份，前端校验全按
    本块走、不写死）；methods 按 qqpay 门禁拼。
    """
    from backend.api import economy as economy_api

    gateway = economy_api._get_config(db, "payment_gateway_url", "").strip()
    pid = economy_api._get_config(db, "payment_partner_id", "").strip()
    key = economy_api._get_config(db, "payment_partner_key", "").strip()
    config = wallet_service.wallet_config(db)
    methods = ["alipay", "wxpay"]
    if economy_api._get_config(
            db, "payment_qqpay_enabled", "false").strip().lower() == "true":
        methods.append("qqpay")
    rate = None
    try:
        rate = format(wallet_topup.effective_rate(db).normalize(), "f")
    except Exception:  # noqa: BLE001 — 汇率读不到只灭预览，不灭钱包页
        logger.exception("充值汇率读取失败，钱包页不展示预览")
    min_fen = int(config["wallet_min_topup_amount"])
    max_fen = int(config["wallet_topup_max_amount"])
    return {
        "enabled": bool(config["wallet_enabled"] and gateway and pid and key),
        "usd2cny": rate,
        "fee_rate": int(config["wallet_topup_fee_pct"]),
        "min_usd_fen": min_fen,
        "max_usd_fen": max_fen,
        "min_usd": _fen_to_usd_text(min_fen),
        "max_usd_epay": _fen_to_usd_text(max_fen) if max_fen > 0 else None,
        "methods": methods,
    }


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
        "topup": _topup_info(db),
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
    # 按月售卖（2026-11-14）：购买月数 1–12，0/缺省＝套餐基准月数
    months: int = Field(default=0, ge=0, le=12)
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

    # 计价走公共函数（与网关下单同一套口径：优惠券/折扣权益 →
    # 换套餐抵扣）。实扣一律以 payable_amount 为准（券后价再扣抵扣）。
    price = economy_api._price_subscription_purchase(
        db, user, plan, req.coupon_code, months=req.months or None)
    paid = price["payable_amount"]
    balance = wallet_service.get_balance(db, user.id)
    if paid > balance:
        shortfall = money(paid - balance)
        raise HTTPException(
            status_code=400,
            detail=f"钱包余额不足，还差 ${shortfall:.2f}，请先充值或更换套餐")

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
        days_granted=price["days"],
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
            discount=price["coupon_discount_amount"] - price["member_discount_amount"],
            paid=price["paid_amount"])
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
        "switch_credit": f"{money(price['switch_credit']):.2f}",
        "switch_type": price["switch_type"],
        "months": price["months"],
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
    # 家庭子账号禁止经济动作：子账号没有钱包，兑换回主账号操作
    sub_accounts.forbid_sub_account(current_user)
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

    # 家庭子账号禁止经济动作：子账号没有钱包与订阅，回主账号操作
    sub_accounts.forbid_sub_account(current_user)
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


# ==================== 礼品卡：用户自生成兑换码 ====================

#: 礼品卡有效期上限（天）：与钱包页文案「有效期最长 7 天」一致
GIFT_CODE_MAX_VALID_DAYS = 7


class WalletGiftCodeCreateRequest(BaseModel):
    # 金额用字符串收，避免 float 丢精度（与管理端余额码生成同口径）；
    # 不设 min_length：空串也要走端点内的统一 400，而不是 pydantic 422
    amount: str = Field(..., max_length=16)
    valid_days: int = Field(default=GIFT_CODE_MAX_VALID_DAYS)


def _gift_code_status(code: models.RegistrationCode, now: datetime) -> str:
    if (code.use_count or 0) >= (code.max_uses or 1):
        return "used"
    if code.expires_at and code.expires_at < now:
        return "expired"
    return "unused"


@wallet_router.post("/wallet/gift-codes")
def create_gift_code(req: WalletGiftCodeCreateRequest,
                     current_user: models.WebUser = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """把余额做成礼品卡：扣款 + 建 balance 兑换码，同一事务。

    码就是普通的 RegistrationCode（reward_type=balance、source='gift'、
    created_by=本人、max_uses=1），兑换走既有 redeem 入口，不另起核销
    逻辑。扣款复用 wallet_service.debit（条件 UPDATE 防超额 + 行锁），
    校验顺序与 redeem 同口径：子账号拦截 → 限流 → 账号状态 → 钱包开关。
    """
    sub_accounts.forbid_sub_account(current_user)
    allowed, _ = check_rate_limit(
        f"wallet_gift_create:{current_user.id}", 10, 60)
    if not allowed:
        return JSONResponse(status_code=429,
                            content={"detail": "操作过于频繁，请稍后再试"})
    if not current_user.is_active:
        return JSONResponse(status_code=403,
                            content={"detail": "账号已被禁用，请联系管理员"})
    if not wallet_service.wallet_enabled(db):
        return JSONResponse(status_code=403,
                            content={"detail": "钱包功能暂未开启"})

    days = req.valid_days
    if not isinstance(days, int) or days < 1 or days > GIFT_CODE_MAX_VALID_DAYS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"有效期只能选 1 到 {GIFT_CODE_MAX_VALID_DAYS} 天"})
    try:
        amount = Decimal(str(req.amount).strip())
    except (InvalidOperation, ValueError):
        return JSONResponse(status_code=400,
                            content={"detail": "金额格式不正确"})
    if amount != wallet_service.money(amount):
        return JSONResponse(status_code=400,
                            content={"detail": "金额最多保留两位小数"})
    amount = wallet_service.money(amount)
    if amount <= 0:
        return JSONResponse(status_code=400,
                            content={"detail": "金额必须大于 0"})
    balance = wallet_service.get_balance(db, current_user.id)
    if amount > balance:
        return JSONResponse(
            status_code=400,
            content={"detail": f"余额不足：当前可用余额 ${balance:.2f}"})

    raw = None
    for _ in range(10):
        candidate = codes.render_code(code_type=codes.CODE_TYPE_RENEW, days=0)
        if codes.find_reg_code(db, candidate) is None:
            raw = candidate
            break
    if raw is None:
        return JSONResponse(status_code=500,
                            content={"detail": "卡码生成冲突，请重试"})

    now = datetime.now()
    code = models.RegistrationCode(
        code=raw, max_uses=1, use_count=0, is_active=True,
        note="礼品卡（用户生成）",
        expires_at=now + timedelta(days=days),
        created_by=current_user.id,
        code_type=codes.CODE_TYPE_RENEW, days=0,
        source="gift", reward_type=codes.REWARD_BALANCE,
        balance_value=amount,
        # 一人一批一码（2026-11-23）：礼品卡一次生成一张、自成一批，
        # 与批量入口同口径打标（见 codes.claim_code）。
        batch_id=codes.new_batch_id())
    db.add(code)
    db.flush()  # 先拿到 code.id，扣款流水 ref 要指向它
    try:
        entry, _replayed = wallet_service.debit(
            db, current_user.id, amount, kind="gift_code",
            ref_id=f"code:{code.id}", note="生成礼品卡")
    except wallet_service.WalletError as exc:
        db.rollback()
        return JSONResponse(status_code=400,
                            content={"detail": str(exc), "code": exc.code})
    commit_with_retry(db, label="生成礼品卡扣款")
    return {
        "ok": True,
        "code": raw,
        "amount": f"{amount:.2f}",
        "valid_days": days,
        "expires_at": code.expires_at.isoformat(),
        "balance": f"{entry.balance_after:.2f}",
    }


def _refund_expired_gift_codes(db: Session, user_id: int) -> bool:
    """惰性退款：本人礼品卡过期未用且未退过的，面值退回钱包 + 停用码。

    退款标记 = 本人流水里 kind='gift_code_refund' 且 ref_id='code:{id}'
    的行：查到已退则只补停用、绝不重复入账（不依赖 note 文案）。
    返回是否发生了写入（调用方据此决定要不要提交）。
    """
    now = datetime.now()
    rows = (db.query(models.RegistrationCode)
            .filter(models.RegistrationCode.created_by == user_id,
                    models.RegistrationCode.source == "gift",
                    models.RegistrationCode.expires_at.isnot(None),
                    models.RegistrationCode.expires_at < now)
            .all())
    changed = False
    for code in rows:
        if (code.use_count or 0) >= (code.max_uses or 1):
            continue  # 已被兑换：钱已到兑换人账上，不退生成人
        refunded = (db.query(models.WalletLedger.id)
                    .filter(models.WalletLedger.user_id == user_id,
                            models.WalletLedger.kind == "gift_code_refund",
                            models.WalletLedger.ref_id == f"code:{code.id}")
                    .first())
        if refunded is None:
            amount = wallet_service.money(code.balance_value or 0)
            if amount > 0:
                wallet_service.credit(
                    db, user_id, amount, kind="gift_code_refund",
                    ref_id=f"code:{code.id}",
                    note="礼品卡过期未使用，面值退回")
                changed = True
        if code.is_active:
            code.is_active = False
            changed = True
    return changed


@wallet_router.get("/wallet/gift-codes")
def list_gift_codes(status: str = "all",
                    current_user: models.WebUser = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """我生成的礼品卡（只回 source='gift' 且 created_by=本人的）。

    查询前先做一轮惰性退款（见 _refund_expired_gift_codes）；退款
    失败只回滚并记日志，列表照常返回，下次查询会再试，不许让退款
    把读口拖死。
    """
    try:
        if _refund_expired_gift_codes(db, current_user.id):
            commit_with_retry(db, label="礼品卡过期退款")
    except Exception:  # noqa: BLE001 — 退款失败不拖垮列表
        db.rollback()
        logger.exception("礼品卡惰性退款失败 user_id=%s", current_user.id)

    now = datetime.now()
    rows = (db.query(models.RegistrationCode)
            .filter(models.RegistrationCode.created_by == current_user.id,
                    models.RegistrationCode.source == "gift")
            .order_by(models.RegistrationCode.id.desc())
            .limit(200).all())
    items = []
    for code in rows:
        st = _gift_code_status(code, now)
        if status in ("unused", "used", "expired") and st != status:
            continue
        items.append({
            "id": code.id,
            "code": code.code,
            "amount": f"{wallet_service.money(code.balance_value or 0):.2f}",
            "status": st,
            "created_at": code.created_at.isoformat() if code.created_at else None,
            "expires_at": code.expires_at.isoformat() if code.expires_at else None,
        })
    return {"items": items, "total": len(items)}


# ==================== 在线充值（钱包批 3：易支付收人民币 → 美元余额） ====================

class WalletTopupCreateRequest(BaseModel):
    # 金额用字符串收（美元，≤2 位小数），避免 float 丢精度（与礼品卡同口径）
    amount_usd: str = Field(..., max_length=16)
    # 与 economy.CreateOrderRequest 同一白名单
    payment_method: str = Field(default="alipay",
                                pattern="^(alipay|wxpay|qqpay)$")


def _new_topup_order_no() -> str:
    """充值单号：WT + 毫秒时间戳 + 6 位随机大写 hex（与网关 RB 单同格式）。"""
    return f"WT{int(time.time() * 1000)}{uuid.uuid4().hex[:6].upper()}"


@wallet_router.post("/wallet/topup/orders")
def create_topup_order(req: WalletTopupCreateRequest,
                       current_user: models.WebUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """创建充值单：美元计价，易支付收人民币，回调后入账美元余额。

    校验与文案同 economy.create_payment_order 口径：子账号拦截 →
    限流 → 账号状态 → 钱包开关 → 三键缺失 503 → qqpay 门禁 →
    金额（≥最低额默认 $1、≤单笔上限默认 $70）→ 待支付上限。金额
    口径：用户选定美元额，应付人民币 ＝ 美元额 × 生效汇率（市场价
    ×(1+缓冲%)，见 backend/wallet_topup）× (1+费率%) 取整到分（手续
    费加在应付侧），到账 ＝ 美元额全额；汇率与两个金额都快照进
    订单，之后价格再变不影响本单。签名复用
    economy._yipay_sign（全站唯一实现），notify 指回本模块的充值
    回调、return 回钱包页。
    """
    from urllib.parse import urlencode

    from backend.api import economy as economy_api

    sub_accounts.forbid_sub_account(current_user)
    allowed, _ = check_rate_limit(f"wallet_topup_order:{current_user.id}", 10, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="下单过于频繁，请稍后再试")
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
    if not wallet_service.wallet_enabled(db):
        raise HTTPException(status_code=403, detail="钱包功能暂未开启")

    gateway = economy_api._get_config(
        db, "payment_gateway_url", "").strip().rstrip("/")
    pid = economy_api._get_config(db, "payment_partner_id", "").strip()
    key = economy_api._get_config(db, "payment_partner_key", "").strip()
    if not gateway or not pid or not key:
        raise HTTPException(status_code=503, detail="支付通道未配置，请联系管理员")

    if req.payment_method == "qqpay" and economy_api._get_config(
            db, "payment_qqpay_enabled", "false").strip().lower() != "true":
        raise HTTPException(status_code=400,
                            detail="QQ 钱包暂未开启，请选择其他支付方式")

    config = wallet_service.wallet_config(db)
    try:
        dollars = Decimal(str(req.amount_usd).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="金额格式不正确")
    if dollars != wallet_service.money(dollars):
        raise HTTPException(status_code=400, detail="金额最多保留两位小数")
    amount_usd_fen = int(dollars * 100)
    min_usd_fen = int(config["wallet_min_topup_amount"])
    if amount_usd_fen < min_usd_fen:
        raise HTTPException(status_code=400,
                            detail=f"单次充值至少 ${_fen_to_usd_text(min_usd_fen)}")
    max_usd_fen = int(config["wallet_topup_max_amount"])
    if max_usd_fen > 0 and amount_usd_fen > max_usd_fen:
        raise HTTPException(
            status_code=400,
            detail=f"法币单笔充值不能超过 ${_fen_to_usd_text(max_usd_fen)}")

    # 待支付上限（与 create_payment_order 同款口径与配置键，防 pending 堆积）
    try:
        max_pending = int(economy_api._get_config(
            db, "max_pending_orders_per_user", "5") or 5)
    except ValueError:
        max_pending = 5
    if max_pending > 0:
        pending = (
            db.query(models.WalletTopupOrder).filter(
                models.WalletTopupOrder.user_id == current_user.id,
                models.WalletTopupOrder.status == "pending",
            ).count()
        )
        if pending >= max_pending:
            raise HTTPException(
                status_code=429,
                detail=f"待支付订单过多（{pending} 笔），请先完成或取消后再下单",
            )

    fee_pct = int(config["wallet_topup_fee_pct"])
    # 汇率解析可能提交一次缓存刷新，必须在暂存订单之前调用
    rate = wallet_topup.effective_rate(db)
    amount_cny_fen, _fee_fen, credit_fen = wallet_topup.compute_topup(
        amount_usd_fen, rate, fee_pct)
    cny_yuan = Decimal(amount_cny_fen) / Decimal(100)

    order_no = _new_topup_order_no()
    site_url = economy_api._get_config(db, "site_url", "").strip().rstrip("/")
    params = {
        "pid": pid,
        "type": req.payment_method,
        "out_trade_no": order_no,
        "notify_url": f"{site_url}/api/user/wallet/topup/notify",
        "return_url": f"{site_url}/wallet?topup=1",
        "name": "钱包充值",
        "money": f"{cny_yuan:.2f}",
    }
    params["sign"] = economy_api._yipay_sign(params, key)
    params["sign_type"] = "MD5"
    pay_url = f"{gateway}/submit.php?{urlencode(params)}"

    order = models.WalletTopupOrder(
        order_no=order_no, user_id=current_user.id,
        amount_usd_fen=amount_usd_fen, amount_cny_fen=amount_cny_fen,
        credit_usd_fen=credit_fen,
        rate_snapshot=rate, fee_pct_snapshot=fee_pct,
        payment_method=req.payment_method, status="pending",
        payment_url=pay_url)
    db.add(order)
    commit_with_retry(db, label="创建充值订单")

    logger.info("创建充值订单: user=%s order=%s usd_fen=%s rate=%s cny_fen=%s credit_fen=%s",
                current_user.id, order_no, amount_usd_fen, rate,
                amount_cny_fen, credit_fen)
    return {
        "ok": True,
        "order_no": order_no,
        "amount_usd": f"{dollars:.2f}",
        "amount_usd_fen": amount_usd_fen,
        "amount_cny": f"{cny_yuan:.2f}",
        "amount_cny_fen": amount_cny_fen,
        "credit_usd": f"{Decimal(credit_fen) / Decimal(100):.2f}",
        "rate": format(rate.normalize(), "f"),
        "fee_pct": fee_pct,
        "pay_url": pay_url,
        "message": "订单已创建，正在跳转支付",
    }


@wallet_router.get("/wallet/topup/orders/{order_no}")
def get_topup_order(order_no: str,
                    current_user: models.WebUser = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """查询本人充值单状态（充值等待屏轮询到账用，只读）。

    只许查本人订单：按 order_no + user_id 双条件查，订单不存在
    或不属于当前用户一律 404，不泄露他人订单存在性。金额字段与
    下单返回同名同口径（美元/人民币各给字符串与美分/分两份），
    status 原样透出订单状态机口径（pending / paid / failed /
    expired），paid_at 为回调入账时间（未支付为 None）。
    """
    order = (
        db.query(models.WalletTopupOrder).filter(
            models.WalletTopupOrder.order_no == order_no,
            models.WalletTopupOrder.user_id == current_user.id,
        ).first()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {
        "ok": True,
        "order_no": order.order_no,
        "status": order.status,
        "amount_usd": f"{Decimal(order.amount_usd_fen) / Decimal(100):.2f}",
        "amount_usd_fen": order.amount_usd_fen,
        "amount_cny": f"{Decimal(order.amount_cny_fen) / Decimal(100):.2f}",
        "amount_cny_fen": order.amount_cny_fen,
        "credit_usd": f"{Decimal(order.credit_usd_fen) / Decimal(100):.2f}",
        "credit_usd_fen": order.credit_usd_fen,
        "payment_method": order.payment_method,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "created_at": (order.created_at.isoformat()
                       if order.created_at else None),
    }


def _topup_notify_core(db: Session, raw: dict) -> tuple:
    """充值回调同步核：验签 → 查单 → 金额核对 → 抢单 → 入账 → 返利。

    返回 (应答文本, 待发通知)。与 economy.payment_notify 同口径：
    验签复用 _verify_yipay_notify（唯一实现）；金额对不上不入账、
    回 fail 让网关重试；履约幂等靠订单状态机（_claim_order 条件
    UPDATE 抢单）+ 钱包流水 idempotency_key=order_no 双保险，重复
    回调只入账一次；返利计提套保存点，失败不拖垮入账（记 partial
    通知请用户联系客服补发，同 _fulfill_order 的接法）。
    """
    from backend import invite_rebate as _invite_rebate
    from backend.api import economy as economy_api
    from backend.notifications import AdminEvent

    out_trade_no = str(raw.get("out_trade_no") or "")
    trade_status = str(raw.get("trade_status") or "")
    logger.info("充值回调: order=%s status=%s", out_trade_no, trade_status)

    key = economy_api._get_config(db, "payment_partner_key", "").strip()
    if not key:
        return "fail", []
    if not economy_api._verify_yipay_notify(raw, key):
        logger.warning("充值回调验签失败: order=%s", out_trade_no)
        return "fail", []
    if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return "success", []  # 非成功状态直接确认，避免重复通知

    order = db.query(models.WalletTopupOrder).filter(
        models.WalletTopupOrder.order_no == out_trade_no).first()
    if order is None:
        logger.warning("充值回调订单不存在: %s", out_trade_no)
        return "fail", []

    reported = economy_api._notify_amount(raw.get("money"))
    expected = (Decimal(order.amount_cny_fen) / Decimal(100)).quantize(
        Decimal("0.01"))
    if reported is not None and reported != expected:
        logger.error("充值回调被拒（金额不符：订单应付 %s，回调报 %s）: order=%s",
                     expected, reported, out_trade_no)
        return "fail", []

    try:
        if not economy_api._claim_order(db, models.WalletTopupOrder, order):
            return "success", []  # 已履约/已关单：幂等确认，不重复入账
        order.trade_no = str(raw.get("trade_no") or "") or None
        user = db.query(models.WebUser).filter(
            models.WebUser.id == order.user_id).first()
        if user is None:
            raise RuntimeError(f"充值履约缺料：订单 {out_trade_no} 的用户已不存在")
        credit_amount = Decimal(order.credit_usd_fen) / Decimal(100)
        wallet_service.credit(
            db, order.user_id, credit_amount, kind="recharge",
            ref_id=order.order_no, note="在线充值",
            idempotency_key=order.order_no)
        pending = [dict(
            event_type="economy.wallet_topup_success",
            user_id=user.id,
            title="💰 充值到账",
            content=(f"充值已到账：余额增加 ${credit_amount:.2f}"
                     f"（实付 ¥{expected:.2f}）。"),
            related_id=None,
        )]
        # 邀请返利计提（钱包批 3）：充值按美元计价，paid_amount 就是
        # 用户选定的美元额——与 invite_rebate 的美元口径（分档门槛
        # $50）及订阅返利同一本账累计。
        try:
            with db.begin_nested():
                accrual = _invite_rebate.record_payment_rebate(
                    db, user,
                    source=_invite_rebate.SOURCE_WALLET_TOPUP,
                    source_ref=order.order_no,
                    paid_amount=Decimal(order.amount_usd_fen) / Decimal(100))
            if accrual is not None and accrual.status == _invite_rebate.STATUS_CREDITED:
                pending.append(dict(
                    event_type="economy.invite_rebate",
                    user_id=accrual.inviter_id,
                    title="💰 邀请返利到账",
                    content=(f"好友充值 ${accrual.paid_amount:.2f}，"
                             f"返利 ${accrual.rebate_amount:.2f} 已存入你的钱包余额。"),
                    related_id=None,
                ))
        except Exception:  # noqa: BLE001
            logger.exception("充值返利计提失败: %s", order.order_no)
            pending.append(dict(
                event_type=AdminEvent.FULFILL_PARTIAL,
                user_id=user.id,
                title="⚠️ 充值成功，邀请返利计提失败",
                content=(f"订单 {order.order_no} 的充值已到账，"
                         "但邀请返利计提失败，请联系客服补发。"),
                related_id=None,
            ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("充值履约失败: %s", out_trade_no)
        return "fail", []
    return "success", pending


@wallet_router.api_route("/wallet/topup/notify", methods=["GET", "POST"],
                         response_class=PlainTextResponse)
async def topup_notify(request: Request, db: Session = Depends(get_db)):
    """易支付充值异步回调：验签 → 入账 → 返回 success（同步核下放线程池）。"""
    raw = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            raw.update({k: v for k, v in form.items() if isinstance(v, str)})
        except Exception:  # noqa: BLE001
            pass

    from fastapi.concurrency import run_in_threadpool

    from backend.api import economy as economy_api

    reply, pending = await run_in_threadpool(_topup_notify_core, db, raw)
    # 先提交再发通知（与 payment_notify 同口径：履约事务未提交时另开
    # 会话写站内信会撞 SQLite 写锁）
    if pending:
        await economy_api.send_fulfill_notifications(pending)
    return reply
