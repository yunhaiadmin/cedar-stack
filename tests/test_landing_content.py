# -*- coding: utf-8 -*-
"""官网落地页文案配置化测试（公开端点 GET /api/site/landing +
管理端 /api/admin/landing/content + config_service 家族 landing）。

隔离：最小 FastAPI 应用挂 site_router 与 admin_landing_router，
get_db 覆写为内存 SQLite 会话，配置直接往 system_configs 表写行。

覆盖：
1. 什么都没配 → 公开端点下发默认整份文案，且与现行写死文案的
   关键句逐字一致（回落不丢字、不改字）
2. 存了自定义文案 → 公开端点原样下发；存量坏 JSON → 回落默认、
   200 不 500；单字段坏掉 → 该字段回落默认、其余自定义保留
3. 管理端 GET（无行）返回默认全量结构 + has_custom=False；
   PUT 合法载荷落库且公开端点立即反映、has_custom=True；
   DELETE 删行恢复默认
4. PUT 非法载荷（主标题为空/副文案为空/卖点卡不是 3 张/超长/
   合作方超量）→ 400 且不落库
5. config_service 家族校验：未知键拒绝；合法载荷落库为可解析的
   canonical JSON
6. 鉴权：无凭证 401、非管理员 403（GET/PUT/DELETE 都要管理员）
"""
import json
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_service, models
from backend.api import admin_landing as admin_module
from backend.api import site as site_module
from backend.api.admin_core import get_current_admin
from backend.config_schema import ConfigValueError, LANDING_DEFAULT_CONTENT
from backend.database import get_db
from backend.integrations import landing_content


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        engine.dispose()


def _make_app(session, admin_mode="admin"):
    app = FastAPI()
    app.include_router(site_module.site_router)
    app.include_router(admin_module.admin_landing_router)

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    if admin_mode == "admin":
        admin = models.WebUser(id=1, username="root", password_hash="x",
                               is_active=True, is_staff=True)
        app.dependency_overrides[get_current_admin] = lambda: admin
    elif admin_mode == "forbidden":
        def _raise():
            raise HTTPException(status_code=403, detail="forbidden")
        app.dependency_overrides[get_current_admin] = _raise
    # admin_mode == "real" 时不覆写，走真实鉴权依赖（无凭证 → 401）
    return app


@pytest.fixture()
def client(session):
    return TestClient(_make_app(session))


def _set(session, key, value):
    session.add(models.SystemConfig(key=key, value=value))
    session.commit()


def _valid_payload():
    payload = json.loads(json.dumps(LANDING_DEFAULT_CONTENT))
    payload["hero"]["title_lines"] = ["测试主标题", "第二行"]
    payload["partners"]["names"] = ["Netflix", "Disney+", "HBO Max", "Apple TV+"]
    return payload


# ---------------------------------------------------------------- 公开端点


def test_public_defaults_when_nothing_configured(client):
    r = client.get("/api/site/landing")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data == LANDING_DEFAULT_CONTENT
    # 回落逐字核对现行写死文案的关键句（改字即红）
    assert data["hero"]["title_lines"] == ["你的私人影院", "随时随地开场"]
    assert data["hero"]["subs"][0] == "无广告干扰，沉浸观影从按下播放开始"
    assert data["partners"]["names"] == ["Netflix", "Disney+", "HBO Max"]
    assert data["essentials"]["cards"][2]["points"][0] == {
        "title": "自动入库", "text": "新片到盘，自动扫描进片库"}


def test_public_saved_config_roundtrip(client, session):
    _set(session, "landing_content", json.dumps(_valid_payload(),
                                                ensure_ascii=False))
    r = client.get("/api/site/landing")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["hero"]["title_lines"] == ["测试主标题", "第二行"]
    assert data["partners"]["names"][-1] == "Apple TV+"


def test_public_corrupt_json_falls_back_to_defaults(client, session):
    _set(session, "landing_content", "{not json")
    r = client.get("/api/site/landing")
    assert r.status_code == 200, r.text
    assert r.json() == LANDING_DEFAULT_CONTENT


