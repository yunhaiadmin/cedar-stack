# -*- coding: utf-8 -*-
"""4K 探针结果管理查看口（二期缺口）：GET /api/admin/probe-4k/results*。

覆盖：
- 鉴权：无凭证 401、非管理员 403。
- 列表：按 created_at 倒序（同刻 id 倒序）；total 正确；列表不带
  payload 原文、只给 payload_chars；带提交人用户名；outcome_options
  是全表去重的结果短码。
- 筛选：item_guid 包含匹配、outcome 精确匹配，两项可叠加。
- 分页：limit/offset 生效，limit 超上限被钳制不报错。
- 详情：含 payload 原文；不存在的 id 404。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-probe4k-admin-key-32chars-pad")

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_probe_4k as probe_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
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
    user = models.WebUser(
        username="probe_admin_" + uuid.uuid4().hex[:8], password_hash="x",
        is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(probe_admin.admin_probe_4k_router)
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


_BASE = datetime(2026, 11, 17, 12, 0, 0)


def _seed(db, user_id, guid, outcome, minutes, attempt="default",
          payload='{"ok": true}'):
    row = models.Probe4kResult(
        user_id=user_id, item_guid=guid, attempt=attempt, outcome=outcome,
        payload=payload, user_agent="ua/1.0",
        created_at=_BASE + timedelta(minutes=minutes))
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def seeded(db, staff):
    _seed(db, staff.id, "guid-alpha-1", "mse-playing", 1)
    _seed(db, staff.id, "guid-alpha-2", "error", 2)
    _seed(db, staff.id, "guid-beta-1", "mse-playing", 3,
          attempt="preflight", payload='{"big": "' + "x" * 500 + '"}')
    db.commit()
    return staff


_URL = "/api/admin/probe-4k/results"


# ------------------------------------------------------------ 鉴权

def test_requires_admin(db):
    assert _client(db, admin_error=401).get(_URL).status_code == 401
    assert _client(db, admin_error=403).get(_URL).status_code == 403


# ------------------------------------------------------------ 列表

def test_list_desc_order_and_shape(db, seeded):
    r = _client(db, seeded).get(_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    guids = [item["item_guid"] for item in body["items"]]
    assert guids == ["guid-beta-1", "guid-alpha-2", "guid-alpha-1"]
    first = body["items"][0]
    assert "payload" not in first
    assert first["payload_chars"] > 500
    assert first["username"] == seeded.username
    assert first["attempt"] == "preflight"
    assert body["outcome_options"] == ["error", "mse-playing"]


def test_list_filters(db, seeded):
    c = _client(db, seeded)
    body = c.get(_URL, params={"item_guid": "alpha"}).json()
    assert body["total"] == 2
    body = c.get(_URL, params={"outcome": "mse-playing"}).json()
    assert body["total"] == 2
    body = c.get(_URL, params={
        "item_guid": "alpha", "outcome": "mse-playing"}).json()
    assert body["total"] == 1
    assert body["items"][0]["item_guid"] == "guid-alpha-1"
    body = c.get(_URL, params={"outcome": "no-such"}).json()
    assert body["total"] == 0 and body["items"] == []


def test_list_pagination(db, seeded):
    c = _client(db, seeded)
    body = c.get(_URL, params={"limit": 2, "offset": 0}).json()
    assert len(body["items"]) == 2 and body["total"] == 3
    body = c.get(_URL, params={"limit": 2, "offset": 2}).json()
    assert len(body["items"]) == 1
    body = c.get(_URL, params={"limit": 99999}).json()
    assert len(body["items"]) == 3


# ------------------------------------------------------------ 详情

def test_detail_includes_payload(db, seeded):
    c = _client(db, seeded)
    row_id = c.get(_URL).json()["items"][0]["id"]
    r = c.get(f"{_URL}/{row_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == row_id
    assert body["payload"].startswith('{"big": "')
    assert body["username"] == seeded.username


def test_detail_missing_404(db, seeded):
    r = _client(db, seeded).get(f"{_URL}/999999")
    assert r.status_code == 404, r.text
