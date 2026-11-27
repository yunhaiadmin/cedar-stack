# -*- coding: utf-8 -*-
"""条目状态分布共享缓存（C-02，2026-11-28）。

背景：health / observability 与管理面多个消费者各自现算 emby_items
（约 41 万行）的 enrich / probe 状态分布——observability._probe_enrich
与 health_report.collect 各跑 4 次 enrich_status COUNT、
health_report 另有 8 次 (item_type, probe_status) COUNT、
probe_worker.status_snapshot 与 enrich_worker.get_progress 各跑
一次 GROUP BY。pg_stat_statements 6.3 天窗口里同族查询累计约
9,248 秒（全 DB 执行时间的 15.9%），且每次 health 检查、管理面
打开都会整族重跑。

本模块把这族聚合收敛成一份进程内共享快照：短 TTL（默认 300 秒，
``ITEM_STATUS_COUNTS_TTL_SECONDS`` 可调）内所有消费者共用同一次
聚合的结果。R-01（2026-11-28）：默认 TTL 由 60 秒改为 300 秒——
复扫实测 4 条残余查询各约 1 次/分钟、合计 0.87s/min，根因是快照
每 60 秒刷新一次仍要跑全表 GROUP BY；300 秒后刷新降为约 0.2 次/
分钟。代价：跨进程写入的数字最多滞后 5 分钟（健康/仪表盘口径本就
容忍分钟级陈旧；本进程管理动作写入仍即时失效，见下）。
口径与原各消费者逐字一致：

- enrich 分布：``GROUP BY enrich_status``，``merged_into_id IS NULL``
  （软删由 ORM 钩子排除，与 _probe_enrich / _enrich_count 原口径同）。
- probe 分布：``GROUP BY item_type, probe_status``，
  ``merged_into_id IS NULL``（与 status_snapshot / get_progress /
  health_report._count 原口径同；按类型过滤在消费侧做，
  PROBE_ITEM_TYPES 的 env 变更不影响缓存口径）。
- 错误分布（R-01）：``GROUP BY probe_last_error``，仅
  ``probe_last_error IS NOT NULL`` 且 ``probe_status IN
  (pending, failed)`` 的行，与 status_snapshot 原现算查询同口径；
  status_snapshot 全量模式的 top_errors 取前 5。
- 近期完成数（R-01）：``enrich_status='done'`` 且 ``last_scraped_at``
  落在窗口内的行数，与 health_report._enrich_recent_completed 同
  口径；窗口见 recent_done_window_minutes()。

失效两条腿（与 enrich 认领快照的「事件失效 + TTL 兜底」同款）：
- 本进程写入即时失效：Session before_flush / do_orm_execute 监听
  发现 MediaItem 写入（新增/改/删，含批量 DML）就作废快照——管理
  动作改完状态，下一次读立刻是新数，上层缓存（admin_tasks 20 秒
  快照、health collect_cached）清掉后能拿到新数，既有契约不变。
- 跨进程写入（worker 在另一进程改状态）靠 TTL 收敛，最多滞后
  一个 TTL——这正是短 TTL 快照的既定语义。

缓存按 session 的 bind（engine）隔离：生产全进程共用一个 engine、
共享一份快照；测试里各 engine 各自一份，不串扰。刷新失败时有旧
快照就退回旧快照（与 health_report.collect_cached 同惯例），没有
旧快照才抛错，由消费者按原有口径吞掉（返回 0 / warn）。

刻意不迁移的消费者（口径不同，强行合并会改变其数字）：
- server_ops._pipeline_payload：按库范围聚合，不是全局分布；
- task_scheduler 的调度状态计数：enrich 计数不排除合并副本，
  与全局口径不同，是否对齐另行拍板；
- panel / admin_dashboard 的按库计数：另一个聚合维度。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.emby_server.env_util import env_float, env_int

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300.0

# RLock：_compute 用调用方 session 现算时可能触发 autoflush，
# flush 监听会同线程重入 _invalidate，普通锁会死锁。
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {
    "bind": None, "at": 0.0, "enrich": None, "probe_by_type": None,
    "errors": None, "recent_done": None,
}
_WATCH_INSTALLED = False


def _ttl_seconds() -> float:
    return env_float("ITEM_STATUS_COUNTS_TTL_SECONDS",
                     DEFAULT_TTL_SECONDS, lo=0.0)

def recent_done_window_minutes() -> int:
    """近期完成窗口（分钟）：与 health_report.HEALTH_ENRICH_PROGRESS_MIN
    同源（同一 env 键 HEALTH_ENRICH_PROGRESS_MIN，默认 10 分钟）。
    快照只存这一个窗口的值；health_report._enrich_recent_completed
    请求别的窗口时不走快照、直接现算。"""
    return env_int("HEALTH_ENRICH_PROGRESS_MIN", 10, lo=1)


def _bind_of(db):
    try:
        return db.get_bind()
    except Exception:  # noqa: BLE001 — 拿不到 bind 就不走缓存（直算）
        return None


def _invalidate() -> None:
    with _CACHE_LOCK:
        _CACHE["bind"] = None
        _CACHE["at"] = 0.0
        _CACHE["enrich"] = None
        _CACHE["probe_by_type"] = None
        _CACHE["errors"] = None
        _CACHE["recent_done"] = None


def _install_invalidate_watch() -> None:
    """注册写失效监听（进程级一次）：MediaItem 写入即作废快照。

    监听只翻标记、整段包 try/except，宁可漏失效（TTL 兜底）也不许
    拖垮任何一次正常 flush——与认领快照 _install_claim_flush_watch
    同纪律。
    """
    global _WATCH_INSTALLED
    if _WATCH_INSTALLED:
        return
    _WATCH_INSTALLED = True
    try:
        from sqlalchemy import event as _sa_event
        from sqlalchemy.orm import Session as _sa_session

        from backend.emby_server import models as _em

        def _watch_flush(session, flush_context, instances):
            try:
                for obj in (list(session.new) + list(session.dirty)
                            + list(session.deleted)):
                    if isinstance(obj, _em.MediaItem):
                        _invalidate()
                        return
            except Exception:  # noqa: BLE001
                pass

        def _watch_execute(execute_state):
            try:
                if not (execute_state.is_insert or execute_state.is_update
                        or execute_state.is_delete):
                    return
                table = getattr(execute_state.statement, "table", None)
                if getattr(table, "name", None) == "emby_items":
                    _invalidate()
            except Exception:  # noqa: BLE001
                pass

        _sa_event.listen(_sa_session, "before_flush", _watch_flush)
        _sa_event.listen(_sa_session, "do_orm_execute", _watch_execute)
    except Exception:  # noqa: BLE001
        logger.debug("状态分布写失效监听安装失败（TTL 兜底）", exc_info=True)


def _compute(db) -> tuple[dict, dict, list, int]:
    """四条聚合现算（口径见模块 docstring）：enrich 分布、probe 按类型
    分布、错误分布、近期完成数。一次刷新把四条全算完，TTL 内所有
    消费者共用——R-01 前错误分布与近期完成数是快照外的实时查，
    各约 1 次/分钟；进快照后随刷新节律走（TTL 300 秒时约 0.2 次/分钟）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from backend.emby_server import models as em

    enrich_rows = (
        db.query(em.MediaItem.enrich_status, func.count(em.MediaItem.id))
        .filter(em.MediaItem.merged_into_id.is_(None))
        .group_by(em.MediaItem.enrich_status)
        .all()
    )
    enrich = {(s or "unknown"): int(c) for s, c in enrich_rows}
    probe_rows = (
        db.query(em.MediaItem.item_type, em.MediaItem.probe_status,
                 func.count(em.MediaItem.id))
        .filter(em.MediaItem.merged_into_id.is_(None))
        .group_by(em.MediaItem.item_type, em.MediaItem.probe_status)
        .all()
    )
    probe_by_type: dict[str, dict[str, int]] = {}
    for item_type, status, cnt in probe_rows:
        probe_by_type.setdefault(item_type or "unknown", {})[
            status or "unknown"] = int(cnt)
    # 错误分布：与 probe_worker.status_snapshot 原现算查询同口径
    #（probe_last_error 非空、probe_status 在 pending/failed、副本排除；
    # 软删/realm 过滤由 ORM 钩子补，与原查询同一 session 同口径）。
    error_rows = (
        db.query(em.MediaItem.probe_last_error, func.count(em.MediaItem.id))
        .filter(em.MediaItem.probe_last_error.isnot(None),
                em.MediaItem.probe_status.in_(("pending", "failed")),
                em.MediaItem.merged_into_id.is_(None))
        .group_by(em.MediaItem.probe_last_error)
        .order_by(func.count(em.MediaItem.id).desc())
        .all()
    )
    errors = [(e, int(c)) for e, c in error_rows]
    # 近期完成数：与 health_report._enrich_recent_completed 同口径。
    cutoff = datetime.now() - timedelta(
        minutes=recent_done_window_minutes())
    recent_done = (
        db.query(func.count(em.MediaItem.id))
        .filter(em.MediaItem.enrich_status == "done",
                em.MediaItem.merged_into_id.is_(None),
                em.MediaItem.last_scraped_at.isnot(None),
                em.MediaItem.last_scraped_at >= cutoff)
        .scalar() or 0
    )
    return enrich, probe_by_type, errors, int(recent_done)


