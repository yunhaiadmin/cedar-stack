# -*- coding: utf-8 -*-
"""隐私政策正文配置化测试（公开端点 GET /api/site/privacy-policy +
管理端 /api/admin/privacy-policy/content + config_service 家族
privacy_policy）。

隔离：最小 FastAPI 应用挂 site_router 与 admin_privacy_router，
get_db 覆写为内存 SQLite 会话，配置直接往 system_configs 表写行。

覆盖：
1. 什么都没配 → 公开端点 markdown 为空、has_custom=False（前端
   据此回落内置写死正文）
2. 存了非空正文 → 公开端点原样下发、has_custom=True
3. 管理端 GET（无行）返回空 content + 现行正文转写起稿；
   PUT 非空落库且公开端点立即反映；PUT 空正文删行回落
4. config_service 家族校验：未知键拒绝；("str",) 空串 = 删行
5. DEFAULT_MARKDOWN 含现行正文的关键句（中英两段都在，转写不丢字）
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

from backend import config_service, models
from backend.api import admin_privacy as admin_module
from backend.api import site as site_module
from backend.api.admin_core import get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import privacy_policy


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
    app.include_router(admin_module.admin_privacy_router)

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


# ---------------------------------------------------------------- 公开端点

def test_public_empty_when_nothing_configured(client):
    resp = client.get("/api/site/privacy-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == ""
    assert body["has_custom"] is False


def test_public_serves_custom_markdown(client, session):
    _set(session, privacy_policy.KEY, "# 隐私政策\n\n自定义正文。")
    resp = client.get("/api/site/privacy-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == "# 隐私政策\n\n自定义正文。"
    assert body["has_custom"] is True


# ---------------------------------------------------------------- 管理端

def test_admin_get_returns_empty_and_default_draft(client):
    resp = client.get("/api/admin/privacy-policy/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == ""
    assert body["has_custom"] is False
    assert body["default_markdown"] == privacy_policy.DEFAULT_MARKDOWN


def test_admin_put_saves_and_public_reflects(client, session):
    resp = client.put("/api/admin/privacy-policy/content",
                      json={"content": "# 隐私政策\n\n新正文。"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "# 隐私政策\n\n新正文。"
    assert body["has_custom"] is True
    row = session.query(models.SystemConfig).filter_by(
        key=privacy_policy.KEY).first()
    assert row is not None and row.value == "# 隐私政策\n\n新正文。"
    pub = client.get("/api/site/privacy-policy").json()
    assert pub["markdown"] == "# 隐私政策\n\n新正文。"


def test_admin_put_empty_clears_back_to_builtin(client, session):
    _set(session, privacy_policy.KEY, "# 旧正文")
    resp = client.put("/api/admin/privacy-policy/content",
                      json={"content": ""})
    assert resp.status_code == 200
    assert resp.json()["has_custom"] is False
    row = session.query(models.SystemConfig).filter_by(
        key=privacy_policy.KEY).first()
    assert row is None
    pub = client.get("/api/site/privacy-policy").json()
    assert pub["markdown"] == "" and pub["has_custom"] is False


# ---------------------------------------------------------------- 家族校验

def test_config_service_rejects_unknown_key(session):
    with pytest.raises(ConfigValueError):
        config_service.save_config(
            session, {"privacy_policy_md_typo": "x"},
            schema="privacy_policy")


def test_default_markdown_keeps_current_text():
    md = privacy_policy.DEFAULT_MARKDOWN
    # 中文段六条与英文段八条的关键句逐一在位（转写不丢字）
    for snippet in (
        "以下内容适用于你通过 Aetrix 绑定自己的谷歌盘的情形。",
        "我们访问什么",
        "用来干什么",
        "凭据怎么存",
        "数据去向",
        "你随时能停",
        "联系开发者",
        "Data Protection and Security Measures",
        "Encryption at rest.",
        "Tokens stay on the server.",
        "Encryption in transit.",
        "Hardened authorization flow.",
        "Least privilege.",
        "Per-user isolation.",
        "Revocation when you disconnect.",
        "Time-limited link sharing for playback.",
        "removed after 2 hours of inactivity",
    ):
        assert snippet in md, snippet
