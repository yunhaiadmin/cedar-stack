# -*- coding: utf-8 -*-
"""一起看管理（后台大改造二期）：/api/admin/watch-together/* 口径。

覆盖：
- 鉴权：无凭证 401、非管理员 403（覆盖 get_current_admin 抛错模拟）。
- 房间列表：建房后能列出（房主/成员/条目 guid/片名/播放态齐），
  按创建时间倒序；回包任何层级不许出现流地址/play_sign/令牌类键（红线）。
- 房间详情：成员名单与同步参数（外推进度/倍速/人数上限/TTL）；
  不存在的房间 404。
- 强制关房：全房收到 wt.ended(reason=admin_closed)、房间从列表
  消失、AdminLog 落一条 watch_together_force_close；关不存在的房 404。
- RoomStore.list_rooms 内存降级口径：过期房间不列出。
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-wt-admin-key-32chars-padding")

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend import watch_together as wt
from backend.api import admin_watch_together as wt_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em


def run(coro):
    return asyncio.run(coro)


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_):
        return [m for m in self.sent if m.get("type") == type_]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def hub():
    # 管理面测试不验门禁：access_checker 直接放行并给用户名
    return wt.WatchTogetherHub(
        store=wt.RoomStore(),
        access_checker=lambda user_id, guid: (f"user{user_id}", True, True))


@pytest.fixture()
def staff(db):
    user = base_models.WebUser(
        username="wt_admin_" + uuid.uuid4().hex[:8], password_hash="x",
        is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db, hub, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(wt_admin.admin_watch_together_router)
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


@pytest.fixture()
def client(db, hub, staff, monkeypatch):
    monkeypatch.setattr(wt_admin, "watch_together_hub", hub)
    return _client(db, hub, admin_user=staff)


def _item(db, name="测试片"):
    lib = em.Library(guid=uuid.uuid4().hex, name="本地库")
    db.add(lib)
    db.flush()
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
        name=name, file_path=None)
    db.add(item)
    db.commit()
    return item


def _make_room(hub, host_id=11, item_guid="guid-a"):
    ws = FakeWS()
    snapshot = run(hub.create_room(ws, host_id, item_guid))
    return snapshot["code"], ws


_FORBIDDEN_KEYS = ("play_sign", "stream", "token", "api_key", "url", "sign")


def _assert_no_stream_fields(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            assert not any(bad in lowered for bad in _FORBIDDEN_KEYS), \
                f"管理面回包出现疑似流/令牌字段：{key}"
            _assert_no_stream_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_stream_fields(value)


# ------------------------------------------------------------ 鉴权

def test_rooms_require_admin(db, hub, monkeypatch):
    monkeypatch.setattr(wt_admin, "watch_together_hub", hub)
    r = _client(db, hub, admin_error=401).get("/api/admin/watch-together/rooms")
    assert r.status_code == 401, r.text
    r = _client(db, hub, admin_error=403).get("/api/admin/watch-together/rooms")
    assert r.status_code == 403, r.text


# ------------------------------------------------------------ 列表 / 详情

def test_list_rooms_empty(client):
    r = client.get("/api/admin/watch-together/rooms")
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 0, "items": []}


def test_list_and_detail_room(client, hub, db):
    item = _item(db)
    code, host_ws = _make_room(hub, host_id=11, item_guid=item.guid)
    guest_ws = FakeWS()
    run(hub.join_room(guest_ws, 12, code))

    r = client.get("/api/admin/watch-together/rooms")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["code"] == code
    assert row["item_id"] == item.guid
    assert row["item_name"] == "测试片"
    assert row["host_id"] == 11
    assert row["member_count"] == 2
    assert row["state"]["playing"] is False
    _assert_no_stream_fields(body)

    r = client.get(f"/api/admin/watch-together/rooms/{code}")
    assert r.status_code == 200, r.text
    detail = r.json()
    members = {m["user_id"]: m for m in detail["members"]}
    assert members[11]["is_host"] is True
    assert members[12]["is_host"] is False
    assert detail["max_members"] == wt.MAX_MEMBERS
    assert detail["ttl_seconds"] == wt.ROOM_TTL_SECONDS
    assert "position" in detail["state"] and "rate" in detail["state"]
    _assert_no_stream_fields(detail)


def test_detail_missing_room_404(client):
    r = client.get("/api/admin/watch-together/rooms/ZZZZZZ")
    assert r.status_code == 404, r.text


# ------------------------------------------------------------ 强制关房

def test_force_close_room(client, hub, db, staff):
    code, host_ws = _make_room(hub, host_id=21, item_guid="guid-x")
    guest_ws = FakeWS()
    run(hub.join_room(guest_ws, 22, code))

    r = client.post(f"/api/admin/watch-together/rooms/{code}/close")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["member_count"] == 2

    ended = [m for m in host_ws.of_type("wt.ended")]
    assert ended and ended[-1]["reason"] == "admin_closed"
    assert guest_ws.of_type("wt.ended")

    r = client.get("/api/admin/watch-together/rooms")
    assert r.json()["total"] == 0

    logs = db.query(base_models.AdminLog).filter_by(
        action="watch_together_force_close").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == staff.id
    assert logs[0].details["code"] == code
    assert logs[0].details["member_count"] == 2


def test_force_close_missing_room_404(client, db):
    r = client.post("/api/admin/watch-together/rooms/ZZZZZZ/close")
    assert r.status_code == 404, r.text
    assert db.query(base_models.AdminLog).filter_by(
        action="watch_together_force_close").count() == 0


# ------------------------------------------------------------ 存储枚举

def test_store_list_rooms_skips_expired():
    store = wt.RoomStore()

    async def seed():
        await store.set("AAAAAA", {"code": "AAAAAA"}, ttl=60)
        await store.set("BBBBBB", {"code": "BBBBBB"}, ttl=-1)
        return await store.list_rooms()

    rooms = run(seed())
    assert [r["code"] for r in rooms] == ["AAAAAA"]
