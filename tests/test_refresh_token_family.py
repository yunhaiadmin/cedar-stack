# -*- coding: utf-8 -*-
"""F8 回归：refresh token 家族（fid）轮换与复用处置。

黑盒审计实证（修复前口径）：同一 refresh 连用两次均 200——当时已有
jti 轮换 + 120s 容忍窗口，但复用处置是「吊销该用户全部 token」
（token_version 自增），一台设备出问题会把同用户所有设备踢下线。

本文件锁定新口径：
1. 登录签发的 access/refresh 带同一 fid（家族 id）；轮换保持 fid 不变。
2. 容忍窗口内的旧 refresh 重试仍放行（P2-6 既有行为，不误杀）。
3. 窗口外复用旧 refresh → 只吊销该家族：该家族的最新 refresh 与
   access 一并失效；用户 token_version 不动；同用户另一台设备
   （另一家族）与其它用户完全不受影响。
4. 无 fid 的存量 refresh（升级前签发）复用时回退既有全量吊销，
   行为与升级前一致；其轮换出的新对自动带 fid 成链。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-refresh-family-key-32chars-min")

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, ratelimit
from backend.api import emby_portal
from backend.database import get_db
from backend.security import (
    _create_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_jti_revoked,
)

PASSWORD = "Family-Pass-123"


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


@pytest.fixture(autouse=True)
def _clear_state():
    ratelimit._limiter._hits.clear()
    ratelimit._login_lockout_guard.clear()
    yield
    ratelimit._limiter._hits.clear()
    ratelimit._login_lockout_guard.clear()


def _client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db):
    u = models.WebUser(
        username="fam_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(PASSWORD),
        email=None,
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, user):
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _refresh(client, refresh_token):
    return client.post("/api/user/auth/refresh",
                       json={"refresh_token": refresh_token})


def _backdate_rotation(db, jti, seconds=121):
    row = db.query(models.RevokedJwt).filter(models.RevokedJwt.jti == jti).first()
    assert row is not None, "轮换应已把旧 jti 写进吊销表"
    row.revoked_at = datetime.now() - timedelta(seconds=seconds)
    db.commit()


def test_tokens_carry_family_id(db):
    client = _client(db)
    user = _user(db)
    pair = _login(client, user)
    access = decode_token(pair["access_token"], expected_type="access")
    refresh = decode_token(pair["refresh_token"], expected_type="refresh")
    assert access.get("fid"), "access 应带 fid"
    assert refresh.get("fid") == access["fid"], "同一登录的 access/refresh 应同家族"
    # 另一次登录是另一个家族
    pair2 = _login(client, user)
    refresh2 = decode_token(pair2["refresh_token"], expected_type="refresh")
    assert refresh2["fid"] != refresh["fid"]


def test_rotation_keeps_family_and_revokes_old_jti(db):
    client = _client(db)
    user = _user(db)
    pair = _login(client, user)
    r1 = pair["refresh_token"]
    fid = decode_token(r1, expected_type="refresh")["fid"]

    resp = _refresh(client, r1)
    assert resp.status_code == 200, resp.text
    pair2 = resp.json()
    assert decode_token(pair2["refresh_token"], expected_type="refresh")["fid"] == fid
    assert decode_token(pair2["access_token"], expected_type="access")["fid"] == fid
    assert is_jti_revoked(db, decode_token(r1, expected_type="refresh")["jti"])


def test_grace_retry_still_allowed(db):
    client = _client(db)
    user = _user(db)
    pair = _login(client, user)
    r1 = pair["refresh_token"]
    assert _refresh(client, r1).status_code == 200
    # 容忍窗口（120s）内携带旧 refresh 重试：良性重复，放行（P2-6）
    resp = _refresh(client, r1)
    assert resp.status_code == 200, resp.text


def test_reuse_outside_grace_revokes_family_only(db):
    client = _client(db)
    user = _user(db)
    pair_a = _login(client, user)          # 设备 A 的家族
    pair_b = _login(client, user)          # 设备 B 的家族（同用户另一设备）
    r_a1 = pair_a["refresh_token"]

    resp = _refresh(client, r_a1)          # A 正常轮换
    assert resp.status_code == 200, resp.text
    pair_a2 = resp.json()

    # 模拟攻击者拿到旧 refresh，在容忍窗口外复用
    _backdate_rotation(db, decode_token(r_a1, expected_type="refresh")["jti"])
    resp = _refresh(client, r_a1)
    assert resp.status_code == 401, resp.text

    # A 家族整链失效：最新 refresh 与已签发 access 都不能再用
    resp = _refresh(client, pair_a2["refresh_token"])
    assert resp.status_code == 401, resp.text
    r = client.get("/api/user/auth/me",
                   headers={"Authorization": f"Bearer {pair_a2['access_token']}"})
    assert r.status_code == 401, r.text

    # 不是全量吊销：token_version 没动，B 家族照常续签与访问
    db.refresh(user)
    assert (user.token_version or 0) == 0
    resp = _refresh(client, pair_b["refresh_token"])
    assert resp.status_code == 200, resp.text
    r = client.get("/api/user/auth/me",
                   headers={"Authorization": f"Bearer {pair_b['access_token']}"})
    assert r.status_code == 200, r.text


def test_legacy_token_without_fid_falls_back_to_full_revoke(db):
    client = _client(db)
    user = _user(db)
    # 升级前签发的 refresh：没有 fid
    legacy = _create_token({"sub": str(user.id), "tv": 0},
                           timedelta(days=30), "refresh")
    assert "fid" not in (decode_token(legacy, expected_type="refresh") or {})

    resp = _refresh(client, legacy)
    assert resp.status_code == 200, resp.text
    new_refresh = resp.json()["refresh_token"]
    # 轮换出的新对自动带 fid 成链
    assert decode_token(new_refresh, expected_type="refresh").get("fid")

    # 窗口外复用无 fid 的旧 token → 回退既有全量吊销（tv 自增）
    _backdate_rotation(db, decode_token(legacy, expected_type="refresh")["jti"])
    resp = _refresh(client, legacy)
    assert resp.status_code == 401, resp.text
    db.refresh(user)
    assert (user.token_version or 0) == 1
    # tv 已变，刚轮换出的新对也随之失效（与升级前行为一致）
    resp = _refresh(client, new_refresh)
    assert resp.status_code == 401, resp.text


def test_create_refresh_token_mints_distinct_families():
    t1 = decode_token(create_refresh_token(1), expected_type="refresh")
    t2 = decode_token(create_refresh_token(1), expected_type="refresh")
    assert t1["fid"] and t2["fid"] and t1["fid"] != t2["fid"]
    t3 = decode_token(create_refresh_token(1, fid="fam-fixed"),
                      expected_type="refresh")
    assert t3["fid"] == "fam-fixed"
