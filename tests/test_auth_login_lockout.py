# -*- coding: utf-8 -*-
"""F4 回归：登录失败锁定（账号＋IP 双维度，SystemConfig 可配）。

黑盒审计实证（修复前）：门户登录连错 12 次无锁定、Emby 登录连错
6 次无锁定，Policy 还下发 LoginAttemptsBeforeLockout=-1（永不锁定）。

覆盖：
1. 守卫单元语义：账号维度达阈值锁定、锁定到期自动恢复、窗口外
   失败不累计、成功清零账号计数、IP 维度跨账号累计、关开关不拦。
2. 配置解析：SystemConfig 行覆盖默认值、非法值回退默认。
3. 门户登录 HTTP：连错达阈值后正确密码也被 429；锁过期后恢复；
   成功登录清零（差一位达阈值时成功一次，之后重新计数）。
4. Emby AuthenticateByName：连错达阈值回 429 TooManyAttempts；
   成功登录清零账号计数。
5. 管理端登录：连错达阈值后 429（与门户/Emby 同一守卫、独立 scope）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-login-lockout-key-32chars-min")

import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import models, ratelimit
from backend.api import emby_portal
from backend.database import get_db
from backend.security import hash_password

PASSWORD = "Lockout-Pass-123"  # secret-scan: allow — 测试夹具假密码，只进内存 SQLite 库、从不连任何真实账号


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


def _cfg(**kw):
    base = dict(enabled=True, account_max_failures=3, ip_max_failures=5,
                window_sec=600, lock_sec=900)
    base.update(kw)
    return ratelimit.LoginLockoutConfig(**base)


def _set_config(db, **values):
    defaults = {
        "login_lockout_enabled": "true",
        "login_lockout_account_max_failures": "3",
        "login_lockout_ip_max_failures": "5",
        "login_lockout_window_sec": "600",
        "login_lockout_lock_sec": "2",
    }
    defaults.update({k: str(v) for k, v in values.items()})
    for key, value in defaults.items():
        db.add(models.SystemConfig(key=key, value=value))
    db.commit()


def _user(db, password=PASSWORD, **kw):
    u = models.WebUser(
        username="lk_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(password),
        email=None,
        is_active=True,
        token_version=0,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ==================== 1. 守卫单元语义 ====================

def test_guard_account_threshold_locks():
    cfg = _cfg()
    g = ratelimit._login_lockout_guard
    t = 1000.0
    for i in range(2):
        assert g.record_failure("portal", "alice", "1.1.1.1", cfg, now=t + i) is None
        blocked, _ = g.check("portal", "alice", "1.1.1.1", cfg, now=t + i)
        assert not blocked
    locked = g.record_failure("portal", "alice", "1.1.1.1", cfg, now=t + 2)
    assert locked == "account"
    blocked, retry = g.check("portal", "alice", "1.1.1.1", cfg, now=t + 2)
    # retry 口径与既有 SlidingWindowLimiter 一致：int(剩余) + 1 向上取整
    assert blocked and 0 < retry <= cfg.lock_sec + 1


def test_guard_lock_expires_and_window_slides():
    cfg = _cfg(lock_sec=60)
    g = ratelimit._login_lockout_guard
    t = 2000.0
    for i in range(3):
        g.record_failure("portal", "bob", "2.2.2.2", cfg, now=t + i)
    blocked, _ = g.check("portal", "bob", "2.2.2.2", cfg, now=t + 59)
    assert blocked
    blocked, _ = g.check("portal", "bob", "2.2.2.2", cfg, now=t + 63)
    assert not blocked, "锁定期满后应自动恢复"
    # 窗口外失败不累计：两次失败相隔超过 window_sec 不触发锁定
    g.record_failure("portal", "carol", "3.3.3.3", cfg, now=t)
    g.record_failure("portal", "carol", "3.3.3.3", cfg, now=t + 601)
    g.record_failure("portal", "carol", "3.3.3.3", cfg, now=t + 602)
    blocked, _ = g.check("portal", "carol", "3.3.3.3", cfg, now=t + 602)
    assert not blocked


def test_guard_success_resets_account_only():
    cfg = _cfg()
    g = ratelimit._login_lockout_guard
    t = 3000.0
    for i in range(2):
        g.record_failure("portal", "dave", "4.4.4.4", cfg, now=t + i)
    g.record_success("portal", "dave", now=t + 3)
    for i in range(2):
        assert g.record_failure("portal", "dave", "4.4.4.4", cfg, now=t + 4 + i) is None
    blocked, _ = g.check("portal", "dave", "4.4.4.4", cfg, now=t + 6)
    assert not blocked, "成功清零后重新计数，两次失败不应锁定"


def test_guard_ip_dimension_cross_account():
    cfg = _cfg()
    g = ratelimit._login_lockout_guard
    t = 4000.0
    # 同一 IP 对 5 个不同账号各失败一次 → IP 维度达阈值（5）
    locked = None
    for i in range(5):
        locked = g.record_failure("portal", f"user{i}", "9.9.9.9", cfg, now=t + i)
    assert locked == "ip"
    # 该 IP 换一个从未失败过的账号也被拦
    blocked, _ = g.check("portal", "fresh", "9.9.9.9", cfg, now=t + 6)
    assert blocked
    # 别的 IP 不受连坐
    blocked, _ = g.check("portal", "fresh", "8.8.8.8", cfg, now=t + 6)
    assert not blocked


def test_guard_disabled_never_blocks():
    cfg = _cfg(enabled=False)
    g = ratelimit._login_lockout_guard
    for i in range(10):
        assert g.record_failure("portal", "erin", "5.5.5.5", cfg, now=5000.0 + i) is None
    blocked, _ = g.check("portal", "erin", "5.5.5.5", cfg, now=5011.0)
    assert not blocked


def test_guard_scopes_isolated():
    cfg = _cfg()
    g = ratelimit._login_lockout_guard
    for i in range(3):
        g.record_failure("emby", "frank", "6.6.6.6", cfg, now=6000.0 + i)
    blocked, _ = g.check("portal", "frank", "6.6.6.6", cfg, now=6003.0)
    assert not blocked, "Emby 侧失败不应锁定门户 scope"


# ==================== 2. 配置解析 ====================

def test_config_defaults_and_overrides(db):
    cfg = ratelimit.login_lockout_config(db)
    assert cfg.enabled is True
    assert cfg.account_max_failures == 10
    assert cfg.ip_max_failures == 30
    assert cfg.window_sec == 900
    assert cfg.lock_sec == 900

    _set_config(db, login_lockout_account_max_failures="7",
                login_lockout_enabled="false")
    cfg = ratelimit.login_lockout_config(db)
    assert cfg.enabled is False
    assert cfg.account_max_failures == 7

    db.query(models.SystemConfig).filter(
        models.SystemConfig.key == "login_lockout_account_max_failures"
    ).update({"value": "abc"})
    db.commit()
    cfg = ratelimit.login_lockout_config(db)
    assert cfg.account_max_failures == 10, "非法值应回退默认"


# ==================== 3. 门户登录 HTTP ====================

def _portal_client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _portal_attempt(client, username, password):
    # 请求数限流（8 次/分钟/IP）与失败锁定是两层：清掉前者的桶，
    # 模拟失败尝试摊在多分钟里发生（锁定窗口 600s 内仍累计）。
    ratelimit._limiter._hits.clear()
    return client.post("/api/user/auth/login",
                       json={"username": username, "password": password})


def test_portal_login_lockout_and_recovery(db):
    _set_config(db)
    user = _user(db)
    client = _portal_client(db)
    for _ in range(3):
        r = _portal_attempt(client, user.username, "wrong-password")
        assert r.status_code == 401, r.text
    # 已锁定：正确密码也回 429
    r = _portal_attempt(client, user.username, PASSWORD)
    assert r.status_code == 429, r.text
    # 锁定期（2s）过后恢复正常登录
    time.sleep(2.2)
    r = _portal_attempt(client, user.username, PASSWORD)
    assert r.status_code == 200, r.text


def test_portal_success_clears_failures(db):
    _set_config(db)
    user = _user(db)
    client = _portal_client(db)
    for _ in range(2):
        r = _portal_attempt(client, user.username, "wrong-password")
        assert r.status_code == 401
    r = _portal_attempt(client, user.username, PASSWORD)
    assert r.status_code == 200, r.text
    # 成功清零：再错两次仍不锁定（阈值 3）
    for _ in range(2):
        r = _portal_attempt(client, user.username, "wrong-password")
        assert r.status_code == 401, r.text
    r = _portal_attempt(client, user.username, PASSWORD)
    assert r.status_code == 200, r.text


# ==================== 4. Emby 登录 ====================

def _emby_req(ip="127.0.0.1"):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/emby/Users/AuthenticateByName",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
        "client": (ip, 54321),
    }
    return Request(scope)


def test_emby_login_lockout_and_success_clears(db):
    from backend.emby_server import api as emby_api

    _set_config(db)
    user = _user(db)

    def attempt(pw):
        ratelimit._limiter._hits.clear()
        # 账号统一后客户端登录用网站名+网站密码（_user 的 PASSWORD）。
        return emby_api.authenticate_by_name(
            _emby_req(), {"Username": user.username, "Pw": pw}, db)

    # 两次失败 → 成功（清零）→ 两次失败仍不锁
    assert attempt("bad-password").status_code == 401
    assert attempt("bad-password").status_code == 401
    ok = attempt(PASSWORD)
    assert isinstance(ok, dict) and ok.get("AccessToken"), ok
    assert attempt("bad-password").status_code == 401
    assert attempt("bad-password").status_code == 401
    # 第三次失败触发锁定，下一次（正确密码）回 429 TooManyAttempts
    assert attempt("bad-password").status_code == 401
    locked = attempt(PASSWORD)
    assert locked.status_code == 429
    assert b"TooManyAttempts" in locked.body


# ==================== 5. 管理端登录 ====================

def test_admin_login_lockout(db):
    from backend.api import admin_auth

    _set_config(db)
    admin = _user(db, is_staff=True)

    def attempt(pw):
        ratelimit._limiter._hits.clear()
        scope = {
            "type": "http", "method": "POST", "path": "/api/admin/auth/login",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"", "client": ("127.0.0.1", 54321),
        }
        req = admin_auth.AdminLoginRequest(username=admin.username, password=pw)
        try:
            admin_auth.admin_login(Request(scope), req, db)
            return 200
        except Exception as exc:  # HTTPException
            return getattr(exc, "status_code", 500)

    assert attempt("bad-password") == 401
    assert attempt("bad-password") == 401
    assert attempt("bad-password") == 401
    assert attempt(PASSWORD) == 429, "达阈值后正确密码也应被锁定拦下"
