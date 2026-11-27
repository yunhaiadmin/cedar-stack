# -*- coding: utf-8 -*-
"""安装应用页服务器地址资格门（2026-11-27 用户亲点）。

口径：
- GET /api/user/install-app 的 server_address 原先对任何登录用户
  无条件下发——与新人指南页同一个口子，#542 已堵指南页，本单堵
  安装页：复用播放门禁单点 emby_server.playback_gate.has_play_grant
  判资格（有效订阅或团队播放资格，家庭子账号随主账号）；
- 无资格：payload 整字段不带 server_address，响应全文不许出现
  地址字符串；platforms（公开商店下载链接）不设门、照常下发；
- 有资格：server_address 与从前完全一致（install_app.SERVER_ADDRESS
  常量口径，不改取值来源）；
- 未登录 401。

隔离：内存 SQLite（StaticPool），不碰真库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

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
PATH = "/api/user/install-app"
IOS_URL = "https://example.com/emby-ios"


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
    store.invalidate("install_app_ios_url")
    try:
        yield session
    finally:
        store.invalidate("install_app_ios_url")
        session.close()
        engine.dispose()


def _client(db, user=None):
    from backend.api.install_app import user_install_app_router
    from backend.api.user import get_current_user

    app = FastAPI()
    app.include_router(user_install_app_router)
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


def _configure_ios(db):
    db.add(models.SystemConfig(key="install_app_ios_url", value=IOS_URL))
    db.commit()
    from backend.integrations import store
    store.invalidate("install_app_ios_url")


class TestAuth:
    def test_unauthenticated_401(self, db):
        resp = _client(db).get(PATH)
        assert resp.status_code == 401
        assert ADDRESS not in resp.text


class TestEligibility:
    def test_unsubscribed_no_address_but_platforms(self, db):
        user = _user(db, "ia_plain")
        _configure_ios(db)
        resp = _client(db, user).get(PATH)
        assert resp.status_code == 200
        body = resp.json()
        assert "server_address" not in body
        assert ADDRESS not in resp.text
        # 平台下载链接是公开商店链接，不设门：照常下发
        assert [p["key"] for p in body["platforms"]] == ["ios"]
        assert body["platforms"][0]["url"] == IOS_URL

    def test_subscribed_user_gets_address(self, db):
        user = _user(db, "ia_sub")
        _subscription(db, user.id)
        resp = _client(db, user).get(PATH)
        assert resp.status_code == 200
        assert resp.json()["server_address"] == ADDRESS

    def test_expired_subscription_gets_no_address(self, db):
        user = _user(db, "ia_expired")
        _subscription(db, user.id, expired=True)
        resp = _client(db, user).get(PATH)
        assert "server_address" not in resp.json()
        assert ADDRESS not in resp.text

    def test_sub_account_follows_parent_subscription(self, db):
        parent = _user(db, "ia_parent")
        _subscription(db, parent.id)
        child = _user(db, "ia_child", parent_user_id=parent.id)
        resp = _client(db, child).get(PATH)
        assert resp.status_code == 200
        assert resp.json()["server_address"] == ADDRESS

    def test_sub_account_of_plain_parent_gets_no_address(self, db):
        parent = _user(db, "ia_parent_plain")
        child = _user(db, "ia_child_plain", parent_user_id=parent.id)
        resp = _client(db, child).get(PATH)
        assert "server_address" not in resp.json()
        assert ADDRESS not in resp.text

    def test_team_owner_gets_address(self, db):
        user = _user(db, "ia_team")
        _team(db, user.id)
        resp = _client(db, user).get(PATH)
        assert resp.status_code == 200
        assert resp.json()["server_address"] == ADDRESS
