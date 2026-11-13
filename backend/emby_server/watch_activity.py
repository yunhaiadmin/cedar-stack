# -*- coding: utf-8 -*-
"""中继流活动打点（中继兜底计时的记录侧）。

为什么有这个模块
----------------
emby_play_start_events 只记起播瞬间、drive_play_access_events 只记
起播入口的门禁/解析事件——都没有「流还在不在被拉」的持续活动时间
戳，还原不出观看跨度。PlaybackSession 又完全依赖客户端上报
Sessions/Playing|Progress|Stopped，遇到全程不上报的客户端（如
2026-11-11 实案里的 SenPlayer）就整段漏账。

本模块在 EA 中继的字节出口（200/206；302 直链客户端离服务器拉
流、结构上记不了，不在本模块范围）记活动：按 (user, item, device)
聚合到 emby_watch_stream_activity 的一行租约记录上，只滚动首末时
间；相邻活动间隔超过 SEGMENT_GAP 时旧段滚入 pending_segments
（2026-11-13 货币下线：结账工人已删，段只留存、不再被消费）。

铁律（照 play_events 同口径）：打点不许拖慢播放。
- 热路径只做一次进程内节流判断 + ``queue.put_nowait``（微秒级）；
- 落库由后台守护线程批量做（独立会话），失败静默计数；
- 同一 (user, item, device) 在 THROTTLE_SECONDS 内的重复拉流
  （Range 请求风暴）直接在进程内合并，不进队列。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

# import 时读一次（与 play_events 同口径）：热路径不查 env。
# 这是紧急止血开关（2026-11-13 货币下线：原结账工人与
# watch_fallback_credit_enabled 配置键已删）。
_ENABLED = os.getenv("WATCH_ACTIVITY_ENABLED", "1") not in (
    "0", "false", "False", "")

#: 切段阈值：相邻两次活动间隔超过它，视为两段不同的观看。
SEGMENT_GAP = timedelta(minutes=10)

#: 进程内节流：同一聚合键两次入队至少间隔这么久（Range 请求合并）。
THROTTLE_SECONDS = 20.0

#: 队列上限：满了就丢新事件（播放高峰宁可少记，不许反压播放）。
QUEUE_MAX = 2048
BATCH = 50
FLUSH_INTERVAL_SEC = 1.0

_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
_STATS = {"noted": 0, "written": 0, "dropped": 0, "write_errors": 0}
_STATS_LOCK = threading.Lock()
_THROTTLE: dict = {}
_THROTTLE_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_THREAD_LOCK = threading.Lock()
_STOP = threading.Event()


def is_enabled() -> bool:
    return _ENABLED


def _bump(name: str, n: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] += n


def activity_stats() -> dict:
    with _STATS_LOCK:
        return dict(_STATS)


def note_stream_activity(user_id, item_id, device_key, *,
                         at: Optional[datetime] = None) -> None:
    """中继字节出口的活动打点（热路径，绝不抛、绝不查库）。

    只收 200/206 的实际出字节响应——调用方（api.video_stream 的
    _observe_traffic）负责口径，本函数不判状态码。
    """
    if not _ENABLED:
        return
    if not user_id or not item_id:
        return
    key = (int(user_id), int(item_id), str(device_key or "anon")[:128])
    now_mono = time.monotonic()
    with _THROTTLE_LOCK:
        last = _THROTTLE.get(key)
        if last is not None and now_mono - last < THROTTLE_SECONDS:
            return
        _THROTTLE[key] = now_mono
        # 节流表防膨胀：超量时清掉过期项（仍在锁内，便宜）。
        if len(_THROTTLE) > 8192:
            cutoff = now_mono - THROTTLE_SECONDS
            for k in [k for k, v in _THROTTLE.items() if v < cutoff]:
                _THROTTLE.pop(k, None)
    try:
        _QUEUE.put_nowait({
            "user_id": key[0], "item_id": key[1], "device_key": key[2],
            "at": at or datetime.now(),
        })
    except queue.Full:
        _bump("dropped")
        return
    _bump("noted")
    _ensure_writer()


def _ensure_writer() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_writer_loop, name="watch-activity-writer", daemon=True)
        _THREAD.start()


def _writer_loop() -> None:
    from backend.database import SessionLocal

    while not _STOP.is_set():
        batch: list = []
        try:
            first = _QUEUE.get(timeout=FLUSH_INTERVAL_SEC)
            batch.append(first)
            deadline = time.monotonic() + FLUSH_INTERVAL_SEC
            while len(batch) < BATCH and time.monotonic() < deadline:
                try:
                    batch.append(_QUEUE.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue
        # 同键一批只保留最晚的一次（批跨度 ≤1 秒，远小于切段阈值）。
        latest: dict = {}
        for ev in batch:
            k = (ev["user_id"], ev["item_id"], ev["device_key"])
            if k not in latest or ev["at"] > latest[k]["at"]:
                latest[k] = ev
        db = SessionLocal()
        try:
            for ev in latest.values():
                apply_touch(db, ev["user_id"], ev["item_id"],
                            ev["device_key"], ev["at"])
            _bump("written", len(latest))
        except Exception:  # noqa: BLE001 — 打点失败静默，绝不影响播放
            _bump("write_errors")
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()


def _load_pending(row) -> list:
    try:
        data = json.loads(row.pending_segments or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _dump_pending(segments: list) -> str:
    return json.dumps(
        [[s.isoformat(), e.isoformat()] for s, e in segments])


def apply_touch(db, user_id: int, item_id: int, device_key: str,
                at: datetime) -> None:
    """把一次活动并入租约行（写线程与测试共用；自带提交）。

    规则：无行则建行开段；活动时间不晚于已记末时间则忽略（乱序）；
    与末时间间隔超 SEGMENT_GAP 时，当前开放段滚入 pending 待结账、
    以本次活动开新段；否则只滚动末时间。
    """
    device_key = str(device_key or "anon")[:128]
    row = (db.query(em.WatchStreamActivity)
           .filter(em.WatchStreamActivity.user_id == user_id,
                   em.WatchStreamActivity.item_id == item_id,
                   em.WatchStreamActivity.device_key == device_key)
           .first())
    if row is None:
        db.add(em.WatchStreamActivity(
            user_id=user_id, item_id=item_id, device_key=device_key,
            first_activity_at=at, last_activity_at=at,
            pending_segments="[]"))
        try:
            db.commit()
            return
        except IntegrityError:
            # 多进程同键并发建行：退回走更新路径再试一次。
            db.rollback()
            row = (db.query(em.WatchStreamActivity)
                   .filter(em.WatchStreamActivity.user_id == user_id,
                           em.WatchStreamActivity.item_id == item_id,
                           em.WatchStreamActivity.device_key == device_key)
                   .first())
            if row is None:
                raise
    if row.last_activity_at is None or row.first_activity_at is None:
        row.first_activity_at = at
        row.last_activity_at = at
    elif at <= row.last_activity_at:
        return  # 乱序/重复触点，不回退时间
    elif at - row.last_activity_at > SEGMENT_GAP:
        pending = _load_pending_pairs(row)
        pending.append((row.first_activity_at, row.last_activity_at))
        row.pending_segments = _dump_pending(pending)
        row.first_activity_at = at
        row.last_activity_at = at
    else:
        row.last_activity_at = at
    row.updated_at = datetime.now()
    db.commit()


def _load_pending_pairs(row) -> list:
    """pending_segments JSON → [(start, end), ...] datetime 对。"""
    out = []
    for pair in _load_pending(row):
        try:
            s = datetime.fromisoformat(pair[0])
            e = datetime.fromisoformat(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if e >= s:
            out.append((s, e))
    return out
