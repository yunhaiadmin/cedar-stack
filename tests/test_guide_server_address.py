# -*- coding: utf-8 -*-
"""新人指南页服务器地址下发（2026-11-27 用户亲点）。

口径：
- 地址不再写前端字面量，改由 GET /api/user/guide/server-address 下发；
- 须登录：未登录 401；
- 只有播放资格（emby_server.playback_gate.has_play_grant：有效订阅
  或团队播放资格，门禁单点复用）才拿到地址；无资格响应不带地址
  字段、响应全文不许出现地址字符串；
- 地址取值顺序：EMBY_PUBLIC_URL（public_url.configured_public_url）
  优先，其次系统配置键 client_server_address，再兜底默认 CDN 口径。

隔离：内存 SQLite（StaticPool），不碰真库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.database import get_db
from backend.emby_server import models as em

ADDRESS = "https://cdn" + ".aetrix.love"
PATH = "/api/user/guide/server-address"


@pytest.fixture(autouse=True)
def _no_public_url_env(monkeypatch):
    # 地址断言要确定性：默认清掉 EMBY_PUBLIC_URL（走配置键/兜底口径），
    # 需要验证 env 优先级的用例自己再 setenv。
    monkeypatch.delenv("EMBY_PUBLIC_URL", raising=False)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(
        id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    from backend.integrations import store
    store.invalidate("client_server_address")
    try:
        yield session
    finally:
        store.invalidate("client_server_address")
        session.close()
        engine.dispose()


def _client(db, user=None):
    from backend.api.guide import user_guide_router
    from backend.api.user import get_current_user

    app = FastAPI()
    app.include_router(user_guide_router)
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _user(db, name, **kw):
    user = models.WebUser(
        username=name, password_hash="x", is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _subscription(db, user_id, *, expired=False):
    plan = models.SubscriptionPlan(
        name=f"plan-{user_id}", price=1, duration_days=30, is_active=True)
    db.add(plan)
    db.flush()
    now = datetime.now()
    if expired:
        start, end = now - timedelta(days=60), now - timedelta(days=30)
    else:
        start, end = now - timedelta(days=1), now + timedelta(days=30)
    sub = models.UserSubscription(
        user_id=user_id, plan_id=plan.id, realm_id=1,
        start_date=start, end_date=end, status="active")
    db.add(sub)
    db.commit()
    return sub


def _team(db, owner_id, *, status="active", expired=False):
    now = datetime.now()
    team = models.Team(
        name="", owner_id=owner_id, status=status,
        plan_started_at=now - timedelta(days=1),
        plan_expires_at=now + timedelta(days=-1 if expired else 30))
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _set_address_config(db, value):
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == "client_server_address").first()
    if row:
        row.value = value
    else:
        db.add(models.SystemConfig(key="client_server_address", value=value))
    db.commit()
    from backend.integrations import store
    store.invalidate("client_server_address")


class TestAuth:
    def test_unauthenticated_401(self, db):
        resp = _client(db).get(PATH)
        assert resp.status_code == 401
        assert ADDRESS not in resp.text


class TestEligibility:
    def test_subscribed_user_gets_address(self, db):
        user = _user(db, "ga_sub")
        _subscription(db, user.id)
        resp = _client(db, user).get(PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["visible"] is True
        assert body["server_address"] == ADDRESS

    def test_unsubscribed_user_gets_no_address(self, db):
        user = _user(db, "ga_plain")
        resp = _client(db, user).get(PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["visible"] is False
        assert "server_address" not in body
        assert ADDRESS not in resp.text

    def test_expired_subscription_gets_no_address(self, db):
        user = _user(db, "ga_expired")
        _subscription(db, user.id, expired=True)
        resp = _client(db, user).get(PATH)
        assert resp.json()["visible"] is False
        assert ADDRESS not in resp.text

    def test_team_owner_gets_address(self, db):
        user = _user(db, "ga_team")
        _team(db, user.id)
        resp = _client(db, user).get(PATH)
        body = resp.json()
        assert body["visible"] is True
        assert body["server_address"] == ADDRESS

    def test_team_pending_activation_gets_no_address(self, db):
        user = _user(db, "ga_team_pending")
        _team(db, user.id, status="pending_activation")
        resp = _client(db, user).get(PATH)
        assert resp.json()["visible"] is False
        assert ADDRESS not in resp.text

    def test_team_expired_gets_no_address(self, db):
        user = _user(db, "ga_team_expired")
        _team(db, user.id, expired=True)
        resp = _client(db, user).get(PATH)
        assert resp.json()["visible"] is False
        assert ADDRESS not in resp.text


class TestAddressSource:
    def test_config_key_used_when_no_public_url(self, db):
        user = _user(db, "ga_cfg")
        _subscription(db, user.id)
        _set_address_config(db, "https://guide.example.com")
        resp = _client(db, user).get(PATH)
        assert resp.json()["server_address"] == "https://guide.example.com"

    def test_public_url_env_wins_over_config_key(self, db, monkeypatch):
        user = _user(db, "ga_env")
        _subscription(db, user.id)
        _set_address_config(db, "https://guide.example.com")
        monkeypatch.setenv("EMBY_PUBLIC_URL", "https://public.example.com")
        resp = _client(db, user).get(PATH)
        assert resp.json()["server_address"] == "https://public.example.com"

    def test_schema_family_registered(self):
        from backend import config_service

        specs = config_service.SCHEMAS.get("guide")
        assert specs is not None
        assert specs.get("client_server_address") == ("str",)

    def test_payload_shape_json(self, db):
        user = _user(db, "ga_shape")
        _subscription(db, user.id)
        resp = _client(db, user).get(PATH)
        assert json.loads(resp.text)["server_address"] == ADDRESS
