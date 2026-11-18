# -*- coding: utf-8 -*-
"""PA-022 后续：查看购买写口墓碑（公益服已于 2026-11-19 整套删除）。

历史：PA-022 钉的是「过期行再次解锁＝原地续开」一类购买口径；
2026-11-09 资格与凭证合并后写口退役（公益服 410 / 付费服 400）。
公益服删除后口径统一：查看权限来自有效订阅或团队资格，从不单独
购买——unlock-view 对任何存在的服一律 400、不落行、存量行原样
不动（过期行不被续开、有效行不被改动）；服不存在 404。
价格/天数配置键（emby_view_unlock_points/days）保留可查，与本口无关。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.emby_server.portal import UnlockViewRequest, unlock_emby_view

_TABLES = ("web_users", "server_realms", "system_configs",
           "emby_view_unlocks")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    for t in _TABLES:
        models.Base.metadata.tables[t].create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _setup(db, row_expires="absent"):
    db.add(models.SystemConfig(key="emby_view_unlock_points", value="200"))
    db.add(models.SystemConfig(key="emby_view_unlock_days", value="30"))
    realm = models.ServerRealm(name="主服", slug="pa022main")
    db.add(realm)
    u = models.WebUser(username="pa022user", password_hash="x",
                       is_active=True)
    db.add(u)
    db.commit()
    if row_expires != "absent":
        db.add(models.EmbyViewUnlock(
            user_id=u.id, realm_id=realm.id,
            unlocked_at=datetime.now() - timedelta(days=40),
            expires_at=row_expires, points_spent=50))
        db.commit()
    return realm, u


def _row(db, u, realm):
    return (db.query(models.EmbyViewUnlock)
              .filter(models.EmbyViewUnlock.user_id == u.id,
                      models.EmbyViewUnlock.realm_id == realm.id)
              .first())


def _call(db, u, realm):
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=realm.id), u, db)
    return exc.value


def test_tombstone_no_row_created(db):
    realm, u = _setup(db)
    exc = _call(db, u, realm)
    assert exc.status_code == 400
    db.refresh(u)
    assert _row(db, u, realm) is None


def test_tombstone_expired_row_untouched(db):
    expired = datetime.now() - timedelta(days=1)
    realm, u = _setup(db, row_expires=expired)
    exc = _call(db, u, realm)
    assert exc.status_code == 400
    db.refresh(u)
    row = _row(db, u, realm)
    assert row is not None and row.expires_at == expired  # 没被续开


def test_tombstone_valid_row_untouched(db):
    valid = datetime.now() + timedelta(days=10)
    realm, u = _setup(db, row_expires=valid)
    exc = _call(db, u, realm)
    assert exc.status_code == 400
    row = _row(db, u, realm)
    assert row is not None and row.expires_at == valid


def test_tombstone_missing_realm_404(db):
    realm, u = _setup(db)
    with pytest.raises(HTTPException) as exc:
        unlock_emby_view(UnlockViewRequest(realm_id=987654), u, db)
    assert exc.value.status_code == 404
