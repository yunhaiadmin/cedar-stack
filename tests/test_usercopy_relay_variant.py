# -*- coding: utf-8 -*-
"""用户 Drive 副本中继变体 usercopy_relay（网页软解取流修复）。

生产实证（2026-11-16）：挂载原件条目《爱上平行时空的你》对 xiaye
只有一份 ready 的本人 Drive 转存副本；PlaybackInfo 清单只算出
[drive_direct] 一条 → 单源下发无变体标记 → video_stream 无变体走
fast302 到副本的免凭证 usercontent 直链 → 网页软解 fetch 无 CORS
取不到字节，且前端回落探测的 byos_drive_relay / drive_relay 两条
候选对此条目双双 409，软解必死。

钉住的口径：
- 清单层：fid 在位 + 本人副本信号成立（该用户有 ready 的本人
  Drive 副本且副本中继后路开关开）→ drive_direct 之后追加
  usercopy_relay（本人令牌中继、is_remote=False、needs_relay=True）；
  信号不成立时清单与现行逐字一致（信号不外溢到 byos / 无 fid 条目）。
- PlaybackInfo：副本在位时双源下发 [drive_direct, usercopy_relay]，
  第二条 URL 带 variant 双保险；无副本时单源旧形态不变。
- 取流：显式 variant=usercopy_relay → 本人绑定令牌中继同一份副本
  出字节，绝不咨询共享 SA 池、绝不回落直链/共享；无副本时该变体
  不在清单 → 409 明示；本人令牌取不到 → 503 明示、中继不调用。

全部离线：令牌、中继、门禁、选路全部桩掉，不碰真 Drive、不读真库。
harness 仿 tests/test_byos_direct_gray.py / test_play_variants.py。
"""
import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import api, play_variants
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import transfer_variant as _tv
from backend.emby_server.drive_oauth import DriveOAuthError

ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"


def _run(coro):
    return asyncio.run(coro)


def _stub_store(monkeypatch, values):
    def fake_get(db, key, default="", **kwargs):
        return values.get(key, default)

    monkeypatch.setattr(_strategy.store, "get_value", fake_get)


def _base_store(monkeypatch, relay="false"):
    _stub_store(monkeypatch, {
        _strategy.KEY_DRIVE_DIRECT_PLAY: "true",
        _strategy.KEY_DRIVE_RELAY: relay,
        _strategy.KEY_BYOS_DIRECT_GRAY: "true",
    })


def _copy_row(**over):
    row = {"id": 5899, "owner_scope": "user", "owner_ref": 11,
           "variant_drive_id": "userdrive:11",
           "variant_file_id": COPY_FID, "status": "ready"}
    row.update(over)
    return SimpleNamespace(**row)


# ---------- 清单层（纯函数，桩 store） ----------

def _mount_item():
    return SimpleNamespace(
        guid="g-uc", drive_file_id=ORIG_FID, container="mkv",
        file_path="mount://3/MoviePilot/电影/测试片 (2025)/测试片.mkv")


def _names(item, **kw):
    return [v.name for v in play_variants.list_variants(None, item, **kw)]


def test_list_user_copy_adds_relay_after_direct(monkeypatch):
    _base_store(monkeypatch)
    names = _names(_mount_item(), drive_mount_locatable=True,
                   user_copy_available=True)
    assert names == [play_variants.DRIVE_DIRECT,
                     play_variants.USERCOPY_RELAY]
    variant = play_variants._VARIANTS[play_variants.USERCOPY_RELAY]
    assert variant.is_remote is False and variant.needs_relay is True


def test_list_no_user_copy_signal_matches_current(monkeypatch):
    _base_store(monkeypatch)
    # 信号不成立：与现行逐字一致（生产形态：只剩直链一条）。
    assert _names(_mount_item(), drive_mount_locatable=True) == [
        play_variants.DRIVE_DIRECT]
    assert _names(_mount_item(), drive_mount_locatable=True,
                  user_copy_available=False) == [
        play_variants.DRIVE_DIRECT]


def test_list_user_copy_with_shared_relay_on(monkeypatch):
    _base_store(monkeypatch, relay="true")
    names = _names(_mount_item(), drive_mount_locatable=True,
                   user_copy_available=True)
    assert names == [play_variants.DRIVE_DIRECT,
                     play_variants.DRIVE_RELAY,
                     play_variants.USERCOPY_RELAY]


def test_list_user_copy_signal_no_leak_to_byos(monkeypatch):
    _base_store(monkeypatch)
    byos_item = SimpleNamespace(guid="g-b", drive_file_id="FID7",
                                container="mkv",
                                file_path="byos://7/movie.mkv")
    names = _names(byos_item, user_drive_byos_available=True,
                   user_copy_available=True)
    assert play_variants.USERCOPY_RELAY not in names


def test_list_user_copy_signal_needs_fid(monkeypatch):
    _base_store(monkeypatch)
    nofid = SimpleNamespace(guid="g-m", drive_file_id="", container="mkv",
                            file_path="mount://3/x.mkv")
    assert _names(nofid, user_copy_available=True) == [
        play_variants.MOUNT_RELAY]


def test_resolve_usercopy_relay_variant():
    assert play_variants.resolve_requested_variant(
        {"variant": "usercopy_relay"},
        ["drive_direct", "usercopy_relay"]) == ("usercopy_relay", None)
    # 无副本用户的清单里没有它 → 已知变体但当前不可用，409 明示。
    assert play_variants.resolve_requested_variant(
        {"variant": "usercopy_relay"}, ["drive_direct"]) == (None, 409)


