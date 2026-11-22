# -*- coding: utf-8 -*-
"""中继热缓存（2026-11-22 智能中继＋热缓存阶段 1）。

覆盖：
- 写穿→全命中：Drive 腿首请求透传上游并把完整分段落盘；同文件
  后续区间请求被缓存分段完全覆盖时本地出字节、**不再回源**
  （计数型假上游断言请求数不变）。
- 部分写穿：区间只覆盖到的完整分段才落盘，段内小区间不落盘。
- 无身份（挂载腿/无腿）不介入：每次都回源、缓存目录无分段。
- 校验器变化（ETag 变）→ 整身份失效，旧分段不再命中。
- LRU 淘汰：超容量按 mtime 从旧到新淘汰到上限以下。
- 跨请求预读：只取请求区间之后 N 段、已缓存/在途去重。
- 缓存层故障 fail-open：元数据/登记抛错时回现行透传、字节无损。
- parse_range 与连接池 env 上限解析的纯函数口径。

全部离线：上游是 httpx MockTransport 计数型假源，不碰外网与 DB
（max_bytes 以 monkeypatch 替身，绕开 SystemConfig 读库）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio

import httpx
import pytest
from starlette.requests import Request

from backend.emby_server import relay_hot_cache as hc
from backend.emby_server import streaming

SEG = hc.SEGMENT_SIZE
BODY = (bytes(range(256)) * 12000)[: 2 * SEG + SEG // 2]  # 2.5 段
IDENT = "drive:D9:F1"


class _FakeUpstream:
    """计数型假上游：支持 Range，回 ETag/Content-Range。"""

    def __init__(self, body=BODY, etag='"v1"'):
        self.body = body
        self.etag = etag
        self.count = 0
        self.ranges = []

    def handler(self, request):
        self.count += 1
        rng = request.headers.get("range")
        self.ranges.append(rng)
        body = self.body
        headers = {"ETag": self.etag, "Content-Type": "video/mp4",
                   "Accept-Ranges": "bytes"}
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
            headers["Content-Range"] = \
                f"bytes {start}-{end}/{len(body)}"
            headers["Content-Length"] = str(len(part))
            return httpx.Response(206, headers=headers, content=part)
        headers["Content-Length"] = str(len(body))
        return httpx.Response(200, headers=headers, content=body)


@pytest.fixture()
def upstream(monkeypatch):
    up = _FakeUpstream()
    client = httpx.AsyncClient(transport=httpx.MockTransport(up.handler))
    monkeypatch.setattr(streaming, "_drive_relay_client", lambda: client)
    monkeypatch.setattr(streaming, "_mount_relay_client", lambda: client)
    return up


@pytest.fixture()
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_HOT_CACHE_DIR", str(tmp_path / "hc"))
    monkeypatch.setenv("RELAY_HOT_CACHE_MIN_FREE_BYTES", "0")
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


async def _serve(range_header=None, identity=IDENT):
    tok = None
    if identity is not None:
        tok = streaming._DRIVE_LEG.set(streaming._DriveLegScope(
            cache_key=None, hot_identity=identity))
    try:
        resp = await streaming.serve_mount_relay_async(
            "https://drive.example/media", _request(range_header),
            {"User-Agent": "t-ua"}, "video/mp4")
        chunks = []
        async for c in resp.body_iterator:
            chunks.append(c)
        return resp.status_code, dict(resp.headers), b"".join(chunks)
    finally:
        if tok is not None:
            streaming._DRIVE_LEG.reset(tok)


def _meta():
    return hc.get_meta(IDENT)


# ---------------- 写穿 → 全命中 ----------------

def test_write_through_then_hit_skips_upstream(cache_env, upstream):
    """首请求透传写穿；之后同文件区间请求全命中、不回源。"""
    status, _h, body = asyncio.run(_serve())
    assert status == 200
    assert body == BODY
    assert upstream.count == 1
    hc.flush_writes()
    meta = _meta()
    assert meta and meta["total_size"] == len(BODY)
    for idx in (0, 1, 2):
        assert hc.segment_present(IDENT, meta, idx), idx

    hits_before = hc.stats()["hit_requests"]
    status, headers, body = asyncio.run(_serve("bytes=100-199"))
    assert status == 206
    assert body == BODY[100:200]
    assert headers.get("content-range") == f"bytes 100-199/{len(BODY)}"
    assert upstream.count == 1, "全命中不许再回源"
    assert hc.stats()["hit_requests"] == hits_before + 1

    # 整文件请求同样全命中
    status, _h, body = asyncio.run(_serve())
    assert status == 200
    assert body == BODY
    assert upstream.count == 1


def test_partial_write_through_only_completed_segments(cache_env, upstream):
    """区间只完整覆盖到的分段才落盘；段内小区间不落盘、仍回源。"""
    status, _h, body = asyncio.run(
        _serve(f"bytes=0-{SEG - 1}"))
    assert status == 206
    assert body == BODY[:SEG]
    hc.flush_writes()
    meta = _meta()
    assert meta
    assert hc.segment_present(IDENT, meta, 0)
    assert not hc.segment_present(IDENT, meta, 1)

    # 第 0 段内的小区间 → 命中
    status, _h, body = asyncio.run(_serve("bytes=5-50"))
    assert status == 206
    assert body == BODY[5:51]
    assert upstream.count == 1

    # 第 1 段的小区间 → 未落盘，回源
    status, _h, body = asyncio.run(
        _serve(f"bytes={SEG}-{SEG + 99}"))
    assert status == 206
    assert body == BODY[SEG:SEG + 100]
    assert upstream.count == 2


def test_no_identity_no_cache(cache_env, upstream):
    """无 hot_identity（挂载腿形态）→ 缓存完全不介入。"""
    for _ in range(2):
        status, _h, body = asyncio.run(_serve(identity=None))
        assert status == 200
        assert body == BODY
    assert upstream.count == 2
    assert _meta() is None
    seg_files = []
    for root, _dirs, files in os.walk(cache_env):
        seg_files += [f for f in files if f.startswith("seg-")]
    assert seg_files == []


def test_validator_change_invalidates(cache_env, upstream):
    """ETag 变 → 整身份失效：旧分段不再命中、按新校验器重建。"""
    # 只缓存第 0 段（第 1 段留着做后续未命中透传的触发口）
    asyncio.run(_serve(f"bytes=0-{SEG - 1}"))
    hc.flush_writes()
    meta = _meta()
    assert hc.segment_present(IDENT, meta, 0)
    assert upstream.count == 1

    upstream.etag = '"v2"'
    # 第 1 段区间未命中走透传，响应头带新 ETag → 触发失效
    asyncio.run(_serve(f"bytes={SEG}-{SEG + 10}"))
    hc.flush_writes()
    assert upstream.count == 2
    meta2 = _meta()
    assert meta2 and meta2["etag"] == '"v2"'
    assert not hc.segment_present(IDENT, meta2, 0), "旧分段必须已失效"

    asyncio.run(_serve("bytes=0-99"))
    assert upstream.count == 3, "失效后同区间必须回源"


# ---------------- LRU / 预读 / fail-open ----------------

def test_stale_generation_write_dropped(cache_env):
    """代际绑定：身份被失效后，旧代际的在途写入必须被写线程丢弃
    （防文件被同尺寸替换时旧字节落进新一代元数据）。"""
    total = 3 * SEG
    assert hc.note_validators(IDENT, total, etag='"v1"')
    gen = hc.current_generation(IDENT)
    hc.invalidate(IDENT)
    assert hc.note_validators(IDENT, total, etag='"v2"')
    assert hc.submit_segment(IDENT, 0, b"s" * SEG, total, gen=gen)
    hc.flush_writes()
    meta = hc.get_meta(IDENT)
    assert not hc.segment_present(IDENT, meta, 0), "旧代际写入必须丢弃"
    # 当前代际照常落盘
    assert hc.submit_segment(IDENT, 0, b"s" * SEG, total)
    hc.flush_writes()
    assert hc.segment_present(IDENT, meta, 0)


def test_lru_eviction_by_mtime(cache_env, monkeypatch):
    """超容量按 mtime 从旧到新淘汰到上限以下。"""
    monkeypatch.setattr(hc, "max_bytes", lambda: 2 * SEG + SEG // 2)
    total = 3 * SEG
    idents = ["drive:D9:A", "drive:D9:B", "drive:D9:C"]
    for i, ident in enumerate(idents):
        assert hc.note_validators(ident, total, etag='"v1"')
        assert hc.submit_segment(ident, 0, bytes([i + 1]) * SEG, total)
        hc.flush_writes()
        if i == 0:
            # 把 A 的分段 mtime 拨老，钉死淘汰顺序与写序无关
            p = hc._segment_path(ident, 0)
            os.utime(p, (1_000_000, 1_000_000))
    assert hc.stats()["segments_evicted"] >= 1
    meta_a = hc.get_meta(idents[0])
    assert not hc.segment_present(idents[0], meta_a, 0), "最旧分段应被淘汰"
    for ident in idents[1:]:
        meta = hc.get_meta(ident)
        assert hc.segment_present(ident, meta, 0), ident


def test_prefetch_bounded_and_deduped(cache_env, monkeypatch):
    """预读只取请求区间之后 N 段；已缓存跳过、并发在途去重。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_SEGMENTS", "2")
    ident = "drive:D9:P"
    total = 5 * SEG
    assert hc.note_validators(ident, total, etag='"v1"')
    assert hc.submit_segment(ident, 0, b"\x00" * SEG, total)
    hc.flush_writes()

    calls = []

    async def fetcher(s, e):
        calls.append((s, e))
        return bytes([(s // SEG) % 250 + 1]) * (e - s + 1)

    asyncio.run(streaming._hot_prefetch_run(ident, "bytes=0-99", fetcher))
    hc.flush_writes()
    assert calls == [(SEG, 2 * SEG - 1), (2 * SEG, 3 * SEG - 1)]
    meta = hc.get_meta(ident)
    assert hc.segment_present(ident, meta, 1)
    assert hc.segment_present(ident, meta, 2)
    assert not hc.segment_present(ident, meta, 3)

    # 再跑一次：已缓存 → 一次都不取
    asyncio.run(streaming._hot_prefetch_run(ident, "bytes=0-99", fetcher))
    assert len(calls) == 2

    # 并发两路同区间预读：在途去重，每段只取一次
    ident2 = "drive:D9:Q"
    assert hc.note_validators(ident2, total, etag='"v1"')
    assert hc.submit_segment(ident2, 0, b"\x00" * SEG, total)
    hc.flush_writes()
    calls2 = []

    async def slow_fetcher(s, e):
        calls2.append((s, e))
        await asyncio.sleep(0.05)
        return b"\x01" * (e - s + 1)

    async def _both():
        await asyncio.gather(
            streaming._hot_prefetch_run(ident2, "bytes=0-99", slow_fetcher),
            streaming._hot_prefetch_run(ident2, "bytes=0-99", slow_fetcher))

    asyncio.run(_both())
    assert sorted(calls2) == [(SEG, 2 * SEG - 1), (2 * SEG, 3 * SEG - 1)]


def test_cache_failure_fail_open(cache_env, upstream, monkeypatch):
    """缓存层抛错 → 回现行透传，字节无损、不回 5xx。"""
    def boom(*a, **k):
        raise RuntimeError("cache down")

    monkeypatch.setattr(hc, "get_meta", boom)
    monkeypatch.setattr(hc, "note_validators", boom)
    status, _h, body = asyncio.run(_serve())
    assert status == 200
    assert body == BODY
    assert upstream.count == 1


def test_open_segments_failure_fail_open(cache_env, upstream, monkeypatch):
    """命中校验/打开抛错 → 回现行透传。"""
    asyncio.run(_serve())
    hc.flush_writes()
    assert _meta()
    monkeypatch.setattr(hc, "open_segments", lambda *a, **k: 1 / 0)
    status, _h, body = asyncio.run(_serve("bytes=0-99"))
    assert status == 206
    assert body == BODY[0:100]
    assert upstream.count == 2


# ---------------- 纯函数口径 ----------------

def test_parse_range_cases():
    assert hc.parse_range(None, 1000) == (0, 999)
    assert hc.parse_range("bytes=0-99", 1000) == (0, 99)
    assert hc.parse_range("bytes=100-", 1000) == (100, 999)
    assert hc.parse_range("bytes=-50", 1000) == (950, 999)
    assert hc.parse_range("bytes=900-5000", 1000) == (900, 999)
    assert hc.parse_range("bytes=1000-", 1000) is None
    assert hc.parse_range("bytes=0-99,200-299", 1000) is None
    assert hc.parse_range("items=0-99", 1000) is None
    assert hc.parse_range("bytes=-0", 1000) is None
    assert hc.parse_range("bytes=abc-def", 1000) is None
    assert hc.parse_range("bytes=0-99", 0) is None


def test_pool_limits_env_parsing(monkeypatch):
    for key in ("RELAY_MAX_CONNECTIONS", "RELAY_MAX_KEEPALIVE_CONNECTIONS",
                "DRIVE_RELAY_MAX_CONNECTIONS",
                "DRIVE_RELAY_MAX_KEEPALIVE_CONNECTIONS"):
        monkeypatch.delenv(key, raising=False)
    assert streaming._mount_pool_limits() == (32, 16)
    assert streaming._drive_pool_limits() == (32, 16)
    monkeypatch.setenv("RELAY_MAX_CONNECTIONS", "48")
    assert streaming._mount_pool_limits() == (48, 16)
    monkeypatch.setenv("RELAY_MAX_KEEPALIVE_CONNECTIONS", "999")
    assert streaming._mount_pool_limits() == (48, 48), "保活数不超总数"
    monkeypatch.setenv("RELAY_MAX_CONNECTIONS", "abc")
    assert streaming._mount_pool_limits()[0] == 32, "非法回默认"
    monkeypatch.setenv("DRIVE_RELAY_MAX_CONNECTIONS", "64")
    assert streaming._drive_pool_limits() == (64, 16)


def test_drive_leg_sets_hot_identity(monkeypatch):
    """serve_drive_relay_async 给 SA/令牌两腿都注入热缓存身份。"""
    seen = {}

    async def fake_mount(url, request, headers=None, media_type="video/mp4",
                         pt=None, sniff_quota_page=False):
        leg = streaming._DRIVE_LEG.get()
        seen["identity"] = getattr(leg, "hot_identity", None)
        return "RESP"

    monkeypatch.setattr(streaming, "serve_mount_relay_async", fake_mount)
    import backend.emby_server.drive_changes as _dc
    monkeypatch.setattr(_dc, "get_token_for_drive",
                        lambda drive_id, force=False: "TOK")
    req = Request({"type": "http", "method": "GET", "path": "/",
                   "headers": [], "query_string": b"",
                   "server": ("t", 80), "client": ("c", 1),
                   "scheme": "http", "raw_path": b"/"})
    out = asyncio.run(streaming.serve_drive_relay_async(
        req, "DRIVE9", "FID123"))
    assert out == "RESP"
    assert seen["identity"] == "drive:DRIVE9:FID123"
    out = asyncio.run(streaming.serve_drive_relay_async(
        req, "pooldrive:7", "FID999", access_token="MEMBERTOK"))
    assert out == "RESP"
    assert seen["identity"] == "drive:pooldrive:7:FID999"
