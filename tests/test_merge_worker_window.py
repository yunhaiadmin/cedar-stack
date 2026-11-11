# -*- coding: utf-8 -*-
"""回归：merge_versions_worker 候选窗口截断导致新入库重复组永远凑不齐。

生产实证（2026-11-11）：find_duplicate_groups 旧实现先按
``ORDER BY id LIMIT MERGE_VERSIONS_BATCH_LIMIT * MERGE_CANDIDATE_MULTIPLIER``
（1000*10=10000 行）取候选行、再在内存按 tmdb 分组。全库候选行 11,453 条，
第 10,000 行 id=2,539,210——新入库的行 id 全部排在窗口之外：对子被窗口
拆散（窗内只剩一条、不成组）或整组落在窗外，工人对新重复组完全瞎眼，
只能靠人工脚本合并存量。

修复口径（本文件钉住）：
- 候选发现改 SQL 聚合下推：按 (library_id, item_type, tmdb_id) 分组、
  HAVING count>=2 在库内找全部重复键，不再有固定行窗口；
- 单轮上限改为「组数上限」（limit 参数，缺省 MERGE_VERSIONS_BATCH_LIMIT）：
  按组内最小 id 升序取组，未取到的组仍是未合并重复，下轮照常发现——
  续跑不丢组；
- 分组语义不变：仍走 _provider_key（同库同类型同 tmdb、无 tmdb 不成组）。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import sys

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.emby_server import merge_versions_worker as mvw
from backend.emby_server import models as em
import backend.models  # noqa: F401 — 注册 web_users 等表，供 create_all 建表


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _movie(db, **kw):
    defaults = dict(
        guid=kw.pop("guid", f"m{kw.get('id', 0)}"),
        library_id=1,
        item_type="movie",
        name="同片",
        production_year=2020,
        file_path="/x/movie - 1080p.mkv",
    )
    defaults.update(kw)
    item = em.MediaItem(**defaults)
    db.add(item)
    db.commit()
    return item


def _group_id_sets(groups):
    return [sorted(it.id for it in g) for g in groups]


def test_pair_both_beyond_window_found_and_merged(db, monkeypatch):
    """对子两条都在旧窗口之外：旧实现凑不齐组，修复后发现并可合并。

    旧窗口 = BATCH_LIMIT(2) * MULTIPLIER(5) = 10 行；先塞 11 条单行候选
    （id 1..11），对子放 id 12/13——旧实现窗口只覆盖 id 1..10，全是单行，
    永远返回空。
    """
    monkeypatch.setattr(mvw, "MERGE_VERSIONS_BATCH_LIMIT", 2)
    monkeypatch.setattr(mvw, "MERGE_CANDIDATE_MULTIPLIER", 5)
    for i in range(1, 12):
        _movie(db, id=i, guid=f"f{i}", tmdb_id=f"single{i}")
    a = _movie(db, id=12, guid="p1", tmdb_id="999",
               file_path="/x/movie - 720p.mkv")
    b = _movie(db, id=13, guid="p2", tmdb_id="999",
               file_path="/x/movie - 2160p.mkv")

    groups = mvw.find_duplicate_groups(db)
    assert _group_id_sets(groups) == [[12, 13]]

    merged = mvw.merge_group(db, groups[0])
    db.commit()
    assert merged == 1
    db.refresh(a)
    db.refresh(b)
    assert (a.merged_into_id is None) != (b.merged_into_id is None)


def test_pair_split_across_window_found(db, monkeypatch):
    """对子被窗口拆散（一条窗内、一条窗外）：旧实现窗内只剩一条不成组。"""
    monkeypatch.setattr(mvw, "MERGE_VERSIONS_BATCH_LIMIT", 2)
    monkeypatch.setattr(mvw, "MERGE_CANDIDATE_MULTIPLIER", 5)
    _movie(db, id=1, guid="p1", tmdb_id="888")
    for i in range(2, 12):
        _movie(db, id=i, guid=f"f{i}", tmdb_id=f"single{i}")
    _movie(db, id=12, guid="p2", tmdb_id="888")

    assert _group_id_sets(mvw.find_duplicate_groups(db)) == [[1, 12]]


def test_round_limit_and_continuation_loses_no_group(db):
    """单轮组数上限 + 续跑不丢组：3 组、每轮上限 2 组，逐轮合并后清空。"""
    for n, base in enumerate((100, 200, 300)):
        _movie(db, id=base + 1, guid=f"g{n}a", tmdb_id=f"dup{n}")
        _movie(db, id=base + 2, guid=f"g{n}b", tmdb_id=f"dup{n}")

    first = mvw.find_duplicate_groups(db, limit=2)
    assert _group_id_sets(first) == [[101, 102], [201, 202]]
    for g in first:
        mvw.merge_group(db, g)
    db.commit()

    second = mvw.find_duplicate_groups(db, limit=2)
    assert _group_id_sets(second) == [[301, 302]]
    for g in second:
        mvw.merge_group(db, g)
    db.commit()

    assert mvw.find_duplicate_groups(db, limit=2) == []