# ---------- PlaybackInfo 下发（内存库 + 桩，仿 test_play_variants） ----------

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.emby_server import models as em
from backend.emby_server import probe_worker, stream_accel


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _request(path="/Items/x/PlaybackInfo"):
    scope = {"type": "http", "method": "GET", "path": path,
             "raw_path": path.encode(), "query_string": b"",
             "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _pi_item(guid):
    item = em.MediaItem()
    item.guid = guid
    item.name = "爱上平行时空的你"
    item.file_path = "mount://3/MoviePilot/电影/测试片.mkv"
    item.drive_file_id = ORIG_FID
    item.item_type = "movie"
    item.size = 4_600_000_000
    item.duration_ticks = 36_000_000_000
    item.bitrate = 2_000_000
    return item


def _call_pi(monkeypatch, db, item, *, copy_row):
    user = SimpleNamespace(id=11, is_staff=False)
    monkeypatch.setattr(api, "_require_visible_item",
                        lambda db_, u_, iid: item)
    monkeypatch.setattr(api, "ensure_playback_allowed",
                        lambda db_, u_: None)
    monkeypatch.setattr(
        "backend.emby_server.playback_gate.ensure_binding_playback_allowed",
        lambda db_, u_, r=None: None)
    monkeypatch.setattr(probe_worker, "maybe_enqueue",
                        lambda db_, it: None)
    monkeypatch.setattr(
        stream_accel, "rewrite_playback_urls_unified",
        lambda db_, s, base: (s, None))
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db_, it: "drive-x")
    monkeypatch.setattr(play_variants, "_ENABLED", True)
    _base_store(monkeypatch)
    monkeypatch.setattr(
        _tv, "resolve_play_file_id",
        lambda db_, it, u: (COPY_FID, copy_row) if copy_row
        else (ORIG_FID, None))
    return asyncio.run(api.playback_info(
        item.guid, _request(), user_id="", user=user, db=db))


def test_playback_info_user_copy_two_sources(db, monkeypatch):
    item = _pi_item("guid-uc-pi")
    resp = _call_pi(monkeypatch, db, item, copy_row=_copy_row())
    sources = resp["MediaSources"]
    # 默认源 Id 恒为 guid 本身（与详情 DTO 同值，2026-11-20 定案），
    # 其余变体源带后缀。
    assert [s["Id"] for s in sources] == [
        item.guid,
        f"{item.guid}:usercopy_relay",
    ]
    assert "variant=usercopy_relay" in sources[1]["DirectStreamUrl"]
    assert sources[1]["IsRemote"] is False


def test_playback_info_no_copy_single_source_legacy(db, monkeypatch):
    item = _pi_item("guid-uc-pi-none")
    resp = _call_pi(monkeypatch, db, item, copy_row=None)
    sources = resp["MediaSources"]
    assert [s["Id"] for s in sources] == [item.guid]
    assert "variant=" not in sources[0]["DirectStreamUrl"]


# ---------- video_stream 显式变体取流 ----------

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


def _setup_stream(monkeypatch, *, copy_row, token="USER_TOKEN"):
    from backend.emby_server import delivery_plan as _dp
    from backend.emby_server import drive_changes as _dc
    from backend.emby_server import playback_gate as _gate
    from backend.emby_server import playback_source_health as _psh
    from backend.emby_server import pool_gate as _pool_gate

    item = _stream_item()
    user = SimpleNamespace(id=11)
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
    monkeypatch.setattr(_pool_gate, "large_file_pool_route",
                        lambda db, u, it: False)
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: "drive-x")
    monkeypatch.setattr(api, "_user_drive_byos_available",
                        lambda db, it, u: False)
    monkeypatch.setattr(api, "_pool_copy_available",
                        lambda db, it, u: False)
    monkeypatch.setattr(
        _tv, "resolve_play_file_id",
        lambda db, it, u: (COPY_FID, copy_row) if copy_row
        else (ORIG_FID, None))
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
        raise AssertionError("副本中继不许咨询共享 SA 池")

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_sa)

    from backend.emby_server import drive_oauth as _drive_oauth
    if isinstance(token, Exception):
        def fake_token(db, user_id):
            raise token
    else:
        def fake_token(db, user_id):
            return token
    monkeypatch.setattr(_drive_oauth, "get_token_for_user",
                        fake_token)

    notes = []
    monkeypatch.setattr(api, "_note_play_start_safe",
                        lambda item_, **kwargs: notes.append(kwargs))
    _base_store(monkeypatch)
    return user, relay_calls, notes


def test_stream_usercopy_relay_serves_via_user_token(monkeypatch):
    user, relay, notes = _setup_stream(monkeypatch, copy_row=_copy_row())
    resp = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp.status_code == 200
    # 本人令牌中继同一份副本：drive_id/file_id/令牌三者都对。
    assert relay == [{"drive_id": "userdrive:11", "file_id": COPY_FID,
                      "access_token": "USER_TOKEN"}]
    assert notes and notes[-1]["variant"] == "usercopy_relay"
    assert notes[-1]["result"] == "ok"


def test_stream_usercopy_relay_no_copy_is_409(monkeypatch):
    user, relay, notes = _setup_stream(monkeypatch, copy_row=None)
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            "item1", _stream_request(), user, _FakeDB()))
    # 无副本：变体不在可用清单 → 409 明示，不改走默认条。
    assert ei.value.status_code == 409
    assert relay == []


def test_stream_usercopy_relay_token_failure_is_503(monkeypatch):
    user, relay, notes = _setup_stream(
        monkeypatch, copy_row=_copy_row(),
        token=DriveOAuthError("not_found", "no binding"))
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            "item1", _stream_request(), user, _FakeDB()))
    # 本人令牌取不到 → 503 明示，绝不回落共享 SA 中继或直链。
    assert ei.value.status_code == 503
    assert relay == []
    assert notes and notes[-1]["result"] == "error"
