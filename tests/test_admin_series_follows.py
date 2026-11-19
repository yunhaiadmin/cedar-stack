# -*- coding: utf-8 -*-
"""追剧订阅管理端（二期缺口项 2）：GET /api/admin/series-follows
与 POST /api/admin/series-follows/{id}/cancel。

覆盖：
1. 列表返回全站订阅行（带用户名/剧名），状态/用户/关键词过滤生效，
   分页 total 正确。
2. 非法 status → 400。
3. 代取消：active 行置 cancelled、不删行，记一条 AdminLog；重复代
   取消幂等、不重复记审计；不存在的行 404。
4. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-series-follows-key-32chars")

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_series_follows as follows_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import backend.emby_server.models  # noqa: F401 — 注册 EA 侧表到同一 Base
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _user(db, staff=False):
    u = models.WebUser(
        username="u_" + uuid.uuid4().hex[:10],
        password_hash="test",
        email=None,
        is_active=True,
        is_staff=staff,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _series(db, name):
    item = em.MediaItem(
        guid=uuid.uuid4().hex[:32],
        library_id=1,
        item_type="series",
        name=name,
        production_year=2024,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _follow(db, user, series, status="active"):
    row = em.SeriesFollow(user_id=user.id, series_item_id=series.id,
                          status=status)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def admin(db):
    return _user(db, staff=True)


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(follows_admin.admin_series_follows_router)
    app.dependency_overrides[get_db] = lambda: db
    if admin_error is not None:
        def _raise():
            raise HTTPException(
                status_code=admin_error,
                detail="forbidden" if admin_error == 403 else "no creds")
        app.dependency_overrides[get_current_admin] = _raise
    elif admin_user is not None:
        app.dependency_overrides[get_current_admin] = lambda: admin_user
    return TestClient(app, raise_server_exceptions=False)


def test_list_all_and_filters(db, admin):
    alice = _user(db)
    bob = _user(db)
    s1 = _series(db, "追风者")
    s2 = _series(db, "慢行者")
    _follow(db, alice, s1)
    _follow(db, alice, s2, status="cancelled")
    _follow(db, bob, s1)

    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/series-follows")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    row = next(r for r in body["items"] if r["user_id"] == alice.id
               and r["series_item_id"] == s1.id)
    assert row["username"] == alice.username
    assert row["series_name"] == "追风者"
    assert row["series_year"] == 2024
    assert row["status"] == "active"

    res = client.get("/api/admin/series-follows",
                     params={"status": "cancelled"})
    assert res.json()["total"] == 1
    res = client.get("/api/admin/series-follows",
                     params={"user_id": bob.id})
    assert res.json()["total"] == 1
    res = client.get("/api/admin/series-follows", params={"q": "慢行"})
    assert res.json()["total"] == 1
    res = client.get("/api/admin/series-follows",
                     params={"q": alice.username})
    assert res.json()["total"] == 2
    res = client.get("/api/admin/series-follows",
                     params={"limit": 2, "offset": 2})
    assert res.json()["total"] == 3
    assert len(res.json()["items"]) == 1


def test_list_bad_status_400(db, admin):
    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/series-follows", params={"status": "paused"})
    assert res.status_code == 400


def test_admin_cancel_writes_audit_and_is_idempotent(db, admin):
    alice = _user(db)
    s1 = _series(db, "追风者")
    row = _follow(db, alice, s1)

    client = _client(db, admin_user=admin)
    res = client.post(f"/api/admin/series-follows/{row.id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"

    db.expire_all()
    kept = db.query(em.SeriesFollow).filter(
        em.SeriesFollow.id == row.id).first()
    assert kept is not None and kept.status == "cancelled"
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "series_follow_admin_cancel").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == admin.id
    assert logs[0].target_id == row.id
    assert logs[0].details["user_id"] == alice.id

    res = client.post(f"/api/admin/series-follows/{row.id}/cancel")
    assert res.status_code == 200
    assert res.json()["reason"] == "already_cancelled"
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "series_follow_admin_cancel").all()
    assert len(logs) == 1


def test_cancel_missing_404(db, admin):
    client = _client(db, admin_user=admin)
    res = client.post("/api/admin/series-follows/999999/cancel")
    assert res.status_code == 404


def test_auth_required(db):
    assert _client(db, admin_error=401).get(
        "/api/admin/series-follows").status_code == 401
    assert _client(db, admin_error=403).get(
        "/api/admin/series-follows").status_code == 403
    assert _client(db, admin_error=403).post(
        "/api/admin/series-follows/1/cancel").status_code == 403
