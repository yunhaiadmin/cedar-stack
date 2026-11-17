"""零集剧集分类报告（scripts/report_zero_episode_series.py）——审计 PD-02。

只读分类，钉死四类口径：
- episodes_merged_elsewhere：名下集全被合并到他剧；
- episodes_soft_deleted：名下集全软删；
- split_shell：名下无集、同库同名另有带集行；
- true_empty：以上皆非。
有 live 集的正常剧不进清单；脚本不写库。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from scripts.report_zero_episode_series import (
    CAT_EMPTY,
    CAT_MERGED,
    CAT_SOFT,
    CAT_SPLIT,
    classify_zero_episode_series,
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


def _item(db, lib, name, **kw):
    kw.setdefault("enrich_status", "done")
    kw.setdefault("item_type", "series")
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


def test_classify_four_categories(db):
    from datetime import datetime

    lib = _library(db, "剧集库")
    normal = _item(db, lib, "正常剧")
    _item(db, lib, "正常集", item_type="episode", series_id=normal.id)

    merged_shell = _item(db, lib, "集被合并")
    other = _item(db, lib, "承接剧")
    main_ep = _item(db, lib, "承接集", item_type="episode",
                    series_id=other.id)
    _item(db, lib, "被合并集", item_type="episode",
          series_id=merged_shell.id, merged_into_id=main_ep.id)

    soft_shell = _item(db, lib, "集全软删")
    _item(db, lib, "软删集", item_type="episode", series_id=soft_shell.id,
          deleted_at=datetime(2026, 1, 1))

    split_shell = _item(db, lib, "拆分剧")
    sibling = _item(db, lib, "拆分剧")
    _item(db, lib, "拆分集", item_type="episode", series_id=sibling.id)

    _item(db, lib, "真空壳")

    rows = classify_zero_episode_series(db)
    by_id = {r["id"]: r["category"] for r in rows}
    assert by_id[merged_shell.id] == CAT_MERGED
    assert by_id[soft_shell.id] == CAT_SOFT
    assert by_id[split_shell.id] == CAT_SPLIT
    assert normal.id not in by_id
    assert sibling.id not in by_id
    empties = [r for r in rows if r["category"] == CAT_EMPTY]
    assert len(empties) == 1 and empties[0]["name"] == "真空壳"
    assert len(rows) == 4


def test_merged_series_rows_themselves_are_not_listed(db):
    lib = _library(db, "剧集库")
    _item(db, lib, "已合并剧", merged_into_id=99)
    assert classify_zero_episode_series(db) == []
