# -*- coding: utf-8 -*-
"""账号统一回归：客户端登录只认网站账号（username + password_hash）。

用户 2026-11-19 亲令：删除 Emby 接线凭据概念，客户端与网站共用
一套账号密码。覆盖：

(a) 从没设过接线凭据的用户，用网站名 + 网站密码能登客户端
    （改造前 emby_password 为空 → 恒 401，这批人占生产 850 人）；
(b) 设过不同播放密码的用户：播放密码登不上、网站密码能登；
    接线名作为查名别名过渡保留（删列收口时才去掉）；
(c) 改造前签发的令牌在接线凭据列清空后照常 resolve
    （令牌只认 user_id，绝不批量吊销）；
(d) POST /api/user/emby/password 已下线（404）；
(e) 网站改密不再写 emby_password 列，令牌吊销行为不变；
(f) 门户登录不再补填 emby_password；
(g) 账号卡下发网站用户名、has_password 看 password_hash、
    导入 scheme 用网站名拼。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-acct-unify-key-32chars-minimum")

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import models, ratelimit
from backend.security import hash_password, verify_password

# secret-scan: allow — 测试夹具假密码，只进内存 SQLite 库
PORTAL_PW = "Portal-Pass-123"
PLAY_PW = "Play-Pass-456"
NEW_PW = "New-Portal-789"


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


@pytest.fixture(autouse=True)
def _clear_state():
    ratelimit._limiter._hits.clear()
    ratelimit._login_lockout_guard.clear()
    yield
    ratelimit._limiter._hits.clear()
    ratelimit._login_lockout_guard.clear()


def _user(db, portal_pw=PORTAL_PW, **kw):
    u = models.WebUser(
        username="au_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(portal_pw),
        email=None,
        is_active=True,
        token_version=0,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _req(extra_headers=()):
    headers = [(b"x-emby-authorization",
                b'MediaBrowser Client="UnifyTest", Device="Phone", '
                b'DeviceId="unify-dev-1", Version="1.0"')]
    headers.extend(extra_headers)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/emby/Users/AuthenticateByName",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 54321),
    }
    return Request(scope)


def _auth(db, username, pw):
    from backend.emby_server import api as emby_api

    ratelimit._limiter._hits.clear()
    return emby_api.authenticate_by_name(_req(), {"Username": username, "Pw": pw}, db)


def _subscribe(db, user):
    from backend import realms

    realm = realms.ensure_default_realm(db)
    plan = models.SubscriptionPlan(
        name=f"月卡-{user.id}", price=1, duration_days=30, realm_id=realm.id,
    )
    db.add(plan)
    db.flush()
    db.add(models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm.id, status="active",
        end_date=datetime.now() + timedelta(days=30),
    ))
    db.commit()


# ==================== (a) 无接线凭据：网站密码直登 ====================

def test_no_emby_credentials_login_with_portal_password(db):
    user = _user(db)
    assert user.emby_username is None and user.emby_password is None
    out = _auth(db, user.username, PORTAL_PW)
    assert isinstance(out, dict) and out.get("AccessToken"), out
    assert out["User"]["Name"] == user.username
    assert out["SessionInfo"]["UserName"] == user.username


def test_no_emby_credentials_wrong_password_still_401(db):
    user = _user(db)
    out = _auth(db, user.username, "not-the-password")
    assert getattr(out, "status_code", None) == 401


# ==================== (b) 播放密码失效、网站密码生效 ====================

def test_legacy_play_password_rejected_portal_password_works(db):
    user = _user(db)
    user.emby_username = "legacy_play_name"
    user.emby_password = hash_password(PLAY_PW)
    db.commit()

    # 旧播放密码：改造前能登，改造后必须 401
    out = _auth(db, "legacy_play_name", PLAY_PW)
    assert getattr(out, "status_code", None) == 401
    out = _auth(db, user.username, PLAY_PW)
    assert getattr(out, "status_code", None) == 401

    # 网站密码：网站名能登；接线名别名过渡保留、同样只认网站密码
    out = _auth(db, user.username, PORTAL_PW)
    assert isinstance(out, dict) and out.get("AccessToken"), out
    assert out["User"]["Name"] == user.username
    out = _auth(db, "legacy_play_name", PORTAL_PW)
    assert isinstance(out, dict) and out.get("AccessToken"), out
    # 返回名一律网站名，不再下发接线名
    assert out["User"]["Name"] == user.username
    assert out["SessionInfo"]["UserName"] == user.username


# ==================== (c) 已签发令牌照常 resolve ====================

def test_preissued_token_survives_credentials_wipe(db):
    from backend.emby_server.auth import issue_token, resolve_token

    user = _user(db)
    user.emby_username = "legacy_play_name"
    user.emby_password = hash_password(PLAY_PW)
    db.commit()
    token_value, row = issue_token(db, user, _req())
    assert row.is_revoked is False

    # 模拟删列收口后的状态：两列清空，令牌不得受影响
    user.emby_username = None
    user.emby_password = None
    db.commit()

    req = _req(extra_headers=[(b"x-emby-token", token_value.encode())])
    resolved = resolve_token(db, req)
    assert resolved is not None
    resolved_user, resolved_row = resolved
    assert resolved_user.id == user.id
    assert resolved_row.id == row.id
    assert resolved_row.is_revoked is False


# ==================== (d) 设接线密码端点下线 ====================

def test_set_emby_password_endpoint_gone(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.emby_server.portal import user_emby_router

    paths = {route.path for route in user_emby_router.routes}
    assert "/api/user/emby/password" not in paths
    assert "/api/user/emby/server" in paths  # 账号卡路由仍在

    app = FastAPI()
    app.include_router(user_emby_router)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/user/emby/password",
                    json={"username": "abc_def", "password": "secret123"})
    assert r.status_code == 404


# ==================== (e) 改密不再写接线密码列 ====================

def test_password_change_does_not_write_emby_password(db):
    from backend.api.emby_portal import _apply_password_change
    from backend.emby_server import models as em
    from backend.emby_server.auth import issue_token

    user = _user(db)
    old_play_hash = hash_password(PLAY_PW)
    user.emby_username = "legacy_play_name"
    user.emby_password = old_play_hash
    db.commit()
    _, row = issue_token(db, user, _req())

    _apply_password_change(db, user, NEW_PW)
    db.commit()
    db.refresh(user)
    assert verify_password(NEW_PW, user.password_hash)
    # 接线密码列一字未动（删列收口前保持存量原样）
    assert user.emby_password == old_play_hash
    # 令牌吊销行为不变：改密仍吊销全部客户端令牌
    db.refresh(row)
    assert row.is_revoked is True
    assert db.query(em.EmbyApiToken).filter(
        em.EmbyApiToken.user_id == user.id,
        em.EmbyApiToken.is_revoked == False,  # noqa: E712
    ).count() == 0


# ==================== (f) 门户登录不再补填 ====================

def test_portal_login_no_longer_fills_emby_password(db):
    from backend.api.emby_portal import _issue_auth_response

    user = _user(db)
    user.emby_username = "legacy_play_name"
    db.commit()
    out = _issue_auth_response(user, db, plain_password=PORTAL_PW)
    assert out.access_token
    db.refresh(user)
    assert user.emby_password is None
    # UserOut 不再下发接线名（字段删除或停下发都算收口）
    assert getattr(out.user, "emby_username", None) is None


# ==================== (g) 账号卡新口径 ====================

def test_account_card_uses_portal_username(db, monkeypatch):
    monkeypatch.setenv("EMBY_PUBLIC_URL", "http://media.example.com:8000")
    from backend.emby_server.portal import _account_card

    user = _user(db)
    user.emby_username = "legacy_play_name"
    user.emby_password = hash_password(PLAY_PW)
    db.commit()
    _subscribe(db, user)

    card = _account_card(user, db)
    assert card["emby_username"] == user.username
    assert card["emby_password"] is None
    # has_password 看网站密码：本站用户恒有网站密码
    assert card["has_password"] is True
    import json as _json
    blob = _json.dumps(card["import_schemes"])
    assert user.username in blob
    assert "legacy_play_name" not in blob

