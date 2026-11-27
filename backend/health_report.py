"""后台健康检查：把「真的出事了」反映到 status 上。

## 为什么要做

``/health`` 以前硬编码返回 ``status="healthy"``——不管数据库断开、Redis 挂了、
worker 刷几千条探测失败、扫描队列堆积，前台「服务健康」页一律显示正常。

这不是"少一个指标"，而是**看板在最需要的时候失明**：出问题时没人能从页面看到，
只能去翻日志。生产上刮削链整条挂掉（series 全部搜不到）而页面一片绿，
就是这么发生的。

## 口径

按严重度分三档，让页面上能一眼看出该不该慌：

- ``ok``      一切正常
- ``warn``   降级但还能用：磁盘吃紧、Redis 不可用（有本地回退）、探测/刮削失败率高
- ``down``    核心不可用：数据库不通、补全积压且最近窗口内没有任何完成

**刻意不纳入**：单条条目的刮削失败不算 warn（TMDB 对中文剧集收录差是常态，
几百条 failed 属于正常业务状态，报出来只会天天误警）。只统计**失败率**与
**堆积量**，这两者才代表系统出了问题。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from datetime import datetime, timedelta

from sqlalchemy import func
from backend.emby_server.env_util import env_float, env_int

logger = logging.getLogger(__name__)

from backend.error_lexicon import translate as _translate_error

# 阈值：经验值，按公测规模调过
WARN_DISK_FREE_PERCENT = 15.0
WARN_PROBE_FAIL_RATIO = 0.30        # 探测失败率超过 30%
WARN_ENRICH_FAIL_RATIO = 0.30       # 刮削失败率超过 30%
# 积压达到此数才进入堆积判定。有进展（见下）只降级，不报 down。
DOWN_QUEUE_BACKLOG = env_int("HEALTH_ENRICH_BACKLOG_FLOOR", 500, 1)
# 最近这么多分钟内有完成条目 = 有进展。判据以 worker 发布的 Redis 快照
# 为准（跨进程完成证据，见 collect 内 L-009 说明）；库里的 last_scraped_at
# 只作旁证（复刮完成不更新它），本进程计数器只在单进程部署时有意义
# （分离部署时 API 进程恒为 0）。
HEALTH_ENRICH_PROGRESS_MIN = env_int("HEALTH_ENRICH_PROGRESS_MIN", 10, 1)


def _count(db, item_type: str, status: str) -> int:
    try:
        # 假数字修复（2026-10-19 系统清点 F7）：只数真实条目——合并副本
        # （merged_into_id 非空）按设计不参与探测，与同文件 _enrich_count 对齐。
        # C-02（2026-11-28）：计数走 item_status_counts 共享分布（同口径、
        # 短 TTL 缓存），不再每次健康检查现算。
        from backend.emby_server import item_status_counts
        by_type = item_status_counts.probe_counts_by_type(db)
        return int((by_type.get(item_type) or {}).get(status, 0))
    except Exception as exc:  # noqa: BLE001 — 统计失败不该让健康检查 500
        logger.debug("统计 probe %s 失败: %s", status, exc)
        return 0


def _enrich_count(db, status: str) -> int:
    try:
        # 只数真实条目：合并副本（merged_into_id 非空）按设计不参与补全
        # （母条目补完即可），把它们算进来会让积压常年虚高几万条、
        # 健康灯长红（2026-10-16 实测：真实 pending 2 条、副本 pending 72167 条）。
        # C-02（2026-11-28）：计数走 item_status_counts 共享分布（同口径、
        # 短 TTL 缓存），不再每次健康检查现算。
        from backend.emby_server import item_status_counts
        return int(item_status_counts.enrich_status_counts(db).get(status, 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计 enrich %s 失败: %s", status, exc)
        return 0


# S8：worker 阶段耗时快照的 Redis 键（与 enrich_worker 侧对齐，janitor
# 每轮发布）；api 进程的 scan_progress 是本进程内存、恒空，故回退读它。
ENRICH_STAGE_STATS_REDIS_KEY = "aetrix:enrich:stage_stats"
ENRICH_STAGE_STATS_MAX_AGE_SEC = 180


def _enrich_worker_snapshot() -> dict | None:
    """读 worker 发布的 Redis 快照（S8 通道），只采信新鲜快照。

    worker janitor 每轮把本进程 ``stage_stats()`` + ``throughput()``
    序列化发布到 ``ENRICH_STAGE_STATS_REDIS_KEY``（见 enrich_worker
    ``_publish_stage_stats_to_redis``）。返回解析后的 payload
    （含 ts/stages/throughput）；未配置 Redis、键缺失、已过期
    （> ENRICH_STAGE_STATS_MAX_AGE_SEC）或解析失败一律返回 None
    ——即「跨进程证据不可得」，调用方不许据此下停滞结论。
    """
    try:
        import json as _json

        from backend import database as _dbmod
        client = _dbmod.redis_client
        if client is None:
            return None
        raw = client.get(ENRICH_STAGE_STATS_REDIS_KEY)
        if not raw:
            return None
        payload = _json.loads(raw)
        ts = float(payload.get("ts") or 0)
        if time.time() - ts > ENRICH_STAGE_STATS_MAX_AGE_SEC:
            return None
        return payload
    except Exception:  # noqa: BLE001 — 快照读不到就是无证据，不让健康检查自己抛
        return None


def _enrich_stage_stats() -> dict:
    """enrich 阶段耗时：本进程统计非空用本进程；为空时回退 Redis 快照。

    worker 与 api 分离部署时阶段统计只存在于 worker 进程内存里，
    health 长期读到空（B6 观测缺口）；快照读取走 _enrich_worker_snapshot
    （与进展判定同一通道、同一新鲜度口径）。
    """
    try:
        from backend.emby_server import scan_progress as _progress
        local = _progress.stage_stats()
    except Exception:  # noqa: BLE001 — 本进程统计拿不到就走回退
        local = {}
    if local:
        return local
    payload = _enrich_worker_snapshot()
    if not payload:
        return {}
    stages = payload.get("stages")
    return stages if isinstance(stages, dict) else {}


def _enrich_snapshot_progress(payload: dict, window_min: int) -> str:
    """按 worker 快照判进展：``progressing`` / ``stalled`` / ``unknown``。

    - 快照显示窗口内有完成（``done_per_min`` > 0，或 ``last_done_at``
      落在窗口内）→ progressing；
    - 快照新鲜（worker 进程活着、持续在发布）但上次完成已在窗口外
      → stalled：这是「真停滞」的跨进程证据，可以据此报 down；
    - 从未有完成记录（worker 刚启动）或时间戳不可解析 → unknown：
      没有证据，不下结论。
    """
    tp = (payload or {}).get("throughput") or {}
    try:
        if float(tp.get("done_per_min") or 0) > 0:
            return "progressing"
    except (TypeError, ValueError):
        pass
    last = tp.get("last_done_at")
    if not last:
        return "unknown"
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return "unknown"
    if datetime.now() - last_dt <= timedelta(minutes=max(1, int(window_min))):
        return "progressing"
    return "stalled"


def _enrich_recent_completed(db, minutes: int) -> int:
    """最近 ``minutes`` 分钟内完成的可见条目数（done 且 last_scraped_at 落在窗口内）。

    与 ``_enrich_count`` 同口径：排除合并副本。软删由 ORM 钩子排除。
    查询失败返回 0，调用方再看进程内计数器，不让健康检查自己抛。
    R-01：请求的窗口与共享快照窗口（``HEALTH_ENRICH_PROGRESS_MIN``，
    默认 10 分钟）一致时走 item_status_counts 共享快照，值最多滞后
    一个 TTL（300 秒）；窗口不一致时仍直接现算，口径不变。
    """
    try:
        from backend.emby_server import item_status_counts
        if int(minutes) == item_status_counts.recent_done_window_minutes():
            return int(item_status_counts.enrich_recent_done(db))
    except Exception as exc:  # noqa: BLE001 — 快照不可用时回退直接现算
        logger.debug("近期完成数快照读取失败，回退直接现算: %s", exc)
    from backend.emby_server import models as em
    cutoff = datetime.now() - timedelta(minutes=max(1, int(minutes)))
    try:
        return db.query(func.count(em.MediaItem.id)).filter(
            em.MediaItem.enrich_status == "done",
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.last_scraped_at.isnot(None),
            em.MediaItem.last_scraped_at >= cutoff,
        ).scalar() or 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计近窗口完成数失败: %s", exc)
        return 0


def library_scan_summary(db) -> dict:
    """各媒体库最近一轮扫描的结果概览（扫描失败的库一眼可见）"""
    from backend.emby_server import models as em
    out = {
        "libraries_total": 0,
        "scanning": 0,
        "failed": 0,
        "interrupted": 0,
        "never_scanned": 0,
        "last_failed": [],
    }
    try:
        rows = db.query(em.Library).all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("读媒体库失败: %s", exc)
        return out
    out["libraries_total"] = len(rows)
    for lib in rows:
        status = (getattr(lib, "scan_status", None) or "").strip()
        if getattr(lib, "is_scanning", False) or status == "running":
            out["scanning"] += 1
        elif status == "failed":
            out["failed"] += 1
            if len(out["last_failed"]) < 5:
                err = (lib.scan_error or "")[:200]
                out["last_failed"].append({
                    "id": lib.id, "name": lib.name,
                    "error": err,
                    # 错误码大白话：原文旁边直接给中文解释 + 检查步骤
                    "hint": _translate_error(err),
                    "at": str(lib.last_scan_at or ""),
                })
        elif status == "interrupted":
            # 被进程重启打断：不是失败，不计 failed、不出告警；
            # 启动维护会自动续扫，这里只留一个计数供观察。
            out["interrupted"] += 1
        elif not lib.last_scan_at:
            out["never_scanned"] += 1
    return out


def collect(db) -> dict:
    """返回 ``{level, status, issues[], metrics{}}``"""
    from sqlalchemy import func

    issues: list[dict] = []
    metrics: dict[str, Any] = {}
    level = "ok"

    def _bump(new_level: str) -> None:
        nonlocal level
        order = {"ok": 0, "warn": 1, "down": 2}
        if order.get(new_level, 0) > order.get(level, 0):
            level = new_level

    # ---- 数据库 ----
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        metrics["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        metrics["database"] = "error"
        _bump("down")
        issues.append({"level": "down", "key": "database",
                       "message": f"数据库不可用：{str(exc)[:120]}"})

    # ---- Redis（队列/熔断器/分布式锁都靠它）----
    try:
        from backend import database as dbmod
        r = dbmod.redis_client
        if r is None:
            metrics["redis"] = "not_configured"
        else:
            r.ping()
            metrics["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        metrics["redis"] = "error"
        _bump("warn")
        issues.append({"level": "warn", "key": "redis",
                       "message": f"Redis 不可用：{str(exc)[:120]}"})

    # ---- 磁盘余量 ----
    try:
        from backend.emby_server import maintenance
        rep = maintenance.resource_report()
        free_pct = rep.get("disk_free_percent")
        metrics["disk_free_percent"] = free_pct
        if free_pct is not None and float(free_pct) < WARN_DISK_FREE_PERCENT:
            _bump("warn")
            issues.append({"level": "warn", "key": "disk",
                           "message": f"磁盘余量偏低（{float(free_pct):.0f}% 剩余）"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("读磁盘信息失败: %s", exc)

    # ---- 后台 worker 存活（StrmAssistant 打磨 R2）----
    # 线程静默死亡时这里报 warn，避免"后台任务停摆了却没人知道"
    try:
        from backend.emby_server import worker_registry as _wr
        workers = _wr.snapshot()
        metrics["workers"] = workers
        dead = [n for n, s in workers.items() if not s["alive"]]
        if dead:
            _bump("warn")
            issues.append({"level": "warn", "key": "workers",
                           "message": f"后台 worker 线程已死亡：{', '.join(dead)}"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("读 worker 状态失败: %s", exc)

    # ---- 媒体探测失败率 ----
    try:
        done = _count(db, "movie", "done") + _count(db, "episode", "done")
        degraded = _count(db, "movie", "degraded") + _count(db, "episode", "degraded")
        # ffprobe 跑完但没时长：可播、不算失败，但必须进分母，
        # 否则它们从总量里消失，失败率会虚低（v2.42.14 新增状态）
        no_duration = (_count(db, "movie", "probed_no_duration")
                       + _count(db, "episode", "probed_no_duration"))
        failed = _count(db, "movie", "failed") + _count(db, "episode", "failed")
        total = done + degraded + no_duration + failed
        ratio = (failed / total) if total else 0.0
        metrics["probe_degraded"] = degraded
        metrics["probe_no_duration"] = no_duration
        metrics["probe_failed"] = failed
        metrics["probe_ratio"] = round(ratio, 4)
        # 样本太少时不算数（刚开机的库会误报）
        if total >= 20 and ratio >= WARN_PROBE_FAIL_RATIO:
            _bump("warn")
            issues.append({
                "level": "warn", "key": "probe",
                "message": f"媒体探测失败率 {ratio:.0%}（{failed}/{total}），"
                           "通常是存储源读不到或云盘异常"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计探测失败率出错: %s", exc)

    # ---- 探测 worker 是否真的在干活（v2.53）----
    # 以前只看失败率：worker 不跑时 pending 只增不减，健康页却一片绿。
    try:
        from backend.emby_server import probe_worker
        ps = probe_worker.status_snapshot(db)
        rt = ps.get("runtime") or {}
        metrics["probe_worker"] = {
            "pending_ready": ps["pending_ready"], "retrying": ps["retrying"],
            "stale_probing": ps["stale_probing"], "paused": ps["paused"],
            "eta_hours": ps["eta_hours"], "rate_per_min": rt.get("rate_per_min"),
            "running": rt.get("running"), "breakers": rt.get("breakers"),
            "last_error": rt.get("last_error"),
        }
        if ps["enabled"] and not ps["paused"] and ps["pending_ready"] > 0 and not rt.get("running"):
            _bump("warn")
            issues.append({
                "level": "warn", "key": "probe_worker",
                "message": f"{ps['pending_ready']} 条待探测，但探测 worker 没有在运行"})
        if ps["stale_probing"]:
            _bump("warn")
            issues.append({
                "level": "warn", "key": "probe_stale",
                "message": f"{ps['stale_probing']} 条探测抢单已过期（worker 卡死或崩溃），"
                           "会自动回收；也可在后台手动重置"})
        if rt.get("breakers") and any(b.get("open_for_sec") for b in rt["breakers"].values()):
            _bump("warn")
            names = "、".join(k for k, b in rt["breakers"].items() if b.get("open_for_sec"))
            issues.append({"level": "warn", "key": "probe_breaker",
                           "message": f"探测熔断中的挂载：{names}（连续超时，挂载可能挂死）"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计探测 worker 状态出错: %s", exc)

    # ---- 补全/刮削失败率与堆积 ----
    try:
        edone = _enrich_count(db, "done")
        efailed = _enrich_count(db, "failed")
        epending = _enrich_count(db, "pending")
        eenriching = _enrich_count(db, "enriching")
        etotal = edone + efailed
        eratio = (efailed / etotal) if etotal else 0.0
        metrics.update({
            "enrich_done": edone, "enrich_failed": efailed,
            "enrich_pending": epending, "enriching": eenriching,
            "enrich_fail_ratio": round(eratio, 4),
        })
        if etotal >= 20 and eratio >= WARN_ENRICH_FAIL_RATIO:
            _bump("warn")
            issues.append({
                "level": "warn", "key": "enrich",
                "message": f"元数据补全失败率 {eratio:.0%}（{efailed}/{etotal}），"
                           "检查 TMDB Key 与网络"})
        backlog = epending + eenriching
        metrics["enrich_backlog"] = backlog
        # 堆积不再单独构成 down：存量大库会让 pending 长期超过阈值，
        # 只要有证据显示最近窗口里有完成条目，服务就在消化，报 degraded
        # （warn）并带数字。down 只给「有积压 + 有证据证明真停滞」。
        # 进展/停滞的证据来源（L-009）：
        #   - worker Redis 快照（S8，janitor 每 60 秒发布本进程
        #     throughput）：唯一现成的跨进程「完成」证据——快照新鲜且
        #     窗口内有完成 = 有进展；快照新鲜但上次完成已在窗口外 =
        #     真停滞；快照不可得 = 无证据，只 warn 不 down。
        #   - 库内 last_scraped_at 近窗口的 done 数：只能作「有进展」
        #     旁证。该字段在各补全收尾路径只在为空时才写，复刮条目
        #     完成不更新它，为 0 证明不了没进展（2026-11-24 假 down
        #     的直接原因：补拉的复刮条目秒级完成，此数恒 0）。
        #   - 本进程 scan_progress 计数：只在单进程部署时是真实证据
        #     （分离部署时 API 进程恒为 0，绝不能据此判停滞）。
        try:
            from backend.emby_server import enrich_worker
            from backend.emby_server import scan_progress as progress

            tp = progress.throughput()
            done_per_min = tp["done_per_min"]
            metrics["enrich_done_per_min"] = done_per_min
            metrics["enrich_idle_sec"] = tp["idle_sec"]
            window_min = HEALTH_ENRICH_PROGRESS_MIN
            recent_done = _enrich_recent_completed(db, window_min)
            metrics["enrich_recent_done"] = recent_done
            metrics["enrich_progress_window_min"] = window_min
            # S8：本进程统计为空时回退 worker 发布的 Redis 快照
            metrics["enrich_stages"] = _enrich_stage_stats()
            snapshot = _enrich_worker_snapshot()
            snap_state = (_enrich_snapshot_progress(snapshot, window_min)
                          if snapshot is not None else "unknown")
            snap_tp = (snapshot or {}).get("throughput") or {}
            try:
                snap_rate = float(snap_tp.get("done_per_min") or 0)
            except (TypeError, ValueError):
                snap_rate = 0.0
            metrics["enrich_worker_progress"] = snap_state
            metrics["enrich_worker_done_per_min"] = snap_tp.get("done_per_min")
            metrics["enrich_worker_last_done_at"] = snap_tp.get("last_done_at")
            progressing = (recent_done > 0 or done_per_min > 0
                           or snap_state == "progressing")
            total = tp["done_total"] + tp["retry_total"] + tp["failed_total"]
            # 停滞必须有证据：快照证明 worker 活着却无完成，或本进程
            # （单进程部署时计数即真实）有过动作但窗口内无成功。
            # 无证据不下停滞结论——证据不可得只 warn（L-009 口径）。
            stalled = (snap_state == "stalled"
                       or (done_per_min <= 0 and total > 0))
            if backlog >= DOWN_QUEUE_BACKLOG:
                if progressing:
                    bits = []
                    if recent_done > 0:
                        bits.append(f"近 {window_min} 分钟完成 {recent_done} 条")
                    if done_per_min > 0:
                        bits.append(f"本进程 {done_per_min:.1f} 条/分")
                    if snap_state == "progressing":
                        if snap_rate > 0:
                            bits.append(f"worker {snap_rate:.1f} 条/分")
                        else:
                            bits.append(f"worker 近 {window_min} 分钟有完成")
                    _bump("warn")
                    issues.append({
                        "level": "warn", "key": "enrich_backlog",
                        "message": (
                            f"补全队列堆积 {backlog} 条，"
                            f"{'，'.join(bits)}，仍在消化")})
                elif stalled:
                    _bump("down")
                    issues.append({
                        "level": "down", "key": "enrich_backlog",
                        "message": (
                            f"补全队列堆积 {backlog} 条，近 {window_min} 分钟"
                            f"没有完成条目，已经处理不过来")})
                else:
                    _bump("warn")
                    issues.append({
                        "level": "warn", "key": "enrich_backlog",
                        "message": (
                            f"补全队列堆积 {backlog} 条，暂未观察到完成条目"
                            f"（进展证据不可得），先观察再排查")})
            # 没进展时保留「卡住 / 证据不足」细分，方便和「根本没跑」区分。
            # 有进展就不再报这两条，避免和上面的 warn 重复。
            # worker 被显式关掉（ENRICH_WORKER=0）时不报「没在跑」：那就是设计如此。
            if (not progressing
                    and enrich_worker.ENRICH_ENABLED
                    and backlog >= DOWN_QUEUE_BACKLOG):
                if stalled:
                    _bump("down")
                    issues.append({
                        "level": "down", "key": "enrich_stalled",
                        "message": f"补全队列堆积 {backlog} 条，有动作但近 "
                                   f"{window_min} 分钟没有任何成功 —— 是卡住了，不是跑得慢"})
                else:
                    _bump("warn")
                    issues.append({
                        "level": "warn", "key": "enrich_no_progress",
                        "message": f"补全队列堆积 {backlog} 条，尚未观察到完成条目 —— "
                                   "worker 可能刚启动或没在跑，先观察再排查"})
        except Exception as exc:  # noqa: BLE001
            logger.debug("统计补全速率出错: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计补全状态出错: %s", exc)

    # ---- enrich done→pending 批量翻转监控（v2.55.x）----
    # 2026-10-11 生产事故：5723 条翻转触发源未知、日志丢失。从此任何超阈值的
    # 翻转都写 EnrichFlipEvent（PG 持久），这里暴露近 1 小时翻转数；>0 就 warn。
    try:
        from backend.emby_server import enrich_flip_monitor as _fm
        # retry_unmatched 是管理端手动触发的已知操作：事件仍在 PG 留痕审计，
        # 但不应把 health 打成 warn。
        flip_1h = _fm.flip_count_last_hours(
            db, hours=1, exclude_triggers=("retry_unmatched",))
        metrics["enrich_flip_last_hour"] = flip_1h
        if flip_1h > 0:
            _bump("warn")
            issues.append({
                "level": "warn", "key": "enrich_flip",
                "message": f"近 1 小时 enrich done→pending 翻转 {flip_1h} 条 —— "
                           "检查翻转告警日志与 EnrichFlipEvent 记录，确认触发源"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计 enrich 翻转数出错: %s", exc)

    # ---- 扫描（库级别的真实故障）----
    try:
        scans = library_scan_summary(db)
        metrics["scans"] = scans
        if scans["failed"]:
            names = "、".join(x["name"] for x in scans["last_failed"][:3])
            _bump("warn")
            issues.append({
                "level": "warn", "key": "scan",
                "message": f"{scans['failed']} 个媒体库最近一次扫描失败：{names}"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计扫描状态出错: %s", exc)

    # ---- 媒体库列表投影新鲜度（2026-10-30 冻结事故的可见信号）----
    # 投影重建工人连续失败时 live 行 computed_at 停住：读侧虽整包回落
    # 现算（数字仍准），但冻结 4 天无人知道的教训不能重演——超读侧
    # 陈旧口径（MAX_STALE_SEC）即 warn，管理端健康页与 /api/health 可见。
    try:
        from backend.emby_server import list_projection as _lp
        ph = _lp.projection_health(db)
        metrics["list_projection"] = {
            "targets": ph["targets"], "rows": ph["rows"],
            "newest_computed_at": (ph["newest_computed_at"].isoformat()
                                   if ph["newest_computed_at"] else None),
            "age_sec": (round(ph["age_sec"], 1)
                        if ph["age_sec"] is not None else None),
            "stale": ph["stale"],
        }
        if ph["stale"]:
            _bump("warn")
            if ph["reason"] == "no_rows":
                msg = (f"媒体库列表投影无任何投影行（目标库 {ph['targets']} 个），"
                       "重建工人可能从未成功；客户端库列表在走现算慢路，"
                       "检查 worker 的列表投影重建日志")
            else:
                msg = (f"媒体库列表投影已冻结：最近一次成功重建在 "
                       f"{ph['newest_computed_at']}（{int(ph['age_sec'] or 0)} 秒前），"
                       "重建工人在连续失败；客户端库列表已回落现算（数字仍准、"
                       "只是变慢），检查 worker 的列表投影重建日志")
            issues.append({"level": "warn", "key": "list_projection",
                           "message": msg})
    except Exception as exc:  # noqa: BLE001
        logger.debug("统计列表投影新鲜度出错: %s", exc)

    # ---- 错误码大白话：每条 issue 旁边挂中文解释 + 检查步骤 ----
    # translate() 命中返回 dict、无命中返回 None（前端按 None 隐藏）。
    for issue in issues:
        try:
            issue["hint"] = _translate_error(issue.get("message", ""))
        except Exception:  # noqa: BLE001 — 翻译失败不影响健康检查
            issue["hint"] = None

    return {
        "level": level,
        "status": {"ok": "healthy", "warn": "degraded", "down": "unhealthy"}[level],
        "issues": issues,
        "metrics": metrics,
    }


# ---- collect() 的短 TTL 缓存入口（2026-10-16 网页加载二轮优化）----
# 实测：扫描打满 2 核生产机时 collect() 一次约 5–10 秒（约 20 个 SQL +
# 目录 walk + Redis ping）；每次打开「服务器」页与每次健康探针都全量重跑，
# 是「服务器」页 5 秒空白的最大头。健康快照 60 秒级延迟可接受，故加进程内
# 短 TTL 缓存：/api/health、业务健康端点、面板 pulse/vitals 全部经此入口。
# collect() 本体保持无缓存——测试与需要实时口径的调用方不受影响。
# TTL 经环境变量 HEALTH_REPORT_TTL_SECONDS 可配（默认 60；<=0 关闭缓存）。
_HEALTH_CACHE = {"at": 0.0, "result": None}
_HEALTH_CACHE_LOCK = threading.Lock()
DEFAULT_HEALTH_TTL_SECONDS = 60.0


def _health_ttl_seconds() -> float:
    return env_float("HEALTH_REPORT_TTL_SECONDS", DEFAULT_HEALTH_TTL_SECONDS)


def collect_cached(db, ttl_seconds: float | None = None) -> dict:
    """collect() 的短 TTL 缓存版：同一进程内 TTL 窗口内只聚合一次。

    刷新失败（理论上 collect() 内部已吞掉一切异常）时返回旧快照，
    绝不让健康检查 500。
    """
    ttl = _health_ttl_seconds() if ttl_seconds is None else float(ttl_seconds)
    if ttl <= 0:
        return collect(db)
    now = time.monotonic()
    with _HEALTH_CACHE_LOCK:
        entry = _HEALTH_CACHE
        if entry["result"] is not None and (now - entry["at"]) < ttl:
            return entry["result"]
        try:
            result = collect(db)
        except Exception:  # noqa: BLE001 — 刷新失败时退回旧快照
            if entry["result"] is not None:
                return entry["result"]
            raise
        entry["at"] = now
        entry["result"] = result
        return result


def _reset_health_cache() -> None:
    """测试隔离用：清空进程内健康缓存。生产代码不调用。"""
    with _HEALTH_CACHE_LOCK:
        _HEALTH_CACHE["at"] = 0.0
        _HEALTH_CACHE["result"] = None
