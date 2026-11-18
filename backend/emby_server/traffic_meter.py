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
``set_current_user(有效账号 id)``——家庭子账号经
``backend/sub_accounts`` 的有效账号解析归到主账号（子账号没有
自己的流量账），字节出口在**包装点**（与 handler 同一上下文）
取出后显式传给 note_*，不依赖消费阶段的上下文传播。
取不到用户（非 video_stream 上下文）就整笔不计——宁可漏计，
不许错记到别人头上。

线路旁路汇总（2026-11-19 线路配额，按线路计）：同一笔被接受
计量的字节再按「这笔请求实际从哪个入口 host 进来的」记一份到
``line_traffic_usage_daily``。入口 host 在请求入口由 video_stream
经 ContextVar 钉住（``set_current_entry_host``，取法见
emby_server.public_url.request_entry_host：可信反代采信
X-Forwarded-Host、否则 Host 头——两跳 nginx 均透传原始 Host），
字节出口在包装点取出后显式传给 note_*。热路径只按 (host, 日)
攒 relay/local 两腿，落库时按 host 匹配 url 的 host 相同的线路
（backend/line_quota.match_line_id）；**匹配不到任何线路就整笔
不记线路账**（只记用户账）——宁可漏计，绝不记错线。线路的配额
判定读该表（backend/line_quota）；本模块只负责记。

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
_CURRENT_ENTRY_HOST: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("traffic_meter_entry_host", default=None))

_LOCK = threading.Lock()
# (user_id, day) -> [relay_bytes, local_bytes, direct_plays]
_PENDING: dict = {}
# 线路旁路：(归属键 host, day) -> [relay_bytes, local_bytes]
# （host → 线路 id 落库时匹配，见 docstring）
_PENDING_LINE: dict = {}
_STATS = {"noted_bytes": 0, "noted_plays": 0, "flushes": 0,
          "written_rows": 0, "write_errors": 0, "skipped_no_user": 0,
          "line_noted_bytes": 0, "line_written_rows": 0,
          "line_write_errors": 0, "line_skipped_no_match": 0}
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


def set_current_entry_host(host) -> None:
    """video_stream 入口调：把本次流的入口 host 钉住（线路归属键）。

    入参应是 public_url.request_entry_host 的结果；这里再归一
    一次（小写/去默认端口），归一不出合法 host 就置空——归属
    宁空勿错，空则这笔字节只记用户账、不记线路账。
    """
    from backend import line_quota

    key = line_quota.normalize_entry_host(host or "")
    _CURRENT_ENTRY_HOST.set(key or None)


def current_entry_host() -> Optional[str]:
    return _CURRENT_ENTRY_HOST.get()


