"""订阅照友站重做专项测试（2026-11-14）。

覆盖：
- 按月售卖折算：三档现总价÷基准月数＝月价；年付＝11×月价；
  发放天数 1 月=30 天、年付=365 天；
- 钱包按月购买：订单 days_granted 按购买月数落账、订阅按它发放；
- 换套餐抵扣：公式＝旧订阅实付金额×剩余时长/总时长（含完整算例）；
  切换时旧行先到期、新行另起，同档续费不抵扣、走顺延；
- 自动续费：窗口内余额扣款续期、订单号去重、余额不足落 closed
  单＋通知、总闸关时不跑、用户侧开关端点（只能改自己的）。
"""
import os
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-subscription-redo-key-32chars")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auto_renew, entitlements as ent, models
from backend import wallet as wallet_service
from backend.api import economy, user as user_api
from backend.api import wallet as wallet_api
from backend.integrations import store


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


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _plan(db, name, price, days):
    p = models.SubscriptionPlan(
        name=name, price=Decimal(price), duration_days=days,
        realm_id=1, is_active=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _topup(db, user, amount):
    wallet_service.credit(db, user.id, Decimal(amount), kind="recharge")
    db.commit()


def _pay(db, user, plan, key, months=0):
    return wallet_api._pay_subscription_core(
        db, user,
        wallet_api.WalletPaySubscriptionRequest(
            plan_id=plan.id, months=months, idempotency_key=key))


# ==================== 按月折算 ====================

def test_monthly_price_conversion_table(db):
    """折算表（生产三档口径）：启程 ¥18/30 天、远行 ¥45/90 天、
    环游 ¥150/365 天 → 月价 ¥18 / ¥15 / ¥12.5。"""
    qicheng = _plan(db, "启程", "18", 30)
    yuanxing = _plan(db, "远行", "45", 90)
    huanyou = _plan(db, "环游", "150", 365)
    assert economy.plan_base_months(qicheng) == 1
    assert economy.plan_base_months(yuanxing) == 3
    assert economy.plan_base_months(huanyou) == 12
    assert economy.plan_monthly_price(qicheng) == Decimal("18.00")
    assert economy.plan_monthly_price(yuanxing) == Decimal("15.00")
    assert economy.plan_monthly_price(huanyou) == Decimal("12.50")
    # 3/6 个月无折扣；仅年付＝11×月价
    assert economy.months_list_price(yuanxing, 3) == Decimal("45.00")
    assert economy.months_list_price(yuanxing, 6) == Decimal("90.00")
    assert economy.months_list_price(yuanxing, 12) == Decimal("165.00")
    assert economy.months_list_price(huanyou, 12) == Decimal("137.50")
    assert economy.months_list_price(qicheng, 1) == Decimal("18.00")
    # 发放天数：1 月 30 天、3 月 90 天、年付 365 天
    assert economy.months_grant_days(1) == 30
    assert economy.months_grant_days(3) == 90
    assert economy.months_grant_days(12) == 365


def test_wallet_pay_by_months(db):
    u = _user(db, "months-buyer")
    _topup(db, u, "200")
    yuanxing = _plan(db, "远行", "45", 90)
    result, _ = _pay(db, u, yuanxing, "m-12", months=12)
    assert result["paid_amount"] == "165.00"  # 年付 15×11
    assert result["months"] == 12
    order = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBm-12").one()
    assert order.days_granted == 365
    sub = db.query(models.UserSubscription).filter_by(
        id=order.subscription_id).one()
    assert (sub.end_date - sub.start_date).days == 365
    # 再买 1 个月（同档续费顺延）：¥15、+30 天
    result2, _ = _pay(db, u, yuanxing, "m-1", months=1)
    assert result2["paid_amount"] == "15.00"
    assert result2["switch_type"] == "renew"
    db.refresh(sub)
    assert (sub.end_date - sub.start_date).days == 395


# ==================== 换套餐抵扣 ====================

def test_switch_credit_full_example(db):
    """完整算例：远行 ¥45 买 3 个月（总时长 90 天），用了 30 天后
    （剩 60 天）切环游买 1 个月（¥12.50）：
    抵扣 = 45 × 60/90 = ¥30.00；应付 = max(0, 12.50 − 30.00) = ¥0，
    不扣款；旧行到期、新行另起 30 天。"""
    u = _user(db, "switcher")
    _topup(db, u, "100")
    yuanxing = _plan(db, "远行", "45", 90)
    huanyou = _plan(db, "环游", "150", 365)
    result, _ = _pay(db, u, yuanxing, "sw-first", months=3)
    assert result["paid_amount"] == "45.00"
    order1 = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBsw-first").one()
    old_sub = db.query(models.UserSubscription).filter_by(
        id=order1.subscription_id).one()
    # 把时钟拨到用了 30 天：总时长仍 90 天、剩余恰 60 天
    now = datetime.now()
    old_sub.start_date = now - timedelta(days=30)
    old_sub.end_date = now + timedelta(days=60)
    db.commit()

    result2, _ = _pay(db, u, huanyou, "sw-second", months=1)
    assert result2["switch_type"] == "switch"
    assert result2["switch_credit"] == "30.00"
    assert result2["paid_amount"] == "0.00"
    # 一分钱没扣：余额仍是充值 100 − 首购 45
    assert wallet_service.get_balance(db, u.id) == Decimal("55.00")
    order2 = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBsw-second").one()
    assert order2.list_price == Decimal("12.50")
    assert order2.discount_amount == Decimal("30.00")
    assert order2.amount == Decimal("0.00")
    assert order2.days_granted == 30
    # 旧行已到期（end<=now），新行是环游、另起 30 天
    db.refresh(old_sub)
    assert old_sub.end_date <= datetime.now()
    new_sub = db.query(models.UserSubscription).filter_by(
        id=order2.subscription_id).one()
    assert new_sub.id != old_sub.id
    assert new_sub.plan_id == huanyou.id
    assert (new_sub.end_date - new_sub.start_date).days == 30


def test_switch_credit_partial_payable(db):
    """抵扣小于新价时只扣差额：启程实付 ¥18、剩 15/30 天 →
    抵扣 18×15/30＝¥9.00；切环游年付 ¥137.50 → 实付 ¥128.50。"""
    u = _user(db, "switcher-2")
    _topup(db, u, "200")
    qicheng = _plan(db, "启程", "18", 30)
    huanyou = _plan(db, "环游", "150", 365)
    _pay(db, u, qicheng, "sw2-first", months=1)
    order1 = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBsw2-first").one()
    old_sub = db.query(models.UserSubscription).filter_by(
        id=order1.subscription_id).one()
    now = datetime.now()
    old_sub.start_date = now - timedelta(days=15)
    old_sub.end_date = now + timedelta(days=15)
    db.commit()

    result, _ = _pay(db, u, huanyou, "sw2-second", months=12)
    assert result["switch_credit"] == "9.00"
    assert result["paid_amount"] == "128.50"
    assert wallet_service.get_balance(db, u.id) == Decimal("53.50")
    order2 = db.query(models.SubscriptionOrder).filter_by(
        order_id="WBsw2-second").one()
    assert order2.days_granted == 365


def test_switch_credit_fallback_without_order(db):
    """旧订阅无订单（兑换码/管理员发放）：实付按旧套餐现标价回退。
    旧行总 90 天、剩 45 天、旧套餐价 ¥45 → 抵扣 45×45/90＝¥22.50。"""
    u = _user(db, "switcher-3")
    _topup(db, u, "100")
    yuanxing = _plan(db, "远行", "45", 90)
    huanyou = _plan(db, "环游", "150", 365)
    now = datetime.now()
    db.add(models.UserSubscription(
        user_id=u.id, plan_id=yuanxing.id, realm_id=1,
        start_date=now - timedelta(days=45),
        end_date=now + timedelta(days=45), status="active"))
    db.commit()
    credit, kind, _old = economy._switch_credit(db, u, huanyou)
    assert kind == "switch"
    assert credit == Decimal("22.50")


def test_quote_endpoint_months_and_credit(db):
    """试算口带 months：与下单同一计价，抵扣字段齐全。"""
    u = _user(db, "quoter")
    huanyou = _plan(db, "环游", "150", 365)
    result = economy.coupon_quote(
        economy.CouponQuoteRequest(
            code="", kind="subscription", item_id=huanyou.id, months=12),
        current_user=u, db=db)
    assert result["list_price"] == 137.50
    assert result["monthly_price"] == 12.50
    assert result["months"] == 12
    assert result["switch_type"] == "new"
    assert result["switch_credit"] == 0.0
    assert result["payable_amount"] == 137.50


# ==================== 自动续费 ====================

def _renewable_sub(db, user, plan, *, balance, days_granted=90,
                   ends_in_hours=12):
    """一笔开了自动续费、上次买了 days_granted 天、即将到期的订阅。"""
    _topup(db, user, balance)
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=1,
        start_date=now - timedelta(days=90),
        end_date=now + timedelta(hours=ends_in_hours),
        status="active", auto_renew=True)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    db.add(models.SubscriptionOrder(
        order_id=f"SEED{sub.id}", user_id=user.id, plan_id=plan.id,
        item_name=plan.name, amount=Decimal("45"), list_price=Decimal("45"),
        discount_amount=0, days_granted=days_granted,
        payment_method="wallet", status="paid", subscription_id=sub.id))
    db.commit()
    return sub


