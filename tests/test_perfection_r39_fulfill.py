"""r39 后端完美迭代：履约副作用失败不再静默。

F1: _fulfill_order 里会员经验累加 / 邀请返利失败原来只记一行日志就提交，
    「钱收了、经验/返利没到」用户无从知晓。现改为：失败时往 pending 里追加
    一条 economy.fulfill_partial 站内信（提交后由 send_fulfill_notifications
    发出），受影响的用户凭此联系客服/服主补发。

能量充值已随货币于 2026-11-13 下线：充值单履约不再自动发能量，
直接抛 FulfillmentError 转人工处理（见下三处充值测试），
订阅履约的经验失败通知机制仍在。

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


def _make_user(db, username, points=0):
    u = models.WebUser(username=username, password_hash="x", points=points,
                       is_active=True, member_xp=0, member_level=1)
    db.add(u)
    db.commit()
    return u


def _make_recharge_order(db, user, order_id="R39T1", amount=100, price=10):
    o = models.RechargeOrder(order_id=order_id, user_id=user.id, package_id=1,
                             amount=amount, price=price, status="pending")
    db.add(o)
    db.commit()
    return o


def _partial_items(pending):
    return [p for p in pending
            if p.get("event_type") == AdminEvent.FULFILL_PARTIAL]


def test_recharge_xp_failure_not_silent(db, monkeypatch):
    """能量充值已下线：充值单履约直接显性失败，不再自动发能量。

    经验累加（已随等级体系删除）/返利路径根本走不到——订单保持
    pending、不发任何通知，由人工补单通道处理，不静默吞单。
    """
    u = _make_user(db, "r39xp")
    o = _make_recharge_order(db, u)

    with pytest.raises(economy.FulfillmentError, match="能量充值已下线"):
        economy._fulfill_order(db, recharge_order=o)

    db.refresh(o)
    db.refresh(u)
    assert o.status == "pending"   # 未履约，不标 paid
    assert u.points == 0           # 能量不发
    assert u.member_xp == 0


def test_recharge_rebate_failure_notifies_inviter(db, monkeypatch):
    """能量充值已下线：充值单履约在返利之前就显性失败。

    邀请返利入口（apply_rebate）已随货币删除移除，返利根本不会被
    触发；订单保持 pending 转人工处理。
    """
    inviter = _make_user(db, "r39inviter")
    invitee = _make_user(db, "r39invitee")
    code = models.InvitationCode(code="R39CODE", user_id=inviter.id,
                                 reward_points=0)
    db.add(code)
    db.flush()
    db.add(models.InvitationRecord(inviter_id=inviter.id,
                                   invitee_id=invitee.id,
                                   code_id=code.id))
    db.commit()
    o = _make_recharge_order(db, invitee, order_id="R39T2")

    with pytest.raises(economy.FulfillmentError,
                       match="能量充值已下线"):
        economy._fulfill_order(db, recharge_order=o)

    db.refresh(o)
    assert o.status == "pending"
    db.refresh(inviter)
    assert inviter.points == 0  # 返利未发


def test_recharge_happy_path_no_partial_message(db):
    """能量充值已下线：即使「正常」充值单也不再自动履约。

    报错文案须点名订单号，方便人工补单通道定位；订单保持 pending，
    不发能量、不产生任何通知（无 fulfill_partial 噪音）。
    """
    u = _make_user(db, "r39ok")
    o = _make_recharge_order(db, u, order_id="R39T3")

    with pytest.raises(economy.FulfillmentError) as excinfo:
        economy._fulfill_order(db, recharge_order=o)
    assert "R39T3" in str(excinfo.value)

    db.refresh(o)
    db.refresh(u)
    assert o.status == "pending"
    assert u.points == 0
    assert u.member_xp == 0


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
    db.refresh(u)
    assert (u.member_xp or 0) == 0
