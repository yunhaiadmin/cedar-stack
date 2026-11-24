# -*- coding: utf-8 -*-
"""多版本下发：同组副本在 PlaybackInfo 与详情 Versions 的归组口径。

生产实证（2026-11-24，临时账号走真实接口测）：
- 单集《早春晴朗》S01E21 DB 有 12 个活副本，PlaybackInfo 只下发 1 源；
- 电影《海洋奇缘》(tmdb 444341) 跨 3 库 3 活行 + 1 合并行，
  PlaybackInfo 只下发 1 源；跨库副本各自详情 Versions 为空；
- 单集详情根本没有 Versions 字段。

钉死口径：
- PlaybackInfo：有同组活副本时每副本一条 MediaSource（Id = 该副本
  guid、取流地址按副本自身经唯一构造点签发、Name 带版本区分标签），
  请求条目本身恒为第一条（默认源）；无副本时下发与改造前逐字一致
  （单源、Name 不加后缀）。
- 分组键：单集 = series_id + season_number + episode_number 三者相同
  且季/集号均非空（集号为空的行是解析失败堆叠，绝不归组）；电影 =
  tmdb_id 相同（非空、跨库）+ merged_into_id 指向组内任一行的合并行；
  tmdb 为空的电影不归组。
- 详情 Versions：单集按同一分组键拼装（与电影同结构、同排序）；
  电影扩到跨库同 tmdb 组 + 合并行；原有同目录兄弟 + 物理合并来源
  不丢；无副本时 Versions = []（与电影现行口径一致）。

harness 仿 tests/test_playbackinfo_default_source_id.py，全部离线。
"""
import asyncio
import os
import uuid
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import models as core
from backend.database import Base
from backend.emby_server import api, delivery_plan, pool_gate
from backend.emby_server import probe_worker, stream_accel
from backend.emby_server import transfer_variant as _tv
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _lib(db, name, ctype):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     collection_type=ctype, is_enabled=True)
    db.add(lib)
    db.flush()
    return lib


def _item(db, lib, item_type, name, file_path, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name, file_path=file_path,
        is_hidden=False, date_added=datetime.now(), **kw)
    db.add(it)
    db.flush()
    return it


@pytest.fixture()
def scene(db):
    """生产同形态种子：单集 12 副本 + 电影跨库 3 活行 1 合并行 + 若干负例。"""
    user = core.WebUser(username="multiver", password_hash="x",
                        is_active=True)
    db.add(user)
    db.flush()
    lib_tv = _lib(db, "国产剧", "tvshows")
    lib_film = _lib(db, "影库", "movies")
    lib_foreign = _lib(db, "外语电影", "movies")
    lib_anime = _lib(db, "动画电影", "movies")

    # ---- 单集组：《早春晴朗》S01E21 共 12 个活副本（其一为合并行）
    series = _item(db, lib_tv, "series", "早春晴朗", None, tmdb_id="2001")
    eps = []
    for i in range(12):
        h = 2160 if i == 3 else 1080
        eps.append(_item(
            db, lib_tv, "episode", "第 21 集",
            f"mount://1/MoviePilot/剧集/国产剧/早春晴朗/Season 1/"
            f"S01E21 - {h}p-{i}.mkv",
            series_id=series.id, season_number=1, episode_number=21,
            height=h, width=h * 16 // 9, size=1_000_000_000 + i,
            bitrate=8_000_000 + i * 1000, duration_ticks=24_040_000_000,
            container="mkv"))
    eps[5].merged_into_id = eps[0].id

    # ---- 假分组：《僵尸国度》式集号解析失败堆叠（episode_number 全空）
    series_null = _item(db, lib_tv, "series", "僵尸国度", None,
                        tmdb_id="2002")
    null_eps = [
        _item(db, lib_tv, "episode", "未识别集",
              f"mount://1/MoviePilot/剧集/国产剧/僵尸国度/Season 1/x{i}.mkv",
              series_id=series_null.id, season_number=1,
              episode_number=None, height=1080, size=10,
              bitrate=1_000_000, duration_ticks=24_040_000_000,
              container="mkv")
        for i in range(3)
    ]

    # ---- 孤集：无任何副本
    series_lone = _item(db, lib_tv, "series", "孤独剧", None,
                        tmdb_id="2003")
    lone_ep = _item(
        db, lib_tv, "episode", "第 1 集",
        "mount://1/MoviePilot/剧集/国产剧/孤独剧/Season 1/S01E01.mkv",
        series_id=series_lone.id, season_number=1, episode_number=1,
        height=1080, size=500, bitrate=5_000_000,
        duration_ticks=24_040_000_000, container="mkv")

    # ---- 电影组：《海洋奇缘》tmdb 444341 跨 3 库 3 活行 + 1 合并行
    m1 = _item(db, lib_film, "movie", "海洋奇缘",
               "mount://2/video/影库/海洋奇缘/Moana - 1080p.mkv",
               tmdb_id="444341", height=1080, width=1920,
               size=8_000_000_000, bitrate=10_000_000,
               duration_ticks=61_200_000_000, container="mkv")
    m2 = _item(db, lib_foreign, "movie", "海洋奇缘",
               "mount://1/MoviePilot/电影/外语电影/Moana - 1080p.mkv",
               tmdb_id="444341", height=1080, width=1920,
               size=7_000_000_000, bitrate=9_000_000,
               duration_ticks=61_200_000_000, container="mkv")
    m3 = _item(db, lib_anime, "movie", "海洋奇缘",
               "mount://1/remux/REMUX电影/动画电影/Moana - 2160p REMUX.mkv",
               tmdb_id="444341", height=2160, width=3840,
               size=40_000_000_000, bitrate=40_000_000,
               duration_ticks=61_200_000_000, container="mkv")
    m4 = _item(db, lib_film, "movie", "海洋奇缘",
               "mount://2/video/影库/海洋奇缘/Moana - 2160p.mkv",
               tmdb_id="444341", height=2160, width=3840,
               size=30_000_000_000, bitrate=35_000_000,
               duration_ticks=61_200_000_000, container="mkv",
               merged_into_id=m1.id)

    # ---- 无 tmdb 电影两行（不同目录）：不归组
    n1 = _item(db, lib_film, "movie", "无名片甲",
               "mount://2/video/影库/无名甲/A.mkv",
               height=1080, size=100, bitrate=1_000_000,
               duration_ticks=36_000_000_000, container="mkv")
    _item(db, lib_film, "movie", "无名片乙",
          "mount://2/video/影库/无名乙/B.mkv",
          height=1080, size=100, bitrate=1_000_000,
          duration_ticks=36_000_000_000, container="mkv")

    # ---- 同目录同主文件名兄弟两行（无 tmdb）：详情 Versions 老口径
    s1 = _item(db, lib_film, "movie", "兄弟片",
               "mount://2/video/影库/兄弟片/Pic - 低码.mkv",
               height=720, size=100, bitrate=1_000_000,
               duration_ticks=36_000_000_000, container="mkv")
    s2 = _item(db, lib_film, "movie", "兄弟片",
               "mount://2/video/影库/兄弟片/Pic - 高码.mkv",
               height=1080, size=200, bitrate=8_000_000,
               duration_ticks=36_000_000_000, container="mkv")

    db.commit()
    return SimpleNamespace(
        db=db, user=user, eps=eps, null_eps=null_eps, lone_ep=lone_ep,
        movies=(m1, m2, m3, m4), no_tmdb=n1, siblings=(s1, s2))


