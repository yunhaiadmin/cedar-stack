"""片库热搜（❌2 闭环）：搜索词聚合计数 + 近 7 天 Top 榜只读口。

口径：
- 搜索时在 Emby Items 口 best-effort 记一笔（只存归一化词与次数、
  不关联用户）；计数失败不许影响搜索本体。
- GET /api/user/media-library/search/hot 回近 7 天 Top 词，
  形状 {"items": [{"term": ..., "count": ...}]}；无数据回空表。
"""

import os
import tempfile
from datetime import date, datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm


import pytest

from backend import models as base_models
from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import search_stats


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （与 test_media_library_endpoints 同口径，防 #1668 同族污染）。"""
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    _hyg_engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = _hyg_saved_engine
        _dbmod.configure_session_local(_hyg_saved_factory)
        if _hyg_saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _hyg_saved_url
        _hyg_engine.dispose()


_TABLES = (em.SearchTermStat, em.MediaItem, em.Library, base_models.WebUser)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        for table in _TABLES:
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        try:
            for table in _TABLES:
                s.query(table).delete()
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()


@pytest.fixture()
def user(db):
    u = base_models.WebUser(username="hotuser", is_staff=True,
                            is_active=True, password_hash="test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: SessionLocal()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def user_client(client, user):
    from backend.api.user import get_current_user
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield client
    finally:
        del app.dependency_overrides[get_current_user]


@pytest.fixture()
def emby_client(client, user):
    from backend.emby_server.auth import get_emby_user
    from backend.main import app

    app.dependency_overrides[get_emby_user] = lambda: user
    try:
        yield client
    finally:
        del app.dependency_overrides[get_emby_user]


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


# ---------- 归一化与记录 ----------

def test_normalize_collapses_and_casefolds():
    assert search_stats.normalize_search_term("  Avatar   2 ") == "avatar 2"
    assert search_stats.normalize_search_term(" 复仇者 联盟 ") == "复仇者 联盟"
    assert search_stats.normalize_search_term("   ") == ""
    assert search_stats.normalize_search_term("") == ""
    assert search_stats.normalize_search_term(None) == ""


def test_record_aggregates_same_normalized_term(db):
    assert search_stats.record_search_term(db, "  Avatar  2 ") is True
    assert search_stats.record_search_term(db, "avatar 2") is True
    rows = db.query(em.SearchTermStat).all()
    assert len(rows) == 1
    assert rows[0].count == 2
    # 展示词跟最近一次的原始写法
    assert rows[0].display_term == "avatar 2"


def test_record_skips_blank_terms(db):
    assert search_stats.record_search_term(db, "   ") is False
    assert search_stats.record_search_term(db, "") is False
    assert db.query(em.SearchTermStat).count() == 0


def test_record_keeps_no_user_link(db, user):
    search_stats.record_search_term(db, "某部电影")
    row = db.query(em.SearchTermStat).one()
    assert not hasattr(row, "user_id")


# ---------- 榜单口 ----------

def test_hot_endpoint_empty_returns_empty_items(user_client):
    r = user_client.get("/api/user/media-library/search/hot")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_hot_endpoint_ranks_last_7_days_only(db, user_client):
    _seed_stat(db, "热门剧", days_ago=0, count=3)
    _seed_stat(db, "热门剧", days_ago=2, count=4)
    _seed_stat(db, "次热片", days_ago=1, count=5)
    _seed_stat(db, "过期词", days_ago=8, count=99)
    r = user_client.get("/api/user/media-library/search/hot")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["term"] for i in items] == ["热门剧", "次热片"]
    assert items[0]["count"] == 7
    assert items[1]["count"] == 5


def test_hot_endpoint_respects_limit(db, user_client):
    for i in range(5):
        _seed_stat(db, f"词{i}", days_ago=0, count=10 - i)
    r = user_client.get("/api/user/media-library/search/hot", params={"limit": 2})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0]["term"] == "词0"


def test_hot_endpoint_requires_login(client):
    r = client.get("/api/user/media-library/search/hot")
    assert r.status_code in (401, 403)


# ---------- 搜索时真计数（Emby Items 口） ----------

def test_items_search_records_term(db, emby_client):
    r = emby_client.get("/emby/Items", params={"SearchTerm": "  测试热片 ", "Recursive": "true"})
    assert r.status_code == 200
    rows = db.query(em.SearchTermStat).all()
    assert len(rows) == 1
    assert rows[0].term_key == "测试热片"
    assert rows[0].count == 1


def test_items_without_search_term_records_nothing(db, emby_client):
    r = emby_client.get("/emby/Items", params={"Recursive": "true"})
    assert r.status_code == 200
    assert db.query(em.SearchTermStat).count() == 0
