"""r39 后端完美迭代：履约副作用失败不再静默。

F1: _fulfill_order 里邀请返利等副作用失败原来只记一行日志就提交，
    「钱收了、返利没到」用户无从知晓。现改为：失败时往 pending 里追加
    一条 economy.fulfill_partial 站内信（提交后由 send_fulfill_notifications
    发出），受影响的用户凭此联系客服/服主补发。

能量充值单（RechargeOrder）与等级经验腿均已删除：_fulfill_order 只剩
订阅单一路，原充值单三处用例随表删除一并摘除。

全部用隔离的内存 SQLite，不碰生产库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import economy
from backend.notifications import AdminEvent


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _make_user(db, username):
    u = models.WebUser(username=username, password_hash="x",
                       is_active=True)
    db.add(u)
    db.commit()
    return u


def _partial_items(pending):
    return [p for p in pending
            if p.get("event_type") == AdminEvent.FULFILL_PARTIAL]


def test_subscription_fulfillment_has_no_xp_leg(db, monkeypatch):
    """订阅履约不再有经验腿（等级体系已删除）：履约成功、订阅到账，
    不产生「经验发放失败」类 FULFILL_PARTIAL 通知。"""
    u = _make_user(db, "r39sub")
    plan = models.SubscriptionPlan(name="月卡", price=30, duration_days=30)
    db.add(plan)
    db.commit()
    o = models.SubscriptionOrder(order_id="R39S1", user_id=u.id,
                                 plan_id=plan.id, amount=30, status="pending")
    db.add(o)
    db.commit()

    pending = economy._fulfill_order(db, subscription_order=o)
    db.commit()

    db.refresh(o)
    assert o.status == "paid"
    assert _partial_items(pending) == []
    assert any(p.get("event_type") == "economy.subscription_success"
               for p in pending)
