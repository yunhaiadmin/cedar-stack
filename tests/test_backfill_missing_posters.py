"""无海报作品回填例程（scripts/backfill_missing_posters.py）——审计 PD-02。

钉死：
- 目标只限 live（未合并、未软删）movie/series 且 poster_path 与
  primary_image_url 双空；任一已有图的行不碰；
- donor 优先、同 tmdb_id 同类型隔离（电影图不喂剧集）；
- TMDB 级走 image_specs 的 Primary（w500）口径落图；
- 无 tmdb_id 计 no_source，不按片名猜身份；
- dry-run 只计数不写库。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from scripts.backfill_missing_posters import (
    backfill_missing_posters,
    donor_posters,
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


def _library(db, name, collection_type="movies"):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     collection_type=collection_type, kind="local")
    db.add(lib)
    db.flush()
    return lib


def _item(db, lib, name, **kw):
    kw.setdefault("enrich_status", "done")
    kw.setdefault("item_type", "movie")
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


def _no_details(tmdb_id, kind):  # pragma: no cover - 不应被调用的桩
    raise AssertionError("donor 可覆盖时不许调 details")


def _capture_setter(item, kind, url):
    assert kind == "Primary"
    item.primary_image_url = url


# ---------- donor 映射 ----------

def test_donor_map_kind_isolation_and_exclusions(db):
    lib = _library(db, "电影库")
    tv = _library(db, "剧集库", collection_type="tvshows")
    _item(db, lib, "电影版", tmdb_id="603",
          primary_image_url="https://img/p.jpg")
    _item(db, tv, "剧集版", item_type="series", tmdb_id="603",
          primary_image_url="https://img/tv.jpg")
    _item(db, lib, "合并行", tmdb_id="604", merged_into_id=99,
          primary_image_url="https://img/m.jpg")
    donors = donor_posters(db)
    assert donors[("603", "movie")][0] == "https://img/p.jpg"
    assert donors[("603", "series")][0] == "https://img/tv.jpg"
    assert ("604", "movie") not in donors


# ---------- 回填主流程 ----------

def test_donor_fill_dry_run_then_commit(db):
    lib = _library(db, "电影库")
    _item(db, lib, "有图版", tmdb_id="603",
          primary_image_url="https://img/p.jpg")
    target = _item(db, lib, "无图版", tmdb_id="603")
    stats = backfill_missing_posters(db, dry_run=True,
                                     details_fetcher=_no_details,
                                     image_setter=_capture_setter)
    assert stats["total"] == 1 and stats["donor_filled"] == 1
    db.refresh(target)
    assert not (target.primary_image_url or "")
    stats = backfill_missing_posters(db, dry_run=False,
                                     details_fetcher=_no_details,
                                     image_setter=_capture_setter)
    assert stats["donor_filled"] == 1
    db.refresh(target)
    assert target.primary_image_url == "https://img/p.jpg"


def test_tmdb_fill_uses_w500_primary(db):
    lib = _library(db, "电影库")
    target = _item(db, lib, "无图有ID", tmdb_id="999")
    seen = {}

    def fake_details(tmdb_id, kind):
        seen["args"] = (tmdb_id, kind)
        return {"poster_path": "/abc.jpg", "backdrop_path": "/bg.jpg"}

    stats = backfill_missing_posters(db, dry_run=False,
                                     details_fetcher=fake_details,
                                     image_setter=_capture_setter)
    assert seen["args"] == ("999", "movie")
    assert stats["tmdb_filled"] == 1
    db.refresh(target)
    assert target.primary_image_url.endswith("/w500/abc.jpg")
    assert not (target.backdrop_image_url or "")  # 只补海报，不顺手改背景


def test_no_tmdb_is_no_source_and_tmdb_no_poster_stays_missing(db):
    lib = _library(db, "电影库")
    _item(db, lib, "无ID")
    _item(db, lib, "有ID无海报", tmdb_id="555")
    stats = backfill_missing_posters(
        db, dry_run=False,
        details_fetcher=lambda tid, kind: {"id": 555},
        image_setter=_capture_setter)
    assert stats["total"] == 2
    assert stats["no_source"] == 1
    assert stats["still_missing"] == 2  # 两部都没补上


def test_rows_with_any_poster_are_not_targets(db):
    lib = _library(db, "电影库")
    _item(db, lib, "有远程图", tmdb_id="1",
          primary_image_url="https://img/x.jpg")
    _item(db, lib, "有本地图", tmdb_id="2", poster_path="/tmp/x.jpg")
    _item(db, lib, "合并行", tmdb_id="3", merged_into_id=99)
    stats = backfill_missing_posters(db, dry_run=False,
                                     details_fetcher=_no_details,
                                     image_setter=_capture_setter)
    assert stats["total"] == 0
