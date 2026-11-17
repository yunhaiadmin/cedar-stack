# -*- coding: utf-8 -*-
"""中继服务端预读（2026-11-17 网页播放中继取流优化）回归测试。

口径：Drive 中继腿（serve_drive_relay_async 经 _DRIVE_LEG 上下文
进 serve_mount_relay_async 的那条路）出流时加有界预读——生产者
先把后续块读进小队列，消费端（写客户端）的间隙上游不空转，抹平
上游抖动。挂载腿（无 Drive 上下文）逐字维持原直通迭代。

本文件钉住：
- _prefetch_stream_chunks：消费端提前结束时生产者停读，源产量
  有界（已消费 + 队列深度 + 在途一块），且不再增长；
- _prefetch_stream_chunks：源中途抛错时，已排队块先吐完、异常
  在原位置重抛（中继层既有 TransportError 收口口径不变）；
- serve_mount_relay_async 带 Drive 上下文时：Range 206 响应头
  与字节逐字正确（预读不改 Range 语义），且最终 URL capture
  回填成立（快速通道落缓存靠它）；
- 预读深度 env：STREAM_RELAY_PREFETCH_CHUNKS=0 关闭、非法回默认。

全部离线：上游是 127.0.0.1 本地测试服务器（仿
tests/test_mount_relay.py）。
"""
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from starlette.requests import Request

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import streaming as _streaming

BODY = b"0123456789" * 1000  # 10000 字节


# ---------------- _prefetch_stream_chunks 单元 ----------------

def test_prefetch_producer_stops_when_consumer_leaves():
    """消费端读 2 块就关：源产量 ≤ 已消费 + 队列深度 + 在途 1 块，
    且关后不再增长（预读真停了，不是后台偷偷读完）。"""
    async def scenario():
        produced = []

        async def source():
            for i in range(50):
                produced.append(i)
                yield f"chunk{i}".encode()
                await asyncio.sleep(0.005)

        stream = _streaming._prefetch_stream_chunks(source(), 3)
        got = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        n_at_close = len(produced)
        await asyncio.sleep(0.15)
        return got, n_at_close, len(produced)

    got, n_at_close, n_later = asyncio.run(scenario())
    assert got == [b"chunk0", b"chunk1"]
    assert n_at_close <= 2 + 3 + 1
    assert n_later == n_at_close  # 关后产量冻结


def test_prefetch_error_reraised_in_position():
    """源第 3 块抛错：消费端先拿到前 2 块，异常在原位置重抛。"""
    async def scenario():
        async def source():
            yield b"a"
            yield b"b"
            raise httpx.TransportError("upstream died")

        stream = _streaming._prefetch_stream_chunks(source(), 2)
        got = [await stream.__anext__(), await stream.__anext__()]
        with pytest.raises(httpx.TransportError):
            await stream.__anext__()
        return got

    assert asyncio.run(scenario()) == [b"a", b"b"]


def test_prefetch_full_drain_order_preserved():
    """读完为止：顺序与内容逐字不变、无丢失无重复。"""
    async def scenario():
        async def source():
            for i in range(10):
                yield f"c{i}".encode()

        out = []
        async for chunk in _streaming._prefetch_stream_chunks(source(), 3):
            out.append(chunk)
        return out

    assert asyncio.run(scenario()) == [f"c{i}".encode() for i in range(10)]


def test_prefetch_chunks_env(monkeypatch):
    """深度 env 口径：默认 3、0 = 关、越界钳制、非法回默认。"""
    monkeypatch.delenv("STREAM_RELAY_PREFETCH_CHUNKS", raising=False)
    assert _streaming._relay_prefetch_chunks() == 3
    monkeypatch.setenv("STREAM_RELAY_PREFETCH_CHUNKS", "0")
    assert _streaming._relay_prefetch_chunks() == 0
    monkeypatch.setenv("STREAM_RELAY_PREFETCH_CHUNKS", "99")
    assert _streaming._relay_prefetch_chunks() == 8
    monkeypatch.setenv("STREAM_RELAY_PREFETCH_CHUNKS", "abc")
    assert _streaming._relay_prefetch_chunks() == 3


# ---------------- serve 层：Drive 腿 206 正确性 + capture ----------------

class _RangeHandler(BaseHTTPRequestHandler):
    received = []

    def do_GET(self):
        type(self).received.append(
            (self.path, {k.lower(): v for k, v in self.headers.items()}))
        range_h = self.headers.get("Range")
        if range_h and range_h.startswith("bytes="):
            spec = range_h[len("bytes="):].split("-")
            start = int(spec[0]) if spec[0] else 0
            end = int(spec[1]) if len(spec) > 1 and spec[1] else len(BODY) - 1
            end = min(end, len(BODY) - 1)
            part = BODY[start:end + 1]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{end}/{len(BODY)}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(part)))
            self.end_headers()
            self.wfile.write(part)
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


@pytest.fixture()
def upstream():
    _RangeHandler.received = []
    srv = HTTPServer(("127.0.0.1", 0), _RangeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def _request(range_header=None):
    headers = []
    if range_header:
        headers = [(b"range", range_header.encode())]
    scope = {"type": "http", "method": "GET", "path": "/Videos/item/stream",
             "raw_path": b"/Videos/item/stream", "query_string": b"",
             "headers": headers, "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


async def _drain(resp):
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c)
    return b"".join(chunks)


def test_drive_leg_relay_206_with_prefetch_and_capture(upstream):
    """Drive 腿（预读开）Range 出流：206/Content-Range/字节逐字
    正确；capture 回填最终 URL（无跳转时即源 URL 本身）。"""
    async def scenario():
        capture = {}
        leg = _streaming._DriveLegScope(cache_key=None, capture=capture)
        token = _streaming._DRIVE_LEG.set(leg)
        try:
            resp = await _streaming.serve_mount_relay_async(
                upstream + "/movie.mkv", _request("bytes=100-199"),
                {"Authorization": "Bearer T"}, "video/mp4")
            body = await _drain(resp)
            return resp, body, capture
        finally:
            _streaming._DRIVE_LEG.reset(token)

    resp, body, capture = asyncio.run(scenario())
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 100-199/{len(BODY)}"
    assert body == BODY[100:200]
    assert capture.get("final_url") == upstream + "/movie.mkv"


def test_drive_leg_relay_full_body_with_prefetch(upstream):
    """无 Range 整段 200：预读下字节完整无缺。"""
    async def scenario():
        leg = _streaming._DriveLegScope(cache_key=None, capture=None)
        token = _streaming._DRIVE_LEG.set(leg)
        try:
            resp = await _streaming.serve_mount_relay_async(
                upstream + "/movie.mkv", _request(),
                {"Authorization": "Bearer T"}, "video/mp4")
            return resp, await _drain(resp)
        finally:
            _streaming._DRIVE_LEG.reset(token)

    resp, body = asyncio.run(scenario())
    assert resp.status_code == 200
    assert body == BODY
