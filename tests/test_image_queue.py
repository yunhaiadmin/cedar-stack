"""封面图片独立队列（图文解耦）：文字刮削落库后，封面后台落盘。

钉死：
- ``process_item_now`` 只写图片字段：远程-only 条目落盘回写
  poster_path/backdrop_path；已落盘/双空/软删/合并/不存在一律 skipped；
  下载失败返回 failed 且不写任何字段；全程不碰 enrich_status。
- ``enqueue_item_images`` 去重（在队/处理中重复入队 False）、队列满
  返回 False 并记 dropped、本地化总开关关时 False。
- 并发键 ``image_queue_concurrency`` 走 SystemConfig：热读、钳
  [1, 16]、非法值回默认 4；且已在 config_schema 登记。
- worker 端到端：入队后条目最终落盘（真实线程 + 替身下载）。
"""
from __future__ import annotations

import os
import time
import uuid

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend import models as base_models  # noqa: E402
from backend.emby_server import image_queue, image_store  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

POSTER_URL = "https://image.tmdb.org/t/p/w500/q.jpg"
BACKDROP_URL = "https://image.tmdb.org/t/p/w1280/q.jpg"


@pytest.fixture()
def db_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path / "images"))
    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    monkeypatch.setattr(image_store, "enqueue_thumb_warm",
                        lambda *a, **k: None)
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _queue_state():
    image_queue._INFLIGHT.clear()
    for key in image_queue._STATS:
        image_queue._STATS[key] = 0
    image_queue._CONC_CACHE = 1  # 测试里只要 1 个 worker，且免读配置
    yield
    image_queue._INFLIGHT.clear()


@pytest.fixture
def downloads(monkeypatch):
    calls: list = []

    def fake_download(url, attempts=1):
        calls.append(url)
        return b"fake-image-bytes"

    monkeypatch.setattr(image_store, "_download", fake_download)
    return calls


def _library(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="电影库",
                     collection_type="movies", kind="local")
    db.add(lib)
    db.flush()
    return lib


def _item(db, lib, name="片", **kw):
    kw.setdefault("enrich_status", "done")
    kw.setdefault("item_type", "movie")
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


# ---------- process_item_now ----------

def test_process_lands_remote_only_images(db, downloads):
    lib = _library(db)
    item = _item(db, lib, primary_image_url=POSTER_URL,
                 backdrop_image_url=BACKDROP_URL)
    db.commit()
    assert image_queue.process_item_now(db, item.id) == "landed"
    db.refresh(item)
    assert item.poster_path == image_store.local_path(POSTER_URL)
    assert item.backdrop_path == image_store.local_path(BACKDROP_URL)
    assert item.enrich_status == "done"  # 刮削状态一字未动
    assert sorted(downloads) == sorted([POSTER_URL, BACKDROP_URL])


def test_process_skips_when_nothing_to_land(db, downloads):
    lib = _library(db)
    landed = _item(db, lib, "已落盘", primary_image_url=POSTER_URL,
                   poster_path="/cache/x.jpg")
    blank = _item(db, lib, "双空")
    merged = _item(db, lib, "合并行", primary_image_url=POSTER_URL,
                   merged_into_id=99)
    db.commit()
    assert image_queue.process_item_now(db, landed.id) == "skipped"
    assert image_queue.process_item_now(db, blank.id) == "skipped"
    assert image_queue.process_item_now(db, merged.id) == "skipped"
    assert image_queue.process_item_now(db, 999999) == "skipped"
    assert downloads == []


def test_process_failure_writes_nothing(db, monkeypatch):
    lib = _library(db)
    item = _item(db, lib, primary_image_url=POSTER_URL,
                 enrich_status="done")
    db.commit()
    monkeypatch.setattr(image_store, "_download",
                        lambda url, attempts=1: None)
    assert image_queue.process_item_now(db, item.id) == "failed"
    db.refresh(item)
    assert not item.poster_path
    assert item.enrich_status == "done"


# ---------- enqueue：去重 / 满队 / 开关 ----------

def test_enqueue_dedupes_inflight(db, monkeypatch):
    monkeypatch.setattr(image_queue, "_ensure_workers", lambda: None)
    assert image_queue.enqueue_item_images(4242) is True
    assert image_queue.enqueue_item_images(4242) is False  # 已在队
    image_queue._QUEUE.get_nowait()
    image_queue._INFLIGHT.discard(4242)


def test_enqueue_full_queue_drops(db, monkeypatch):
    import queue as queue_mod
    monkeypatch.setattr(image_queue, "_ensure_workers", lambda: None)
    monkeypatch.setattr(image_queue, "_QUEUE", queue_mod.Queue(maxsize=1))
    assert image_queue.enqueue_item_images(1) is True
    assert image_queue.enqueue_item_images(2) is False
    assert image_queue._STATS["dropped"] == 1


def test_enqueue_respects_localize_switch(db, monkeypatch):
    monkeypatch.setenv("EMBY_LOCALIZE_IMAGES", "0")
    monkeypatch.setattr(image_queue, "_ensure_workers", lambda: None)
    assert image_queue.enqueue_item_images(7) is False


# ---------- 并发键 ----------

def test_concurrency_reads_system_config(db_factory, monkeypatch):
    db = db_factory()
    db.add(base_models.SystemConfig(key="image_queue_concurrency",
                                    value="7"))
    db.commit()
    db.close()
    monkeypatch.setattr(image_queue, "SessionLocal", db_factory)
    image_queue._CONC_CACHE = None
    assert image_queue._target_concurrency() == 7


def test_concurrency_clamps_and_defaults(db_factory, monkeypatch):
    monkeypatch.setattr(image_queue, "SessionLocal", db_factory)
    db = db_factory()
    db.add(base_models.SystemConfig(key="image_queue_concurrency",
                                    value="99"))
    db.commit()
    db.close()
    image_queue._CONC_CACHE = None
    assert image_queue._target_concurrency() == 16  # 钳上限
    db = db_factory()
    row = db.query(base_models.SystemConfig).filter_by(
        key="image_queue_concurrency").first()
    row.value = "abc"
    db.commit()
    db.close()
    image_queue._CONC_CACHE = None
    assert image_queue._target_concurrency() == 4  # 非法回默认


def test_concurrency_key_registered_in_schema():
    from backend import config_schema, config_service
    assert config_schema.IMAGE_QUEUE_CONFIG_SCHEMA[
        "image_queue_concurrency"] == ("int", 1, 16)
    assert config_service.SCHEMAS["image_queue"] is \
        config_schema.IMAGE_QUEUE_CONFIG_SCHEMA


# ---------- worker 端到端 ----------

def test_worker_lands_enqueued_item(db, db_factory, monkeypatch, downloads):
    lib = _library(db)
    item = _item(db, lib, primary_image_url=POSTER_URL)
    db.commit()
    item_id = item.id
    monkeypatch.setattr(image_queue, "SessionLocal", db_factory)
    assert image_queue.enqueue_item_images(item_id) is True
    deadline = time.time() + 10
    landed = False
    while time.time() < deadline:
        check = db_factory()
        try:
            row = check.query(em.MediaItem).filter_by(id=item_id).first()
            if row and row.poster_path:
                landed = True
                break
        finally:
            check.close()
        time.sleep(0.05)
    assert landed, "worker 应在后台把海报落盘"
    assert image_queue.stats()["landed"] >= 1
