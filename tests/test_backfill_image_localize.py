"""只存远程地址的存量图片回填例程（scripts/backfill_image_localize.py）。

钉死：
- 目标只限 live（未软删、未合并）且「有远程地址、无本地文件」的行；
  已落盘 / 双空 / 软删 / 合并行都不碰；
- dry-run 只计数：不调 prewarm、不写库；
- commit 走 prewarm + localize(allow_download=False) 回读落字段，
  海报与背景分别判定，一张落下也计 filled；
- 幂等：落完再跑 total=0。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from scripts.backfill_image_localize import backfill_image_localize

POSTER_URL = "https://image.tmdb.org/t/p/w500/a.jpg"
BACKDROP_URL = "https://image.tmdb.org/t/p/w1280/a.jpg"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _library(db, name="电影库"):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     collection_type="movies", kind="local")
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


class _FakeStore:
    """prewarm/localize 替身：prewarm 记下 URL，localize 按 URL 造本地路径。"""

    def __init__(self, fail_urls=()):
        self.prewarmed: list = []
        self.fail = set(fail_urls)

    def prewarm(self, urls):
        self.prewarmed.extend(urls)
        return len(urls)

    def localize(self, url, allow_download=True):
        assert allow_download is False  # 回读档：不许在写库阶段真下载
        if url in self.fail:
            return ""
        return f"/cache/images/{url.rsplit('/', 1)[-1]}"


def test_targets_only_remote_only_live_rows(db):
    lib = _library(db)
    _item(db, lib, "远程only", primary_image_url=POSTER_URL,
          backdrop_image_url=BACKDROP_URL)
    _item(db, lib, "已落盘", primary_image_url=POSTER_URL,
          poster_path="/cache/x.jpg")
    _item(db, lib, "双空")
    _item(db, lib, "合并行", primary_image_url=POSTER_URL, merged_into_id=99)
    gone = _item(db, lib, "软删行", primary_image_url=POSTER_URL)
    from datetime import datetime
    gone.deleted_at = datetime.now()
    db.commit()
    stats = backfill_image_localize(db, dry_run=True)
    assert stats == {"total": 1, "filled": 0, "still_remote": 0}


def test_dry_run_downloads_nothing(db):
    lib = _library(db)
    _item(db, lib, "远程only", primary_image_url=POSTER_URL)
    db.commit()
    store = _FakeStore()
    stats = backfill_image_localize(db, dry_run=True,
                                    prewarm=store.prewarm,
                                    localize=store.localize)
    assert stats["total"] == 1
    assert store.prewarmed == []


def test_commit_fills_and_is_idempotent(db):
    lib = _library(db)
    item = _item(db, lib, "远程only", primary_image_url=POSTER_URL,
                 backdrop_image_url=BACKDROP_URL)
    db.commit()
    store = _FakeStore()
    stats = backfill_image_localize(db, dry_run=False,
                                    prewarm=store.prewarm,
                                    localize=store.localize)
    assert stats == {"total": 1, "filled": 1, "still_remote": 0}
    assert sorted(store.prewarmed) == sorted([POSTER_URL, BACKDROP_URL])
    db.refresh(item)
    assert item.poster_path == "/cache/images/a.jpg"
    assert item.backdrop_path == "/cache/images/a.jpg"
    # 幂等：再跑候选为 0
    stats2 = backfill_image_localize(db, dry_run=False,
                                     prewarm=store.prewarm,
                                     localize=store.localize)
    assert stats2["total"] == 0


def test_partial_failure_counts_still_remote(db):
    lib = _library(db)
    _item(db, lib, "下不到", primary_image_url=POSTER_URL)
    ok = _item(db, lib, "只落背景", backdrop_image_url=BACKDROP_URL)
    db.commit()
    store = _FakeStore(fail_urls={POSTER_URL})
    stats = backfill_image_localize(db, dry_run=False,
                                    prewarm=store.prewarm,
                                    localize=store.localize)
    assert stats == {"total": 2, "filled": 1, "still_remote": 1}
    db.refresh(ok)
    assert ok.backdrop_path == "/cache/images/a.jpg"
