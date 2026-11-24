# -*- coding: utf-8 -*-
"""挂载中继腿热缓存接线（2026-11-23 播放卡顿修复）。

背景：#428 智能中继三件套（热缓存命中/写穿/跨请求预读）只对 Drive
中继腿生效，挂载腿被排除——而全库已是 mount:// 条目，播放器每个
新请求（起播/拖动/续传/并行分段）都要等 rcd 向谷歌重新开流
（TTFB 1.6–2.2s），缓冲一空就卡。本期给挂载腿接上同一套机制：
身份 = 条目绑定的谷歌 drive_file_id（``mount:{drive_file_id}``，
谷歌侧不可变身份、与内容绑定），由 api.video_stream 在挂载取流
调用点注入 serve_mount_relay_async 的 hot_identity 参数；不启用
Drive 腿的 SA 池/最终 URL 缓存/熔断。

覆盖：
- 写穿→全命中：挂载腿（无 Drive 上下文）带身份首请求透传并落盘；
  第二轮同 Range 全命中、不打上游。
- 跨请求预读：出流后把请求区间之后 N 段提前取回落盘
  （prefetch_fetched 增量 > 0、上游收到预读段 Range）。
- 无身份（无 drive_file_id 的挂载条目形态，参数不传）：行为与
  现状一致——每次回源、缓存完全不介入。
- 缓存层故障 fail-open：登记/读元数据抛错时回现行透传、字节无损。
- 淘汰与清理（2026-11-23 用户追加）：写超容量后按 mtime 最旧
  分段被 LRU 淘汰；校验器变化整身份失效、旧分段文件被清。
- 容量口径（同日追加）：缺省总上限 20GiB（配置键可调）；磁盘
  余量低于安全线（缺省 10GiB）停写新分段、出流照常透传。
- api 接线：video_stream 挂载分支对有 drive_file_id 的条目传
  ``mount:{fid}``、无 fid 传 None。

全部离线：上游是 httpx MockTransport 计数型假源，不碰外网；
api 接线用例的 db/user/鉴权全部桩掉（同 test_mount_relay 口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import Response

from backend.emby_server import api
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import relay_hot_cache as hc
from backend.emby_server import streaming
from backend.emby_server.mounts import PlayTarget

SEG = hc.SEGMENT_SIZE
BODY = (bytes(range(256)) * 12000)[: 2 * SEG + SEG // 2]  # 2.5 段
MIDENT = "mount:FIDMOUNT123"
FID = "FIDMOUNT123"


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


async def _serve_mount(range_header=None, identity=None):
    """挂载腿形态出流：不设 _DRIVE_LEG 上下文，身份只经参数传。"""
    assert streaming._DRIVE_LEG.get() is None
    resp = await streaming.serve_mount_relay_async(
        "https://rcd.example/media", _request(range_header),
        {"User-Agent": "t-ua"}, "video/mp4", hot_identity=identity)
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c)
    return resp.status_code, dict(resp.headers), b"".join(chunks)


# ---------------- 写穿 → 全命中 ----------------

def test_mount_write_through_then_hit_skips_upstream(
        cache_env, upstream, monkeypatch):
    """首请求透传写穿；第二轮同 Range 全命中、不打上游。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    status, _h, body = asyncio.run(_serve_mount(identity=MIDENT))
    assert status == 200
    assert body == BODY
    assert upstream.count == 1
    hc.flush_writes()
    meta = hc.get_meta(MIDENT)
    assert meta and meta["total_size"] == len(BODY)
    for idx in (0, 1, 2):
        assert hc.segment_present(MIDENT, meta, idx), idx

    hits_before = hc.stats()["hit_requests"]
    status, headers, body = asyncio.run(
        _serve_mount("bytes=100-199", identity=MIDENT))
    assert status == 206
    assert body == BODY[100:200]
    assert headers.get("content-range") == f"bytes 100-199/{len(BODY)}"
    assert upstream.count == 1, "全命中不许再回源"
    assert hc.stats()["hit_requests"] == hits_before + 1


