# -*- coding: utf-8 -*-
"""贡献池专属授权入口（2026-11-24）：绑盘下线后入池的唯一授权腿。

钉死的范围：
- 池专属 OAuth：start 建带 purpose='pool_join' 标记的 state 行 +
  返回 Google 授权地址（PKCE S256 + offline + consent）；callback
  经浏览器会话校验（nonce + 会话用户）后单次验讫换 token + 验活
  建绑定行；三档失败落失败态行、一过性失败不落行、state 复用/
  过期/异用途一律拒绝；
- 池流不归绑盘开关管：drive_binding_enabled 恒 False 时照常发起
  （开关只管已下线的通用绑盘）；
- 授权只落绑定行，入池仍走既有 apply 口与容量双闸（申请闸 +
  批准闸），本文件把全程串起来验一遍；
- Google / Drive 网络全部用桩替身，不碰真实上游；令牌原文不出
  响应、不入日志的口径由序列化层既有测试钉，本文件只验落点。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse, parse_qs

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_service, models as base_models
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import pool_gate, user_bindings as ub
from backend.emby_server import transfer_variant as tv
from backend.integrations import store

FULL_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/drive.file")
FIVE_T_LIMIT = 5_497_558_138_880


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


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    monkeypatch.setenv("DRIVE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("DRIVE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://aetrix.example")
    monkeypatch.delenv("DRIVE_OAUTH_INCLUDE_READONLY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _clean_store_cache():
    yield
    store.invalidate()
    pool_gate.clear_pool_quota_cache()


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    return user


def _set_pool(db, **values):
    config_service.save_config(db, values, schema="pool")
    db.commit()


def _stub_drive_limit(monkeypatch, limit):
    def fake_about_storage(self, drive_id):
        return {"limit": limit, "usage": 0,
                "free": limit if limit is not None else None}

    monkeypatch.setattr(
        tv.UserDriveApiClient, "about_storage", fake_about_storage)
    pool_gate.clear_pool_quota_cache()


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")

    @property
    def text(self):
        return self._payload if isinstance(self._payload, str) else ""


def _token_post(behavior="ok"):
    """token 端点桩：ok / invalid_client / invalid_grant / transient /
    no_refresh。ok 时带实授范围（仅 drive.file）。"""
    def _post(url, data=None, timeout=30):
        if behavior == "invalid_client":
            return _FakeResp(401, {"error": "invalid_client"})
        if behavior == "invalid_grant":
            return _FakeResp(400, {"error": "invalid_grant"})
        if behavior == "transient":
            return _FakeResp(500, {"error": "server_error"})
        payload = {"access_token": "AT-CODE", "expires_in": 3600,
                   "token_type": "Bearer",
                   "scope": "https://www.googleapis.com/auth/drive.file"}
        if behavior != "no_refresh":
            payload["refresh_token"] = "RT-CODE"
        return _FakeResp(200, payload)
    return _post


def _about_get(behavior="ok"):
    def _get(url, headers=None, timeout=30):
        if behavior == "unauthorized":
            return _FakeResp(401, {"error": {"code": 401, "message": "bad"}})
        return _FakeResp(200, {"user": {"emailAddress": "u@example.com"}})
    return _get


def _state_of(authorization_url: str) -> str:
    qs = parse_qs(urlparse(authorization_url).query)
    return qs["state"][0]


def _start(db, user_id, label="贡献盘"):
    from backend.emby_server import pool_oauth

    started = pool_oauth.start_pool_oauth(db, user_id, label)
    return started, _state_of(started["authorization_url"])


# ------------------------------------------------------------ start

def test_start_creates_pool_state_row_and_url(db):
    from backend.emby_server import pool_oauth
    from backend.emby_server.secret_sealing import unseal_secret

    user = _user(db, "starter")
    started, state = _start(db, user.id)
    url = started["authorization_url"]
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["test-client-id"]
    assert qs["redirect_uri"] == [
        "https://aetrix.example" + pool_oauth.POOL_OAUTH_CALLBACK_PATH]
    # 默认只请求池取流所需的 drive.file（readonly 是受限范围、
    # 谷歌验证未过，旧口径默认不请求，不扩权）。
    assert qs["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]
    assert started["_oauth_nonce"]

    row = db.query(em.DriveOAuthState).one()
    assert row.user_id == user.id
    assert row.purpose == "pool_join"
    assert row.oauth_client == "primary"
    assert row.nonce_hash
    assert row.expires_at > datetime.now()
    # PKCE verifier 只密封存，原文不入库。
    assert unseal_secret(row.sealed_verifier)
    assert row.sealed_verifier != qs["code_challenge"][0]


def test_start_scopes_include_readonly_only_when_env_on(db, monkeypatch):
    monkeypatch.setenv("DRIVE_OAUTH_INCLUDE_READONLY", "1")
    user = _user(db, "scopes")
    started, _state = _start(db, user.id)
    qs = parse_qs(urlparse(started["authorization_url"]).query)
    assert qs["scope"] == [FULL_SCOPES]


def test_start_rejected_when_server_creds_missing(db, monkeypatch):
    from backend.emby_server import pool_oauth

    monkeypatch.delenv("DRIVE_OAUTH_CLIENT_ID")
    user = _user(db, "nocreds")
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.start_pool_oauth(db, user.id, "贡献盘")
    assert exc.value.kind == "validation"
    assert db.query(em.DriveOAuthState).count() == 0


def test_start_rejected_when_public_url_missing(db, monkeypatch):
    from backend.emby_server import pool_oauth

    monkeypatch.setenv("EMBY_PUBLIC_URL", "")
    user = _user(db, "nopub")
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.start_pool_oauth(db, user.id, "贡献盘")
    assert exc.value.kind == "validation"
    assert db.query(em.DriveOAuthState).count() == 0


def test_start_not_gated_by_drive_binding_switch(db):
    """红线：绑盘开关已收敛恒关，池专属授权不归它管、照常发起。"""
    assert ub.drive_binding_enabled(db) is False
    user = _user(db, "switchfree")
    started, _state = _start(db, user.id)
    assert started["authorization_url"]


def test_start_label_and_quota_validation(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "labelq")
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.start_pool_oauth(db, user.id, "  ")
    assert exc.value.kind == "validation"
    # 同 provider 同名已有绑定 → 拒绝（不落 state）。
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user.id,
        sealed_credential="", key_id="fernet-v1", status="active")
    db.add(cred)
    db.flush()
    db.add(em.UserStorageBinding(
        user_id=user.id, provider="drive", account_label="贡献盘",
        credential_id=cred.id, status="active"))
    db.commit()
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.start_pool_oauth(db, user.id, "贡献盘")
    assert exc.value.kind == "validation"
    assert db.query(em.DriveOAuthState).count() == 0


# ------------------------------------------------------------ complete

def test_complete_happy_path_creates_active_binding(db):
    from backend.emby_server import pool_oauth
    from backend.emby_server.drive_oauth import decode_drive_token
    from backend.emby_server.secret_sealing import unseal_secret

    user = _user(db, "finisher")
    _started, state = _start(db, user.id)
    binding = pool_oauth.complete_pool_oauth(
        db, state, "code-1", http_post=_token_post(),
        http_get=_about_get())
    assert binding.status == "active"
    assert binding.provider == "drive"
    assert binding.user_id == user.id
    assert binding.oauth_client == "primary"
    assert binding.granted_scopes == (
        "https://www.googleapis.com/auth/drive.file")
    assert binding.last_verified_at is not None
    cred = db.query(em.MountCredential).one()
    assert cred.scope == "user" and cred.owner_user_id == user.id
    token = decode_drive_token(unseal_secret(cred.sealed_credential))
    assert token["refresh_token"] == "RT-CODE"
    # state 单次有效：行已消费，同一 state 再完成按不存在拒绝。
    assert db.query(em.DriveOAuthState).count() == 0
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.complete_pool_oauth(
            db, state, "code-1", http_post=_token_post(),
            http_get=_about_get())
    assert exc.value.kind == "not_found"


def test_complete_rejects_foreign_purpose_state(db):
    """异用途 state 行（purpose 非 pool_join）池回调不许消费、
    也不许动它（防与任何其它授权流串用）。"""
    import hashlib

    from backend.emby_server import pool_oauth
    from backend.emby_server.secret_sealing import seal_secret

    user = _user(db, "foreign")
    state = "foreign-state-value"
    row = em.DriveOAuthState(
        state_hash=hashlib.sha256(state.encode()).hexdigest(),
        user_id=user.id, account_label="贡献盘",
        sealed_verifier=seal_secret("verifier-x"),
        expires_at=datetime.now() + timedelta(minutes=5),
        purpose=None)
    db.add(row)
    db.commit()
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.complete_pool_oauth(
            db, state, "code-1", http_post=_token_post(),
            http_get=_about_get())
    assert exc.value.kind == "not_found"
    assert db.query(em.DriveOAuthState).count() == 1
    assert db.query(em.UserStorageBinding).count() == 0


def test_complete_expired_state_rejected_and_burned(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "expired")
    _started, state = _start(db, user.id)
    row = db.query(em.DriveOAuthState).one()
    row.expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.complete_pool_oauth(
            db, state, "code-1", http_post=_token_post(),
            http_get=_about_get())
    assert exc.value.kind == "not_found"
    assert db.query(em.DriveOAuthState).count() == 0
    assert db.query(em.UserStorageBinding).count() == 0


def test_complete_auth_error_lands_failed_rows(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "autherr")
    _started, state = _start(db, user.id)
    binding = pool_oauth.complete_pool_oauth(
        db, state, "code-1", http_post=_token_post("invalid_client"),
        http_get=_about_get())
    assert binding.status == "invalid"
    assert binding.last_error_kind == "auth_error"
    assert db.query(em.UserStorageBinding).filter_by(
        status="active").count() == 0


def test_complete_missing_refresh_token_lands_failed_rows(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "norefresh")
    _started, state = _start(db, user.id)
    binding = pool_oauth.complete_pool_oauth(
        db, state, "code-1", http_post=_token_post("no_refresh"),
        http_get=_about_get())
    assert binding.status == "invalid"
    assert binding.last_error_kind == "auth_error"


def test_complete_liveness_failure_lands_failed_rows(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "deadlive")
    _started, state = _start(db, user.id)
    binding = pool_oauth.complete_pool_oauth(
        db, state, "code-1", http_post=_token_post(),
        http_get=_about_get("unauthorized"))
    assert binding.status == "expired"
    assert binding.last_error_kind == "expired"


def test_complete_transient_lands_no_rows(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "transient")
    _started, state = _start(db, user.id)
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.complete_pool_oauth(
            db, state, "code-1", http_post=_token_post("transient"),
            http_get=_about_get())
    assert exc.value.kind == "transient"
    assert db.query(em.UserStorageBinding).count() == 0
    assert db.query(em.MountCredential).count() == 0


# ------------------------------------------------- callback 会话校验

def test_callback_browser_binding_nonce_guard(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "nonce")
    started, state = _start(db, user.id)
    nonce = started["_oauth_nonce"]
    # 正确 nonce + 无会话（浏览器回跳常态）→ 放行。
    pool_oauth.assert_pool_callback_browser_binding(
        db, state, nonce, None)
    # 会话用户是本人 → 放行。
    pool_oauth.assert_pool_callback_browser_binding(
        db, state, nonce, user.id)
    # 会话用户是别人 → 拒绝并烧行。
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.assert_pool_callback_browser_binding(
            db, state, nonce, user.id + 999)
    assert exc.value.kind == "auth_error"
    assert db.query(em.DriveOAuthState).count() == 0


def test_callback_browser_binding_wrong_nonce_burns(db):
    from backend.emby_server import pool_oauth

    user = _user(db, "badnonce")
    _started, state = _start(db, user.id)
    with pytest.raises(pool_oauth.PoolOAuthError) as exc:
        pool_oauth.assert_pool_callback_browser_binding(
            db, state, "not-the-nonce", None)
    assert exc.value.kind == "auth_error"
    assert db.query(em.DriveOAuthState).count() == 0


# ------------------------------------------------------------ 路由全程

@pytest.fixture()
def user_client(db):
    from backend.emby_server.portal import get_admin_or_emby_user
    from backend.main import app

    holder = {}

    def _current_user():
        return holder["user"]

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_admin_or_emby_user] = _current_user
    try:
        yield TestClient(app, raise_server_exceptions=False), holder
    finally:
        app.dependency_overrides.clear()


def test_route_start_sets_nonce_cookie_and_hides_nonce(user_client, db):
    from backend.emby_server import pool_oauth

    client, holder = user_client
    holder["user"] = _user(db, "routestart")
    resp = client.post(
        "/api/user/emby/pool/drive/oauth/start",
        json={"account_label": "贡献盘"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorization_url"]
    assert "_oauth_nonce" not in body
    assert "nonce" not in body["authorization_url"]
    cookie = resp.headers.get("set-cookie", "")
    assert pool_oauth.OAUTH_NONCE_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert pool_oauth.POOL_OAUTH_CALLBACK_PATH in cookie


def test_route_callback_google_error(user_client, db):
    client, holder = user_client
    holder["user"] = _user(db, "gerr")
    resp = client.get(
        "/api/user/emby/pool/drive/oauth/callback",
        params={"error": "access_denied", "state": "x"})
    assert resp.status_code == 400


def test_route_callback_without_nonce_cookie_rejected(user_client, db):
    client, holder = user_client
    user = _user(db, "nocookie")
    holder["user"] = user
    started = client.post(
        "/api/user/emby/pool/drive/oauth/start",
        json={"account_label": "贡献盘"})
    state = _state_of(started.json()["authorization_url"])
    # 换一个没拿过 nonce cookie 的客户端模拟受害者浏览器回跳。
    from backend.main import app

    stranger = TestClient(app, raise_server_exceptions=False)
    resp = stranger.get(
        "/api/user/emby/pool/drive/oauth/callback",
        params={"state": state, "code": "code-1"})
    assert resp.status_code == 403
    assert db.query(em.DriveOAuthState).count() == 0
    assert db.query(em.UserStorageBinding).count() == 0


def test_route_full_join_flow(user_client, db, monkeypatch):
    """授权 → 绑定落行 → 申请（容量达标）→ 管理员批准 → active。"""
    from backend.emby_server import pool_oauth

    _stub_drive_limit(monkeypatch, FIVE_T_LIMIT)
    monkeypatch.setattr(pool_oauth, "_default_http_post", _token_post())
    monkeypatch.setattr(pool_oauth, "_default_http_get", _about_get())

    client, holder = user_client
    user = _user(db, "joinflow")
    holder["user"] = user

    started = client.post(
        "/api/user/emby/pool/drive/oauth/start",
        json={"account_label": "贡献盘"})
    assert started.status_code == 200, started.text
    state = _state_of(started.json()["authorization_url"])

    callback = client.get(
        "/api/user/emby/pool/drive/oauth/callback",
        params={"state": state, "code": "code-1"})
    assert callback.status_code == 200, callback.text
    binding_body = callback.json()
    assert binding_body["status"] == "active"
    assert binding_body["provider"] == "drive"
    binding_id = binding_body["id"]

    status = client.get("/api/user/emby/pool/contribution")
    entry = [b for b in status.json()["bindings"]
             if b["binding_id"] == binding_id][0]
    assert entry["can_apply"] is True
    assert entry["apply_block_reason"] is None

    applied = client.post(
        "/api/user/emby/pool/contribution/apply",
        json={"binding_id": binding_id})
    assert applied.status_code == 200, applied.text
    member = applied.json()["member"]
    assert member["status"] == "pending"

    approved = pool_gate.approve_pool_member(db, member["id"], actor_id=1)
    assert approved.status == "active"


def test_route_capacity_gate_blocks_apply(user_client, db, monkeypatch):
    """容量不达标：授权照常落绑定，但申请被双闸之一（申请闸）拦，
    状态口给出人话原因；批准闸同判（apply 过不去即无从批准）。"""
    from backend.emby_server import pool_oauth

    _stub_drive_limit(monkeypatch, 1_000_000_000_000)  # 1T < 5T 门槛
    monkeypatch.setattr(pool_oauth, "_default_http_post", _token_post())
    monkeypatch.setattr(pool_oauth, "_default_http_get", _about_get())

    client, holder = user_client
    user = _user(db, "smalldisk")
    holder["user"] = user

    started = client.post(
        "/api/user/emby/pool/drive/oauth/start",
        json={"account_label": "小盘"})
    state = _state_of(started.json()["authorization_url"])
    callback = client.get(
        "/api/user/emby/pool/drive/oauth/callback",
        params={"state": state, "code": "code-1"})
    assert callback.status_code == 200, callback.text
    binding_id = callback.json()["id"]

    status = client.get("/api/user/emby/pool/contribution")
    entry = [b for b in status.json()["bindings"]
             if b["binding_id"] == binding_id][0]
    assert entry["can_apply"] is False
    assert "容量" in (entry["apply_block_reason"] or "")

    applied = client.post(
        "/api/user/emby/pool/contribution/apply",
        json={"binding_id": binding_id})
    assert applied.status_code == 400
    assert "容量" in applied.json()["detail"]
    assert db.query(em.PoolMember).count() == 0


def test_route_start_unconfigured_is_loud(user_client, db, monkeypatch):
    client, holder = user_client
    holder["user"] = _user(db, "loud")
    monkeypatch.delenv("DRIVE_OAUTH_CLIENT_ID")
    resp = client.post(
        "/api/user/emby/pool/drive/oauth/start",
        json={"account_label": "贡献盘"})
    assert resp.status_code == 400
    assert "DRIVE_OAUTH" in resp.json()["detail"]
