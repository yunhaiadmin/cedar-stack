# -*- coding: utf-8 -*-
"""公告改发 TG 群 + 弹幕 external_url 掩码 + 免登录交换码原子认领（2026-11-22）。

钉住的口径：
- 管理端创建/更新公告不再给全员落 StationMessage（站内广播已于
  2026-11-19 取消）；改为经 tg_bot.sender 发一条消息到正式群
  （bot_group_ids 第一个群，rankpush.target_group_id 同口径）。
  群未配置 / 发送失败都不许让请求失败，响应带 tg_sent 如实回。
- 弹幕线路配置 GET：external_url 含 token，只回掩码 "******" +
  external_url_set 布尔；PUT 收到 "******" 保留原值、带新址正常更新。
- 免登录交换码：同一码重放（used_at 已置位）一律 401。

全部离线：内存 SQLite + 进程内路由，TG 发送打桩不发真实网络请求。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, ratelimit
from backend.api import admin_auth as _admin_auth  # noqa: F401 — 导入即注册全部管理端点
from backend.api.admin_core import admin_router
from backend.database import get_db
from backend.security import hash_password

PASSWORD = "test-admin-password"  # 夹具用假密码，仅测试库内使用
GROUP_ID = -1009876543210
REAL_URL = "https://dm.example.com/tok123secret"
NEW_URL = "https://dm2.example.com/newtok456"


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


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from backend.integrations import store

    store.invalidate()
    yield
    store.invalidate()


def _client(db):
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, *, staff=False, username=None):
    u = models.WebUser(
        username=username or ("u_" + uuid.uuid4().hex[:10]),
        password_hash=hash_password(PASSWORD),
        is_staff=staff,
        admin_role="super" if staff else None,
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth_header(client, user):
    res = client.post("/api/admin/auth/login",
                      json={"username": user.username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _set_group(db):
    db.add(models.SystemConfig(key="bot_group_ids", value=str(GROUP_ID)))
    db.commit()


@pytest.fixture()
def tg_calls(monkeypatch):
    calls = []

    def _fake_send(db_, chat_id, text, **kwargs):
        calls.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return True, None

    monkeypatch.setattr("backend.tg_bot.sender.send_message", _fake_send)
    return calls


def _station_count(db):
    db.expire_all()
    return db.query(models.StationMessage).count()


# ------------------------------------------------------------ 公告 → TG 群

def test_create_announcement_goes_to_tg_not_station(db, tg_calls):
    client = _client(db)
    admin = _user(db, staff=True)
    _user(db)  # 普通启用用户：旧行为会给他落站内消息
    _set_group(db)
    headers = _auth_header(client, admin)

    res = client.post("/api/admin/announcements", headers=headers, json={
        "title": "维护通知", "content": "今晚十点维护", "type": "info"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tg_sent"] is True
    assert "站内消息" not in body["message"]
    assert _station_count(db) == 0
    assert len(tg_calls) == 1
    assert tg_calls[0]["chat_id"] == GROUP_ID
    assert "维护通知" in tg_calls[0]["text"]
    assert "今晚十点维护" in tg_calls[0]["text"]


def test_create_announcement_without_group_still_succeeds(db, tg_calls):
    client = _client(db)
    admin = _user(db, staff=True)
    headers = _auth_header(client, admin)

    res = client.post("/api/admin/announcements", headers=headers, json={
        "title": "无群公告", "content": "内容", "type": "info"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tg_sent"] is False
    assert tg_calls == []
    assert _station_count(db) == 0
    db.expire_all()
    assert db.query(models.Announcement).count() == 1


def test_create_announcement_tg_failure_still_succeeds(db, monkeypatch):
    def _fail(db_, chat_id, text, **kwargs):
        return False, "Telegram API 错误"

    monkeypatch.setattr("backend.tg_bot.sender.send_message", _fail)
    client = _client(db)
    admin = _user(db, staff=True)
    _set_group(db)
    headers = _auth_header(client, admin)

    res = client.post("/api/admin/announcements", headers=headers, json={
        "title": "发送失败公告", "content": "内容", "type": "info"})
    assert res.status_code == 200, res.text
    assert res.json()["tg_sent"] is False
    assert _station_count(db) == 0


def test_update_announcement_goes_to_tg_not_station(db, tg_calls):
    client = _client(db)
    admin = _user(db, staff=True)
    _user(db)
    _set_group(db)
    ann = models.Announcement(title="旧标题", content="旧内容",
                              type="info", is_active=True)
    db.add(ann)
    db.commit()
    headers = _auth_header(client, admin)

    res = client.put(f"/api/admin/announcements/{ann.id}", headers=headers,
                     json={"title": "新标题", "content": "新内容"})
    assert res.status_code == 200, res.text
    assert res.json()["tg_sent"] is True
    assert _station_count(db) == 0
    assert len(tg_calls) == 1
    assert tg_calls[0]["chat_id"] == GROUP_ID
    assert "新标题" in tg_calls[0]["text"]
    assert "新内容" in tg_calls[0]["text"]


def test_update_announcement_without_group_still_succeeds(db, tg_calls):
    client = _client(db)
    admin = _user(db, staff=True)
    ann = models.Announcement(title="旧标题", content="旧内容",
                              type="info", is_active=True)
    db.add(ann)
    db.commit()
    headers = _auth_header(client, admin)

    res = client.put(f"/api/admin/announcements/{ann.id}", headers=headers,
                     json={"title": "改个标题"})
    assert res.status_code == 200, res.text
    assert res.json()["tg_sent"] is False
    assert tg_calls == []
    assert _station_count(db) == 0


# ------------------------------------------------------------ 弹幕掩码

_DANMU_KEYS = ("danmu_api_lines", "danmu_api_external_enabled",
               "danmu_api_external_url")


def _danmu_invalidate():
    from backend import danmu_api as _dm
    from backend.integrations import store

    store.invalidate(*_DANMU_KEYS)
    _dm._ext_cache.clear()


def _danmu_admin(db):
    return _user(db, staff=True)


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "PUT", "path": "/",
                    "client": ("127.0.0.1", 12345), "headers": []})


_LINES = [{"id": "intl", "name": "国际方向", "note": "",
           "host": "cdn.aetrix.love"}]


def _danmu_set_url(db, admin, url):
    from backend.api.admin_danmu_api import (
        DanmuLinesRequest,
        danmu_lines_update_config,
    )

    _danmu_invalidate()
    return danmu_lines_update_config(
        DanmuLinesRequest(lines=_LINES, external_enabled=True,
                          external_url=url),
        _request(), admin, db)


def test_danmu_get_masks_external_url(db):
    from backend import danmu_api as core_api
    from backend.api.admin_danmu_api import danmu_lines_get_config

    admin = _danmu_admin(db)
    _danmu_set_url(db, admin, REAL_URL)
    _danmu_invalidate()
    out = danmu_lines_get_config(admin, db)
    assert out["external_url"] == "******"
    assert out["external_url_set"] is True
    assert REAL_URL not in str(out)
    assert "tok123secret" not in str(out)
    # 库里仍是真值（用户端外部源照常工作）
    _danmu_invalidate()
    assert core_api.external_url(db) == REAL_URL


def test_danmu_get_unset_external_url(db):
    from backend.api.admin_danmu_api import danmu_lines_get_config

    admin = _danmu_admin(db)
    _danmu_invalidate()
    out = danmu_lines_get_config(admin, db)
    assert out["external_url"] == ""
    assert out["external_url_set"] is False


def test_danmu_put_mask_preserves_original(db):
    from backend import danmu_api as core_api
    from backend.api.admin_danmu_api import (
        DanmuLinesRequest,
        danmu_lines_update_config,
    )

    admin = _danmu_admin(db)
    _danmu_set_url(db, admin, REAL_URL)
    out = danmu_lines_update_config(
        DanmuLinesRequest(lines=_LINES, external_url="******"),
        _request(), admin, db)
    assert out["external_url"] == "******"
    assert out["external_url_set"] is True
    _danmu_invalidate()
    assert core_api.external_url(db) == REAL_URL


def test_danmu_put_new_url_updates(db):
    from backend import danmu_api as core_api
    from backend.api.admin_danmu_api import (
        DanmuLinesRequest,
        danmu_lines_get_config,
        danmu_lines_update_config,
    )

    admin = _danmu_admin(db)
    _danmu_set_url(db, admin, REAL_URL)
    danmu_lines_update_config(
        DanmuLinesRequest(lines=_LINES, external_url=NEW_URL + "/"),
        _request(), admin, db)
    _danmu_invalidate()
    assert core_api.external_url(db) == NEW_URL
    out = danmu_lines_get_config(admin, db)
    assert out["external_url"] == "******"
    assert out["external_url_set"] is True


# ------------------------------------------------------------ 交换码重放

def _magic_code(client, headers):
    res = client.post("/api/admin/auth/magic-link/generate", json={},
                      headers=headers)
    assert res.status_code == 200, res.text
    key = res.json()["url"].split("key=", 1)[1]
    follow = client.get(f"/api/admin/magic-login?key={key}",
                        follow_redirects=False)
    assert follow.status_code == 302, follow.text
    return follow.headers["location"].split("#magic=", 1)[1]


def test_magic_exchange_replay_after_used_at_set(db):
    """used_at 已被（并发地）置位后，同一码再交换必须 401。"""
    client = _client(db)
    admin = _user(db, staff=True)
    headers = _auth_header(client, admin)
    code = _magic_code(client, headers)

    row = db.query(models.AdminMagicExchangeCode).filter_by(
        admin_user_id=admin.id).one()
    row.used_at = datetime.now()
    db.commit()

    res = client.post("/api/admin/auth/magic-exchange", json={"code": code})
    assert res.status_code == 401


def test_magic_exchange_second_use_rejected(db):
    client = _client(db)
    admin = _user(db, staff=True)
    headers = _auth_header(client, admin)
    code = _magic_code(client, headers)

    first = client.post("/api/admin/auth/magic-exchange", json={"code": code})
    assert first.status_code == 200, first.text
    second = client.post("/api/admin/auth/magic-exchange", json={"code": code})
    assert second.status_code == 401
