# -*- coding: utf-8 -*-
"""登录锁定管理端配置口（F4/F8 补项）：GET/PUT /api/admin/login-lockout/config。

覆盖：
1. GET 默认值（开关开、账号 10 次、IP 30 次、窗口 900s、锁 900s）与库行覆盖。
2. PUT 合法值落库、GET 回读一致；写操作记一条 AdminLog。
3. PUT 非法值一律 400、整批不落库（阈值 <1 或 >1000、窗口/锁长 <60 或 >86400）。
4. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-login-lockout-admin-key-32chars")

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_login_lockout as lockout_admin
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


@pytest.fixture()
def staff(db):
    u = models.WebUser(
        username="lockout_admin_" + uuid.uuid4().hex[:8],
        password_hash="test",
        email=None,
        is_active=True,
        is_staff=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(lockout_admin.admin_login_lockout_router)
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


VALID = {
    "enabled": True,
    "account_max_failures": 10,
    "ip_max_failures": 30,
    "window_sec": 900,
    "lock_sec": 900,
}


def _rows(db):
    return {
        r.key: r.value
        for r in db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("login_lockout_%")).all()
    }


def test_get_defaults(db, staff):
    r = _client(db, staff).get("/api/admin/login-lockout/config")
    assert r.status_code == 200, r.text
    assert r.json() == VALID


def test_get_reflects_stored_rows(db, staff):
    for key, value in {
        "login_lockout_enabled": "false",
        "login_lockout_account_max_failures": "5",
        "login_lockout_window_sec": "600",
    }.items():
        db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    r = _client(db, staff).get("/api/admin/login-lockout/config")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is False
    assert data["account_max_failures"] == 5
    assert data["ip_max_failures"] == 30
    assert data["window_sec"] == 600
    assert data["lock_sec"] == 900


def test_put_valid_persists_and_audits(db, staff):
    client = _client(db, staff)
    payload = {**VALID, "enabled": False, "account_max_failures": 5,
               "lock_sec": 1800}
    r = client.put("/api/admin/login-lockout/config", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == payload
    rows = _rows(db)
    assert rows["login_lockout_enabled"] == "false"
    assert rows["login_lockout_account_max_failures"] == "5"
    assert rows["login_lockout_lock_sec"] == "1800"
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action == "login_lockout_update_config").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == staff.id
    # GET 回读与保存值一致
    r = client.get("/api/admin/login-lockout/config")
    assert r.json() == payload


@pytest.mark.parametrize("bad", [
    {"account_max_failures": 0},
    {"account_max_failures": 1001},
    {"ip_max_failures": 0},
    {"ip_max_failures": 1001},
    {"window_sec": 59},
    {"window_sec": 86401},
    {"lock_sec": 59},
    {"lock_sec": 86401},
])
def test_put_invalid_rejected_and_nothing_persisted(db, staff, bad):
    client = _client(db, staff)
    r = client.put("/api/admin/login-lockout/config",
                   json={**VALID, **bad})
    assert r.status_code == 400, r.text
    assert _rows(db) == {}, "非法值整批不落库"
    assert db.query(models.AdminLog).count() == 0


def test_put_wrong_type_rejected(db, staff):
    r = _client(db, staff).put(
        "/api/admin/login-lockout/config",
        json={**VALID, "account_max_failures": "many"})
    assert r.status_code in (400, 422), r.text
    assert _rows(db) == {}


def test_unauthenticated_401(db):
    r = _client(db).get("/api/admin/login-lockout/config")
    assert r.status_code == 401, r.text


def test_non_staff_403(db):
    r = _client(db, admin_error=403).put(
        "/api/admin/login-lockout/config", json=VALID)
    assert r.status_code == 403, r.text
    assert _rows(db) == {}
