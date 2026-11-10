# -*- coding: utf-8 -*-
"""求片到货盯梢工人（求片减负，2026-11-10 用户亲点）。

为什么有这个工人
----------------
此前求片「到货联动」只有读时触发：管理员（或求片人）打开列表那
一刻才拿 tmdb 去库里对，对上才翻已入库并发到货通知。没人打开
页面，到货的片就一直躺在「已批准」，管理员还得逐条点「标记已
入库」试探到了没有。本工人把这件事变成后台常态盯梢：周期性地
把处理中的求片跟片库对一遍，对上自动置 completed 并入队到货
私聊，管理员什么都不用点。

口径（不许漂移）
----------------
- 判定与翻转只走一条：``media_seek.sync_seek_library_row``——与
  读时联动（api.store_requests._sync_row）完全同一条口径（处理中
  状态 + seek 类 + 有 tmdb + find_library_item 对得上）。本工人
  只负责「定时把行喂给它」，不许在这里另写匹配逻辑。
- 补缺集行（request_kind='missing_episodes'）由共享口径内的守卫
  排除，它的完成只认缺集核算工人。
- 到货私聊走 proactive 既有入队与已推去重：工人与读路径谁先翻
  都只发一次，重复轮次不重发。

资源纪律（照工人家族同口径）
----------------
- 轮间隔走 SystemConfig ``media_seek_fulfill_interval_minutes``
  （默认 5 分钟，每轮热读、改完不用重启）；单轮最多处理
  MAX_ROWS_PER_ROUND 行（按 id 升序，老行优先），超出的下轮再来。
- 纯本地 DB 判定（tmdb 索引点查），不发出站请求；单行异常只记
  账并回滚该行，绝不拖垮整轮；线程异常不退出循环。启动时先随
  机错峰等待，避免与其它工人同秒齐发。
"""

import logging
import random
import threading
import time

from backend import media_seek, models

logger = logging.getLogger(__name__)

#: worker_registry 登记名（/api/health 与管理总览按此名认人）。
WORKER_NAME = "seek_fulfill"

#: 单轮最多对多少条求片（有界扫描；积压再深也分多轮慢慢吃）。
MAX_ROWS_PER_ROUND = 200

_stop_event = threading.Event()
_start_lock = threading.Lock()
_thread: threading.Thread | None = None


def _default_session_factory():
    from backend.database import SessionLocal

    return SessionLocal


def _interval_sec() -> float:
    """本轮过后等多久（秒）：热读配置，读不到回默认 5 分钟。"""
    try:
        db = _default_session_factory()()
        try:
            return media_seek.fulfill_interval_minutes(db) * 60.0
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 配置读失败不许拖死循环
        return media_seek.DEFAULT_FULFILL_INTERVAL_MINUTES * 60.0


def fulfill_once(session_factory=None) -> dict:
    """跑一轮盯梢，返回本轮统计（任何情况下都返回，不抛）。

    统计：checked＝本轮喂给联动判定的行数，fulfilled＝翻成
    completed 的行数，failed_rows＝单行判定异常被隔离的行数。
    """
    stats = {"ran": False, "checked": 0, "fulfilled": 0, "failed_rows": 0}
    factory = session_factory or _default_session_factory()
    db = factory()
    try:
        rows = (
            db.query(models.MovieRequest)
            .filter(
                models.MovieRequest.status.in_(media_seek.ACTIVE_STATUSES),
                models.MovieRequest.tmdb_id.isnot(None),
                models.MovieRequest.tmdb_id != "",
            )
            .order_by(models.MovieRequest.id)
            .limit(MAX_ROWS_PER_ROUND)
            .all()
        )
        stats["ran"] = True
        for row in rows:
            stats["checked"] += 1
            try:
                if media_seek.sync_seek_library_row(db, row):
                    db.commit()
                    stats["fulfilled"] += 1
            except Exception:  # noqa: BLE001 — 单行隔离，不拖垮整轮
                db.rollback()
                stats["failed_rows"] += 1
                logger.warning(
                    "求片到货盯梢单行失败 request=%s",
                    getattr(row, "id", None), exc_info=True)
        return stats
    finally:
        db.close()


def _fulfill_loop():
    logger.info("求片到货盯梢 worker 启动")
    # 错峰：启动后先随机等一小段，避免与其它工人同秒齐发抢 DB
    _stop_event.wait(random.uniform(5.0, 30.0))
    while not _stop_event.is_set():
        started = time.monotonic()
        try:
            stats = fulfill_once()
            if stats.get("fulfilled") or stats.get("failed_rows"):
                logger.info(
                    "求片到货盯梢一轮：检查 %d，翻已入库 %d，单行失败 %d",
                    stats["checked"], stats["fulfilled"],
                    stats["failed_rows"])
        except Exception as exc:  # noqa: BLE001 — 循环体异常不许退出
            logger.warning("求片到货盯梢一轮异常: %s", exc, exc_info=True)
        finally:
            try:
                from backend.emby_server import worker_registry as _wr

                _wr.heartbeat(WORKER_NAME)
            except Exception:  # noqa: BLE001
                pass
        elapsed = time.monotonic() - started
        wait = max(5.0, _interval_sec() - elapsed)
        _stop_event.wait(wait)


def start() -> bool:
    """启动盯梢后台线程（幂等）。"""
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop_event.clear()
        _thread = threading.Thread(
            target=_fulfill_loop, name="seek-fulfill", daemon=True)
        _thread.start()
        from backend.emby_server import worker_registry as _wr

        _wr.register(WORKER_NAME, _thread)
        return True


def stop() -> None:
    _stop_event.set()
