"""仪表盘「正在看」now_playing 口径（2026-11-28 用户亲点新增）。

get_playback_stats 新增 now_playing：ended_at 为空即在播，与 overview 的
active_sessions 同口径。本文件钉住：
- 只含在播会话（已结束的 ended_at 非空不进列表）；
- 按 last_update_at 倒序；
- episode 行带 series_name（所属剧集条目的 name），电影行为空字符串；
- 每条字段齐全：username / name / series_name / client_name /
  device_name / is_paused / position_ticks / duration_ticks / start_time。

隔离 SQLite 内存库，夹具口径同 test_admin_playback_ranking.py。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

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
NOW = datetime(2026, 11, 28, 12, 0, 0)


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


def _user(db, name):
    u = base_models.WebUser(username=name, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _lib(db):
    lib = em.Library(guid="nplib", name="nplib", is_enabled=True)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return lib


def _item(db, lib, guid, name, item_type, series_id=None, duration_ticks=3600 * TICKS):
    it = em.MediaItem(guid=guid, name=name, item_type=item_type,
                      library_id=lib.id, series_id=series_id,
                      duration_ticks=duration_ticks)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _session(db, user_id, item_id, key, *, ended_at=None, minutes_ago=0,
             client_name="", device_name="", is_paused=False,
             position_ticks=0):
    ts = NOW - timedelta(minutes=minutes_ago)
    s = em.PlaybackSession(
        session_key=key, user_id=user_id, item_id=item_id,
        client_name=client_name, device_name=device_name,
        is_paused=is_paused, position_ticks=position_ticks,
        start_time=ts - timedelta(minutes=10), last_update_at=ts,
        ended_at=ended_at,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_now_playing_lists_only_active_sessions(db):
    from backend.api import admin as admin_api

    admin = _user(db, "npadmin")
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    lib = _lib(db)
    series = _item(db, lib, "np-series", "黑镜", "series")
    ep = _item(db, lib, "np-ep", "第 1 集", "episode", series_id=series.id,
               duration_ticks=3600 * TICKS)
    movie = _item(db, lib, "np-movie", "星愿", "movie",
                  duration_ticks=5400 * TICKS)

    # 在播：alice 看黑镜第 1 集（最近上报）；bob 看星愿（较早、已暂停）
    _session(db, alice.id, ep.id, "np-live-ep", minutes_ago=1,
             client_name="Rex", device_name="iPhone 15",
             position_ticks=1800 * TICKS)
    _session(db, bob.id, movie.id, "np-live-movie", minutes_ago=5,
             client_name="SenPlayer", device_name="Apple TV",
             is_paused=True, position_ticks=900 * TICKS)
    # 已结束：不许进列表
    _session(db, alice.id, movie.id, "np-ended", minutes_ago=2,
             ended_at=NOW - timedelta(minutes=1))

    stats = admin_api.get_playback_stats(current_admin=admin, db=db)
    rows = stats["now_playing"]

    assert len(rows) == 2, rows

    # 按 last_update_at 倒序：最近上报的 episode 行在前
    first, second = rows
    assert first["username"] == "alice"
    assert first["name"] == "第 1 集"
    assert first["series_name"] == "黑镜"
    assert first["client_name"] == "Rex"
    assert first["device_name"] == "iPhone 15"
    assert first["is_paused"] is False
    assert first["position_ticks"] == 1800 * TICKS
    assert first["duration_ticks"] == 3600 * TICKS
    assert isinstance(first["start_time"], str) and first["start_time"]

    # 电影行 series_name 为空字符串，其余字段齐全
    assert second["username"] == "bob"
    assert second["name"] == "星愿"
    assert second["series_name"] == ""
    assert second["client_name"] == "SenPlayer"
    assert second["device_name"] == "Apple TV"
    assert second["is_paused"] is True
    assert second["position_ticks"] == 900 * TICKS
    assert second["duration_ticks"] == 5400 * TICKS
    assert isinstance(second["start_time"], str) and second["start_time"]


def test_now_playing_empty_when_nobody_watching(db):
    from backend.api import admin as admin_api

    admin = _user(db, "npadmin2")
    stats = admin_api.get_playback_stats(current_admin=admin, db=db)
    assert stats["now_playing"] == []
