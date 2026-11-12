"""认领候选组聚合缓存（减负 A1）回归兜底。

生产 pg_stat_statements 实证（2026-11-12 审计）：``_claim_batch`` 的
``coalesce(series_id,id) + max(date_added)`` GROUP BY 聚合四变体合计约
29 万次调用、单次 1.2~1.6 秒，是全站第一烧 CPU 的查询——16~32 个工人
线程每轮认领都把全部 pending 重新聚合一遍，而积压集合分钟级才有变化。

钉死的新口径：
① TTL 内（``ENRICH_CLAIM_GROUPS_TTL_SEC``，缺省 30 秒）同进程同口径的
   认领复用候选组快照，不再重复打聚合；快照只决定「先看哪些组」，
   组员仍走实时谓词 + 原子认领，抢不重、抢不丢；
② 入库事件同进程即时失效：新 pending 条目插入 / 条目转回 pending /
   父级（series/season）状态或合并关系变化 / 修复请求新到——下一轮
   立刻重新聚合，不等 TTL；
③ 缓存开与关（TTL=0）认领集合逐轮等价；
④ 已被整组认领完的组在快照内标记 consumed，不再逐轮重试其组员查询；
   恰好装满批次的组不标（可能还有余量）。
"""
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import enrich_worker
from backend.emby_server import models as em


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    base_models.Base.metadata.create_all(eng)
    em.Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture()
def agg_counter(engine):
    """统计候选组聚合（带 coalesce 的 GROUP BY）的真实执行次数。"""
    counts = {"agg": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "coalesce" in low and "group by" in low:
            counts["agg"] += 1

    return counts


@pytest.fixture()
def lib(db):
    row = em.Library(guid=uuid.uuid4().hex, name="测试库",
                     collection_type="tvshows")
    db.add(row)
    db.flush()
    return row


def _item(db, lib, name, item_type="movie", **kw):
    kw.setdefault("enrich_status", "pending")
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name,
        file_path=f"/m/{uuid.uuid4().hex}.mkv", **kw)
    db.add(row)
    db.flush()
    return row


def _movies(db, lib, n, prefix="电影"):
    rows = [_item(db, lib, f"{prefix}{i}") for i in range(n)]
    db.commit()
    return rows


# ---------- ① TTL 内复用快照，不重复聚合 ----------

def test_second_claim_reuses_group_snapshot(db, lib, agg_counter):
    _movies(db, lib, 10)
    first = enrich_worker._claim_batch(db, 4)
    assert len(first) == 4
    assert agg_counter["agg"] == 1
    second = enrich_worker._claim_batch(db, 4)
    assert len(second) == 4
    assert agg_counter["agg"] == 1, "TTL 内第二轮不许再打全量聚合"
    assert {r.id for r in first}.isdisjoint({r.id for r in second})


def test_snapshot_groups_consumed_not_retried(db, lib, agg_counter):
    # 5 部电影 = 5 个单行组，date_added 显式递增 → 快照顺序确定为
    # 「最新在前」：rows[4], rows[3], rows[2], rows[1], rows[0]。
    # 第一轮 limit=2：rows[4] 组认领 1 行 < 当时余量 2 → 已抽干，
    # 标 consumed；rows[3] 组认领 1 行 == 当时余量 1（恰好装满）
    # → 不许标（判据见 _claim_batch 快照路径注释）。
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1)
    rows = [_item(db, lib, f"电影{i}",
                  date_added=base + timedelta(minutes=i)) for i in range(5)]
    db.commit()
    first = enrich_worker._claim_batch(db, 2)
    assert [r.id for r in first] == [rows[4].id, rows[3].id]
    entry = next(iter(enrich_worker._CLAIM_GROUPS_CACHE.values()))
    assert entry["consumed"] == {rows[4].id}
    # 第二轮：rows[3] 组先被重试（组员已 enriching → 0 行后转 consumed），
    # 再往后认领 rows[2]、rows[1]；全程不重新聚合。
    second = enrich_worker._claim_batch(db, 2)
    assert [r.id for r in second] == [rows[2].id, rows[1].id]
    assert agg_counter["agg"] == 1


# ---------- ② 入库事件即时失效 ----------

def test_new_pending_insert_invalidates_snapshot(db, lib, agg_counter):
    _movies(db, lib, 3)
    first = enrich_worker._claim_batch(db, 2)
    assert len(first) == 2
    assert agg_counter["agg"] == 1
    new_row = _item(db, lib, "新入库")
    db.commit()
    # 新条目同进程立即可见：下一轮重新聚合（计数 +1）且新条目被认领
    second = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2
    assert new_row.id in {r.id for r in second}


def test_series_done_invalidates_snapshot(db, lib, agg_counter):
    series = _item(db, lib, "剧", item_type="series")
    ep = _item(db, lib, "剧 E1", item_type="episode", series_id=series.id,
               season_number=1, episode_number=1)
    db.commit()
    first = enrich_worker._claim_batch(db, 10)
    assert [r.id for r in first] == [series.id]  # 父级未终态，单集被挡
    assert agg_counter["agg"] == 1
    series_row = db.query(em.MediaItem).filter(
        em.MediaItem.id == series.id).first()
    series_row.enrich_status = "done"
    db.commit()
    second = enrich_worker._claim_batch(db, 10)
    assert [r.id for r in second] == [ep.id]
    assert agg_counter["agg"] == 2, "父级终态变化必须让快照失效重聚合"


# ---------- ③ 缓存开/关等价 ----------

def _seed_mixed(db, lib):
    series = _item(db, lib, "剧", item_type="series")
    _item(db, lib, "剧 S1", item_type="season", series_id=series.id,
          season_number=1)
    _item(db, lib, "剧 E1", item_type="episode", series_id=series.id,
          season_number=1, episode_number=1)
    _item(db, lib, "剧 E2", item_type="episode", series_id=series.id,
          season_number=1, episode_number=2)
    movies = [_item(db, lib, f"电影{i}") for i in range(6)]
    db.commit()
    return series, movies


def test_cache_on_off_claim_equivalence(db_factory, monkeypatch):
    results = {}
    for mode in ("on", "off"):
        db = db_factory()
        try:
            lib = em.Library(guid=uuid.uuid4().hex, name="测试库",
                             collection_type="tvshows")
            db.add(lib)
            db.flush()
            _seed_mixed(db, lib)
            if mode == "off":
                monkeypatch.setattr(
                    enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
            else:
                monkeypatch.setattr(
                    enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 30)
            enrich_worker.invalidate_claim_groups_cache()
            rounds = []
            for _ in range(3):
                claimed = enrich_worker._claim_batch(db, 3)
                rounds.append(sorted(r.name for r in claimed))
            results[mode] = rounds
        finally:
            db.close()
    assert results["on"] == results["off"]


# ---------- TTL=0 回到旧路径 ----------

def test_ttl_zero_aggregates_every_round(db, lib, agg_counter, monkeypatch):
    monkeypatch.setattr(enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
    _movies(db, lib, 6)
    enrich_worker._claim_batch(db, 2)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "TTL=0 必须逐字走旧路径：每轮都聚合"
