"""仪表盘观影榜 item_ranking 口径（2026-11-28 修复）。

旧实现按 MediaItem.name 分组：全库所有剧的「第 1 集」被并成一行
（生产实测 45 次＝22 部不同剧的第一集之和），榜单行毫无意义。
本文件钉住新口径：
- 按条目本身（MediaItem.id）聚合，同名不同条目各占一行；
- episode 行新增 series_name（所属剧集条目的 name），其余行为空字符串；
- 既有字段 name/type/plays 形状不变。

隔离 SQLite 内存库，夹具口径同 test_pa037_join.py。
"""

from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import soft_delete  # noqa: F401 — 装全局软删钩子

TICKS = 10_000_000


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _user(db, name="rankadmin"):
    u = base_models.WebUser(username=name, password_hash="x", is_active=True,
                            is_staff=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _lib(db):
    lib = em.Library(guid="ranklib", name="ranklib", is_enabled=True)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return lib


def _item(db, lib, guid, name, item_type, series_id=None):
    it = em.MediaItem(guid=guid, name=name, item_type=item_type,
                      library_id=lib.id, series_id=series_id,
                      duration_ticks=3600 * TICKS)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _play(db, user_id, item_id, key):
    s = em.PlaybackSession(session_key=key, user_id=user_id, item_id=item_id,
                           start_time=datetime.now(),
                           last_update_at=datetime.now(),
                           position_ticks=120 * TICKS)
    db.add(s)
    db.commit()


def test_item_ranking_groups_by_item_not_name(db):
    from backend.api import admin as admin_api

    admin = _user(db)
    lib = _lib(db)
    series_a = _item(db, lib, "rank-series-a", "黑镜", "series")
    series_b = _item(db, lib, "rank-series-b", "权力的游戏", "series")
    ep_a = _item(db, lib, "rank-ep-a", "第 1 集", "episode", series_id=series_a.id)
    ep_b = _item(db, lib, "rank-ep-b", "第 1 集", "episode", series_id=series_b.id)
    movie = _item(db, lib, "rank-movie", "星愿", "movie")

    for i in range(3):
        _play(db, admin.id, ep_a.id, f"rank-a-{i}")
    for i in range(2):
        _play(db, admin.id, ep_b.id, f"rank-b-{i}")
    for i in range(4):
        _play(db, admin.id, movie.id, f"rank-m-{i}")

    stats = admin_api.get_playback_stats(current_admin=admin, db=db)
    ranking = stats["item_ranking"]

    # 同名「第 1 集」必须出两行，不许并成一行
    eps = [r for r in ranking if r["name"] == "第 1 集"]
    assert len(eps) == 2, ranking
    by_series = {r["series_name"]: r for r in eps}
    assert by_series["黑镜"]["plays"] == 3
    assert by_series["权力的游戏"]["plays"] == 2
    assert all(r["type"] == "episode" for r in eps)

    # 电影行 series_name 为空字符串，既有字段形状不变
    movies = [r for r in ranking if r["type"] == "movie"]
    assert len(movies) == 1, ranking
    assert movies[0]["name"] == "星愿"
    assert movies[0]["plays"] == 4
    assert movies[0]["series_name"] == ""
