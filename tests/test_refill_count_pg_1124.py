"""补拉计数查询 PG GroupingError 回归（2026-11-24，#490 部署后生产实测）。

根因：``enqueue_details_refill`` 的人群查询带 ``.order_by(MediaItem.id)``，
计数时 ``q.with_entities(func.count(...)).scalar()`` 仍保留 ORDER BY，
Postgres 拒绝「聚合查询 ORDER BY 非聚合列」报 GroupingError，
dry-run 即炸、--commit 同样炸在同一行。SQLite 容忍该写法，
``test_cast_pipeline_1124.py`` 的 SQLite 夹具没拦住。

本文件只在真 PG 下跑（CI 后端·PG 兼容冒烟的 DATABASE_URL），其余环境跳过，
与 ``test_coupons_reserve_race.py`` 同口径。
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.emby_server import enrich_worker as ew
from backend.emby_server import models as em


def _pg_url():
    """CI 后端·PG 兼容冒烟的 DATABASE_URL；非 PG 返回 None（调用方跳过）。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    if url.startswith("postgresql://"):
        # 与 backend/database.py 同一口径：没装 psycopg(v3) 就显式走 psycopg2
        try:
            import psycopg  # noqa: F401
        except ImportError:
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _purge_stale_refill_rows(s):
    """清掉以往崩掉的轮次残留的 refill-pg-* 库行：人群计数是全库口径，
    残留行会直接污染 matched，必须先清再断言。"""
    stale_ids = [r[0] for r in s.query(em.Library.id).filter(
        em.Library.guid.like("refill-pg-%")).all()]
    if not stale_ids:
        return
    s.query(em.EmbyPerson).filter(em.EmbyPerson.item_id.in_(
        s.query(em.MediaItem.id).filter(
            em.MediaItem.library_id.in_(stale_ids)))).delete(
                synchronize_session=False)
    s.query(em.MediaItem).filter(
        em.MediaItem.library_id.in_(stale_ids)).delete(
            synchronize_session=False)
    s.query(em.Library).filter(em.Library.id.in_(stale_ids)).delete(
        synchronize_session=False)
    s.commit()


@pytest.mark.skipif(_pg_url() is None, reason="需要真 PG（DATABASE_URL=postgresql://...）")
def test_enqueue_details_refill_count_with_order_by_pg():
    """人群计数在 PG 下不许带 ORDER BY：未修时这里直接报 GroupingError。"""
    engine = create_engine(_pg_url())
    # trigram GIN 索引依赖 pg_trgm 扩展；与 backend/database.py 的 init_db()
    # 同口径先装扩展（IF NOT EXISTS 幂等，PG13 起为 trusted 扩展）。
    with engine.begin() as _conn:
        _conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    for table in (em.Library.__table__, em.MediaItem.__table__,
                  em.EmbyPerson.__table__):
        table.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    s = Session()
    suffix = uuid.uuid4().hex[:8]
    ids: list = []
    try:
        _purge_stale_refill_rows(s)
        lib = em.Library(guid=f"refill-pg-{suffix}", name=f"补拉回归-{suffix}")
        s.add(lib)
        s.commit()
        a = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                         item_type="series", name="甲剧", file_path="/tmp/a.mp4",
                         enrich_status="done", tmdb_id="101",
                         overview=None, genres=None)
        b = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                         item_type="movie", name="乙片", file_path="/tmp/b.mp4",
                         enrich_status="done", tmdb_id="102",
                         overview="有简介", genres="剧情", community_rating=7.5)
        c = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                         item_type="series", name="丙剧", file_path="/tmp/c.mp4",
                         enrich_status="done", tmdb_id="103",
                         overview="有", genres="剧情", community_rating=8.0)
        d = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                         item_type="series", name="丁剧", file_path="/tmp/d.mp4",
                         enrich_status="done", overview=None)  # 无编号，不进人群
        s.add_all([a, b, c, d])
        s.commit()
        ids = [a.id, b.id, c.id, d.id]
        s.add(em.EmbyPerson(item_id=c.id, name="现成演员", sort_order=0))
        s.commit()

        dry = ew.enqueue_details_refill(s, dry_run=True)
        assert dry["matched"] == 2, dry
        assert set(dry["ids"]) == {a.id, b.id}
        s.refresh(a)
        assert a.enrich_status == "done", "dry-run 不许改状态"

        out = ew.enqueue_details_refill(s)
        assert out["matched"] == 2 and out["enqueued"] == 2, out
        s.commit()
        for it in (a, b):
            s.refresh(it)
            assert it.enrich_status == "pending"
            assert it.enrich_priority == ew.ENRICH_PRIORITY_FIELD_REFILL == -10
            assert it.enrich_field_refill_done is True

        again = ew.enqueue_details_refill(s)
        assert again["matched"] == 0, "已入队的人群不许重复入队"
    finally:
        # 失败轮次（尤其 GroupingError）会让会话处于 aborted 事务态，
        # 必须先 rollback，否则清理删不掉、还会继续污染下一轮。
        s.rollback()
        if ids:
            s.query(em.EmbyPerson).filter(
                em.EmbyPerson.item_id.in_(ids)).delete(
                    synchronize_session=False)
            s.query(em.MediaItem).filter(
                em.MediaItem.id.in_(ids)).delete(synchronize_session=False)
        s.query(em.Library).filter(
            em.Library.guid.like("refill-pg-" + suffix)).delete()
        s.commit()
        s.close()
        engine.dispose()
