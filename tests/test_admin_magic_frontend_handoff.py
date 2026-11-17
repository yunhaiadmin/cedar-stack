# -*- coding: utf-8 -*-
"""前台「管理后台」入口免密交接（2026-11-17 用户亲点「我能在前台进入」）：
管理员账号登录用户前台时，「我的」菜单里有一行「管理后台」，点一下免密
直进管理后台。本文件钉住后端交接口 POST /api/user/auth/admin-handoff：

- 鉴权走前台现行用户会话（与 /api/user/auth/me 同一个 get_current_user_jwt
  依赖、同一令牌签发口径），不新造第二套；
- 只有 is_staff 的账号能拿到交换码：非 staff 回 403 且不签发任何码、
  不落交接审计；未登录回 401；
- 签发复用 admin_magic 的交换码机制（同一张表、同一 60 秒口径），
  拿到的码经既有 magic-exchange 换出的会话与管理员密码登录同形状；
- 成功签发写 AdminLog 审计（action=admin_magic_frontend_handoff，
  与链接入口的 admin_magic_login 可区分）；
- 按 IP 限流 5 次/分钟（复用现行 ratelimit），超限回 429 且不签发。
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

from backend import models, ratelimit
from backend.api import admin_auth as _admin_auth  # noqa: F401 — 导入即注册全部管理端点（含免登录交换口）
from backend.api.admin_core import admin_router
from backend.api.emby_portal import auth_router
from backend.database import get_db
from backend.security import create_access_token, hash_password

PASSWORD = "handoff-pass-123"
HANDOFF_URL = "/api/user/auth/admin-handoff"


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
    # 限流器是进程级滑窗：不清的话本文件各用例会互相烧额度（与顺序耦合）。
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


def _client(db):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, *, staff: bool):
    u = models.WebUser(
        username="usr_" + uuid.uuid4().hex[:10],
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


def _user_token(user):
    # 与用户登录 _issue_auth_response 一字不差的签发口径
    return create_access_token(
        user.id, {"username": user.username},
        token_version=user.token_version or 0)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _handoff(client, token):
    return client.post(HANDOFF_URL, headers=_auth(token))


def _exchange(client, code):
    return client.post("/api/admin/auth/magic-exchange", json={"code": code})


def _code_rows(db, user_id):
    return db.query(models.AdminMagicExchangeCode).filter_by(
        admin_user_id=user_id).all()


def _handoff_audits(db, user_id):
    return db.query(models.AdminLog).filter_by(
        admin_user_id=user_id, action="admin_magic_frontend_handoff").all()


def test_staff_handoff_full_flow(db):
    """staff 前台会话 → 拿到 60 秒交换码 → 换出与密码登录同形状的管理员会话。"""
    client = _client(db)
    admin = _user(db, staff=True)

    res = _handoff(client, _user_token(admin))
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url.startswith("/admin/#magic=")
    code = url.split("#magic=", 1)[1]
    assert code

    # 签发落同一张交换码表、同一 60 秒口径
    rows = _code_rows(db, admin.id)
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert datetime.now() < rows[0].expires_at <= datetime.now() + timedelta(seconds=61)

    # 交接审计落账（与链接入口的 admin_magic_login 区分来源）
    assert len(_handoff_audits(db, admin.id)) == 1

    # 换出的会话与管理员密码登录同一响应形状（同一签发口径）
    login_payload = client.post(
        "/api/admin/auth/login",
        json={"username": admin.username, "password": PASSWORD}).json()
    exchanged = _exchange(client, code)
    assert exchanged.status_code == 200, exchanged.text
    payload = exchanged.json()
    assert set(payload.keys()) == set(login_payload.keys())
    assert set(payload["user"].keys()) == set(login_payload["user"].keys())
    assert payload["user"]["id"] == admin.id

    me = client.get("/api/admin/auth/me", headers=_auth(payload["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == admin.username

    # 交换码单次使用：同一个码再换一次被拒
    assert _exchange(client, code).status_code == 401


def test_non_staff_forbidden_and_issues_nothing(db):
    """非 staff 回 403：不签发任何交换码、不落交接审计。"""
    client = _client(db)
    plain = _user(db, staff=False)

    res = _handoff(client, _user_token(plain))
    assert res.status_code == 403, res.text
    assert _code_rows(db, plain.id) == []
    assert _handoff_audits(db, plain.id) == []


def test_anonymous_unauthorized(db):
    """没登录（无令牌 / 坏令牌）回 401，不签发。"""
    client = _client(db)
    assert client.post(HANDOFF_URL).status_code == 401
    assert _handoff(client, "not-a-token").status_code == 401
    assert db.query(models.AdminMagicExchangeCode).count() == 0


def test_rate_limit_blocks_and_issues_nothing_more(db):
    """同 IP 1 分钟内前 5 次放行，第 6 次 429 且不再签发新码。"""
    client = _client(db)
    admin = _user(db, staff=True)
    token = _user_token(admin)

    for _ in range(5):
        assert _handoff(client, token).status_code == 200
    assert len(_code_rows(db, admin.id)) == 5

    assert _handoff(client, token).status_code == 429
    assert len(_code_rows(db, admin.id)) == 5
    assert len(_handoff_audits(db, admin.id)) == 5

    # 额度恢复后立刻可用，证明挡的是限流不是账号坏了
    ratelimit._limiter._hits.clear()
    assert _handoff(client, token).status_code == 200
