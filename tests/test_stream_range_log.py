# -*- coding: utf-8 -*-
"""seek 取证轮（2026-11-25）：流请求 Range 日志 + 最近 3 笔环形缓冲。

覆盖：
- 环形缓冲：每 sign 只留最近 3 笔、键数上限、空 sign 不进缓冲。
- [range-log] 日志行格式：range/parsed/suffix/outcome/content-range 齐全，
  sign 绝不许进日志。
- 中继接线：挂载腿首请求透传记 miss、第二轮同 Range 全命中记 hit；
  无签请求只记日志、不进缓冲。
- 读口端点：签名验过给最近记录，签名不对 403。
全部离线：上游是 httpx MockTransport 计数型假源（同 test_mount_hot_cache 口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.emby_server import relay_hot_cache as hc
from backend.emby_server import streaming

SEG = hc.SEGMENT_SIZE
BODY = (bytes(range(256)) * 12000)[: 2 * SEG + SEG // 2]  # 2.5 段
MIDENT = "mount:FIDMOUNT123"
SIGN = "a" * 32
GUID = "c4a84409f1abcbeedf94c416ccc05807"


class _FakeUpstream:
    """计数型假上游：支持 Range，回 ETag/Last-Modified/Content-Range。"""

    def __init__(self, body=BODY):
        self.body = body
        self.etag = '"m1"'
        self.count = 0
        self.ranges = []

    def handler(self, request):
        self.count += 1
        rng = request.headers.get("range")
        self.ranges.append(rng)
        body = self.body
        headers = {"ETag": self.etag, "Content-Type": "video/mp4",
                   "Accept-Ranges": "bytes",
                   "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
        if rng and rng.startswith("bytes="):
            a, _, b = rng[len("bytes="):].partition("-")
            if a:
                start = int(a)
                end = int(b) if b else len(body) - 1
            else:
                start = len(body) - int(b)
                end = len(body) - 1
            end = min(end, len(body) - 1)
            part = body[start:end + 1]
            headers["Content-Range"] = (
                f"bytes {start}-{end}/{len(body)}")
            headers["Content-Length"] = str(len(part))
            return httpx.Response(206, headers=headers, content=part)
        headers["Content-Length"] = str(len(body))
        return httpx.Response(200, headers=headers, content=body)


@pytest.fixture()
def upstream(monkeypatch):
    up = _FakeUpstream()
    client = httpx.AsyncClient(transport=httpx.MockTransport(up.handler))
    monkeypatch.setattr(streaming, "_mount_relay_client", lambda: client)
    return up


@pytest.fixture()
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_HOT_CACHE_DIR", str(tmp_path / "hc"))
    monkeypatch.setenv("RELAY_HOT_CACHE_MIN_FREE_BYTES", "0")
    monkeypatch.setattr(hc, "max_bytes", lambda: 500 * 1024 * 1024)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_recent():
    with streaming._STREAM_RANGE_LOCK:
        streaming._STREAM_RANGE_RECENT.clear()
    yield
    with streaming._STREAM_RANGE_LOCK:
        streaming._STREAM_RANGE_RECENT.clear()


def _request(range_header=None, sign=None):
    headers = []
    if range_header:
        headers = [(b"range", range_header.encode())]
    qs = f"sign={sign}".encode() if sign else b""
    scope = {"type": "http", "method": "GET", "path": "/Videos/item/stream",
             "raw_path": b"/Videos/item/stream", "query_string": qs,
             "headers": headers, "scheme": "http",
             "server": ("testserver", 80), "client": ("testclient", 50000),
             "root_path": ""}
    return Request(scope)


async def _serve_mount(range_header=None, identity=None, sign=None):
    """挂载腿形态出流：不设 _DRIVE_LEG 上下文，身份只经参数传。"""
    assert streaming._DRIVE_LEG.get() is None
    resp = await streaming.serve_mount_relay_async(
        "https://rcd.example/media", _request(range_header, sign),
        {"User-Agent": "t-ua"}, "video/mp4", hot_identity=identity)
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c)
    return resp.status_code, dict(resp.headers), b"".join(chunks)


# ---------------- 环形缓冲 ----------------

def test_recent_keeps_last_three():
    for i in range(4):
        streaming._record_stream_range(
            MIDENT, SIGN, f"bytes={i}-{(i + 1) * 10}", i, (i + 1) * 10,
            "miss", f"bytes {i}-{(i + 1) * 10}/1000")
    recs = streaming.recent_stream_ranges(SIGN)
    assert len(recs) == 3
    assert [r["start"] for r in recs] == [1, 2, 3]
    assert recs[0]["outcome"] == "miss"


def test_recent_empty_sign_not_buffered():
    streaming._record_stream_range(MIDENT, "", "bytes=0-99", 0, 99,
                                   "miss", "bytes 0-99/1000")
    streaming._record_stream_range(MIDENT, None, "bytes=0-99", 0, 99,
                                   "miss", "bytes 0-99/1000")
    assert streaming.recent_stream_ranges("") == []
    assert streaming.recent_stream_ranges(SIGN) == []
    assert streaming._STREAM_RANGE_RECENT == {}


def test_recent_caps_keys():
    for i in range(600):
        streaming._record_stream_range(
            MIDENT, f"{i:032x}", "bytes=0-99", 0, 99,
            "miss", "bytes 0-99/1000")
    assert len(streaming._STREAM_RANGE_RECENT) <= 512
    # 最新的 key 必须还在（淘汰只丢最旧的）
    assert streaming.recent_stream_ranges(f"{599:032x}")


def test_range_log_line_format(caplog):
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.streaming"):
        streaming._record_stream_range(
            MIDENT, SIGN, "bytes=-1219380021", 1141964800, 2361344820,
            "miss", "bytes 1141964800-2361344820/2361344821")
    lines = [r.message for r in caplog.records if "[range-log]" in r.message]
    assert len(lines) == 1
    text = lines[0]
    assert "identity=mount:FIDMOUNT123" in text
    assert "range=bytes=-1219380021" in text
    assert "parsed=1141964800-2361344820" in text
    assert "suffix=true" in text
    assert "outcome=miss" in text
    assert "content-range=bytes 1141964800-2361344820/2361344821" in text
    assert SIGN not in text, "sign 是短期播放凭证，绝不许进日志"


# ---------------- 中继接线 ----------------

def test_mount_relay_records_miss_then_hit(cache_env, upstream, monkeypatch):
    """首请求整文件透传记 miss；第二轮同 Range 全命中记 hit。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    status, _h, body = asyncio.run(
        _serve_mount(None, identity=MIDENT, sign=SIGN))
    assert status == 200
    assert body == BODY
    hc.flush_writes()
    recs = streaming.recent_stream_ranges(SIGN)
    assert len(recs) == 1
    assert recs[0]["outcome"] == "miss"
    assert recs[0]["range"] is None
    assert (recs[0]["start"], recs[0]["end"]) == (0, len(BODY) - 1)
    assert recs[0]["content_range"] is None
    assert recs[0]["suffix"] is False

    status, headers, body = asyncio.run(
        _serve_mount("bytes=100-199", identity=MIDENT, sign=SIGN))
    assert status == 206
    assert body == BODY[100:200]
    assert headers.get("content-range") == f"bytes 100-199/{len(BODY)}"
    assert upstream.count == 1, "第二轮全命中不许再回源"
    recs = streaming.recent_stream_ranges(SIGN)
    assert [r["outcome"] for r in recs] == ["miss", "hit"]
    assert recs[1]["range"] == "bytes=100-199"
    assert (recs[1]["start"], recs[1]["end"]) == (100, 199)
    assert recs[1]["content_range"] == f"bytes 100-199/{len(BODY)}"
    assert recs[1]["suffix"] is False


