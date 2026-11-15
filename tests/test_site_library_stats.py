# -*- coding: utf-8 -*-
"""官网片库数字公开端点测试（GET /api/site/library-stats）。

背景：官网首页片库数据行此前是 2026-11-15 的一次性生产快照写死在
LandingView.vue 里，数字随入库漂移失真。本端点回四个实时数，前端改读
接口、失败回落快照值。

计数口径（与快照定义一致，2026-11-15 已用生产库核对）：
- ORM 可见行：deleted_at IS NULL AND merged_into_id IS NULL
  （排除软删除行与合并副本，psql 裸查会虚高两个数量级）
- works     = item_type IN ('movie', 'series') 的可见行数
- episodes  = item_type = 'episode' 的可见行数
- total_items = 全部类型可见行数（movie+series+season+episode+…）
- libraries = emby_libraries 中 is_enabled 的行数

隔离：最小 FastAPI 应用只挂 site_router，get_db 覆写为内存 SQLite 会话；
模块级缓存每个用例前后重置。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models  # noqa: F401  # 注册全量表到共享 Base
from backend.api import site as site_module
from backend.database import Base, get_db
from backend.emby_server import models as em


def _guid():
    return uuid.uuid4().hex


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    site_module._library_stats_cache = {"at": None, "fail_at": None, "data": None}

    app = FastAPI()
    app.include_router(site_module.site_router)

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        site_module._library_stats_cache = {"at": None, "fail_at": None, "data": None}


def _seed(session):
    """1 启用库 + 1 停用库；可见行 2 电影 + 1 剧集 + 1 季 + 4 集，
    另加 1 软删电影、1 软删剧集、1 合并副本单集（都不应计入）。"""
    lib_on = em.Library(guid=_guid(), name="启用库", collection_type="mixed",
                        is_enabled=True)
    lib_off = em.Library(guid=_guid(), name="停用库", collection_type="movies",
                         is_enabled=False)
    session.add_all([lib_on, lib_off])
    session.flush()

    def mk(item_type, name, **kw):
        it = em.MediaItem(guid=_guid(), library_id=lib_on.id,
                          item_type=item_type, name=name, **kw)
        session.add(it)
        session.flush()
        return it

    mk("movie", "电影甲")
    mk("movie", "电影乙")
    series = mk("series", "剧集甲")
    season = mk("season", "Season 01", series_id=series.id, season_number=1)
    for i in range(1, 4):
        mk("episode", f"剧集甲 S01E0{i}", series_id=series.id,
           parent_id=season.id, season_number=1, episode_number=i)
    # 不可见行：软删除与合并副本
    mk("movie", "已下架电影", deleted_at=datetime(2026, 11, 1, 12, 0, 0))
    mk("series", "已下架剧集", deleted_at=datetime(2026, 11, 1, 12, 0, 0))
    keep = mk("episode", "剧集甲 S01E04", series_id=series.id,
              parent_id=season.id, season_number=1, episode_number=4)
    mk("episode", "合并副本", series_id=series.id, parent_id=season.id,
       season_number=1, episode_number=4, merged_into_id=keep.id)
    session.commit()
    return lib_on


EXPECTED = {"works": 3, "episodes": 4, "total_items": 8, "libraries": 1}
EMPTY = {"works": 0, "episodes": 0, "total_items": 0, "libraries": 0}


def test_counts_follow_orm_visible_scope(client, session):
    _seed(session)
    resp = client.get("/api/site/library-stats")
    assert resp.status_code == 200
    assert resp.json() == EXPECTED


def test_cache_avoids_recount_within_ttl(client, session):
    lib = _seed(session)
    assert client.get("/api/site/library-stats").json() == EXPECTED
    # TTL 内新增可见行，第二次读仍回缓存值（不每次打全库 COUNT）
    session.add(em.MediaItem(guid=_guid(), library_id=lib.id,
                             item_type="movie", name="新入库电影"))
    session.commit()
    assert client.get("/api/site/library-stats").json() == EXPECTED
    # 缓存过期后重数，新行计入
    site_module._library_stats_cache["at"] = 0.0
    refreshed = dict(EXPECTED, works=4, total_items=9)
    assert client.get("/api/site/library-stats").json() == refreshed


def test_failure_returns_zeros_not_500_when_no_cache(client, monkeypatch):
    calls = {"n": 0}

    def _boom(db):
        calls["n"] += 1
        raise RuntimeError("db down")

    monkeypatch.setattr(site_module, "_count_library_stats", _boom)
    resp = client.get("/api/site/library-stats")
    assert resp.status_code == 200
    assert resp.json() == EMPTY
    # 失败负缓存窗口内不重复打 DB
    resp = client.get("/api/site/library-stats")
    assert resp.status_code == 200
    assert resp.json() == EMPTY
    assert calls["n"] == 1


def test_failure_serves_stale_cache(client, session, monkeypatch):
    _seed(session)
    assert client.get("/api/site/library-stats").json() == EXPECTED

    def _boom(db):
        raise RuntimeError("db down")

    monkeypatch.setattr(site_module, "_count_library_stats", _boom)
    # 缓存过期后 DB 故障：回旧值而不是 0 或 500（官网数字宁旧勿空）
    site_module._library_stats_cache["at"] = 0.0
    resp = client.get("/api/site/library-stats")
    assert resp.status_code == 200
    assert resp.json() == EXPECTED
