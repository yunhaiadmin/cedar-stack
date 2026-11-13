# -*- coding: utf-8 -*-
"""请求广场按媒体聚合（media_seek.plaza_groups）测试。

覆盖：
1. 同 tmdb_id 的多条求片合成一组：计数（求片/追新/总数）、处理中计数、明细只带 pending；
2. 原名兜底行（无 tmdb）按归一化片名+年份+类型成组，同名不同年不串组；
3. 撤回行不进广场；状态过滤先作用在行上再聚合（done 只剩完成行的话题）；
4. kind 过滤：seek 只计整片求片、chase 只计补缺集行；
5. 关键词 q 按片名过滤；
6. 库内命中：tmdb 对上可见条目 → in_library + 海报 + 条目 guid；
   已隐藏条目不算命中；组内有 completed 行时无需命中也算库内已有；
7. feedback_count 恒 0（工单不进广场的数据口径）。

全部用隔离的内存 SQLite。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import media_seek, models
from backend.emby_server import models as em
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


_seq = [0]


def _req(db, user, name, *, status="pending", tmdb_id=None, kind="seek",
         type="movie", year="2024", votes=0, emby_item_id=None, days_ago=0):
    _seq[0] += 1
    r = models.MovieRequest(
        user_id=user.id, movie_name=name, type=type, year=year, status=status,
        tmdb_id=tmdb_id, request_kind=kind, vote_count=votes,
        emby_item_id=emby_item_id,
        created_at=datetime.now() - timedelta(days=days_ago),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_same_tmdb_groups_together(db):
    u1 = _user(db, "alice")
    u2 = _user(db, "bob")
    _req(db, u1, "黑客帝国", tmdb_id="603", votes=2)
    _req(db, u2, "黑客帝国", tmdb_id="603", status="approved")
    _req(db, u2, "黑客帝国", tmdb_id="603", kind="missing_episodes",
         type="series")
    groups = media_seek.plaza_groups(db, u1.id)
    assert len(groups) == 1
    g = groups[0]
    assert g["movie_name"] == "黑客帝国"
    assert g["seek_count"] == 2
    assert g["chase_count"] == 1
    assert g["feedback_count"] == 0
    assert g["total_count"] == 3
    assert g["processing_count"] == 3
    # 明细只带 pending 行（approved 不可附议、不进明细）：pending 有 2 条
    assert len(g["requests"]) == 2
    assert {r["request_kind"] for r in g["requests"]} == {"seek", "missing_episodes"}
    assert any(r["mine"] for r in g["requests"])


def test_title_only_grouping_by_normalized_name(db):
    u1 = _user(db, "alice")
    _req(db, u1, "《在阳光下腐烂》", tmdb_id=None, year="2023")
    _req(db, u1, "在阳光下腐烂", tmdb_id=None, year="2023")
    _req(db, u1, "在阳光下腐烂", tmdb_id=None, year="1999")
    groups = media_seek.plaza_groups(db, u1.id)
    assert len(groups) == 2
    by_year = {g["year"]: g for g in groups}
    assert by_year["2023"]["total_count"] == 2
    assert by_year["1999"]["total_count"] == 1


def test_withdrawn_excluded_and_status_filter(db):
    u1 = _user(db, "alice")
    _req(db, u1, "片甲", tmdb_id="11", status="withdrawn")
    _req(db, u1, "片乙", tmdb_id="22", status="completed")
    _req(db, u1, "片乙", tmdb_id="22", status="pending")
    groups = media_seek.plaza_groups(db, u1.id)
    assert [g["movie_name"] for g in groups] == ["片乙"]
    done = media_seek.plaza_groups(db, u1.id, status="done")
    assert len(done) == 1
    assert done[0]["total_count"] == 1
    assert done[0]["in_library"] is True
    pending = media_seek.plaza_groups(db, u1.id, status="pending")
    assert len(pending) == 1
    assert pending[0]["total_count"] == 1


def test_kind_filter_and_search(db):
    u1 = _user(db, "alice")
    _req(db, u1, "剧甲", tmdb_id="33", type="series", kind="missing_episodes")
    _req(db, u1, "剧甲", tmdb_id="33", type="series", kind="seek")
    _req(db, u1, "片乙", tmdb_id="44")
    chase = media_seek.plaza_groups(db, u1.id, kind="chase")
    assert len(chase) == 1
    assert chase[0]["chase_count"] == 1
    assert chase[0]["seek_count"] == 0
    found = media_seek.plaza_groups(db, u1.id, q="剧甲")
    assert len(found) == 1
    assert found[0]["movie_name"] == "剧甲"
    assert media_seek.plaza_groups(db, u1.id, q="不存在的片") == []


def _library(db):
    lib = em.Library(guid="lib-plaza", name="广场桩库", collection_type="movies",
                     is_enabled=True)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return lib


def test_library_hit_marks_in_library_and_poster(db):
    u1 = _user(db, "alice")
    lib = _library(db)
    _req(db, u1, "黑球", tmdb_id="77", status="pending")
    item = em.MediaItem(
        guid="guid-77", library_id=lib.id, name="黑球", item_type="movie",
        tmdb_id="77",
        primary_image_url="https://img.example/poster.jpg",
        is_hidden=False,
    )
    db.add(item)
    db.commit()
    groups = media_seek.plaza_groups(db, u1.id)
    assert len(groups) == 1
    g = groups[0]
    assert g["in_library"] is True
    assert g["poster_url"] == "https://img.example/poster.jpg"
    assert g["emby_item_id"] == "guid-77"


def test_hidden_library_item_not_counted(db):
    u1 = _user(db, "alice")
    lib = _library(db)
    _req(db, u1, "黑球", tmdb_id="78", status="pending")
    item = em.MediaItem(
        guid="guid-78", library_id=lib.id, name="黑球", item_type="movie",
        tmdb_id="78",
        primary_image_url="https://img.example/hidden.jpg",
        is_hidden=True,
    )
    db.add(item)
    db.commit()
    groups = media_seek.plaza_groups(db, u1.id)
    assert groups[0]["in_library"] is False
    assert groups[0]["poster_url"] is None
