# -*- coding: utf-8 -*-
"""用户收藏查看管理端（二期缺口项 6）：GET /api/admin/users/{id}/favorites。

覆盖：
1. 返回该用户的作品收藏（is_favorite 行）与人员收藏，字段齐全；
   未收藏的条目行（is_favorite=False）不入列；别人的收藏不串。
2. 条目已软删时该行仍在、标 item_deleted=true（管理口看标记本身）。
3. 用户不存在 404；无写方法（POST/DELETE 405）。
4. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-user-favorites-key-32ch")

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_user_favorites as fav_admin
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


def _item(db, name, item_type="movie"):
    item = em.MediaItem(
        guid=uuid.uuid4().hex[:32],
        library_id=1,
        item_type=item_type,
        name=name,
        production_year=2021,
        community_rating=8.5,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture()
def admin(db):
    return _user(db, staff=True)


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(fav_admin.admin_user_favorites_router)
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


def test_lists_items_and_persons(db, admin):
    alice = _user(db)
    bob = _user(db)
    m1 = _item(db, "海上钢琴师")
    s1 = _item(db, "追风者", item_type="series")
    db.add(em.UserMediaData(user_id=alice.id, item_id=m1.id,
                            is_favorite=True))
    db.add(em.UserMediaData(user_id=alice.id, item_id=s1.id,
                            is_favorite=False))
    db.add(em.UserMediaData(user_id=bob.id, item_id=s1.id,
                            is_favorite=True))
    db.add(em.UserPersonFavorite(
        user_id=alice.id, person_key="tmdb:123", person_tmdb_id="123",
        name="周迅", image="https://img.example/zx.jpg"))
    db.commit()

    client = _client(db, admin_user=admin)
    res = client.get(f"/api/admin/users/{alice.id}/favorites")
    assert res.status_code == 200
    body = res.json()
    assert body["user"] == {"id": alice.id, "username": alice.username}
    assert body["counts"] == {"items": 1, "persons": 1}
    row = body["items"][0]
    assert row["item_id"] == m1.id
    assert row["name"] == "海上钢琴师"
    assert row["type"] == "movie"
    assert row["year"] == 2021
    assert row["rating"] == 8.5
    assert row["item_deleted"] is False
    assert body["persons"][0]["name"] == "周迅"
    assert body["persons"][0]["person_tmdb_id"] == "123"


def test_soft_deleted_item_marked_not_hidden(db, admin):
    alice = _user(db)
    alice_id = alice.id
    m1 = _item(db, "已下架的片")
    m1_id = m1.id
    db.add(em.UserMediaData(user_id=alice_id, item_id=m1_id,
                            is_favorite=True))
    m1.deleted_at = datetime.now()
    db.commit()
    db.expunge_all()

    client = _client(db, admin_user=admin)
    body = client.get(f"/api/admin/users/{alice_id}/favorites").json()
    assert body["counts"]["items"] == 1
    assert body["items"][0]["item_id"] == m1_id
    assert body["items"][0]["item_deleted"] is True


def test_user_missing_404_and_no_write_methods(db, admin):
    client = _client(db, admin_user=admin)
    assert client.get("/api/admin/users/999999/favorites").status_code == 404
    alice = _user(db)
    assert client.post(
        f"/api/admin/users/{alice.id}/favorites").status_code == 405
    assert client.delete(
        f"/api/admin/users/{alice.id}/favorites").status_code == 405


def test_auth_required(db):
    assert _client(db, admin_error=401).get(
        "/api/admin/users/1/favorites").status_code == 401
    assert _client(db, admin_error=403).get(
        "/api/admin/users/1/favorites").status_code == 403
