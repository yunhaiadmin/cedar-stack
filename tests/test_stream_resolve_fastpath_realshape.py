# -*- coding: utf-8 -*-
"""续段快速通道「真实请求形状」回归（#256 v2，2026-11-17）。

背景：#256 上线后 parent 依 play_start 事件 resolve_ms（596–863ms）
判「快速通道根本没命中」，但 aetrix-ea 的 [play-timing] 日志实证
续段请求是 ``auth_visible=6ms relay=7ms ttfb=727ms``——全链四个
auth 标记整段消失、relay 标记 7ms 出现，命中确凿；resolve_ms
口径从 serve 调用前计到 serve 返回（拿到上游首字节），含 Google
TTFB 0.6–0.8s，本就降不到 100ms。误判根源有二：#256 没给快速
通道留任何命中/未命中日志，无从观测；且 #256 的测试夹具与
AVPlayer 真实请求形状不一致（只有 variant 一个参数、无签名、
无 path_params、签名校验被桩掉），真实签名路径从未被测过。

本文件钉住（夹具逐字仿生产访问日志，aetrix-ea 2026-11-17
02:27:28 UTC）：
``GET /emby/Videos/{guid}/stream.mkv?static=true&
MediaSourceId={guid}:usercopy_relay&api_key=…&uid=1&exp=…&
sign=…&variant=usercopy_relay``，签名由 play_sign.issue_play_sign
真签发、门禁 _request_uses_issued_play_sign 真校验（不桩）：

- 首请求 miss（reason=no_descriptor 有日志）→ 全链落存
  （store 有日志、kind=drive_token：令牌腿 capture 回填的是
  alt=media API 地址本身、与拼出的 API 地址相同，不成
  drive_final）→ 续段命中（hit 有日志），令牌/选路计数不增，
  续段用的是存入的那枚令牌（令牌工厂产出带序号，串味可辨）；
- 首请求响应体未被消费（AVPlayer 拿了响应就移走/中途断开）
  时描述符已在缓存里，次请求照常命中；
- 首请求 serve 阶段就失败（上游异常）时，令牌描述符已在
  解析成功时先行落存（不依赖 serve 完成），次请求仍可命中；
- 签名被篡改 → 真校验不过 → miss（reason=sign_invalid 有
  日志）、快速通道零出流，回全链按门禁 403；
- 跨用户：B 用自己的真签名同条目，各走各的链、各自的令牌，
  不吃 A 的描述符。

除签名/门禁判定外其余全链桩口径仿
tests/test_stream_resolve_fastpath.py。
"""
import asyncio
import logging
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import api, play_sign, play_variants
from backend.emby_server import drive_oauth as do
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import streaming as _streaming
from backend.emby_server import transfer_variant as _tv
from backend.emby_server.drive_auth import (
    drive_api_media_url, drive_usercontent_download_url)

GUID = "0123456789abcdef0123456789abcdef"
ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"
# 令牌腿在 serve_mount_relay_async 里跟随的终点就是 alt=media
# API 地址本身（带 Authorization 头直取、不跳转），capture 回
# 填的 final_url 与 _store_stream_resolve 拼出的 api_url 相同
# → 描述符落 drive_token 而非 drive_final（生产实况）。
API_URL = drive_api_media_url(drive_usercontent_download_url(COPY_FID))

API_LOGGER = "backend.emby_server.api"


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
        id=598385, guid=GUID, name="爱上平行时空的你",
        drive_file_id=ORIG_FID, container="mkv",
        file_path="mount://3/MoviePilot/电影/测试片.mkv")


def _real_request(user_id, *, sign=None, exp=None):
    """逐字仿生产 AVPlayer 请求：.mkv 后缀路径、guid 在路径与
    MediaSourceId 双处、static/api_key/uid/exp/sign/variant 全参，
    path_params 由路由注入（scope 手工带上，与生产一致）。"""
    if sign is None or exp is None:
        exp, sign = play_sign.issue_play_sign(user_id, GUID, 3600)
    qs = (f"static=true&MediaSourceId={GUID}:usercopy_relay"
          "&api_key=" + "TESTKEY" + f"&uid={user_id}"
          f"&exp={exp}&sign={sign}&variant=usercopy_relay")
    scope = {"type": "http", "method": "GET",
             "path": f"/emby/Videos/{GUID}/stream.mkv",
             "raw_path": f"/emby/Videos/{GUID}/stream.mkv".encode(),
             "query_string": qs.encode(), "headers": [],
             "scheme": "http", "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": "",
             "path_params": {"item_id": GUID}}
    return Request(scope)


@pytest.fixture(autouse=True)
def _clean_resolve_cache():
    keys = [(11, GUID, "usercopy_relay"), (12, GUID, "usercopy_relay")]
    for k in keys:
        _streaming.stream_resolve_cache_invalidate(*k)
    yield
    for k in keys:
        _streaming.stream_resolve_cache_invalidate(*k)


