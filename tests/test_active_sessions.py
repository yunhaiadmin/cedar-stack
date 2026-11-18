# -*- coding: utf-8 -*-
"""活跃会话合并模型测试（2026-11-18 UHDNow 差异定案 G-60/G-62~G-66）。

隔离：内存 SQLite + 最小 FastAPI 应用（auth_router + security_router），
不碰开发库/生产库。限流滑窗在 fixture 里清零防用例互踩。

覆盖：
1. 三类合并：网页登录（refresh 家族行）＋媒体客户端设备（user_devices）
   ＋弹幕 Token（平台 API Key），计数 all/web/danmaku；
2. 上限 N 读全站 device_limit_per_user（代码里没有套餐级设备上限权益键；
   订阅页已明文「登录设备上限是全站口径」——定案说的「套餐 10」在代码
   里无此口径，故以现行全站配置为准，见报告）；
3. 登录建网页会话行（UA/IP、到期＝登录＋30 天）、refresh 刷新最近活动与到期；
4. 上限淘汰：新登录超限时淘汰最久未活动的未锁定会话，锁定者不被淘汰；
5. 锁定/改名/删除：网页会话删＝吊销整个 refresh 家族（同家族 refresh 再换
   401、其它家族不受影响）；设备删＝移除设备＋吊销其令牌；
6. 弹幕 Token＝平台 API Key（唯一满足 G-66 的既有 Token 体系：有效期默认
   365 天、可改名、可作废）：有效期＝签发＋1 年、只有编辑/删除、无锁定；
7. 设备到期＝其最新未吊销 Emby 令牌的 expires_at；改密/登出 bump
   token_version 后旧会话行不再列出（剪枝）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import account_security as sec
from backend import models, ratelimit
from backend.api import account_security as sec_api
from backend.api import emby_portal
from backend.database import get_db
from backend.emby_server import models as em
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


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


def _client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.include_router(sec_api.security_router)
    app.include_router(sec_api.platform_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, password=None):
    # 密码不写字面量：随机生成后挂在实例上（写盘层会把 password 字面赋值
    # 脱敏写坏，见 AGENTS.md 2026-10-30 条；此处彻底避开该写法）。
    password = password or ("pw-" + uuid.uuid4().hex)
    u = models.WebUser(
        username="sess_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(password),
        email="sess@example.com",
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    u._test_password = password
    return u


def _auth_headers(user) -> dict:
    token = create_access_token(
        user.id, token_version=user.token_version or 0)
    return {"Authorization": f"Bearer {token}"}


def _set_config(db, key: str, value: str) -> None:
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if row is None:
        db.add(models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _web_session(db, user, fid=None, **kw):
    now = datetime.now()
    row = models.UserWebSession(
        user_id=user.id,
        fid=fid or secrets.token_hex(16),
        display_name=kw.get("display_name"),
        user_agent=kw.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
        ip=kw.get("ip", "1.2.3.4"),
        created_at=kw.get("created_at", now - timedelta(days=1)),
        last_seen_at=kw.get("last_seen_at", now - timedelta(hours=1)),
        expires_at=kw.get("expires_at", now + timedelta(days=29)),
        token_version=kw.get("token_version", user.token_version or 0),
        locked=kw.get("locked", False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _device(db, user, device_id=None, **kw):
    row = models.UserDevice(
        user_id=user.id,
        device_id=device_id or ("dev-" + uuid.uuid4().hex[:8]),
        name=kw.get("name", "客厅电视"),
        client=kw.get("client", "Infuse"),
        app_version=kw.get("app_version", "7.0"),
        ip=kw.get("ip", "5.6.7.8"),
        first_seen_at=kw.get("first_seen_at", datetime.now() - timedelta(days=3)),
        last_seen_at=kw.get("last_seen_at", datetime.now() - timedelta(hours=2)),
        is_blocked=kw.get("is_blocked", False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _danmaku_token(db, user, name="弹幕 Token"):
    """直接落平台 API Key 行（绕开创建前置的强因子校验；前置另有测试覆盖）。
    有效期＝签发＋365 天，与 sec.create_platform_api_key 同口径。"""
    now = datetime.now()
    base = now - timedelta(days=2)
    row = models.UserPlatformApiKey(
        user_id=user.id,
        key_id="pak_" + uuid.uuid4().hex[:12],
        key_hash="0" * 64,
        name=name,
        expires_at=base + timedelta(days=365),
        is_active=True,
        last_used_at=now - timedelta(hours=3),
        created_at=base,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _login(client, user, ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"):
    r = client.post(
        "/api/user/auth/login",
        json={"username": user.username, "password": user._test_password},
        headers={"User-Agent": ua},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _list(client, user):
    r = client.get("/api/user/security/sessions", headers=_auth_headers(user))
    assert r.status_code == 200, r.text
    return r.json()

# ==================== 三类合并与计数 ====================

def test_sessions_merge_three_kinds_and_counts(db):
    client = _client(db)
    user = _user(db)
    ws = _web_session(db, user)
    dev = _device(db, user)
    tok = _danmaku_token(db, user)
    body = _list(client, user)
    kinds = {it["kind"] for it in body["items"]}
    assert kinds == {"web", "device", "danmaku"}
    assert body["counts"] == {"all": 3, "web": 1, "danmaku": 1}
    by_kind = {it["kind"]: it for it in body["items"]}
    assert by_kind["web"]["id"] == f"web:{ws.fid}"
    assert by_kind["web"]["user_agent"].startswith("Mozilla/5.0")
    assert by_kind["web"]["expires_at"] is not None
    assert by_kind["web"]["can_lock"] is True
    assert by_kind["device"]["id"] == f"device:{dev.device_id}"
    assert by_kind["device"]["can_lock"] is True
    assert by_kind["danmaku"]["id"] == f"danmaku:{tok.id}"
    assert by_kind["danmaku"]["can_lock"] is False  # G-66：弹幕 Token 无锁定
    assert by_kind["danmaku"]["locked"] is False


def test_sessions_limit_reads_device_limit_config(db):
    client = _client(db)
    user = _user(db)
    assert _list(client, user)["limit"] == 0  # 未配＝不限
    _set_config(db, "device_limit_per_user", "10")
    assert _list(client, user)["limit"] == 10


def test_sessions_other_users_invisible(db):
    client = _client(db)
    user = _user(db)
    other = _user(db)
    ws = _web_session(db, other)
    body = _list(client, user)
    assert body["items"] == []
    r = client.delete(f"/api/user/security/sessions/web:{ws.fid}",
                      headers=_auth_headers(user))
    assert r.status_code == 404


# ==================== 登录建会话 / refresh 刷新 ====================

def test_login_records_web_session(db):
    client = _client(db)
    user = _user(db)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
    tokens = _login(client, user, ua=ua)
    body = _list(client, user)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == "web"
    assert item["user_agent"] == ua
    assert item["ip"] is not None
    exp = datetime.fromisoformat(item["expires_at"])
    assert timedelta(days=29) < exp - datetime.now() < timedelta(days=31)


def test_refresh_touches_web_session(db):
    client = _client(db)
    user = _user(db)
    tokens = _login(client, user)
    before = _list(client, user)["items"][0]
    old_seen = datetime.fromisoformat(before["last_seen_at"])
    r = client.post("/api/user/auth/refresh",
                    json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    after = _list(client, user)["items"][0]
    assert after["id"] == before["id"]  # 同家族，不另起一行
    assert datetime.fromisoformat(after["last_seen_at"]) >= old_seen
    assert datetime.fromisoformat(after["expires_at"]) > datetime.fromisoformat(
        before["expires_at"])


# ==================== 上限淘汰与锁定 ====================

def test_eviction_oldest_unlocked_on_login(db):
    client = _client(db)
    user = _user(db)
    _set_config(db, "device_limit_per_user", "2")
    old_ws = _web_session(db, user, last_seen_at=datetime.now() - timedelta(days=5))
    old_fid = old_ws.fid  # 先取值：淘汰会删行，之后再读实例会报 ObjectDeletedError
    _device(db, user, last_seen_at=datetime.now() - timedelta(days=1))
    _login(client, user)
    body = _list(client, user)
    assert len(body["items"]) == 2
    ids = {it["id"] for it in body["items"]}
    assert f"web:{old_fid}" not in ids  # 最久未活动的被淘汰
    assert any(it["kind"] == "web" and it["id"] != f"web:{old_fid}"
               for it in body["items"])


def test_locked_session_survives_eviction(db):
    client = _client(db)
    user = _user(db)
    _set_config(db, "device_limit_per_user", "2")
    locked_ws = _web_session(db, user, locked=True,
                             last_seen_at=datetime.now() - timedelta(days=9))
    locked_fid = locked_ws.fid
    dev = _device(db, user, last_seen_at=datetime.now() - timedelta(days=1))
    _login(client, user)
    body = _list(client, user)
    ids = {it["id"] for it in body["items"]}
    assert f"web:{locked_fid}" in ids  # 锁定者不被新登录挤掉
    assert f"device:{dev.device_id}" not in ids


def test_lock_toggle_roundtrip(db):
    client = _client(db)
    user = _user(db)
    ws = _web_session(db, user)
    r = client.post(f"/api/user/security/sessions/web:{ws.fid}/lock",
                    headers=_auth_headers(user), json={"locked": True})
    assert r.status_code == 200, r.text
    assert r.json()["locked"] is True
    assert _list(client, user)["items"][0]["locked"] is True
    r = client.post(f"/api/user/security/sessions/web:{ws.fid}/lock",
                    headers=_auth_headers(user), json={"locked": False})
    assert r.json()["locked"] is False

# ==================== 改名 / 删除（吊销） ====================

def test_rename_each_kind(db):
    client = _client(db)
    user = _user(db)
    ws = _web_session(db, user)
    dev = _device(db, user)
    tok = _danmaku_token(db, user)
    h = _auth_headers(user)
    r = client.patch(f"/api/user/security/sessions/web:{ws.fid}",
                     headers=h, json={"display_name": "家里电脑"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "家里电脑"
    r = client.patch(f"/api/user/security/sessions/device:{dev.device_id}",
                     headers=h, json={"display_name": "卧室电视"})
    assert r.status_code == 200, r.text
    assert db.query(models.UserDevice).filter(
        models.UserDevice.id == dev.id).first().name == "卧室电视"
    r = client.patch(f"/api/user/security/sessions/danmaku:{tok.id}",
                     headers=h, json={"display_name": "播放器弹幕"})
    assert r.status_code == 200, r.text
    assert db.query(models.UserPlatformApiKey).filter(
        models.UserPlatformApiKey.id == tok.id).first().name == "播放器弹幕"


def test_revoke_web_session_revokes_family(db):
    client = _client(db)
    user = _user(db)
    tokens = _login(client, user)
    tokens2 = _login(client, user)
    assert len(_list(client, user)["items"]) == 2
    # 列表按最近活动倒序：首项是后一次登录的家族
    first = _list(client, user)["items"][0]
    r = client.delete(f"/api/user/security/sessions/{first['id']}",
                      headers=_auth_headers(user))
    assert r.status_code == 200, r.text
    assert len(_list(client, user)["items"]) == 1
    # 被吊销家族的 refresh 再换 401；另一家族不受影响
    r = client.post("/api/user/auth/refresh",
                    json={"refresh_token": tokens2["refresh_token"]})
    assert r.status_code == 401
    r = client.post("/api/user/auth/refresh",
                    json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text


def test_delete_device_removes_it_and_revokes_tokens(db):
    client = _client(db)
    user = _user(db)
    dev = _device(db, user)
    from backend.emby_server.auth import hash_emby_token
    plain = secrets.token_hex(16)
    tok_row = em.EmbyApiToken(
        token=hash_emby_token(plain), user_id=user.id,
        device_id=dev.device_id, app_name="Infuse",
        expires_at=datetime.now() + timedelta(days=30))
    db.add(tok_row)
    db.commit()
    r = client.delete(f"/api/user/security/sessions/device:{dev.device_id}",
                      headers=_auth_headers(user))
    assert r.status_code == 200, r.text
    assert db.query(models.UserDevice).filter(
        models.UserDevice.id == dev.id).first() is None
    assert db.query(em.EmbyApiToken).filter(
        em.EmbyApiToken.id == tok_row.id).first().is_revoked is True


def test_delete_danmaku_token_revokes_it(db):
    client = _client(db)
    user = _user(db)
    tok = _danmaku_token(db, user)
    r = client.delete(f"/api/user/security/sessions/danmaku:{tok.id}",
                      headers=_auth_headers(user))
    assert r.status_code == 200, r.text
    assert _list(client, user)["items"] == []
    assert db.query(models.UserPlatformApiKey).filter(
        models.UserPlatformApiKey.id == tok.id).first().is_active is False


# ==================== 弹幕 Token：1 年有效期、无锁定 ====================

def test_danmaku_token_one_year_and_no_lock(db):
    client = _client(db)
    user = _user(db)
    tok = _danmaku_token(db, user)
    item = _list(client, user)["items"][0]
    assert item["kind"] == "danmaku"
    created = datetime.fromisoformat(item["created_at"])
    expires = datetime.fromisoformat(item["expires_at"])
    assert timedelta(days=364) < expires - created < timedelta(days=366)
    r = client.post(f"/api/user/security/sessions/danmaku:{tok.id}/lock",
                    headers=_auth_headers(user), json={"locked": True})
    assert r.status_code == 400  # G-66：弹幕 Token 无锁定


# ==================== 设备到期与剪枝 ====================

def test_device_expiry_from_emby_token(db):
    client = _client(db)
    user = _user(db)
    dev = _device(db, user)
    from backend.emby_server.auth import hash_emby_token
    exp = datetime.now() + timedelta(days=17)
    db.add(em.EmbyApiToken(
        token=hash_emby_token(secrets.token_hex(16)), user_id=user.id,
        device_id=dev.device_id, app_name="Infuse", expires_at=exp))
    db.commit()
    item = _list(client, user)["items"][0]
    assert item["kind"] == "device"
    got = datetime.fromisoformat(item["expires_at"])
    assert abs((got - exp).total_seconds()) < 5


def test_stale_sessions_pruned_after_token_version_bump(db):
    client = _client(db)
    user = _user(db)
    _web_session(db, user, token_version=0)
    user.token_version = 1  # 改密/登出后旧家族全作废
    db.commit()
    assert _list(client, user)["items"] == []
    assert db.query(models.UserWebSession).filter(
        models.UserWebSession.user_id == user.id).count() == 0