def test_auto_renew_success_and_dedupe(db):
    u = _user(db, "renewer")
    yuanxing = _plan(db, "远行", "45", 90)
    sub = _renewable_sub(db, u, yuanxing, balance="100")
    old_end = sub.end_date

    result = auto_renew.process_auto_renewals(db)
    assert result["renewed"] == 1
    assert result["failed"] == 0
    # 按上次时长（90 天＝3 个月，月价 ¥15）扣款 ¥45
    assert wallet_service.get_balance(db, u.id) == Decimal("55.00")
    order = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id.like("AR%")).one()
    assert order.payment_method == "auto_renew"
    assert order.status == "paid"
    assert order.amount == Decimal("45")
    assert order.days_granted == 90
    db.refresh(sub)
    assert sub.end_date == old_end + timedelta(days=90)
    # 再跑一轮：已续期不在窗口内、且订单号去重，不重复扣
    result2 = auto_renew.process_auto_renewals(db)
    assert result2["renewed"] == 0
    assert wallet_service.get_balance(db, u.id) == Decimal("55.00")
    assert db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id.like("AR%")).count() == 1


def test_auto_renew_insufficient_balance(db):
    u = _user(db, "renewer-poor")
    yuanxing = _plan(db, "远行", "45", 90)
    sub = _renewable_sub(db, u, yuanxing, balance="10")
    old_end = sub.end_date

    result = auto_renew.process_auto_renewals(db)
    assert result["renewed"] == 0
    assert result["failed"] == 1
    # 不扣款、订阅不动、开关保持开；落一笔 closed 单 + 一条通知
    assert wallet_service.get_balance(db, u.id) == Decimal("10.00")
    db.refresh(sub)
    assert sub.end_date == old_end
    assert sub.auto_renew is True
    order = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id.like("AR%")).one()
    assert order.status == "closed"
    assert len(result["pending_notifications"]) == 1
    # 再跑一轮不重复落单
    auto_renew.process_auto_renewals(db)
    assert db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id.like("AR%")).count() == 1