def _setup_real(monkeypatch):
    """全链桩（仿 #256 harness）＋真签名判定：不桩
    _request_uses_issued_play_sign / verify_play_sign；store 热读
    桩只给策略键，防盗链键回空串 → hotlink_enabled 回落默认开。"""
    from backend.emby_server import delivery_plan as _dp
    from backend.emby_server import drive_changes as _dc
    from backend.emby_server import playback_gate as _gate
    from backend.emby_server import playback_source_health as _psh
    from backend.emby_server import pool_gate as _pool_gate

    item = _stream_item()
    copy_row = _copy_row()
    counts = {"token": 0, "resolve": 0, "relay": 0}
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
    monkeypatch.setattr(_gate, "has_active_binding",
                        lambda db, uid: True)
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

    async def fake_relay(request, drive_id, file_id,
                         media_type="video/mp4", pt=None,
                         access_token=None, **kwargs):
        counts["relay"] += 1
        relay_calls.append({"drive_id": drive_id, "file_id": file_id,
                            "access_token": access_token})
        # 仿真实令牌腿：capture 回填 alt=media API 地址本身
        box = _streaming._RELAY_FINAL_CAPTURE.get()
        if box is not None:
            box["final_url"] = API_URL
        return Response(content=b"x", media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", fake_relay)

    def fake_sa(drive_id, force_refresh=False):
        raise AssertionError("用户令牌中继不许咨询共享 SA 池")

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_sa)

    def fake_token(db, user_id):
        counts["token"] += 1
        # 带序号：现取的第二枚与存入的第一枚可区分，防「看着
        # 像命中、其实重取了同值令牌」的假绿。
        return f"TOK{user_id}"

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
    return counts, relay_calls, notes


def _fp_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if "[stream-fastpath]" in r.getMessage()]


# ---------- 真实形状：miss → 落存 → 命中，全程有日志 ----------

def test_realshape_miss_store_hit_with_logs(monkeypatch, caplog):
    counts, relay_calls, notes = _setup_real(monkeypatch)
    user = SimpleNamespace(id=11)
    exp, sign = play_sign.issue_play_sign(11, GUID, 3600)

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        resp1 = _run(api.video_stream(
            GUID, _real_request(11, sign=sign, exp=exp),
            user, _FakeDB()))
    assert resp1.status_code == 200
    first_counts = dict(counts)
    assert first_counts["token"] >= 1 and first_counts["relay"] == 1
    desc = _streaming.stream_resolve_cache_get(
        11, GUID, "usercopy_relay")
    assert desc is not None and desc["kind"] == "drive_token"
    assert desc["access_token"] == "TOK11"
    lines = _fp_lines(caplog)
    assert any("miss" in m and "reason=no_descriptor" in m
               for m in lines), lines
    assert any("store" in m and "kind=drive_token" in m
               for m in lines), lines

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        resp2 = _run(api.video_stream(
            GUID, _real_request(11, sign=sign, exp=exp),
            user, _FakeDB()))
    assert resp2.status_code == 200
    # 命中：令牌不再现取、选路不再重跑；中继腿用存入的 TOK11
    assert counts["token"] == first_counts["token"]
    assert counts["resolve"] == first_counts["resolve"]
    assert counts["relay"] == first_counts["relay"] + 1
    assert relay_calls[-1]["access_token"] == "TOK11"
    lines = _fp_lines(caplog)
    assert any("hit" in m and "kind=drive_token" in m
               for m in lines), lines
    assert len(notes) == 2  # 打点口径不变：每请求一条


# ---------- 首请求中途断开：描述符不依赖 serve 完成 ----------

def test_realshape_disconnect_before_body_still_hits(monkeypatch):
    """AVPlayer 拿到响应头就移走（正文没消费完）：落存发生在
    handler 内、响应返回之前，次请求照常命中。"""
    counts, relay_calls, notes = _setup_real(monkeypatch)

    async def streaming_relay(request, drive_id, file_id,
                              media_type="video/mp4", pt=None,
                              access_token=None, **kwargs):
        counts["relay"] += 1
        relay_calls.append({"access_token": access_token})
        box = _streaming._RELAY_FINAL_CAPTURE.get()
        if box is not None:
            box["final_url"] = API_URL

        async def _gen():
            yield b"chunk-1"
            await asyncio.sleep(0)
            yield b"chunk-2"

        return StreamingResponse(_gen(), media_type="video/mp4")

    monkeypatch.setattr(api, "serve_drive_relay_async", streaming_relay)
    user = SimpleNamespace(id=11)
    resp1 = _run(api.video_stream(
        GUID, _real_request(11), user, _FakeDB()))
    # 正文一个字节都还没消费，描述符必须已经在缓存里
    assert _streaming.stream_resolve_cache_get(
        11, GUID, "usercopy_relay") is not None

    async def _abandon(resp):
        it = resp.body_iterator
        first = await it.__anext__()
        await it.aclose()
        return first

    assert _run(_abandon(resp1)) == b"chunk-1"

    resp2 = _run(api.video_stream(
        GUID, _real_request(11), user, _FakeDB()))
    assert resp2.status_code == 200
    assert counts["token"] == 1  # 未重取令牌＝命中


