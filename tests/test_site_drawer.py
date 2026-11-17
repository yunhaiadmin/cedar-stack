# -*- coding: utf-8 -*-
"""官网菜单抽屉配置化测试（公开端点 GET /api/site/drawer + 管理端
/api/admin/site-drawer/config + config_service 家族 site_drawer）。

隔离：最小 FastAPI 应用挂 site_router 与 admin_site_drawer_router，
get_db 覆写为内存 SQLite 会话，配置直接往 system_configs 表写行。

覆盖：
1. 什么都没配 → 默认导航（首页/定价）、空渠道、默认分组标题与底按钮
2. 旧「官网社区渠道」配置（landing_docs_url + community_ch*）在未存
   site_drawer_config 时作为默认渠道/文档行来源（生产存量不丢）
3. 存了 site_drawer_config → 公开端点只下发 enabled 且链接合法的项，
   未知图标回落；底按钮关掉时 cta 为 None
4. 配置行是坏 JSON → 回落默认、200 不 500
5. 管理端 GET（无行）返回默认全量结构；PUT 合法载荷落库且公开端点
   立即反映；PUT 非法载荷（危险协议/空标题/未知图标/超量）→ 400
6. config_service.save_config 家族校验：非法结构抛 ConfigValueError，
   合法载荷落库为可解析的 canonical JSON
"""
import json
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_service, models
from backend.api import admin_site_drawer as admin_module
from backend.api import site as site_module
from backend.api.admin_core import get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import site_drawer


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


@pytest.fixture()
def client(session):
    app = FastAPI()
    app.include_router(site_module.site_router)
    app.include_router(admin_module.admin_site_drawer_router)

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    admin = models.WebUser(id=1, username="root", password_hash="x",
                           is_active=True, is_staff=True)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin
    return TestClient(app)


def _set(session, key, value):
    session.add(models.SystemConfig(key=key, value=value))
    session.commit()


def _valid_payload():
    return {
        "nav_items": [
            {"icon": "home", "label": "首页", "url": "/",
             "external": False, "enabled": True},
            {"icon": "book", "label": "文档", "url": "https://docs.example.com",
             "external": True, "enabled": True},
            {"icon": "tag", "label": "定价", "url": "/pricing",
             "external": False, "enabled": False},
        ],
        "channels_title": "社区渠道",
        "channels": [
            {"icon": "users", "title": "讨论组", "subtitle": "@my_group",
             "url": "https://t.me/my_group", "enabled": True},
            {"icon": "bot", "title": "机器人", "subtitle": "",
             "url": "https://t.me/my_bot", "enabled": False},
        ],
        "cta": {"label": "进入后台", "url": "", "enabled": True},
    }


# ---------------------------------------------------------------- 公开端点

def test_public_defaults_when_nothing_configured(client):
    resp = client.get("/api/site/drawer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nav_items"] == [
        {"icon": "home", "label": "首页", "url": "/", "external": False},
        {"icon": "tag", "label": "定价", "url": "/pricing", "external": False},
    ]
    assert body["channels_title"] == "社区渠道"
    assert body["channels"] == []
    assert body["cta"] == {"label": "进入后台", "url": ""}


def test_public_legacy_community_fallback(client, session):
    _set(session, "landing_docs_url", "https://docs.example.com/guide")
    _set(session, "community_ch1_enabled", "true")
    _set(session, "community_ch1_title", "讨论组")
    _set(session, "community_ch1_subtitle", "@legacy_group")
    _set(session, "community_ch1_url", "https://t.me/legacy_group")
    _set(session, "community_ch1_icon", "users")

    body = client.get("/api/site/drawer").json()
    assert {"icon": "book", "label": "文档",
            "url": "https://docs.example.com/guide",
            "external": True} in body["nav_items"]
    assert body["channels"] == [
        {"icon": "users", "title": "讨论组", "subtitle": "@legacy_group",
         "url": "https://t.me/legacy_group"},
    ]


