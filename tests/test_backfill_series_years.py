"""剧集年份回填例程（scripts/backfill_series_years.py）——审计 PD-01。

钉死：
- 目标只限 live（未合并、未软删）且 production_year 与
  premiere_date 双空的 series；任一列已有值的行不碰；
- 第 1 级集年份取同剧 live 集行 production_year 最小值，
  合并/软删集不算，只填 production_year、不动 premiere_date；
- 第 2 级 TMDB 只喂「无集年份且有 tmdb_id」的行，kind 恒
  series，年份与首播日期都只填空；
- 无集年份且无 tmdb_id 计 no_source，不猜；
- dry-run 只计数不写库。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from scripts.backfill_series_years import (
    backfill_series_years,
    episode_year_map,
)


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


def _library(db, name):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     collection_type="tvshows", kind="local")
    db.add(lib)
    db.flush()
    return lib


def _series(db, lib, name, **kw):
    kw.setdefault("enrich_status", "done")
    kw.setdefault("item_type", "series")
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


def _episode(db, lib, series, name, **kw):
    kw.setdefault("enrich_status", "done")
    kw.setdefault("item_type", "episode")
    kw.setdefault("series_id", series.id)
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


def _no_details(tmdb_id, kind):  # pragma: no cover - 不应被调用的桩
    raise AssertionError("集年份可覆盖时不许调 details")


# ---------- 集年份映射 ----------

def test_episode_year_map_takes_min_of_live_episodes(db):
    lib = _library(db, "剧集库")
    s = _series(db, lib, "某剧")
    _episode(db, lib, s, "S02E01", production_year=2021)
    _episode(db, lib, s, "S01E01", production_year=2019)
    _episode(db, lib, s, "合并集", production_year=1999, merged_into_id=42)
    assert episode_year_map(db) == {s.id: 2019}


# ---------- 回填主流程 ----------

def test_episode_tier_fills_year_only_and_dry_run_writes_nothing(db):
    lib = _library(db, "剧集库")
    s = _series(db, lib, "某剧")
    _episode(db, lib, s, "S01E01", production_year=2019)
    stats = backfill_series_years(db, dry_run=True,
                                  details_fetcher=_no_details)
    assert stats["total"] == 1 and stats["episode_filled"] == 1
    db.refresh(s)
    assert s.production_year is None
    stats = backfill_series_years(db, dry_run=False,
                                  details_fetcher=_no_details)
    assert stats["episode_filled"] == 1
    db.refresh(s)
    assert s.production_year == 2019
    assert s.premiere_date is None  # 集行无日期，不许凭空造


def test_tmdb_tier_fills_year_and_premiere_for_episodeless_series(db):
    lib = _library(db, "剧集库")
    s = _series(db, lib, "无集剧", tmdb_id="12345")
    seen = {}

    def fake_details(tmdb_id, kind):
        seen["args"] = (tmdb_id, kind)
        return {"first_air_date": "2008-01-20"}

    stats = backfill_series_years(db, dry_run=False,
                                  details_fetcher=fake_details)
    assert seen["args"] == ("12345", "series")
    assert stats["tmdb_filled"] == 1
    db.refresh(s)
    assert s.production_year == 2008
    assert s.premiere_date is not None and s.premiere_date.year == 2008


def test_no_source_counted_and_untouched(db):
    lib = _library(db, "剧集库")
    s = _series(db, lib, "无源剧")
    _episode(db, lib, s, "无年份集", production_year=None)
    stats = backfill_series_years(db, dry_run=False,
                                  details_fetcher=_no_details)
    assert stats["total"] == 1
    assert stats["no_source"] == 1 and stats["still_missing"] == 1
    db.refresh(s)
    assert s.production_year is None and s.premiere_date is None


def test_non_double_null_and_dead_rows_are_not_targets(db):
    lib = _library(db, "剧集库")
    from datetime import datetime

    has_year = _series(db, lib, "有年份", production_year=2010)
    has_prem = _series(db, lib, "有首播", tmdb_id="777",
                       premiere_date=datetime(2015, 1, 1))
    merged = _series(db, lib, "合并行", merged_into_id=99)
    for s in (has_year, has_prem, merged):
        _episode(db, lib, s, "集", production_year=2001)
    stats = backfill_series_years(db, dry_run=False,
                                  details_fetcher=_no_details)
    assert stats["total"] == 0
    db.refresh(has_prem)
    assert has_prem.production_year is None  # 不是双空，不在本脚本范围


def test_tmdb_without_air_date_stays_missing(db):
    lib = _library(db, "剧集库")
    _series(db, lib, "TMDB无日期", tmdb_id="888")
    stats = backfill_series_years(
        db, dry_run=False, details_fetcher=lambda tid, kind: {"id": 888})
    assert stats["still_missing"] == 1 and stats["tmdb_filled"] == 0
