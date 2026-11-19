# -*- coding: utf-8 -*-
"""管理端用户 TOTP 管理（二期缺口 · 项 4）：
GET /api/admin/users/{user_id}/totp、POST /api/admin/users/{user_id}/totp/reset。

覆盖：
1. GET 已启用：enabled=True、pending_setup=False、时间戳齐；
   响应绝不含 secret_sealed 字段名与其值。
2. GET 待确认（setup 已发密钥未确认）：enabled=False、pending_setup=True。
3. GET 无绑定行：enabled=False、pending_setup=False。
4. GET 不存在的用户 404。
5. POST reset 已启用：行删除 + AdminLog 一条（user_totp_reset, was_enabled=True），
   删后 GET 回到未设置态（用户可重新设置）。
6. POST reset 待确认行：行删除，审计 was_enabled=False。
7. POST reset 无绑定行：404 且不写审计。
8. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-totp-key-32chars-long!!")

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_totp as totp_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db

SEALED = "sealed-material-must-never-leak"


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


def _mk_user(db, staff=False):
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


@pytest.fixture()
def staff(db):
    return _mk_user(db, staff=True)


@pytest.fixture()
def target(db):
    return _mk_user(db)


def _mk_totp(db, user_id, enabled=True):
    row = models.UserTotp(
        user_id=user_id,
        secret_sealed=SEALED,
        enabled=enabled,
        last_used_counter=12345 if enabled else None,
        confirmed_at=datetime(2026, 10, 21, 19, 3, 0) if enabled else None,
        created_at=datetime(2026, 10, 21, 19, 2, 0),
        updated_at=datetime(2026, 11, 5, 22, 41, 0),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(totp_admin.admin_totp_router)
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


def test_get_enabled_status_without_secret(db, staff, target):
    _mk_totp(db, target.id, enabled=True)
    r = _client(db, staff).get(f"/api/admin/users/{target.id}/totp")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is True
    assert data["pending_setup"] is False
    assert data["confirmed_at"].startswith("2026-10-21T19:03:00")
    assert data["created_at"].startswith("2026-10-21T19:02:00")
    # 安全红线：密钥材料（字段名与密封值）绝不出现在响应里
    assert "secret" not in r.text
    assert SEALED not in r.text


def test_get_pending_setup_status(db, staff, target):
    _mk_totp(db, target.id, enabled=False)
    r = _client(db, staff).get(f"/api/admin/users/{target.id}/totp")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is False
    assert data["pending_setup"] is True
    assert data["confirmed_at"] is None


def test_get_no_row_status(db, staff, target):
    r = _client(db, staff).get(f"/api/admin/users/{target.id}/totp")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is False
    assert data["pending_setup"] is False
    assert data["created_at"] is None


def test_get_user_not_found_404(db, staff):
    r = _client(db, staff).get("/api/admin/users/999999/totp")
    assert r.status_code == 404, r.text


def test_reset_enabled_deletes_and_audits(db, staff, target):
    _mk_totp(db, target.id, enabled=True)
    client = _client(db, staff)
    r = client.post(f"/api/admin/users/{target.id}/totp/reset")
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True}
    assert db.query(models.UserTotp).filter(
        models.UserTotp.user_id == target.id).count() == 0
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "user_totp_reset").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == staff.id
    assert logs[0].target_type == "web_user"
    assert logs[0].target_id == target.id
    assert logs[0].details["was_enabled"] is True
    # 重置后回到未设置态，用户可从零重新设置
    r = client.get(f"/api/admin/users/{target.id}/totp")
    assert r.json()["enabled"] is False
    assert r.json()["pending_setup"] is False


def test_reset_pending_row(db, staff, target):
    _mk_totp(db, target.id, enabled=False)
    r = _client(db, staff).post(f"/api/admin/users/{target.id}/totp/reset")
    assert r.status_code == 200, r.text
    assert db.query(models.UserTotp).count() == 0
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "user_totp_reset").all()
    assert len(logs) == 1
    assert logs[0].details["was_enabled"] is False


def test_reset_without_row_404_no_audit(db, staff, target):
    r = _client(db, staff).post(f"/api/admin/users/{target.id}/totp/reset")
    assert r.status_code == 404, r.text
    assert db.query(models.AdminLog).count() == 0


def test_reset_user_not_found_404(db, staff):
    r = _client(db, staff).post("/api/admin/users/999999/totp/reset")
    assert r.status_code == 404, r.text


def test_unauthenticated_401(db, target):
    r = _client(db).get(f"/api/admin/users/{target.id}/totp")
    assert r.status_code == 401, r.text


def test_non_staff_403(db, target):
    _mk_totp(db, target.id, enabled=True)
    r = _client(db, admin_error=403).post(
        f"/api/admin/users/{target.id}/totp/reset")
    assert r.status_code == 403, r.text
    assert db.query(models.UserTotp).count() == 1