def _bump(name: str, n: int = 1) -> None:
    _STATS[name] = _STATS.get(name, 0) + n


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def note_bytes(count: int, source: str, *,
               user_id: Optional[int] = None,
               entry_host: Optional[str] = None,
               day: Optional[date] = None) -> None:
    """记一笔真实出字节（热路径：只累加内存，绝不抛、绝不查库）。

    ``source`` 只许 "relay" / "local"；``user_id`` 不传时读
    ContextVar（字节出口的正常路径），取不到就不计。``entry_host``
    不传时同样读 ContextVar（入口 host 归属键）；取不到/归一不出
    就只记用户账、不记线路账。
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
    host = entry_host if entry_host is not None else _CURRENT_ENTRY_HOST.get()
    if host:
        from backend import line_quota

        host = line_quota.normalize_entry_host(host) or None
    key = (int(uid), day or datetime.now().date())
    with _LOCK:
        cell = _PENDING.get(key)
        if cell is None:
            cell = _PENDING[key] = [0, 0, 0]
        cell[0 if source == "relay" else 1] += int(count)
        _bump("noted_bytes", int(count))
        # 线路旁路：同一笔字节按 (入口 host, 日) 再攒一份，
        # 落库时按 host 匹配线路（匹配不到不记线路账）
        if host:
            lkey = (host, key[1])
            lcell = _PENDING_LINE.get(lkey)
            if lcell is None:
                lcell = _PENDING_LINE[lkey] = [0, 0]
            lcell[0 if source == "relay" else 1] += int(count)
            _bump("line_noted_bytes", int(count))
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


def apply_line_batch(db, batch: dict) -> int:
    """把线路旁路聚合 upsert 进线路日账（写线程与测试共用；自带提交）。

    ``batch``：(line_id, day) -> [relay_bytes, local_bytes]。并发
    建行撞唯一约束时回滚后走更新路径重试一次（与 apply_batch
    同口径）。
    """
    written = 0
    for (line_id, day), (relay, local) in batch.items():
        row = (db.query(em.LineTrafficUsageDaily)
               .filter(em.LineTrafficUsageDaily.line_id == line_id,
                       em.LineTrafficUsageDaily.day == day)
               .first())
        if row is None:
            db.add(em.LineTrafficUsageDaily(
                line_id=line_id, day=day, relay_bytes=relay,
                local_bytes=local))
            try:
                db.commit()
                written += 1
                continue
            except IntegrityError:
                db.rollback()
                row = (db.query(em.LineTrafficUsageDaily)
                       .filter(em.LineTrafficUsageDaily.line_id == line_id,
                               em.LineTrafficUsageDaily.day == day)
                       .first())
                if row is None:
                    raise
        row.relay_bytes = int(row.relay_bytes or 0) + relay
        row.local_bytes = int(row.local_bytes or 0) + local
        row.updated_at = datetime.now()
        db.commit()
        written += 1
    return written


def _resolve_line_batch(db, batch: dict) -> tuple[dict, int]:
    """把 (host, day) 聚合按入口 host 匹配成 (line_id, day) 聚合。

    返回 ``(matched, skipped_bytes)``：匹配不到任何线路的字节
    整笔丢弃（只记用户账）——host 是记账当刻的事实，线路配置
    之后再改也补不回来，留着重试只会记错线。
    """
    from backend import line_quota

    matched: dict = {}
    skipped = 0
    for (host, day), (relay, local) in batch.items():
        line_id = line_quota.match_line_id(db, host)
        if line_id is None:
            skipped += relay + local
            continue
        cell = matched.setdefault((line_id, day), [0, 0])
        cell[0] += relay
        cell[1] += local
    return matched, skipped


def _swap_pending_line() -> dict:
    with _LOCK:
        if not _PENDING_LINE:
            return {}
        batch = dict(_PENDING_LINE)
        _PENDING_LINE.clear()
        return batch


def flush_now(db=None) -> int:
    """把当前聚合落库，返回写入行数（无待落数据返回 0）。

    失败静默计数返回 0——这一批就此丢弃（见模块 docstring 的
    取舍），绝不把异常抛回调用方（可能是播放路径的溢出触发）。
    线路旁路与用户账分开结算：任一侧失败只丢自己那批，不连累另一侧。
    """
    batch = _swap_pending()
    line_batch = _swap_pending_line()
    if not batch and not line_batch:
        return 0
    own = db is None
    if own:
        from backend.database import SessionLocal
        db = SessionLocal()
    written = 0
    try:
        if batch:
            try:
                written = apply_batch(db, batch)
                with _LOCK:
                    _bump("flushes")
                    _bump("written_rows", written)
            except Exception:  # noqa: BLE001 — 计量失败静默，绝不影响播放
                with _LOCK:
                    _bump("write_errors")
                logger.warning("流量计量落库失败（本批丢弃）", exc_info=True)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        if line_batch:
            try:
                matched, skipped = _resolve_line_batch(db, line_batch)
                if skipped:
                    with _LOCK:
                        _bump("line_skipped_no_match", skipped)
                    logger.warning(
                        "线路计量落库跳过 %s 字节：入口 host 匹配不到任何线路（只记用户账）",
                        skipped)
                if matched:
                    line_written = apply_line_batch(db, matched)
                    with _LOCK:
                        _bump("line_written_rows", line_written)
            except Exception:  # noqa: BLE001 — 线路计量失败静默，不连累用户账
                with _LOCK:
                    _bump("line_write_errors")
                logger.warning("线路计量落库失败（本批丢弃）", exc_info=True)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        return written
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
        _PENDING_LINE.clear()
        for key in _STATS:
            _STATS[key] = 0
