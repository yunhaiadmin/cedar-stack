"""A2（2026-11-12 服务器减负审计）：_claim_batch 组聚合单查回归兜底。

背景：组聚合 GROUP BY coalesce(series_id, id) 是生产第一烧 CPU 的查询
（pg_stat_statements 累计约 28 万次、单次约 1.2~1.6 秒）。旧 _claim_batch
为装满批次按 20 → 100 → 400 递增重查，每一档都把整个 pending 全集重新
聚合一遍——装满一批最多烧 3 遍。本单合并为一轮单查（上限取旧封顶 400），
并给 idx_item_enrich_claim_cover 补齐聚合/成员/EXISTS 实际读列（见
test_legacy_indexes 的 spec 断言；生产同量级 EXPLAIN 复现 1869ms →
约 58ms、index-only、Heap Fetches 归零）。

钉死：
① 一轮 _claim_batch 里组聚合恰好执行 1 次（旧实现 ≥2 次，先红后绿）；
② 领取语义不变：limit 恰好封顶、逐轮排空不重不漏；
③ 并发领取不重不漏（sqlite 文件库多线程；PG SKIP LOCKED 真并发由
   test_enrich_claim_skiplocked_race 在 PG 冒烟里兜底，本单未动
   _claim_group_atomic 与 FOR UPDATE 路径）。
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from backend import models as base_models
from backend.emby_server import enrich_worker
from backend.emby_server import models as em


def _make_item(db, lib, name, days_ago=0, **kw):
    kw.setdefault("enrich_status", "pending")
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
        name=name, sort_name=name,
        file_path=f"/m/{uuid.uuid4().hex}.mkv",
        date_added=datetime(2026, 1, 1) + timedelta(days=days_ago), **kw)
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine), engine


@pytest.fixture()
def db(db_factory):
    factory, _engine = db_factory
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def lib(db):
    row = em.Library(guid=uuid.uuid4().hex, name="单查测试库",
                     collection_type="movies")
    db.add(row)
    db.flush()
    return row


# ---------- ① 组聚合一轮只查一次 ----------

def test_groups_aggregation_runs_once_per_batch(db_factory, db, lib):
    factory, engine = db_factory
    for i in range(60):
        _make_item(db, lib, f"片{i}", days_ago=i)
    db.commit()

    group_bys: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if "GROUP BY" in statement.upper():
            group_bys.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        claimed = enrich_worker._claim_batch(db, 200)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    # 60 个单行组（电影）一轮全领走，且组聚合只跑了 1 次
    # （旧递增实现：20 组一轮 → 重查 100 → 共 2 次，本断言先红后绿）。
    assert len(claimed) == 60
    assert len(group_bys) == 1


# ---------- ② limit 封顶 + 逐轮排空不重不漏 ----------

def test_single_fetch_respects_limit_and_drains(db, lib):
    ids = [_make_item(db, lib, f"片{i}", days_ago=i).id for i in range(60)]
    db.commit()

    seen: list[int] = []
    for want in (25, 25, 10, 0):
        batch = enrich_worker._claim_batch(db, 25)
        assert len(batch) == want
        seen.extend(row.id for row in batch)
    assert sorted(seen) == sorted(ids)  # 不重（长度守恒）不漏（全集相等）


# ---------- ③ 并发领取不重不漏（sqlite 文件库多线程） ----------

def test_concurrent_claim_no_dup_no_loss(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}",
                           connect_args={"check_same_thread": False},
                           poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    seed = factory()
    library = em.Library(guid=uuid.uuid4().hex, name="并发测试库",
                         collection_type="movies")
    seed.add(library)
    seed.flush()
    all_ids = [_make_item(seed, library, f"片{i}", days_ago=i).id
               for i in range(40)]
    seed.commit()
    seed.close()

    claimed: list[int] = []
    lock = threading.Lock()

    def _drain():
        session = factory()
        try:
            while True:
                rows = enrich_worker._claim_batch(session, 7)
                if not rows:
                    break
                with lock:
                    claimed.extend(row.id for row in rows)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: _drain(), range(4)))

    assert len(claimed) == len(set(claimed)), "同一行被两个 worker 重复领走"
    assert sorted(claimed) == sorted(all_ids), "有 pending 行没被任何一轮领走"
    engine.dispose()
