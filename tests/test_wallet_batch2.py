"""钱包批 2 专项测试（2026-11-14）：余额买订阅 + 余额购流量 +
返利入账结算 + 退款回钱包 + 返利撤回欠账重试/豁免。

覆盖任务书点名的五项：扣款防负/幂等重放（买订阅、购流量两路）、
优惠券计价与网关同口径、pending 返利在钱包在位时即时结算、
reversed 不计入上限与分档累计、退款进余额（不走原路）且天数回滚、
邀请人余额不足时返利欠账并可重试/豁免、套餐接口的余额通道标识。

用内存 SQLite（StaticPool 跨线程共用连接，退款端点下放线程池），
不碰生产库。通知静音（与 PA-023 同款写法）。
"""
import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-wallet-batch2-key-32chars!")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import entitlements as ent
from backend import invite_rebate, member_level as ml, models, wallet as wallet_service
from backend import traffic_quota as tq
from backend.emby_server import models as em_models
from backend.api import admin_wallet, economy
from backend.api import orders_admin
from backend.api import wallet as wallet_api
from backend.integrations import store

GB = 1024 ** 3


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main",
                                   is_active=True))
    session.commit()
    ml.ensure_member_levels_seeded(session)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()
    store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        store.invalidate()
        ent._USER_PLAN_CACHE.clear()
        ent._PLAN_SNAPSHOT_CACHE.clear()


@pytest.fixture(autouse=True)
def _mute_notify(monkeypatch):
    """退款成功后的站内通知与本单无关，静音避免触碰真实通知通道。"""

    async def _noop(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "backend.notifications.notify_admin_event_quiet", _noop)


def _set(db, key, value):
    row = db.query(models.SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()
    store.invalidate(key)


def _user(db, username, **kw):
    kw.setdefault("member_xp", 0)
    kw.setdefault("member_level", 1)
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _plan(db, price="45", days=30, entitlements=None):
    p = models.SubscriptionPlan(name="测试套餐", price=Decimal(price),
                                duration_days=days, realm_id=1,
                                is_active=True, entitlements=entitlements)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _code(db, inviter, code_str):
    code = models.InvitationCode(code=code_str, user_id=inviter.id,
                                 is_active=True, max_uses=0, use_count=0,
                                 reward_points=0)
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def _relation(db, inviter, invitee, code):
    row = models.InvitationRecord(inviter_id=inviter.id,
                                  invitee_id=invitee.id, code_id=code.id,
                                  reward_points=0)
    db.add(row)
    db.commit()
    return row


def _accruals(db, inviter_id):
    return (db.query(models.InviteRebateAccrual)
            .filter_by(inviter_id=inviter_id)
            .order_by(models.InviteRebateAccrual.id).all())


def _pay(db, user, plan, key, coupon_code=""):
    return wallet_api._pay_subscription_core(
        db, user,
        wallet_api.WalletPaySubscriptionRequest(
            plan_id=plan.id, coupon_code=coupon_code, idempotency_key=key))


def _refund(db, admin, order_id):
    return asyncio.run(orders_admin.refund_order(
        order_id=order_id,
        request=orders_admin.RefundRequest(reason="钱包批 2 回归测试"),
        current_admin=admin,
        db=db,
    ))


def _traffic_user(db, balance="100"):
    """带 500GB 流量额度订阅的用户（购流量测试用）。"""
    user = _user(db, "tq-buyer")
    wallet_service.credit(db, user.id, Decimal(balance), kind="recharge")
    plan = _plan(db, entitlements={"monthly_traffic_bytes": 500 * GB})
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=1,
        start_date=now, end_date=now + timedelta(days=30), status="active")
    db.add(sub)
    db.commit()
    ent.invalidate(user.id)
    return user


# ==================== ① 余额买订阅 ====================