def test_unsigned_request_logs_but_not_buffered(cache_env, upstream,
                                               monkeypatch, caplog):
    """无签请求（第三方 api_key 直调形态）：记日志、不进缓冲。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.streaming"):
        status, _h, body = asyncio.run(
            _serve_mount("bytes=0-99", identity=MIDENT))
    assert status == 206
    assert body == BODY[0:100]
    assert streaming._STREAM_RANGE_RECENT == {}
    assert any("[range-log]" in r.message for r in caplog.records)


# ---------------- 读口端点 ----------------

def test_stream_ranges_endpoint_ok_and_forbidden():
    from backend.api.user import stream_ranges
    from backend.emby_server import play_sign as ps

    uid = 7
    exp, sign = ps.issue_play_sign(uid, GUID, 3600)
    streaming._record_stream_range(
        MIDENT, sign, "bytes=0-99", 0, 99, "miss", "bytes 0-99/1000")
    user = SimpleNamespace(id=uid)
    out = stream_ranges(item_id=GUID, exp=exp, sign=sign, current_user=user)
    assert out["item_id"] == GUID
    assert len(out["ranges"]) == 1
    assert out["ranges"][0]["outcome"] == "miss"
    with pytest.raises(HTTPException) as ei:
        stream_ranges(item_id=GUID, exp=exp, sign="0" * 32,
                      current_user=user)
    assert ei.value.status_code == 403
    # 别人的签名在本账号下同样 403
    exp2, sign2 = ps.issue_play_sign(999, GUID, 3600)
    with pytest.raises(HTTPException) as ei:
        stream_ranges(item_id=GUID, exp=exp2, sign=sign2, current_user=user)
    assert ei.value.status_code == 403
