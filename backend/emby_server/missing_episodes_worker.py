# -*- coding: utf-8 -*-
"""缺集核算工人（缺集全套，2026-11-10 用户亲点）。

为什么有这个工人
----------------
站内剧集常常缺集：源盘收藏本身不全，入库时照实收下，缺哪几集
此前只能靠人工普查（2026-11-09 手查过一轮：5,591 部有集的剧里
947 部缺第一集）。本工人把这件事变成常态核算：周期性地拿每部
剧在 TMDB 上的集表跟盘里实际有的集对表，缺集落台账
（emby_missing_episode_records），供管理端缺集清单与许愿池
「求补这些集」共用同一份事实。

口径（不许漂移）
----------------
- 应有集＝TMDB 已播出的集：走现成 tmdb_client 单例的 details
  （拿季表）＋ season_episodes（每集 air_date），air_date ≤ 今天
  （Asia/Shanghai）才算应有；还没播的不算缺。第 0 季（特别篇）
  默认不算，MISSING_EPISODES_INCLUDE_SPECIALS 打开才算。
- 实有集＝该 series 名下可见集行（deleted_at IS NULL AND
  merged_into_id IS NULL）的 (season_number, episode_number)
  集合；集号为空的行不计实有、也不单独报缺。
- TMDB 集表走 tmdb_client 自带的两级缓存（L1 进程内＋L2 磁盘，
  details 口径 30 天 TTL），叠加工人自己的台账节流：同一部剧
  最多每 MISSING_EPISODES_REFRESH_DAYS 天（默认 7）重算一次。
  单轮 TMDB 调用数有预算硬帽（默认 400，含缓存命中的调用也计
  ——保守口径，宁可少算几部也不许超）。
- TMDB 失败/无数据（details 为空、任一季取不到、瞬态异常）→
  整部跳过、下轮再试，绝不把失败算成「全缺」；预算中途耗尽
  同样整部放弃、不落半截台账。
- 无 tmdb_id 的剧跳过，但每轮统计「跳过无 TMDB 号 N 部」进
  工人状态，管理员心里有数。
- 补齐闭环：核算发现某剧从 open 变不缺 → 台账置 resolved，
  并把该剧未完结的补缺集求片置 completed、走现成到货通知收口
  （tg_bot.proactive.note_store_request_completed）；部分补齐
  只刷新求片上的缺集快照，不发通知。剧被删/合并/丢了 TMDB 号
  → open 台账行置 resolved（失效处理，不删行）。

资源纪律（照 drive_warm_worker 同口径）：总开关默认开（站里
没用户时开关默认开的既定口径）、单轮双预算（时长＋TMDB 调用
数）、任务间随机抖动、起播繁忙或系统高负载整轮让路、失败只
记账不冒泡、线程异常不退出循环。
"""

import json
import logging
import os
import random
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func

from backend.emby_server import models as em
from backend.emby_server.env_util import env_float, env_int

logger = logging.getLogger(__name__)

#: worker_registry 登记名（/api/health 与管理总览按此名认人）。
WORKER_NAME = "missing_episodes"

#: 运行状态发布缓存键（照 drive_warm_worker 的形状）。
STATUS_CACHE_KEY = "aetrix:missing_episodes:status"
_STATUS_TTL_SEC = 1800

_SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# 配置（全部 env 热读：改配置不用改代码，脏值回默认）
# ---------------------------------------------------------------------------

