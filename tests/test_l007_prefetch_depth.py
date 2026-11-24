# -*- coding: utf-8 -*-
"""L-007 二次优化：预读追不上读取前沿（2026-11-25）。

生产证据（2026-11-25 01:15–01:18，#510 部署后）：Rex 冷播 4K
（消费 ≈1.9MB/s）时 stream 请求 miss→partial 交替，miss 的
first_byte 2.0–2.9s；缓存前缀以约 8MB/轮增长——预读前沿推进
≈4MB/s 只略高于消费速率，读取前沿一追上就 miss、干等一次 rcd
开流（ttfb 1.8–2.7s）。近 30 分钟全站 hit+partial 仅约 58%。

根因（代码级）：
1. 窗口内取数串行：一窗多块逐块 await，任一时刻只有一块在途，
   前沿推进速率 = 块大小 / (ttfb + 传输)，8MB 块封顶 ≈4MB/s。
2. 每请求重锚取消旧窗：播放器逐段请求锚点不断前移，新窗把旧窗
   整窗 cancel，在途块字节全废——预读实际产出 ≈ 每轮一块。
3. 窗口 32MB / 块 8MB 太小：4K 码率下窗口十几秒就被消费完。

本文件钉子：
- 缺省参数上调：窗口 128MB、单块 32MB、并发 4、预热 32MB。
- 流水线：同一窗多块同时在途（max_in_flight ≥ 3）。
- 假源模拟（等比缩放模型：单块首字节延迟固定、逐轮请求重锚）：
  旧参数（32MB/8MB/并发 1）miss 成串；新参数（128MB/32MB/
  并发 4）全程 miss ≤ 1（只有最开始那一下冷段）。
- 锚点落在在途窗覆盖范围内不许取消旧窗（字节不许白拉）；真
  跳走（锚点越出窗外）才取消。
- 单块取数失败只丢该块，后续块照常落盘（旧串行口径一失败整
  窗停摆）。
- PlaybackInfo 预热：解析出挂载/Drive 身份与取数定位、只预热
  首段窗口、不抢占在途窗、spawn 不阻塞响应路径。
- 打点：stats 有 prefetch_blocks / prefetch_fetch_ms 可算推进
  速率。

全部离线：fetcher 是内存假源（asyncio.sleep 模拟首字节延迟），
不碰外网、不拉谷歌流。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import time
from types import SimpleNamespace

import pytest

from backend.emby_server import api
from backend.emby_server import relay_hot_cache as hc
from backend.emby_server import streaming
from backend.emby_server.mounts import PlayTarget

SEG = hc.SEGMENT_SIZE


@pytest.fixture()
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_HOT_CACHE_DIR", str(tmp_path / "hc"))
    monkeypatch.setenv("RELAY_HOT_CACHE_MIN_FREE_BYTES", "0")
    monkeypatch.setattr(hc, "max_bytes", lambda: 500 * 1024 * 1024)
    # 每用例独立的预读参数起点（个别用例再覆盖）
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(32 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(8 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "4")
    return tmp_path


def _body(total):
    return (bytes(range(256)) * (total // 256 + 1))[:total]


def _make_fetcher(body, latency=0.0, calls=None, tracker=None,
                  fail_ranges=()):
    """内存假源：按绝对区间回字节；latency 模拟首字节延迟（每次
    取数都付一次，等比缩放生产 rcd_ttfb）。"""
    async def fetcher(s, e):
        if calls is not None:
            calls.append((s, e))
        if tracker is not None:
            tracker["inflight"] += 1
            tracker["max"] = max(tracker["max"], tracker["inflight"])
        try:
            if latency:
                await asyncio.sleep(latency)
            if (s, e) in fail_ranges:
                return None
            return body[s:e + 1]
        finally:
            if tracker is not None:
                tracker["inflight"] -= 1
    return fetcher


# ---------------- 缺省参数 ----------------

def test_prefetch_param_defaults_raised(monkeypatch):
    """新缺省：窗口 128MB / 单块 32MB / 并发 4 / 预热 32MB；
    钳位上限同步放宽（窗 256MB、块 64MB、并发 16）。"""
    for name in ("RELAY_HOT_CACHE_PREFETCH_BYTES",
                 "RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                 "RELAY_HOT_CACHE_PREFETCH_CONCURRENCY",
                 "RELAY_HOT_CACHE_WARMUP_BYTES"):
        monkeypatch.delenv(name, raising=False)
    assert hc.prefetch_bytes() == 128 * SEG
    assert hc.prefetch_fetch_bytes() == 32 * SEG
    assert hc.prefetch_concurrency() == 4
    assert hc.warmup_bytes() == 32 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(10 ** 12))
    assert hc.prefetch_bytes() == 256 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(10 ** 12))
    assert hc.prefetch_fetch_bytes() == 64 * SEG
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "99")
    assert hc.prefetch_concurrency() == 16
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "0")
    assert hc.prefetch_concurrency() == 1
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "abc")
    assert hc.prefetch_concurrency() == 4
    monkeypatch.setenv("RELAY_HOT_CACHE_WARMUP_BYTES", "0")
    assert hc.warmup_bytes() == 0


# ---------------- 流水线在途深度 ----------------

def test_prefetch_pipeline_keeps_blocks_in_flight(cache_env,
                                                  monkeypatch):
    """一窗多块同时在途（旧串行口径任一时刻只有一块在飞 →
    max_in_flight 恒 1，前沿推进被 ttfb 串行封顶）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(16 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    ident = "drive:D9:PIPE"
    total = 16 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    tracker = {"inflight": 0, "max": 0}
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-", _make_fetcher(body, latency=0.05,
                                         tracker=tracker)))
    hc.flush_writes()
    assert tracker["max"] >= 3, tracker
    meta = hc.get_meta(ident)
    for idx in range(16):
        assert hc.segment_present(ident, meta, idx), idx