def test_pay_subscription_success(db):
    alice = _user(db, "alice-pay")
    wallet_service.credit(db, alice.id, Decimal("100"), kind="recharge")
    db.commit()
    plan = _plan(db)

    result, _pending = _pay(db, alice, plan, "pay-ok-1")

    assert result["ok"] is True
    assert result["replayed"] is False
    assert result["order_id"] == "WBpay-ok-1"
    assert result["paid_amount"] == "45.00"
    assert result["discount_amount"] == "0.00"
    assert result["balance_after"] == "55.00"
    assert wallet_service.get_balance(db, alice.id) == Decimal("55.00")

    order = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBpay-ok-1").one()
    assert order.status == "paid"
    assert order.payment_method == "wallet"
    assert order.subscription_id is not None
    sub = db.query(models.UserSubscription).filter_by(
        id=order.subscription_id).one()
    assert sub.status == "active"
    assert (sub.end_date - sub.start_date).days == 30

    rows = db.query(models.WalletLedger).filter_by(user_id=alice.id).all()
    debits = [r for r in rows if r.kind == "subscription_purchase"]
    assert len(debits) == 1
    assert debits[0].amount == Decimal("-45.00")


def test_pay_subscription_insufficient(db):
    alice = _user(db, "alice-poor")
    wallet_service.credit(db, alice.id, Decimal("10"), kind="recharge")
    db.commit()
    plan = _plan(db)

    with pytest.raises(HTTPException) as excinfo:
        _pay(db, alice, plan, "pay-poor-1")
    db.rollback()
    assert excinfo.value.status_code == 400
    assert "还差 ¥35.00" in excinfo.value.detail
    assert wallet_service.get_balance(db, alice.id) == Decimal("10.00")
    assert db.query(models.SubscriptionOrder).count() == 0
    assert db.query(models.UserSubscription).count() == 0
    # 失败不落扣款流水：只有入账那一条
    assert db.query(models.WalletLedger).count() == 1


def test_pay_subscription_replay_same_key(db):
    alice = _user(db, "alice-replay")
    wallet_service.credit(db, alice.id, Decimal("100"), kind="recharge")
    db.commit()
    plan = _plan(db)

    first, _ = _pay(db, alice, plan, "pay-dup-1")
    second, _ = _pay(db, alice, plan, "pay-dup-1")

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["order_id"] == second["order_id"]
    assert wallet_service.get_balance(db, alice.id) == Decimal("55.00")
    assert db.query(models.SubscriptionOrder).count() == 1
    assert db.query(models.UserSubscription).count() == 1


def test_pay_subscription_with_coupon(db):
    alice = _user(db, "alice-coupon")
    wallet_service.credit(db, alice.id, Decimal("100"), kind="recharge")
    db.add(models.CouponCode(code="SAVE10", kind="all",
                             discount_type="percent", value=90,
                             is_active=True))
    db.commit()
    plan = _plan(db)

    result, _ = _pay(db, alice, plan, "pay-coupon-1", coupon_code="SAVE10")

    # 9 折：45 → 实付 40.50、优惠 4.50（与网关下单同一计价口径）
    assert result["paid_amount"] == "40.50"
    assert result["discount_amount"] == "4.50"
    assert result["balance_after"] == "59.50"
    order = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBpay-coupon-1").one()
    assert order.coupon_usage_id is not None
    usage = db.query(models.CouponUsage).filter_by(
        id=order.coupon_usage_id).one()
    assert usage.status == "consumed"


def test_pay_subscription_wallet_disabled(db):
    alice = _user(db, "alice-off")
    wallet_service.credit(db, alice.id, Decimal("100"), kind="recharge")
    db.commit()
    _set(db, "wallet_enabled", "false")
    plan = _plan(db)

    with pytest.raises(HTTPException) as excinfo:
        _pay(db, alice, plan, "pay-off-1")
    db.rollback()
    assert excinfo.value.status_code == 403
    assert wallet_service.get_balance(db, alice.id) == Decimal("100.00")
    assert db.query(models.SubscriptionOrder).count() == 0


def test_pay_subscription_credits_inviter_rebate_inline(db):
    """余额购买的返利钩子：钱包在位时即时入邀请人钱包（不挂账）。"""
    inviter = _user(db, "inviter-inline")
    invitee = _user(db, "invitee-inline")
    code = _code(db, inviter, "CODEINL1")
    _relation(db, inviter, invitee, code)
    wallet_service.credit(db, invitee.id, Decimal("100"), kind="recharge")
    db.commit()
    plan = _plan(db)

    result, _ = _pay(db, invitee, plan, "pay-invite-1")

    assert result["ok"] is True
    rows = _accruals(db, inviter.id)
    assert len(rows) == 1
    assert rows[0].rebate_amount == Decimal("2.25")  # 默认 tiered 前段 5%
    assert rows[0].status == "credited"
    assert rows[0].wallet_ledger_id is not None
    assert wallet_service.get_balance(db, inviter.id) == Decimal("2.25")