def _env_on(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def missing_episodes_config() -> dict:
    """缺集核算工人有效配置（每轮热读一次）。

    键名与 env.example 一一对应。总开关默认开（既定口径：站里
    没用户时开关默认开），但工人限速、错峰、带预算与让路。
    """
    return {
        # 总开关（关 = 工人不启动、在跑的整轮让路退出）
        "enabled": _env_on("MISSING_EPISODES_ENABLED", True),
        # 轮间隔（秒）：核算本身有 7 天台账节流，轮询勤一点只是
        # 让新入库的剧早点被捡到，不会多打 TMDB
        "interval_sec": env_float(
            "MISSING_EPISODES_INTERVAL_SEC", 3600.0, 60.0, 86400.0),
        # 同一部剧两次核算的最小间隔（天）
        "refresh_days": env_float(
            "MISSING_EPISODES_REFRESH_DAYS", 7.0, 0.0, 90.0),
        # 单轮最多核算多少部剧
        "max_series_per_round": env_int(
            "MISSING_EPISODES_MAX_SERIES_PER_ROUND", 200, 1, 5000),
        # 单轮 TMDB 调用预算（details＋season_episodes 每次调用
        # 计 1，含缓存命中——保守硬帽，默认量级照 drive_warm）
        "max_tmdb_calls": env_int(
            "MISSING_EPISODES_MAX_TMDB_CALLS_PER_ROUND", 400, 1, 100000),
        # 单轮总时长预算（秒）：到点不再开新剧
        "round_budget_sec": env_float(
            "MISSING_EPISODES_ROUND_BUDGET_SEC", 600.0, 10.0, 7200.0),
        # 第 0 季（特别篇）算不算缺集（默认不算）
        "include_specials": _env_on(
            "MISSING_EPISODES_INCLUDE_SPECIALS", False),
        # 每部剧核算之间的随机抖动上限（秒），反风控
        "jitter_sec": env_float(
            "MISSING_EPISODES_JITTER_SEC", 2.0, 0.0, 60.0),
        # 让路判据一：近 5 分钟起播数 >= 该值 → 整轮跳过
        "busy_plays_5min": env_int(
            "MISSING_EPISODES_BUSY_PLAYS_5MIN", 30, 1, 100000),
        # 让路判据二：1 分钟负载 / CPU 数 > 该值 → 跳过/停轮
        "load_ratio": env_float(
            "MISSING_EPISODES_LOAD_RATIO", 0.85, 0.1, 10.0),
    }


# ---------------------------------------------------------------------------
# 让路判据（照 drive_warm_worker 同口径实现）
# ---------------------------------------------------------------------------

def _load_ratio() -> float:
    """1 分钟负载 / CPU 数（取不到按 0：不许因测不准饿死工人）。"""
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 0.0
    return load1 / float(os.cpu_count() or 1)


def _recent_play_starts(db, minutes, now=None) -> int:
    """近 N 分钟全站起播数（让路判据；读失败按 0）。"""
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=minutes)
    try:
        return int(
            db.query(func.count(em.PlayStartEvent.id))
            .filter(em.PlayStartEvent.created_at >= cutoff)
            .scalar() or 0)
    except Exception:  # noqa: BLE001 — 判据读不到不拦核算
        return 0


# ---------------------------------------------------------------------------
# TMDB 应有集（走现成 tmdb_client 单例，不新造客户端）
# ---------------------------------------------------------------------------

class _BudgetExhausted(Exception):
    """单轮 TMDB 调用预算耗尽（整部放弃，下轮再来）。"""


class _CallBudget:
    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self.count = 0

    def take(self) -> None:
        if self.count >= self.limit:
            raise _BudgetExhausted()
        self.count += 1


def _tmdb_client():
    from backend.emby_server.tmdb import tmdb_client

    return tmdb_client


def _parse_air_date(raw) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _expected_episodes(tmdb_id, cfg, budget: _CallBudget,
                       today: date) -> Optional[dict]:
    """TMDB 应有集 ``{季号: {已播出集号}}``；拿不全返回 None。

    任一季取不到（None/瞬态异常）→ 整部 None（跳过、下轮再试），
    绝不拿半截集表算缺集。预算耗尽抛 _BudgetExhausted。
    """
    from backend.emby_server.tmdb import TmdbTransientError

    client = _tmdb_client()
    budget.take()
    try:
        details = client.details(str(tmdb_id), "series")
    except TmdbTransientError:
        return None
    if not isinstance(details, dict):
        return None
    seasons = details.get("seasons")
    if not isinstance(seasons, list):
        return None
    expected: dict[int, set] = {}
    for entry in seasons:
        if not isinstance(entry, dict):
            continue
        try:
            sn = int(entry.get("season_number"))
        except (TypeError, ValueError):
            continue
        if sn < 0:
            continue
        if sn == 0 and not cfg["include_specials"]:
            continue
        budget.take()
        try:
            eps = client.season_episodes(str(tmdb_id), sn)
        except TmdbTransientError:
            return None
        if eps is None:
            return None
        aired = set()
        for ep in eps:
            if not isinstance(ep, dict):
                continue
            try:
                en = int(ep.get("episode_number"))
            except (TypeError, ValueError):
                continue
            air = _parse_air_date(ep.get("air_date"))
            if air is None or air > today:
                continue  # 没播出日/还没播：不算应有，不报缺
            aired.add(en)
        expected[sn] = aired
    return expected


# ---------------------------------------------------------------------------
# 实有集与对表
# ---------------------------------------------------------------------------

