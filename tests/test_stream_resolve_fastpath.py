# -*- coding: utf-8 -*-
"""续段请求快速通道（2026-11-17 网页播放中继取流优化）回归测试。

背景：网页软解（AVPlayer）分段取流时，每个 /stream 请求都重跑
video_stream 全链（门禁→选路→令牌现取→逐跳跟随），实测单次
resolve 343–1142ms，缓冲节律被固定开销拖死。优化口径（用户
2026-11-17 拍板）：首次请求全链通过后，把解析结果（最终上游
URL / 令牌 / 选路）按 (用户, 条目, 变体) 存短时缓存；续段请求
命中时只做便宜校验——play_sign 验签（必做）或 active 绑定
关系校验——直接出流。

本文件钉住：
- 首次全链请求落缓存（drive_final 描述符，最终 URL 来自中继腿
  的 capture 回填）；
- 续段命中时令牌获取与选路各只发生一次（计数断言，非墙钟）；
- 带签名但验签不过 → 绝不走快速通道（回全链按原口径 403）；
- 签名消费成立时不要求绑定（已下发链接不追溯口径）；
- 不同用户同条目缓存不串（键含用户维度，各自走各自全链）；
- 快速通道出流失败 → 描述符失效 + 回落全链；
- 缓存 TTL：≤ 签名剩余有效期、过短不存、env 0 = 关闭。

全部离线：网络与上游全桩，harness 仿
tests/test_byos_token_relay_wiring.py。
"""
import asyncio
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import api, play_variants
from backend.emby_server import drive_oauth as do
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import streaming as _streaming
from backend.emby_server import transfer_variant as _tv

ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"
FINAL_URL = ("https://drive.usercontent.google.com/download"
             "?id=copy&export=download&confirm=t")


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


class _Pt:
    def mark(self, *args):
        pass

    def elapsed_ms(self):
        return 0


@pytest.fixture(autouse=True)
def _clean_resolve_cache():
    """缓存是进程级共享状态：每条用例前后清掉本文件用到的键。"""
    keys = [(11, "item1", "usercopy_relay"), (12, "item1", "usercopy_relay"),
            (11, "itemX", "usercopy_relay"), (11, "itemX", "drive_relay")]
    for k in keys:
        _streaming.stream_resolve_cache_invalidate(*k)
    yield
    for k in keys:
        _streaming.stream_resolve_cache_invalidate(*k)


def _setup_stream(monkeypatch, *, sign_ok=False, bound=True):
    """与 test_byos_token_relay_wiring 同口径的全链桩，另加计数器
    与快速通道出流桩（serve_mount_relay_async 直打最终 URL）。"""
    from backend.emby_server import delivery_plan as _dp
    from backend.emby_server import drive_changes as _dc
    from backend.emby_server import playback_gate as _gate
    from backend.emby_server import playback_source_health as _psh

    item = _stream_item()
    user = SimpleNamespace(id=11)
    copy_row = _copy_row()
    counts = {"token": 0, "resolve": 0, "relay": 0, "final_serve": 0}
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
                        lambda req, db: sign_ok)
    monkeypatch.setattr(_gate, "has_active_binding",
                        lambda db, uid: bound)
    # 2026-11-22 门禁改版：快速通道无签名时改复核订阅资格
    # （has_play_grant），假 DB 跑不动真判定——资格同样由 bound 代表。
    monkeypatch.setattr(_gate, "has_play_grant",
                        lambda db, u: bound)
    monkeypatch.setattr(_psh, "item_source_available",
                        lambda db, it: True)
    monkeypatch.setattr(_dp, "plan_ready_leg",
                        lambda db, it, u: None)
    # 贡献池下线后 video_stream 门禁走 playback_gate 按条目
    # 入口：本文件的假条目是需过门禁的 Drive 条目，条目判定恒
    # True（bound 参数只代表用户侧资格/绑定，与原决策入口恒
    # kind="bound" 的桩同口径）。
    monkeypatch.setattr(_gate, "binding_required_for_item",
                        lambda db, it: True)
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: "drive-x")
    monkeypatch.setattr(api, "_user_drive_byos_available",
                        lambda db, it, u: False)

    def fake_resolve(db, it, u):
        counts["resolve"] += 1
        return (COPY_FID, copy_row)

    monkeypatch.setattr(_tv, "resolve_play_file_id", fake_resolve)
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
                         pt=None, access_token=None, **kwargs):
        counts["relay"] += 1
        relay_calls.append({"drive_id": drive_id, "file_id": file_id,
                            "access_token": access_token})
        # 仿真实中继腿：把跟随到的最终 URL 回填 capture 盒
        box = _streaming._RELAY_FINAL_CAPTURE.get()
        if box is not None:
            box["final_url"] = FINAL_URL
        return Response(content=b"x", media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", fake_relay)

    final_calls = []

    async def fake_final_serve(url, request, headers=None,
                               media_type="video/mp4", pt=None,
                               sniff_quota_page=False):
        counts["final_serve"] += 1
        final_calls.append({"url": url, "headers": headers})
        return Response(content=b"seg", media_type="video/mp4")

    monkeypatch.setattr(_streaming, "serve_mount_relay_async",
                        fake_final_serve)

    def fake_sa(drive_id, force_refresh=False):
        raise AssertionError("用户令牌中继不许咨询共享 SA 池")

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_sa)

    def fake_token(db, user_id):
        counts["token"] += 1
        return f"TOKEN{user_id}"

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
    return user, counts, relay_calls, final_calls, notes


