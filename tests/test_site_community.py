# -*- coding: utf-8 -*-
"""官网社区渠道公开端点测试（GET /api/site/community）与能力注册。

隔离：最小 FastAPI 应用只挂 site_router，get_db 覆写为内存 SQLite 会话，
配置直接往 system_configs 表写行，不碰真实配置。

覆盖：
1. 什么都没配 → 200 返回空文档地址与空渠道列表（前端据此不渲染）
2. 配齐的渠道格按槽位顺序下发；关闭的、没填链接的、非 http(s) 链接的格子被过滤
3. 文档地址只透传 http(s)，非法协议回落空串
4. 读取配置抛异常 → 仍 200 返回全空（公开端点绝不 500）
5. 能力已注册进能力中心，卡片字段与 SPEC 一致
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import integrations, models
from backend.api import site as site_module
from backend.database import get_db
from backend.integrations import community


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

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _set(session, key, value):
    session.add(models.SystemConfig(key=key, value=value))
    session.commit()


def test_empty_when_nothing_configured(client):
    resp = client.get("/api/site/community")
    assert resp.status_code == 200
    assert resp.json() == {"docs_url": "", "channels": []}


def test_channels_filtered_and_ordered(client, session):
    _set(session, "landing_docs_url", "https://docs.example.com/guide")
    # 槽 1：配齐 → 下发
    _set(session, "community_ch1_enabled", "true")
    _set(session, "community_ch1_title", "通知频道")
    _set(session, "community_ch1_subtitle", "@my_channel")
    _set(session, "community_ch1_url", "https://t.me/my_channel")
    _set(session, "community_ch1_icon", "bell")
    # 槽 2：链接填了但开关关着 → 不下发
    _set(session, "community_ch2_enabled", "false")
    _set(session, "community_ch2_title", "讨论组")
    _set(session, "community_ch2_url", "https://t.me/my_group")
    # 槽 3：开关开了但没填链接 → 不下发
    _set(session, "community_ch3_enabled", "true")
    _set(session, "community_ch3_title", "客服")
    # 槽 4：链接是危险协议 → 不下发
    _set(session, "community_ch4_enabled", "true")
    _set(session, "community_ch4_title", "机器人")
    _set(session, "community_ch4_url", "javascript:alert(1)")

    resp = client.get("/api/site/community")
    assert resp.status_code == 200
    body = resp.json()
    assert body["docs_url"] == "https://docs.example.com/guide"
    assert body["channels"] == [
        {"title": "通知频道", "subtitle": "@my_channel",
         "url": "https://t.me/my_channel", "icon": "bell"},
    ]


def test_docs_url_rejects_non_http(client, session):
    _set(session, "landing_docs_url", "data:text/html;base64,AAAA")
    resp = client.get("/api/site/community")
    assert resp.status_code == 200
    assert resp.json()["docs_url"] == ""


def test_unknown_icon_falls_back(session):
    _set(session, "community_ch1_enabled", "true")
    _set(session, "community_ch1_title", "频道")
    _set(session, "community_ch1_url", "https://t.me/x")
    _set(session, "community_ch1_icon", "rocket")
    info = community.public_info(session)
    assert info["channels"][0]["icon"] == "message"


def test_read_failure_still_200(client, monkeypatch):
    def _boom(db):
        raise RuntimeError("db down")

    monkeypatch.setattr(site_module.community_capability, "public_info", _boom)
    resp = client.get("/api/site/community")
    assert resp.status_code == 200
    assert resp.json() == {"docs_url": "", "channels": []}


def test_capability_registered(session):
    assert "community" in integrations.CAPABILITY_MODULES
    cards = {c["slug"]: c for c in integrations.list_capabilities(session)}
    card = cards["community"]
    keys = set(card["fields"])
    assert "landing_docs_url" in keys
    for slot in range(1, 5):
        for suffix in ("enabled", "title", "subtitle", "url", "icon"):
            assert f"community_ch{slot}_{suffix}" in keys