def _actual_episodes(db, series_id) -> dict:
    """实有集 ``{季号: {集号}}``（可见集行；集号为空不计）。"""
    rows = (
        db.query(em.MediaItem.season_number, em.MediaItem.episode_number)
        .filter(
            em.MediaItem.item_type == "episode",
            em.MediaItem.series_id == series_id,
            em.MediaItem.deleted_at.is_(None),
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.season_number.isnot(None),
            em.MediaItem.episode_number.isnot(None),
        )
        .all())
    actual: dict[int, set] = {}
    for sn, en in rows:
        actual.setdefault(int(sn), set()).add(int(en))
    return actual


def compute_missing(expected: dict, actual: dict) -> dict:
    """应有 − 实有 → 规范缺集明细 ``{季号: [缺的集号升序]}``。"""
    missing = {}
    for sn, eps in expected.items():
        lack = sorted(set(eps) - actual.get(sn, set()))
        if lack:
            missing[int(sn)] = lack
    return missing


# ---------------------------------------------------------------------------
# 台账写入与求片闭环
# ---------------------------------------------------------------------------

def _active_missing_requests(db, series):
    """该剧未完结的补缺集求片行（按快照里的 series_item_id 核对，
    tmdb 撞号时不误伤别的剧行；老快照没带 id 时按 tmdb 认）。"""
    from backend import media_seek, missing_episodes as me
    from backend import models as web_models

    rows = (
        db.query(web_models.MovieRequest)
        .filter(
            web_models.MovieRequest.request_kind == me.KIND_MISSING,
            web_models.MovieRequest.tmdb_id == str(series.tmdb_id or ""),
            web_models.MovieRequest.status.in_(media_seek.ACTIVE_STATUSES),
        )
        .all())
    out = []
    for row in rows:
        snap_id = me.parse_request_detail(
            row.request_detail).get("series_item_id")
        if snap_id is None or int(snap_id) == int(series.id):
            out.append(row)
    return out


def _refresh_request_snapshots(db, series, missing, now) -> None:
    """部分补齐/缺集变化：只刷新求片快照，不发通知。"""
    from backend import missing_episodes as me

    for row in _active_missing_requests(db, series):
        row.request_detail = me.build_request_detail(
            series.id, missing, checked_at=now)
        row.updated_at = now


def _close_requests(db, series, now) -> int:
    """全部补齐：未完结的补缺集求片置 completed 并走到货通知收口。"""
    from backend.tg_bot import proactive

    closed = 0
    for row in _active_missing_requests(db, series):
        row.status = "completed"
        row.updated_at = now
        closed += 1
        try:
            proactive.note_store_request_completed(db, row)
        except Exception:  # noqa: BLE001 — 通知入队失败不回滚补齐结论
            logger.warning(
                "补缺集到货通知入队失败 request=%s", row.id, exc_info=True)
    return closed


def _apply_result(db, series, missing, now, stats) -> None:
    """把一部剧的核算结论落台账，并驱动求片快照/完成闭环。"""
    from backend import missing_episodes as me

    record = (
        db.query(em.MissingEpisodeRecord)
        .filter(em.MissingEpisodeRecord.series_item_id == series.id)
        .first())
    count = me.missing_count(missing)
    if record is None:
        record = em.MissingEpisodeRecord(series_item_id=series.id)
        db.add(record)
    record.tmdb_id = str(series.tmdb_id or "")
    record.series_name = series.name or ""
    record.library_id = series.library_id
    record.last_checked_at = now
    if count > 0:
        was_open = record.status == "open" and (record.missing_count or 0) > 0
        if not was_open:
            record.first_detected_at = now
            record.resolved_at = None
            stats["opened"] += 1
        else:
            stats["kept_open"] += 1
        record.status = "open"
        record.missing_detail = me.missing_detail_to_json(missing)
        record.missing_count = count
        _refresh_request_snapshots(db, series, missing, now)
    else:
        was_open = record.status == "open" and (record.missing_count or 0) > 0
        record.status = "resolved"
        record.missing_detail = "{}"
        record.missing_count = 0
        if was_open:
            record.resolved_at = now
            stats["resolved"] += 1
            stats["requests_closed"] += _close_requests(db, series, now)
        else:
            stats["clean"] += 1


# ---------------------------------------------------------------------------
# 一轮核算
# ---------------------------------------------------------------------------

