"""r33 后端完美迭代：充值/订单域 4 项修复。

F1: （已随等级体系于 2026-11-14 删除：履约不再发经验、退款无经验可撤，
    原 add_xp 负增量与 _reverse_xp 测试一并摘除。）
F2: 自定义金额充值带优惠码从「静默忽略」改为明确 400。
F3: 积分转账每日上限的日界线改上海自然日（UTC 入库帧对齐）。
F4: 关单/退款时把兑换码折扣权益还回未用（与优惠券释放同一道理）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import economy, orders_admin
from backend.api.admin_core import get_current_admin
from backend.api.user import get_current_user
from backend.database import get_db


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


def _make_user(db, username="u1", points=1000):
    u = models.WebUser(username=username, password_hash="", points=points,
                       is_active=True, member_xp=0, member_level=1)
    db.add(u)
    db.commit()
    return u


def _set_cfg(db, key, value):
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


# ---------------- F4: 折扣权益还回 ----------------

def _make_credit(db, user_id, order_id):
    c = models.ExchangeDiscountCredit(
        user_id=user_id, exchange_code_id=None, discount_pct=85,
        status="used", used_order_id=order_id)
    db.add(c)
    db.commit()
    return c


def test_restore_discount_credit(db):
    u = _make_user(db)
    _make_credit(db, u.id, "RB7")
    assert orders_admin._restore_discount_credit(db, "RB7") is True
    c = db.query(models.ExchangeDiscountCredit).filter(
        models.ExchangeDiscountCredit.user_id == u.id).first()
    assert c.status == "unused"
    assert c.used_order_id is None
    # 幂等：第二次无可用行
    assert orders_admin._restore_discount_credit(db, "RB7") is False


def _admin_app(db):
    app = FastAPI()
    app.include_router(orders_admin.admin_orders_router)
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=999)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_close_order_restores_credit(db):
    u = _make_user(db)
    order = models.RechargeOrder(
        order_id="RB8", user_id=u.id, package_id=0, amount=12,
        price=10, payment_method="alipay", status="pending")
    db.add(order)
    _make_credit(db, u.id, "RB8")
    db.commit()

    resp = _admin_app(db).post("/api/admin/economy/orders/RB8/close")
    assert resp.status_code == 200, resp.text
    db.expire_all()
    c = db.query(models.ExchangeDiscountCredit).filter(
        models.ExchangeDiscountCredit.user_id == u.id).first()
    assert c.status == "unused"
    assert c.used_order_id is None
    o = db.query(models.RechargeOrder).filter(
        models.RechargeOrder.order_id == "RB8").first()
    assert o.status == "closed"


# ---------------- F2: 自定义充值 + 优惠码 ----------------

def _user_app(db, user):
    app = FastAPI()
    app.include_router(economy.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _gateway_cfg(db):
    _set_cfg(db, "payment_gateway_url", "https://pay.example.com")
    _set_cfg(db, "payment_partner_id", "10001")
    _set_cfg(db, "payment_partner_key", "testkey")
    _set_cfg(db, "site_url", "https://app.example.com")


def test_custom_recharge_with_coupon_400(db):
    # 能量充值已随货币下线（2026-11-13）：下单端点只接受 subscription，
    # recharge 不再建单（带不带优惠券都一样）。
    u = _make_user(db)
    _gateway_cfg(db)
    resp = _user_app(db, u).post(
        "/api/user/economy/payment/order",
        json={"kind": "recharge", "custom_amount": 10.0, "coupon_code": "ABC123"})
    assert resp.status_code == 400, resp.text
    assert "kind 必须是 subscription" in resp.json()["detail"]


def test_custom_recharge_without_coupon_ok(db):
    # 同上：不带优惠券的自定义充值同样被拒，不再出支付链接。
    u = _make_user(db)
    _gateway_cfg(db)
    resp = _user_app(db, u).post(
        "/api/user/economy/payment/order",
        json={"kind": "recharge", "custom_amount": 10.0})
    assert resp.status_code == 400, resp.text
    assert "kind 必须是 subscription" in resp.json()["detail"]


# ---------------- F3: 上海口径日界线 ----------------

def test_today_start_utc_naive_frame():
    ts = economy._today_start_utc_naive()
    assert ts.tzinfo is None
    sh = ts.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    assert (sh.hour, sh.minute, sh.second, sh.microsecond) == (0, 0, 0, 0)


def test_today_start_utc_naive_is_shanghai_midnight():
    # 与「上海自然日 00:00」是同一时刻（容差 1 天内的表示差异）
    ts = economy._today_start_utc_naive()
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0)
    expected = now_sh.astimezone(timezone.utc).replace(tzinfo=None)
    assert ts == expected