def test_auto_renew_master_switch_off(db):
    u = _user(db, "renewer-off")
    yuanxing = _plan(db, "远行", "45", 90)
    _renewable_sub(db, u, yuanxing, balance="100")
    row = models.SystemConfig(key="wallet_auto_renew_enabled",
                              value="false")
    db.add(row)
    db.commit()
    store.invalidate("wallet_auto_renew_enabled")
    result = auto_renew.process_auto_renewals(db)
    assert result["renewed"] == 0 and result["failed"] == 0
    assert wallet_service.get_balance(db, u.id) == Decimal("100.00")


def test_auto_renew_toggle_endpoint(db):
    u = _user(db, "toggler")
    other = _user(db, "toggler-other")
    yuanxing = _plan(db, "远行", "45", 90)
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=u.id, plan_id=yuanxing.id, realm_id=1,
        start_date=now, end_date=now + timedelta(days=30),
        status="active", auto_renew=False)
    db.add(sub)
    db.commit()
    db.refresh(sub)

    out = user_api.set_my_subscription_auto_renew(
        sub.id, user_api.AutoRenewRequest(enabled=True),
        current_user=u, db=db)
    assert out["auto_renew"] is True
    db.refresh(sub)
    assert sub.auto_renew is True
    out2 = user_api.set_my_subscription_auto_renew(
        sub.id, user_api.AutoRenewRequest(enabled=False),
        current_user=u, db=db)
    assert out2["auto_renew"] is False
    # 别人的订阅改不动
    with pytest.raises(HTTPException) as ei:
        user_api.set_my_subscription_auto_renew(
            sub.id, user_api.AutoRenewRequest(enabled=True),
            current_user=other, db=db)
    assert ei.value.status_code == 404
