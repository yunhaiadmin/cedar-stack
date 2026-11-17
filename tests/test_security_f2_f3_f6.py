"""黑盒审计 F2/F3/F6 回归（2026-11-18 外部黑盒审计修复）。

F2：主 API 的 /api/docs、/api/redoc、/api/openapi.json 生产默认关闭
（env API_DOCS_ENABLED 显式开启才挂），不再把全部管理接口的攻击面
地图免费送给未授权访问者。EA 侧本来就 docs_url=None，不在本文件范围。

F3：/api/health（EM 与 EA 两侧）未授权只回最小存活体
（status / service / timestamp）；全量遥测（worker 名单、队列深度、
库规模、磁盘余量、配对状态、版本号等）仅对可信来源开放：本机/内网
来源（部署脚本、巡检、容器 healthcheck 的既有读法），或携带有效
管理员凭证的请求（管理后台健康页走 getPublic，自带 staff JWT）。
/api/health/detailed 同口径。

F6：POST /api/admin/auth/login 的未认证失败不可区分：密码错误、
密码对但非管理员、密码对但账号被禁用，一律 401 +「用户名或密码
错误」（与门户登录防枚举口径一致），不再替撞库者验证凭据真伪；
真实原因只留在内部登录日志里。

全部离线：内存/临时 SQLite + TestClient，不连生产库、不碰网络。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-for-f2f3f6-suite-0123456789")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.emby_server import models as em
from backend.security import create_access_token, hash_password

# 公网来源（is_private/is_loopback 均为 False 的真实公网地址形态）
PUBLIC_IP = "8.8.4.4"
LOOPBACK_IP = "127.0.0.1"

# ===========================================================================
# F6：管理员登录失败不可区分（凭据有效性预言机）
# ===========================================================================

@pytest.fixture()
def f6_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _f6_user(db, username, password, **kw):
    fields = {"is_active": True, "token_version": 0}
    fields.update(kw)
    u = models.WebUser(username=username, password_hash=hash_password(password),
                       **fields)
    db.add(u)
    db.commit()
    return u


def _f6_client(db, ip):
    from backend.api.admin_core import admin_router
    from backend.database import get_db

    app = FastAPI()
    app.include_router(admin_router)

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app, client=(ip, 50000))


def test_f6_all_credential_failures_are_identical(f6_db):
    """密码错 / 密码对但非管理员 / 密码对但被禁用 / 用户不存在：
    状态码与响应体必须逐字一致（401 + 同一文案）。"""
    _f6_user(f6_db, "boss", "boss-pass-12345", is_staff=True, admin_role="super")
    _f6_user(f6_db, "alice", "alice-pass-12345")  # 非管理员
    _f6_user(f6_db, "oldboss", "old-pass-12345", is_staff=True, is_active=False)
    client = _f6_client(f6_db, "10.61.0.1")

    cases = [
        {"username": "boss", "password": "wrong-pass-1"},      # 密码错误
        {"username": "alice", "password": "alice-pass-12345"},  # 密码对、非管理员
        {"username": "oldboss", "password": "old-pass-12345"},  # 密码对、已禁用
        {"username": "ghost", "password": "whatever-1"},        # 用户不存在
    ]
    responses = [client.post("/api/admin/auth/login", json=c) for c in cases]
    for r in responses:
        assert r.status_code == 401, r.text
        assert r.json() == {"detail": "用户名或密码错误"}, r.text


def test_f6_real_admin_still_logs_in(f6_db):
    _f6_user(f6_db, "boss", "boss-pass-12345", is_staff=True, admin_role="super")
    client = _f6_client(f6_db, "10.61.0.2")
    r = client.post("/api/admin/auth/login",
                    json={"username": "boss", "password": "boss-pass-12345"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert r.json()["user"]["is_staff"] is True

# ===========================================================================
# F2 / F3：真实 app（EM backend.main + EA emby_api.main）
# ===========================================================================

@pytest.fixture(scope="module", autouse=True)
def _app_db(tmp_path_factory):
    """健康端点直查全局 SessionLocal：把全局 engine 换绑到本模块独占的
    临时 SQLite 并 init_db，模块结束原样还原（防 #1668 式全局引擎污染）。"""
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm

    from backend import database as dbmod

    dbfile = tmp_path_factory.mktemp("f2f3f6") / "app.db"
    url = f"sqlite:///{dbfile}"
    saved_engine = dbmod.engine
    saved_factory = dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    dbmod.engine = _ce(url)
    dbmod.configure_session_local(_sm(bind=dbmod.engine))
    dbmod.init_db()
    new_engine = dbmod.engine
    try:
        yield
    finally:
        dbmod.engine = saved_engine
        dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        new_engine.dispose()


@pytest.fixture(scope="module")
def em_clients():
    from backend.main import app

    return {
        "public": TestClient(app, client=(PUBLIC_IP, 50000),
                             raise_server_exceptions=False),
        "loopback": TestClient(app, client=(LOOPBACK_IP, 50000),
                               raise_server_exceptions=False),
    }