# ==================== ② 余额购流量 ====================

def test_buy_traffic_success(db):
    user = _traffic_user(db, balance="100")

    result = tq.buy_traffic_with_balance(db, user.id, 100, "tq-ok-1")

    assert result["replayed"] is False
    assert result["paid_amount"] == "3.00"  # 默认单价 ¥3/100GB
    assert result["balance_after"] == "97.00"
    assert result["gb"] == 100
    assert wallet_service.get_balance(db, user.id) == Decimal("97.00")
    log = db.query(em_models.TrafficExchangeLog).filter_by(
        idempotency_key="tq-ok-1").one()
    assert log.kind == "balance_purchase"
    assert log.bytes_credited == 100 * GB
    # 抵扣只冲本周期已用：credit_bytes 现算可见，配额本身不扩
    assert result["summary"]["credit_bytes"] == 100 * GB
    assert result["summary"]["quota_bytes"] == 500 * GB


def test_buy_traffic_replay(db):
    user = _traffic_user(db, balance="100")

    first = tq.buy_traffic_with_balance(db, user.id, 200, "tq-dup-1")
    second = tq.buy_traffic_with_balance(db, user.id, 200, "tq-dup-1")

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["paid_amount"] == second["paid_amount"] == "6.00"
    assert wallet_service.get_balance(db, user.id) == Decimal("94.00")
    assert db.query(em_models.TrafficExchangeLog).count() == 1
    assert (db.query(models.WalletLedger)
            .filter_by(kind="traffic_purchase").count() == 1)


def test_buy_traffic_insufficient(db):
    user = _traffic_user(db, balance="2")

    with pytest.raises(wallet_service.InsufficientWalletBalance):
        tq.buy_traffic_with_balance(db, user.id, 100, "tq-poor-1")
    db.rollback()
    assert wallet_service.get_balance(db, user.id) == Decimal("2.00")
    assert db.query(em_models.TrafficExchangeLog).count() == 0
    assert (db.query(models.WalletLedger)
            .filter_by(kind="traffic_purchase").count() == 0)


def test_buy_traffic_bad_option(db):
    user = _traffic_user(db, balance="100")

    with pytest.raises(tq.TrafficError) as excinfo:
        tq.buy_traffic_with_balance(db, user.id, 150, "tq-bad-1")
    db.rollback()
    assert excinfo.value.code == "bad_option"
    assert wallet_service.get_balance(db, user.id) == Decimal("100.00")
    assert db.query(em_models.TrafficExchangeLog).count() == 0


def test_buy_traffic_without_quota(db):
    user = _user(db, "tq-noquota")
    wallet_service.credit(db, user.id, Decimal("100"), kind="recharge")
    db.commit()

    with pytest.raises(tq.TrafficError) as excinfo:
        tq.buy_traffic_with_balance(db, user.id, 100, "tq-noq-1")
    db.rollback()
    assert excinfo.value.code == "quota_not_enabled"
    assert wallet_service.get_balance(db, user.id) == Decimal("100.00")


# ==================== ③ 返利入账结算 ====================

def test_settle_pending_after_wallet_enabled(db):
    # 钱包关着时计提挂 pending（#175 的口径）
    _set(db, "wallet_enabled", "false")
    _set(db, "wallet_invite_rebate_mode", "flat")
    inviter = _user(db, "inviter-settle")
    invitee = _user(db, "invitee-settle")
    code = _code(db, inviter, "CODESET1")
    _relation(db, inviter, invitee, code)
    row = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-SET-1",
        paid_amount=Decimal("45"))
    db.commit()
    assert row.status == "pending"
    assert invite_rebate.settle_pending_rebates(db) == 0

    # 钱包开了：补结算把挂账一次入完
    _set(db, "wallet_enabled", "true")
    assert invite_rebate.settle_pending_rebates(db) == 1
    db.commit()
    row = _accruals(db, inviter.id)[0]
    assert row.status == "credited"
    assert row.wallet_ledger_id is not None
    assert wallet_service.get_balance(db, inviter.id) == Decimal("2.25")
    # 再次结算不再重复
    assert invite_rebate.settle_pending_rebates(db) == 0