# ---------- 命中行为：令牌/选路只发生一次，续段直打最终 URL ----------

def test_first_request_stores_descriptor_second_hits_fastpath(monkeypatch):
    user, counts, relay, final_calls, notes = _setup_stream(monkeypatch)
    resp1 = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp1.status_code == 200
    assert counts["token"] == 1 and counts["relay"] == 1
    assert counts["final_serve"] == 0
    first_counts = dict(counts)
    # 首次全链后描述符已落缓存：最终 URL 形态（跟随只发生一次的证据）
    desc = _streaming.stream_resolve_cache_get(
        11, "item1", "usercopy_relay")
    assert desc is not None and desc["kind"] == "drive_final"
    assert desc["final_url"] == FINAL_URL

    resp2 = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp2.status_code == 200
    # 续段命中：令牌不再现取、选路不再重跑、全链中继不再调用
    # （相对首次的增量全为零）；改由最终 URL 直打（无
    # Authorization 头——签名 URL 自带授权）。
    assert counts["token"] == first_counts["token"]
    assert counts["resolve"] == first_counts["resolve"]
    assert counts["relay"] == first_counts["relay"]
    assert counts["final_serve"] == 1
    assert final_calls[0]["url"] == FINAL_URL
    assert "Authorization" not in (final_calls[0]["headers"] or {})
    # 打点口径不变：两次请求各记一条起播事件
    assert len(notes) == 2
    assert notes[-1]["variant"] == "usercopy_relay"
    assert notes[-1]["result"] == "ok"


def test_fastpath_sign_present_but_invalid_never_serves(monkeypatch):
    """带签名参数但验签不过：哪怕缓存里有描述符也不许出流，
    回全链按原口径被门禁 403（get_play_user 在生产依赖层还会先
    拦一道，这里钉的是 video_stream 内部不凭缓存跳过验签）。"""
    from backend.emby_server import playback_gate as _gate

    user, counts, relay, final_calls, notes = _setup_stream(
        monkeypatch, sign_ok=False, bound=False)
    resp1 = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp1.status_code == 200
    assert _streaming.stream_resolve_cache_get(
        11, "item1", "usercopy_relay") is not None

    def _deny(db, u, req=None):
        raise HTTPException(status_code=403, detail="未绑定")

    monkeypatch.setattr(_gate, "ensure_binding_playback_allowed", _deny)

    signed = _stream_request(
        b"variant=usercopy_relay&uid=11&exp=1999999999&sign=deadbeef")
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream("item1", signed, user, _FakeDB()))
    assert ei.value.status_code == 403
    assert counts["final_serve"] == 0  # 快速通道零出流


