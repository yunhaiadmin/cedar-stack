# tests/test_subscription_records.py
"""订阅记录端点（GET /api/user/subscriptions/records）回归测试。

口径（2026-11-13「我的」页对齐友站新增）：
- 购买订单 + 无订单的订阅发放（兑换码/管理员授予）合并成一条时间线；
- 订单行展示下单时写下的 item_name 快照原文（套餐改名后历史不跟着变）；
- 已被订单履约的订阅行不重复出现；
- 发放行金额为 None、状态按 end_date 现算（与 GET /subscriptions 同口径）；
- 只见自己的记录。
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api.user import get_my_subscription_records


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture()
def db():
    engine = _engine()
    models.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    session = SessionLocal()
    from backend.integrations import store

    store.invalidate()
    try:
        yield session
    finally:
        session.close()
        store.invalidate()


def _user(db, username: str) -> models.WebUser:
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_records_merge_orders_and_grants(db):
    tag = uuid.uuid4().hex[:8]
    me = _user(db, f"rec-me-{tag}")
    other = _user(db, f"rec-other-{tag}")
    # 套餐现名已改成「远行」，订单快照里存的仍是下单时的「远行执照」
    plan = models.SubscriptionPlan(
        name="远行", price=100, duration_days=30, is_active=True
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    now = datetime.now()
    # 订单履约出的订阅行（不应在发放行里重复出现）
    sub_from_order = models.UserSubscription(
        user_id=me.id, plan_id=plan.id,
        start_date=now - timedelta(days=2), end_date=now + timedelta(days=28),
        status="active", created_at=now - timedelta(days=2),
    )
    # 无订单的发放行（兑换码授予）：生效中，30 天
    sub_grant = models.UserSubscription(
        user_id=me.id, plan_id=plan.id,
        start_date=now - timedelta(days=1), end_date=now + timedelta(days=29),
        status="active", created_at=now - timedelta(hours=1),
    )
    db.add_all([sub_from_order, sub_grant])
    db.commit()
    db.refresh(sub_from_order)
    db.refresh(sub_grant)

    db.add_all([
        models.SubscriptionOrder(
            order_id=f"SO-PAID-{tag}", user_id=me.id, plan_id=plan.id,
            item_name="远行执照", amount=88, status="paid",
            subscription_id=sub_from_order.id, days_granted=30,
            created_at=now - timedelta(days=2),
        ),
        models.SubscriptionOrder(
            order_id=f"SO-PEND-{tag}", user_id=me.id, plan_id=plan.id,
            item_name="启程", amount=30, status="pending",
            subscription_id=None, days_granted=0,
            created_at=now - timedelta(days=3),
        ),
        # 别人的订单不许串进来
        models.SubscriptionOrder(
            order_id=f"SO-OTHER-{tag}", user_id=other.id, plan_id=plan.id,
            item_name="环游", amount=200, status="paid",
            subscription_id=None, days_granted=30,
            created_at=now,
        ),
    ])
    db.commit()

    out = get_my_subscription_records(current_user=me, db=db)
    records = out["records"]

    assert len(records) == 3
    # 时间线倒序：最新的是发放行（1 小时前）
    assert records[0]["kind"] == "grant"
    assert records[0]["status"] == "active"
    assert records[0]["days"] == 30
    assert records[0]["amount"] is None
    # 发放行的到期 = 订阅本身的 end_date
    assert records[0]["expires_at"].startswith(
        (now + timedelta(days=29)).date().isoformat())
    # 订单行用快照原名，不回查套餐现名
    paid = next(r for r in records if r["status"] == "paid")
    assert paid["kind"] == "order"
    assert paid["name"] == "远行执照"
    assert paid["days"] == 30
    assert paid["amount"] == 88.0
    # 订单行的到期 = 它履约出的订阅的 end_date
    assert paid["expires_at"].startswith(
        (now + timedelta(days=28)).date().isoformat())
    # 待支付订单 days_granted=0 → None（还没发放，不许编时长）
    pend = next(r for r in records if r["status"] == "pending")
    assert pend["days"] is None
    # 待支付还没履约 → 没有到期时间
    assert pend["expires_at"] is None


def test_records_empty_for_new_user(db):
    tag = uuid.uuid4().hex[:8]
    me = _user(db, f"rec-empty-{tag}")
    out = get_my_subscription_records(current_user=me, db=db)
    assert out == {"records": []}
