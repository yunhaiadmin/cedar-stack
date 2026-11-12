"""老入口隐藏口径（2026-11-12 用户改令：删 → 隐藏）。

钉死口径：
- 入口清单/状态接口（providers）只对外给出新入口一份状态，
  不再宣称老入口可用于新绑；新入口的展示文案按单入口呈现
  （不再有「新入口/第二入口」这类双入口话术），但「仅限持有
  查看凭证」的限制说明保留；
- 老绑定一律不碰：oauth_client='primary' 与 NULL（存量老行）
  的刷新都继续按老应用凭据选客户端；老绑定的「重新授权」
  （rebind 走 portal 老 start 口）是存量维护，必须照常可用；
- 新入口发起流程不变（portal oauth2 start 口径与既有套件一致）。
Google / Drive 网络全部用桩替身，不碰真实上游。
"""

import time
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request as StarletteRequest

from backend import models as base_models
from backend import realms
from backend.emby_server import models as em
from backend.emby_server import drive_oauth as do
from backend.emby_server import portal
from backend.emby_server.upstream_sources import seal_secret
from backend.integrations import store


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    session.add(base_models.SystemConfig(
        key="drive_binding_enabled", value="true"))
    session.commit()
    store.invalidate()
    yield session
    session.close()
    store.invalidate()


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    monkeypatch.setenv("DRIVE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("DRIVE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://aetrix.example")
    monkeypatch.delenv("DRIVE_OAUTH2_CLIENT_ID", raising=False)
    monkeypatch.delenv("DRIVE_OAUTH2_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("DRIVE_OAUTH2_ENTRY_LABEL", raising=False)
    monkeypatch.delenv("DRIVE_OAUTH2_ENTRY_DESCRIPTION", raising=False)
    do._probe_thread = None
    yield
    do._probe_thread = None


@pytest.fixture()
def oauth2_env(monkeypatch):
    monkeypatch.setenv("DRIVE_OAUTH2_CLIENT_ID", "client2-id")
    monkeypatch.setenv("DRIVE_OAUTH2_CLIENT_SECRET", "client2-secret")


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


def _token_post_factory(behavior="ok", calls=None):
    def _post(url, data=None, timeout=30):
        if calls is not None:
            calls.append(data or {})
        if behavior == "invalid_grant":
            return _FakeResp(400, {"error": "invalid_grant"})
        grant = (data or {}).get("grant_type", "")
        if grant == "authorization_code":
            return _FakeResp(200, {"access_token": "AT-CODE",
                                   "refresh_token": "RT-CODE",
                                   "expires_in": 3600,
                                   "token_type": "Bearer"})
        return _FakeResp(200, {"access_token": "AT-NEW",
                               "refresh_token": "RT-NEW",
                               "expires_in": 3600,
                               "token_type": "Bearer"})
    return _post


def _about_get_factory():
    def _get(url, headers=None, timeout=30):
        return _FakeResp(200, {"user": {"emailAddress": "u@example.com"}})
    return _get


def _web_user(db, user_id):
    user = base_models.WebUser(
        id=user_id, username=f"hide-entry-user-{user_id}",
        password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    return user


def _grant_view_credential(db, user_id):
    user = _web_user(db, user_id)
    realm = base_models.ServerRealm(
        name="公益服", slug=f"free-hide-{user_id}", access_mode="free",
        is_active=True)
    db.add(realm)
    db.commit()
    realms.set_active_realm(db, realm.id)
    db.add(base_models.EmbyViewUnlock(
        user_id=user_id, realm_id=realm.id, unlocked_at=datetime.now(),
        expires_at=None, points_spent=0))
    db.commit()
    return user


def _state_of(started):
    query = parse_qs(urlparse(started["authorization_url"]).query)
    return unquote(query["state"][0])


def _http_request(host="aetrix.example"):
    scope = {"type": "http", "method": "POST", "path": "/",
             "headers": [(b"host", host.encode())], "query_string": b"",
             "scheme": "https", "server": (host, 443),
             "client": ("127.0.0.1", 40000)}
    return StarletteRequest(scope)


def _seed_binding(db, user_id=7, label="老盘", oauth_client=None,
                  expired=True):
    """直接造双行（存量老行口径：oauth_client 可为 NULL）。"""
    expires_at = int(time.time()) - 10 if expired else int(time.time()) + 3600
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential=seal_secret(do.encode_drive_token(
            "AT-OLD", "RT-OLD", expires_at)),
        key_id="fernet-v1", status="active")
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider="drive", account_label=label,
        credential_id=cred.id, status="active", oauth_client=oauth_client)
    db.add(binding)
    db.commit()
    return binding


def _bind_via_primary_service(db, user_id=7, label="老盘"):
    started = do.start_drive_oauth(db, user_id, label)
    return do.complete_drive_oauth(
        db, _state_of(started), "CODE-123",
        http_post=_token_post_factory(), http_get=_about_get_factory())


# ---------------------------------------------------------------------------
# ① 入口清单/状态口：只剩新入口，且文案按单入口呈现
# ---------------------------------------------------------------------------

def test_providers_advertises_only_new_entry(db, oauth2_env):
    _web_user(db, 8)
    result = portal.list_storage_binding_providers(
        SimpleNamespace(id=8), db)
    # 入口状态只有新入口一份：没有任何老入口状态对象对外宣称可用
    assert set(result.keys()) == {"providers", "drive_oauth2"}
    assert result["drive_oauth2"]["available"] is True


def test_entry_copy_presented_as_single_entry(db, oauth2_env):
    status = do.oauth2_entry_status(db, 8)
    assert status["label"] == "谷歌盘"
    assert "新入口" not in status["label"]
    assert "新入口" not in (status["description"] or "")
    assert "第二" not in (status["description"] or "")
    # 单入口呈现不许把资格限制说明也顺手删掉
    assert "查看凭证" in (status["description"] or "")


# ---------------------------------------------------------------------------
# ② 老绑定回归：刷新选客户端（primary / NULL 同口径）＋重新授权可用
# ---------------------------------------------------------------------------

def test_null_client_binding_refresh_uses_primary_client(db):
    _seed_binding(db, 7, oauth_client=None)
    calls = []
    token = do.get_user_drive_access_token(
        db, 7, http_post=_token_post_factory(calls=calls))
    assert token == "AT-NEW"
    refresh = [c for c in calls if c.get("grant_type") == "refresh_token"]
    assert len(refresh) == 1
    assert refresh[0]["client_id"] == "test-client-id"
    assert refresh[0]["client_secret"] == "test-client-secret"


def test_primary_binding_rebind_via_portal_start_still_works(db):
    binding = _bind_via_primary_service(db, 7, "老盘")
    assert (binding.oauth_client or "primary") == "primary"
    # 重新授权是存量维护：走 portal 老 start 口带 rebind 必须照常发起
    result = portal.start_drive_oauth_binding(
        portal.DriveOAuthStart(account_label="老盘",
                               rebind_binding_id=binding.id),
        _http_request(), Response(), SimpleNamespace(id=7), db)
    assert "client_id=test-client-id" in result["authorization_url"]
    calls = []
    out = do.complete_drive_oauth(
        db, _state_of(result), "CODE-456",
        http_post=_token_post_factory(calls=calls),
        http_get=_about_get_factory())
    assert out.id == binding.id
    assert (out.oauth_client or "primary") == "primary"
    assert db.query(em.UserStorageBinding).count() == 1
    exchange = [c for c in calls
                if c.get("grant_type") == "authorization_code"]
    assert exchange[0]["client_id"] == "test-client-id"


# ---------------------------------------------------------------------------
# ③ 新入口发起流程不变
# ---------------------------------------------------------------------------

def test_new_entry_portal_start_unchanged(db, oauth2_env):
    _grant_view_credential(db, 7)
    result = portal.start_drive_oauth2_binding(
        portal.DriveOAuthStart(account_label="新盘"),
        _http_request(), Response(), SimpleNamespace(id=7), db)
    assert "client_id=client2-id" in result["authorization_url"]
    row = db.query(em.DriveOAuthState).first()
    assert row is not None and row.oauth_client == "oauth2"
