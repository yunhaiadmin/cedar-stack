# -*- coding: utf-8 -*-
"""[play-timing] stream 分段打点（L-007 第一步：拆开冷起播 TTFB）。

背景：冷门片起播时 [play-timing] stream 行里 ttfb 高达 1.7–2.7s，
但既有打点只有 mount_relay→ttfb 一个粗段，中继内部（热缓存查询 /
向 rcd 发请求等响应头 / 响应头到首块）全是黑盒。本组测试钉住新
分段字段：

- PlayTimer.span：时长型字段的输出格式与量级；_NoopTimer.span 不抛。
- serve_mount_relay_async 透传路径：汇总行含 cache_lookup /
  rcd_ttfb / rcd_first_chunk / first_byte 新字段，旧字段 ttfb /
  total 不丢；慢响应头计入 rcd_ttfb、慢首块计入 rcd_first_chunk；
  字节逐字不变、状态/头透传不变。
- 空 body（首字节前流结束）：汇总行仍恰好输出一行。
- 热缓存全命中路径：汇总行含 cache_lookup 与 ttfb。

全部离线：上游是 127.0.0.1 本地测试服务器 / httpx MockTransport。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from starlette.requests import Request

from backend.emby_server import relay_hot_cache as hc
from backend.emby_server import streaming
from backend.emby_server.play_timing import PlayTimer, _NoopTimer

BODY = b"0123456789" * 100  # 1000 字节
HEADERS_DELAY = 0.4   # 上游收到请求后、发响应头前的停顿（计入 rcd_ttfb）
FIRST_CHUNK_DELAY = 0.3  # 上游发完响应头后、发首块前的停顿（计入 rcd_first_chunk）


def _field(msg, name):
    m = re.search(rf"(?:^| ){re.escape(name)}=(\d+)ms", msg)
    return int(m.group(1)) if m else None


def _timing_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("[play-timing]")]


# ---------------- PlayTimer.span ----------------

def test_span_records_duration_field(caplog):
    t = PlayTimer("stream item=span")
    t0 = time.perf_counter()
    time.sleep(0.05)
    t.span("rcd_ttfb", t0)
    t.mark("ttfb")
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.play_timing"):
        t.done()
    lines = _timing_lines(caplog)
    assert len(lines) == 1
    v = _field(lines[0], "rcd_ttfb")
    assert v is not None and v >= 40, lines[0]  # 50ms 睡眠的时长字段
    assert _field(lines[0], "ttfb") is not None
    assert _field(lines[0], "total") is not None


def test_noop_timer_span_is_free():
    t = _NoopTimer()
    t.span("rcd_ttfb", time.perf_counter())  # 不抛、不输出
    t.mark("x")
    t.done()


# ---------------- 透传路径（慢上游拆段） ----------------

class _SlowHandler(BaseHTTPRequestHandler):
    """先睡 HEADERS_DELAY 再发头、再睡 FIRST_CHUNK_DELAY 再发 body。"""

    mode = "slow"  # slow | empty

    def do_GET(self):
        if type(self).mode == "empty":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        time.sleep(HEADERS_DELAY)
        range_h = self.headers.get("Range")
        if range_h and range_h.startswith("bytes="):
            spec = range_h[len("bytes="):].split("-")
            start = int(spec[0]) if spec[0] else 0
            end = int(spec[1]) if len(spec) > 1 and spec[1] else len(BODY) - 1
            end = min(end, len(BODY) - 1)
            part = BODY[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{len(BODY)}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(part)))
            self.end_headers()
            time.sleep(FIRST_CHUNK_DELAY)
            self.wfile.write(part)
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        time.sleep(FIRST_CHUNK_DELAY)
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


@pytest.fixture()
def slow_upstream():
    _SlowHandler.mode = "slow"
    srv = HTTPServer(("127.0.0.1", 0), _SlowHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_HOT_CACHE_DIR", str(tmp_path / "hc"))
    monkeypatch.setenv("RELAY_HOT_CACHE_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_SEGMENTS", "0")
    monkeypatch.setattr(hc, "max_bytes", lambda: 500 * 1024 * 1024)
    return tmp_path


def _request(range_header=None):
    headers = []
    if range_header:
        headers = [(b"range", range_header.encode())]
    scope = {"type": "http", "method": "GET", "path": "/Videos/item/stream",
             "raw_path": b"/Videos/item/stream", "query_string": b"",
             "headers": headers, "scheme": "http",
             "server": ("testserver", 80), "client": ("testclient", 50000),
             "root_path": ""}
    return Request(scope)


async def _drain(resp):
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c)
    return b"".join(chunks)


async def _serve_and_drain(url, range_header, pt=None, hot_identity=None):
    """serve + 读完流必须在同一次 asyncio.run 里（共享客户端按 loop 绑）。"""
    resp = await streaming.serve_mount_relay_async(
        url, _request(range_header), {"User-Agent": "t-ua"}, "video/mp4",
        pt=pt, hot_identity=hot_identity)
    return resp, await _drain(resp)


def test_relay_stream_timing_breakdown(slow_upstream, cache_env, caplog):
    """冷透传：分段字段齐全且各就其位，字节与状态逐字不变。"""
    pt = PlayTimer("stream item=ttfb-breakdown")
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.play_timing"):
        resp, body = asyncio.run(_serve_and_drain(
            slow_upstream + "/movie.mkv", "bytes=0-99", pt=pt,
            hot_identity="mount:ttfb-breakdown-fid"))
    assert resp.status_code == 206
    assert body == BODY[0:100]
    lines = _timing_lines(caplog)
    assert len(lines) == 1, lines
    msg = lines[0]
    # 新字段齐全
    cache_lookup = _field(msg, "cache_lookup")
    rcd_ttfb = _field(msg, "rcd_ttfb")
    rcd_first_chunk = _field(msg, "rcd_first_chunk")
    first_byte = _field(msg, "first_byte")
    # 旧字段不丢
    ttfb = _field(msg, "ttfb")
    total = _field(msg, "total")
    assert cache_lookup is not None and cache_lookup >= 0, msg
    assert rcd_ttfb is not None and rcd_ttfb >= 250, msg  # 0.4s 响应头停顿
    assert rcd_first_chunk is not None and rcd_first_chunk >= 180, msg  # 0.3s 首块停顿
    assert ttfb is not None and first_byte is not None and total is not None, msg
    # 累计点单调：ttfb（响应头）<= first_byte（首字节）<= total
    assert ttfb <= first_byte <= total, msg


def test_relay_stream_timing_line_emitted_on_empty_body(slow_upstream, caplog):
    """上游 200 空 body：首字节前流结束，汇总行仍恰好输出一行。"""
    _SlowHandler.mode = "empty"
    pt = PlayTimer("stream item=ttfb-empty")
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.play_timing"):
        resp, body = asyncio.run(_serve_and_drain(
            slow_upstream + "/movie.mkv", None, pt=pt))
    assert resp.status_code == 200
    assert body == b""
    lines = _timing_lines(caplog)
    assert len(lines) == 1, lines
    assert _field(lines[0], "ttfb") is not None
    assert _field(lines[0], "total") is not None
    assert _field(lines[0], "first_byte") is None  # 没有首字节就不许假装有


# ---------------- 热缓存全命中路径 ----------------

HIT_BODY = b"hitdata!" * 1250  # 10000 字节
IDENT = "mount:ttfb-hit-fid"


def _hit_handler(request):
    body = HIT_BODY
    range_h = request.headers.get("range")
    headers = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes"}
    if range_h and range_h.startswith("bytes="):
        spec = range_h[len("bytes="):].split("-", 1)
        start = int(spec[0]) if spec[0] else 0
        end = int(spec[1]) if len(spec) > 1 and spec[1] else len(body) - 1
        end = min(end, len(body) - 1)
        part = body[start:end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
        headers["Content-Length"] = str(len(part))
        return httpx.Response(206, headers=headers, content=part)
    headers["Content-Length"] = str(len(body))
    return httpx.Response(200, headers=headers, content=body)


@pytest.fixture()
def mock_upstream(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(_hit_handler))
    monkeypatch.setattr(streaming, "_mount_relay_client", lambda: client)
    return client


def test_hot_cache_hit_timing_has_cache_lookup(cache_env, mock_upstream, caplog):
    """写穿后全命中：汇总行含 cache_lookup 与 ttfb（命中路径同名字段）。"""
    async def _serve(range_header, pt=None):
        resp = await streaming.serve_mount_relay_async(
            "https://mount.example/media", _request(range_header),
            {"User-Agent": "t-ua"}, "video/mp4", pt=pt, hot_identity=IDENT)
        return resp, await _drain(resp)

    # 首请求透传写穿
    resp, body = asyncio.run(_serve(None))
    assert resp.status_code == 200 and body == HIT_BODY
    hc.flush_writes()

    pt = PlayTimer("stream item=ttfb-hit")
    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.play_timing"):
        resp, body = asyncio.run(_serve("bytes=100-199", pt=pt))
    assert resp.status_code == 206
    assert body == HIT_BODY[100:200]
    lines = _timing_lines(caplog)
    assert len(lines) == 1, lines
    assert _field(lines[0], "cache_lookup") is not None, lines[0]
    assert _field(lines[0], "ttfb") is not None, lines[0]
    assert _field(lines[0], "total") is not None, lines[0]
