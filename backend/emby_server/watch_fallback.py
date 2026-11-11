# -*- coding: utf-8 -*-
"""中继兜底计时·结账工人。

把 watch_activity 记下的中继流活动段，按与真实会话完全相同的一套
入账函数补记观看时长：

- 活力：``vitality.award_watch_minutes_reward``（单次分钟数分档，
  与 compat_routes._record_stopped 的调用同路径）；
- 观影能量：``watch_points.award_watch_points``（当日累计分档）。
  该函数按 (user, item) 的 item_baselines 基线只计增量——兜底调用
  时传「当前基线 + 本段应补分钟」，使本段贡献恰为应补分钟，且与
  真实会话共用同一本 watch_points_daily 账，不另写累加逻辑。

不补 member_level 的观影 XP：本单范围只含 watch_points_daily 与
活力两条账（任务书口径），XP 漏计与否另议。

入账前逐项过门（顺序即代码顺序）：
1. 起步门槛：段时长 < MIN_SEGMENT_MINUTES 不入账（防预加载/误点）；
   孤零零一次拉流的段时长为 0，天然被本门拦下。
2. 去重：段与同用户同条目的真实 PlaybackSession 时间窗
   [start_time, ended_at or last_update_at] 重叠的部分逐段扣除，
   只补扣除后仍 ≥ 起步门槛的真空碎片（真实会话优先）。
3. 单段封顶：不超过条目自身时长（duration_ticks 可读时）。
4. 每日封顶：每用户每日兜底入账 ≤ DAILY_FALLBACK_CAP_MINUTES；
   已入账的兜底分钟记在 watch_points_daily.item_baselines 的保留
   键 "__fallback__" 里——这就是兜底来源的账面标记，管理端/事后
   查账按此键区分哪些分钟是兜底来的；工人日志同步逐段留痕。

触发：worker 进程内常驻线程，ROUND_INTERVAL_SEC 一轮，只处理已
静默收尾的段（末次活动距今超过切段阈值 SEGMENT_GAP），开放段不
提前结账。开关 watch_fallback_credit_enabled（SystemConfig，默认
开）关时整轮跳过且不消费任何段，重新打开后旧段照常结账（超过
STALE_DROP 的陈年段直接丢弃，防开关反复后补记远古账）。
"""

import json
import logging
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from backend.emby_server import models as em
from backend.emby_server.watch_activity import SEGMENT_GAP, _dump_pending

logger = logging.getLogger(__name__)

#: worker_registry 登记名。
WORKER_NAME = "watch_fallback_credit"

#: 工人轮间隔（30 分钟）：只结已静默收尾的段，延迟无感。
ROUND_INTERVAL_SEC = 1800.0

#: 起步门槛：单段（扣除真实会话覆盖后的碎片）不足此分钟数不入账。
MIN_SEGMENT_MINUTES = 2.0

#: 每用户每日兜底入账上限（分钟）。
DAILY_FALLBACK_CAP_MINUTES = 240.0

#: 陈年段丢弃线：段末距今超过它不再补记（开关反复/工人停摆后防
#: 补记远古账目）。
STALE_DROP = timedelta(days=7)

#: item_baselines 里记录兜底分钟的保留键（来源标记）。
FALLBACK_BASELINE_KEY = "__fallback__"

#: 一轮最多处理的活动行数（防积压时拖垮工人；剩余下轮继续）。
_MAX_ROWS_PER_ROUND = 500


def fallback_enabled(db) -> bool:
    """总开关 watch_fallback_credit_enabled：默认开。"""
    from backend.integrations import store

    raw = store.get_value(db, "watch_fallback_credit_enabled", "")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() == "true"


# ---------------------------------------------------------------------------
# 段重建与入账
# ---------------------------------------------------------------------------

def _subtract_intervals(start, end, covers):
    """从 [start, end] 里扣掉 covers（已排序的 (s, e) 列表），
    返回剩余碎片 [(s, e), ...]。"""
    frags = [(start, end)]
    for cs, ce in covers:
        nxt = []
        for s, e in frags:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        frags = nxt
        if not frags:
            break
    return frags


