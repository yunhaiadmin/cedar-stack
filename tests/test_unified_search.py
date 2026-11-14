# -*- coding: utf-8 -*-
"""统一搜索（本地版）测试。

上游全清后统一搜索只剩本站一条腿：本地候选按作品归一键去重成形。
只测纯合并 + 路由成形，不发真实 HTTP、不写库。本地 Python 3.11
口径，全量以 GitHub CI 为准。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import unified_search as us


def _local(key, name="肖申克的救赎", year=1994):
    return us.LocalSearchEntry(aggregate_key=key, name=name,
                               item_type="movie", production_year=year)


def test_local_dedup_same_key_single_row():
    merged = us.merge_search_results(
        [_local("tmdb:278"), _local("tmdb:278"),
         _local("tmdb:329", name="阿甘正传")], "肖申克")
    # 同键只留一行（先到者），异键保留；来源只有本地一条腿。
    assert [r.aggregate_key for r in merged.results] == ["tmdb:278", "tmdb:329"]
    assert merged.results[0].origin == "local"
    assert merged.results[0].source == "local"
    assert len(merged.sources) == 1
    assert merged.sources[0].source_id == "local"
    assert merged.sources[0].status == "ok"
    assert merged.sources[0].count == 2


def test_merge_skips_entries_without_key():
    merged = us.merge_search_results(
        [_local(""), _local("tmdb:278")], "肖申克")
    assert [r.aggregate_key for r in merged.results] == ["tmdb:278"]


def test_empty_query_returns_nothing_not_whole_catalog():
    merged = us.merge_search_results([_local("tmdb:278")], "   ")
    assert merged.results == ()
    assert merged.query == ""


def test_response_body_shape():
    merged = us.merge_search_results([_local("tmdb:278")], "肖申克")
    body = us.to_response_body(merged, limit=10)
    assert body["total"] == 1
    assert body["query"] == "肖申克"
    assert body["results"][0]["origin"] == "local"
    assert body["sources"] == [
        {"source_id": "local", "status": "ok", "count": 1, "error": None}]


def test_switch_off_by_default(monkeypatch):
    monkeypatch.delenv("UNIFIED_SEARCH_ENABLED", raising=False)
    assert us.unified_search_enabled() is False
    monkeypatch.setenv("UNIFIED_SEARCH_ENABLED", "1")
    assert us.unified_search_enabled() is True


def test_local_key_prefers_production_year_over_scan_year():
    # 本地键年份口径：production_year 优先，scan_year 只作回落，
    # 保证同作品在各入口算出同一标题键。
    key = us.local_aggregate_key(
        name="无身份片", item_type="movie",
        production_year=2021, scan_year=2020,
        tmdb_id=None, imdb_id=None,
        season_number=None, episode_number=None)
    assert key == "title:无身份片:2021"


def test_local_key_falls_back_to_scan_year_when_no_production_year():
    key = us.local_aggregate_key(
        name="无身份片", item_type="movie",
        production_year=None, scan_year=2020,
        tmdb_id=None, imdb_id=None,
        season_number=None, episode_number=None)
    assert key == "title:无身份片:2020"


def test_local_key_prefers_tmdb_then_imdb():
    key = us.local_aggregate_key(
        name="肖申克的救赎", item_type="movie",
        production_year=1994, scan_year=None,
        tmdb_id="278", imdb_id="tt0111161",
        season_number=None, episode_number=None)
    assert key == "tmdb:278"
    key = us.local_aggregate_key(
        name="肖申克的救赎", item_type="movie",
        production_year=1994, scan_year=None,
        tmdb_id=None, imdb_id="tt0111161",
        season_number=None, episode_number=None)
    assert key == "imdb:tt0111161"


def test_route_returns_local_results_only(monkeypatch):
    # 路由层端到端：开关开时只出本地结果，sources 只有 local 一条。
    monkeypatch.setenv("UNIFIED_SEARCH_ENABLED", "1")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    from sqlalchemy.pool import StaticPool as _SP
    from backend.emby_server import models as _em
    from backend.emby_server import search_api as _api
    from backend.emby_server.auth import get_emby_user as _geu
    from backend.database import get_db as _gdb
    from backend import library_scope as _ls

    engine = _ce("sqlite://", connect_args={"check_same_thread": False},
                 poolclass=_SP)
    _em.MediaItem.__table__.create(engine, checkfirst=True)
    db = _sm(bind=engine)()
    db.add(_em.MediaItem(id=1, guid="00000000000000000000000000000001",
                         library_id=1, item_type="movie", name="肖申克的救赎",
                         production_year=1994, sort_name="肖申克的救赎",
                         tmdb_id="278", is_hidden=False))
    db.commit()
    monkeypatch.setattr(_ls, "effective_ids_safe", lambda d, u: {1})
    app = FastAPI()
    app.include_router(_api.search_router)
    app.dependency_overrides[_geu] = lambda: type(
        "U", (), {"id": 1, "is_staff": False})()
    app.dependency_overrides[_gdb] = lambda: db
    client = TestClient(app)
    r = client.get("/emby/UnifiedSearch", params={"SearchTerm": "肖申克"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["results"][0]["aggregate_key"] == "tmdb:278"
    assert [s["source_id"] for s in body["sources"]] == ["local"]
