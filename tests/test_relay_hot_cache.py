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


def _pattern_fetcher(calls):
    """按绝对偏移生成可校验字节的假 fetcher：每段填段号+1。"""
    async def fetcher(s, e):
        calls.append((s, e))
        out = bytearray()
        pos = s
        while pos <= e:
            idx = pos // SEG
            take = min(SEG - (pos - idx * SEG), e - pos + 1)
            out += bytes([idx % 250 + 1]) * take
            pos += take
        return bytes(out)
    return fetcher


def test_prefetch_open_ended_range_anchors_at_start(cache_env):
    """开尾 Range 预读钉子（L-007 根因 2）：旧口径以区间末尾为锚，
    开尾时末尾＝文件尾 → 取数恒 0 次。新口径以起始段为锚、从覆盖
    前沿起按字节窗口取数：空缓存时第一个取数区间必须从 0 开始。"""
    ident = "drive:D9:OPEN"
    total = 5 * SEG
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-", _pattern_fetcher(calls)))
    hc.flush_writes()
    assert calls, "开尾 Range 的预读取数不许为 0 次"
    assert calls[0][0] == 0
    meta = hc.get_meta(ident)
    for idx in range(5):
        assert hc.segment_present(ident, meta, idx), idx


def test_prefetch_fetch_granularity_and_cut(cache_env):
    """取数粒度（L-007 根因 3）：窗口内按 8MB 一次 GET 取数、本地
    切成 1MB 段逐段提交；尾窗按实际区间收尾。"""
    ident = "drive:D9:GRAN"
    total = 20 * SEG
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []
    fetched_before = hc.stats()["prefetch_fetched"]
    bytes_before = hc.stats()["prefetch_bytes"]
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-99", _pattern_fetcher(calls)))
    hc.flush_writes()
    assert calls == [(0, 8 * SEG - 1), (8 * SEG, 16 * SEG - 1),
                     (16 * SEG, total - 1)]
    meta = hc.get_meta(ident)
    for idx in range(20):
        assert hc.segment_present(ident, meta, idx), idx
    # 切段字节逐字对：段内字节与 fetcher 的按段图案一致
    with open(hc._segment_path(ident, 3), "rb") as fh:
        assert fh.read() == bytes([4]) * SEG
    assert hc.stats()["prefetch_fetched"] - fetched_before == 20
    assert hc.stats()["prefetch_bytes"] - bytes_before == total


def test_prefetch_generation_change_drops_window(cache_env):
    """取数中途身份被失效（代际 +1）→ 在途窗口全部段按代际丢弃、
    且不再继续取下一块。"""
    ident = "drive:D9:GEN"
    total = 20 * SEG
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []

    async def killing_fetcher(s, e):
        calls.append((s, e))
        if len(calls) == 1:
            hc.invalidate(ident)
            assert hc.note_validators(ident, total, etag='"v2"')
        return b"\x07" * (e - s + 1)

    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-99", killing_fetcher))
    hc.flush_writes()
    assert len(calls) == 1, "代际变化后不许再取下一块"
    meta = hc.get_meta(ident)
    for idx in range(8):
        assert not hc.segment_present(ident, meta, idx), idx


def test_prefetch_window_frontier_and_singleflight(cache_env):
    """窗口从覆盖前沿起算（已缓存段不重取）；同身份同锚点并发只
    允许一个窗口在途（single-flight），每块只取一次。"""
    ident = "drive:D9:FRONT"
    total = 33 * SEG
    assert hc.note_validators(ident, total, etag='"v1"')
    assert hc.submit_segment(ident, 0, b"\x00" * SEG, total)
    hc.flush_writes()
    calls = []
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-99", _pattern_fetcher(calls)))
    hc.flush_writes()
    # 前沿在第 1 段：32MB 窗口 = 4 次 8MB 取数，覆盖 1..32 段
    assert calls == [(SEG, 9 * SEG - 1), (9 * SEG, 17 * SEG - 1),
                     (17 * SEG, 25 * SEG - 1), (25 * SEG, total - 1)]
    meta = hc.get_meta(ident)
    for idx in range(33):
        assert hc.segment_present(ident, meta, idx), idx
    # 再跑：窗口内已全覆盖 → 零取数
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-99", _pattern_fetcher(calls)))
    assert len(calls) == 4

    # 并发两路同锚点：single-flight，第二路直接返回不重复取数
    ident2 = "drive:D9:FRONT2"
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
    hc.flush_writes()
    assert sorted(calls2) == [(SEG, 9 * SEG - 1), (9 * SEG, 17 * SEG - 1),
                              (17 * SEG, 25 * SEG - 1),
                              (25 * SEG, total - 1)]


def test_prefetch_env_parsing(monkeypatch):
    """预读 env 口径：窗口缺省 32MB、钳 [0, 128MB]；取数粒度缺省
    8MB、按整段对齐（向下取整到 1MB 的整数倍、最小 1 段）。"""
    monkeypatch.delenv("RELAY_HOT_CACHE_PREFETCH_BYTES", raising=False)
    monkeypatch.delenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       raising=False)
    assert hc.prefetch_bytes() == 32 * SEG
    assert hc.prefetch_fetch_bytes() == 8 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    assert hc.prefetch_bytes() == 0
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(10 ** 12))
    assert hc.prefetch_bytes() == 128 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "abc")
    assert hc.prefetch_bytes() == 32 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES", "2500000")
    assert hc.prefetch_fetch_bytes() == 2 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES", "100")
    assert hc.prefetch_fetch_bytes() == SEG


def test_drive_prefetch_same_window_fetch(cache_env, monkeypatch):
    """Drive 腿与挂载腿同一套预读（横切只许一套）：Drive fetcher
    被窗口以 8MB 区间调用、字节切段落盘到 drive: 身份下。"""
    ident = "drive:D9:L7WIN"
    total = 10 * SEG
    body = (bytes(range(256)) * (total // 256 + 1))[:total]
    up = _FakeUpstream(body=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(up.handler))
    monkeypatch.setattr(streaming, "_acquire_drive_client",
                        lambda: (client, False))
    assert hc.note_validators(ident, total, etag='"v1"')

    async def fetcher(s, e):
        return await streaming._fetch_drive_segment_bytes(
            "D9", "L7WIN", "TOK", s, e)

    asyncio.run(streaming._hot_prefetch_run(ident, "bytes=0-99", fetcher))
    hc.flush_writes()
    assert up.ranges == ["bytes=0-%d" % (8 * SEG - 1),
                         "bytes=%d-%d" % (8 * SEG, total - 1)]
    meta = hc.get_meta(ident)
    for idx in range(10):
        assert hc.segment_present(ident, meta, idx), idx


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
