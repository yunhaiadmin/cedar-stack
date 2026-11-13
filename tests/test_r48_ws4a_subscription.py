"""r48 遗留 WS4a：订阅口径回归测试（2026-11-13 货币下线后只剩 ⑫）

⑫ 续买不同档位时 plan_id 跟随新档（_grant_subscription 顺延口径）。
（⑪ 积分买订阅同口径、⑬ 积分自动续订已随货币下线删除。）
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.models import Base
from backend.api import economy
from backend.api.economy import _grant_subscription
from backend import subscriptions as subs


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    yield s
    s.close()


def _user(db, username="u1", points=0, level=1):
    u = models.WebUser(username=username, password_hash="x", points=points,
                       member_level=level, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    if points:
        # 生产里余额变动必经 _add_points/_spend_points 写台账（全仓无
        # 直接赋值 points 的代码路径）。P1-4 来源限定是台账纯函数，
        # 裸余额不代表任何真实用户：这里把初始余额按「签到挣来」入账，
        # 与成功路径用例的隐含前提（余额可花）对齐。
        db.add(models.PointsLog(user_id=u.id, amount=points,
                                balance_after=points, type="checkin",
                                description="test", created_at=datetime.now()))
        db.commit()
    return u


def _realm(db, access_mode="paid"):
    r = models.ServerRealm(name="付费服", slug="paid1", access_mode=access_mode)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _plan(db, realm_id, name="月卡", price="18.00", points_price="400", days=30):
    p = models.SubscriptionPlan(
        name=name, price=Decimal(price),
        points_price=Decimal(points_price) if points_price is not None else None,
        duration_days=days, is_active=True, realm_id=realm_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _level(db, level, discount_pct):
    db.add(models.MemberLevel(level=level, name=f"Lv{level}",
                              xp_threshold=0, discount_pct=discount_pct,
                              is_active=True))
    db.commit()


def _order_req(plan_id, **kw):
    base = dict(kind="subscription", item_id=plan_id,
                payment_method="alipay", pay_with_points=True)
    base.update(kw)
    return economy.CreateOrderRequest(**base)


def _balance(db, uid):
    db.expire_all()
    return db.query(models.WebUser.points).filter(
        models.WebUser.id == uid).scalar() or 0


def _gateway_cfg(db):
    # create_payment_order 入口先校验支付网关配置（积分支付也一样），测试里种上
    for k, v in (("payment_gateway_url", "https://pay.example"),
                 ("payment_partner_id", "1"),
                 ("payment_partner_key", "k")):
        db.add(models.SystemConfig(key=k, value=v))
    db.commit()


# ==================== ⑪ 积分口径 ====================

def test_grant_renew_updates_plan_id(db):
    """续买不同档位：同一行顺延，plan_id 跟随新档，天数按新档"""
    r = _realm(db)
    u = _user(db, "u1")
    plan_a = _plan(db, r.id, name="月卡", days=30)
    plan_b = _plan(db, r.id, name="季卡", days=90)
    s1 = _grant_subscription(db, u, plan_a, 30,
                             realm_id=r.id)
    db.commit()
    first_end = s1.end_date
    s2 = _grant_subscription(db, u, plan_b, 90,
                             realm_id=r.id)
    db.commit()
    assert s2.id == s1.id  # 同一行
    assert s2.plan_id == plan_b.id  # 跟随新档
    assert s2.end_date == first_end + timedelta(days=90)  # 天数按新档


# ==================== ⑬ 自动续订 ====================

def _active_sub(db, user, plan, realm, hours_left=12, auto_renew=True):
    now = datetime.now()
    s = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm.id,
        start_date=now - timedelta(days=20),
        end_date=now + timedelta(hours=hours_left),
        status="active", auto_renew=auto_renew)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