@pytest.fixture(scope="module")
def ea_clients():
    from emby_api.main import app as ea_app

    return {
        "public": TestClient(ea_app, client=(PUBLIC_IP, 50000),
                             raise_server_exceptions=False),
        "loopback": TestClient(ea_app, client=(LOOPBACK_IP, 50000),
                               raise_server_exceptions=False),
    }


@pytest.fixture()
def staff_and_user_tokens():
    """在换绑后的临时库里建 staff 与普通用户各一，返回 (staff_token, user_token)。"""
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        staff = models.WebUser(username="f3staff", password_hash=hash_password("x-123456"),
                               is_staff=True, is_active=True, token_version=0)
        plain = models.WebUser(username="f3plain", password_hash=hash_password("x-123456"),
                               is_staff=False, is_active=True, token_version=0)
        db.add_all([staff, plain])
        db.commit()
        staff_token = create_access_token(staff.id, {"username": "f3staff", "staff": True},
                                          token_version=0)
        user_token = create_access_token(plain.id, {"username": "f3plain"},
                                         token_version=0)
        yield staff_token, user_token
        db.query(models.WebUser).filter(
            models.WebUser.username.in_(["f3staff", "f3plain"])).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()

# ---------------------------------------------------------------------------
# F2：API 文档默认关闭
# ---------------------------------------------------------------------------

def test_f2_docs_helper_env_switch(monkeypatch):
    from backend.main import _api_docs_enabled

    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    assert _api_docs_enabled() is False
    monkeypatch.setenv("API_DOCS_ENABLED", "true")
    assert _api_docs_enabled() is True
    monkeypatch.setenv("API_DOCS_ENABLED", "1")
    assert _api_docs_enabled() is True
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    assert _api_docs_enabled() is False


def test_f2_docs_routes_closed_by_default(em_clients):
    """默认（未设 API_DOCS_ENABLED）：docs / redoc / openapi.json 全部 404，
    且不再吐出含管理接口清单的 schema。"""
    client = em_clients["loopback"]
    for path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
        r = client.get(path)
        assert r.status_code == 404, (path, r.status_code, r.text[:200])
    r = client.get("/api/openapi.json")
    assert "paths" not in r.text


# ---------------------------------------------------------------------------
# F3：EM /api/health 最小体与可信来源
# ---------------------------------------------------------------------------

EM_TELEMETRY_KEYS = {
    "health_level", "health_issues", "health_metrics", "observability",
    "database", "online_users", "emby_server", "version", "emby_gateway",
    "runtime", "redis",
}


def test_f3_em_health_public_gets_minimal_body(em_clients):
    r = em_clients["public"].get("/api/health")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")
    assert EM_TELEMETRY_KEYS.isdisjoint(body.keys()), body.keys()
    assert set(body.keys()) <= {"status", "service", "timestamp"}


def test_f3_em_health_loopback_gets_full_detail(em_clients):
    r = em_clients["loopback"].get("/api/health")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "health_metrics" in body
    assert "version" in body
    assert "database" in body
    assert "runtime" in body


def test_f3_em_health_staff_token_unlocks_detail_from_public_ip(
        em_clients, staff_and_user_tokens):
    staff_token, _ = staff_and_user_tokens
    r = em_clients["public"].get(
        "/api/health", headers={"Authorization": f"Bearer {staff_token}"})
    assert r.status_code == 200, r.text[:300]
    assert "health_metrics" in r.json()


def test_f3_em_health_plain_user_token_stays_minimal(
        em_clients, staff_and_user_tokens):
    _, user_token = staff_and_user_tokens
    r = em_clients["public"].get(
        "/api/health", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 200, r.text[:300]
    assert "health_metrics" not in r.json()


def test_f3_em_health_detailed_gated_the_same_way(em_clients):
    r = em_clients["public"].get("/api/health/detailed")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "services" not in body
    assert "metrics" not in body
    r = em_clients["loopback"].get("/api/health/detailed")
    assert r.status_code == 200, r.text[:300]
    assert "services" in r.json()


# ---------------------------------------------------------------------------
# F3：EA /api/health 最小体与可信来源
# ---------------------------------------------------------------------------

EA_TELEMETRY_KEYS = {
    "version", "database", "paired_with_em", "missing_em_tables",
    "em_panel_url", "emby_server_name", "redis", "node", "runtime",
}


def test_f3_ea_health_public_gets_minimal_body(ea_clients):
    r = ea_clients["public"].get("/api/health")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("service") == "ea"
    assert body.get("status") in ("healthy", "unpaired")
    assert EA_TELEMETRY_KEYS.isdisjoint(body.keys()), body.keys()


def test_f3_ea_health_loopback_gets_full_detail(ea_clients):
    r = ea_clients["loopback"].get("/api/health")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("service") == "ea"
    assert "paired_with_em" in body
    assert "missing_em_tables" in body
    assert "version" in body