# ---------------- 假源模拟：旧参数 miss 成串 / 新参数不 miss ----------------

async def _wait_present(ident, meta, idx, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if hc.segment_present(ident, meta, idx):
            return True
        await asyncio.sleep(0.01)
    return hc.segment_present(ident, meta, idx)


async def _simulate_player(ident, body, fetcher):
    """Rex 式逐段消费模拟：每轮在读位置调度一次预读（与 serve
    路径同口径）；读位置所在段不在盘 → 记一次 miss 并按需直取
    一块落盘（模拟中继透传+写穿）；然后消费前进 4 段。

    节奏（等比缩放）：单块取数延迟 0.15s、每轮 0.03s 消费 4MB
    ——消费 ≈133MB/s、旧参数串行推进 = 8MB/0.15s ≈ 53MB/s，
    与生产的「消费 1.9MB/s、推进 ≈4MB/s、ttfb 2s」同形。"""
    total = len(body)
    assert hc.note_validators(ident, total, etag='"sim"')
    meta = hc.get_meta(ident)
    misses = 0
    pos = 0
    while pos < total:
        streaming._schedule_hot_prefetch(
            ident, f"bytes={pos}-", fetcher)
        idx = pos // SEG
        if not hc.segment_present(ident, meta, idx):
            misses += 1
            block = hc.prefetch_fetch_bytes()
            bstart = idx * SEG
            bend = min(bstart + block - 1, total - 1)
            data = await fetcher(bstart, bend)
            assert data and len(data) == bend - bstart + 1
            off = bstart
            while off <= bend:
                i2 = off // SEG
                exp = hc.segment_expected_len(total, i2)
                hc.submit_segment(
                    ident, i2,
                    data[off - bstart: off - bstart + exp], total)
                off += exp
            await _wait_present(ident, meta, idx)
        await asyncio.sleep(0.03)
        pos += 4 * SEG
    return misses


def test_simulation_old_params_miss_new_params_dont(cache_env,
                                                    monkeypatch):
    """先红后绿的总钉子：旧参数 miss 成串；新参数全程 ≤1 次
    （只有最开始那一下冷段——stream 到达前无从预热的那一段）。"""
    total = 96 * SEG
    body = _body(total)

    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(32 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(8 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "1")
    old_misses = asyncio.run(_simulate_player(
        "mount:SIMOLD", body, _make_fetcher(body, latency=0.15)))
    assert old_misses >= 3, old_misses

    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(128 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(32 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY", "4")
    new_misses = asyncio.run(_simulate_player(
        "mount:SIMNEW", body, _make_fetcher(body, latency=0.15)))
    assert new_misses <= 1, new_misses


# ---------------- 重锚不乱杀在途窗 ----------------

def test_reanchor_inside_running_window_keeps_it(cache_env,
                                                 monkeypatch):
    """播放中逐段请求锚点前移、落在在途窗覆盖内 → 不许取消旧窗
    （旧口径每请求整窗 cancel，在途块全废、每轮重付 ttfb）。
    全窗每块只许取一次。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(16 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    ident = "drive:D9:KEEPWIN"
    total = 32 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []
    fetcher = _make_fetcher(body, latency=0.1, calls=calls)

    async def scenario():
        t1 = asyncio.create_task(streaming._hot_prefetch_run(
            ident, "bytes=0-", fetcher))
        await asyncio.sleep(0.03)  # 窗已在途
        # 锚点前移到窗内第 4 段：不许取消 t1、不许另起重复窗
        await streaming._hot_prefetch_run(
            ident, f"bytes={4 * SEG}-", fetcher)
        await t1

    asyncio.run(scenario())
    hc.flush_writes()
    assert sorted(calls) == [(i * 2 * SEG, (i + 1) * 2 * SEG - 1)
                             for i in range(8)], calls
    meta = hc.get_meta(ident)
    for idx in range(16):
        assert hc.segment_present(ident, meta, idx), idx


def test_seek_beyond_window_cancels_and_takes_over(cache_env,
                                                   monkeypatch):
    """真跳走（锚点越出在途窗覆盖）→ 旧窗取消、新窗在新锚点接管
    （拖动语义保留，不许被「不取消」优化误伤）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(8 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    ident = "drive:D9:SEEK"
    total = 64 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []
    fetcher = _make_fetcher(body, latency=0.1, calls=calls)

    async def scenario():
        t1 = asyncio.create_task(streaming._hot_prefetch_run(
            ident, "bytes=0-", fetcher))
        await asyncio.sleep(0.03)
        await streaming._hot_prefetch_run(
            ident, f"bytes={40 * SEG}-", fetcher)
        assert t1.cancelled() or t1.done()

    asyncio.run(scenario())
    hc.flush_writes()
    # 新窗的块取到了（40 段起 8MB 窗 = 4 块）
    assert (40 * SEG, 42 * SEG - 1) in calls
    meta = hc.get_meta(ident)
    for idx in range(40, 48):
        assert hc.segment_present(ident, meta, idx), idx


# ---------------- 单块失败不拖垮整窗 ----------------

def test_prefetch_block_failure_keeps_other_blocks(cache_env,
                                                   monkeypatch):
    """中间一块取数失败 → 只丢该块，前后块照常落盘（旧串行口径
    一失败 break 整窗，后面的块永远取不到）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(6 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    ident = "drive:D9:FAILBLK"
    total = 6 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    fetcher = _make_fetcher(body, fail_ranges={(2 * SEG, 4 * SEG - 1)})
    asyncio.run(streaming._hot_prefetch_run(ident, "bytes=0-", fetcher))
    hc.flush_writes()
    meta = hc.get_meta(ident)
    for idx in (0, 1, 4, 5):
        assert hc.segment_present(ident, meta, idx), idx
    for idx in (2, 3):
        assert not hc.segment_present(ident, meta, idx), idx


# ---------------- 打点 ----------------

def test_prefetch_stats_have_block_timing(cache_env, monkeypatch):
    """验收口径：stats 必须能算预读推进速率——每块记
    prefetch_blocks 与 prefetch_fetch_ms（旧 stats 只有段数与
    字节数，算不出每块拉取耗时）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(4 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    ident = "drive:D9:STATS"
    total = 4 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    before = hc.stats()
    asyncio.run(streaming._hot_prefetch_run(
        ident, "bytes=0-", _make_fetcher(body, latency=0.02)))
    hc.flush_writes()
    after = hc.stats()
    assert after["prefetch_blocks"] - before.get("prefetch_blocks", 0) == 2
    assert after["prefetch_fetch_ms"] - before.get(
        "prefetch_fetch_ms", 0) >= 20


# ---------------- PlaybackInfo 预热 ----------------

async def _run_warmup_once(ident, body, calls):
    fetcher = _make_fetcher(body, calls=calls)
    streaming.schedule_warmup_prefetch(ident, fetcher,
                                       total_size_hint=len(body))
    # 调度是后台任务：等它跑完
    for _ in range(100):
        tasks = [t for t in streaming._HOT_PREFETCH_TASKS
                 if not t.done()]
        if not tasks:
            break
        await asyncio.sleep(0.02)


def test_warmup_only_fetches_head_window(cache_env, monkeypatch):
    """预热只取首段窗口（缺省 32MB；本例钳到 4MB 验证边界）：
    窗外分段不许取；无 meta 时按条目 size 登记。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_WARMUP_BYTES", str(4 * SEG))
    ident = "mount:WARMFID"
    total = 10 * SEG
    body = _body(total)
    calls = []
    asyncio.run(_run_warmup_once(ident, body, calls))
    hc.flush_writes()
    meta = hc.get_meta(ident)
    assert meta and meta["total_size"] == total
    for idx in range(4):
        assert hc.segment_present(ident, meta, idx), idx
    for idx in range(4, 10):
        assert not hc.segment_present(ident, meta, idx), idx
    assert calls == [(0, 4 * SEG - 1)], calls


def test_warmup_does_not_take_over_running_window(cache_env,
                                                  monkeypatch):
    """预热不抢占：已有在途窗时预热直接让路（不取消、不重复
    取数）——PlaybackInfo 在播放中被重发也不许打断正在推进的
    预读。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_BYTES", str(16 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                       str(2 * SEG))
    monkeypatch.setenv("RELAY_HOT_CACHE_WARMUP_BYTES", str(4 * SEG))
    ident = "mount:WARMKEEP"
    total = 32 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"v1"')
    calls = []
    fetcher = _make_fetcher(body, latency=0.1, calls=calls)

    async def scenario():
        t1 = asyncio.create_task(streaming._hot_prefetch_run(
            ident, f"bytes={8 * SEG}-", fetcher))
        await asyncio.sleep(0.03)  # 锚点 8 的窗在途
        streaming.schedule_warmup_prefetch(ident, fetcher)
        await asyncio.sleep(0.05)
        assert not t1.cancelled() and not t1.done()
        await t1

    asyncio.run(scenario())
    hc.flush_writes()
    # 在途窗的块（8 段起、16MB 窗 = 8 块）取齐且只取一次；
    # 预热没有另拉 0 段起的块
    assert (0, 2 * SEG - 1) not in calls
    assert sorted(calls) == [(i * 2 * SEG, (i + 1) * 2 * SEG - 1)
                             for i in range(4, 12)], calls


def test_warmup_hint_does_not_clobber_existing_meta(cache_env,
                                                    monkeypatch):
    """已有 meta（真实流登记过）时，预热的 size 提示不许改写
    总长（DB size 可能是旧值，改写会整身份失效）。"""
    monkeypatch.setenv("RELAY_HOT_CACHE_WARMUP_BYTES", str(2 * SEG))
    ident = "mount:WARMKEEP2"
    total = 10 * SEG
    body = _body(total)
    assert hc.note_validators(ident, total, etag='"real"')
    asyncio.run(_run_warmup_once(ident, body, []))
    meta = hc.get_meta(ident)
    assert meta["total_size"] == total
    assert meta.get("etag") == '"real"'


# ---------------- api 接线：预热目标解析 ----------------

def _warm_item(**kw):
    base = {"id": 7, "file_path": "mount://3/Movies/a.mkv",
            "drive_file_id": "FID9", "size": 123456, "library": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_playback_warmup_spec_mount_and_drive(monkeypatch):
    item = _warm_item()
    monkeypatch.setattr(
        api.mount_lib, "resolve_play_target",
        lambda path, db, lib: PlayTarget(
            "url", "https://rcd.example/a", {"User-Agent": "t"}))
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: None)
    specs = api._playback_warmup_spec(None, item)
    assert specs == [("mount:FID9", "mount",
                      ("https://rcd.example/a", {"User-Agent": "t"}),
                      123456)]
    # drive 型挂载：两条身份都预热（实际走哪条腿由取流路由定，
    # 预热有界、宁可都喂）
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: "DRV1")
    specs = api._playback_warmup_spec(None, item)
    got = {(s[0], s[1]) for s in specs}
    assert ("mount:FID9", "mount") in got
    assert ("drive:DRV1:FID9", "drive") in got
    # 无 drive_file_id / 非挂载路径 → 不预热
    assert api._playback_warmup_spec(
        None, _warm_item(drive_file_id=None)) is None
    assert api._playback_warmup_spec(
        None, _warm_item(file_path="/local/a.mkv")) is None
    # 解析失败（挂载不可用）→ 整体让路、不抛
    def boom(path, db, lib):
        raise RuntimeError("mount down")
    monkeypatch.setattr(api.mount_lib, "resolve_play_target", boom)
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db, it: None)
    assert api._playback_warmup_spec(None, item) is None


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row

    def query(self, *a):
        return _FakeQuery(self._row)

    def close(self):
        pass


def test_spawn_playback_warmup_nonblocking_and_cooldown(monkeypatch):
    """spawn 必须立即返回（解析在后台线程里睡 1.5s 也不许挡
    PlaybackInfo 响应）；冷却期内重复 spawn 不再重复解析。"""
    item = _warm_item(id=987001)
    spec_calls = []

    def slow_spec(db, it):
        spec_calls.append(1)
        time.sleep(1.5)
        return [("mount:FID9", "mount",
                 ("https://rcd.example/a", {"User-Agent": "t"}), 100)]

    scheduled = []
    monkeypatch.setattr(api, "_playback_warmup_spec", slow_spec)
    monkeypatch.setattr(api, "SessionLocal", lambda: _FakeDB(item))
    monkeypatch.setattr(
        streaming, "schedule_mount_warmup",
        lambda identity, url, headers, hint=None: scheduled.append(
            (identity, url)))

    async def scenario():
        t0 = time.monotonic()
        api._spawn_playback_warmup(item)
        assert time.monotonic() - t0 < 0.5, "spawn 不许阻塞响应路径"
        api._spawn_playback_warmup(item)  # 冷却内：不再起线程解析
        await asyncio.sleep(2.2)
        assert scheduled == [("mount:FID9", "https://rcd.example/a")]

    asyncio.run(scenario())
    assert len(spec_calls) == 1