def test_fastpath_valid_sign_does_not_require_binding(monkeypatch):
    """已下发签名消费不追溯：验签成立时无绑定也走快速通道。"""
    user, counts, relay, final_calls, notes = _setup_stream(
        monkeypatch, sign_ok=True, bound=False)
    _run(api.video_stream("item1", _stream_request(), user, _FakeDB()))
    signed = _stream_request(
        b"variant=usercopy_relay&uid=11&exp=1999999999&sign=validsign")
    resp = _run(api.video_stream("item1", signed, user, _FakeDB()))
    assert resp.status_code == 200
    assert counts["final_serve"] == 1
    assert counts["token"] == 1  # 仍只取过一次令牌


def test_fastpath_cache_is_per_user(monkeypatch):
    """不同用户同条目不串缓存：B 必须走自己的全链、自己的令牌。"""
    user, counts, relay, final_calls, notes = _setup_stream(monkeypatch)
    _run(api.video_stream("item1", _stream_request(), user, _FakeDB()))
    assert _streaming.stream_resolve_cache_get(
        12, "item1", "usercopy_relay") is None

    user_b = SimpleNamespace(id=12)
    before = dict(counts)
    resp = _run(api.video_stream(
        "item1", _stream_request(), user_b, _FakeDB()))
    assert resp.status_code == 200
    assert counts["resolve"] > before["resolve"]
    assert counts["token"] == before["token"] + 1
    assert relay[-1]["access_token"] == "TOKEN12"
    assert counts["final_serve"] == 0  # B 的首次请求没吃 A 的缓存


def test_fastpath_failure_invalidates_and_falls_back(monkeypatch):
    """快速通道出流失败（最终 URL 已死）→ 失效描述符并回落全链，
    全链重新取令牌/选路后正常出字节。"""
    user, counts, relay, final_calls, notes = _setup_stream(monkeypatch)
    _run(api.video_stream("item1", _stream_request(), user, _FakeDB()))
    assert _streaming.stream_resolve_cache_get(
        11, "item1", "usercopy_relay") is not None

    async def dead_final(url, request, headers=None, media_type="video/mp4",
                         pt=None, sniff_quota_page=False):
        raise HTTPException(status_code=403, detail="签名已失效")

    monkeypatch.setattr(_streaming, "serve_mount_relay_async", dead_final)
    before = dict(counts)
    resp = _run(api.video_stream(
        "item1", _stream_request(), user, _FakeDB()))
    assert resp.status_code == 200
    # 回落全链的证据：选路、令牌、中继都重新发生
    assert counts["resolve"] > before["resolve"]
    assert counts["token"] == before["token"] + 1
    assert counts["relay"] == before["relay"] + 1


# ---------- 描述符服务形态：SA 腿（helper 直测） ----------

