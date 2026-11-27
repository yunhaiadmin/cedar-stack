# -*- coding: utf-8 -*-
"""闭环 #13 接线契约：BYOS 用户令牌中继的取令牌口规范名。

登记 #13 的审计口径名是 get_token_for_user。这条口在代码里历史
名是 drive_oauth.get_user_drive_access_token（BYOS P2 留口），
video_stream 的中继选路（副本额度后路 + usercopy_relay 变体腿）
经它取当前用户令牌；BYOS 路径绑定腿经同源的绑定级口
get_binding_drive_access_token 钉住路径绑定行。本文件钉住的是
接线契约本身：

- 规范名 get_token_for_user 存在，且与旧名是同一个实现
  （横切只许一套：别名只是同一对象的另一称呼，不许分叉）；
- 规范名行为与既有唯一口逐字一致：只读 scope=user 且
  owner=本人的凭据行；无 active 绑定 → DriveOAuthError
  (not_found) 硬失败，绝不回落 SA 池 / scope=source 行；
- video_stream 的 usercopy_relay 变体取流经规范名取令牌：
  令牌在手 → 本人令牌中继出字节、SA 零咨询；令牌失败 →
  503 明示、中继零调用、不回落直链/共享。

BYOS 路径绑定腿的行为面由 tests/test_byos_user_relay.py 覆盖，
本文件不重复。全部离线：网络与上游全桩，不碰真 Drive。
"""
import asyncio
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.responses import Response

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import drive_oauth as do
from backend.emby_server.secret_sealing import seal_secret


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _seed_drive_binding(db, user_id=7, label="盘", status="active",
                        expires_at=None, refresh="RT-OLD",
                        access="AT-OLD", scope="user", owner=None):
    """直接造双行（仿 test_byos_p2_drive_oauth 的同名夹具）。"""
    token = do.encode_drive_token(
        access, refresh,
        expires_at if expires_at is not None else int(time.time()) + 3600)
    cred = em.MountCredential(
        mount_id=None, scope=scope,
        owner_user_id=user_id if owner is None else owner,
        sealed_credential=seal_secret(token), key_id="fernet-v1",
        status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider="drive", account_label=label,
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding, cred


# ---------- 规范名本身：唯一口、单实现 ----------

def test_get_token_for_user_is_the_single_canonical_port():
    """规范名存在，且与旧名是同一函数对象（不许分叉出第二套）。"""
    assert callable(do.get_token_for_user)
    assert do.get_token_for_user is do.get_user_drive_access_token


def test_get_token_for_user_returns_users_own_token(db):
    _seed_drive_binding(db, 7, "我的盘", access="AT-OWN")
    assert do.get_token_for_user(db, 7) == "AT-OWN"


def test_get_token_for_user_no_binding_is_not_found(db):
    """无 active 绑定 → not_found 硬失败（调用方据此 503 明示）。"""
    _seed_drive_binding(db, 7, "别人的盘")
    with pytest.raises(do.DriveOAuthError) as exc:
        do.get_token_for_user(db, 8)
    assert exc.value.kind == "not_found"


def test_get_token_for_user_never_falls_back_to_source_rows(db):
    """源级行（SA 池口径）纵有令牌也不给出；本人令牌只出本人行。"""
    _seed_drive_binding(db, 7, "我的盘", access="AT-OWN")
    source_cred = em.MountCredential(
        mount_id=999, scope="source", owner_user_id=None,
        sealed_credential=seal_secret(do.encode_drive_token(
            "AT-SOURCE", "RT-SOURCE", int(time.time()) + 3600)),
        key_id="fernet-v1", status="active")
    db.add(source_cred)
    db.commit()
    assert do.get_token_for_user(db, 7) == "AT-OWN"
    with pytest.raises(do.DriveOAuthError) as exc:
        do.get_token_for_user(db, 8)
    assert exc.value.kind == "not_found"


# ---------- video_stream 选路经规范名取令牌（harness 仿
# tests/test_usercopy_relay_variant.py，只改令牌桩的挂名） ----------

from backend.emby_server import api, play_variants
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import transfer_variant as _tv

ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"


def _run(coro):
    return asyncio.run(coro)


class _FakeQuery:
    def __init__(self, row=None):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row=None):
        self._row = row

    def query(self, *args, **kwargs):
        return _FakeQuery(self._row)


def _copy_row(**over):
    row = {"id": 5899, "owner_scope": "user", "owner_ref": 11,
           "variant_drive_id": "userdrive:11",
           "variant_file_id": COPY_FID, "status": "ready"}
    row.update(over)
    return SimpleNamespace(**row)


def _stream_item():
    return SimpleNamespace(
        id=598385, guid="g-uc-stream", name="爱上平行时空的你",
        drive_file_id=ORIG_FID, container="mkv",
        file_path="mount://3/MoviePilot/电影/测试片.mkv")


