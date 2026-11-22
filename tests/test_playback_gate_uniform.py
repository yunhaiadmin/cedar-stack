# -*- coding: utf-8 -*-
"""播放门禁无豁免（2026-11-14 定版 + 2026-11-22 改版）。

2026-11-03 的「无界（CMS）片库免绑定」随 CMS 来源删除一并撤销：
- 全部条目一律过门禁（``binding_required_for_item`` 恒 True），
  包括存量旧 CMS 库形态的条目（库行 ``upstream_source_ids`` 非空
  也不再免门禁）；
- 2026-11-22 改版：门禁判定对象由绑盘换成订阅资格
  （has_play_grant → view_grant）——无订阅 403 + 订阅引导文案，
  有绑盘也一样；有订阅（含本文件 BOUND 种子）放行；
- 判定只放 playback_gate 一处（binding_required_for_item /
  sign_allowed_for_item）；
- 查不到库 / 条目无 library_id → 需要门禁（fail-closed）。

覆盖：判定入口矩阵、ensure 按条目口径、详情签名判定矩阵，
以及真 TestClient 全链路（PlaybackInfo、详情线路签名，
全部走本地库形态，本文件只钉门禁口径）。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import types
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import playback_gate as gate


# ---------------------------------------------------------------- 单元层


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(base_models.ServerRealm(
        id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    yield session
    session.close()


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    return user


def _binding(db, user_id, status="active", provider="drive"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label="盘",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _subscription(db, user_id):
    from datetime import datetime, timedelta

    plan = base_models.SubscriptionPlan(
        name=f"plan-{user_id}", price=1, duration_days=30)
    db.add(plan)
    db.flush()
    now = datetime.now()
    sub = base_models.UserSubscription(
        user_id=user_id, plan_id=plan.id, realm_id=1,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), status="active")
    db.add(sub)
    db.commit()
    return sub


def _library(db, name, upstream_source_ids=""):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     upstream_source_ids=upstream_source_ids)
    db.add(lib)
    db.commit()
    return lib


def _item(db, lib_id, name="片", file_path=None):
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib_id, item_type="movie",
        name=name, file_path=file_path)
    db.add(item)
    db.commit()
    return item


def _blocked(fn, *args, **kwargs):
    with pytest.raises(HTTPException) as excinfo:
        fn(*args, **kwargs)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE


def test_binding_required_former_cms_item_is_true(db):
    # 存量旧 CMS 库形态（upstream_source_ids 非空）：下线后不再免门禁
    lib = _library(db, "旧cms库", upstream_source_ids="7,9")
    item = _item(db, lib.id)
    assert gate.binding_required_for_item(db, item) is True


def test_binding_required_local_item_is_true(db):
    lib = _library(db, "本地库", upstream_source_ids="")
    item = _item(db, lib.id)
    assert gate.binding_required_for_item(db, item) is True


def test_binding_required_fail_closed(db):
    assert gate.binding_required_for_item(db, None) is True
    orphan = types.SimpleNamespace(library_id=None)
    assert gate.binding_required_for_item(db, orphan) is True
    no_attr = types.SimpleNamespace()
    assert gate.binding_required_for_item(db, no_attr) is True
    dangling = _item(db, 999999)
    assert gate.binding_required_for_item(db, dangling) is True


def test_ensure_for_item_unbound_former_cms_blocked(db):
    lib = _library(db, "旧cms库", upstream_source_ids="7")
    item = _item(db, lib.id)
    user = _user(db, "free")
    _blocked(gate.ensure_binding_playback_allowed_for_item, db, user, item)


def test_ensure_for_item_unbound_local_blocked_same_message(db):
    lib = _library(db, "本地库")
    item = _item(db, lib.id)
    user = _user(db, "free")
    _blocked(gate.ensure_binding_playback_allowed_for_item, db, user, item)


def test_ensure_for_item_bound_local_passes(db):
    # 2026-11-22 改版：放行看订阅资格（该用户同时有绑盘与订阅）。
    lib = _library(db, "本地库")
    item = _item(db, lib.id)
    user = _user(db, "bound")
    _binding(db, user.id)
    _subscription(db, user.id)
    gate.ensure_binding_playback_allowed_for_item(db, user, item)


def test_ensure_for_item_bound_former_cms_passes(db):
    lib = _library(db, "旧cms库", upstream_source_ids="7")
    item = _item(db, lib.id)
    user = _user(db, "bound")
    _binding(db, user.id)
    _subscription(db, user.id)
    gate.ensure_binding_playback_allowed_for_item(db, user, item)


def test_ensure_for_item_bound_without_subscription_blocked(db):
    # 改版核心：只有绑盘、没有订阅 → 403（绑盘不再是资格）。
    lib = _library(db, "本地库")
    item = _item(db, lib.id)
    user = _user(db, "boundonly")
    _binding(db, user.id)
    _blocked(gate.ensure_binding_playback_allowed_for_item, db, user, item)


def test_ensure_for_item_keeps_play_sign_consumption(db, monkeypatch):
    # 本地条目 + 已下发签名消费：与 ensure 原口径一致，不追溯。
    lib = _library(db, "本地库")
    item = _item(db, lib.id)
    user = _user(db, "free")
    monkeypatch.setattr(
        "backend.emby_server.play_sign.hotlink_enabled", lambda db: True)
    monkeypatch.setattr(
        "backend.emby_server.play_sign.verify_play_sign",
        lambda uid, item_id, exp, sign: True)
    request = types.SimpleNamespace(
        query_params={"sign": "s", "exp": "9999999999", "uid": "7"},
        path_params={"item_id": "guid-1"})
    gate.ensure_binding_playback_allowed_for_item(db, user, item, request)


def test_sign_allowed_matrix(db):
    former_cms_lib = _library(db, "旧cms库", upstream_source_ids="7")
    local_lib = _library(db, "本地库")
    cms_item = _item(db, former_cms_lib.id)
    local_item = _item(db, local_lib.id)
    bound = _user(db, "bound")
    _binding(db, bound.id)
    _subscription(db, bound.id)
    bound_only = _user(db, "boundonly")
    _binding(db, bound_only.id)
    free = _user(db, "free")
    assert gate.sign_allowed_for_item(db, bound.id, local_item) is True
    assert gate.sign_allowed_for_item(db, bound.id, cms_item) is True
    # 2026-11-22 改版：只有绑盘没有订阅 → 不可签（绑盘不再是资格）
    assert gate.sign_allowed_for_item(db, bound_only.id, local_item) is False
    # 未绑定无订阅：旧 CMS 条目不再免门禁可签（池未开时 False）
    assert gate.sign_allowed_for_item(db, free.id, cms_item) is False
    assert gate.sign_allowed_for_item(db, free.id, local_item) is False
    assert gate.sign_allowed_for_item(db, None, cms_item) is False
    assert gate.sign_allowed_for_item(db, 0, cms_item) is False


# ---------------------------------------------------------------- 全链路

import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import backend.models as models
from backend.database import SessionLocal, init_db
from backend.emby_server import play_sign
from backend.security import hash_password

init_db()

PW = "GateUniform#2026"
BOUND = "gateuniform_bound"
FREE = "gateuniform_free"
LOCAL_LIB_NAME = "门禁本地库"
ITEM_NAMES = ["门禁本地片"]


def _guid() -> str:
    return uuid.uuid4().hex


def _cleanup(db: Session) -> None:
    rows = db.query(em.MediaItem).filter(
        em.MediaItem.name.in_(ITEM_NAMES)).all()
    ids = [r.id for r in rows]
    if ids:
        db.query(em.MediaItem).filter(
            em.MediaItem.id.in_(ids)).delete(synchronize_session=False)
    db.query(em.Library).filter(
        em.Library.name.in_([LOCAL_LIB_NAME])).delete()
    for uname in (BOUND, FREE):
        u = db.query(models.WebUser).filter(
            models.WebUser.username == uname).first()
        if u is not None:
            db.query(models.UserSubscription).filter(
                models.UserSubscription.user_id == u.id).delete()
            db.query(em.UserStorageBinding).filter(
                em.UserStorageBinding.user_id == u.id).delete()
            db.query(em.MountCredential).filter(
                em.MountCredential.owner_user_id == u.id).delete()
            db.query(models.StationMessage).filter(
                (models.StationMessage.to_user_id == u.id)
                | (models.StationMessage.from_user_id == u.id)
            ).delete(synchronize_session=False)
            db.delete(u)
    db.commit()


@pytest.fixture(scope="module")
def seed():
    db = SessionLocal()
    _cleanup(db)
    users = {}
    for uname in (BOUND, FREE):
        u = models.WebUser(
            username=uname, password_hash=hash_password(PW),
            is_active=True, is_staff=True)
        db.add(u)
        db.flush()
        users[uname] = u.id
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=users[BOUND],
        sealed_credential="", key_id="fernet-v1", status="active")
    db.add(cred)
    db.flush()
    db.add(em.UserStorageBinding(
        user_id=users[BOUND], provider="drive", account_label="种子盘",
        credential_id=cred.id, status="active"))
    # 2026-11-22 改版：BOUND 的放行资格来自订阅（绑盘只作并存事实）
    from datetime import datetime, timedelta

    _plan = models.SubscriptionPlan(
        name="门禁统一测试套餐", price=1, duration_days=30)
    db.add(_plan)
    db.flush()
    _now = datetime.now()
    db.add(models.UserSubscription(
        user_id=users[BOUND], plan_id=_plan.id, realm_id=1,
        start_date=_now - timedelta(days=1),
        end_date=_now + timedelta(days=30), status="active"))
    local_lib = em.Library(
        guid=_guid(), name=LOCAL_LIB_NAME, collection_type="movies",
        is_enabled=True)
    db.add(local_lib)
    db.flush()
    local_movie = em.MediaItem(
        guid=_guid(), library_id=local_lib.id, item_type="movie",
        name="门禁本地片",
        file_path="mount://3/电影/门禁本地片/movie.mp4",
        container="mp4", size=2_000_000_000,
        duration_ticks=118 * 60 * 10_000_000,
        probe_status="failed", probe_attempts=99)
    db.add(local_movie)
    db.flush()
    db.commit()
    data = {"bound_id": users[BOUND], "free_id": users[FREE],
            "local_movie": local_movie.guid}
    db.close()
    yield data
    db = SessionLocal()
    try:
        _cleanup(db)
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _auth_header():
    q = chr(34)
    return (f"MediaBrowser Client={q}GateUniformTest{q}, Device={q}iOS{q}, "
            f"DeviceId={q}gateuniform-dev-1{q}, Version={q}1.0{q}")


def _token(client, uname):
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": uname, "Pw": PW},
        headers={"X-Emby-Authorization": _auth_header()})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


@pytest.fixture(scope="module")
def bound_token(client, seed):
    return _token(client, BOUND)


@pytest.fixture(scope="module")
def free_token(client, seed):
    return _token(client, FREE)


def _h(token):
    return {"X-Emby-Token": token}


def _qs(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _upstream_line(body):
    return [s for s in body.get("MediaSources") or []
            if ":upstream" in str(s.get("Id", ""))]


def test_playbackinfo_unbound_local_403_same_message(
        client, free_token, seed):
    r = client.post(f"/emby/Items/{seed['local_movie']}/PlaybackInfo",
                    headers=_h(free_token), json={})
    assert r.status_code == 403, r.text
    assert gate.GATE_MESSAGE in r.text


def test_playbackinfo_bound_local_still_200(client, bound_token, seed):
    r = client.post(f"/emby/Items/{seed['local_movie']}/PlaybackInfo",
                    headers=_h(bound_token), json={})
    assert r.status_code == 200, r.text
    assert _upstream_line(r.json()) == []
    assert len(r.json()["MediaSources"]) >= 1


def test_detail_unbound_local_line_unsigned(client, free_token, seed):
    r = client.get(f"/emby/Items/{seed['local_movie']}",
                   headers=_h(free_token))
    assert r.status_code == 200, r.text
    lines = r.json().get("MediaSources") or []
    assert len(lines) == 1
    assert ":upstream" not in str(lines[0].get("Id", ""))
    q = _qs(lines[0]["DirectStreamUrl"])
    assert "sign" not in q and "exp" not in q and "uid" not in q