def test_fastpath_drive_sa_descriptor_passes_failover_args(monkeypatch):
    """SA 腿描述符：命中后经 serve_drive_relay_async 出流，
    rotate_on_quota / max_attempts 按存入值透传。"""
    from backend.emby_server import playback_gate as _gate

    _streaming.stream_resolve_cache_put(
        11, "itemX", "drive_relay",
        {"kind": "drive_sa", "drive_id": "drive-9", "file_id": "FID9",
         "rotate_on_quota": False, "max_attempts": 5})
    monkeypatch.setattr(_gate, "_request_uses_issued_play_sign",
                        lambda req, db: True)
    # drive_sa 腿在签名模式下也要复核订阅资格（防无资格者吃共享
    # 原件）；本用例是有资格用户，桩成 True。（2026-11-22 门禁
    # 改版：复核口径由绑定关系改为 has_play_grant 订阅资格。）
    monkeypatch.setattr(_gate, "has_play_grant",
                        lambda db, u: True)
    calls = []

    async def fake_relay(request, drive_id, file_id, media_type="video/mp4",
                         pt=None, access_token=None, rotate_on_quota=True,
                         max_attempts=None):
        calls.append({"drive_id": drive_id, "file_id": file_id,
                      "rotate_on_quota": rotate_on_quota,
                      "max_attempts": max_attempts,
                      "access_token": access_token})
        return Response(content=b"s", media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", fake_relay)
    monkeypatch.setattr(api, "_observe_traffic", lambda resp: resp)
    monkeypatch.setattr(api, "_note_watch_activity",
                        lambda req, u, it, code: None)
    monkeypatch.setattr(api, "_note_play_start_safe",
                        lambda item_, **kwargs: None)
    item = _stream_item()
    req = _stream_request(b"variant=drive_relay&uid=11&exp=1&sign=x")
    resp = _run(api._try_fastpath_relay(
        req, _FakeDB(), SimpleNamespace(id=11), item, "itemX",
        "drive_relay", _Pt()))
    assert resp is not None and resp.status_code == 200
    assert calls == [{"drive_id": "drive-9", "file_id": "FID9",
                      "rotate_on_quota": False, "max_attempts": 5,
                      "access_token": None}]


def test_fastpath_helper_failure_invalidates(monkeypatch):
    """helper 直测：出流抛 HTTPException → 返回 None 且缓存已失效。"""
    from backend.emby_server import playback_gate as _gate

    _streaming.stream_resolve_cache_put(
        11, "itemX", "usercopy_relay",
        {"kind": "drive_final", "final_url": FINAL_URL})
    monkeypatch.setattr(_gate, "_request_uses_issued_play_sign",
                        lambda req, db: True)

    async def dead(url, request, headers=None, media_type="video/mp4",
                   pt=None, sniff_quota_page=False):
        raise HTTPException(status_code=410, detail="gone")

    monkeypatch.setattr(_streaming, "serve_mount_relay_async", dead)
    req = _stream_request(b"variant=usercopy_relay&uid=11&exp=1&sign=x")
    resp = _run(api._try_fastpath_relay(
        req, _FakeDB(), SimpleNamespace(id=11), _stream_item(), "itemX",
        "usercopy_relay", _Pt()))
    assert resp is None
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "usercopy_relay") is None


# ---------- TTL 口径（streaming 层单元） ----------

def test_resolve_cache_ttl_capped_by_sign_remaining():
    """TTL 取小者：签名只剩 30 秒 → 描述符 30 秒量级内有效；
    签名已过期/剩余过短（<10s）→ 干脆不存。"""
    _streaming.stream_resolve_cache_put(
        11, "itemX", "usercopy_relay",
        {"kind": "drive_final", "final_url": FINAL_URL},
        sign_cap_seconds=30)
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "usercopy_relay") is not None
    _streaming.stream_resolve_cache_put(
        11, "itemX", "drive_relay",
        {"kind": "drive_sa", "drive_id": "d", "file_id": "f"},
        sign_cap_seconds=-5)
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "drive_relay") is None
    _streaming.stream_resolve_cache_put(
        11, "itemX", "drive_relay",
        {"kind": "drive_sa", "drive_id": "d", "file_id": "f"},
        sign_cap_seconds=3)
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "drive_relay") is None


def test_resolve_cache_disabled_by_env(monkeypatch):
    """STREAM_RESOLVE_CACHE_TTL_SEC=0 → 缓存关闭（写不进、读不到）。"""
    monkeypatch.setenv("STREAM_RESOLVE_CACHE_TTL_SEC", "0")
    _streaming.stream_resolve_cache_put(
        11, "itemX", "usercopy_relay",
        {"kind": "drive_final", "final_url": FINAL_URL})
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "usercopy_relay") is None


def test_resolve_cache_rejects_malformed_payload():
    """缓存里混入非描述符内容（老键/脏数据）→ 按未命中处理，不许抛。"""
    from backend.database import CacheManager

    CacheManager.set(
        _streaming._stream_resolve_cache_key(11, "itemX", "usercopy_relay"),
        "not-a-descriptor", 60, domain="playback")
    assert _streaming.stream_resolve_cache_get(
        11, "itemX", "usercopy_relay") is None
