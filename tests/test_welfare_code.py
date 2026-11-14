# -*- coding: utf-8 -*-
"""资格码（reward_type=welfare）已下线回归（2026-11-14 公益资格整族删除）。

新口径：
- 存量资格码核销直接拒绝、不烧码（与能量码同口径），不发资格、不打包
  查看行；生成端同样拒绝（400）。
- 白名单码与 reward_type 无关：welfare 型白名单码仍按 code_type 走
  长期有效（订阅＋查看行永久），只是不再含公益资格一处。
- 其余奖励类型（subscription/points）行为不变。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import codes, models
from backend.api import admin_ops as ops


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(name="公益服", slug="main", is_active=True))
    session.commit()
    yield session
    session.close()


def _make_user(db, username, **kw):
    u = models.WebUser(username=username, password_hash="x", **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_code(db, code_str, **kw):
    kw.setdefault("code_type", codes.CODE_TYPE_RENEW)
    kw.setdefault("max_uses", 1)
    kw.setdefault("use_count", 0)
    kw.setdefault("is_active", True)
    kw.setdefault("days", 30)
    kw.setdefault("reward_type", "welfare")
    c = models.RegistrationCode(code=code_str, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _unlock(db, user_id):
    return db.query(models.EmbyViewUnlock).filter(
        models.EmbyViewUnlock.user_id == user_id).first()


# ==================== 核销：资格码已下线 ====================

def test_welfare_code_rejected_without_burning(db):
    """存量资格码核销：直接拒绝、不烧码、不发资格、不建查看行。"""
    user = _make_user(db, "alice")
    code = _make_code(db, "WEL-AAAA")
    result = codes.redeem_code(db, user, "WEL-AAAA")
    db.commit()
    assert result["success"] is False
    assert "下线" in result["message"]
    db.refresh(user)
    assert not user.is_welfare
    assert _unlock(db, user.id) is None
    db.refresh(code)
    assert (code.use_count or 0) == 0


def test_whitelist_code_with_welfare_flavor_still_permanent(db):
    """白名单码语义只看 code_type：welfare 型白名单码照常置长期有效
    （订阅＋查看行永久），但不再发公益资格。"""
    user = _make_user(db, "bob")
    _make_code(db, "VIP-AAAA", code_type=codes.CODE_TYPE_WHITELIST)
    result = codes.redeem_code(db, user, "VIP-AAAA")
    db.commit()
    assert result["success"] is True
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == user.id).first()
    assert sub is not None
    unlock = _unlock(db, user.id)
    assert unlock is not None
    assert unlock.expires_at is None
    db.refresh(user)
    assert not user.is_welfare


# ==================== 生成：资格码已下线 ====================

@pytest.fixture()
def admin(db):
    return _make_user(db, "admin_test", is_staff=True, is_active=True)


def test_generate_welfare_code_rejected(db, admin):
    req = ops.CodeGenerateRequest(
        count=1, code_type=2, reward_type="welfare", days=30)
    with pytest.raises(HTTPException) as exc:
        ops.generate_codes(req, admin, db)
    assert exc.value.status_code == 400
    assert db.query(models.RegistrationCode).count() == 0


# ==================== 既有码类不回归（抽样） ====================

def test_subscription_code_still_grants_membership(db):
    user = _make_user(db, "alice")
    _make_code(db, "SUB-AAAA", reward_type="subscription")
    result = codes.redeem_code(db, user, "SUB-AAAA")
    db.commit()
    assert result["success"] is True
    assert result["reward_type"] == "subscription"
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == user.id).first()
    assert sub is not None
    db.refresh(user)
    assert not user.is_welfare


def test_points_code_rejected_without_burning(db):
    """能量卡码已下线：核销直接拒绝、不烧码（use_count 不动）。"""
    user = _make_user(db, "alice", points=0)
    code = _make_code(db, "PTS-AAAA", reward_type="points", points_value=50)
    result = codes.redeem_code(db, user, "PTS-AAAA")
    db.commit()
    assert result["success"] is False
    assert "下线" in result["message"]
    db.refresh(user)
    assert (user.points or 0) == 0
    db.refresh(code)
    assert (code.use_count or 0) == 0