def test_public_bad_field_falls_back_per_field(client, session):
    payload = _valid_payload()
    payload["hero"]["title_lines"] = []  # 坏字段：读侧回落默认标题
    payload["devices"]["sub"] = "自定义设备副文案"
    _set(session, "landing_content", json.dumps(payload, ensure_ascii=False))
    r = client.get("/api/site/landing")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["hero"]["title_lines"] == ["你的私人影院", "随时随地开场"]
    assert data["devices"]["sub"] == "自定义设备副文案"


def test_public_read_failure_still_200(session, monkeypatch):
    def _boom(db):
        raise RuntimeError("db down")
    monkeypatch.setattr(landing_content, "read_config", _boom)
    r = TestClient(_make_app(session)).get("/api/site/landing")
    assert r.status_code == 200, r.text
    assert r.json() == LANDING_DEFAULT_CONTENT


# ---------------------------------------------------------------- 管理端


def test_admin_get_returns_defaults(client):
    r = client.get("/api/admin/landing/content")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_custom"] is False
    assert data["config"] == LANDING_DEFAULT_CONTENT


def test_admin_put_roundtrip_and_public_reflects(client, session):
    r = client.put("/api/admin/landing/content", json=_valid_payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_custom"] is True
    assert data["config"]["hero"]["title_lines"] == ["测试主标题", "第二行"]
    # 落库的是 canonical JSON，公开端点立即反映
    row = session.query(models.SystemConfig).filter_by(
        key="landing_content").one()
    assert json.loads(row.value)["partners"]["names"][-1] == "Apple TV+"
    pub = client.get("/api/site/landing").json()
    assert pub["hero"]["title_lines"] == ["测试主标题", "第二行"]
    # 审计留痕
    logs = session.query(models.AdminLog).filter_by(
        action="landing_content_update").all()
    assert len(logs) == 1


def test_admin_delete_resets_to_defaults(client, session):
    assert client.put("/api/admin/landing/content",
                      json=_valid_payload()).status_code == 200
    r = client.delete("/api/admin/landing/content")
    assert r.status_code == 200, r.text
    assert r.json()["has_custom"] is False
    assert r.json()["config"] == LANDING_DEFAULT_CONTENT
    assert session.query(models.SystemConfig).filter_by(
        key="landing_content").count() == 0
    assert client.get("/api/site/landing").json() == LANDING_DEFAULT_CONTENT


@pytest.mark.parametrize("mutate", [
    lambda p: p["hero"].update(title_lines=[]),          # 主标题为空
    lambda p: p["hero"].update(subs=[]),                 # 轮换副文案为空
    lambda p: p["essentials"].update(cards=p["essentials"]["cards"][:2]),
    lambda p: p["hero"].update(scroll_hint="长" * 41),   # 超长
    lambda p: p["partners"].update(names=["N"] * 11),     # 合作方超量
    lambda p: p["essentials"]["cards"][0].update(desc=""),  # 卡描述为空
])
def test_admin_put_rejects_invalid(client, session, mutate):
    payload = _valid_payload()
    mutate(payload)
    r = client.put("/api/admin/landing/content", json=payload)
    assert r.status_code == 400, r.text
    assert session.query(models.SystemConfig).filter_by(
        key="landing_content").count() == 0, "非法载荷不许落库"


# ---------------------------------------------------------------- 家族校验


def test_save_config_family_validation(session):
    with pytest.raises(ConfigValueError):
        config_service.save_config(
            session, {"landing_unknown_key": "x"}, schema="landing")
    config_service.save_config(
        session, {"landing_content": _valid_payload()}, schema="landing")
    session.commit()
    row = session.query(models.SystemConfig).filter_by(
        key="landing_content").one()
    parsed = json.loads(row.value)
    assert parsed["hero"]["title_lines"] == ["测试主标题", "第二行"]


# ---------------------------------------------------------------- 鉴权


def test_unauthenticated_401(session):
    app = _make_app(session, admin_mode="real")
    r = TestClient(app, raise_server_exceptions=False).get(
        "/api/admin/landing/content")
    assert r.status_code == 401, r.text


def test_non_staff_403(session):
    c = TestClient(_make_app(session, admin_mode="forbidden"),
                   raise_server_exceptions=False)
    assert c.get("/api/admin/landing/content").status_code == 403
    assert c.put("/api/admin/landing/content",
                 json=_valid_payload()).status_code == 403
    assert c.delete("/api/admin/landing/content").status_code == 403
    assert session.query(models.SystemConfig).filter_by(
        key="landing_content").count() == 0
