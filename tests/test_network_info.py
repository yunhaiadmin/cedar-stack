"""测速页网络信息：IP 打码 / 地区推导纯函数 + /api/user/network-info 端点。

口径要点（与 backend/network_info.py 同源）：
- IPv4 只保留前两段，后两段星号（友站样式 139.162.**.**）；
- 地区只认 CF-IPCountry 头，不接外部 IP 库；没头/坏值回 None；
- 运营商恒 None（无可信离线来源，前端显示 --）；
- 端点只下发打码串，原始 IP 不出服务端。
"""
import os
import sys
import uuid

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import network_info as ni
from backend import ratelimit
from backend.api.user import user_router
from backend.database import get_db
from backend.security import create_access_token, hash_password


# ---------- 纯函数 ----------

def test_mask_ipv4_keeps_first_two_octets():
    assert ni.mask_ip("139.162.10.20") == "139.162.**.**"
    assert ni.mask_ip("1.2.3.4") == "1.2.**.**"


def test_mask_ipv6_keeps_first_two_groups():
    assert ni.mask_ip("2401:b200::1") == "2401:b200:**:**"


def test_mask_ip_rejects_unparsable():
    assert ni.mask_ip("not-an-ip") is None
    assert ni.mask_ip("") is None
    assert ni.mask_ip(None) is None


def test_region_label_from_country_code():
    assert ni.region_label("SG") == "SG / 东南亚"
    assert ni.region_label("cn") == "CN / 中国"
    assert ni.region_label("ZZ") == "ZZ"  # 未知代码只显示代码本身
    assert ni.region_label(None) is None
    assert ni.region_label("") is None
    assert ni.region_label("SGP") is None  # 非两字母不当代码用


# ---------- 端点 ----------

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


def _client(db):
    app = FastAPI()
    app.include_router(user_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers(db):
    u = models.WebUser(
        username="net_" + uuid.uuid4().hex[:10],
        password_hash=hash_password("Passw0rd!x"),
        email="net@example.com",
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    token = create_access_token(u.id, {"username": u.username},
                                token_version=u.token_version or 0)
    return {"Authorization": f"Bearer {token}"}


def test_network_info_masks_ip_and_maps_region(db, monkeypatch):
    monkeypatch.setattr(ratelimit, "client_ip", lambda req: "139.162.10.20")
    client = _client(db)
    r = client.get("/api/user/network-info",
                   headers={**_auth_headers(db), "CF-IPCountry": "SG"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ip_masked"] == "139.162.**.**"
    assert "139.162.10.20" not in r.text  # 原始 IP 不许出服务端
    assert body["region"] == "SG / 东南亚"
    assert body["operator"] is None


def test_network_info_without_cf_header_region_null(db, monkeypatch):
    monkeypatch.setattr(ratelimit, "client_ip", lambda req: "10.0.0.8")
    client = _client(db)
    r = client.get("/api/user/network-info", headers=_auth_headers(db))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ip_masked"] == "10.0.**.**"
    assert body["region"] is None


def test_network_info_requires_auth(db):
    client = _client(db)
    r = client.get("/api/user/network-info")
    assert r.status_code in (401, 403)