def test_mount_prefetch_fetches_window_from_frontier(
        cache_env, upstream, monkeypatch):
    """出流后跨请求预读：从覆盖前沿起按字节窗口取数（大区间一次
    GET、本地切段落盘）；窗口外的段不取。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(2 * SEG))
    fetched_before = hc.stats()["prefetch_fetched"]

    async def scenario():
        status, _h, body = await _serve_mount(
            "bytes=0-99", identity=MIDENT)
        assert status == 206
        assert body == BODY[0:100]
        # 预读是后台任务：同一 loop 里等它跑完
        for _ in range(100):
            if hc.stats()["prefetch_fetched"] >= fetched_before + 2:
                break
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    hc.flush_writes()
    assert hc.stats()["prefetch_fetched"] - fetched_before == 2
    meta = hc.get_meta(MIDENT)
    assert meta
    # 前沿＝第 0 段（首请求只流过 100 字节、没有完整段落盘）：
    # 窗口 2MB 一次取回第 0、1 段，第 2 段在窗口外不取
    assert hc.segment_present(MIDENT, meta, 0)
    assert hc.segment_present(MIDENT, meta, 1)
    assert not hc.segment_present(MIDENT, meta, 2)
    assert f"bytes=0-{2 * SEG - 1}" in upstream.ranges


def test_mount_prefetch_after_open_ended_request(cache_env, upstream):
    """开尾请求出流后预读真触发（L-007 根因 2 的端到端钉子）：旧
    口径锚在区间末尾＝文件尾、取数恒 0；新口径从前沿按窗口取数，
    整个文件被一次大区间取数覆盖。"""
    fetched_before = hc.stats()["prefetch_fetched"]

    async def scenario():
        status, _h, body = await _serve_mount(
            "bytes=0-99", identity=MIDENT)
        assert status == 206
        assert body == BODY[0:100]
        for _ in range(100):
            if hc.stats()["prefetch_fetched"] >= fetched_before + 3:
                break
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    hc.flush_writes()
    assert hc.stats()["prefetch_fetched"] - fetched_before == 3
    assert f"bytes=0-{len(BODY) - 1}" in upstream.ranges
    meta = hc.get_meta(MIDENT)
    for idx in (0, 1, 2):
        assert hc.segment_present(MIDENT, meta, idx), idx


def test_mount_no_identity_no_cache(cache_env, upstream):
    """无 hot_identity（无 drive_file_id 的挂载条目）→ 逐字现状。"""
    for _ in range(2):
        status, _h, body = asyncio.run(_serve_mount(identity=None))
        assert status == 200
        assert body == BODY
    assert upstream.count == 2
    assert hc.get_meta(MIDENT) is None
    seg_files = []
    for root, _dirs, files in os.walk(cache_env):
        seg_files += [f for f in files if f.startswith("seg-")]
    assert seg_files == []


def test_mount_cache_failure_fail_open(cache_env, upstream, monkeypatch):
    """缓存层抛错 → 回现行透传，字节无损、不回 5xx。"""
    def boom(*a, **k):
        raise RuntimeError("cache down")

    monkeypatch.setattr(hc, "get_meta", boom)
    monkeypatch.setattr(hc, "note_validators", boom)
    status, _h, body = asyncio.run(_serve_mount(identity=MIDENT))
    assert status == 200
    assert body == BODY
    assert upstream.count == 1


# ---------------- 淘汰 / 失效清理 / 磁盘护栏 ----------------

def test_mount_lru_eviction_over_capacity(
        cache_env, upstream, monkeypatch):
    """写超容量 → 按 mtime 最旧的挂载身份分段被淘汰（只写不清不许）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    monkeypatch.setattr(hc, "max_bytes", lambda: 2 * SEG + SEG // 2)
    idents = ["mount:LRU-A", "mount:LRU-B", "mount:LRU-C"]
    evicted_before = hc.stats()["segments_evicted"]
    for i, ident in enumerate(idents):
        status, _h, body = asyncio.run(
            _serve_mount(f"bytes=0-{SEG - 1}", identity=ident))
        assert status == 206
        assert body == BODY[:SEG]
        hc.flush_writes()
        if i == 0:
            # 把 A 的分段 mtime 拨老，钉死淘汰顺序与写序无关
            os.utime(hc._segment_path(ident, 0), (1_000_000, 1_000_000))
    assert hc.stats()["segments_evicted"] > evicted_before
    meta_a = hc.get_meta(idents[0])
    assert not hc.segment_present(idents[0], meta_a, 0), \
        "超容量后最旧分段必须被淘汰"
    for ident in idents[1:]:
        meta = hc.get_meta(ident)
        assert hc.segment_present(ident, meta, 0), ident


def test_mount_validator_change_cleans_identity(
        cache_env, upstream, monkeypatch):
    """上游校验器（ETag）变 → 整身份失效、旧分段文件被清掉。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    status, _h, _b = asyncio.run(
        _serve_mount(f"bytes=0-{SEG - 1}", identity=MIDENT))
    assert status == 206
    hc.flush_writes()
    meta = hc.get_meta(MIDENT)
    assert meta and meta["etag"] == '"m1"'
    seg0_path = hc._segment_path(MIDENT, 0)
    assert hc.segment_present(MIDENT, meta, 0)
    assert os.path.exists(seg0_path)

    upstream.etag = '"m2"'
    # 未缓存区间走透传，响应头带新 ETag → 触发整身份失效
    status, _h, _b = asyncio.run(
        _serve_mount(f"bytes={SEG}-{SEG + 10}", identity=MIDENT))
    assert status == 206
    hc.flush_writes()
    meta2 = hc.get_meta(MIDENT)
    assert meta2 and meta2["etag"] == '"m2"'
    assert not os.path.exists(seg0_path), "失效身份的旧分段必须清掉"
    assert not hc.segment_present(MIDENT, meta2, 0)


def test_mount_disk_guardrail_stops_writes_fail_open(
        cache_env, upstream, monkeypatch):
    """磁盘余量低于安全线 → 停写缓存、出流照常透传（fail-open）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    monkeypatch.setattr(hc, "min_free_bytes", lambda: 10 ** 15)
    dropped_before = hc.stats()["segments_dropped"]
    status, _h, body = asyncio.run(_serve_mount(identity=MIDENT))
    assert status == 200
    assert body == BODY, "护栏触发时播放字节必须无损透传"
    assert upstream.count == 1
    hc.flush_writes()
    assert hc.stats()["segments_dropped"] > dropped_before
    seg_files = []
    for root, _dirs, files in os.walk(cache_env):
        seg_files += [f for f in files if f.startswith("seg-")]
    assert seg_files == [], "护栏触发时不许落任何分段"
    # 投递口径直钉：余量不足时 submit 直接拒绝
    assert hc.submit_segment(MIDENT, 0, b"x" * SEG, len(BODY)) is False


def test_huge_file_write_through_never_exceeds_cap(
        cache_env, monkeypatch):
    """超大文件（200GiB 电影）连续写穿：缓存是分段级的——只落实际
    流过的段，盘上占用始终 ≤ 容量硬顶，最旧段被 LRU 挤掉；绝不
    因为文件大就整文件缓存或放大上限。"""
    cap = 4 * SEG
    monkeypatch.setattr(hc, "max_bytes", lambda: cap)
    ident = "mount:HUGE200G"
    total = 200 * 1024 * 1024 * 1024  # 200GiB，远大于上限
    assert hc.note_validators(
        ident, total, etag='"h1"',
        last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    gen = hc.current_generation(ident)

    def disk_bytes():
        n = 0
        for root, _d, files in os.walk(cache_env):
            for f in files:
                if f.startswith("seg-"):
                    n += os.path.getsize(os.path.join(root, f))
        return n

    async def write_segments(first_idx, count):
        # 走真实写穿路径（_hot_cache_tee）：从 first_idx 段起流过
        # count 个完整段
        plan = {"identity": ident, "start": first_idx * SEG,
                "total": total, "gen": gen}

        async def source():
            for i in range(first_idx, first_idx + count):
                yield bytes([i + 1]) * SEG

        async for _chunk in streaming._hot_cache_tee(source(), plan):
            pass

    for rnd in range(3):
        asyncio.run(write_segments(rnd * 4, 4))
        hc.flush_writes()
        assert disk_bytes() <= cap, \
            f"第 {rnd + 1} 轮写穿后占用 {disk_bytes()} 超上限 {cap}"
        # 已落盘段按序号拨老 mtime，钉死 LRU 顺序与写入顺序一致
        for idx in range(rnd * 4 + 4):
            p = hc._segment_path(ident, idx)
            if os.path.exists(p):
                os.utime(p, (1_000_000 + idx, 1_000_000 + idx))
    meta = hc.get_meta(ident)
    assert not hc.segment_present(ident, meta, 0), "最旧段必须被淘汰"
    assert hc.segment_present(ident, meta, 11), "最新段必须保留"
    assert disk_bytes() <= cap


def test_default_capacity_is_20g():
    """缺省总上限 20GiB（2026-11-23 用户亲令：根盘 435G 别贪大）；
    配置键仍可调（SystemConfig relay_hot_cache_max_bytes）。"""
    assert hc.DEFAULT_MAX_BYTES == 20 * 1024 * 1024 * 1024
    assert hc.KEY_MAX_BYTES == "relay_hot_cache_max_bytes"
    # 磁盘护栏缺省阈值 10GiB
    assert hc.min_free_bytes() == 10 * 1024 * 1024 * 1024


# ---------------- api 接线 ----------------

def _stub_stream_common(monkeypatch, item, target):
    monkeypatch.setattr(
        api, "_require_visible_item", lambda db, user, item_id: item)
    monkeypatch.setattr(api, "ensure_playback_allowed", lambda db, user: None)
    monkeypatch.setattr(
        "backend.emby_server.playback_gate.ensure_binding_playback_allowed",
        lambda db, u, r=None: None)
    monkeypatch.setattr(api.play_sign, "check_referer", lambda *a, **k: None)
    monkeypatch.setattr(api, "_play_target", lambda db, it: target)
    monkeypatch.setattr(_strategy, "maybe_direct_url", lambda db, fid: None)
    # 直链开关关（生产现行口径）：带 fid 的挂载条目落到挂载中继分支
    monkeypatch.setattr(
        _strategy, "drive_direct_play_enabled", lambda db: False)


def _capture_relay(monkeypatch):
    calls = {}

    async def fake_relay(url, request, headers, media_type, pt=None,
                         sniff_quota_page=False, hot_identity=None):
        calls.update(url=url, headers=headers,
                     media_type=media_type, hot_identity=hot_identity)
        return Response(b"fake-bytes", media_type="video/mp4")

    monkeypatch.setattr(streaming, "serve_mount_relay_async", fake_relay)
    return calls


def test_api_mount_item_with_fid_passes_hot_identity(monkeypatch):
    """挂载条目有 drive_file_id → 传 mount:{fid}。"""
    item = SimpleNamespace(container="mkv", drive_file_id=FID,
                           file_path="mount://1/Movies/a.mkv")
    target = PlayTarget("url", "http://127.0.0.1:5574/%5BMP%5D/a.mkv",
                        {"User-Agent": "rcd-ua"})
    _stub_stream_common(monkeypatch, item, target)
    calls = _capture_relay(monkeypatch)

    resp = asyncio.run(api.video_stream("260026", _request(),
                                        object(), object()))
    assert resp.status_code == 200
    assert calls["hot_identity"] == f"mount:{FID}"


def test_api_mount_item_without_fid_passes_none(monkeypatch):
    """挂载条目无 drive_file_id → 传 None（缓存不介入）。"""
    item = SimpleNamespace(container="mkv", drive_file_id="",
                           file_path="mount://1/Movies/a.mkv")
    target = PlayTarget("url", "http://127.0.0.1:5574/%5BMP%5D/a.mkv",
                        {"User-Agent": "rcd-ua"})
    _stub_stream_common(monkeypatch, item, target)
    calls = _capture_relay(monkeypatch)

    resp = asyncio.run(api.video_stream("item", _request(),
                                        object(), object()))
    assert resp.status_code == 200
    assert calls["hot_identity"] is None


# ---------------- L-007：部分覆盖出流 ----------------

BIG = (bytes(range(256)) * 30000)[: 6 * SEG + SEG // 2]  # 6.5 段
PIDENT = "mount:L7PARTIAL"


def _seed_segments(ident, body, idxs, monkeypatch):
    """把 body 的指定完整分段直接落盘（同既有夹具的直投口径）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    assert hc.note_validators(
        ident, len(body), etag='"p1"',
        last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    for idx in idxs:
        exp = hc.segment_expected_len(len(body), idx)
        assert hc.submit_segment(
            ident, idx, body[idx * SEG: idx * SEG + exp], len(body))
    hc.flush_writes()


def _local_upstream(monkeypatch, body):
    up = _FakeUpstream(body=body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(up.handler))
    monkeypatch.setattr(streaming, "_mount_relay_client", lambda: client)
    return up


def test_mount_partial_hit_open_ended_range(
        cache_env, upstream, monkeypatch):
    """开尾 Range + 起始段起连续覆盖 ≥1 段 → 部分出流（L-007 改动
    一）：206、Content-Range/Content-Length 钳到覆盖末尾、字节逐字
    对、不回源。"""
    _seed_segments(PIDENT, BIG, (0, 1, 2), monkeypatch)
    partial_before = hc.stats()["partial_requests"]
    hits_before = hc.stats()["hit_requests"]
    status, headers, body = asyncio.run(
        _serve_mount("bytes=0-", identity=PIDENT))
    assert status == 206
    assert headers.get("content-range") == \
        f"bytes 0-{3 * SEG - 1}/{len(BIG)}"
    assert headers.get("content-length") == str(3 * SEG)
    assert body == BIG[:3 * SEG]
    assert upstream.count == 0, "部分出流不许回源"
    assert hc.stats()["partial_requests"] == partial_before + 1
    assert hc.stats()["hit_requests"] == hits_before

    # 段内起始：从已覆盖段中间起，同样钳到覆盖末尾
    status, headers, body = asyncio.run(
        _serve_mount("bytes=100-", identity=PIDENT))
    assert status == 206
    assert headers.get("content-range") == \
        f"bytes 100-{3 * SEG - 1}/{len(BIG)}"
    assert body == BIG[100:3 * SEG]
    assert upstream.count == 0


def test_mount_partial_hit_disabled_by_env(cache_env, monkeypatch):
    """部分出流开关关 → 回现行全覆盖口径：开尾请求透传回源。"""
    _seed_segments(PIDENT, BIG, (0, 1, 2), monkeypatch)
    monkeypatch.setenv("RELAY_HOT_CACHE_PARTIAL_ENABLED", "0")
    up = _local_upstream(monkeypatch, BIG)
    status, _h, body = asyncio.run(
        _serve_mount("bytes=0-", identity=PIDENT))
    hc.flush_writes()
    assert status == 206
    assert body == BIG
    assert up.count == 1


def test_mount_no_partial_for_suffix_range(cache_env, monkeypatch):
    """后缀 Range 不走部分出流（规格明示排除）：区间首段有覆盖、
    尾段缺失时仍回源透传完整后缀区间。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    body4 = BIG[:4 * SEG]
    ident = "mount:L7SUFFIX"
    assert hc.note_validators(ident, len(body4), etag='"p1"')
    assert hc.submit_segment(ident, 2, body4[2 * SEG:3 * SEG], len(body4))
    hc.flush_writes()
    up = _local_upstream(monkeypatch, body4)
    status, _h, body = asyncio.run(
        _serve_mount(f"bytes=-{2 * SEG}", identity=ident))
    assert status == 206
    assert body == body4[2 * SEG:]
    assert up.count == 1, "后缀 Range 不许走部分出流"


def test_mount_no_partial_when_start_segment_missing(
        cache_env, monkeypatch):
    """起始段无覆盖（第 0 段缺失、第 1 段在）→ 不出流、回透传。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", "0")
    ident = "mount:L7NOHEAD"
    assert hc.note_validators(ident, len(BIG), etag='"p1"')
    assert hc.submit_segment(ident, 1, BIG[SEG:2 * SEG], len(BIG))
    hc.flush_writes()
    up = _local_upstream(monkeypatch, BIG)
    status, _h, body = asyncio.run(
        _serve_mount("bytes=0-", identity=ident))
    assert status == 206
    assert body == BIG
    assert up.count == 1


def test_mount_malformed_range_passthrough(cache_env, monkeypatch):
    """多段 Range（parse_range 不认）→ 不走部分出流、逐字透传。"""
    _seed_segments(PIDENT, BIG, (0, 1, 2), monkeypatch)

    def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "video/mp4",
                          "Content-Length": str(len(BIG))},
            content=BIG)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(streaming, "_mount_relay_client", lambda: client)
    status, _h, body = asyncio.run(
        _serve_mount("bytes=0-99,200-299", identity=PIDENT))
    hc.flush_writes()
    assert status == 200
    assert body == BIG


def test_mount_partial_hit_chain_advances(cache_env, monkeypatch):
    """链式前进（规格用例 5）：部分出流后覆盖前移，播放器对剩余
    区间重发请求能继续部分出流，Content-Range 逐字接续。"""
    _seed_segments("mount:L7CHAIN", BIG, (0, 1), monkeypatch)
    up = _local_upstream(monkeypatch, BIG)
    status, headers, body = asyncio.run(
        _serve_mount("bytes=0-", identity="mount:L7CHAIN"))
    assert status == 206
    assert headers.get("content-range") == \
        f"bytes 0-{2 * SEG - 1}/{len(BIG)}"
    assert body == BIG[:2 * SEG]
    # 预读/写穿把覆盖前沿推到第 3 段末（模拟下一窗落盘）
    for idx in (2, 3):
        exp = hc.segment_expected_len(len(BIG), idx)
        assert hc.submit_segment(
            "mount:L7CHAIN", idx, BIG[idx * SEG: idx * SEG + exp],
            len(BIG))
    hc.flush_writes()
    status, headers, body = asyncio.run(
        _serve_mount(f"bytes={2 * SEG}-", identity="mount:L7CHAIN"))
    assert status == 206
    assert headers.get("content-range") == \
        f"bytes {2 * SEG}-{4 * SEG - 1}/{len(BIG)}"
    assert body == BIG[2 * SEG:4 * SEG]
    assert up.count == 0, "链式两跳都必须本地出流、不回源"


def test_mount_play_timing_cache_field(cache_env, monkeypatch, caplog):
    """[play-timing] 汇总行带 cache=hit|partial|miss（L-007 验收
    前提）：三种出口各归其位，partial 附出流字节数。"""
    import logging

    from backend.emby_server.play_timing import PlayTimer

    _seed_segments(PIDENT, BIG, (0, 1, 2), monkeypatch)
    _local_upstream(monkeypatch, BIG)

    async def _serve_pt(range_header, identity, label):
        pt = PlayTimer(label)
        resp = await streaming.serve_mount_relay_async(
            "https://rcd.example/media", _request(range_header),
            {"User-Agent": "t-ua"}, "video/mp4", pt=pt,
            hot_identity=identity)
        async for _c in resp.body_iterator:
            pass
        return resp

    with caplog.at_level(logging.INFO,
                         logger="backend.emby_server.play_timing"):
        asyncio.run(_serve_pt("bytes=0-", PIDENT, "stream t=partial"))
        asyncio.run(_serve_pt("bytes=0-99", PIDENT, "stream t=hit"))
        asyncio.run(_serve_pt("bytes=0-", "mount:L7MISS",
                              "stream t=miss"))
    hc.flush_writes()
    lines = [r.getMessage() for r in caplog.records
             if "[play-timing]" in r.getMessage()]
    partial = [l for l in lines if "t=partial" in l]
    hit = [l for l in lines if "t=hit" in l]
    miss = [l for l in lines if "t=miss" in l]
    assert partial and "cache=partial" in partial[0], lines
    assert f"cache_bytes={3 * SEG}" in partial[0], lines
    assert hit and "cache=hit" in hit[0], lines
    assert miss and "cache=miss" in miss[0], lines