def test_reversed_excluded_from_cap_and_tier(db):
    """已撤回的不计入上限与分档累计：退款后等于这笔付费没发生过。"""
    inviter = _user(db, "inviter-rev")
    invitee = _user(db, "invitee-rev")
    code = _code(db, inviter, "CODEREV1")
    _relation(db, inviter, invitee, code)

    # 第一笔 ¥400：350×5% + 50×1% = ¥18.00（默认 tiered）
    first = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-REV-1",
        paid_amount=Decimal("400"))
    db.commit()
    assert first.rebate_amount == Decimal("18.00")
    assert first.status == "credited"
    assert wallet_service.get_balance(db, inviter.id) == Decimal("18.00")

    # 退款撤回：上限额度与分档累计都应还回来
    reversed_amount, owed = orders_admin._reverse_rebate(db, "SO-REV-1", False)
    db.commit()
    assert reversed_amount == Decimal("18.00")
    assert owed is False
    assert _accruals(db, inviter.id)[0].status == "reversed"
    assert wallet_service.get_balance(db, inviter.id) == Decimal("0.00")
    assert invite_rebate._accrued_total(db, inviter.id) == Decimal("0")
    totals = invite_rebate.totals_for_inviter(db, inviter.id)
    assert totals["reversed"] == Decimal("18.00")
    assert totals["credited"] == Decimal("0")

    # 第二笔同样 ¥400：若 reversed 还占累计，只剩 1%（¥4.00）；
    # 排除后重新从阈值下爬，应仍是 ¥18.00
    second = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-REV-2",
        paid_amount=Decimal("400"))
    db.commit()
    assert second.rebate_amount == Decimal("18.00")
    assert invite_rebate._accrued_total(db, inviter.id) == Decimal("18.00")


# ==================== ④ 退款回钱包 ====================

def _invite_purchase(db):
    """邀请人拉新、被邀请人用钱包买订阅的完整现场：
    返回 (admin, inviter, invitee, order_id, accrual)。"""
    admin = _user(db, "refund-admin", is_staff=True)
    inviter = _user(db, "inviter-refund")
    invitee = _user(db, "invitee-refund")
    code = _code(db, inviter, "CODERFD1")
    _relation(db, inviter, invitee, code)
    wallet_service.credit(db, invitee.id, Decimal("100"), kind="recharge")
    db.commit()
    plan = _plan(db)
    result, _ = _pay(db, invitee, plan, "pay-refund-1")
    assert result["ok"] is True
    accrual = _accruals(db, inviter.id)[0]
    assert accrual.status == "credited"  # 返利 ¥2.25 已入邀请人钱包
    return admin, inviter, invitee, result["order_id"], accrual


def test_refund_subscription_returns_to_wallet(db):
    admin = _user(db, "refund-admin-1", is_staff=True)
    buyer = _user(db, "refund-buyer-1")
    wallet_service.credit(db, buyer.id, Decimal("100"), kind="recharge")
    db.commit()
    plan = _plan(db)
    result, _ = _pay(db, buyer, plan, "pay-refund-2")
    order_id = result["order_id"]
    assert wallet_service.get_balance(db, buyer.id) == Decimal("55.00")

    out = _refund(db, admin, order_id)

    assert out["success"] is True
    assert out["revoked_days"] == 30
    assert out["refund_to_wallet"] is True
    assert out["refund_amount"] == "45.00"
    assert out["rebate_reversal_failed"] is False
    # 钱回来了（55 + 45），订单已标 refunded、订阅已撤销
    assert wallet_service.get_balance(db, buyer.id) == Decimal("100.00")
    order = db.query(models.SubscriptionOrder).filter_by(
        order_id=order_id).one()
    assert order.status == "refunded"
    sub = db.query(models.UserSubscription).filter_by(
        id=order.subscription_id).one()
    assert sub.status == "cancelled"
    # 退款只入账一次：kind=refund 恰好一条
    assert (db.query(models.WalletLedger)
            .filter_by(user_id=buyer.id, kind="refund").count() == 1)

    # 重复退款被拒（端点本身 400），余额不再动
    with pytest.raises(HTTPException):
        _refund(db, admin, order_id)
    db.rollback()
    assert wallet_service.get_balance(db, buyer.id) == Decimal("100.00")


