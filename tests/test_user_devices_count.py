# -*- coding: utf-8 -*-
"""我的设备计数口径（2026-11-15 概览「15 / 3 台」核查的后端钉子）。

口径：GET /api/user/emby/devices 同时下发两个数——
- count＝历史登记总数（含过期/闲置/已禁用记录），只供管理面核对；
- active_count＝有效设备数（未禁用且近 ACTIVE_DEVICE_DAYS=30 天内活跃），
  与 remaining＝limit - active_count 同口径；概览与设置页展示的
  「N / 上限 台」必须用 active_count，不许用 count。
本测试钉住三者的语义，防止再把历史总数当有效数展示。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api.user import user_router
from backend.database import get_db
from backend.security import create_access_token, hash_password


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _client(db):
    app = FastAPI()
    app.include_router(user_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, name: str):
    u = models.WebUser(
        username=name + uuid.uuid4().hex[:8],
        password_hash=hash_password("Passw0rd!x"),
        email=f"{name}@example.com",
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(user) -> dict:
    token = create_access_token(
        user.id, {"username": user.username},
        token_version=user.token_version or 0,
    )
    return {"Authorization": f"Bearer {token}"}


def _device(db, user_id: int, device_id: str, *, days_ago: int, blocked=False):
    seen = datetime.now() - timedelta(days=days_ago)
    d = models.UserDevice(
        user_id=user_id,
        device_id=device_id,
        name="iOS",
        client="Safari",
        first_seen_at=seen,
        last_seen_at=seen,
        is_blocked=blocked,
    )
    db.add(d)
    db.commit()
    return d


def test_devices_count_vs_active_count(db):
    user = _user(db, "devcnt_")
    other = _user(db, "devoth_")
    db.add(models.SystemConfig(key="device_limit_per_user", value="3"))
    db.commit()
    _device(db, user.id, "recent", days_ago=0)
    _device(db, user.id, "stale", days_ago=40)
    _device(db, user.id, "blocked", days_ago=0, blocked=True)
    _device(db, other.id, "foreign", days_ago=0)

    r = _client(db).get("/api/user/emby/devices", headers=_auth(user))
    assert r.status_code == 200
    body = r.json()
    # 历史总数含过期与禁用＝3；有效数只算近 30 天未禁用的＝1
    assert body["count"] == 3
    assert body["active_count"] == 1
    assert body["limit"] == 3
    assert body["remaining"] == 2
    assert len(body["devices"]) == 3
    flags = {d["device_id"]: d["is_online_recent"] for d in body["devices"]}
    assert flags == {"recent": True, "stale": False, "blocked": False}
