# -*- coding: utf-8 -*-
"""本人盘直链灰度（byos_direct_gray_enabled）。

用户口径（2026-11-12 17:15 点选）：本人绑盘副本（Drive）直链升为
默认第一线路、中继保留同变体第二线路兜底不删；池副本与挂载/paul/
远程文件维持中继、不试直链；115 本人盘本来就是直链、不动。

钉住的口径：
- 开关 byos_direct_gray_enabled 进 playback_strategy 的 KEY_* 家族
  （store 热读、默认关、fail-safe 回关），只加配置键不加 UI。
- 开关开 + byos 可用 + 有 fid → 清单第一 byos_drive_direct
  （新增变体：凭本人令牌给本人文件授 anyone-reader 后 302 免凭证
  usercontent，is_remote=True / needs_relay=False）、第二
  byos_drive_relay；开关关 → 清单与现行逐字一致。
- quota 已知（探针已判该副本公开链回额度页）时清单不列直链：
  公开直链必死，列出来就是把死链当第一线路（可用性判定，不是
  发链后静默改走）。
- 取流：显式选 byos_drive_direct 且开关开 → ensure anyone-reader
  成功后 302 usercontent；ensure 失败 → 明示错误（503），绝不
  静默改走中继；开关关时该变体不在清单 → resolve 409 明示。
- 池副本 / 挂载条目恒不出现 byos 直链（信号不外溢）。

全部离线：令牌、中继、授公开全部桩掉，不碰真 Drive、不读真库。
video_stream 桩 harness 仿 tests/test_byos_drive_play_variant.py。
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


def _run(coro):
    return asyncio.run(coro)


# ---------- 开关 getter（同家族口径：默认关、fail-safe 回关） ----------

def _stub_store(monkeypatch, values):
    def fake_get(db, key, default="", **kwargs):
        return values.get(key, default)

    monkeypatch.setattr(_strategy.store, "get_value", fake_get)


def test_gray_switch_default_off_and_on(monkeypatch):
    _stub_store(monkeypatch, {})
    assert _strategy.byos_direct_gray_enabled(None) is False
    _stub_store(monkeypatch, {_strategy.KEY_BYOS_DIRECT_GRAY: "true"})
    assert _strategy.byos_direct_gray_enabled(None) is True
    _stub_store(monkeypatch, {_strategy.KEY_BYOS_DIRECT_GRAY: "0"})
    assert _strategy.byos_direct_gray_enabled(None) is False


def test_gray_switch_read_failure_fails_closed(monkeypatch):
    def boom(db, key, default="", **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(_strategy.store, "get_value", boom)
    assert _strategy.byos_direct_gray_enabled(None) is False


# ---------- 清单层（纯函数，桩 store） ----------

def _drive_item():
    return SimpleNamespace(guid="g-byos", drive_file_id="FID7",
                           container="mkv",
                           file_path="byos://7/movie.mkv")


def _gray_store(monkeypatch, gray):
    _stub_store(monkeypatch, {
        _strategy.KEY_DRIVE_DIRECT_PLAY: "true",
        _strategy.KEY_DRIVE_RELAY: "false",
        _strategy.KEY_BYOS_DIRECT_GRAY: "true" if gray else "false",
    })


def _names(db, item, **kw):
    return [v.name for v in play_variants.list_variants(db, item, **kw)]


def test_list_gray_on_direct_first_relay_second(monkeypatch):
    _gray_store(monkeypatch, gray=True)
    names = _names(None, _drive_item(), user_drive_byos_available=True)
    assert names[0] == play_variants.BYOS_DRIVE_DIRECT
    assert names[1] == play_variants.BYOS_DRIVE_RELAY
    variant = play_variants._VARIANTS[play_variants.BYOS_DRIVE_DIRECT]
    assert variant.is_remote is True and variant.needs_relay is False


def test_list_gray_off_matches_current_behavior(monkeypatch):
    _gray_store(monkeypatch, gray=False)
    # 现行：主线 drive_direct 之后追加 byos_drive_relay，无直链变体。
    assert _names(None, _drive_item(), user_drive_byos_available=True) == [
        play_variants.DRIVE_DIRECT, play_variants.BYOS_DRIVE_RELAY]
    assert _names(None, _drive_item()) == [play_variants.DRIVE_DIRECT]


def test_list_gray_on_no_byos_signal_no_direct(monkeypatch):
    _gray_store(monkeypatch, gray=True)
    # 未绑 / 信号不成立：开关开也不出现 byos 直链（信号不外溢）。
    names = _names(None, _drive_item(), user_drive_byos_available=False)
    assert play_variants.BYOS_DRIVE_DIRECT not in names
    assert play_variants.BYOS_DRIVE_RELAY not in names


def test_list_gray_on_mount_item_stays_relay_only(monkeypatch):
    _gray_store(monkeypatch, gray=True)
    mount_item = SimpleNamespace(guid="g-m", drive_file_id="",
                                 file_path="mount://3/x.mkv")
    # 挂载条目即使误传 byos 信号（无 fid 兜底）也只有 mount_relay。
    assert _names(None, mount_item, user_drive_byos_available=True) == [
        play_variants.MOUNT_RELAY]


def test_list_gray_on_pool_copy_shape_unchanged(monkeypatch):
    _gray_store(monkeypatch, gray=True)
    pool_item = SimpleNamespace(guid="g-p", drive_file_id="FIDP",
                                file_path="mount://3/pool.mkv")
    names = _names(None, pool_item, pool_copy_available=True)
    # 池变体仍以既有形态出现在末尾，绝不给池新增直链外行为。
    assert names[-1] == play_variants.POOL_COPY
    assert play_variants.BYOS_DRIVE_DIRECT not in names


def test_list_gray_on_quota_known_hides_direct(monkeypatch):
    _gray_store(monkeypatch, gray=True)
    names = _names(None, _drive_item(), user_drive_byos_available=True,
                   byos_quota_known=True)
    # 探针已判 quota：公开直链必死，清单不列直链，中继仍在。
    assert play_variants.BYOS_DRIVE_DIRECT not in names
    assert play_variants.BYOS_DRIVE_RELAY in names


def test_resolve_byos_direct_variant():
    assert play_variants.resolve_requested_variant(
        {"variant": "byos_drive_direct"},
        ["byos_drive_direct", "byos_drive_relay"]) == (
            "byos_drive_direct", None)
    # 开关关时的清单里没有它 → 已知变体但当前不可用，409 明示。
    assert play_variants.resolve_requested_variant(
        {"variant": "byos_drive_direct"},
        ["drive_direct", "byos_drive_relay"]) == (None, 409)
    # 显式选第二位中继在清单内可播（无 409）。
    assert play_variants.resolve_requested_variant(
        {"variant": "byos_drive_relay"},
        ["byos_drive_direct", "byos_drive_relay"]) == (
            "byos_drive_relay", None)


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


def _binding(**over):
    row = {"id": 7, "user_id": 11, "provider": "drive",
           "status": "active"}
    row.update(over)
    return SimpleNamespace(**row)


def _user():
    return SimpleNamespace(id=11)


def _fake_request(query=b"variant=byos_drive_direct"):
    scope = {"type": "http", "method": "GET",
             "path": "/emby/Videos/item1/stream",
             "raw_path": b"/emby/Videos/item1/stream",
             "query_string": query, "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _setup_video_stream(monkeypatch, *, gray=True, ensure_ok=True):
    from backend.emby_server import drive_changes as _dc

    monkeypatch.setattr(api, "_require_visible_item",
                        lambda db, u, item_id: _drive_item())
    monkeypatch.setattr(api, "ensure_playback_allowed", lambda db, u: None)
    monkeypatch.setattr(api, "_enforce_path_access",
                        lambda db, u, it: None)
    monkeypatch.setattr(api, "_enforce_stream_limit",
                        lambda db, req, u, it, item_id: None)
    monkeypatch.setattr(api, "_observe_traffic", lambda resp: resp)
    monkeypatch.setattr(api.play_sign, "check_referer",
                        lambda request, db: None)

    relay_calls = []

    async def fake_relay(request, drive_id, file_id, media_type="video/mp4",
                         pt=None, access_token=None):
        relay_calls.append({"drive_id": drive_id, "file_id": file_id})
        return Response(content=b"x", media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", fake_relay)

    def fake_sa(drive_id, force_refresh=False):
        raise AssertionError("byos 直链路上不许咨询共享 SA 池")

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_sa)

    ensure_calls = []

    def fake_ensure(db, user_id, file_id):
        ensure_calls.append({"user_id": user_id, "file_id": file_id})
        return ensure_ok

    monkeypatch.setattr(_tv, "ensure_user_copy_public", fake_ensure)

    notes = []
    monkeypatch.setattr(api, "_note_play_start_safe",
                        lambda item_, **kwargs: notes.append(kwargs))

    _stub_store(monkeypatch, {
        _strategy.KEY_DRIVE_DIRECT_PLAY: "true",
        _strategy.KEY_DRIVE_RELAY: "true",
        _strategy.KEY_BYOS_DIRECT_GRAY: "true" if gray else "false",
    })
    return relay_calls, ensure_calls, notes


def test_stream_byos_direct_302_after_ensure(monkeypatch):
    from backend.emby_server.drive_auth import (
        drive_usercontent_download_url)
    relay, ensure, notes = _setup_video_stream(monkeypatch)
    resp = _run(api.video_stream(
        "item1", _fake_request(), _user(), _FakeDB(_binding())))
    assert resp.status_code == 302
    assert resp.headers["location"] == drive_usercontent_download_url("FID7")
    # ensure 用本人身份对本人文件授 anyone-reader，不走中继、不碰 SA。
    assert ensure == [{"user_id": 11, "file_id": "FID7"}]
    assert relay == []
    assert notes and notes[-1]["variant"] == "byos_drive_direct"
    assert notes[-1]["result"] == "ok"


def test_stream_byos_direct_ensure_failure_no_silent_relay(monkeypatch):
    relay, ensure, notes = _setup_video_stream(
        monkeypatch, ensure_ok=False)
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            "item1", _fake_request(), _user(), _FakeDB(_binding())))
    # ensure 失败 → 明示错误，绝不静默改走中继。
    assert ei.value.status_code == 503
    assert relay == []
    assert notes and notes[-1]["result"] == "error"


def test_stream_byos_direct_gray_off_is_409(monkeypatch):
    relay, ensure, notes = _setup_video_stream(monkeypatch, gray=False)
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            "item1", _fake_request(), _user(), _FakeDB(_binding())))
    # 开关关：变体不在可用清单，resolve 阶段 409 明示、不改走默认条。
    assert ei.value.status_code == 409
    assert relay == [] and ensure == []