def _visible_series(db):
    """可见剧行（ORM 口径：未软删、未被合并）。"""
    return (
        db.query(em.MediaItem)
        .filter(
            em.MediaItem.item_type == "series",
            em.MediaItem.deleted_at.is_(None),
            em.MediaItem.merged_into_id.is_(None),
        )
        .all())


def _due_series(db, cfg, now):
    """到期待核算的剧（带 tmdb）＋无 tmdb 跳过数。

    从没核算过的优先，其次按 last_checked_at 最久没算的在前；
    单轮上限在返回前截断。
    """
    horizon = now - timedelta(days=cfg["refresh_days"])
    series_rows = _visible_series(db)
    records = {
        r.series_item_id: r
        for r in db.query(em.MissingEpisodeRecord).all()
    }
    skipped_no_tmdb = 0
    due = []
    for series in series_rows:
        if not (series.tmdb_id or "").strip():
            skipped_no_tmdb += 1
            continue
        record = records.get(series.id)
        if record is None:
            due.append((0, datetime.min, series))
        elif (record.last_checked_at is None
                or record.last_checked_at <= horizon):
            due.append((1, record.last_checked_at or datetime.min, series))
    due.sort(key=lambda t: (t[0], t[1], t[2].id))
    picked = [s for _rank, _ts, s in due[: cfg["max_series_per_round"]]]
    return picked, skipped_no_tmdb


def _invalidate_gone(db, visible_ids, now) -> int:
    """剧已不可见（删/合并/丢号）→ open 台账行置 resolved 留痕。"""
    rows = (
        db.query(em.MissingEpisodeRecord)
        .filter(em.MissingEpisodeRecord.status == "open")
        .all())
    n = 0
    for record in rows:
        if record.series_item_id not in visible_ids:
            record.status = "resolved"
            record.resolved_at = now
            n += 1
    return n


def _default_session_factory():
    from backend.database import SessionLocal

    return SessionLocal


_last_round: dict = {}
_last_lock = threading.Lock()


def _today_shanghai() -> date:
    return datetime.now(_SHANGHAI).date()


def audit_once(session_factory=None, cfg=None, today=None) -> dict:
    """跑一轮缺集核算，返回本轮统计（任何情况下都返回，不抛）。

    让路顺序：总开关 → 系统负载 → 起播繁忙 → 逐部核算（轮中
    时长/TMDB 双预算守恒）。每部剧核算完单独提交：一部坏数据
    不许拖垮整轮。
    """
    cfg = cfg or missing_episodes_config()
    today = today or _today_shanghai()
    started = time.monotonic()
    stats = {
        "ran": False, "reason": None,
        "checked": 0, "opened": 0, "kept_open": 0, "resolved": 0,
        "clean": 0, "tmdb_skipped": 0, "skipped_no_tmdb": 0,
        "invalidated": 0, "requests_closed": 0, "tmdb_calls": 0,
        "stopped": None,
        "elapsed_sec": 0.0,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
    }
    try:
        if not cfg["enabled"]:
            stats["reason"] = "disabled"
            return stats
        if _load_ratio() > cfg["load_ratio"]:
            stats["reason"] = "load"
            return stats
        factory = session_factory or _default_session_factory()
        db = factory()
        try:
            if _recent_play_starts(db, 5) >= cfg["busy_plays_5min"]:
                stats["reason"] = "busy"
                return stats
            now = datetime.now()
            due, skipped_no_tmdb = _due_series(db, cfg, now)
            stats["skipped_no_tmdb"] = skipped_no_tmdb
            budget = _CallBudget(cfg["max_tmdb_calls"])
            for series in due:
                if (time.monotonic() - started
                        > cfg["round_budget_sec"]):
                    stats["stopped"] = "round_budget"
                    break
                try:
                    expected = _expected_episodes(
                        series.tmdb_id, cfg, budget, today)
                except _BudgetExhausted:
                    stats["stopped"] = "tmdb_budget"
                    break
                if expected is None:
                    stats["tmdb_skipped"] += 1
                    continue
                try:
                    actual = _actual_episodes(db, series.id)
                    missing = compute_missing(expected, actual)
                    _apply_result(db, series, missing, now, stats)
                    db.commit()
                except Exception:  # noqa: BLE001 — 单部失败不拖垮整轮
                    db.rollback()
                    logger.warning(
                        "缺集核算单部失败 series=%s", series.id,
                        exc_info=True)
                    stats["tmdb_skipped"] += 1
                    continue
                stats["checked"] += 1
                if cfg["jitter_sec"] > 0:
                    time.sleep(random.uniform(0, cfg["jitter_sec"]))
            stats["tmdb_calls"] = budget.count
            visible_ids = {
                s.id for s in _visible_series(db)
                if (s.tmdb_id or "").strip()}
            stats["invalidated"] = _invalidate_gone(db, visible_ids, now)
            db.commit()
        finally:
            db.close()
        stats["ran"] = True
        return stats
    finally:
        _finish_round(stats, started)