def test_public_saved_config_filters_disabled_and_bad_urls(client, session):
    payload = _valid_payload()
    # 渠道里混一条危险协议：公开端点必须过滤（存量脏数据纵深防御）
    payload["channels"].append(
        {"icon": "bell", "title": "坏渠道", "subtitle": "",
         "url": "javascript:alert(1)", "enabled": True})
    # 导航里混一条未知图标：回落默认图标但保留
    payload["nav_items"].append(
        {"icon": "rocket", "label": "神秘", "url": "/library",
         "external": False, "enabled": True})
    _set(session, site_drawer.KEY, json.dumps(payload, ensure_ascii=False))

    body = client.get("/api/site/drawer").json()
    labels = [item["label"] for item in body["nav_items"]]
    assert labels == ["首页", "文档", "神秘"]  # 定价 enabled=False 被过滤
    assert body["nav_items"][2]["icon"] == "home"
    assert [c["title"] for c in body["channels"]] == ["讨论组"]
    assert body["cta"] == {"label": "进入后台", "url": ""}


def test_public_cta_disabled_returns_none(client, session):
    payload = _valid_payload()
    payload["cta"]["enabled"] = False
    _set(session, site_drawer.KEY, json.dumps(payload, ensure_ascii=False))
    body = client.get("/api/site/drawer").json()
    assert body["cta"] is None


def test_public_corrupt_json_falls_back_to_defaults(client, session):
    _set(session, site_drawer.KEY, "{not json")
    resp = client.get("/api/site/drawer")
    assert resp.status_code == 200
    body = resp.json()
    assert [i["label"] for i in body["nav_items"]] == ["首页", "定价"]
    assert body["channels"] == []


def test_public_read_failure_still_200(client, monkeypatch):
    def _boom(db):
        raise RuntimeError("db down")

    monkeypatch.setattr(site_module.site_drawer_capability, "public_info", _boom)
    resp = client.get("/api/site/drawer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channels"] == []
    assert body["cta"] == {"label": "进入后台", "url": ""}


# ---------------------------------------------------------------- 管理端

def test_admin_get_returns_defaults_with_icon_options(client):
    resp = client.get("/api/admin/site-drawer/config")
    assert resp.status_code == 200
    body = resp.json()
    cfg = body["config"]
    assert [i["label"] for i in cfg["nav_items"]] == ["首页", "定价"]
    assert cfg["cta"] == {"label": "进入后台", "url": "", "enabled": True}
    assert "users" in body["icon_options"]


def test_admin_put_roundtrip_and_public_reflects(client, session):
    resp = client.put("/api/admin/site-drawer/config", json=_valid_payload())
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert [i["label"] for i in cfg["nav_items"]] == ["首页", "文档", "定价"]

    row = session.query(models.SystemConfig).filter_by(
        key=site_drawer.KEY).one()
    stored = json.loads(row.value)
    assert stored["channels"][0]["title"] == "讨论组"

    body = client.get("/api/site/drawer").json()
    assert [i["label"] for i in body["nav_items"]] == ["首页", "文档"]
    assert [c["title"] for c in body["channels"]] == ["讨论组"]


@pytest.mark.parametrize("mutate", [
    lambda p: p["nav_items"][0].update(url="javascript:alert(1)"),
    lambda p: p["nav_items"][0].update(label=""),
    lambda p: p["nav_items"][0].update(icon="rocket"),
    lambda p: p["channels"][0].update(url="ftp://example.com/x"),
    lambda p: p.update(nav_items=p["nav_items"] * 7),
    lambda p: p["cta"].update(url="//evil.example.com"),
])
def test_admin_put_rejects_invalid(client, mutate):
    payload = _valid_payload()
    mutate(payload)
    resp = client.put("/api/admin/site-drawer/config", json=payload)
    assert resp.status_code == 400


# ---------------------------------------------------------------- 家族校验

def test_save_config_family_validation(session):
    with pytest.raises(ConfigValueError):
        config_service.save_config(
            session,
            {site_drawer.KEY: {"nav_items": [
                {"icon": "home", "label": "首页", "url": "javascript:x",
                 "external": False, "enabled": True}],
                "channels_title": "社区渠道", "channels": [],
                "cta": {"label": "进入后台", "url": "", "enabled": True}}},
            schema="site_drawer",
        )
    session.rollback()

    saved = config_service.save_config(
        session, {site_drawer.KEY: _valid_payload()}, schema="site_drawer")
    session.commit()
    assert saved == [site_drawer.KEY]
    row = session.query(models.SystemConfig).filter_by(
        key=site_drawer.KEY).one()
    parsed = json.loads(row.value)
    assert parsed["nav_items"][0]["label"] == "首页"
    assert parsed["channels"][1]["enabled"] is False
