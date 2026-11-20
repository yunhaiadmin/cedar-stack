"""封面图片独立队列：文字刮削先落库，封面下载在后台慢慢补（图文解耦）。

**为什么要有这一层**：刮削主流程（``enrich_worker._process_item``）的写库
阶段早已零下载（v2.42.9），但「图什么时候落盘」此前只有三条路——刮削期
预热（``EMBY_IMAGE_PREWARM``，默认关，开了就占刮削 IO 阶段）、janitor
补下载巡检（带播放信号门 BE-18，没人看过的条目永远轮不到）、取图时
按需自愈（第一次打开的人付全额延迟，图床不可达时直接没图）。

本队列补上刮削侧的通道：条目文字刮削一提交，立刻把「有远程地址、
无本地文件」的条目 id 入队，worker 池在后台把海报/背景下载落盘并
回写 ``poster_path`` / ``backdrop_path``。

- **失败不拖累文字**：入队是 post-commit 的旁路动作，队列满/入队异常/
  下载失败都只记计数，绝不回滚、绝不改 ``enrich_status``；漏掉的条目
  仍有 janitor 巡检与取图自愈兜底（本队列不取代它们）。
- **并发可配**：worker 数走 SystemConfig ``image_queue_concurrency``
  （config_schema 登记，默认 4、钳 [1, 16]，热读带 60 秒进程内缓存；
  调大即时生效，调小等进程重启后收敛——worker 是守护线程不强杀）。
  实际下载仍过 ``image_store`` 的全局信号量（``EMBY_IMAGE_DL_CONCURRENCY``，
  非阻塞），两层都有界。
- **去重**：同一条目在队列中/处理中只占一个名额（``_inflight``），
  重复入队直接跳过。
"""
from __future__ import annotations

import logging
import queue as _queue_mod
import threading
import time
from typing import Optional

from backend.database import SessionLocal
from backend.emby_server import image_store
from backend.emby_server import models as em
from backend.integrations import store as _store

logger = logging.getLogger(__name__)

KEY_IMAGE_QUEUE_CONCURRENCY = "image_queue_concurrency"
DEFAULT_CONCURRENCY = 4
_MIN_CONCURRENCY = 1
_MAX_CONCURRENCY = 16
_QUEUE_MAXSIZE = 2000
_CONC_CACHE_TTL_SEC = 60.0

_QUEUE: "_queue_mod.Queue[int]" = _queue_mod.Queue(maxsize=_QUEUE_MAXSIZE)
_INFLIGHT: set = set()
_LOCK = threading.Lock()
_WORKERS_STARTED = 0
_CONC_CACHE: Optional[int] = None
_CONC_CACHE_AT = 0.0
_STATS = {"enqueued": 0, "dropped": 0, "landed": 0, "failed": 0, "skipped": 0}


def _target_concurrency() -> int:
    """当前配置的 worker 数（SystemConfig 热读，60 秒进程内缓存）。"""
    global _CONC_CACHE, _CONC_CACHE_AT
    now = time.monotonic()
    with _LOCK:
        if _CONC_CACHE is not None and now - _CONC_CACHE_AT < _CONC_CACHE_TTL_SEC:
            return _CONC_CACHE
    value = DEFAULT_CONCURRENCY
    try:
        db = SessionLocal()
        try:
            vals = _store.read_values(
                db, [KEY_IMAGE_QUEUE_CONCURRENCY],
                {KEY_IMAGE_QUEUE_CONCURRENCY: str(DEFAULT_CONCURRENCY)})
            value = int(vals.get(KEY_IMAGE_QUEUE_CONCURRENCY)
                        or DEFAULT_CONCURRENCY)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 读不到配置就用默认值，绝不挡入队
        value = DEFAULT_CONCURRENCY
    value = max(_MIN_CONCURRENCY, min(_MAX_CONCURRENCY, value))
    with _LOCK:
        _CONC_CACHE = value
        _CONC_CACHE_AT = now
    return value


def _ensure_workers() -> None:
    """按配置把 worker 池补到目标数（只增不减，守护线程）。"""
    global _WORKERS_STARTED
    target = _target_concurrency()
    with _LOCK:
        while _WORKERS_STARTED < target:
            t = threading.Thread(target=_worker_loop,
                                 name=f"image-queue-{_WORKERS_STARTED}",
                                 daemon=True)
            t.start()
            _WORKERS_STARTED += 1


def enqueue_item_images(item_id) -> bool:
    """把一个条目交给图片队列后台落盘；成功入队返回 True。

    幂等去重：已在队列/处理中的条目重复入队返回 False。队列满、
    本地化总开关关、任何异常都返回 False 并记计数——调用方（刮削
    写库后）绝不因入队失败受影响。
    """
    try:
        if not item_id or not image_store.enabled():
            return False
        item_id = int(item_id)
        with _LOCK:
            if item_id in _INFLIGHT:
                return False
            _INFLIGHT.add(item_id)
        try:
            _QUEUE.put_nowait(item_id)
        except _queue_mod.Full:
            with _LOCK:
                _INFLIGHT.discard(item_id)
                _STATS["dropped"] += 1
            return False
        with _LOCK:
            _STATS["enqueued"] += 1
        _ensure_workers()
        return True
    except Exception:  # noqa: BLE001 — 入队绝不影响调用方主流程
        logger.debug("图片队列入队失败 item=%s", item_id, exc_info=True)
        with _LOCK:
            _INFLIGHT.discard(item_id)
        return False


def process_item_now(db, item_id: int) -> str:
    """同步处理一个条目（worker 本体，测试可直调）：返回结果分类。

    - ``"landed"``：至少一张图落盘并回写；
    - ``"failed"``：有待落的图但一张都没下到（下载失败/被并发旋钮挡住）；
    - ``"skipped"``：条目不存在/已删/已合并，或本来就没有待落的图。
    只写图片字段，绝不碰 enrich_status 等刮削状态。
    """
    row = (db.query(em.MediaItem)
           .filter(em.MediaItem.id == item_id).first())
    if row is None or row.deleted_at is not None \
            or row.merged_into_id is not None:
        return "skipped"
    jobs = []
    if row.primary_image_url and not row.poster_path:
        jobs.append(("poster_path", row.primary_image_url))
    if row.backdrop_image_url and not row.backdrop_path:
        jobs.append(("backdrop_path", row.backdrop_image_url))
    if not jobs:
        return "skipped"
    image_store.prewarm([url for _field, url in jobs])
    got = False
    for field, url in jobs:
        local = image_store.localize(url, allow_download=False)
        if local:
            setattr(row, field, local)
            got = True
    if got:
        db.commit()
        return "landed"
    db.rollback()
    return "failed"


def _worker_loop() -> None:
    while True:
        item_id = _QUEUE.get()
        try:
            db = SessionLocal()
            try:
                outcome = process_item_now(db, item_id)
            finally:
                db.close()
            with _LOCK:
                if outcome in _STATS:
                    _STATS[outcome] += 1
        except Exception:  # noqa: BLE001 — 单条失败不许杀 worker
            logger.warning("图片队列处理失败 item=%s", item_id, exc_info=True)
            with _LOCK:
                _STATS["failed"] += 1
        finally:
            with _LOCK:
                _INFLIGHT.discard(item_id)
            _QUEUE.task_done()


def stats() -> dict:
    """队列当前状态（健康检查 / 测试用）。"""
    with _LOCK:
        return {"queue_size": _QUEUE.qsize(), "inflight": len(_INFLIGHT),
                "workers": _WORKERS_STARTED,
                "target_concurrency": _CONC_CACHE or DEFAULT_CONCURRENCY,
                **_STATS}
