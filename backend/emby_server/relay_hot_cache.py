# -*- coding: utf-8 -*-
"""中继热缓存（2026-11-22 智能中继＋热缓存阶段 1）。

Drive 中继腿（SA / 池副本 / 本人令牌）与挂载中继腿的字节级磁盘
分段缓存：

- 分段 = 1MB（与 streaming.READ_CHUNK 对齐），键 = 文件身份 + 分段
  序号；身份由 streaming 注入——Drive 腿经 _DriveLegScope
  （``drive:{drive_id}:{file_id}``）、挂载腿经 serve_mount_relay_async
  的 hot_identity 参数（``mount:{drive_file_id}``），本模块不认识
  URL 与令牌，只管字节落盘与命中判定。
- 命中口径（streaming.serve_mount_relay_async 开流前查）：请求区间
  被已缓存分段**完全覆盖**才整段本地出字节、不回源；未全覆盖走
  现行透传 + 写穿（write-through）把流过的完整分段落盘。L-007
  （2026-11-24）起另有部分覆盖出流：带 Range 的请求自起始段起有
  ≥1 段连续覆盖前缀时，先本地出流这一截（短 206、Content-Range
  钳到覆盖末尾），播放器对剩余区间重发时覆盖已随写穿/预读前移；
  后缀 Range 不走部分出流。开关 RELAY_HOT_CACHE_PARTIAL_ENABLED，
  关即回全覆盖口径。
- 写入走进程内有界队列 + 守护写线程（请求路径只投递、满了就丢，
  绝不阻塞出流），落盘临时文件 + 原子 rename；总容量走
  SystemConfig ``relay_hot_cache_max_bytes``（默认 20GiB，注册见
  config_schema.RELAY_HOT_CACHE_CONFIG_SCHEMA），超限按 mtime
  LRU 淘汰；磁盘余量护栏：可用空间低于 RELAY_HOT_CACHE_MIN_FREE_
  BYTES（默认 10GiB）即停写新分段、出流照常透传（fail-open），
  绝不把盘写满拖累主服务；已缓存分段的命中读不受护栏影响。
- 身份的大小/校验器（ETag / Last-Modified）变化即整身份失效
  （note_validators 比对；代际计数让在途的旧写入自动作废）。
- 本模块公开函数永不抛：缓存故障一律按未命中/丢弃处理，
  streaming 侧 fail-open 回现行透传路径。

挂载腿二期（2026-11-23 已接）：一期时挂载身份只有路径/pickcode
——rcd 路径可被原地换文件（同路径不同字节），身份与内容未绑定，
故一期不接。二期改用条目绑定的谷歌 drive_file_id 作身份
（``mount:{drive_file_id}``，api.video_stream 注入）：file_id 是
谷歌侧不可变身份、与内容绑定，不是路径；且每次透传仍按上游响应
登记总长＋Last-Modified 校验器把关，变化即整身份失效。无
drive_file_id 的挂载条目不设身份、不缓存，行为逐字不变。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: 分段大小：与 streaming.READ_CHUNK（1MB）对齐——中继流过的块攒满
#: 一段即落盘，不额外切分。
SEGMENT_SIZE = 1024 * 1024

#: 总容量配置键（SystemConfig；纯 SystemConfig 键不进 env.example，
#: #1527 先例）。
KEY_MAX_BYTES = "relay_hot_cache_max_bytes"

#: 总容量代码默认：20GiB（2026-11-23 用户亲令收紧：根盘总共
#: 435G、海报回填还要占约 55G，50G 缺省太贪）。两条腿共享同一个
#: 缓存池/同一个总上限（分开计账护不住同一块盘）；管理员改
#: SystemConfig 热生效，60 秒进程内缓存。
DEFAULT_MAX_BYTES = 20 * 1024 * 1024 * 1024

_MAX_BYTES_LIMIT = 10 ** 15
_CONFIG_TTL_SEC = 60.0

_DEFAULT_DIR = "/data/relay-hot-cache"
#: 磁盘余量护栏缺省阈值：可用空间低于 10GiB 停写新分段（出流透传
#: 不受影响）。env RELAY_HOT_CACHE_MIN_FREE_BYTES 可调。
_DEFAULT_MIN_FREE_BYTES = 10 * 1024 * 1024 * 1024
#: 跨请求预读窗口缺省（字节）：L-007 起预读以请求起始段为锚、从
#: 覆盖前沿起按字节窗口推进（旧 RELAY_HOT_CACHE_PREFETCH_SEGMENTS
#: 分段口径已废止——它以区间末尾为锚，开尾 Range 下恒为空转）。
#: L-007 二次优化（2026-11-25）窗口 32MB → 128MB：32MB 在 4K 码率
#: （≈1.9MB/s）下十几秒就被消费完、窗一空就成串 miss；128MB 约
#: 顶 60 秒以上消费，给预读流水线留出追赶余量。窗口按「距读取
#: 前沿的字节距离」维护（见 streaming._hot_prefetch_run）。
_DEFAULT_PREFETCH_BYTES = 128 * 1024 * 1024
_PREFETCH_BYTES_LIMIT = 256 * 1024 * 1024
#: 预读单次取数粒度缺省（字节）：一次开流取回一整块再本地切段，
#: 摊薄谷歌开流成本（旧口径逐段一开流，每 1MB 重付一次 TTFB）。
#: 二次优化 8MB → 32MB：串行/少并发时前沿推进速率 ≈ 块/(ttfb+
#: 传输)，8MB 块在 ttfb≈2s 下封顶 ≈4MB/s、只略高于 4K 消费速率，
#: 一有抖动就被追上；32MB 块把同样的 ttfb 摊到 4 倍字节上。
_DEFAULT_PREFETCH_FETCH_BYTES = 32 * 1024 * 1024
_PREFETCH_FETCH_BYTES_LIMIT = 64 * 1024 * 1024
#: 预读流水线并发缺省：一窗内同时在途的取数块数。ttfb 与块大小
#: 无关，只能靠多块在途掩盖——并发 N 时推进速率 ≈ N×块/(ttfb+
#: 传输)。信号量是全进程共享的（streaming._hot_prefetch_sem），
#: 在途字节上限 = 并发 × 单块，全进程有界。
_DEFAULT_PREFETCH_CONCURRENCY = 4
_PREFETCH_CONCURRENCY_LIMIT = 16
#: PlaybackInfo 预热窗口缺省（字节）：stream 请求到达前先把条目
#: 首段喂进缓存，冷起播的第一下 miss 也尽量消掉。只预热首段
#: 窗口（锚点 0），不抢占在途窗；0 = 关预热。
_DEFAULT_WARMUP_BYTES = 32 * 1024 * 1024
_WARMUP_BYTES_LIMIT = 128 * 1024 * 1024

_WRITER_QUEUE_MAX = 128


def _env_flag(name: str, default: bool) -> bool:
    """读布尔型环境变量；非法值记 warning 并回落默认值。"""
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    logger.warning("环境变量 %s=%r 不是合法布尔值，使用默认值 %s",
                   name, raw, default)
    return default


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """读整型环境变量；非法回默认、合法钳 [lo, hi]。"""
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 %s=%r 不是整数，使用默认值 %d",
                       name, raw, default)
        return default
    return min(max(value, lo), hi)


def enabled() -> bool:
    """总开关：env RELAY_HOT_CACHE_ENABLED，默认开。"""
    return _env_flag("RELAY_HOT_CACHE_ENABLED", True)


def cache_dir() -> str:
    """缓存落盘根目录：env RELAY_HOT_CACHE_DIR，默认 /data 下。"""
    return os.getenv("RELAY_HOT_CACHE_DIR", "").strip() or _DEFAULT_DIR


def min_free_bytes() -> int:
    """停写阈值：磁盘剩余低于此值不再写新分段（已缓存照常读）。"""
    return _env_int("RELAY_HOT_CACHE_MIN_FREE_BYTES",
                    _DEFAULT_MIN_FREE_BYTES, 0, _MAX_BYTES_LIMIT)


def partial_enabled() -> bool:
    """部分覆盖出流开关：env RELAY_HOT_CACHE_PARTIAL_ENABLED，默认
    开；关 → 回 L-007 前的全覆盖口径（未全覆盖即透传）。"""
    return _env_flag("RELAY_HOT_CACHE_PARTIAL_ENABLED", True)


def prefetch_bytes() -> int:
    """跨请求预读窗口（字节）：env RELAY_HOT_CACHE_PREFETCH_BYTES，
    默认 128MB，钳 [0, 256MB]，0 = 关预读。"""
    return _env_int("RELAY_HOT_CACHE_PREFETCH_BYTES",
                    _DEFAULT_PREFETCH_BYTES, 0, _PREFETCH_BYTES_LIMIT)


def prefetch_fetch_bytes() -> int:
    """预读单次取数粒度（字节）：env RELAY_HOT_CACHE_PREFETCH_
    FETCH_BYTES，默认 32MB，钳 [1 段, 64MB] 并向下对齐到整段——
    一次开流取回一整块、本地按 1MB 切段提交，块界不许切开分段。"""
    value = _env_int("RELAY_HOT_CACHE_PREFETCH_FETCH_BYTES",
                     _DEFAULT_PREFETCH_FETCH_BYTES,
                     SEGMENT_SIZE, _PREFETCH_FETCH_BYTES_LIMIT)
    return value - value % SEGMENT_SIZE


def prefetch_concurrency() -> int:
    """预读流水线并发：env RELAY_HOT_CACHE_PREFETCH_CONCURRENCY，
    默认 4，钳 [1, 16]。streaming 的预读信号量按进程内首次读取
    建好后固定（改 env 需重启进程生效）；窗口/粒度则每次取数现读。"""
    return _env_int("RELAY_HOT_CACHE_PREFETCH_CONCURRENCY",
                    _DEFAULT_PREFETCH_CONCURRENCY, 1,
                    _PREFETCH_CONCURRENCY_LIMIT)


def warmup_bytes() -> int:
    """PlaybackInfo 预热窗口（字节）：env RELAY_HOT_CACHE_WARMUP_
    BYTES，默认 32MB，钳 [0, 128MB] 并向下对齐到整段，0 = 关预热。
    预热只锚条目首段（字节 0 起），有界、失败放行、不抢占在途窗。"""
    value = _env_int("RELAY_HOT_CACHE_WARMUP_BYTES",
                     _DEFAULT_WARMUP_BYTES, 0, _WARMUP_BYTES_LIMIT)
    return value - value % SEGMENT_SIZE


# ---------------------------------------------------------------------------
# 总容量（SystemConfig 热读，同 image_queue 的 60 秒进程内缓存口径）
# ---------------------------------------------------------------------------
_CAP_LOCK = threading.Lock()
_CAP_CACHE: Optional[int] = None
_CAP_CACHE_AT = 0.0


def max_bytes() -> int:
    """总容量上限（字节）：SystemConfig relay_hot_cache_max_bytes
    热读；无行/非法/读失败回代码默认 20GiB；0 = 关缓存（不读不写）。"""
    global _CAP_CACHE, _CAP_CACHE_AT
    now = time.monotonic()
    with _CAP_LOCK:
        if _CAP_CACHE is not None and now - _CAP_CACHE_AT < _CONFIG_TTL_SEC:
            return _CAP_CACHE
    value = DEFAULT_MAX_BYTES
    try:
        from backend.database import SessionLocal
        from backend.integrations import store as _store
        db = SessionLocal()
        try:
            vals = _store.read_values(
                db, [KEY_MAX_BYTES], {KEY_MAX_BYTES: str(DEFAULT_MAX_BYTES)})
            value = int(vals.get(KEY_MAX_BYTES) or DEFAULT_MAX_BYTES)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 读不到配置用默认，绝不挡播放
        value = DEFAULT_MAX_BYTES
    value = min(max(value, 0), _MAX_BYTES_LIMIT)
    with _CAP_LOCK:
        _CAP_CACHE = value
        _CAP_CACHE_AT = now
    return value


# ---------------------------------------------------------------------------
# 统计（进程内计数：命中/落盘/淘汰，运维与验收口径）
# ---------------------------------------------------------------------------
_STATS_LOCK = threading.Lock()
_STATS = {
    "hit_requests": 0,
    "hit_bytes": 0,
    "miss_requests": 0,
    "segments_written": 0,
    "segments_dropped": 0,
    "segments_evicted": 0,
    "prefetch_fetched": 0,
    "prefetch_bytes": 0,
    "prefetch_blocks": 0,
    "prefetch_fetch_ms": 0,
    "partial_requests": 0,
    "partial_bytes": 0,
}


def _bump(name: str, delta: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] = _STATS.get(name, 0) + delta


def note_hit(nbytes: int) -> None:
    _bump("hit_requests")
    _bump("hit_bytes", int(nbytes))


def note_miss() -> None:
    _bump("miss_requests")


def note_prefetch_fetched() -> None:
    _bump("prefetch_fetched")


def note_prefetch_block(fetch_ms: int) -> None:
    """预读块级打点（L-007 二次优化）：每完成一次整块取数记一块
    数与该块拉取耗时——验收时推进速率 = prefetch_bytes /
    (prefetch_fetch_ms / prefetch_blocks 的块均耗时推算)，与消费
    速率直接对账；旧 stats 只有段数/字节数，算不出块耗时。"""
    _bump("prefetch_blocks")
    _bump("prefetch_fetch_ms", int(fetch_ms))


def note_prefetch_bytes(nbytes: int) -> None:
    """预读实际落盘字节（投机拉取的成本口径，验收期与命中收益
    对账：浪费比失控就调小窗口）。"""
    _bump("prefetch_bytes", int(nbytes))


def note_partial(nbytes: int) -> None:
    """部分覆盖出流计数（与全命中分开记，验收口径 cache=partial）。"""
    _bump("partial_requests")
    _bump("partial_bytes", int(nbytes))


def stats() -> dict:
    with _STATS_LOCK:
        return dict(_STATS)


# ---------------------------------------------------------------------------
# 身份代际（失效时 +1，在途旧写入在写线程里按代际作废）
# ---------------------------------------------------------------------------
_GEN_LOCK = threading.Lock()
_GEN: dict = {}


def _generation(identity: str) -> int:
    with _GEN_LOCK:
        return _GEN.get(identity, 0)


def current_generation(identity: str) -> int:
    """当前代际（写穿计划/预读在开始时捕获，提交时回验：期间身份
    被失效过 → 字节属于旧校验器，submit_segment 按代际丢弃）。"""
    return _generation(identity)


def _bump_generation(identity: str) -> int:
    with _GEN_LOCK:
        _GEN[identity] = _GEN.get(identity, 0) + 1
        return _GEN[identity]


def _segment_write_current(identity: str, gen: int) -> bool:
    return _generation(identity) == gen


# ---------------------------------------------------------------------------
# 落盘布局与元数据
# ---------------------------------------------------------------------------
def _identity_dir(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir(), digest)


def _meta_path(identity: str) -> str:
    return os.path.join(_identity_dir(identity), "meta.json")


def _segment_path(identity: str, idx: int) -> str:
    return os.path.join(_identity_dir(identity), f"seg-{idx:08d}.bin")


def get_meta(identity: str) -> Optional[dict]:
    """读身份元数据；不存在/损坏/不合规一律 None。永不抛。"""
    if not identity:
        return None
    try:
        with open(_meta_path(identity), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except Exception:  # noqa: BLE001 — 读不到按无缓存
        return None
    if not isinstance(meta, dict):
        return None
    try:
        total = int(meta.get("total_size") or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    meta["total_size"] = total
    return meta


def _write_meta(identity: str, meta: dict) -> None:
    d = _identity_dir(identity)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(
        d, f".meta-{os.getpid()}-{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    os.replace(tmp, _meta_path(identity))


def note_validators(identity: str, total_size: int, etag=None,
                    last_modified=None, content_type=None) -> Optional[dict]:
    """登记/校验文件校验器（写穿路径在上游响应头到达时调用）。

    与已存元数据比对：总大小或校验器（双方都有值时）变了 → 整身份
    失效（删全部分段）后按新值重建元数据；一致则原样返回已存的。
    缓存未启用/容量为 0/落盘失败一律 None（＝本次不缓存）。永不抛。
    """
    if not identity or not total_size or int(total_size) <= 0:
        return None
    if not enabled() or max_bytes() <= 0:
        return None
    try:
        existing = get_meta(identity)
        if existing is not None:
            same = int(existing["total_size"]) == int(total_size)
            if same and etag and existing.get("etag") \
                    and etag != existing.get("etag"):
                same = False
            if same and last_modified and existing.get("last_modified") \
                    and last_modified != existing.get("last_modified"):
                same = False
            if same:
                return existing
            invalidate(identity)
        meta = {
            "identity": identity,
            "total_size": int(total_size),
            "etag": etag or None,
            "last_modified": last_modified or None,
            "content_type": content_type or None,
            "updated_at": time.time(),
        }
        _write_meta(identity, meta)
        return meta
    except Exception:  # noqa: BLE001 — 登记失败本次不缓存
        logger.warning("relay_hot_cache 元数据登记失败 identity=%s",
                       identity, exc_info=True)
        return None


def invalidate(identity: str) -> None:
    """整身份失效：代际 +1（在途旧写入自动作废）＋删整个身份目录。"""
    if not identity:
        return
    _bump_generation(identity)
    try:
        shutil.rmtree(_identity_dir(identity), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 分段读（命中判定与出字节）
# ---------------------------------------------------------------------------
def segment_expected_len(total_size: int, idx: int) -> int:
    """分段 idx 的应有长度：整段 SEGMENT_SIZE，尾段为剩余字节。"""
    start = idx * SEGMENT_SIZE
    if start >= total_size:
        return 0
    return min(SEGMENT_SIZE, total_size - start)


def segment_present(identity: str, meta: dict, idx: int) -> bool:
    """分段在盘且长度精确（长度不符按损坏/未缓存处理）。"""
    try:
        exp = segment_expected_len(int(meta["total_size"]), idx)
        if exp <= 0:
            return False
        return os.path.getsize(_segment_path(identity, idx)) == exp
    except OSError:
        return False


def open_segments(identity: str, meta: dict, start: int, end: int):
    """整段命中校验＋打开：[start, end] 覆盖的每个分段都必须在盘且
    长度精确。全部打开成功返回 [(fileobj, length), ...]（fileobj 已
    seek 到段内起点，调用方负责关）；任一缺失/不符 → 关掉已开的、
    返回 None。先打开再出流：淘汰 unlink 已打开的文件不影响读
    （inode 还在）。命中顺手刷新 mtime（LRU 依据）。"""
    total = int(meta["total_size"])
    first = start // SEGMENT_SIZE
    last = end // SEGMENT_SIZE
    opened = []
    try:
        for idx in range(first, last + 1):
            seg_start = idx * SEGMENT_SIZE
            exp = segment_expected_len(total, idx)
            path = _segment_path(identity, idx)
            if os.path.getsize(path) != exp:
                raise OSError("segment size mismatch")
            fh = open(path, "rb")
            in_off = max(0, start - seg_start)
            take = min(seg_start + exp - 1, end) - (seg_start + in_off) + 1
            if take <= 0:
                fh.close()
                continue
            fh.seek(in_off)
            opened.append((fh, take))
            try:
                os.utime(path, None)
            except OSError:
                pass
        return opened or None
    except OSError:
        for fh, _ in opened:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
        return None


def range_is_suffix(range_header) -> bool:
    """Range 头是否为后缀形态（``bytes=-N``）：起始点由文件尾
    倒推，L-007 部分出流明示排除（回现行透传/全覆盖口径）。"""
    if not range_header:
        return False
    spec = str(range_header).strip()
    if not spec.lower().startswith("bytes="):
        return False
    spec = spec[len("bytes="):].strip()
    if "," in spec or spec.count("-") != 1:
        return False
    a, _, b = spec.partition("-")
    return not a.strip() and bool(b.strip())


def open_covered_prefix(identity, meta, start, end):
    """部分覆盖出流（L-007）：[start, end] 自起始段起的最长连续
    覆盖前缀。≥1 个完整分段连续在盘 → 打开并返回
    ``(opened, covered_end)``（opened 形态同 open_segments，
    covered_end 为实际可出流的末尾字节、≤ end，调用方据此钳制
    Content-Range）；起始段缺失/长度不符 → None。校验口径与
    open_segments 一致：长度精确、先打开再出流（淘汰 unlink 不
    影响已打开句柄）、命中刷新 mtime。永不抛（OSError 收敛为
    None，调用方 fail-open 回透传）。"""
    total = int(meta["total_size"])
    first = start // SEGMENT_SIZE
    last = end // SEGMENT_SIZE
    opened = []
    covered_end = None
    try:
        for idx in range(first, last + 1):
            seg_start = idx * SEGMENT_SIZE
            exp = segment_expected_len(total, idx)
            path = _segment_path(identity, idx)
            try:
                size_ok = os.path.getsize(path) == exp
            except OSError:
                size_ok = False
            if not size_ok:
                if idx == first:
                    return None
                break
            fh = open(path, "rb")
            in_off = max(0, start - seg_start)
            take = min(seg_start + exp - 1, end) \
                - (seg_start + in_off) + 1
            if take <= 0:
                fh.close()
                continue
            fh.seek(in_off)
            opened.append((fh, take))
            covered_end = seg_start + in_off + take - 1
            try:
                os.utime(path, None)
            except OSError:
                pass
        if not opened or covered_end is None or covered_end < start:
            for fh, _ in opened:
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass
            return None
        return opened, covered_end
    except OSError:
        for fh, _ in opened:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
        return None


def covered_prefix_len(identity, meta, start: int, end: int,
                       max_scan: int = 512) -> int:
    """从 start 起连续覆盖的字节数（只 stat 长度、不打开文件）：
    供 miss 路径打点报「前沿差」——读取前沿距已缓存前沿还差多少
    字节。扫描上限 max_scan 段防大区间逐段 stat 拖慢热路径。
    永不抛（异常回 0）。"""
    try:
        total = int(meta["total_size"])
        first = start // SEGMENT_SIZE
        last = min(end // SEGMENT_SIZE, first + max_scan - 1)
        covered = 0
        for idx in range(first, last + 1):
            if not segment_present(identity, meta, idx):
                break
            seg_start = idx * SEGMENT_SIZE
            seg_end = seg_start + segment_expected_len(total, idx) - 1
            covered = min(seg_end, end) - start + 1
        return max(0, covered)
    except Exception:  # noqa: BLE001 — 打点取数永不抛
        return 0


def parse_range(range_header, total_size: int):
    """把客户端 Range 头解析成绝对区间 (start, end)（含尾）。

    只认单段 bytes 区间（播放器口径）；多段/畸形/越界一律 None
    （调用方回现行透传，让上游按原口径处置，含 416）。无 Range
    头 = 整文件。total_size 来自缓存元数据。
    """
    if not total_size or total_size <= 0:
        return None
    if not range_header:
        return (0, total_size - 1)
    spec = str(range_header).strip()
    if not spec.lower().startswith("bytes="):
        return None
    spec = spec[len("bytes="):].strip()
    if "," in spec or spec.count("-") != 1:
        return None
    a, b = (part.strip() for part in spec.split("-"))
    try:
        if not a and not b:
            return None
        if not a:
            n = int(b)
            if n <= 0:
                return None
            return (max(0, total_size - n), total_size - 1)
        start = int(a)
        end = int(b) if b else total_size - 1
    except ValueError:
        return None
    if start < 0 or start >= total_size:
        return None
    end = min(end, total_size - 1)
    if end < start:
        return None
    return (start, end)


# ---------------------------------------------------------------------------
# 分段写（有界队列 + 守护写线程；请求路径只投递，绝不阻塞出流）
# ---------------------------------------------------------------------------
_WRITE_QUEUE: "queue.Queue" = queue.Queue(maxsize=_WRITER_QUEUE_MAX)
_WRITER_STARTED = False
_WRITER_LOCK = threading.Lock()

_TOTAL_LOCK = threading.Lock()
_TOTAL_STATE = {"dir": None, "bytes": None}


def _ensure_writer() -> None:
    global _WRITER_STARTED
    if _WRITER_STARTED:
        return
    with _WRITER_LOCK:
        if _WRITER_STARTED:
            return
        t = threading.Thread(target=_writer_loop,
                             name="relay-hot-cache-writer", daemon=True)
        t.start()
        _WRITER_STARTED = True


def submit_segment(identity: str, idx: int, data: bytes,
                   total_size: int, gen: Optional[int] = None) -> bool:
    """投递一个完整分段给写线程（非阻塞）。

    队列满/未启用/容量为 0/磁盘剩余低于停写阈值/数据长度与分段期望
    不符 → 丢弃并计数，返回 False。gen 给定时按调用方捕获的代际
    入队（写线程落盘前再验，期间身份被失效过就丢弃）。永不抛。
    """
    try:
        if not identity or not data:
            return False
        if not enabled() or max_bytes() <= 0:
            return False
        if len(data) != segment_expected_len(int(total_size), idx):
            return False
        try:
            if shutil.disk_usage(cache_dir()).free < min_free_bytes():
                _bump("segments_dropped")
                return False
        except OSError:
            pass  # 盘信息读不到不拦投递，写线程落盘失败会自吞
        # 计数器初始化必须赶在本段落盘之前（此刻盘上还没有它）：
        # 写后再扫描初始化会把刚写的文件重复计一遍（实测 1MB 报
        # 2MB、连环误淘汰），_add_total 因此只做纯增量、永不扫描。
        _current_total_bytes()
        _ensure_writer()
        item = (identity, int(idx), bytes(data), int(total_size),
                _generation(identity) if gen is None else int(gen))
        try:
            _WRITE_QUEUE.put_nowait(item)
        except queue.Full:
            _bump("segments_dropped")
            return False
        return True
    except Exception:  # noqa: BLE001 — 投递失败只丢这一段
        _bump("segments_dropped")
        return False


def flush_writes() -> None:
    """等写队列排空（测试/停机用；生产路径不调用）。"""
    _WRITE_QUEUE.join()


def _scan_total_bytes() -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(cache_dir()):
            for name in files:
                if name.startswith("seg-") and name.endswith(".bin"):
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def _current_total_bytes() -> int:
    with _TOTAL_LOCK:
        if _TOTAL_STATE["bytes"] is None \
                or _TOTAL_STATE["dir"] != cache_dir():
            _TOTAL_STATE["bytes"] = _scan_total_bytes()
            _TOTAL_STATE["dir"] = cache_dir()
        return _TOTAL_STATE["bytes"]


def _add_total(delta: int) -> None:
    """纯增量记账（写线程专用）：绝不在这里扫描——调用时新文件
    已经落盘，扫描会把它与 delta 重复计一遍。初始化由
    submit_segment 在投递前完成；这里未初始化只兜底为 0 起算。"""
    with _TOTAL_LOCK:
        if _TOTAL_STATE["bytes"] is None \
                or _TOTAL_STATE["dir"] != cache_dir():
            _TOTAL_STATE["bytes"] = 0
            _TOTAL_STATE["dir"] = cache_dir()
        _TOTAL_STATE["bytes"] = max(0, _TOTAL_STATE["bytes"] + delta)


def _evict_if_needed() -> None:
    """超容量时按 mtime 从旧到新淘汰分段，留 5% 余量防抖。

    只由写线程调用（串行，无并发淘汰）；被淘汰分段若正被命中出流
    打开着，unlink 不影响已打开的句柄读完本段。
    """
    cap = max_bytes()
    if cap <= 0:
        return
    total = _current_total_bytes()
    if total <= cap:
        return
    target = int(cap * 0.95)
    entries = []
    try:
        for root, _dirs, files in os.walk(cache_dir()):
            for name in files:
                if name.startswith("seg-") and name.endswith(".bin"):
                    p = os.path.join(root, name)
                    try:
                        st = os.stat(p)
                        entries.append((st.st_mtime, st.st_size, p))
                    except OSError:
                        pass
    except OSError:
        return
    entries.sort(key=lambda e: e[0])
    for _mt, size, path in entries:
        if total <= target:
            break
        try:
            os.unlink(path)
            total -= size
            _add_total(-size)
            _bump("segments_evicted")
        except OSError:
            pass


def _writer_loop() -> None:
    while True:
        item = _WRITE_QUEUE.get()
        try:
            identity, idx, data, total_size, gen = item
            if not _segment_write_current(identity, gen):
                _bump("segments_dropped")
                continue
            path = _segment_path(identity, idx)
            tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                old_size = 0
                try:
                    old_size = os.path.getsize(path)
                except OSError:
                    pass
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
                if not _segment_write_current(identity, gen):
                    # 写入期间身份被失效（失效方已删目录，这段是
                    # 旧校验器的字节）：删掉、不计账，不许留给新一代
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                    _bump("segments_dropped")
                    continue
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                _add_total(len(data) - old_size)
                _bump("segments_written")
            except OSError:
                _bump("segments_dropped")
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            try:
                _evict_if_needed()
            except Exception:  # noqa: BLE001 — 淘汰失败不许杀写线程
                pass
        except Exception:  # noqa: BLE001 — 写线程绝不死
            logger.warning("relay_hot_cache 写线程处理异常", exc_info=True)
        finally:
            _WRITE_QUEUE.task_done()
