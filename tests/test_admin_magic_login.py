# -*- coding: utf-8 -*-
"""管理员免登录链接（2026-11-17 用户亲点）：站长凭专属链接直进管理后台。

钉住的口径：
- 令牌只存 SHA256（与 password_reset_tokens 同口径），明文只在生成响应里
  出现一次；状态口永不回显明文/哈希；
- 链接绑定生成者本人：一人一行，别人管不到、换不到别人的会话；
- 入口失败一律 404（不存在/停用/账号被禁/被限流不可区分），交换失败一律
  统一 401；从未生成过链接时入口完全惰性；
- 交换码 60 秒、单次使用；换出的会话与密码登录同签发同形状（同一响应
  字段集、access 能过 /auth/me）；
- 重新生成旧链接立即失效；停用后链接失效且停用幂等；
- 生成/停用是「给自己发凭据」，与 API Key 同级只给超管（状态 GET 不限）；
- 成功使用落 last_used_at + AdminLog 审计 + 登录日志各一份。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import hashlib
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, ratelimit
from backend.api import admin_auth as _admin_auth  # noqa: F401 — 包 __init__ 导入即注册全部管理端点（含免登录）
from backend.api.admin_core import admin_router
from backend.database import get_db
from backend.security import hash_password

SITE_URL = "https://www.aetrix.love"
PASSWORD = "magic-pass-123"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.SystemConfig(key="site_url", value=SITE_URL))
    session.commit()
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
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _admin(db, *, role="super", is_active=True):
    u = models.WebUser(
        username="adm_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(PASSWORD),
        is_staff=True,
        admin_role=role,
        is_active=is_active,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, user):
    res = client.post("/api/admin/auth/login",
                      json={"username": user.username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _generate_key(client, token):
    res = client.post("/api/admin/auth/magic-link/generate", json={},
                      headers=_auth(token))
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url.startswith(f"{SITE_URL}/api/admin/magic-login?key=")
    return url.split("key=", 1)[1]


def _follow_magic(client, key):
    return client.get(f"/api/admin/magic-login?key={key}", follow_redirects=False)


def _exchange(client, code):
    return client.post("/api/admin/auth/magic-exchange", json={"code": code})


def test_inert_when_never_generated(db):
    """从未生成过链接：入口对任何 key 都是 404，交换口对任何码都是 401。"""
    client = _client(db)
    assert _follow_magic(client, "whatever-key").status_code == 404
    assert client.get("/api/admin/magic-login").status_code == 404
    assert _exchange(client, "whatever-code").status_code == 401


def test_generate_stores_only_hash(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)

    row = db.query(models.AdminMagicLink).filter_by(admin_user_id=admin.id).one()
    assert row.token_hash == hashlib.sha256(key.encode()).hexdigest()
    assert row.token_hash != key
    assert row.is_active is True

    status = client.get("/api/admin/auth/magic-link", headers=_auth(token))
    assert status.status_code == 200
    body = status.json()
    assert body["state"] == "active"
    assert body["username"] == admin.username
    assert body["last_used_at"] is None
    # 状态口永不回显明文与哈希
    assert key not in status.text
    assert row.token_hash not in status.text


def test_full_flow_session_matches_password_login(db):
    client = _client(db)
    admin = _admin(db)
    login_payload = _login(client, admin)
    key = _generate_key(client, login_payload["access_token"])

    res = _follow_magic(client, key)
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith("/admin/#magic=")
    code = location.split("#magic=", 1)[1]
    assert code and code != key

    exchanged = _exchange(client, code)
    assert exchanged.status_code == 200, exchanged.text
    payload = exchanged.json()
    # 与密码登录同一响应形状（同一签发口径，不存在第二种会话）
    assert set(payload.keys()) == set(login_payload.keys())
    assert set(payload["user"].keys()) == set(login_payload["user"].keys())
    assert payload["user"]["id"] == admin.id
    assert payload["token_type"] == "bearer"

    me = client.get("/api/admin/auth/me", headers=_auth(payload["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == admin.username


def test_exchange_code_single_use(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    code = _follow_magic(client, key).headers["location"].split("#magic=", 1)[1]
    assert _exchange(client, code).status_code == 200
    assert _exchange(client, code).status_code == 401


def test_exchange_code_expired(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    code = _follow_magic(client, key).headers["location"].split("#magic=", 1)[1]
    row = db.query(models.AdminMagicExchangeCode).filter_by(
        admin_user_id=admin.id).one()
    row.expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    assert _exchange(client, code).status_code == 401


def test_invalid_key_and_code(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    _generate_key(client, token)
    assert _follow_magic(client, "not-the-key").status_code == 404
    assert _exchange(client, "not-the-code").status_code == 401


def test_regenerate_invalidates_old_key(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    old_key = _generate_key(client, token)
    new_key = _generate_key(client, token)
    assert new_key != old_key
    assert _follow_magic(client, old_key).status_code == 404
    assert _follow_magic(client, new_key).status_code == 302


def test_disable_invalidates_and_is_idempotent(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    res = client.post("/api/admin/auth/magic-link/disable", json={},
                      headers=_auth(token))
    assert res.status_code == 200
    status = client.get("/api/admin/auth/magic-link", headers=_auth(token)).json()
    assert status["state"] == "disabled"
    assert _follow_magic(client, key).status_code == 404
    # 停用幂等：再停一次仍是成功
    res = client.post("/api/admin/auth/magic-link/disable", json={},
                      headers=_auth(token))
    assert res.status_code == 200


def test_rate_limit_blocks_even_valid_key(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    # 同一 IP 先烧满 5 次/分钟的入口额度（失败尝试也计数）
    for _ in range(5):
        assert _follow_magic(client, "bad-key").status_code == 404
    # 第 6 次即使是真 key 也被限流挡下，对外仍是无信息量的 404
    assert _follow_magic(client, key).status_code == 404
    # 额度恢复（滑窗清空）后真 key 立刻可用，证明挡的是限流不是链接坏了
    ratelimit._limiter._hits.clear()
    assert _follow_magic(client, key).status_code == 302


def test_binding_isolation_between_admins(db):
    client = _client(db)
    alice = _admin(db)
    bob = _admin(db)
    alice_token = _login(client, alice)["access_token"]
    bob_token = _login(client, bob)["access_token"]

    alice_key = _generate_key(client, alice_token)
    # Bob 看不到也管不到 Alice 的链接：自己状态仍是未生成
    bob_status = client.get("/api/admin/auth/magic-link",
                            headers=_auth(bob_token)).json()
    assert bob_status["state"] == "none"
    # Bob 停用（自己没有链接，幂等成功）不影响 Alice 的链接
    client.post("/api/admin/auth/magic-link/disable", json={},
                headers=_auth(bob_token))
    res = _follow_magic(client, alice_key)
    assert res.status_code == 302
    code = res.headers["location"].split("#magic=", 1)[1]
    payload = _exchange(client, code).json()
    assert payload["user"]["id"] == alice.id


def test_generate_requires_super_role(db):
    """生成是给自己发凭据：只读与运营角色都被拒（与 API Key 同级口径）。"""
    client = _client(db)
    for role in ("viewer", "operator"):
        admin = _admin(db, role=role)
        token = _login(client, admin)["access_token"]
        res = client.post("/api/admin/auth/magic-link/generate", json={},
                          headers=_auth(token))
        assert res.status_code == 403, (role, res.text)
        # 状态 GET 不受写限制影响（只回本人状态）
        status = client.get("/api/admin/auth/magic-link", headers=_auth(token))
        assert status.status_code == 200


def test_success_writes_last_used_and_audit(db):
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    code = _follow_magic(client, key).headers["location"].split("#magic=", 1)[1]
    assert _exchange(client, code).status_code == 200

    db.expire_all()
    link = db.query(models.AdminMagicLink).filter_by(admin_user_id=admin.id).one()
    assert link.last_used_at is not None
    audits = db.query(models.AdminLog).filter_by(
        admin_user_id=admin.id, action="admin_magic_login").all()
    assert len(audits) == 1
    logs = db.query(models.LoginLog).filter_by(
        user_id=admin.id, reason="admin_magic_login", success=True).all()
    assert len(logs) == 1


def test_link_dies_with_account(db):
    """账号被禁用后，其名下链接立即失效（入口 404，与不存在不可区分）。"""
    client = _client(db)
    admin = _admin(db)
    token = _login(client, admin)["access_token"]
    key = _generate_key(client, token)
    admin.is_active = False
    db.commit()
    assert _follow_magic(client, key).status_code == 404
