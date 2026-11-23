"""回归测试：批量写库路径必须把封面交给图片队列（2026-11-23 实证修复）。

事故：图文解耦的图片入队（``image_queue.enqueue_item_images``）此前
只接在逐条路径 ``_process_item`` 上，批量路径 ``_process_batch`` 的
两个写库段（串行 Phase 3 与 ``_phase3_post_concurrent`` 3b）都漏接。
批量是生产主路径（enrich_batch=200），于是绝大多数刮完的条目海报
永远不入队：生产实测 6 小时 5.3 万条 done 条目有远程图地址，海报
落盘只有数百张，片库海报全靠用户首开时现拉（0.4–1.6s/张）。

本文件钉住：
- 批量串行段（ENRICH_IO_WORKERS=1）与并发段（=4）写库成功后，
  「有远程图、无本地图」的条目必须各入队一次；
- 已有本地图 / 没有远程图的条目不入队；
- 共用 helper ``_enqueue_item_images_after_commit`` 本身的判定与
  异常吞没（入队炸了不许拖垮刮削）。

脚手架复用 test_enrich_io_concurrency.py 的隔离 SQLite 方案。
"""
import os
import tempfile
import uuid

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)
from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

import pytest

from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import enrich_worker, image_queue


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    _hyg_engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = _hyg_saved_engine
        _dbmod.configure_session_local(_hyg_saved_factory)
        if _hyg_saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _hyg_saved_url
        _hyg_engine.dispose()


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _make_item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "movie"),
        name=kw.pop("name", "测试"),
        file_path=kw.pop("file_path", "/tmp/x.mp4"),
        enrich_status=kw.pop("enrich_status", "pending"),
        **kw,
    )
    db.add(it)
    db.commit()
    return it


def _ok_pre(snapshot, holder=None, inherit_parent=None, folder_candidate=None):
    ctx = {"kind": snapshot.item_type or "movie", "needs_repair": False,
           "tmdb_plan": {"op": "none"}}
    return {"ok": True, "marker": snapshot.name}, ctx


def _ok_post(result, snapshot, tmdb_ctx, batch_out=None):
    out = dict(result)
    out["ok"] = True
    return out


def _patch_batch_seams(monkeypatch, apply_fake):
    monkeypatch.setattr(enrich_worker, "_enrich_fetch_pre", _ok_pre)
    monkeypatch.setattr(enrich_worker, "_enrich_fetch_tmdb_post", _ok_post)
    monkeypatch.setattr(
        enrich_worker, "_inherit_parent_info", lambda d, snapshot: None)
    monkeypatch.setattr(
        enrich_worker, "_folder_title_candidate", lambda d, snapshot: None)
    monkeypatch.setattr(
        enrich_worker, "_maybe_flip_inherit_children",
        lambda d, fetched: None)
    monkeypatch.setattr(
        enrich_worker, "_enrich_season_posters_io",
        lambda d, snapshot, fetched: None)
    monkeypatch.setattr(enrich_worker, "_apply_write_phase", apply_fake)


def _apply_with_image(d, fresh, fetched, queue=None):
    """写库段替身：模拟刮削给条目填上远程海报地址并置 done。"""
    fresh.primary_image_url = "https://image.tmdb.org/t/p/w500/x.jpg"
    fresh.enrich_status = "done"


class TestBatchPathsEnqueueImages:
    @pytest.mark.parametrize("workers", [1, 4])
    def test_batch_write_enqueues_unlocalized_images(self, db, monkeypatch,
                                                     workers):
        enqueued = []
        monkeypatch.setattr(
            image_queue, "enqueue_item_images",
            lambda item_id: enqueued.append(item_id) or True)
        _patch_batch_seams(monkeypatch, _apply_with_image)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", workers)

        m1 = _make_item(db, name="电影一")
        m2 = _make_item(db, name="电影二")
        snaps = [enrich_worker._snapshot_item(m1),
                 enrich_worker._snapshot_item(m2)]
        db.rollback()
        enrich_worker._process_batch(db, snaps)

        assert sorted(enqueued) == sorted([m1.id, m2.id])

    @pytest.mark.parametrize("workers", [1, 4])
    def test_batch_write_skips_items_without_remote_image(self, db,
                                                          monkeypatch,
                                                          workers):
        enqueued = []
        monkeypatch.setattr(
            image_queue, "enqueue_item_images",
            lambda item_id: enqueued.append(item_id) or True)

        def _apply_no_image(d, fresh, fetched, queue=None):
            fresh.enrich_status = "done"

        _patch_batch_seams(monkeypatch, _apply_no_image)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", workers)

        m1 = _make_item(db, name="无图电影")
        snaps = [enrich_worker._snapshot_item(m1)]
        db.rollback()
        enrich_worker._process_batch(db, snaps)

        assert enqueued == []


class TestEnqueueHelper:
    def test_enqueues_when_remote_without_local(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            image_queue, "enqueue_item_images",
            lambda item_id: calls.append(item_id) or True)
        fresh = _make_item_stub(primary_image_url="https://x/y.jpg")
        enrich_worker._enqueue_item_images_after_commit(fresh)
        assert calls == [fresh.id]

    def test_skips_when_poster_already_local(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            image_queue, "enqueue_item_images",
            lambda item_id: calls.append(item_id) or True)
        fresh = _make_item_stub(primary_image_url="https://x/y.jpg",
                                poster_path="/data/images/ab/cd.jpg")
        enrich_worker._enqueue_item_images_after_commit(fresh)
        assert calls == []

    def test_backdrop_only_also_enqueues(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            image_queue, "enqueue_item_images",
            lambda item_id: calls.append(item_id) or True)
        fresh = _make_item_stub(backdrop_image_url="https://x/bg.jpg")
        enrich_worker._enqueue_item_images_after_commit(fresh)
        assert calls == [fresh.id]

    def test_enqueue_exception_is_swallowed(self, monkeypatch):
        def _boom(item_id):
            raise RuntimeError("queue exploded")

        monkeypatch.setattr(image_queue, "enqueue_item_images", _boom)
        fresh = _make_item_stub(primary_image_url="https://x/y.jpg")
        # 不抛即通过：入队失败绝不影响刮削结果
        enrich_worker._enqueue_item_images_after_commit(fresh)


class _Stub:
    def __init__(self, **kw):
        self.id = kw.get("id", 4242)
        self.primary_image_url = kw.get("primary_image_url")
        self.poster_path = kw.get("poster_path")
        self.backdrop_image_url = kw.get("backdrop_image_url")
        self.backdrop_path = kw.get("backdrop_path")


def _make_item_stub(**kw):
    return _Stub(**kw)
