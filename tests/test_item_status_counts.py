# -*- coding: utf-8 -*-
"""C-02 状态分布共享缓存（item_status_counts）验收。

钉住四件事：
1. 口径与原各消费者逐字一致——副本（merged_into_id 非空）与软删行
   不计入；enrich 分布、probe 按类型分布、probe 类型集合总量都与
   直接 GROUP BY 现算的结果相等。
2. TTL 内多次调用只算一次：消费者整族 sweep（observability 探针 +
   health_report 计数 + status_snapshot + get_progress）只发出
   3 条分布 GROUP BY（enrich + probe + 错误分布），近期完成 COUNT
   也随快照只算一次；TTL 内第二遍 sweep 为 0 条。
3. 缓存失效行为：TTL 过期后刷新；本进程写入（ORM 新增/批量更新）
   即时失效；刷新失败有旧快照退旧值、无旧快照抛错（消费者原有
   吞错口径不变）；不同 engine 不串扰。
4. 消费者等价：health_report._count/_enrich_count、
   probe_worker.status_snapshot、enrich_worker.get_progress、
   observability._probe_enrich 的数字与现算口径一致。
"""
import os
import tempfile

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

import pytest  # noqa: E402

from backend import health_report  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import enrich_worker  # noqa: E402
from backend.emby_server import item_status_counts as isc  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server import observability  # noqa: E402
from backend.emby_server import probe_worker  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库（与 test_enrich_observability 同惯例）。"""
    saved_engine = _dbmod.engine
    saved_factory = _dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = saved_engine
        _dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_items():
    isc._reset_cache()
    db = SessionLocal()
    try:
        db.query(em.MediaItem).delete(synchronize_session=False)
        db.query(em.Library).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    isc._reset_cache()


_counter = {"n": 0}


def _mk(db, lib_id, **kw):
    _counter["n"] += 1
    row = em.MediaItem(
        guid=f"c02-test-{_counter['n']:05d}",
        library_id=lib_id,
        item_type=kw.pop("item_type", "movie"),
        name=kw.pop("name", "测试条目"),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def _seed(db):
    """造一组口径样本，返回库 id。数字在各测试里按此对账：

    真实条目（计入）：movie enrich pending×3/done×2、episode enriching×1、
    episode failed×1；probe：movie done×2/failed×1、episode done×1/
    pending×3、series done×5。
    不计入：合并副本（enrich pending×2、probe done×4）、软删行
    （enrich pending×1、probe done×2）。
    """
    lib = em.Library(guid="c02-lib-main", name="C02 测试库")
    db.add(lib)
    db.commit()
    lid = lib.id
    for _ in range(3):
        _mk(db, lid, enrich_status="pending", probe_status="done")
    # 上面 3 条 movie 的 probe 分开摆：done×2 + failed×1
    # （_mk 已提交，逐条改：第 3 条改 failed）
    rows = db.query(em.MediaItem).filter(
        em.MediaItem.library_id == lid).order_by(em.MediaItem.id).all()
    rows[2].probe_status = "failed"
    db.commit()
    for _ in range(2):
        _mk(db, lid, enrich_status="done", probe_status="pending")
    # movie probe 至此：done×2、failed×1、pending×2 —— 与 docstring 对账时
    # 注意 pending 的 2 条来自 enrich done 的两条。
    _mk(db, lid, item_type="episode", enrich_status="enriching",
        probe_status="done")
    _mk(db, lid, item_type="episode", enrich_status="failed",
        probe_status="pending")
    for _ in range(2):
        _mk(db, lid, item_type="episode", enrich_status="done",
            probe_status="pending")
    for _ in range(5):
        _mk(db, lid, item_type="series", enrich_status="done",
            probe_status="done")
    parent = _mk(db, lid, enrich_status="done", probe_status="done")
    for _ in range(2):
        _mk(db, lid, enrich_status="pending", probe_status="done",
            merged_into_id=parent.id)
    for _ in range(2):
        _mk(db, lid, enrich_status="done", probe_status="done",
            merged_into_id=parent.id)
    from datetime import datetime
    _mk(db, lid, enrich_status="pending", probe_status="done",
        deleted_at=datetime.now())
    _mk(db, lid, item_type="episode", enrich_status="done",
        probe_status="done", deleted_at=datetime.now())
    _mk(db, lid, item_type="episode", enrich_status="done",
        probe_status="done", deleted_at=datetime.now())
    return lid


# 期望值（与 _seed 对账）：
# enrich 真实分布：pending 3（movie）+0、enriching 1、failed 1、
# done = movie 2 + episode 2 + series 5 + parent 1 = 10
EXPECT_ENRICH = {"pending": 3, "enriching": 1, "failed": 1, "done": 10}
# probe 按类型：movie done 2 / failed 1 / pending 2；
# episode done 1 / pending 3；series done 5；另 parent(movie) done 1
EXPECT_PROBE_BY_TYPE = {
    "movie": {"done": 3, "failed": 1, "pending": 2},
    "episode": {"done": 1, "pending": 3},
    "series": {"done": 5},
}
EXPECT_PROBE_TOTALS = {"done": 4, "failed": 1, "pending": 5}


def test_enrich_counts_caliber():
    db = SessionLocal()
    try:
        _seed(db)
        got = isc.enrich_status_counts(db)
        assert got == EXPECT_ENRICH
        # 与现算 GROUP BY（原口径）逐字对账
        rows = (db.query(em.MediaItem.enrich_status,
                         func.count(em.MediaItem.id))
                .filter(em.MediaItem.merged_into_id.is_(None))
                .group_by(em.MediaItem.enrich_status).all())
        assert got == {(s or "unknown"): int(c) for s, c in rows}
    finally:
        db.close()


def test_probe_counts_caliber():
    db = SessionLocal()
    try:
        _seed(db)
        assert isc.probe_counts_by_type(db) == EXPECT_PROBE_BY_TYPE
        assert isc.probe_status_counts(
            db, probe_worker.PROBE_ITEM_TYPES) == EXPECT_PROBE_TOTALS
        # health_report 两支计数与现算口径一致
        assert health_report._enrich_count(db, "pending") == 3
        assert health_report._enrich_count(db, "done") == 10
        assert health_report._count(db, "movie", "done") == 3
        assert health_report._count(db, "episode", "pending") == 3
        assert health_report._count(db, "series", "done") == 5
        assert health_report._count(db, "movie", "degraded") == 0
    finally:
        db.close()


def _groupby_counter(engine):
    """数 emby_items 上的 GROUP BY 语句条数，返回 (计数器 dict, 注销函数)。"""
    counter = {"n": 0}

    def _listen(conn, cursor, statement, parameters, context, executemany):
        if "emby_items" in statement and "GROUP BY" in statement.upper():
            counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _listen)
    return counter, lambda: event.remove(
        engine, "before_cursor_execute", _listen)


def test_ttl_single_refresh_and_expiry():
    db = SessionLocal()
    engine = db.get_bind()
    try:
        lid = _seed(db)
        counter, unlisten = _groupby_counter(engine)
        try:
            assert isc.enrich_status_counts(db) == EXPECT_ENRICH
            assert isc.probe_counts_by_type(db) == EXPECT_PROBE_BY_TYPE
            assert isc.probe_status_counts(db, ("movie", "episode")) == \
                EXPECT_PROBE_TOTALS
            # 三次调用只算一次（一份快照 = enrich + probe + 错误分布三条 GROUP BY）
            assert counter["n"] == 3
            # TTL 过期后刷新（值不变、查询数 +3）
            isc._CACHE["at"] -= 3600
            assert isc.enrich_status_counts(db) == EXPECT_ENRICH
            assert counter["n"] == 6
        finally:
            unlisten()
    finally:
        db.close()


def test_write_invalidation():
    """本进程写入即时失效：TTL 内改完状态，下一次读就是新数。

    这是上层缓存（admin_tasks 20 秒快照 / health collect_cached）
    「清掉后拿到新数」契约的地基，也是增量断言类测试的口径。
    """
    db = SessionLocal()
    engine = db.get_bind()
    try:
        lid = _seed(db)
        counter, unlisten = _groupby_counter(engine)
        try:
            assert isc.enrich_status_counts(db)["pending"] == 3
            assert counter["n"] == 3
            # ORM 新增一行 → 立刻失效重算
            _mk(db, lid, enrich_status="pending")
            assert isc.enrich_status_counts(db)["pending"] == 4
            assert counter["n"] == 6
            # 批量 UPDATE（Query.update）→ 同样立刻失效
            db.query(em.MediaItem).filter(
                em.MediaItem.enrich_status == "pending").update(
                    {"enrich_status": "done"}, synchronize_session=False)
            db.commit()
            got = isc.enrich_status_counts(db)
            assert got.get("pending", 0) == 0
            assert got["done"] == EXPECT_ENRICH["done"] + 4
            assert counter["n"] == 9
        finally:
            unlisten()
    finally:
        db.close()


def test_bind_isolation(tmp_path):
    db = SessionLocal()
    try:
        _seed(db)
        assert isc.enrich_status_counts(db)["pending"] == 3
    finally:
        db.close()
    # 第二个 engine（另一临时库）：只有 1 条 pending
    eng2 = _ce(f"sqlite:///{tmp_path}/other.db")
    em.MediaItem.__table__.create(eng2, checkfirst=True)
    em.Library.__table__.create(eng2, checkfirst=True)
    db2 = _sm(bind=eng2)()
    try:
        lib = em.Library(guid="c02-lib-other", name="另一库")
        db2.add(lib)
        db2.commit()
        _mk(db2, lib.id, enrich_status="pending")
        assert isc.enrich_status_counts(db2) == {"pending": 1}
        # 回到第一个 engine 仍然是它自己的数字（重算，不串扰）
        db = SessionLocal()
        try:
            assert isc.enrich_status_counts(db)["pending"] == 3
        finally:
            db.close()
    finally:
        db2.close()
        eng2.dispose()


def test_stale_on_refresh_error(monkeypatch):
    db = SessionLocal()
    try:
        _seed(db)
        assert isc.enrich_status_counts(db) == EXPECT_ENRICH

        def _boom(_db):
            raise RuntimeError("模拟 DB 故障")

        monkeypatch.setattr(isc, "_compute", _boom)
        isc._CACHE["at"] -= 3600  # 强制过期触发刷新
        # 有旧快照：退回旧值不抛错
        assert isc.enrich_status_counts(db) == EXPECT_ENRICH
        # 无旧快照：抛错给消费者按原口径处理
        isc._reset_cache()
        with pytest.raises(RuntimeError):
            isc.enrich_status_counts(db)
    finally:
        db.close()


def test_consumers_equivalence():
    db = SessionLocal()
    try:
        _seed(db)
        # probe_worker 快照（light）：counts 与分布总量一致
        snap = probe_worker.status_snapshot(db, light=True)
        assert snap["counts"] == EXPECT_PROBE_TOTALS
        assert snap["pending_ready"] == EXPECT_PROBE_TOTALS["pending"]
        # observability enrich 探针：metrics 与分布一致
        res = observability._probe_enrich(db)
        m = res["metrics"]
        assert m["pending"] == 3 and m["enriching"] == 1
        assert m["done"] == 10 and m["failed"] == 1
        assert m["backlog"] == 4
    finally:
        db.close()
    # enrich_worker 进度（走全局 SessionLocal，已换绑到本文件临时库）
    prog = enrich_worker.get_progress(light=False)
    assert prog["enrich"]["pending"] == 3
    assert prog["enrich"]["enriching"] == 1
    assert prog["enrich"]["done"] == 10
    assert prog["enrich"]["failed"] == 1
    assert prog["probe"] == EXPECT_PROBE_TOTALS


def test_consumer_sweep_query_count():
    """整族消费者 sweep 只算一次分布（C-02 的核心收益断言）。"""
    db = SessionLocal()
    engine = db.get_bind()
    try:
        _seed(db)
        isc._reset_cache()
        counter, unlisten = _groupby_counter(engine)
        try:
            observability._probe_enrich(db)
            for st in ("done", "failed", "pending", "enriching"):
                health_report._enrich_count(db, st)
            for t in ("movie", "episode"):
                for st in ("done", "degraded", "probed_no_duration",
                           "failed"):
                    health_report._count(db, t, st)
            probe_worker.status_snapshot(db, light=True)
            enrich_worker.get_progress(light=True)
            # 迁移前这一遍要跑 18 条状态分布查询（4+4+8 COUNT + 2
            # GROUP BY）；迁移后只有共享快照的 3 条 GROUP BY
            #（enrich + probe + 错误分布）。
            assert counter["n"] == 3
            # TTL 内第二遍：0 条
            observability._probe_enrich(db)
            probe_worker.status_snapshot(db, light=True)
            enrich_worker.get_progress(light=True)
            assert counter["n"] == 3
        finally:
            unlisten()
    finally:
        db.close()


# ================= R-01（2026-11-28）：TTL 300 秒 + 错误分布/近期完成进快照 ===

def test_ttl_default_is_300_seconds(monkeypatch):
    """R-01：默认 TTL 由 60 秒改为 300 秒（env 仍可覆盖）。"""
    assert isc.DEFAULT_TTL_SECONDS == 300.0
    monkeypatch.delenv("ITEM_STATUS_COUNTS_TTL_SECONDS", raising=False)
    assert isc._ttl_seconds() == 300.0
    monkeypatch.setenv("ITEM_STATUS_COUNTS_TTL_SECONDS", "45")
    assert isc._ttl_seconds() == 45.0


def _seed_errors(db, lid):
    """错误分布样本：pending/failed 带错（计入），其余不计。
    计入：timeout x3（pending 2 + failed 1）、quota x1（failed）。
    不计：done 带错、NULL 错误、合并副本、软删行。"""
    from datetime import datetime
    now = datetime.now()
    _mk(db, lid, enrich_status="done", probe_status="pending",
        probe_last_error="timeout")
    _mk(db, lid, enrich_status="done", probe_status="pending",
        probe_last_error="timeout")
    _mk(db, lid, enrich_status="failed", probe_status="failed",
        probe_last_error="timeout")
    _mk(db, lid, enrich_status="done", probe_status="failed",
        probe_last_error="quota")
    _mk(db, lid, enrich_status="done", probe_status="done",
        probe_last_error="timeout")
    _mk(db, lid, enrich_status="done", probe_status="failed")
    parent = _mk(db, lid, enrich_status="done", probe_status="done")
    _mk(db, lid, enrich_status="done", probe_status="failed",
        probe_last_error="timeout", merged_into_id=parent.id)
    _mk(db, lid, enrich_status="done", probe_status="failed",
        probe_last_error="timeout", deleted_at=now)
    return lid


def test_probe_error_counts_caliber():
    """R-01：错误分布与原 status_snapshot 口径的现算 GROUP BY 逐字一致，
    且 status_snapshot 全量模式的 top_errors 与快照一致。"""
    db = SessionLocal()
    try:
        lid = _seed(db)
        _seed_errors(db, lid)
        got = isc.probe_error_counts(db)
        assert got == [("timeout", 3), ("quota", 1)]
        rows = (db.query(em.MediaItem.probe_last_error,
                         func.count(em.MediaItem.id))
                .filter(em.MediaItem.probe_last_error.isnot(None),
                        em.MediaItem.probe_status.in_(("pending", "failed")),
                        em.MediaItem.merged_into_id.is_(None))
                .group_by(em.MediaItem.probe_last_error)
                .order_by(func.count(em.MediaItem.id).desc()).all())
        assert got == [(e, int(c)) for e, c in rows]
        snap = probe_worker.status_snapshot(db, light=False)
        assert [e["error"] for e in snap["top_errors"]] == ["timeout", "quota"]
        assert [e["count"] for e in snap["top_errors"]] == [3, 1]
    finally:
        db.close()


def test_recent_done_caliber():
    """R-01：近期完成数与原 _enrich_recent_completed 口径的直接 COUNT 一致。"""
    from datetime import datetime, timedelta
    db = SessionLocal()
    try:
        lib = em.Library(guid="c02-lib-recent", name="C02 近期完成库")
        db.add(lib)
        db.commit()
        lid = lib.id
        w = health_report.HEALTH_ENRICH_PROGRESS_MIN
        now = datetime.now()
        for _ in range(2):
            _mk(db, lid, enrich_status="done", probe_status="done",
                last_scraped_at=now - timedelta(minutes=1))
        _mk(db, lid, enrich_status="done", probe_status="done",
            last_scraped_at=now - timedelta(minutes=w + 5))
        _mk(db, lid, enrich_status="done", probe_status="done")
        _mk(db, lid, enrich_status="pending", probe_status="done",
            last_scraped_at=now - timedelta(minutes=1))
        assert isc.enrich_recent_done(db) == 2
        assert health_report._enrich_recent_completed(db, w) == 2
        cutoff = now - timedelta(minutes=w)
        direct = db.query(func.count(em.MediaItem.id)).filter(
            em.MediaItem.enrich_status == "done",
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.last_scraped_at.isnot(None),
            em.MediaItem.last_scraped_at >= cutoff).scalar() or 0
        assert direct == 2
    finally:
        db.close()


def _last_scraped_counter(engine):
    """数含 last_scraped_at 的语句条数，返回 (计数器 dict, 注销函数)。"""
    counter = {"n": 0}

    def _listen(conn, cursor, statement, parameters, context, executemany):
        if "last_scraped_at" in statement:
            counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _listen)
    return counter, lambda: event.remove(
        engine, "before_cursor_execute", _listen)


def test_snapshot_covers_errors_and_recent_done():
    """R-01：错误分布与近期完成数也进同一份快照——整族 sweep 只刷新一次。"""
    db = SessionLocal()
    engine = db.get_bind()
    try:
        lid = _seed(db)
        _seed_errors(db, lid)
        isc._reset_cache()
        counter, unlisten = _groupby_counter(engine)
        lsc, unlisten_ls = _last_scraped_counter(engine)
        try:
            observability._probe_enrich(db)
            for st in ("done", "failed", "pending", "enriching"):
                health_report._enrich_count(db, st)
            for t in ("movie", "episode"):
                for st in ("done", "degraded", "probed_no_duration",
                           "failed"):
                    health_report._count(db, t, st)
            probe_worker.status_snapshot(db, light=False)
            enrich_worker.get_progress(light=False)
            health_report._enrich_recent_completed(
                db, health_report.HEALTH_ENRICH_PROGRESS_MIN)
            # 一份快照 = 3 条 GROUP BY + 1 条近期完成 COUNT
            assert counter["n"] == 3
            assert lsc["n"] == 1
            # TTL 内第二遍：0 条新增
            observability._probe_enrich(db)
            probe_worker.status_snapshot(db, light=False)
            enrich_worker.get_progress(light=False)
            health_report._enrich_recent_completed(
                db, health_report.HEALTH_ENRICH_PROGRESS_MIN)
            assert counter["n"] == 3
            assert lsc["n"] == 1
        finally:
            unlisten()
            unlisten_ls()
    finally:
        db.close()
