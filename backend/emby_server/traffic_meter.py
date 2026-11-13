# -*- coding: utf-8 -*-
"""流量计量（记录侧）：播放字节出口按人按日累计。

为什么有这个模块
----------------
流量额度（backend/traffic_quota.py）的「当月已用」必须有真实计量
来源。此前全站只有一个进程级总数（line_stats.bytes_out，不分人、
不落库、重启清零），撑不起按人账目。本模块在既有字节出口旁路
归账到 ``user_traffic_usage_daily``：

- 中继腿：``api._observe_traffic`` 的计数包装本来就在数每次响应
  真正 yield 的字节（与 line_stats 同一口径），跑完后顺带调
  ``note_bytes(total, "relay")`` 把这笔字节归到当前用户；
- 本机文件腿：``streaming.serve_file`` 的 Range 迭代器逐块 yield，
  包装一层数实际 yield 字节后调 ``note_bytes(total, "local")``。
  FileResponse 全文件直发不走迭代器——**不计**（与 line_stats
  现行口径一致：宁可少算，不许按 Content-Length 虚报）；
- 302 直链：字节在客户端与网盘之间走，服务端结构上看不见——
  ``note_direct_play()`` 只记一次起播次数（direct_plays），
  **永远不记字节**，不许拿文件大小/码率估算冒充计量。

用户归属走 ContextVar：``api.video_stream`` 在入口
``set_current_user(user.id)``，字节出口在**包装点**（与 handler
同一上下文）取出后显式传给 note_*，不依赖消费阶段的上下文传播。
取不到用户（非 video_stream 上下文）就整笔不计——宁可漏计，
不许错记到别人头上。

铁律（照 watch_activity 同口径）：计量不许拖慢/拖垮播放。
- 热路径只做进程内字典累加（一次锁、O(1)），绝不查库；
- 落库由后台守护线程按 FLUSH_INTERVAL_SEC 批量 upsert（独立
  会话），失败静默计数、下轮重试同一批的剩余部分也不做——
  丢就丢（计量是观测面，不是账房；真正扣天数的换流量成交走
  traffic_quota 的同步事务，不经本模块）；
- 紧急止血开关 env ``TRAFFIC_METER_ENABLED=0``（import 时读一次）。

跨日：聚合键带日期（事件发生日），落账永远落到事件那一天的行，
进程跨午夜不串日。
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

# import 时读一次（与 watch_activity 同口径）：热路径不查 env。
_ENABLED = os.getenv("TRAFFIC_METER_ENABLED", "1") not in (
    "0", "false", "False", "")

#: 后台落库间隔（秒）。聚合在内存里攒，丢一轮进程崩溃最多丢
#: 这么久的计量——观测面可接受，不为它上 WAL 式复杂度。
FLUSH_INTERVAL_SEC = 30.0

#: 聚合表上限（防异常流量把字典撑爆）：超过即先触发一次落库。
_PENDING_MAX_KEYS = 4096

_CURRENT_USER_ID: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("traffic_meter_user_id", default=None))

_LOCK = threading.Lock()
# (user_id, day) -> [relay_bytes, local_bytes, direct_plays]
_PENDING: dict = {}
_STATS = {"noted_bytes": 0, "noted_plays": 0, "flushes": 0,
          "written_rows": 0, "write_errors": 0, "skipped_no_user": 0}
_THREAD: Optional[threading.Thread] = None
_THREAD_LOCK = threading.Lock()
_STOP = threading.Event()


def is_enabled() -> bool:
    return _ENABLED


def set_current_user(user_id) -> None:
    """video_stream 入口调：把本次流响应归到这个用户。

    非法值（None/0/非整数）一律置空——归属宁空勿错。
    """
    try:
        uid = int(user_id) if user_id else None
    except (TypeError, ValueError):
        uid = None
    _CURRENT_USER_ID.set(uid if uid and uid > 0 else None)


def current_user_id() -> Optional[int]:
    return _CURRENT_USER_ID.get()


def _bump(name: str, n: int = 1) -> None:
    _STATS[name] = _STATS.get(name, 0) + n


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def note_bytes(count: int, source: str, *,
               user_id: Optional[int] = None,
               day: Optional[date] = None) -> None:
    """记一笔真实出字节（热路径：只累加内存，绝不抛、绝不查库）。

    ``source`` 只许 "relay" / "local"；``user_id`` 不传时读
    ContextVar（字节出口的正常路径），取不到就不计。
    """
    if not _ENABLED or not count or count <= 0:
        return
    if source not in ("relay", "local"):
        return
    uid = user_id if user_id is not None else _CURRENT_USER_ID.get()
    if not uid:
        with _LOCK:
            _bump("skipped_no_user")
        return
    key = (int(uid), day or datetime.now().date())
    with _LOCK:
        cell = _PENDING.get(key)
        if cell is None:
            cell = _PENDING[key] = [0, 0, 0]
        cell[0 if source == "relay" else 1] += int(count)
        _bump("noted_bytes", int(count))
        over = len(_PENDING) > _PENDING_MAX_KEYS
    _ensure_flusher()
    if over:
        flush_now()


def note_direct_play(*, user_id: Optional[int] = None,
                     day: Optional[date] = None) -> None:
    """记一次 302 直链起播（只记次、不记字节，见模块 docstring）。"""
    if not _ENABLED:
        return
    uid = user_id if user_id is not None else _CURRENT_USER_ID.get()
    if not uid:
        with _LOCK:
            _bump("skipped_no_user")
        return
    key = (int(uid), day or datetime.now().date())
    with _LOCK:
        cell = _PENDING.get(key)
        if cell is None:
            cell = _PENDING[key] = [0, 0, 0]
        cell[2] += 1
        _bump("noted_plays")
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_flusher_loop, name="traffic-meter-flusher",
            daemon=True)
        _THREAD.start()


def _flusher_loop() -> None:
    while not _STOP.wait(FLUSH_INTERVAL_SEC):
        try:
            flush_now()
        except Exception:  # noqa: BLE001 — 落库失败静默，下轮再试新批
            logger.warning("流量计量落库轮次失败", exc_info=True)


def _swap_pending() -> dict:
    with _LOCK:
        if not _PENDING:
            return {}
        batch = dict(_PENDING)
        _PENDING.clear()
        return batch


def apply_batch(db, batch: dict) -> int:
    """把一批聚合 upsert 进日账（写线程与测试共用；自带提交）。

    返回写入行数。同键并发建行撞唯一约束时回滚后走更新路径重试
    一次（照 watch_activity.apply_touch 的并发收法）。
    """
    written = 0
    for (user_id, day), (relay, local, plays) in batch.items():
        row = (db.query(em.TrafficUsageDaily)
               .filter(em.TrafficUsageDaily.user_id == user_id,
                       em.TrafficUsageDaily.day == day)
               .first())
        if row is None:
            db.add(em.TrafficUsageDaily(
                user_id=user_id, day=day, relay_bytes=relay,
                local_bytes=local, direct_plays=plays))
            try:
                db.commit()
                written += 1
                continue
            except IntegrityError:
                db.rollback()
                row = (db.query(em.TrafficUsageDaily)
                       .filter(em.TrafficUsageDaily.user_id == user_id,
                               em.TrafficUsageDaily.day == day)
                       .first())
                if row is None:
                    raise
        row.relay_bytes = int(row.relay_bytes or 0) + relay
        row.local_bytes = int(row.local_bytes or 0) + local
        row.direct_plays = int(row.direct_plays or 0) + plays
        row.updated_at = datetime.now()
        db.commit()
        written += 1
    return written


def flush_now(db=None) -> int:
    """把当前聚合落库，返回写入行数（无待落数据返回 0）。

    失败静默计数返回 0——这一批就此丢弃（见模块 docstring 的
    取舍），绝不把异常抛回调用方（可能是播放路径的溢出触发）。
    """
    batch = _swap_pending()
    if not batch:
        return 0
    own = db is None
    if own:
        from backend.database import SessionLocal
        db = SessionLocal()
    try:
        written = apply_batch(db, batch)
        with _LOCK:
            _bump("flushes")
            _bump("written_rows", written)
        return written
    except Exception:  # noqa: BLE001 — 计量失败静默，绝不影响播放
        with _LOCK:
            _bump("write_errors")
        logger.warning("流量计量落库失败（本批丢弃）", exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    finally:
        if own:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def reset_pending() -> None:
    """测试用：清聚合与统计（不要在生产路径调用）。"""
    with _LOCK:
        _PENDING.clear()
        for key in _STATS:
            _STATS[key] = 0