def _distributions(db) -> tuple[dict, dict, list, int]:
    """缓存入口：同 bind + TTL 内共用一份快照（collect_cached 同惯例）。"""
    _install_invalidate_watch()
    ttl = _ttl_seconds()
    bind = _bind_of(db)
    if ttl <= 0 or bind is None:
        return _compute(db)
    with _CACHE_LOCK:
        if (_CACHE["bind"] is bind and _CACHE["enrich"] is not None
                and (time.monotonic() - _CACHE["at"]) < ttl):
            return (_CACHE["enrich"], _CACHE["probe_by_type"],
                    _CACHE["errors"], _CACHE["recent_done"])
        try:
            enrich, probe_by_type, errors, recent_done = _compute(db)
        except Exception:  # noqa: BLE001 — 刷新失败退回旧快照
            if _CACHE["bind"] is bind and _CACHE["enrich"] is not None:
                logger.debug("状态分布刷新失败，退回旧快照", exc_info=True)
                return (_CACHE["enrich"], _CACHE["probe_by_type"],
                        _CACHE["errors"], _CACHE["recent_done"])
            raise
        _CACHE["bind"] = bind
        _CACHE["at"] = time.monotonic()
        _CACHE["enrich"] = enrich
        _CACHE["probe_by_type"] = probe_by_type
        _CACHE["errors"] = errors
        _CACHE["recent_done"] = recent_done
        return enrich, probe_by_type, errors, recent_done