def test_refund_rebate_owed_then_retry(db):
    admin, inviter, invitee, order_id, accrual = _invite_purchase(db)
    # 邀请人把返利花光：退款时返利扣不回来，只能挂欠账
    wallet_service.debit(db, inviter.id, Decimal("2.25"),
                         kind="subscription_purchase",
                         idempotency_key="spend-rebate-1")
    db.commit()
    assert wallet_service.get_balance(db, inviter.id) == Decimal("0.00")

    out = _refund(db, admin, order_id)

    # 主退款照做（买家钱回钱包），返利欠着、如实上报
    assert out["success"] is True
    assert out["refund_to_wallet"] is True
    assert out["refund_amount"] == "45.00"
    assert out["rebate_reversed"] == 0
    assert out["rebate_reversal_failed"] is True
    assert wallet_service.get_balance(db, invitee.id) == Decimal("100.00")
    db.refresh(accrual)
    assert accrual.status == "credited"
    assert accrual.reversal_owed is True

    # 管理端欠账清单能看到这一条
    owed = admin_wallet.wallet_rebate_reversals(
        status="owed", current_admin=admin, db=db)
    assert len(owed["records"]) == 1
    assert owed["records"][0]["id"] == accrual.id

    # 余额仍不足时重试：不报错、如实返回失败，欠账还在
    retry = admin_wallet.wallet_retry_rebate_reversal(
        accrual.id, admin_wallet.RebateReversalRetryRequest(),
        admin, db)
    assert retry["success"] is False
    db.refresh(accrual)
    assert accrual.reversal_owed is True

    # 邀请人补钱后重试成功：计提行转 reversed、欠账清掉
    wallet_service.credit(db, inviter.id, Decimal("10"), kind="recharge")
    db.commit()
    retry = admin_wallet.wallet_retry_rebate_reversal(
        accrual.id, admin_wallet.RebateReversalRetryRequest(),
        admin, db)
    assert retry["success"] is True
    assert retry["reversed_amount"] == "2.25"
    db.refresh(accrual)
    assert accrual.status == "reversed"
    assert accrual.reversal_owed is False
    assert accrual.reversed_at is not None
    assert wallet_service.get_balance(db, inviter.id) == Decimal("7.75")


def test_refund_rebate_owed_then_waive(db):
    admin, inviter, invitee, order_id, accrual = _invite_purchase(db)
    wallet_service.debit(db, inviter.id, Decimal("2.25"),
                         kind="subscription_purchase",
                         idempotency_key="spend-rebate-2")
    db.commit()
    out = _refund(db, admin, order_id)
    assert out["rebate_reversal_failed"] is True
    db.refresh(accrual)
    assert accrual.reversal_owed is True

    # 豁免：欠账清掉、钱留在邀请人钱包（状态仍是 credited）
    waived = admin_wallet.wallet_waive_rebate_reversal(
        accrual.id, admin, db)
    assert waived["success"] is True
    assert waived["status"] == "waived"
    db.refresh(accrual)
    assert accrual.reversal_owed is False
    assert accrual.status == "credited"
    assert wallet_service.get_balance(db, inviter.id) == Decimal("0.00")

    # 已处理的欠账再豁免应 404
    with pytest.raises(HTTPException):
        admin_wallet.wallet_waive_rebate_reversal(accrual.id, admin, db)
    db.rollback()


# ==================== ⑤ 套餐接口的余额通道标识 ====================

def test_payment_plans_wallet_channel(db):
    plan = _plan(db)
    # 网关开关关着、钱包开着：套餐照常下发，余额通道标识为真
    _set(db, "subscription_purchase_enabled", "false")
    out = economy.payment_plans(db=db)
    assert out["enabled"] is False
    assert out["wallet_purchase_enabled"] is True
    assert [p["id"] for p in out["plans"]] == [plan.id]

    # 两条通道都关：整页空，前端显示「订阅购买暂未开启」
    _set(db, "wallet_enabled", "false")
    out = economy.payment_plans(db=db)
    assert out["enabled"] is False
    assert out["wallet_purchase_enabled"] is False
    assert out["plans"] == []