# ---------------------------------------------------------------------------
# 状态上报（照 drive_warm_worker 形状：进程内最近轮 + 缓存发布跨进程读）
# ---------------------------------------------------------------------------

def runtime_status() -> dict:
    with _last_lock:
        last = dict(_last_round) if _last_round else None
    thread = _audit_thread
    return {
        "running": bool(thread is not None and thread.is_alive()),
        "enabled": missing_episodes_config()["enabled"],
        "last_round": last,
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _finish_round(stats, started_monotonic):
    stats["elapsed_sec"] = round(time.monotonic() - started_monotonic, 2)
    stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
    with _last_lock:
        _last_round.clear()
        _last_round.update(stats)
    _publish_status()


def _publish_status() -> None:
    try:
        from backend.database import CacheManager

        CacheManager.set(
            STATUS_CACHE_KEY,
            json.dumps(runtime_status(), ensure_ascii=False, default=str),
            ttl=_STATUS_TTL_SEC)
    except Exception:  # noqa: BLE001 — 状态发布失败不影响核算本体
        pass


def status_snapshot() -> dict:
    """管理总览读口：本进程在跑给本地状态，否则读工人发布的状态。"""
    thread = _audit_thread
    if thread is not None and thread.is_alive():
        snap = runtime_status()
        snap["source"] = "local"
        return snap
    try:
        from backend.database import CacheManager

        raw = CacheManager.get(STATUS_CACHE_KEY)
        if raw:
            snap = json.loads(raw)
            snap["source"] = "worker"
            return snap
    except Exception:  # noqa: BLE001 — 读不到就回本地空状态
        pass
    snap = runtime_status()
    snap["source"] = "none"
    return snap


# ---------------------------------------------------------------------------
# 工人线程（照 drive_warm_worker 的 start/stop/registry 形状）
# ---------------------------------------------------------------------------

_audit_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_start_lock = threading.Lock()


def _audit_loop():
    logger.info("缺集核算 worker 启动（间隔 %.0f 秒）",
                missing_episodes_config()["interval_sec"])
    while not _stop_event.is_set():
        try:
            stats = audit_once()
            if stats.get("ran"):
                logger.info(
                    "缺集核算一轮：核算 %d，新发现 %d，仍缺 %d，"
                    "补齐 %d，干净 %d，TMDB 跳过 %d，无号跳过 %d，"
                    "失效 %d，TMDB 调用 %d，耗时 %.1fs",
                    stats["checked"], stats["opened"], stats["kept_open"],
                    stats["resolved"], stats["clean"],
                    stats["tmdb_skipped"], stats["skipped_no_tmdb"],
                    stats["invalidated"], stats["tmdb_calls"],
                    stats["elapsed_sec"])
            elif stats.get("reason") not in (None, "disabled"):
                logger.info("缺集核算本轮让路：%s", stats["reason"])
        except Exception as exc:  # noqa: BLE001 — 循环体异常不许退出
            logger.warning("缺集核算一轮异常: %s", exc, exc_info=True)
        finally:
            try:
                from backend.emby_server import worker_registry as _wr

                _wr.heartbeat(WORKER_NAME)
            except Exception:  # noqa: BLE001
                pass
        _stop_event.wait(missing_episodes_config()["interval_sec"])


def start() -> bool:
    """启动缺集核算后台线程（幂等；总开关关时不启动）。"""
    global _audit_thread
    if not missing_episodes_config()["enabled"]:
        logger.info("缺集核算已禁用（MISSING_EPISODES_ENABLED=0）")
        return False
    with _start_lock:
        if _audit_thread is not None and _audit_thread.is_alive():
            return True
        _stop_event.clear()
        _audit_thread = threading.Thread(
            target=_audit_loop, name="missing-episodes", daemon=True)
        _audit_thread.start()
        from backend.emby_server import worker_registry as _wr

        _wr.register(WORKER_NAME, _audit_thread)
        return True


def stop():
    _stop_event.set()
