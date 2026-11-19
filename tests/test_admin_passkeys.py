# -*- coding: utf-8 -*-
"""管理端用户 Passkey 管理（二期缺口 · 项 3）：
GET/DELETE /api/admin/users/{user_id}/passkeys[/{passkey_id}]。

覆盖：
1. GET 按用户列凭据：只含展示字段，绝不回 credential_id / public_key。
2. GET 不存在的用户 404。
3. DELETE 吊销：行删除 + AdminLog 一条（user_passkey_revoke）。
4. DELETE 他人凭据（id 存在但不属于该用户）404 且行保留，不写审计。
5. DELETE 不存在的凭据 404。
6. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-passkeys-key-32chars!!")

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_passkeys as passkeys_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db


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


def _mk_passkey(db, user_id, device_name="我的 iPhone"):
    row = models.UserPasskey(
        user_id=user_id,
        credential_id="cred_" + uuid.uuid4().hex,
        public_key="cose_" + uuid.uuid4().hex,
        sign_count=7,
        transports="internal",
        device_name=device_name,
        created_at=datetime(2026, 11, 1, 10, 0, 0),
        last_used_at=datetime(2026, 11, 18, 9, 30, 0),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(passkeys_admin.admin_passkeys_router)
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


def test_list_passkeys_display_fields_only(db, staff, target):
    pk = _mk_passkey(db, target.id)
    _mk_passkey(db, target.id, device_name="备用钥匙")
    other = _mk_user(db)
    _mk_passkey(db, other.id, device_name="别人的")

    r = _client(db, staff).get(f"/api/admin/users/{target.id}/passkeys")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["user_id"] == target.id
    assert data["username"] == target.username
    assert len(data["items"]) == 2
    item = next(i for i in data["items"] if i["id"] == pk.id)
    assert item["device_name"] == "我的 iPhone"
    assert item["transports"] == "internal"
    assert item["sign_count"] == 7
    assert item["created_at"].startswith("2026-11-01T10:00:00")
    assert item["last_used_at"].startswith("2026-11-18T09:30:00")
    # 安全红线：凭据材料绝不出现在响应里
    assert "credential_id" not in r.text
    assert "public_key" not in r.text
    assert "cred_" not in r.text
    assert "cose_" not in r.text


def test_list_passkeys_empty(db, staff, target):
    r = _client(db, staff).get(f"/api/admin/users/{target.id}/passkeys")
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []


def test_list_passkeys_user_not_found(db, staff):
    r = _client(db, staff).get("/api/admin/users/999999/passkeys")
    assert r.status_code == 404, r.text


def test_revoke_passkey_deletes_and_audits(db, staff, target):
    pk = _mk_passkey(db, target.id)
    r = _client(db, staff).delete(
        f"/api/admin/users/{target.id}/passkeys/{pk.id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True}
    assert db.query(models.UserPasskey).filter(
        models.UserPasskey.id == pk.id).count() == 0
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "user_passkey_revoke").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == staff.id
    assert logs[0].target_type == "web_user"
    assert logs[0].target_id == target.id
    assert logs[0].details["passkey_id"] == pk.id


def test_revoke_other_users_passkey_404_and_kept(db, staff, target):
    other = _mk_user(db)
    pk = _mk_passkey(db, other.id)
    r = _client(db, staff).delete(
        f"/api/admin/users/{target.id}/passkeys/{pk.id}")
    assert r.status_code == 404, r.text
    assert db.query(models.UserPasskey).filter(
        models.UserPasskey.id == pk.id).count() == 1
    assert db.query(models.AdminLog).count() == 0


def test_revoke_missing_passkey_404(db, staff, target):
    r = _client(db, staff).delete(
        f"/api/admin/users/{target.id}/passkeys/999999")
    assert r.status_code == 404, r.text


def test_revoke_user_not_found_404(db, staff):
    r = _client(db, staff).delete("/api/admin/users/999999/passkeys/1")
    assert r.status_code == 404, r.text


def test_unauthenticated_401(db, target):
    r = _client(db).get(f"/api/admin/users/{target.id}/passkeys")
    assert r.status_code == 401, r.text


def test_non_staff_403(db, target):
    pk_owner = target
    r = _client(db, admin_error=403).delete(
        f"/api/admin/users/{pk_owner.id}/passkeys/1")
    assert r.status_code == 403, r.text
