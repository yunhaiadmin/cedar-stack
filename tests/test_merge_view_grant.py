# -*- coding: utf-8 -*-
"""公益资格与查看凭证合并（2026-11-09 用户定版）回归。

定版口径：公益服查看放行 = 公益资格在期 OR 持有未到期查看行
（subscriptions.view_grant 全站唯一口径）。资格在期复用既有口径
（is_welfare 且 welfare_expires_at 为空或未到期）；保留期（已过期、
未超保留期）不算在期。存量查看行继续有效到期为止；购买写口退役
（410、不落行、不扣分）。付费服口径不变。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models, subscriptions, user_grants


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _realm(db, name="公益服", slug="free", access_mode="free"):
    realm = models.ServerRealm(name=name, slug=slug, access_mode=access_mode)
    db.add(realm)
    db.commit()
    return realm


def _user(db, username="u1", **kw):
    u = models.WebUser(username=username, password_hash="x", is_active=True, **kw)
    db.add(u)
    db.commit()
    return u


def _unlock(db, user, realm, expires_at):
    row = models.EmbyViewUnlock(
        user_id=user.id, realm_id=realm.id,
        unlocked_at=datetime.now() - timedelta(days=10),
        expires_at=expires_at, points_spent=50)
    db.add(row)
    db.commit()
    return row


NOW = datetime.now()


# ---------------------------------------------------------------------------
# view_grant：公益服新口径
# ---------------------------------------------------------------------------

def test_welfare_in_period_without_unlock_row_grants(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is True
    assert unlock is None


def test_welfare_permanent_without_unlock_row_grants(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=None)
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is True


def test_welfare_expired_without_row_denied(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW - timedelta(days=30))
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is False
    assert unlock is None


def test_welfare_expired_with_valid_row_grants(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW - timedelta(days=30))
    row = _unlock(db, u, realm, NOW + timedelta(days=5))
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is True
    assert unlock is not None and unlock.id == row.id


def test_welfare_expired_with_expired_row_denied(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW - timedelta(days=30))
    _unlock(db, u, realm, NOW - timedelta(days=1))
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is False


def test_welfare_in_grace_period_without_row_denied(db):
    # 保留期：资格昨天刚过期（远未超任何保留期口径），不算「在期」，
    # 无存量查看行即不放行——保留期不给查看权。
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW - timedelta(days=1))
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is False


def test_not_welfare_with_valid_row_grants(db):
    # 非公益资格用户（如资格码打包赠送的查看行）存量行照旧有效。
    realm = _realm(db)
    u = _user(db, is_welfare=False)
    _unlock(db, u, realm, NOW + timedelta(days=5))
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is True


# ---------------------------------------------------------------------------
# view_grant：付费服回归不变
# ---------------------------------------------------------------------------

def test_paid_realm_subscription_still_grants(db):
    realm = _realm(db, name="付费服", slug="paid", access_mode="paid")
    plan = models.SubscriptionPlan(name="月卡", price=10, duration_days=30,
                                   realm_id=realm.id)
    db.add(plan)
    db.commit()
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    db.add(models.UserSubscription(
        user_id=u.id, plan_id=plan.id, realm_id=realm.id,
        start_date=NOW - timedelta(days=1),
        end_date=NOW + timedelta(days=29), status="active"))
    db.commit()
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is True
    assert unlock is None


def test_paid_realm_welfare_alone_does_not_grant(db):
    # 付费服不认公益资格：资格在期、无订阅仍不放行（付费口径零改动）。
    realm = _realm(db, name="付费服", slug="paid", access_mode="paid")
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is False


# ---------------------------------------------------------------------------
# 管理端资源卡：公益服查看跟随资格
# ---------------------------------------------------------------------------

def test_admin_card_welfare_in_period_grants_view(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    payload = user_grants.cards(db, u)
    card = next(c for c in payload["cards"] if c["realm_id"] == realm.id)
    assert card["grant"] == user_grants.GRANT_WELFARE
    assert card["view_granted"] is True


def test_admin_card_expired_welfare_with_row_marks_unlock_source(db):
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW - timedelta(days=30))
    _unlock(db, u, realm, NOW + timedelta(days=5))
    payload = user_grants.cards(db, u)
    card = next(c for c in payload["cards"] if c["realm_id"] == realm.id)
    assert card["grant"] == user_grants.GRANT_UNLOCK
    assert card["view_granted"] is True


# ---------------------------------------------------------------------------
# 购买写口退役：410、不落行、不扣分
# ---------------------------------------------------------------------------

def test_unlock_endpoint_retired_410_no_charge_no_row(db):
    from backend.emby_server.portal import UnlockViewRequest, unlock_emby_view
    realm = _realm(db)
    u = _user(db, points=1000, is_welfare=True,
              welfare_expires_at=NOW - timedelta(days=30))
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=realm.id), u, db)
    assert exc.value.status_code == 410
    db.refresh(u)
    assert int(u.points or 0) == 1000
    assert db.query(models.EmbyViewUnlock).count() == 0


def test_unlock_endpoint_retired_even_with_expired_row(db):
    from backend.emby_server.portal import UnlockViewRequest, unlock_emby_view
    realm = _realm(db)
    u = _user(db, points=1000)
    row = _unlock(db, u, realm, NOW - timedelta(days=1))
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=realm.id), u, db)
    assert exc.value.status_code == 410
    db.refresh(u)
    db.refresh(row)
    assert int(u.points or 0) == 1000
    assert row.expires_at < NOW  # 存量行原样不动，没被续开


# ---------------------------------------------------------------------------
# TG 面板：查看行已整行删除（2026-11-09 追加口径，覆盖原三态展示）
# ---------------------------------------------------------------------------

def test_tg_account_card_has_no_view_row(db):
    from backend.tg_bot import features
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    _unlock(db, u, realm, NOW + timedelta(days=5))
    text = features.account_card(db, u)
    assert "查看凭证" not in text
    assert "公益资格" in text


def test_tg_whois_cred_page_has_no_view_row(db):
    from backend.tg_bot import whois_panel
    realm = _realm(db)
    u = _user(db, is_welfare=True, welfare_expires_at=NOW + timedelta(days=10))
    _unlock(db, u, realm, NOW + timedelta(days=5))
    text, _markup = whois_panel.cred_page(db, u)
    assert "查看凭证" not in text
    assert "公益资格" in text
    assert "资格到期" in text
