# -*- coding: utf-8 -*-
"""查看放行口径回归（公益资格来源已随福利服役族于 2026-11-14 删除）。

现行口径：公益服查看放行 = 持有未到期查看行（subscriptions.view_grant
全站唯一口径）。原「公益资格在期自动带查看」已删除——资格已全量折算
成订阅时间（2026-11-13 执行完毕）；存量查看行继续有效到期为止；
购买写口退役（410、不落行、不扣分）。付费服口径不变（有效订阅即权限）。
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
# view_grant：公益服现行口径（只认存量查看行）
# ---------------------------------------------------------------------------

def test_free_realm_without_unlock_row_denied(db):
    realm = _realm(db)
    u = _user(db)
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is False
    assert unlock is None


def test_free_realm_with_valid_row_grants(db):
    realm = _realm(db)
    u = _user(db)
    row = _unlock(db, u, realm, NOW + timedelta(days=5))
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is True
    assert unlock is not None and unlock.id == row.id


def test_free_realm_with_permanent_row_grants(db):
    realm = _realm(db)
    u = _user(db)
    _unlock(db, u, realm, None)
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is True


def test_free_realm_with_expired_row_denied(db):
    realm = _realm(db)
    u = _user(db)
    _unlock(db, u, realm, NOW - timedelta(days=1))
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is False


# ---------------------------------------------------------------------------
# view_grant：付费服回归不变
# ---------------------------------------------------------------------------

def test_paid_realm_subscription_still_grants(db):
    realm = _realm(db, name="付费服", slug="paid", access_mode="paid")
    plan = models.SubscriptionPlan(name="月卡", price=10, duration_days=30,
                                   realm_id=realm.id)
    db.add(plan)
    db.commit()
    u = _user(db)
    db.add(models.UserSubscription(
        user_id=u.id, plan_id=plan.id, realm_id=realm.id,
        start_date=NOW - timedelta(days=1),
        end_date=NOW + timedelta(days=29), status="active"))
    db.commit()
    granted, unlock = subscriptions.view_grant(db, u, realm.id)
    assert granted is True
    assert unlock is None


def test_paid_realm_without_subscription_denied(db):
    realm = _realm(db, name="付费服", slug="paid", access_mode="paid")
    u = _user(db)
    granted, _ = subscriptions.view_grant(db, u, realm.id)
    assert granted is False


# ---------------------------------------------------------------------------
# 管理端资源卡：公益服查看只认存量行来源
# ---------------------------------------------------------------------------

def test_admin_card_with_row_marks_unlock_source(db):
    realm = _realm(db)
    u = _user(db)
    _unlock(db, u, realm, NOW + timedelta(days=5))
    payload = user_grants.cards(db, u)
    card = next(c for c in payload["cards"] if c["realm_id"] == realm.id)
    assert card["grant"] == user_grants.GRANT_UNLOCK
    assert card["view_granted"] is True


def test_admin_card_without_row_not_granted(db):
    realm = _realm(db)
    u = _user(db)
    payload = user_grants.cards(db, u)
    card = next(c for c in payload["cards"] if c["realm_id"] == realm.id)
    assert card["grant"] != user_grants.GRANT_UNLOCK
    assert card["view_granted"] is False


# ---------------------------------------------------------------------------
# 购买写口退役：410、不落行、不扣分
# ---------------------------------------------------------------------------

def test_unlock_endpoint_retired_410_no_charge_no_row(db):
    from backend.emby_server.portal import UnlockViewRequest, unlock_emby_view
    realm = _realm(db)
    u = _user(db)
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=realm.id), u, db)
    assert exc.value.status_code == 410
    db.refresh(u)
    assert db.query(models.EmbyViewUnlock).count() == 0


def test_unlock_endpoint_retired_even_with_expired_row(db):
    from backend.emby_server.portal import UnlockViewRequest, unlock_emby_view
    realm = _realm(db)
    u = _user(db)
    row = _unlock(db, u, realm, NOW - timedelta(days=1))
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=realm.id), u, db)
    assert exc.value.status_code == 410
    db.refresh(u)
    db.refresh(row)
    assert row.expires_at < NOW  # 存量行原样不动，没被续开


# ---------------------------------------------------------------------------
# TG 面板：查看行与公益资格页均已删除
# ---------------------------------------------------------------------------

def test_tg_account_card_has_no_view_row(db):
    from backend.tg_bot import features
    realm = _realm(db)
    u = _user(db)
    _unlock(db, u, realm, NOW + timedelta(days=5))
    text = features.account_card(db, u)
    assert "查看凭证" not in text
    assert "公益资格" not in text


def test_tg_whois_panel_has_no_cred_page(db):
    from backend.tg_bot import whois_panel
    assert not hasattr(whois_panel, "cred_page")
