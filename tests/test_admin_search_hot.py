# -*- coding: utf-8 -*-
"""热搜管理口（二期缺口「搜索排序/热搜管理」）：/api/admin/search/*。

覆盖：
- 鉴权：无凭证 401、非管理员 403（读口与写口）。
- 生效榜合并：置顶把低频词顶到第一、无统计的置顶词以 0 次上榜、
  下榜词从用户口消失但统计仍在、取消干预恢复自然序、置顶重排生效。
- 删除统计：词与统计行一起消失、置顶行随删、下榜行保留（再搜仍不上榜）。
- 用户口形状不变：GET /search/hot 返回项仍只有 {term, count}。
- 校验：非法 mode / 空词 400；重排集合不符 400；删不存在的词 404。
- 写操作全部落 AdminLog。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-search-admin-key-32chars-padxxx")

import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_search, media_library
from backend.api.admin_core import get_current_admin
from backend.api.user import get_current_user
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import search_stats


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    import backend.emby_server.models  # noqa: F401 — 注册 EA 侧表到同一 Base
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
        username="search_admin_" + uuid.uuid4().hex[:8], password_hash="x",
        is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def viewer(db):
    user = models.WebUser(
        username="search_viewer_" + uuid.uuid4().hex[:8], password_hash="x",
        is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db, admin_user=None, admin_error=None, user=None):
    app = FastAPI()
    app.include_router(admin_search.admin_search_router)
    app.include_router(media_library.router)
    app.dependency_overrides[get_db] = lambda: db
    if admin_error is not None:
        def _raise():
            raise HTTPException(
                status_code=admin_error,
                detail="forbidden" if admin_error == 403 else "no creds")
        app.dependency_overrides[get_current_admin] = _raise
    elif admin_user is not None:
        app.dependency_overrides[get_current_admin] = lambda: admin_user
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _seed_stat(db, term, days_ago, count):
    day = date.today() - timedelta(days=days_ago)
    db.add(em.SearchTermStat(
        term_key=search_stats.normalize_search_term(term),
        display_term=term,
        day=day,
        count=count,
        last_seen_at=datetime.combine(day, datetime.min.time()),
    ))
    db.commit()


def _user_hot(client):
    r = client.get("/api/user/media-library/search/hot")
    assert r.status_code == 200, r.text
    return r.json()["items"]


_ADMIN = "/api/admin/search"


# ------------------------------------------------------------ 鉴权

def test_requires_admin(db, viewer):
    c = _client(db, admin_error=401, user=viewer)
    assert c.get(f"{_ADMIN}/ranking").status_code == 401
    assert c.get(f"{_ADMIN}/terms").status_code == 401
    assert c.post(f"{_ADMIN}/overrides",
                  json={"term": "x", "mode": "pin"}).status_code == 401
    c403 = _client(db, admin_error=403, user=viewer)
    assert c403.get(f"{_ADMIN}/ranking").status_code == 403
    assert c403.delete(f"{_ADMIN}/terms",
                       params={"term": "x"}).status_code == 403


# ------------------------------------------------------------ 生效榜形状

def test_user_hot_shape_unchanged(db, staff, viewer):
    _seed_stat(db, "热门剧", 0, 7)
    _seed_stat(db, "次热片", 1, 5)
    c = _client(db, admin_user=staff, user=viewer)
    items = _user_hot(c)
    assert [i["term"] for i in items] == ["热门剧", "次热片"]
    for item in items:
        assert set(item.keys()) == {"term", "count"}
    # 管理预览口同序、多带关联键
    r = c.get(f"{_ADMIN}/ranking")
    assert r.status_code == 200
    preview = r.json()["items"]
    assert [i["term"] for i in preview] == ["热门剧", "次热片"]
    assert preview[0]["term_key"] == "热门剧"
    assert preview[0]["pinned"] is False


# ------------------------------------------------------------ 置顶

def test_pin_promotes_low_term(db, staff, viewer):
    _seed_stat(db, "高频片", 0, 50)
    _seed_stat(db, "低频片", 0, 1)
    c = _client(db, admin_user=staff, user=viewer)
    r = c.post(f"{_ADMIN}/overrides", json={"term": "低频片", "mode": "pin"})
    assert r.status_code == 200, r.text
    items = _user_hot(c)
    assert [i["term"] for i in items] == ["低频片", "高频片"]
    assert items[0]["count"] == 1


def test_pin_manual_term_without_stats(db, staff, viewer):
    _seed_stat(db, "高频片", 0, 50)
    c = _client(db, admin_user=staff, user=viewer)
    r = c.post(f"{_ADMIN}/overrides",
               json={"term": "  从没搜过的片 ", "mode": "pin"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["term_key"] == "从没搜过的片"
    items = _user_hot(c)
    assert items[0] == {"term": "从没搜过的片", "count": 0}
    assert items[1]["term"] == "高频片"


def test_reorder_pins(db, staff, viewer):
    c = _client(db, admin_user=staff, user=viewer)
    c.post(f"{_ADMIN}/overrides", json={"term": "甲片", "mode": "pin"})
    c.post(f"{_ADMIN}/overrides", json={"term": "乙片", "mode": "pin"})
    assert [i["term"] for i in _user_hot(c)] == ["甲片", "乙片"]
    r = c.post(f"{_ADMIN}/overrides/reorder",
               json={"term_keys": ["乙片", "甲片"]})
    assert r.status_code == 200, r.text
    assert [i["term"] for i in _user_hot(c)] == ["乙片", "甲片"]
    # 集合不符（少一个）→ 400，不许用过期顺序覆盖
    r = c.post(f"{_ADMIN}/overrides/reorder", json={"term_keys": ["乙片"]})
    assert r.status_code == 400


def test_remove_override_restores_natural_order(db, staff, viewer):
    _seed_stat(db, "高频片", 0, 50)
    _seed_stat(db, "低频片", 0, 1)
    c = _client(db, admin_user=staff, user=viewer)
    c.post(f"{_ADMIN}/overrides", json={"term": "低频片", "mode": "pin"})
    assert _user_hot(c)[0]["term"] == "低频片"
    r = c.delete(f"{_ADMIN}/overrides", params={"term": "低频片"})
    assert r.status_code == 200, r.text
    assert [i["term"] for i in _user_hot(c)] == ["高频片", "低频片"]
    # 再取消一次 → 404
    assert c.delete(f"{_ADMIN}/overrides",
                    params={"term": "低频片"}).status_code == 404


# ------------------------------------------------------------ 下榜与删除

def test_block_hides_term_but_stats_remain(db, staff, viewer):
    _seed_stat(db, "违规词", 0, 99)
    _seed_stat(db, "正常片", 0, 3)
    c = _client(db, admin_user=staff, user=viewer)
    r = c.post(f"{_ADMIN}/overrides", json={"term": "违规词", "mode": "block"})
    assert r.status_code == 200, r.text
    assert [i["term"] for i in _user_hot(c)] == ["正常片"]
    # 统计仍在，词表里能看到且标了下榜
    assert db.query(em.SearchTermStat).filter_by(term_key="违规词").count() == 1
    terms = c.get(f"{_ADMIN}/terms").json()["items"]
    row = next(t for t in terms if t["term_key"] == "违规词")
    assert row["override"] == "block"
    assert row["count"] == 99


def test_delete_stats_removes_term_and_pin(db, staff, viewer):
    _seed_stat(db, "待删片", 0, 10)
    _seed_stat(db, "待删片", 1, 5)
    c = _client(db, admin_user=staff, user=viewer)
    c.post(f"{_ADMIN}/overrides", json={"term": "待删片", "mode": "pin"})
    r = c.delete(f"{_ADMIN}/terms", params={"term": "待删片"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_rows"] == 2
    assert body["removed_pin"] is True
    assert _user_hot(c) == []
    assert db.query(em.SearchTermStat).count() == 0
    assert db.query(em.SearchTermOverride).count() == 0
    # 再删 → 404
    assert c.delete(f"{_ADMIN}/terms",
                    params={"term": "待删片"}).status_code == 404


def test_delete_stats_keeps_block(db, staff, viewer):
    _seed_stat(db, "屏蔽词", 0, 8)
    c = _client(db, admin_user=staff, user=viewer)
    c.post(f"{_ADMIN}/overrides", json={"term": "屏蔽词", "mode": "block"})
    r = c.delete(f"{_ADMIN}/terms", params={"term": "屏蔽词"})
    assert r.status_code == 200
    assert r.json()["removed_pin"] is False
    # 下榜行保留：词重新被搜、重新累计后仍不上榜
    assert db.query(em.SearchTermOverride).count() == 1
    assert search_stats.record_search_term(db, "屏蔽词") is True
    assert _user_hot(c) == []


# ------------------------------------------------------------ 词表窗口与校验

def test_terms_window(db, staff, viewer):
    _seed_stat(db, "近词", 1, 4)
    _seed_stat(db, "远古词", 20, 99)
    c = _client(db, admin_user=staff, user=viewer)
    week = c.get(f"{_ADMIN}/terms", params={"days": 7}).json()
    assert [t["term"] for t in week["items"]] == ["近词"]
    month = c.get(f"{_ADMIN}/terms", params={"days": 30}).json()
    assert [t["term"] for t in month["items"]] == ["远古词", "近词"]
    assert c.get(f"{_ADMIN}/terms",
                 params={"days": 999}).status_code == 400


def test_set_override_validation(db, staff, viewer):
    c = _client(db, admin_user=staff, user=viewer)
    assert c.post(f"{_ADMIN}/overrides",
                  json={"term": "x", "mode": "top"}).status_code == 400
    assert c.post(f"{_ADMIN}/overrides",
                  json={"term": "   ", "mode": "pin"}).status_code == 400
    # 改模式：先置顶再改下榜，同一词只一行
    c.post(f"{_ADMIN}/overrides", json={"term": "双面词", "mode": "pin"})
    r = c.post(f"{_ADMIN}/overrides", json={"term": "双面词", "mode": "block"})
    assert r.status_code == 200
    assert r.json()["item"]["mode"] == "block"
    assert db.query(em.SearchTermOverride).count() == 1


# ------------------------------------------------------------ 审计

def test_writes_are_audited(db, staff, viewer):
    _seed_stat(db, "审计片", 0, 2)
    c = _client(db, admin_user=staff, user=viewer)
    c.post(f"{_ADMIN}/overrides", json={"term": "审计片", "mode": "pin"})
    c.delete(f"{_ADMIN}/terms", params={"term": "审计片"})
    actions = [row.action for row in db.query(models.AdminLog).all()]
    assert "search_hot_override_set" in actions
    assert "search_hot_stats_delete" in actions
