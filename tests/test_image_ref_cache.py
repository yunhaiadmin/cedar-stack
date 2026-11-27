"""C-05：图片引用集缓存快照＋增量合并＋删除快照界。

背景：prune 每轮全量扫 emby_items（41 万行）＋emby_people（18 万行）
构建引用集（生产 6.3 天 715 次全扫）。改后：全量快照按 TTL 重建、
轮次间只增量合并水位后的新行；快照之后落盘的文件本轮绝不删
（引用可能还没进快照），杜绝误删在用图。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend import models as _core_models  # noqa: E402,F401
from backend.emby_server import image_store  # noqa: E402
from backend.emby_server import models as em  # noqa: E402


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path))
    monkeypatch.setenv("EMBY_LOCALIZE_IMAGES", "1")
    monkeypatch.setenv("EMBY_IMAGE_GRACE_SECONDS", "0")  # 关保护期，专测快照界
    engine = create_engine("sqlite://")
    em.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s, tmp_path
    finally:
        s.close()
        engine.dispose()


def _add_item(s, guid, poster_path=None):
    lib = s.query(em.Library).first()
    if lib is None:
        lib = em.Library(guid="lib", name="库", collection_type="movies", paths="/media")
        s.add(lib)
        s.flush()
    it = em.MediaItem(guid=guid, name=guid, item_type="movie", library_id=lib.id,
                      poster_path=poster_path)
    s.add(it)
    s.flush()
    return it


def test_snapshot_bound_protects_post_snapshot_files(db_session):
    """快照之后落盘的孤儿文件本轮不删；快照之前的照常回收。"""
    s, tmp_path = db_session
    ref = tmp_path / "ref1.jpg"
    ref.write_bytes(b"x")
    _add_item(s, "m1", poster_path=str(ref))
    orphan_old = tmp_path / "orphanold.jpg"
    orphan_old.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(orphan_old, (past, past))
    s.commit()

    image_store.prune(s)  # 第一轮：建快照，回收快照前的孤儿
    assert ref.exists()
    assert not orphan_old.exists()

    orphan_new = tmp_path / "orphannew.jpg"
    orphan_new.write_bytes(b"x")
    future = time.time() + 3600  # 落盘时刻在快照之后
    os.utime(orphan_new, (future, future))
    image_store.prune(s)  # 第二轮：快照未重建，新文件受快照界保护
    assert orphan_new.exists(), "快照后落盘的文件不许删（引用可能还没进快照）"
    assert ref.exists()


def test_incremental_merge_without_rebuild(db_session):
    """水位后的新行引用靠增量合并入集，不触发全量重建（built_at 不变）。"""
    s, tmp_path = db_session
    p1 = tmp_path / "first.jpg"
    p1.write_bytes(b"x")
    _add_item(s, "m1", poster_path=str(p1))
    s.commit()
    names1, built1 = image_store.referenced_basenames_snapshot(s)
    assert "first.jpg" in names1

    p2 = tmp_path / "second.jpg"
    p2.write_bytes(b"x")
    _add_item(s, "m2", poster_path=str(p2))
    s.commit()
    names2, built2 = image_store.referenced_basenames_snapshot(s)
    assert "second.jpg" in names2, "新行引用应靠增量合并入集"
    assert built2 == built1, "增量合并不得重建快照"


def test_ttl_zero_rebuilds_and_drops_stale_refs(db_session, monkeypatch):
    """TTL=0 时每轮重建快照：已删行的引用退出集合（垃圾最终被回收）。"""
    s, tmp_path = db_session
    p1 = tmp_path / "gone.jpg"
    p1.write_bytes(b"x")
    it = _add_item(s, "m1", poster_path=str(p1))
    s.commit()
    names1, _ = image_store.referenced_basenames_snapshot(s)
    assert "gone.jpg" in names1

    s.delete(it)
    s.commit()
    monkeypatch.setenv("EMBY_IMAGE_REF_CACHE_TTL_SECONDS", "0")
    names2, _ = image_store.referenced_basenames_snapshot(s)
    assert "gone.jpg" not in names2
