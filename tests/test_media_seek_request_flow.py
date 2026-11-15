# -*- coding: utf-8 -*-
"""求片反馈按作品组织（2026-11-15 对齐友站形态）后端测试。

覆盖：
1. **搜索标注**：候选带原名（original_name）与未完成请求标注
   （open_seek / open_chase，追新类含补缺集行）；
2. **追新落单**（_create_chase_request）：只许库内已有——不在库 400；
   在库落 kind='chase' 行；本人已有未完成追新 409；他人已有转附议；
   与整片求片同一额度收口（月度用完 429）；追新行不被入库联动误翻完成；
3. **补充说明**（_add_supplement）：本人未完结行可补；他人行 404；
   已完结/已撤回 400；超长 400；
4. **作品详情聚合**（media_seek.work_detail）：按 tmdb 汇集求片+追新、
   计数三分、my_open 指针、补充说明随行下发、撤回行不计；
   无 tmdb 的原名行按片名兜底认亲；
5. **广场**：kind='chase' 行计入追新数；组海报可由提交时的海报快照回退；
6. **求片落单**：poster_url 快照落库；具体说明（note）超 2000 字 400。

全部用隔离的内存 SQLite，不碰网络（TMDB 客户端用假替身）。
"""
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import media_seek, models
from backend.api import user as user_api
from backend.emby_server import models as em
from backend.emby_server import tmdb
from backend.integrations import store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    store.invalidate()
    session.close()


