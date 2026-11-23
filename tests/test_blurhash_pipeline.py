"""BlurHash 占位全链（封面三项之三）：编码 → 存库 → 巡检补算 → 下发。

钉死：
- 编码器：4×3 分量哈希长 28；固色图的 DC（平均色）分量能解回原色
  （±6）；同图确定性、异图不同；坏文件/缺文件 → None 不抛。
- 模型列 + _auto_migrate 补列条目（老库 DROP 两列后跑迁移补齐）。
- blurhash_patrol：有本地图缺哈希的条目补算回写；已有哈希不重算；
  文件缺失跳过不写；软删/合并行不碰。
- image_backlog_patrol 落盘即算：补下载成功后同轮写上哈希。
- EA _image_blur_hashes：自身哈希直下发；集自身无图无哈希时回退
  剧的哈希；自身有图无哈希时不拿父行凑数；全无 → {}。
- 门户 _serialize_cursor_item 下发 poster_blur_hash。
"""
from __future__ import annotations

import io
import os
import uuid

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend import models as base_models  # noqa: E402
from backend.emby_server import blurhash_util, enrich_worker, image_store  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

POSTER_URL = "https://image.tmdb.org/t/p/w500/q.jpg"


def _png_bytes(color=(200, 30, 40), size=(60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------- 编码器 ----------

def test_encode_solid_color_dc_roundtrip():
    img = Image.new("RGB", (100, 150), (200, 30, 40))
    h = blurhash_util.encode_image(img)
    assert len(h) == 28  # 4×3 分量：1+1+4+2×11
    avg = blurhash_util.average_color(h)
    assert avg is not None
    for got, want in zip(avg, (200, 30, 40)):
        assert abs(got - want) <= 6


def test_encode_deterministic_and_color_sensitive():
    a = Image.new("RGB", (50, 75), (10, 120, 200))
    b = Image.new("RGB", (50, 75), (240, 220, 10))
    assert blurhash_util.encode_image(a) == blurhash_util.encode_image(a)
    assert blurhash_util.encode_image(a) != blurhash_util.encode_image(b)


def test_encode_gradient_average_is_midrange():
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = (x * 4, y * 4, 128)
    avg = blurhash_util.average_color(blurhash_util.encode_image(img))
    assert avg is not None
    # DC 是线性空间平均再转回 sRGB：渐变 0..252 的线性均值回转后
    # 约 150（凸函数均值大于均值函数值），不是 sRGB 算术平均 126。
    assert abs(avg[0] - 150) <= 8 and abs(avg[1] - 150) <= 8
    assert abs(avg[2] - 128) <= 8


def test_encode_from_file_guards(tmp_path):
    assert blurhash_util.encode_from_file(str(tmp_path / "nope.jpg")) is None
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert blurhash_util.encode_from_file(str(bad)) is None
    good = tmp_path / "good.png"
    good.write_bytes(_png_bytes())
    h = blurhash_util.encode_from_file(str(good))
    assert h and len(h) == 28


def test_average_color_rejects_garbage():
    assert blurhash_util.average_color("") is None
    assert blurhash_util.average_color("xx") is None
    assert blurhash_util.average_color("!!!!!!") is None


# ---------- 模型列 + 迁移 ----------

def test_model_has_blur_columns():
    cols = em.MediaItem.__table__.columns
    assert "poster_blur_hash" in cols
    assert "backdrop_blur_hash" in cols


def test_auto_migrate_adds_blur_columns(tmp_path):
    import backend.database as dbmod
    import backend.models  # noqa: F401 — 注册其余表进同一 Base.metadata

    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    dbmod.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for col in ("poster_blur_hash", "backdrop_blur_hash"):
            conn.exec_driver_sql(f"ALTER TABLE emby_items DROP COLUMN {col}")
    old_engine = dbmod.engine
    dbmod.engine = engine
    try:
        dbmod._auto_migrate()
    finally:
        dbmod.engine = old_engine
    names = {c["name"] for c in inspect(engine).get_columns("emby_items")}
    assert "poster_blur_hash" in names
    assert "backdrop_blur_hash" in names


# ---------- 巡检补算 ----------

@pytest.fixture()
def db_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("EMBY_LOCALIZE_IMAGES", "1")  # 2026-11-23 起默认关，本文件测开启态行为
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


def _library(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="电影库",
                     collection_type="movies", kind="local")
    db.add(lib)
    db.flush()
    return lib


def _item(db, lib, name="片", **kw):
    kw.setdefault("item_type", "movie")
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        name=name, sort_name=name, **kw)
    db.add(item)
    db.flush()
    return item


def test_patrol_fills_missing_hash(db, tmp_path):
    lib = _library(db)
    poster_file = tmp_path / "poster.png"
    poster_file.write_bytes(_png_bytes())
    item = _item(db, lib, poster_path=str(poster_file))
    preset = _item(db, lib, "已有哈希", poster_path=str(poster_file),
                   poster_blur_hash="PRESET")
    ghost = _item(db, lib, "文件丢了", poster_path=str(tmp_path / "gone.png"))
    merged = _item(db, lib, "合并行", poster_path=str(poster_file),
                   merged_into_id=99)
    db.commit()
    assert enrich_worker.blurhash_patrol(db) == 1
    db.refresh(item)
    db.refresh(preset)
    db.refresh(ghost)
    db.refresh(merged)
    assert item.poster_blur_hash and len(item.poster_blur_hash) == 28
    assert preset.poster_blur_hash == "PRESET"  # 不重算
    assert ghost.poster_blur_hash is None  # 文件缺失跳过不写
    assert merged.poster_blur_hash is None
    # 再跑一轮：候选只剩文件缺失行，补算 0
    assert enrich_worker.blurhash_patrol(db) == 0


def test_backlog_patrol_computes_hash_on_land(db, monkeypatch):
    lib = _library(db)
    item = _item(db, lib, primary_image_url=POSTER_URL)
    db.add(em.UserMediaData(user_id=1, item_id=item.id,
                            played=True, play_count=1))
    db.commit()
    png = _png_bytes()
    monkeypatch.setattr(image_store, "_download",
                        lambda url, attempts=1: png)
    monkeypatch.delenv("EMBY_IMAGE_BACKFILL", raising=False)
    enrich_worker._IMAGE_BACKFILL_TRIED.clear()
    assert enrich_worker.image_backlog_patrol(db) == 1
    db.refresh(item)
    assert item.poster_path
    assert item.poster_blur_hash and len(item.poster_blur_hash) == 28


# ---------- EA 下发 ----------

def test_ea_blur_hashes_own_and_fallback(db):
    from backend.emby_server import api as ea_api

    lib = _library(db)
    series = _item(db, lib, "剧", item_type="series",
                   poster_blur_hash="SERIESHASH")
    episode = _item(db, lib, "集", item_type="episode",
                    series_id=series.id, parent_id=series.id)
    own = _item(db, lib, "有图集", item_type="episode",
                series_id=series.id, parent_id=series.id,
                primary_image_url=POSTER_URL)  # 有图无哈希：不许拿剧的凑数
    movie = _item(db, lib, "电影", poster_blur_hash="MOVIEHASH",
                  backdrop_blur_hash="BGHASH")
    plain = _item(db, lib, "素片")
    db.commit()
    assert ea_api._image_blur_hashes(movie, db) == {
        "Primary": {"1": "MOVIEHASH"}, "Backdrop": {"1": "BGHASH"}}
    assert ea_api._image_blur_hashes(episode, db) == {
        "Primary": {"1": "SERIESHASH"}}
    assert ea_api._image_blur_hashes(own, db) == {}
    assert ea_api._image_blur_hashes(plain, db) == {}


# ---------- 门户下发 ----------

def test_portal_serializer_carries_blur_hash(db):
    from backend.api import media_library as ml

    lib = _library(db)
    item = _item(db, lib, primary_image_url=POSTER_URL,
                 poster_blur_hash="PORTALHASH")
    db.commit()
    out = ml._serialize_cursor_item(item, db)
    assert out["poster_blur_hash"] == "PORTALHASH"
    assert out["poster_url"]
