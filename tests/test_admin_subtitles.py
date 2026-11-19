# -*- coding: utf-8 -*-
"""字幕管理只读盘点口（二期全缺第 7 项）：GET /api/admin/subtitles/items*。

覆盖：
- 鉴权：无凭证 401、非管理员 403。
- 列表：只列 movie/episode 主记录（软删、合并副本、series 节点不入列）；
  每条字幕轨数 = MediaStream 里 stream_type='Subtitle' 的行数，
  内嵌（is_external 非 True）/ 外挂（is_external=True）分开计数。
- 筛选：有轨 / 无轨；搜索按条目名、集按剧名也能命中。
- 分页与参数校验：limit 钳制、非法 subtitle_filter 400。
- 明细：轨按 stream_index 排序，外挂带 external_path、内嵌为空；
  不存在 / 非叶子条目 404。
- 只读：POST/PUT/DELETE 全部 405（本口没有任何写端点）。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-subtitles-admin-key-32chars-p")

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_subtitles as subs_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture()
def staff(db):
    user = models.WebUser(
        username="subs_admin_" + uuid.uuid4().hex[:8], password_hash="x",
        is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(subs_admin.admin_subtitles_router)
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


_BASE = datetime(2026, 11, 19, 10, 0, 0)



def _item(db, library_id, guid, name, item_type, minutes, **kw):
    row = em.MediaItem(
        guid=guid, library_id=library_id, name=name, item_type=item_type,
        date_added=_BASE + timedelta(minutes=minutes), **kw)
    db.add(row)
    db.flush()
    return row


def _stream(db, item_id, index, **kw):
    row = em.MediaStream(item_id=item_id, stream_index=index, **kw)
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def seeded(db, staff):
    lib = em.Library(guid="lib-subtitles-test", name="字幕测试库",
                     collection_type="mixed", paths="")
    db.add(lib)
    db.flush()

    series = _item(db, lib.id, "guid-series-1", "盘点剧集", "series", 0)
    movie_a = _item(db, lib.id, "guid-movie-a", "有字幕电影", "movie", 3,
                    production_year=2024)
    episode = _item(db, lib.id, "guid-ep-1", "第 1 集", "episode", 2,
                    series_id=series.id, season_number=1, episode_number=1)
    movie_b = _item(db, lib.id, "guid-movie-b", "无字幕电影", "movie", 1)
    gone = _item(db, lib.id, "guid-gone", "已下架电影", "movie", 4,
                 deleted_at=_BASE)
    _item(db, lib.id, "guid-copy", "合并副本电影", "movie", 5,
          merged_into_id=movie_a.id)

    _stream(db, movie_a.id, 0, stream_type="Video", codec="h264")
    _stream(db, movie_a.id, 1, stream_type="Subtitle", codec="subrip",
            language="chi", display_title="简体中文",
            is_default=True, is_forced=False, is_external=False)
    _stream(db, movie_a.id, 2, stream_type="Subtitle", codec="srt",
            language="chi", display_title="有字幕电影.chi.srt",
            title="有字幕电影.chi.srt", is_default=False, is_forced=False,
            is_external=True, external_path="/media/有字幕电影.chi.srt")
    # is_external=NULL 的内嵌轨（ffprobe 旧写入口径）：必须算内嵌
    _stream(db, movie_a.id, 3, stream_type="Subtitle", codec="ass",
            language="chi", display_title="强制字幕",
            is_default=False, is_forced=True, is_external=None)
    _stream(db, episode.id, 1, stream_type="Subtitle", codec="ass",
            language="chi", display_title="盘点剧集 - S01E01.chi.ass",
            is_default=True, is_forced=False, is_external=True,
            external_path="mount://gd/盘点剧集 - S01E01.chi.ass")
    _stream(db, movie_b.id, 0, stream_type="Video", codec="hevc")
    _stream(db, gone.id, 1, stream_type="Subtitle", codec="subrip",
            language="chi", is_external=False)
    # gone 是软删行：提交后 ORM 刷新会被软删钩子挡下（ObjectDeletedError），
    # 提前把 id 摘成整数给 404 用例用
    gone_id = gone.id
    db.commit()
    return {"staff": staff, "series": series, "movie_a": movie_a,
            "episode": episode, "movie_b": movie_b, "gone_id": gone_id}


_URL = "/api/admin/subtitles/items"



# ------------------------------------------------------------ 鉴权

def test_requires_admin(db, seeded):
    assert _client(db, admin_error=401).get(_URL).status_code == 401
    assert _client(db, admin_error=403).get(_URL).status_code == 403
    detail = f"{_URL}/{seeded['movie_a'].id}"
    assert _client(db, admin_error=401).get(detail).status_code == 401
    assert _client(db, admin_error=403).get(detail).status_code == 403


# ------------------------------------------------------------ 列表

def test_list_counts_and_scope(db, seeded):
    r = _client(db, seeded["staff"]).get(_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    # 软删、合并副本、series 节点都不入列；按 date_added 倒序
    assert body["total"] == 3
    names = [item["name"] for item in body["items"]]
    assert names == ["有字幕电影", "第 1 集", "无字幕电影"]
    by_name = {item["name"]: item for item in body["items"]}
    a = by_name["有字幕电影"]
    assert a["subtitle_count"] == 3
    assert a["external_count"] == 1
    assert a["embedded_count"] == 2
    assert a["production_year"] == 2024
    ep = by_name["第 1 集"]
    assert ep["subtitle_count"] == 1
    assert ep["external_count"] == 1
    assert ep["embedded_count"] == 0
    assert ep["series_name"] == "盘点剧集"
    assert ep["season_number"] == 1 and ep["episode_number"] == 1
    b = by_name["无字幕电影"]
    assert b["subtitle_count"] == 0
    assert b["external_count"] == 0 and b["embedded_count"] == 0


def test_list_subtitle_filter(db, seeded):
    c = _client(db, seeded["staff"])
    body = c.get(_URL, params={"subtitle_filter": "with"}).json()
    assert body["total"] == 2
    assert [i["name"] for i in body["items"]] == ["有字幕电影", "第 1 集"]
    body = c.get(_URL, params={"subtitle_filter": "without"}).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "无字幕电影"
    r = c.get(_URL, params={"subtitle_filter": "bogus"})
    assert r.status_code == 400, r.text


def test_list_search_by_title_and_series(db, seeded):
    c = _client(db, seeded["staff"])
    body = c.get(_URL, params={"q": "无字幕"}).json()
    assert body["total"] == 1 and body["items"][0]["name"] == "无字幕电影"
    # 集按剧名搜也能命中
    body = c.get(_URL, params={"q": "盘点剧集"}).json()
    assert body["total"] == 1 and body["items"][0]["name"] == "第 1 集"
    body = c.get(_URL, params={"q": "不存在的片名"}).json()
    assert body["total"] == 0 and body["items"] == []


def test_list_pagination(db, seeded):
    c = _client(db, seeded["staff"])
    body = c.get(_URL, params={"limit": 2, "offset": 0}).json()
    assert len(body["items"]) == 2 and body["total"] == 3
    body = c.get(_URL, params={"limit": 2, "offset": 2}).json()
    assert len(body["items"]) == 1 and body["total"] == 3
    body = c.get(_URL, params={"limit": 99999}).json()
    assert len(body["items"]) == 3



# ------------------------------------------------------------ 明细

def test_detail_tracks(db, seeded):
    c = _client(db, seeded["staff"])
    r = c.get(f"{_URL}/{seeded['movie_a'].id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "有字幕电影"
    assert body["subtitle_count"] == 3
    tracks = body["tracks"]
    assert [t["stream_index"] for t in tracks] == [1, 2, 3]
    assert [t["source"] for t in tracks] == ["embedded", "external", "embedded"]
    first, ext, forced = tracks
    assert first["codec"] == "subrip" and first["language"] == "chi"
    assert first["title"] == "简体中文"
    assert first["is_default"] is True and first["is_forced"] is False
    assert first["external_path"] == ""
    assert ext["codec"] == "srt"
    assert ext["external_path"] == "/media/有字幕电影.chi.srt"
    assert forced["is_forced"] is True and forced["codec"] == "ass"


def test_detail_episode_series_name(db, seeded):
    c = _client(db, seeded["staff"])
    body = c.get(f"{_URL}/{seeded['episode'].id}").json()
    assert body["series_name"] == "盘点剧集"
    assert len(body["tracks"]) == 1
    assert body["tracks"][0]["source"] == "external"
    assert body["tracks"][0]["external_path"].startswith("mount://")


def test_detail_404s(db, seeded):
    c = _client(db, seeded["staff"])
    assert c.get(f"{_URL}/999999").status_code == 404
    # series 是结构节点，不在盘点范围
    assert c.get(f"{_URL}/{seeded['series'].id}").status_code == 404
    # 已下架条目同样 404
    assert c.get(f"{_URL}/{seeded['gone_id']}").status_code == 404


# ------------------------------------------------------------ 只读

def test_read_only_no_write_routes(db, seeded):
    c = _client(db, seeded["staff"])
    detail = f"{_URL}/{seeded['movie_a'].id}"
    assert c.post(_URL, json={}).status_code == 405
    assert c.put(detail, json={}).status_code == 405
    assert c.delete(detail).status_code == 405
