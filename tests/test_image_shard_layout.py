"""图片落盘分片布局（借鉴 Jellyfin 按哈希分片）+ 平铺存量兼容。

钉死：
- ``local_path`` 是 ``<image_dir>/<digest 前 2 位>/<digest><ext>``；
- ``localize`` 新下载落进分片目录；分片前的平铺存量文件照样命中
  （``existing_local_path`` 两处都认），不重复下载、不搬迁；
- ``localize(allow_download=False)``（写事务档）对分片与平铺都只认
  已有文件、零下载；
- ``prune`` 覆盖分片子目录：被引用的分片原图不删、未引用的删；
  非分片子目录（library-covers 等）绝不碰；
- ``stats`` 计数含分片文件。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend import models as _core_models  # noqa: E402,F401
from backend.emby_server import image_store  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

POSTER_URL = "https://image.tmdb.org/t/p/w500/shardme.jpg"
BACKDROP_URL = "https://image.tmdb.org/t/p/w1280/shardbg.jpg"


@pytest.fixture(autouse=True)
def _image_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path / "images"))
    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    monkeypatch.setattr(image_store, "enqueue_thumb_warm", lambda *a, **k: None)
    yield


@pytest.fixture
def downloads(monkeypatch):
    calls: list = []

    def fake_download(url, attempts=1):
        calls.append(url)
        return b"fake-image-bytes"

    monkeypatch.setattr(image_store, "_download", fake_download)
    return calls


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path))
    engine = create_engine("sqlite://")
    em.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s, tmp_path
    finally:
        s.close()
        engine.dispose()


def _write(path: str, data: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ---------- 路径形状 ----------

def test_local_path_is_sharded_by_digest_prefix():
    path = image_store.local_path(POSTER_URL)
    digest, ext = image_store._url_digest_ext(POSTER_URL)
    assert os.path.basename(path) == digest + ext
    assert os.path.basename(os.path.dirname(path)) == digest[:2]
    assert os.path.dirname(os.path.dirname(path)) == image_store.image_dir()


def test_legacy_path_is_flat():
    legacy = image_store.legacy_local_path(POSTER_URL)
    assert os.path.dirname(legacy) == image_store.image_dir()


# ---------- localize：分片写入 + 平铺存量命中 ----------

def test_localize_downloads_into_shard(downloads):
    path = image_store.localize(POSTER_URL)
    assert path == image_store.local_path(POSTER_URL)
    assert os.path.isfile(path)
    assert downloads == [POSTER_URL]
    # 第二次：命中已有文件，零下载
    assert image_store.localize(POSTER_URL) == path
    assert downloads == [POSTER_URL]


def test_localize_honors_legacy_flat_file(downloads):
    legacy = _write(image_store.legacy_local_path(POSTER_URL), b"old-bytes")
    assert image_store.localize(POSTER_URL) == legacy
    assert image_store.localize(POSTER_URL, allow_download=False) == legacy
    assert downloads == []  # 存量已在盘上，绝不重下


def test_existing_local_path_prefers_shard(downloads):
    legacy = _write(image_store.legacy_local_path(POSTER_URL), b"old")
    shard = _write(image_store.local_path(POSTER_URL), b"new")
    assert legacy != shard
    assert image_store.existing_local_path(POSTER_URL) == shard
    assert image_store.existing_local_path(BACKDROP_URL) == ""


# ---------- prune / stats 覆盖分片 ----------

def _lib(s):
    lib = s.query(em.Library).first()
    if lib is None:
        lib = em.Library(guid="lib", name="库", collection_type="movies",
                         paths="/media")
        s.add(lib)
        s.flush()
    return lib


def _add_item(s, guid, poster_path=None):
    it = em.MediaItem(guid=guid, name=guid, item_type="movie",
                      library_id=_lib(s).id, poster_path=poster_path)
    s.add(it)
    s.flush()
    return it


def test_prune_covers_shards_and_spares_other_subdirs(db_session, monkeypatch):
    s, tmp_path = db_session
    monkeypatch.setenv("EMBY_IMAGE_GRACE_SECONDS", "0")
    keep = _write(image_store.local_path(POSTER_URL), b"keep")
    drop = _write(image_store.local_path(BACKDROP_URL), b"drop")
    _add_item(s, "m1", poster_path=keep)
    s.commit()
    # 非分片子目录（库封面等别家的文件）：prune 绝不碰
    other = tmp_path / "library-covers" / "lib1.webp"
    _write(str(other), b"cover")
    result = image_store.prune(s)
    assert os.path.isfile(keep), "被引用的分片原图不许删"
    assert not os.path.isfile(drop), "未引用的分片原图应被清理"
    assert os.path.isfile(str(other)), "非分片子目录不归 prune 管"
    assert result["removed"] >= 1


def test_stats_counts_sharded_files():
    _write(image_store.local_path(POSTER_URL), b"1234")
    _write(image_store.legacy_local_path(BACKDROP_URL), b"12")
    st = image_store.stats()
    assert st["files"] == 2
    assert st["bytes"] == 6