def _session_covers(db, user_id: int, item_id: int, start, end):
    """同用户同条目、与 [start, end] 相交的真实会话时间窗。"""
    rows = (db.query(em.PlaybackSession)
            .filter(em.PlaybackSession.user_id == user_id,
                    em.PlaybackSession.item_id == item_id,
                    em.PlaybackSession.start_time < end)
            .all())
    covers = []
    for sess in rows:
        s_end = sess.ended_at or sess.last_update_at or sess.start_time
        if s_end is not None and s_end > start:
            covers.append((sess.start_time, s_end))
    covers.sort()
    return covers


def _daily_row(db, user_id: int, day: str):
    from backend import models as web_models

    return (db.query(web_models.WatchPointsDaily)
            .filter(web_models.WatchPointsDaily.user_id == user_id,
                    web_models.WatchPointsDaily.day == day)
            .first())


def _baselines_of(daily) -> dict:
    if daily is None:
        return {}
    try:
        data = json.loads(daily.item_baselines or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _credit_segment(db, user_id: int, item_id: int, start, end,
                    day: str) -> float:
    """一段过门并入账，返回实际入账分钟（0 = 被门拦下/无真空）。"""
    from backend import vitality, watch_points

    raw_minutes = (end - start).total_seconds() / 60.0
    if raw_minutes < MIN_SEGMENT_MINUTES:
        return 0.0
    frags = _subtract_intervals(
        start, end, _session_covers(db, user_id, item_id, start, end))
    creditable = sum(
        (e - s).total_seconds() / 60.0 for s, e in frags
        if (e - s).total_seconds() / 60.0 >= MIN_SEGMENT_MINUTES)
    if creditable <= 0:
        return 0.0
    item = db.query(em.MediaItem).filter(em.MediaItem.id == item_id).first()
    if item is not None and (item.duration_ticks or 0) > 0:
        creditable = min(creditable,
                         item.duration_ticks / 600_000_000.0)
    daily = _daily_row(db, user_id, day)
    baselines = _baselines_of(daily)
    used = float(baselines.get(FALLBACK_BASELINE_KEY) or 0)
    allowed = min(creditable, DAILY_FALLBACK_CAP_MINUTES - used)
    if allowed <= 0:
        return 0.0
    baseline = float(baselines.get(str(item_id)) or 0)
    # 与真实会话同一套入账函数：活力按单次分钟分档；观影能量传
    # 「基线 + 应补」使贡献恰为 allowed（见模块头说明）。
    vitality.award_watch_minutes_reward(db, user_id, allowed)
    watch_points.award_watch_points(
        db, user_id, baseline + allowed, item_id, finished=False)
    # 来源标记：把兜底分钟记进同一本账的保留键（award 已提交，
    # 这里重读行再写回并提交；行不存在＝非公益用户无账可标）。
    daily = _daily_row(db, user_id, day)
    if daily is not None:
        baselines = _baselines_of(daily)
        baselines[FALLBACK_BASELINE_KEY] = (
            float(baselines.get(FALLBACK_BASELINE_KEY) or 0) + allowed)
        daily.item_baselines = json.dumps(baselines)
        db.commit()
    logger.info(
        "watch-fallback credit user=%s item=%s minutes=%.1f "
        "segment=%s..%s", user_id, item_id, allowed,
        start.isoformat(), end.isoformat())
    return allowed


def settle_once(db, now: Optional[datetime] = None) -> dict:
    """结账一轮：消费全部已闭合段。返回统计 dict（测试/日志用）。"""
    stats = {"ran": False, "reason": None, "credited_segments": 0,
             "credited_minutes": 0.0, "skipped_segments": 0}
    if not fallback_enabled(db):
        stats["reason"] = "disabled"
        return stats
    stats["ran"] = True
    now = now or datetime.now()
    day = now.date().isoformat()
    close_cutoff = now - SEGMENT_GAP
    stale_cutoff = now - STALE_DROP
    rows = (db.query(em.WatchStreamActivity)
            .filter((em.WatchStreamActivity.pending_segments != "[]")
                    | ((em.WatchStreamActivity.last_activity_at != None)  # noqa: E711
                       & (em.WatchStreamActivity.last_activity_at
                          <= close_cutoff)))
            .order_by(em.WatchStreamActivity.id)
            .limit(_MAX_ROWS_PER_ROUND)
            .all())
    for row in rows:
        from backend.emby_server.watch_activity import _load_pending_pairs

        segments = _load_pending_pairs(row)
        current_closed = (
            row.first_activity_at is not None
            and row.last_activity_at is not None
            and row.last_activity_at <= close_cutoff)
        if current_closed:
            segments.append((row.first_activity_at, row.last_activity_at))
        if not segments:
            continue
        settled_through = row.credited_through
        for start, end in segments:
            if end < stale_cutoff:
                stats["skipped_segments"] += 1
                continue
            try:
                credited = _credit_segment(
                    db, row.user_id, row.item_id, start, end, day)
            except Exception:  # noqa: BLE001 — 单段失败隔离，段照常消费防毒丸
                logger.warning(
                    "watch-fallback 单段结账失败 user=%s item=%s "
                    "segment=%s..%s", row.user_id, row.item_id,
                    start, end, exc_info=True)
                db.rollback()
                credited = 0.0
            if credited > 0:
                stats["credited_segments"] += 1
                stats["credited_minutes"] += credited
            else:
                stats["skipped_segments"] += 1
            if settled_through is None or end > settled_through:
                settled_through = end
        row.pending_segments = _dump_pending([])
        if current_closed:
            row.first_activity_at = None
            row.last_activity_at = None
        row.credited_through = settled_through
        row.updated_at = datetime.now()
        db.commit()
    return stats


# ---------------------------------------------------------------------------
# 工人线程（照工人家族 start/stop/registry 形状）
# ---------------------------------------------------------------------------

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_start_lock = threading.Lock()
_last_round: dict = {}
_last_lock = threading.Lock()


def runtime_status() -> dict:
    with _last_lock:
        last = dict(_last_round) if _last_round else None
    thread = _thread
    return {
        "running": bool(thread is not None and thread.is_alive()),
        "last_round": last,
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _loop():
    from backend.database import SessionLocal

    logger.info("中继兜底计时 worker 启动（间隔 %.0f 秒）",
                ROUND_INTERVAL_SEC)
    _stop_event.wait(random.uniform(5.0, 30.0))
    while not _stop_event.is_set():
        started = time.monotonic()
        try:
            db = SessionLocal()
            try:
                stats = settle_once(db)
            finally:
                db.close()
            stats["finished_at"] = datetime.now().isoformat(
                timespec="seconds")
            with _last_lock:
                _last_round.clear()
                _last_round.update(stats)
            if stats.get("credited_segments"):
                logger.info(
                    "中继兜底计时一轮：入账 %d 段 / %.1f 分钟，"
                    "跳过 %d 段", stats["credited_segments"],
                    stats["credited_minutes"], stats["skipped_segments"])
        except Exception as exc:  # noqa: BLE001 — 循环体异常不许退出
            logger.warning("中继兜底计时一轮异常: %s", exc, exc_info=True)
        finally:
            try:
                from backend.emby_server import worker_registry as _wr

                _wr.heartbeat(WORKER_NAME)
            except Exception:  # noqa: BLE001
                pass
        elapsed = time.monotonic() - started
        _stop_event.wait(max(60.0, ROUND_INTERVAL_SEC - elapsed))


def start() -> bool:
    """启动兜底结账后台线程（幂等）。线程常驻、轮内现读开关：
    开关默认开，关时每轮空转，管理员改配置后下一轮自动生效。"""
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop, name="watch-fallback-credit", daemon=True)
        _thread.start()
        from backend.emby_server import worker_registry as _wr

        _wr.register(WORKER_NAME, _thread)
        return True


def stop():
    _stop_event.set()