def _user(db, username):
    user = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _item(db, *, name="谷雨", tmdb_id="1001", item_type="series", year=2026,
          guid=None):
    lib = em.Library(guid=f"lib-{name}-{tmdb_id}", name="测试库",
                     collection_type="tvshows", paths="")
    db.add(lib)
    db.flush()
    item = em.MediaItem(
        guid=guid or f"item-{tmdb_id}", library_id=lib.id, item_type=item_type,
        name=name, tmdb_id=tmdb_id, production_year=year,
        file_path=f"/media/{name}.mkv",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _req(db, user, name, *, status="pending", tmdb_id=None, kind="seek",
         type="series", year="2026", note=None, poster_url=None):
    r = models.MovieRequest(
        user_id=user.id, movie_name=name, type=type, year=year, status=status,
        tmdb_id=tmdb_id, request_kind=kind, note=note, poster_url=poster_url,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


class FakeTmdb:
    def __init__(self, *, configured=True, hits=None):
        self.configured = configured
        self._hits = hits or {}

    def search_candidates(self, name, kind, limit=6):
        return list(self._hits.get(kind, []))[:limit]


TV_HIT = {
    "id": 1001, "name": "谷雨", "original_name": "Gu Yu",
    "first_air_date": "2026-04-01", "poster_path": "/gy.jpg",
    "vote_average": 8.1, "overview": "一场雨",
}
MOVIE_HIT = {
    "id": 2002, "title": "遮天", "original_title": "Shrouding the Heavens",
    "release_date": "2025-01-01", "poster_path": "/zt.jpg",
    "vote_average": 7.9, "overview": "遮天",
}


# ==================== 1. 搜索标注 ====================

def test_search_candidates_carry_original_name_and_open_flags(db, monkeypatch):
    me = _user(db, "me")
    other = _user(db, "other")
    # 谷雨：已在库，且他人有一条未完成的追新（补缺集行也算追新类）
    _item(db, tmdb_id="1001")
    _req(db, other, "谷雨", tmdb_id="1001", kind="missing_episodes")
    # 遮天：不在库，本人有一条未完成的求片
    _req(db, me, "遮天", tmdb_id="2002", kind="seek", type="movie", year="2025")
    monkeypatch.setattr(tmdb, "tmdb_client", FakeTmdb(
        hits={"series": [TV_HIT], "movie": [MOVIE_HIT]}))

    out = media_seek.search_candidates(db, "谷")
    by_id = {r["tmdb_id"]: r for r in out["results"]}
    assert by_id["1001"]["original_name"] == "Gu Yu"
    assert by_id["1001"]["in_library"] is True
    assert by_id["1001"]["open_chase"] is True
    assert by_id["1001"]["open_seek"] is False
    assert by_id["2002"]["original_name"] == "Shrouding the Heavens"
    assert by_id["2002"]["in_library"] is False
    assert by_id["2002"]["open_seek"] is True
    assert by_id["2002"]["open_chase"] is False


def test_search_open_flags_ignore_finished_rows(db, monkeypatch):
    other = _user(db, "other")
    _req(db, other, "遮天", tmdb_id="2002", kind="seek", type="movie",
         year="2025", status="completed")
    _req(db, other, "遮天", tmdb_id="2002", kind="chase", type="movie",
         year="2025", status="rejected")
    monkeypatch.setattr(tmdb, "tmdb_client", FakeTmdb(hits={"movie": [MOVIE_HIT]}))
    out = media_seek.search_candidates(db, "遮天")
    r = out["results"][0]
    assert r["open_seek"] is False and r["open_chase"] is False


# ==================== 2. 追新落单 ====================

def test_chase_requires_library_item(db):
    me = _user(db, "me")
    with pytest.raises(HTTPException) as ei:
        user_api._create_chase_request(db, me, "9999", "追新：第 2 季")
    assert ei.value.status_code == 400


def test_chase_creates_row_against_library_item(db):
    me = _user(db, "me")
    _item(db, tmdb_id="1001")
    out = user_api._create_chase_request(
        db, me, "1001", "追新：谷雨 S02E06 还没更",
        poster_url="https://image.tmdb.org/t/p/w300/gy.jpg")
    assert out["created"] is True
    row = db.query(models.MovieRequest).filter_by(id=out["request_id"]).one()
    assert row.request_kind == "chase"
    assert row.tmdb_id == "1001"
    assert row.movie_name == "谷雨"
    assert row.type == "series"
    assert row.note == "追新：谷雨 S02E06 还没更"
    assert row.poster_url.endswith("/gy.jpg")
    assert row.status == "pending"


def test_chase_dup_mine_conflict(db):
    me = _user(db, "me")
    _item(db, tmdb_id="1001")
    _req(db, me, "谷雨", tmdb_id="1001", kind="chase")
    with pytest.raises(HTTPException) as ei:
        user_api._create_chase_request(db, me, "1001", "追新：谷雨")
    assert ei.value.status_code == 409
    assert ei.value.detail  # 带已有请求指引文案


def test_chase_dup_other_converts_to_vote(db):
    me = _user(db, "me")
    other = _user(db, "other")
    _item(db, tmdb_id="1001")
    existing = _req(db, other, "谷雨", tmdb_id="1001", kind="missing_episodes")
    out = user_api._create_chase_request(db, me, "1001", "追新：谷雨")
    assert out["created"] is False
    assert out["request_id"] == existing.id
    assert out["voted"] is True
    # 没有新建第二条追新行
    assert db.query(models.MovieRequest).filter_by(tmdb_id="1001").count() == 1


def test_chase_blocked_when_monthly_quota_used_up(db):
    me = _user(db, "me")
    _item(db, tmdb_id="1001")
    db.add(models.SystemConfig(key="paid_request_monthly", value="1"))
    db.commit()
    _req(db, me, "别的片", tmdb_id="3003", kind="seek", type="movie")
    with pytest.raises(HTTPException) as ei:
        user_api._create_chase_request(db, me, "1001", "追新：谷雨")
    assert ei.value.status_code == 429


def test_chase_row_not_flipped_by_library_sync(db):
    me = _user(db, "me")
    _item(db, tmdb_id="1001")
    out = user_api._create_chase_request(db, me, "1001", "追新：谷雨")
    row = db.query(models.MovieRequest).filter_by(id=out["request_id"]).one()
    # 追的作品本来就在库：入库联动只认整片求片，追新行不许被误翻完成
    assert media_seek.sync_seek_library_row(db, row) is False
    db.refresh(row)
    assert row.status == "pending"


def test_chase_note_too_long(db):
    me = _user(db, "me")
    _item(db, tmdb_id="1001")
    with pytest.raises(HTTPException) as ei:
        user_api._create_chase_request(db, me, "1001", "追" * 2001)
    assert ei.value.status_code == 400


# ==================== 3. 补充说明 ====================

def test_supplement_by_owner_on_open_request(db):
    me = _user(db, "me")
    row = _req(db, me, "遮天", tmdb_id="2002", kind="seek", type="movie",
               year="2025")
    out = user_api._add_supplement(db, me, row.id, "补充：想要 4K 版本")
    assert out["body"] == "补充：想要 4K 版本"
    assert db.query(models.MovieRequestSupplement).count() == 1


def test_supplement_rejects_non_owner(db):
    me = _user(db, "me")
    other = _user(db, "other")
    row = _req(db, other, "遮天", tmdb_id="2002", kind="seek", type="movie",
               year="2025")
    with pytest.raises(HTTPException) as ei:
        user_api._add_supplement(db, me, row.id, "补充")
    assert ei.value.status_code == 404


def test_supplement_rejects_finished_or_withdrawn(db):
    me = _user(db, "me")
    done = _req(db, me, "遮天", tmdb_id="2002", kind="seek", type="movie",
                year="2025", status="completed")
    with pytest.raises(HTTPException) as ei:
        user_api._add_supplement(db, me, done.id, "补充")
    assert ei.value.status_code == 400
    gone = _req(db, me, "遮天2", tmdb_id="2003", kind="seek", type="movie",
                year="2025", status="withdrawn")
    with pytest.raises(HTTPException) as ei:
        user_api._add_supplement(db, me, gone.id, "补充")
    assert ei.value.status_code == 400


def test_supplement_rejects_empty_and_too_long(db):
    me = _user(db, "me")
    row = _req(db, me, "遮天", tmdb_id="2002", kind="seek", type="movie",
               year="2025")
    with pytest.raises(HTTPException):
        user_api._add_supplement(db, me, row.id, "  ")
    with pytest.raises(HTTPException):
        user_api._add_supplement(db, me, row.id, "x" * 2001)


# ==================== 4. 作品详情聚合 ====================

def test_work_detail_aggregates_by_tmdb(db):
    me = _user(db, "me")
    other = _user(db, "other")
    _item(db, tmdb_id="1001")
    seek = _req(db, other, "谷雨", tmdb_id="1001", kind="seek",
                note="求片：谷雨", status="completed")
    chase = _req(db, me, "谷雨", tmdb_id="1001", kind="chase",
                 note="追新：谷雨 S02E06")
    _req(db, me, "谷雨", tmdb_id="1001", kind="chase", status="withdrawn")
    user_api._add_supplement(db, me, chase.id, "补充：是第 6 集")

    out = media_seek.work_detail(db, me, tmdb_id="1001")
    assert out["work"]["in_library"] is True
    assert out["work"]["name"] == "谷雨"
    assert out["counts"] == {"seek": 1, "chase": 1, "feedback": 0, "total": 2}
    assert out["my_open"] == {"seek": None, "chase": chase.id}
    rows = {r["id"]: r for r in out["requests"]}
    assert set(rows) == {seek.id, chase.id}, "撤回行不进详情"
    assert rows[chase.id]["mine"] is True
    assert rows[chase.id]["kind"] == "chase"
    assert rows[chase.id]["state"] == "pending"
    assert rows[seek.id]["state"] == "done"
    sups = rows[chase.id]["supplements"]
    assert len(sups) == 1 and sups[0]["body"] == "补充：是第 6 集"
    assert sups[0]["mine"] is True


def test_work_detail_title_only_fallback(db):
    me = _user(db, "me")
    _req(db, me, "无名之片", tmdb_id=None, kind="seek", type="movie",
         year="2020")
    out = media_seek.work_detail(db, me, name="无名之片", year="2020",
                                 media_type="movie")
    assert out["work"]["in_library"] is False
    assert out["counts"]["total"] == 1
    assert out["my_open"]["seek"] is not None


def test_work_detail_poster_falls_back_to_snapshot(db):
    me = _user(db, "me")
    _req(db, me, "遮天", tmdb_id="2002", kind="seek", type="movie",
         year="2025", poster_url="https://image.tmdb.org/t/p/w300/zt.jpg")
    out = media_seek.work_detail(db, me, tmdb_id="2002")
    assert out["work"]["poster_url"].endswith("/zt.jpg")
    assert out["work"]["in_library"] is False


# ==================== 5. 广场 ====================

def test_plaza_counts_chase_kind_and_poster_snapshot(db):
    me = _user(db, "me")
    _req(db, me, "谷雨", tmdb_id="1001", kind="chase",
         poster_url="https://image.tmdb.org/t/p/w300/gy.jpg")
    _req(db, me, "谷雨", tmdb_id="1001", kind="missing_episodes")
    groups = media_seek.plaza_groups(db, me.id)
    assert len(groups) == 1
    g = groups[0]
    assert g["chase_count"] == 2 and g["seek_count"] == 0
    assert g["poster_url"].endswith("/gy.jpg")
    # kind=chase 过滤要同时命中两种追新行
    only = media_seek.plaza_groups(db, me.id, kind="chase")
    assert len(only) == 1 and only[0]["total_count"] == 2
    none = media_seek.plaza_groups(db, me.id, kind="seek")
    assert none == []


# ==================== 6. 求片落单 ====================

def test_seek_create_stores_poster_and_note(db):
    me = _user(db, "me")
    req = user_api.MediaSeekRequest(
        movie_name="遮天", year="2025", type="movie", tmdb_id="2002",
        note="求片：遮天", poster_url="https://image.tmdb.org/t/p/w300/zt.jpg")
    out = user_api._create_media_seek_result(db, me, "遮天", req)
    assert out["created"] is True
    row = db.query(models.MovieRequest).filter_by(id=out["request_id"]).one()
    assert row.poster_url.endswith("/zt.jpg")
    assert row.note == "求片：遮天"


def test_seek_create_rejects_overlong_note(db):
    me = _user(db, "me")
    req = user_api.MediaSeekRequest(
        movie_name="遮天", year="2025", type="movie", tmdb_id="2002",
        note="x" * 2001)
    with pytest.raises(HTTPException) as ei:
        user_api._create_media_seek_result(db, me, "遮天", req)
    assert ei.value.status_code == 400


def test_seek_create_drops_dirty_poster_url(db):
    me = _user(db, "me")
    req = user_api.MediaSeekRequest(
        movie_name="遮天", year="2025", type="movie", tmdb_id="2002",
        poster_url="javascript:alert(1)")
    out = user_api._create_media_seek_result(db, me, "遮天", req)
    row = db.query(models.MovieRequest).filter_by(id=out["request_id"]).one()
    assert row.poster_url is None
