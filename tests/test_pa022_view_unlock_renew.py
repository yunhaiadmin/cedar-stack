# -*- coding: utf-8 -*-
"""PA-022 后续：公益服查看购买写口退役（2026-11-09 资格与凭证合并）。

历史：PA-022 钉的是「过期行再次解锁＝原地续开」一类购买口径。
2026-11-09 用户定版资格与凭证合并后，公益服查看随公益资格自动
开通（subscriptions.view_grant），购买写口整体退役。本文件改钉
下线口径：

1. 任何人调 unlock-view（公益服）：一律 410 Gone，不落行、不扣分、
   存量行原样不动（过期行不被续开、有效行不被改动）。
2. 付费服：保持原 400 口径（查看来自订阅），不进 410 分支。
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
           "emby_view_unlocks", "points_logs")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    for t in _TABLES:
        models.Base.metadata.tables[t].create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _setup(db, points=1000, row_expires="absent", access_mode="free"):
    db.add(models.SystemConfig(key="emby_view_unlock_points", value="200"))
    db.add(models.SystemConfig(key="emby_view_unlock_days", value="30"))
    realm = models.ServerRealm(name="公益服", slug="pa022free",
                               access_mode=access_mode)
    db.add(realm)
    u = models.WebUser(username="pa022user", password_hash="x",
                       is_active=True, points=points)
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


def test_retired_no_row_no_charge(db):
    realm, u = _setup(db)
    exc = _call(db, u, realm)
    assert exc.status_code == 410
    db.refresh(u)
    assert int(u.points or 0) == 1000
    assert _row(db, u, realm) is None


def test_retired_expired_row_untouched(db):
    expired = datetime.now() - timedelta(days=1)
    realm, u = _setup(db, row_expires=expired)
    exc = _call(db, u, realm)
    assert exc.status_code == 410
    db.refresh(u)
    row = _row(db, u, realm)
    assert int(u.points or 0) == 1000
    assert row is not None and row.expires_at == expired  # 没被续开


def test_retired_valid_row_untouched(db):
    valid = datetime.now() + timedelta(days=10)
    realm, u = _setup(db, row_expires=valid)
    exc = _call(db, u, realm)
    assert exc.status_code == 410
    row = _row(db, u, realm)
    assert row is not None and row.expires_at == valid


def test_retired_insufficient_points_still_410(db):
    realm, u = _setup(db, points=0)
    exc = _call(db, u, realm)
    assert exc.status_code == 410  # 下线与余额无关，不再有「能量不足」


def test_paid_realm_keeps_400(db):
    realm, u = _setup(db, access_mode="paid")
    exc = _call(db, u, realm)
    assert exc.status_code == 400