def test_realshape_store_survives_serve_failure(monkeypatch):
    """首请求 serve 阶段上游异常：描述符在解析成功（令牌到手）
    时已先行落存、不等 serve 结果；上游恢复后次请求直接命中，
    不必再付一次全链选路＋令牌现取。"""
    counts, relay_calls, notes = _setup_real(monkeypatch)
    state = {"fail": True}
    good_relay = api.serve_drive_relay_async

    async def flaky_relay(request, drive_id, file_id,
                          media_type="video/mp4", pt=None,
                          access_token=None, **kwargs):
        if state["fail"]:
            counts["relay"] += 1
            raise HTTPException(status_code=503, detail="上游抖动")
        return await good_relay(
            request, drive_id, file_id, media_type=media_type,
            pt=pt, access_token=access_token)

    monkeypatch.setattr(api, "serve_drive_relay_async", flaky_relay)
    user = SimpleNamespace(id=11)
    with pytest.raises(HTTPException) as ei:
        _run(api.video_stream(
            GUID, _real_request(11), user, _FakeDB()))
    assert ei.value.status_code == 503
    desc = _streaming.stream_resolve_cache_get(
        11, GUID, "usercopy_relay")
    assert desc is not None and desc["kind"] == "drive_token"
    assert desc["access_token"] == "TOK11"

    state["fail"] = False
    mid_counts = dict(counts)
    resp2 = _run(api.video_stream(
        GUID, _real_request(11), user, _FakeDB()))
    assert resp2.status_code == 200
    assert counts["token"] == mid_counts["token"]
    assert counts["resolve"] == mid_counts["resolve"]


# ---------- 篡改签名：真校验拦截＋miss 日志 ----------

def test_realshape_tampered_sign_miss_logged_never_serves(
        monkeypatch, caplog):
    from backend.emby_server import playback_gate as _gate
    from backend.emby_server import pool_gate as _pool_gate

    counts, relay_calls, notes = _setup_real(monkeypatch)
    user = SimpleNamespace(id=11)
    exp, sign = play_sign.issue_play_sign(11, GUID, 3600)
    resp1 = _run(api.video_stream(
        GUID, _real_request(11, sign=sign, exp=exp), user, _FakeDB()))
    assert resp1.status_code == 200
    before = dict(counts)

    def _deny(db, u, req=None):
        raise HTTPException(status_code=403, detail="未绑定")

    def _deny_pool(db, u, it, req=None):
        raise HTTPException(status_code=403, detail="未绑定")

    monkeypatch.setattr(_gate, "ensure_binding_playback_allowed", _deny)
    monkeypatch.setattr(
        _pool_gate, "ensure_playback_allowed_for_item", _deny_pool)

    # 32 位 ASCII、格式合法但值不对的签名（真 verify 必拒）
    bad = _real_request(11, sign="0" * 32, exp=exp)
    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        with pytest.raises(HTTPException) as ei:
            _run(api.video_stream(GUID, bad, user, _FakeDB()))
    assert ei.value.status_code == 403
    # 快速通道零出流、全链也在门禁处停住：计数零增量
    assert counts == before
    lines = _fp_lines(caplog)
    assert any("miss" in m and "reason=sign_invalid" in m
               for m in lines), lines


# ---------- 跨用户：各自真签名、各自令牌 ----------

def test_realshape_cross_user_own_chain(monkeypatch):
    counts, relay_calls, notes = _setup_real(monkeypatch)
    user_a = SimpleNamespace(id=11)
    _run(api.video_stream(
        GUID, _real_request(11), user_a, _FakeDB()))
    assert _streaming.stream_resolve_cache_get(
        12, GUID, "usercopy_relay") is None

    user_b = SimpleNamespace(id=12)
    before = dict(counts)
    resp = _run(api.video_stream(
        GUID, _real_request(12), user_b, _FakeDB()))
    assert resp.status_code == 200
    assert counts["resolve"] > before["resolve"]
    assert counts["token"] > before["token"]
    assert relay_calls[-1]["access_token"] == "TOK12"
    # A 的描述符原样未动，B 落的是自己的令牌
    assert _streaming.stream_resolve_cache_get(
        11, GUID, "usercopy_relay")["access_token"] == "TOK11"
    assert _streaming.stream_resolve_cache_get(
        12, GUID, "usercopy_relay")["access_token"] == "TOK12"
