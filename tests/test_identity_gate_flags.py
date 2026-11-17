"""身份准入货架·判据物化标记（BE-24）。

背景（网页媒体库审计 BE-24）：旧 ``identity_clause()`` 是「自身两列判定
OR series EXISTS 子查询 + external_ids 七键 LIKE 逐行求值」，不可索引——
生产实测 /Items/Counts 两条带门计数各约 13s，只能靠 300s 计数缓存摊薄。

本批把判据物化为条目上的两列（写路径由 identity_gate 的 flush 同步钩子
维护，存量由 scripts/backfill_identity_flags.py 回填）：

- ``identity_bound``：自身已绑定（与 ``is_bound_row`` 的 Python 口径逐字
  一致：tmdb_id / imdb_id 任一 strip 后非空，或 external_ids 任一在用
  来源键非空）；
- ``has_identity``：货架判据的物化结果（movie/series = 自身已绑定；
  season/episode = 自身已绑定或所属 series 已绑定且未软删；其余类型
  恒 True，不在治理内）。

``identity_clause()`` 改走 ``has_identity`` 列，本文件钉住：

1. 子句结构：编译出的 SQL 不含 LIKE / EXISTS（不可索引形态回归即红）；
2. 口径对齐：新子句结果集与旧子句（本文件按旧实现复刻）逐行一致，
   每行 has_identity / identity_bound 与 Python 判据一致；
3. 已知分歧（旧 SQL 与 Python 的历史分歧，_external_bound_sql docstring
   已声明「口径以 Python 侧为准」）：external_ids 数值型值、纯空白值——
   旧 LIKE 只认字符串形，物化标记按 Python 判据走，单独钉住不混进
   严格对齐场景；
4. 写路径维护：series 绑定/解绑/软删/复活（含 soft_delete 的 Core 批量
   路径）都级联刷新其 season/episode 的 has_identity；同 flush 内新建
   series + 经 relationship 挂上的 episode 也算对；
5. 回填脚本：Core 直插（标记 NULL）的存量经 dry-run 对账、--commit
   回填后与判据逐行一致，重跑幂等。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, exists, func, or_, and_, select, text
from sqlalchemy.orm import aliased, sessionmaker

from backend import models  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import identity_gate
from backend.emby_server import models as em
from backend.emby_server import soft_delete


@pytest.fixture()
def db():
    # 内存库：本文件只验判据/维护逻辑，不依赖文件型库的持久化行为
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _mk(db, lib, item_type, name, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name, file_path=f"/m/{uuid.uuid4().hex}.mkv",
        **kw)
    db.add(it)
    db.flush()
    return it


def _legacy_clause():
    """旧 identity_clause 逐字复刻（BE-24 改动前实现），作对齐基准。"""
    series_alias = aliased(em.MediaItem)
    series_bound = exists(
        select(series_alias.id)
        .where(
            series_alias.id == em.MediaItem.series_id,
            series_alias.deleted_at.is_(None),
            identity_gate._own_bound(series_alias),
        )
        .correlate(em.MediaItem)
    )
    top = ("movie", "series")
    child = ("season", "episode")
    return or_(
        and_(em.MediaItem.item_type.in_(top),
             identity_gate._own_bound(em.MediaItem)),
        and_(
            em.MediaItem.item_type.in_(child),
            or_(identity_gate._own_bound(em.MediaItem), series_bound),
        ),
        ~em.MediaItem.item_type.in_(top + child),
    )


def _ids(db, clause):
    rows = db.query(em.MediaItem.id).filter(clause).all()
    return {r[0] for r in rows}


def _python_verdict(db, item):
    """按 Python 口径逐行算货架判据（与物化标记的应然值）。"""
    if item.item_type in ("movie", "series"):
        return identity_gate.is_bound_row(item)
    if item.item_type in ("season", "episode"):
        if identity_gate.is_bound_row(item):
            return True
        if item.series_id is None:
            return False
        series = (db.query(em.MediaItem)
                  .filter(em.MediaItem.id == item.series_id)
                  .first())
        # 全局软删钩子会把已删 series 滤掉 → None 即「不能替集绑定」
        return bool(series is not None
                    and identity_gate.is_bound_row(series))
    return True


@pytest.fixture()
def lib(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="库",
                     collection_type="mixed", is_enabled=True)
    db.add(lib)
    db.flush()
    return lib


@pytest.fixture()
def scene(db, lib):
    m = {}
    m["m_tmdb"] = _mk(db, lib, "movie", "有TMDB", tmdb_id="123")
    m["m_imdb"] = _mk(db, lib, "movie", "只有IMDb", imdb_id="tt0000001")
    m["m_none"] = _mk(db, lib, "movie", "无身份")
    m["m_blank"] = _mk(db, lib, "movie", "空白TMDB", tmdb_id="   ")
    m["m_empty"] = _mk(db, lib, "movie", "空串TMDB", tmdb_id="")
    m["m_douban"] = _mk(db, lib, "movie", "只有豆瓣",
                        external_ids='{"douban": "1234567"}')
    m["m_douban_nospace"] = _mk(db, lib, "movie", "豆瓣无空格形",
                                external_ids='{"douban":"777"}')
    m["m_bangumi"] = _mk(db, lib, "movie", "只有Bangumi",
                         external_ids='{"bangumi": "12345"}')
    m["m_anilist"] = _mk(db, lib, "movie", "只有AniList",
                         external_ids='{"anilist": "678"}')
    m["m_tvmaze"] = _mk(db, lib, "movie", "只有TVmaze",
                        external_ids='{"tvmaze": "82"}')
    m["m_tvdb"] = _mk(db, lib, "series", "只有TVDB",
                      external_ids='{"tvdb": "81189"}')
    m["m_ext_empty"] = _mk(db, lib, "movie", "豆瓣空值",
                           external_ids='{"douban": ""}')
    m["m_ext_unknown"] = _mk(db, lib, "movie", "未知来源键",
                             external_ids='{"wikidata": "Q123"}')
    m["m_ext_bad"] = _mk(db, lib, "movie", "坏JSON",
                         external_ids='not-a-json')
    m["s_douban"] = _mk(db, lib, "series", "只有豆瓣剧",
                        external_ids='{"douban": "23456"}')
    m["ep_douban_follow"] = _mk(db, lib, "episode", "集-跟随豆瓣剧",
                                series_id=m["s_douban"].id,
                                season_number=1, episode_number=1)
    m["s_bound"] = _mk(db, lib, "series", "已绑定剧", tmdb_id="999")
    m["s_unbound"] = _mk(db, lib, "series", "未绑定剧")
    m["season_bound"] = _mk(db, lib, "season", "季-已绑定剧",
                            series_id=m["s_bound"].id, season_number=1)
    m["season_unbound"] = _mk(db, lib, "season", "季-未绑定剧",
                              series_id=m["s_unbound"].id, season_number=1)
    m["ep_own"] = _mk(db, lib, "episode", "集-自身有号未绑定剧",
                      series_id=m["s_unbound"].id, tmdb_id="555",
                      season_number=1, episode_number=1)
    m["ep_follow"] = _mk(db, lib, "episode", "集-跟随已绑定剧",
                         series_id=m["s_bound"].id,
                         season_number=1, episode_number=1)
    m["ep_none"] = _mk(db, lib, "episode", "集-跟随未绑定剧",
                       series_id=m["s_unbound"].id,
                       season_number=1, episode_number=2)
    m["ep_noseries"] = _mk(db, lib, "episode", "集-无所属剧",
                           season_number=1, episode_number=9)
    m["s_deleted"] = _mk(db, lib, "series", "已删剧", tmdb_id="777",
                         deleted_at=datetime(2026, 10, 24))
    m["ep_deleted_series"] = _mk(db, lib, "episode", "集-所属剧已删",
                                  series_id=m["s_deleted"].id,
                                  season_number=1, episode_number=1)
    m["season_deleted_series"] = _mk(db, lib, "season", "季-所属剧已删",
                                      series_id=m["s_deleted"].id,
                                      season_number=1)
    m["person"] = _mk(db, lib, "person", "某演员")
    db.commit()
    return m


# ---------- 1. 子句结构 ----------

def test_clause_is_column_only_no_like_no_exists():
    sql = str(identity_gate.identity_clause()).upper()
    assert "LIKE" not in sql, "新子句不许再有 external_ids LIKE 逐行求值"
    assert "EXISTS" not in sql, "新子句不许再有 series EXISTS 子查询"
    assert "HAS_IDENTITY" in sql, "新子句必须走 has_identity 物化列"


# ---------- 2. 口径对齐 ----------

def test_flags_parity_with_legacy_clause(db, scene):
    db.expire_all()
    legacy_ids = _ids(db, _legacy_clause())
    new_ids = _ids(db, identity_gate.identity_clause())
    assert new_ids == legacy_ids, "新子句结果集必须与旧子句逐行一致"
    items = db.query(em.MediaItem).all()
    assert items, "场景不能为空"
    for it in items:
        assert it.identity_bound is not None, f"{it.name} identity_bound 未维护"
        assert it.has_identity is not None, f"{it.name} has_identity 未维护"
        assert bool(it.identity_bound) == identity_gate.is_bound_row(it), \
            f"{it.name} identity_bound 与 Python 自身判据不一致"
        assert bool(it.has_identity) == _python_verdict(db, it), \
            f"{it.name} has_identity 与 Python 货架判据不一致"
        assert (it.id in legacy_ids) == bool(it.has_identity), \
            f"{it.name} has_identity 与旧子句结果不一致"


def test_counts_parity_by_type(db, scene):
    def counts(clause):
        rows = (db.query(em.MediaItem.item_type, func.count())
                .filter(clause).group_by(em.MediaItem.item_type).all())
        return dict(rows)
    assert counts(identity_gate.identity_clause()) == counts(_legacy_clause())


def test_python_canonical_divergences(db, lib):
    """旧 SQL LIKE 与 Python 判据的历史分歧两例：物化标记按 Python 走
    （identity_gate docstring 已声明口径以 Python 侧为准；这两类值写入方
    实际不会产生，仅钉住行为防将来无声漂移）。"""
    numeric = _mk(db, lib, "movie", "数值型AniList",
                  external_ids='{"anilist": 123}')
    blank = _mk(db, lib, "movie", "空白豆瓣值",
                external_ids='{"douban": "  "}')
    db.commit()
    db.expire_all()
    # Python 判据：数值非空算绑定；纯空白不算
    assert identity_gate.is_bound_row(
        SimpleNamespace(tmdb_id=None, imdb_id=None,
                        external_ids='{"anilist": 123}')) is True
    assert identity_gate.is_bound_row(
        SimpleNamespace(tmdb_id=None, imdb_id=None,
                        external_ids='{"douban": "  "}')) is False
    assert bool(numeric.has_identity) is True
    assert bool(blank.has_identity) is False
    new_ids = _ids(db, identity_gate.identity_clause())
    assert numeric.id in new_ids
    assert blank.id not in new_ids


# ---------- 3. 写路径维护 ----------

def test_series_bind_unbind_cascades(db, lib):
    s = _mk(db, lib, "series", "待绑定剧")
    ep = _mk(db, lib, "episode", "集", series_id=s.id,
             season_number=1, episode_number=1)
    season = _mk(db, lib, "season", "季", series_id=s.id, season_number=1)
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False
    assert bool(season.has_identity) is False
    # 刮削给剧补上身份 → 季/集级联变已绑定
    s.external_ids = '{"douban": "31337"}'
    db.commit()
    db.expire_all()
    assert bool(s.has_identity) is True
    assert bool(ep.has_identity) is True, "剧绑定后集应级联已绑定"
    assert bool(season.has_identity) is True
    # 身份被清掉 → 级联回落（集自身无身份 → False）
    s.external_ids = None
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False
    assert bool(season.has_identity) is False


def test_episode_own_identity_survives_series_unbind(db, lib):
    s = _mk(db, lib, "series", "剧", tmdb_id="42")
    ep = _mk(db, lib, "episode", "自有号集", series_id=s.id, tmdb_id="4242",
             season_number=1, episode_number=1)
    db.commit()
    s.tmdb_id = None
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is True, "集自身有号，剧解绑后仍已绑定"
    ep.tmdb_id = "   "
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False, "集自身只剩空白号应回落未绑定"


def test_series_soft_delete_revive_orm_cascades(db, lib):
    s = _mk(db, lib, "series", "剧", tmdb_id="43")
    ep = _mk(db, lib, "episode", "集", series_id=s.id,
             season_number=1, episode_number=1)
    db.commit()
    s.deleted_at = datetime(2026, 11, 17)
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False, "剧软删后不能替集绑定"
    # 复活要先让 s 能被刷新到（全局钩子挡已删行，须在 include_deleted 里改）
    with soft_delete.include_deleted():
        s.deleted_at = None
        db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is True, "剧复活后集恢复跟随"


def test_soft_delete_core_paths_resync(db, lib):
    s = _mk(db, lib, "series", "剧", tmdb_id="44")
    ep = _mk(db, lib, "episode", "集", series_id=s.id,
             season_number=1, episode_number=1)
    s_id = s.id  # 软删后 s 对象被全局钩子挡刷新，后面只用 id 操作
    db.commit()
    # soft_delete 的 Core 批量 UPDATE 不走 ORM 事件，靠显式 resync 兜住
    assert soft_delete.mark_soft_deleted(db, [s_id]) == 1
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False
    assert soft_delete.resurrect(db, [s_id]) == 1
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is True


def test_same_flush_series_and_episode_via_relationship(db, lib):
    s = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                     item_type="series", name="同flush剧", sort_name="同flush剧",
                     file_path="/m/x.mkv", tmdb_id="45")
    ep = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                      item_type="episode", name="同flush集", sort_name="同flush集",
                      file_path="/m/y.mkv", season_number=1, episode_number=1)
    ep.series = s  # 只经 relationship 关联，flush 前 series_id 还是 None
    db.add_all([s, ep])
    db.commit()
    db.expire_all()
    assert ep.series_id == s.id
    assert bool(ep.has_identity) is True, \
        "同 flush 新建剧+集（relationship 关联）也必须算对"


def test_child_reparented_to_bound_series(db, lib):
    s_unbound = _mk(db, lib, "series", "未绑定剧")
    s_bound = _mk(db, lib, "series", "已绑定剧", tmdb_id="46")
    ep = _mk(db, lib, "episode", "集", series_id=s_unbound.id,
             season_number=1, episode_number=1)
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is False
    ep.series_id = s_bound.id
    db.commit()
    db.expire_all()
    assert bool(ep.has_identity) is True, "集改挂到已绑定剧应跟随变已绑定"


# ---------- 4. 索引 ----------

def test_explain_uses_identity_index(db, lib):
    # 造量 + ANALYZE，让 SQLite 规划器在选择性谓词上选索引（小表必全扫，
    # 断言的是「有索引可用且被选中」，量级与 bench 同理）
    rows = []
    tbl = em.MediaItem.__table__
    for i in range(4000):
        rows.append({
            "guid": uuid.uuid4().hex, "library_id": lib.id,
            "item_type": "movie", "name": f"影{i}", "sort_name": f"影{i}",
            "file_path": f"/m/{i}.mkv", "is_hidden": False,
            "tmdb_id": "7" if i % 50 == 0 else None,
            "identity_bound": i % 50 == 0, "has_identity": i % 50 == 0,
        })
    db.connection().execute(tbl.insert(), rows)
    db.commit()
    db.connection().execute(text("ANALYZE"))
    plan = db.connection().execute(text(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM emby_items "
        "WHERE item_type = 'movie' AND has_identity = 1 AND library_id = :lib"
    ), {"lib": lib.id}).fetchall()
    details = " | ".join(str(r[-1]) for r in plan)
    assert "idx_item_identity_gate" in details, \
        f"带门计数谓词应走 idx_item_identity_gate，实际计划：{details}"


# ---------- 5. 回填脚本 ----------

def test_backfill_script_reconciles(db, lib):
    from scripts.backfill_identity_flags import backfill_identity_flags
    # Core 直插模拟存量（标记列全 NULL，钩子不触发）
    s = _mk(db, lib, "series", "已绑定剧", tmdb_id="47")
    db.commit()
    tbl = em.MediaItem.__table__
    db.connection().execute(tbl.insert(), [
        {"guid": uuid.uuid4().hex, "library_id": lib.id,
         "item_type": "episode", "name": "存量集", "sort_name": "存量集",
         "file_path": "/m/old1.mkv", "is_hidden": False,
         "series_id": s.id, "season_number": 1, "episode_number": 1,
         "external_ids": None},
        {"guid": uuid.uuid4().hex, "library_id": lib.id,
         "item_type": "movie", "name": "存量未绑定影", "sort_name": "存量未绑定影",
         "file_path": "/m/old2.mkv", "is_hidden": False,
         "series_id": None, "season_number": None,
         "episode_number": None, "external_ids": None},
        {"guid": uuid.uuid4().hex, "library_id": lib.id,
         "item_type": "movie", "name": "存量豆瓣影", "sort_name": "存量豆瓣影",
         "file_path": "/m/old3.mkv", "is_hidden": False,
         "series_id": None, "season_number": None,
         "episode_number": None, "external_ids": '{"douban": "555"}'},
    ])
    db.commit()
    dry = backfill_identity_flags(db, dry_run=True)
    assert dry["mismatch"] >= 3, f"dry-run 应报出存量未回填行：{dry}"
    done = backfill_identity_flags(db, dry_run=False)
    assert done["updated"] >= 3
    again = backfill_identity_flags(db, dry_run=True)
    assert again["mismatch"] == 0, f"回填后应对账为零差异：{again}"
    db.expire_all()
    items = {it.name: it for it in db.query(em.MediaItem).all()}
    assert bool(items["存量集"].has_identity) is True
    assert bool(items["存量未绑定影"].has_identity) is False
    assert bool(items["存量豆瓣影"].has_identity) is True