def _request():
    scope = {"type": "http", "method": "GET",
             "path": "/Items/x/PlaybackInfo",
             "raw_path": b"/Items/x/PlaybackInfo",
             "query_string": b"", "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _call_pi(monkeypatch, scene, item):
    monkeypatch.setattr(api, "ensure_playback_allowed",
                        lambda db_, u_: None)
    monkeypatch.setattr(api, "_library_scope", lambda db_, u_: None)
    monkeypatch.setattr(
        pool_gate, "ensure_playback_allowed_for_item",
        lambda db_, u_, it, request=None: pool_gate.PoolDecision(
            allowed=True, kind="not_applicable"))
    monkeypatch.setattr(probe_worker, "maybe_enqueue",
                        lambda db_, it: None)
    monkeypatch.setattr(delivery_plan, "spawn_ensure_plan",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        stream_accel, "rewrite_playback_urls_unified",
        lambda db_, s, base: (s, None))
    monkeypatch.setattr(_tv, "resolve_play_file_id",
                        lambda db_, it, u: ("", None))
    return asyncio.run(api.playback_info(
        item.guid, _request(), user_id="", user=scene.user, db=scene.db))


# ---------------------------------------------------------------------------
# PlaybackInfo：多副本多源
# ---------------------------------------------------------------------------

def test_pi_episode_twelve_copies_all_sources(scene, monkeypatch):
    rep = scene.eps[0]
    resp = _call_pi(monkeypatch, scene, rep)
    sources = resp["MediaSources"]
    assert len(sources) == 12
    # 请求条目本身恒为第一条（默认源），Id 与详情 DTO 同值（guid）。
    assert sources[0]["Id"] == rep.guid
    # 每副本一条：Id 集合恰为 12 个副本 guid，互不重复。
    assert {s["Id"] for s in sources} == {e.guid for e in scene.eps}
    by_id = {e.guid: e for e in scene.eps}
    for s in sources:
        row = by_id[s["Id"]]
        # 取流地址按副本自身签发：路径与 MediaSourceId 都是副本 guid。
        assert f"/Videos/{row.guid}/stream" in s["DirectStreamUrl"]
        assert f"MediaSourceId={row.guid}" in s["DirectStreamUrl"]
        assert s["Size"] == row.size
    # 副本源 Name 带版本区分标签（与详情 Versions 的 _version_label 同口径）。
    copy_names = [s["Name"] for s in sources[1:]]
    assert all(" · " in n for n in copy_names)
    assert any(n.endswith("2160p-3") for n in copy_names)


def test_pi_movie_cross_library_sources(scene, monkeypatch):
    m1, m2, m3, m4 = scene.movies
    resp = _call_pi(monkeypatch, scene, m1)
    sources = resp["MediaSources"]
    # 跨库 3 活行 + 1 合并行 = 4 源，请求条目第一。
    assert len(sources) == 4
    assert sources[0]["Id"] == m1.guid
    assert {s["Id"] for s in sources} == {m.guid for m in scene.movies}
    by_id = {m.guid: m for m in scene.movies}
    for s in sources:
        assert f"MediaSourceId={s['Id']}" in s["DirectStreamUrl"]
        assert s["Size"] == by_id[s["Id"]].size


def test_pi_merged_movie_row_request_gets_group(scene, monkeypatch):
    m1, m2, m3, m4 = scene.movies
    resp = _call_pi(monkeypatch, scene, m4)
    sources = resp["MediaSources"]
    assert len(sources) == 4
    assert sources[0]["Id"] == m4.guid
    assert {s["Id"] for s in sources} == {m.guid for m in scene.movies}


def test_pi_episode_null_numbers_no_group(scene, monkeypatch):
    resp = _call_pi(monkeypatch, scene, scene.null_eps[0])
    # 集号为空的堆叠行绝不归组：仍是单源。
    assert len(resp["MediaSources"]) == 1
    assert resp["MediaSources"][0]["Id"] == scene.null_eps[0].guid


def test_pi_movie_without_tmdb_no_group(scene, monkeypatch):
    resp = _call_pi(monkeypatch, scene, scene.no_tmdb)
    assert len(resp["MediaSources"]) == 1


def test_pi_single_episode_unchanged(scene, monkeypatch):
    resp = _call_pi(monkeypatch, scene, scene.lone_ep)
    sources = resp["MediaSources"]
    assert len(sources) == 1
    assert sources[0]["Id"] == scene.lone_ep.guid
    # 无副本时 Name 逐字不变（不加版本后缀）。
    assert sources[0]["Name"] == scene.lone_ep.name


# ---------------------------------------------------------------------------
# 分组函数口径
# ---------------------------------------------------------------------------

def test_group_items_movie_includes_merged_and_cross_library(scene):
    m1, m2, m3, m4 = scene.movies
    got = api._version_group_items(scene.db, m2)
    assert {g.id for g in got} == {m.id for m in scene.movies}


def test_group_items_episode_null_anchor_single(scene):
    got = api._version_group_items(scene.db, scene.null_eps[0])
    assert [g.id for g in got] == [scene.null_eps[0].id]


def test_group_items_episode_merged_copy_anchor(scene):
    got = api._version_group_items(scene.db, scene.eps[5])
    assert {g.id for g in got} == {e.id for e in scene.eps}


# ---------------------------------------------------------------------------
# 详情 DTO Versions
# ---------------------------------------------------------------------------

def test_dto_episode_versions(scene):
    rep = scene.eps[0]
    dto = api._item_dto(rep, "http://test", scene.user.id, scene.db,
                        full=True)
    versions = dto["Versions"]
    assert len(versions) == 12
    assert {v["Id"] for v in versions} == {e.guid for e in scene.eps}
    primary = [v for v in versions if v["IsPrimary"]]
    assert len(primary) == 1 and primary[0]["Id"] == rep.guid
    by_id = {e.guid: e for e in scene.eps}
    for v in versions:
        assert v["Size"] == by_id[v["Id"]].size
        assert v["Height"] == by_id[v["Id"]].height


def test_dto_movie_cross_library_versions(scene):
    m1, m2, m3, m4 = scene.movies
    dto = api._item_dto(m2, "http://test", scene.user.id, scene.db,
                        full=True)
    versions = dto["Versions"]
    assert len(versions) == 4
    assert {v["Id"] for v in versions} == {m.guid for m in scene.movies}
    # 打开合并行详情：主记录解析到 m1，IsPrimary 标 m1。
    dto4 = api._item_dto(m4, "http://test", scene.user.id, scene.db,
                         full=True)
    assert len(dto4["Versions"]) == 4
    primary4 = [v for v in dto4["Versions"] if v["IsPrimary"]]
    assert len(primary4) == 1 and primary4[0]["Id"] == m1.guid


def test_dto_episode_no_copies_versions_empty(scene):
    dto = api._item_dto(scene.lone_ep, "http://test", scene.user.id,
                        scene.db, full=True)
    assert dto["Versions"] == []


def test_dto_movie_sibling_versions_preserved(scene):
    s1, s2 = scene.siblings
    dto = api._item_dto(s1, "http://test", scene.user.id, scene.db,
                        full=True)
    assert {v["Id"] for v in dto["Versions"]} == {s1.guid, s2.guid}