def enrich_status_counts(db) -> dict[str, int]:
    """enrich 状态分布 ``{status: count}``（副本排除口径）。"""
    enrich, _, _, _ = _distributions(db)
    return dict(enrich)


def probe_counts_by_type(db) -> dict[str, dict[str, int]]:
    """probe 状态分布 ``{item_type: {status: count}}``（副本排除口径）。"""
    _, probe_by_type, _, _ = _distributions(db)
    return {t: dict(m) for t, m in probe_by_type.items()}


def probe_status_counts(db, item_types) -> dict[str, int]:
    """指定类型集合的 probe 状态总量 ``{status: count}``。"""
    _, probe_by_type, _, _ = _distributions(db)
    totals: dict[str, int] = {}
    for t in item_types:
        for status, cnt in (probe_by_type.get(t) or {}).items():
            totals[status] = totals.get(status, 0) + cnt
    return totals


def probe_error_counts(db) -> list[tuple[str, int]]:
    """探测错误分布 ``[(error, count)]``（按 count 倒序），与
    probe_worker.status_snapshot 原现算 GROUP BY 同口径：
    probe_last_error 非空、probe_status 在 (pending, failed)、
    合并副本与软删行排除。值随快照走，最多滞后一个 TTL。"""
    _, _, errors, _ = _distributions(db)
    return list(errors)


def enrich_recent_done(db) -> int:
    """最近窗口内完成的可见条目数（done 且 last_scraped_at 落在窗口内），
    与 health_report._enrich_recent_completed 同口径。窗口见
    recent_done_window_minutes()；值随快照走，最多滞后一个 TTL。"""
    _, _, _, recent_done = _distributions(db)
    return int(recent_done)


def _reset_cache() -> None:
    """测试隔离用：清空进程内快照。生产代码不调用。"""
    _invalidate()
