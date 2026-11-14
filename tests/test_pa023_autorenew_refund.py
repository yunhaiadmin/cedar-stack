"""PA-023 回归：自动续订订单退款的天数回滚口径。

背景（审计 R10 实证）：自动续订单退款曾漏退能量，积分腿修复后又随
货币于 2026-11-13 整体下线；points_logs 台账与 WebUser.points 列已于
2026-11-14 全清，退款不再有任何积分腿——revoked_points 出参保留、
恒 0。本文件走真实退款端点验证现行口径：

1. 自动续订单退款 → 天数回滚 30 天 + 订阅撤销，revoked_points 恒 0；
2. 历史积分支付单（payment_method='points'）退款行为一致；
3. 无台账续订单与现金单退款同样只回滚天数。
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.models import Base
from backend.api import orders_admin


@pytest.fixture
def db():
    # 退款端点把冲正下放线程池执行，内存库必须跨线程共用同一连接
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _mute_notify(monkeypatch):
    """退款成功后的站内通知与本单无关，静音避免触碰真实通知通道。"""

    async def _noop(*args, **kwargs):
        return True

    monkeypatch.setattr(
        "backend.notifications.notify_admin_event_quiet", _noop)


def _admin(db):
    admin = models.WebUser(username="pa023-admin", password_hash="x",
                           is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _user_with_sub(db, username):
    user = models.WebUser(username=username, password_hash="x",
                          is_active=True)
    db.add(user)
    plan = models.SubscriptionPlan(name="远行", price=100, duration_days=30,
                                   realm_id=None)
    db.add(plan)
    db.commit()
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=None,
        start_date=now, end_date=now + timedelta(days=30), status="active")
    db.add(sub)
    db.commit()
    db.refresh(user)
    return user, plan, sub, now


def _refund(db, admin, order_id):
    return asyncio.run(orders_admin.refund_order(
        order_id=order_id,
        request=orders_admin.RefundRequest(reason="PA-023 回归测试"),
        current_admin=admin,
        db=db,
    ))


def test_auto_renew_order_refund_returns_points(db):
    """自动续订单退款：天数回滚 30 天；积分腿已随货币下线不再退回。

    revoked_points 出参保留恒 0。退款口径如需重定（例如按人民币
    原路退），由 parent 另行拍板——本测试只钉住当前行为。
    """
    admin = _admin(db)
    user, plan, sub, now = _user_with_sub(db, "pa023-renew")
    order = models.SubscriptionOrder(
        order_id="AR-PA023-1", user_id=user.id, plan_id=plan.id,
        item_name="远行", amount=plan.price, payment_method="auto_renew",
        status="paid", paid_at=now, subscription_id=sub.id, days_granted=30)
    db.add(order)
    db.commit()

    result = _refund(db, admin, "AR-PA023-1")

    assert result["revoked_days"] == 30
    assert result["revoked_points"] == 0  # 积分腿已下线，不退回
    db.expire_all()
    sub2 = db.query(models.UserSubscription).filter_by(id=sub.id).first()
    assert sub2.end_date <= now + timedelta(seconds=5)
    assert sub2.status == "cancelled"
    order2 = db.query(models.SubscriptionOrder).filter_by(
        order_id="AR-PA023-1").first()
    assert order2.status == "refunded"


def test_manual_points_order_refund_unchanged(db):
    """手动积分单退款：天数回滚 30 天；积分腿已随货币下线不再退回。

    与自动续订单同桩：实付 500 能量不退回、余额不动。口径重定前，
    本测试只钉住当前桩行为。
    """
    admin = _admin(db)
    user, plan, sub, now = _user_with_sub(db, "pa023-manual")
    order = models.SubscriptionOrder(
        order_id="PT-PA023-1", user_id=user.id, plan_id=plan.id,
        item_name="远行", amount=plan.price, payment_method="points",
        status="paid", paid_at=now, subscription_id=sub.id, days_granted=30)
    db.add(order)
    db.commit()

    result = _refund(db, admin, "PT-PA023-1")

    assert result["revoked_days"] == 30
    assert result["revoked_points"] == 0  # 积分腿已下线，不退回


def test_auto_renew_refund_without_ledger_returns_no_points(db):
    """续订单查不到扣款台账：只回滚天数，不凭空退能量。"""
    admin = _admin(db)
    user, plan, sub, now = _user_with_sub(db, "pa023-nolog")
    order = models.SubscriptionOrder(
        order_id="AR-PA023-2", user_id=user.id, plan_id=plan.id,
        item_name="远行", amount=plan.price, payment_method="auto_renew",
        status="paid", paid_at=now, subscription_id=sub.id, days_granted=30)
    db.add(order)
    db.commit()

    result = _refund(db, admin, "AR-PA023-2")

    assert result["revoked_days"] == 30
    assert result["revoked_points"] == 0


def test_cash_order_refund_does_not_touch_points(db):
    """现金订阅单退款：只回滚天数，能量余额不动。"""
    admin = _admin(db)
    user, plan, sub, now = _user_with_sub(db, "pa023-cash")
    order = models.SubscriptionOrder(
        order_id="CS-PA023-1", user_id=user.id, plan_id=plan.id,
        item_name="远行", amount=plan.price, payment_method="alipay",
        status="paid", paid_at=now, subscription_id=sub.id, days_granted=30)
    db.add(order)
    db.commit()

    result = _refund(db, admin, "CS-PA023-1")

    assert result["revoked_days"] == 30
    assert result["revoked_points"] == 0
