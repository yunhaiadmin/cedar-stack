# -*- coding: utf-8 -*-
"""管理端追新日历（二期缺口项）：GET /api/admin/chase-calendar
与 GET /api/admin/chase-calendar/items/{item_id}。

钉住的口径（与用户端 GET /api/user/emby/calendar 同一来源）：
1. 按 ``date_added`` 分组、分格用 Asia/Shanghai 自然日
   （date_added 是 UTC-naive：+8h 才是上海日）。
2. ``types`` 角标不受 item_type 筛选影响；season 不上日历；
   is_hidden 的条目不上日历。
3. 追剧人数 = emby_series_follows 里 status=active 的计数，
   单集行带的是所属剧的人数；电影 followers=null。
4. 详情：来源字段 + 所属剧追剧块（active/总数/最近订阅者/库内集数），
   MP 订阅无本地关联 → mp_subscription=None 明示；不存在的条目 404。
5. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-chase-calendar-key-32chars")

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_chase_calendar as cal_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import backend.emby_server.models  # noqa: F401 — 注册 EA 侧表到同一 Base
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _user(db, staff=False):
    u = models.WebUser(
        username="u_" + uuid.uuid4().hex[:10],
        password_hash="test",
        email=None,
        is_active=True,
        is_staff=staff,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _library(db, name="剧集库"):
    lib = em.Library(guid="guid-" + uuid.uuid4().hex[:12], name=name)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return lib


def _item(db, lib, name, item_type="episode", added=None, **kw):
    row = em.MediaItem(
        guid=uuid.uuid4().hex[:32],
        library_id=lib.id,
        item_type=item_type,
        name=name,
        date_added=added,
        **kw,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _follow(db, user, series, status="active"):
    row = em.SeriesFollow(user_id=user.id, series_item_id=series.id,
                          status=status)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def admin(db):
    return _user(db, staff=True)


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(cal_admin.admin_chase_calendar_router)
    app.dependency_overrides[get_db] = lambda: db
    if admin_error is not None:
        def _raise():
            raise HTTPException(
                status_code=admin_error,
                detail="forbidden" if admin_error == 403 else "no creds")
        app.dependency_overrides[get_current_admin] = _raise
    elif admin_user is not None:
        app.dependency_overrides[get_current_admin] = lambda: admin_user
    return TestClient(app, raise_server_exceptions=False)


def test_grouping_uses_shanghai_day(db, admin):
    lib = _library(db)
    # 2026-11-18 17:30 UTC = 11-19 01:30 CST → 落 11-19 格
    _item(db, lib, "甲", added=datetime(2026, 11, 18, 17, 30))
    # 2026-11-19 15:59 UTC = 11-19 23:59 CST → 落 11-19 格
    _item(db, lib, "乙", added=datetime(2026, 11, 19, 15, 59))
    # 2026-11-19 16:00 UTC = 11-20 00:00 CST → 不在 11-19 区间内
    _item(db, lib, "丙", added=datetime(2026, 11, 19, 16, 0))

    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/chase-calendar",
                     params={"start": "2026-11-19", "end": "2026-11-19"})
    assert res.status_code == 200
    body = res.json()
    assert body["start"] == "2026-11-19" and body["end"] == "2026-11-19"
    assert body["total"] == 2
    assert len(body["days"]) == 1
    day = body["days"][0]
    assert day["date"] == "2026-11-19"
    assert day["count"] == 2
    assert day["truncated"] is False
    assert {i["name"] for i in day["items"]} == {"甲", "乙"}


def test_types_counts_ignore_type_filter_and_exclusions(db, admin):
    lib = _library(db)
    at = datetime(2026, 11, 19, 4, 0)  # 11-19 12:00 CST
    _item(db, lib, "电影甲", item_type="movie", added=at)
    _item(db, lib, "单集甲", item_type="episode", added=at)
    _item(db, lib, "整季", item_type="season", added=at)  # season 不上日历
    _item(db, lib, "隐藏片", item_type="movie", added=at, is_hidden=True)

    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/chase-calendar",
                     params={"start": "2026-11-19", "end": "2026-11-19",
                             "item_type": "episode"})
    assert res.status_code == 200
    body = res.json()
    # 角标是全类型总量（与用户端口径一致），不受当前筛选影响
    assert body["types"] == {"movie": 1, "episode": 1}
    assert body["total"] == 1
    names = [i["name"] for d in body["days"] for i in d["items"]]
    assert names == ["单集甲"]


def test_followers_on_series_and_episode_rows(db, admin):
    lib = _library(db)
    at = datetime(2026, 11, 19, 4, 0)
    series = _item(db, lib, "追风者", item_type="series", added=at)
    ep = _item(db, lib, "第3集", item_type="episode", added=at,
               series_id=series.id, season_number=1, episode_number=3)
    _item(db, lib, "电影甲", item_type="movie", added=at)
    alice, bob, carol = _user(db), _user(db), _user(db)
    _follow(db, alice, series)
    _follow(db, bob, series)
    _follow(db, carol, series, status="cancelled")

    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/chase-calendar",
                     params={"start": "2026-11-19", "end": "2026-11-19"})
    assert res.status_code == 200
    items = {i["name"]: i for d in res.json()["days"] for i in d["items"]}
    assert items["追风者"]["followers"] == 2
    assert items["第3集"]["followers"] == 2
    assert items["第3集"]["series_name"] == "追风者"
    assert items["第3集"]["season_number"] == 1
    assert items["第3集"]["episode_number"] == 3
    assert items["电影甲"]["followers"] is None
    assert ep.id  # silence linter; ids are exercised via detail test


def test_default_range_is_last_30_days(db, admin):
    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/chase-calendar")
    assert res.status_code == 200
    body = res.json()
    today = cal_admin._today_shanghai().date()
    assert body["end"] == today.isoformat()
    assert body["start"] == (today - timedelta(days=29)).isoformat()
    assert body["days"] == []
    assert body["total"] == 0


def test_detail_episode_with_follow_block(db, admin):
    lib = _library(db)
    at = datetime(2026, 11, 19, 4, 0)
    series = _item(db, lib, "追风者", item_type="series", added=at,
                   production_year=2024, tmdb_id="12345",
                   premiere_date=datetime(2024, 3, 1))
    ep = _item(db, lib, "第3集", item_type="episode", added=at,
               series_id=series.id, season_number=1, episode_number=3,
               file_path="/mnt/lib/追风者/S01E03.mkv", media_source="WEB-DL")
    alice, bob = _user(db), _user(db)
    _follow(db, alice, series)
    _follow(db, bob, series, status="cancelled")

    client = _client(db, admin_user=admin)
    res = client.get(f"/api/admin/chase-calendar/items/{ep.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "第3集"
    assert body["library_name"] == "剧集库"
    assert body["file_path"] == "/mnt/lib/追风者/S01E03.mkv"
    assert body["series"] == {"series_id": series.id, "series_name": "追风者"}
    follow = body["follow"]
    assert follow["followers_active"] == 1
    assert follow["followers_total"] == 2
    assert follow["episode_count"] == 1
    assert follow["recent_followers"][0]["username"] in {
        alice.username, bob.username}
    # MP 无本地关联：明示 None，不许编造状态
    assert body["mp_subscription"] is None
    assert "未落库" in body["mp_note"]


def test_detail_404_and_auth(db, admin):
    client = _client(db, admin_user=admin)
    res = client.get("/api/admin/chase-calendar/items/999999")
    assert res.status_code == 404

    res = _client(db, admin_error=401).get("/api/admin/chase-calendar")
    assert res.status_code == 401
    res = _client(db, admin_error=403).get("/api/admin/chase-calendar")
    assert res.status_code == 403
