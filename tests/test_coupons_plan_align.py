"""优惠券对齐现行套餐体系（2026-11-19）回归

背景：现行在售套餐只有 Global 国际版 $1.20 / 家庭版 $3.60（美元），
优惠券唯一消费路径是订阅购买；在线充值未落地，recharge 券无处可用。
本单四件事的回归锚点：

- 固定减免支持到分：fixed value=0.5 能建券、落库为 Decimal('0.50')、
  compute 对 $1.20 套餐算出减 0.50 / 实付 0.70；
- 限定套餐：plan_id 券只能用在指定套餐的 quote 上，错套餐 400；
- 适用范围收口：create kind=recharge 直接 400（下线文案）；
  存量 recharge 券用于订阅购买仍被拒；
- 币种文案：min_amount 门槛报错是美元口径（$），不再出现「元」；
- 改券 plan_id：传值设定、传 null 清除、未传保留（model_fields_set 口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import coupons, models
from backend.api.coupons_admin import (
    CouponCreateRequest,
    CouponUpdateRequest,
    create_coupons,
    serialize,
    update_coupon,
)


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


def _user(db, username, is_staff=False):
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       is_staff=is_staff)
    db.add(u)
    db.commit()
    return u


def _plan(db, name, price):
    p = models.SubscriptionPlan(name=name, price=Decimal(price),
                                duration_days=30, is_active=True)
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# 固定减免支持到分
# ---------------------------------------------------------------------------

def test_fixed_fractional_discount_create_and_compute(db):
    admin = _user(db, "admin", is_staff=True)
    plan = _plan(db, "Global 国际版", "1.20")

    resp = create_coupons(
        CouponCreateRequest(code="HALF", kind="subscription",
                            discount_type="fixed", value=0.5),
        current_admin=admin, db=db)
    assert resp["success"] is True
    assert resp["coupons"][0]["value"] == 0.5

    row = db.query(models.CouponCode).filter_by(code="HALF").one()
    assert row.value == Decimal("0.50")

    discount, paid = coupons.compute(Decimal("1.20"), row)
    assert discount == Decimal("0.50")
    assert paid == Decimal("0.70")

    # quote 同口径：$1.20 的套餐用这张券，实付 $0.70
    buyer = _user(db, "buyer")
    result = coupons.quote(db, user=buyer, code="HALF",
                           kind="subscription", item_id=plan.id)
    assert result["discount_amount"] == 0.5
    assert result["paid_amount"] == 0.7
    assert result["value"] == 0.5


def test_percent_value_stays_integer(db):
    admin = _user(db, "admin", is_staff=True)
    resp = create_coupons(
        CouponCreateRequest(code="PCT85", kind="subscription",
                            discount_type="percent", value=85),
        current_admin=admin, db=db)
    assert resp["coupons"][0]["value"] == 85
    row = db.query(models.CouponCode).filter_by(code="PCT85").one()
    assert serialize(row)["value"] == 85
    discount, paid = coupons.compute(Decimal("3.60"), row)
    assert discount == Decimal("0.54")
    assert paid == Decimal("3.06")


def test_fixed_discount_must_be_positive_usd(db):
    admin = _user(db, "admin", is_staff=True)
    with pytest.raises(HTTPException) as excinfo:
        create_coupons(
            CouponCreateRequest(code="ZERO", kind="subscription",
                                discount_type="fixed", value=0),
            current_admin=admin, db=db)
    assert excinfo.value.status_code == 400
    assert "美元" in excinfo.value.detail


# ---------------------------------------------------------------------------
# 限定套餐
# ---------------------------------------------------------------------------

def test_plan_limited_coupon_quote(db):
    admin = _user(db, "admin", is_staff=True)
    buyer = _user(db, "buyer")
    plan_a = _plan(db, "Global 国际版", "1.20")
    plan_b = _plan(db, "Global 家庭版", "3.60")

    resp = create_coupons(
        CouponCreateRequest(code="ONLYA", kind="subscription",
                            discount_type="percent", value=50,
                            plan_id=plan_a.id),
        current_admin=admin, db=db)
    assert resp["coupons"][0]["plan_id"] == plan_a.id
    assert resp["coupons"][0]["plan_name"] == "Global 国际版"

    # 对的套餐：五折，$1.20 → 实付 $0.60
    result = coupons.quote(db, user=buyer, code="ONLYA",
                           kind="subscription", item_id=plan_a.id)
    assert result["valid"] is True
    assert result["paid_amount"] == 0.6

    # 错的套餐：400 并讲清是套餐限定
    with pytest.raises(HTTPException) as excinfo:
        coupons.quote(db, user=buyer, code="ONLYA",
                      kind="subscription", item_id=plan_b.id)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "该优惠券仅适用于指定套餐"


def test_unlimited_coupon_works_on_any_plan(db):
    admin = _user(db, "admin", is_staff=True)
    buyer = _user(db, "buyer")
    plan_b = _plan(db, "Global 家庭版", "3.60")
    create_coupons(
        CouponCreateRequest(code="ANYPLAN", kind="subscription",
                            discount_type="percent", value=90),
        current_admin=admin, db=db)
    result = coupons.quote(db, user=buyer, code="ANYPLAN",
                           kind="subscription", item_id=plan_b.id)
    assert result["paid_amount"] == 3.24


def test_update_plan_limit_set_clear_and_keep(db):
    admin = _user(db, "admin", is_staff=True)
    plan = _plan(db, "Global 国际版", "1.20")
    create_coupons(
        CouponCreateRequest(code="EDITME", kind="subscription",
                            discount_type="percent", value=90),
        current_admin=admin, db=db)
    row = db.query(models.CouponCode).filter_by(code="EDITME").one()
    assert row.plan_id is None

    # 传值 → 设定限定
    resp = update_coupon(row.id, CouponUpdateRequest(plan_id=plan.id),
                         current_admin=admin, db=db)
    assert resp["coupon"]["plan_id"] == plan.id

    # 未传 plan_id → 保留原限定（改别的字段不能顺手清掉）
    resp = update_coupon(row.id, CouponUpdateRequest(note="改备注"),
                         current_admin=admin, db=db)
    assert resp["coupon"]["plan_id"] == plan.id

    # 显式传 null → 清除限定
    resp = update_coupon(row.id, CouponUpdateRequest(plan_id=None),
                         current_admin=admin, db=db)
    assert resp["coupon"]["plan_id"] is None


# ---------------------------------------------------------------------------
# 适用范围收口与币种文案
# ---------------------------------------------------------------------------

def test_create_recharge_kind_rejected(db):
    admin = _user(db, "admin", is_staff=True)
    with pytest.raises(HTTPException) as excinfo:
        create_coupons(
            CouponCreateRequest(code="TOPUP", kind="recharge",
                                discount_type="percent", value=100),
            current_admin=admin, db=db)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == (
        "充值类优惠券已下线：在线充值暂未开放，优惠券目前仅适用于订阅购买")
    assert db.query(models.CouponCode).count() == 0


def test_legacy_recharge_coupon_unusable_for_subscription(db):
    """存量 recharge 券（生产那张）保留原值，但订阅购买用不了它。"""
    buyer = _user(db, "buyer")
    plan = _plan(db, "Global 国际版", "1.20")
    db.add(models.CouponCode(code="LEGACYRC", kind="recharge",
                             discount_type="percent", value=100,
                             per_user_limit=1, max_uses=0, use_count=0,
                             is_active=True))
    db.commit()
    with pytest.raises(HTTPException) as excinfo:
        coupons.quote(db, user=buyer, code="LEGACYRC",
                      kind="subscription", item_id=plan.id)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "该优惠券不适用于此商品"


def test_min_amount_error_in_usd(db):
    admin = _user(db, "admin", is_staff=True)
    buyer = _user(db, "buyer")
    plan = _plan(db, "Global 国际版", "1.20")
    create_coupons(
        CouponCreateRequest(code="MIN5", kind="subscription",
                            discount_type="percent", value=90,
                            min_amount=5),
        current_admin=admin, db=db)
    with pytest.raises(HTTPException) as excinfo:
        coupons.quote(db, user=buyer, code="MIN5",
                      kind="subscription", item_id=plan.id)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "该券满 $5.00 可用，当前金额不足"
    assert "元" not in excinfo.value.detail