def _stream_request(query=b"variant=usercopy_relay"):
    scope = {"type": "http", "method": "GET",
             "path": "/emby/Videos/item1/stream",
             "raw_path": b"/emby/Videos/item1/stream",
             "query_string": query, "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _setup_stream(monkeypatch, *, token="USER_TOKEN"):
    """与 test_usercopy_relay_variant._setup_stream 同口径，差别只在
    令牌桩挂规范名 get_token_for_user：本文件钉的就是这条接线——
    选路若还从旧名取令牌，本桩拦不到、断言必红。"""
    from backend.emby_server import delivery_plan as _dp
    from backend.emby_server import drive_changes as _dc
    from backend.emby_server import playback_gate as _gate
    from backend.emby_server import playback_source_health as _psh

    item = _stream_item()
    user = SimpleNamespace(id=11)
    copy_row = _copy_row()
    monkeypatch.setattr(api, "_require_visible_item",
                        lambda db, u, item_id: item)
    monkeypatch.setattr(api, "ensure_playback_allowed",
                        lambda db, u: None)
    monkeypatch.setattr(api, "_enforce_path_access",
                        lambda db, u, it: None)
    monkeypatch.setattr(api, "_enforce_stream_limit",
                        lambda db, req, u, it, item_id: None)
    monkeypatch.setattr(api, "_observe_traffic", lambda resp: resp)
    monkeypatch.setattr(api, "_note_watch_activity",
                        lambda req, u, it, code: None)
    monkeypatch.setattr(api.play_sign, "check_referer",
                        lambda request, db: None)
    monkeypatch.setattr(_gate, "ensure_binding_playback_allowed",
                        lambda db, u, req=None: None)
    monkeypatch.setattr(_gate, "_request_uses_issued_play_sign",
                        lambda req, db: False)
    monkeypatch.setattr(_psh, "item_source_available",
                        lambda db, it: True)
    monkeypatch.setattr(_dp, "plan_ready_leg",
                        lambda db, it, u: None)
    # 贡献池下线后 video_stream 门禁只剩二值门禁 + 条目判定：
    # 条目判定桩成 bound。
    monkeypatch.setattr(_gate, "binding_required_for_item",
                        lambda db, it: True)
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: "drive-x")
    monkeypatch.setattr(api, "_user_drive_byos_available",
                        lambda db, it, u: False)
    monkeypatch.setattr(
        _tv, "resolve_play_file_id",
        lambda db, it, u: (COPY_FID, copy_row))
    monkeypatch.setattr(
        _tv, "ensure_user_copy_gate",
        lambda db, it, u, play_sign_consume=False: {
            "verdict": "proceed", "reason": "has_ready_variant",
            "variant": copy_row})
    monkeypatch.setattr(_tv, "usercopy_quota_probe_cached",
                        lambda uid, fid: False)
    monkeypatch.setattr(_tv, "probe_user_copy_quota_decision",
                        lambda uid, fid: "ok")
    monkeypatch.setattr(_tv, "usercopy_quota_probe_detail",
                        lambda uid, fid: {})

    relay_calls = []

    async def fake_relay(request, drive_id, file_id, media_type="video/mp4",
                         pt=None, access_token=None):
        relay_calls.append({"drive_id": drive_id, "file_id": file_id,
                            "access_token": access_token})
        return Response(content=b"x", media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", fake_relay)

    def fake_sa(drive_id, force_refresh=False):
        raise AssertionError("用户令牌中继不许咨询共享 SA 池")

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_sa)

    if isinstance(token, Exception):
        def fake_token(db, user_id):
            raise token
    else:
        def fake_token(db, user_id):
            return token
    monkeypatch.setattr(do, "get_token_for_user", fake_token)

    notes = []
    monkeypatch.setattr(api, "_note_play_start_safe",
                        lambda item_, **kwargs: notes.append(kwargs))

    def fake_get(db, key, default="", **kwargs):
        values = {
            _strategy.KEY_DRIVE_DIRECT_PLAY: "true",
            _strategy.KEY_DRIVE_RELAY: "false",
            _strategy.KEY_BYOS_DIRECT_GRAY: "true",
        }
        return values.get(key, default)

    monkeypatch.setattr(_strategy.store, "get_value", fake_get)
    return user, relay_calls, notes


def test_stream_usercopy_relay_routes_via_get_token_for_user(monkeypatch):
    user, relay, notes = _setup_stream(monkeypatch)
    resp = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp.status_code == 200
    # 令牌必须经规范名取到并原样进中继腿（drive/file/令牌三对上）。
    assert relay == [{"drive_id": "userdrive:11", "file_id": COPY_FID,
                      "access_token": "USER_TOKEN"}]
    assert notes and notes[-1]["variant"] == "usercopy_relay"
    assert notes[-1]["result"] == "ok"


def test_stream_usercopy_relay_canonical_token_failure_is_503(monkeypatch):
    user, relay, notes = _setup_stream(
        monkeypatch,
        token=do.DriveOAuthError("not_found", "no binding"))
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            "item1", _stream_request(), user, _FakeDB()))
    # 规范名取不到令牌 → 503 明示，中继零调用、不回落。
    assert ei.value.status_code == 503
    assert relay == []
    assert notes and notes[-1]["result"] == "error"
