"""A3（2026-11-12 服务器减负审计）：upstream_item_mappings 空表短路兜底。

背景：生产实测该表 0 行（索引却占约 246M，回收走单独 DDL、不在本单），
历史上却有约 307 万次点查打在它上面。映射层加「表空」负缓存（沿
integrations.store 的短 TTL 口径：进程内缓存 + 写时失效 + 跨进程 TTL
兜底）后，空表期间读路径不再逐条目查这张表。

钉死：
① 空表时：第一笔读至多一条 LIMIT 1 探针，之后 get_best / get_all /
   get_grouped / migrate 对该表的语句数恒为 0，结果与无短路时一致
   （None / [] / [] / 0）；旧实现每笔都查，本组断言先红后绿；
② 有映射时行为不变：写后读得到、upsert 仍只摸一行、migrate 正常迁；
③ 写时失效：经 record_mapping 或直接 ORM db.add 写入后，本进程的读
   立即看得见新行，不等 TTL 过期。
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import upstream_item_mappings as uim


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


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    monkeypatch.setenv(uim.MAPPING_ENABLED_ENV, "1")


@pytest.fixture(autouse=True)
def _fresh_negative_cache():
    with uim._empty_table_lock:
        uim._empty_table_by_bind.clear()
    yield
    with uim._empty_table_lock:
        uim._empty_table_by_bind.clear()


@pytest.fixture(autouse=True)
def _seed_live_rows(db):
    """读契约要验本地活行与源 enabled：预置条目 10/11 与源 3/5。"""
    library = em.Library(guid="lib-shortcircuit", name="短路测试库")
    db.add(library)
    db.flush()
    for item_id in (10, 11):
        db.add(em.MediaItem(
            id=item_id, guid=f"item-shortcircuit-{item_id}",
            library_id=library.id, item_type="movie",
            name=f"片{item_id}", file_path=f"mount://1/{item_id}.mp4"))
    for source_id in (3, 5):
        db.add(em.UpstreamSource(
            id=source_id, name=f"src-{source_id}", source_type="emby",
            base_url="https://example.invalid", auth_method="api_key",
            enabled=True))
    db.commit()


class _MappingTableCounter:
    """数打在 upstream_item_mappings 上的语句（探针 / 查询 / 写都算）。"""

    def __init__(self, engine):
        self.count = 0
        self._engine = engine
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            if "upstream_item_mappings" in statement:
                self.count += 1

        self._listener = _count
        event.listen(self._engine, "before_cursor_execute", _count)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._listener)
        return False

    def reset(self):
        self.count = 0


# ---------- ① 空表：探针一次，之后全短路 ----------

def test_empty_table_reads_shortcircuit(db_factory, db):
    _factory, engine = db_factory
    with _MappingTableCounter(engine) as counter:
        # 第一笔读：至多一条探针确认表空
        assert uim.get_best_mapping(db, 10) is None
        assert counter.count <= 1

        counter.reset()
        # 之后所有读口（含换条目读）对该表零语句，结果与无短路一致
        assert uim.get_best_mapping(db, 10) is None
        assert uim.get_best_mapping(db, 11) is None
        assert uim.get_all_mappings(db, 10) == []
        assert uim.get_grouped_mappings(db, 10) == []
        assert uim.migrate_mappings(db, 10, 11) == 0
        assert counter.count == 0


# ---------- ② 有映射时行为不变 ----------

def test_behavior_unchanged_with_mappings(db_factory, db):
    _factory, engine = db_factory
    row = uim.record_mapping(db, local_item_id=10, source_id=3,
                             upstream_item_id="abc123", confidence=0.9,
                             evidence="tmdb")
    assert row is not None
    db.commit()

    with _MappingTableCounter(engine) as counter:
        best = uim.get_best_mapping(db, 10)
        assert best is not None
        assert (best.source_id, best.upstream_item_id) == (3, "abc123")
        assert len(uim.get_all_mappings(db, 10)) == 1
        grouped = uim.get_grouped_mappings(db, 10)
        assert [g.upstream_item_id for g in grouped] == ["abc123"]
        # 表里有行：读路径照常查库（短路只在整表为空时生效）
        assert counter.count > 0

    # upsert 幂等不变：同三元组重写仍只有一行、置信度被更新
    uim.record_mapping(db, local_item_id=10, source_id=3,
                       upstream_item_id="abc123", confidence=0.7,
                       evidence="imdb")
    db.commit()
    assert db.query(em.UpstreamItemMapping).count() == 1
    assert uim.get_best_mapping(db, 10).confidence == 0.7

    # migrate 口径不变：迁到条目 11 后旧条目读空、新条目读得到
    assert uim.migrate_mappings(db, 10, 11) == 1
    db.commit()
    assert uim.get_best_mapping(db, 10) is None
    moved = uim.get_best_mapping(db, 11)
    assert moved is not None and moved.upstream_item_id == "abc123"


# ---------- ③ 写时失效：不用等 TTL ----------

def test_record_write_invalidates_negative_cache(db_factory, db):
    _factory, engine = db_factory
    with _MappingTableCounter(engine) as counter:
        assert uim.get_best_mapping(db, 10) is None  # 先把「表空」缓存住
        counter.reset()
        uim.record_mapping(db, local_item_id=10, source_id=3,
                           upstream_item_id="zzz999", confidence=0.5,
                           evidence="tmdb")
        db.commit()
        best = uim.get_best_mapping(db, 10)
        assert best is not None and best.upstream_item_id == "zzz999"


def test_direct_orm_insert_invalidates_negative_cache(db_factory, db):
    _factory, engine = db_factory
    assert uim.get_best_mapping(db, 10) is None  # 先把「表空」缓存住
    # 旁路写入（苹果 CMS 同步等直接 db.add 的路径）同样立即作废标记
    db.add(em.UpstreamItemMapping(local_item_id=10, source_id=3,
                                  upstream_item_id="raw001",
                                  confidence=0.8, evidence="tmdb",
                                  episode_index=None))
    db.commit()
    best = uim.get_best_mapping(db, 10)
    assert best is not None and best.upstream_item_id == "raw001"
