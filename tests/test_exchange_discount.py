"""折扣权益（ExchangeDiscountCredit）消费侧测试。

折扣权益由会员卡码 discount 型核销发放（backend/codes.py），下单时
自动抵扣。本文件只测保留下来的消费侧口径：最优权益挑选与折扣金额
计算；旧兑换码（ExchangeCode）核销发放路径已随旧体系于 2026-11-17
删除。

全部用隔离的内存 SQLite，不碰生产库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend import codes as code_lib
from backend.api import economy as E


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(db, name):
    u = models.WebUser(username=name, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _credit(db, user_id, pct, status="unused", expired=False):
    """与 codes.py 卡码 discount 型发放同形状地落一张权益（来源记 0）。"""
    cr = models.ExchangeDiscountCredit(
        user_id=user_id,
        exchange_code_id=0,
        discount_pct=pct,
        status=status,
        expires_at=(datetime.now() - timedelta(days=1)) if expired
        else (datetime.now() + timedelta(days=30)),
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return cr


def test_credit_visible_to_best(db):
    u = _user(db, "alice")
    credit = _credit(db, u.id, 85)
    best = E._best_discount_credit(db, u.id)
    assert best is not None
    assert best.id == credit.id
    assert best.discount_pct == 85


def test_math():
    assert E._exchange_discount_amount(Decimal("99.99"), 85) == (Decimal("15.00"), Decimal("84.99"))


def test_edge():
    assert E._exchange_discount_amount(Decimal("100"), 100) == (Decimal("1.00"), Decimal("99.00"))
    d, p = E._exchange_discount_amount(Decimal("0"), 85)
    assert d == Decimal("0") and p == Decimal("0")


def test_best_picks_lowest_pct(db):
    u = _user(db, "bob")
    _credit(db, u.id, 90)
    _credit(db, u.id, 85)
    best = E._best_discount_credit(db, u.id)
    assert best is not None
    assert best.discount_pct == 85


def test_best_skips_used_expired(db):
    u = _user(db, "carol")
    _credit(db, u.id, 85, status="used")
    _credit(db, u.id, 90, expired=True)
    assert E._best_discount_credit(db, u.id) is None


def test_redeem_twice_blocked(db):
    u = _user(db, "dave")
    first = code_lib.record_redemption(db, code_lib.REDEMPTION_KIND_REG, 852, u.id)
    second = code_lib.record_redemption(db, code_lib.REDEMPTION_KIND_REG, 852, u.id)
    assert first is True
    assert second is False
