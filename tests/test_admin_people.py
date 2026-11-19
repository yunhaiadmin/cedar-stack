# -*- coding: utf-8 -*-
"""人物条目管理管理端（二期部分缺）：/api/admin/people 列表 / 改名 / 合并。

人物口径：emby_people 是按条目落的 credits 行，人物 = 按规范键聚合
（tmdb:<id> 或 name:<归一化姓名>）。覆盖：
1. 列表聚合：分组计数（关联数 / 作品数）精确、搜索、分页、排序。
2. 改名：只改本组 credits 行的 name，其余字段不动；AdminLog 留痕；
   指向旧键的人员收藏同步改指新键；与另一人物同名 409；同名幂等。
3. 合并对账：迁移数 + 去重数 + 合并后关联数 / 作品数逐项断言；
   重复条目目标行优先（缺图 / 缺角色时从源行补入）；源人物消灭；
   收藏改指（含同用户已收藏目标时删旧行）；预览只读不落库。
4. 鉴权：无凭证 401、非管理员 403。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-people-key-32chars-pad")

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin_people
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


@pytest.fixture()
def admin(db):
    return _user(db, staff=True)


def _item(db, name):
    item = em.MediaItem(
        guid=uuid.uuid4().hex[:32],
        library_id=1,
        item_type="movie",
        name=name,
        production_year=2021,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _credit(db, item, name, role=None, image=None, tmdb=None):
    row = em.EmbyPerson(
        item_id=item.id, name=name, role=role, image=image,
        sort_order=0, person_tmdb_id=tmdb,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _favorite(db, user, key, name, image=None, tmdb=None):
    fav = em.UserPersonFavorite(
        user_id=user.id, person_key=key, person_tmdb_id=tmdb,
        name=name, image=image,
    )
    db.add(fav)
    db.commit()
    db.refresh(fav)
    return fav


def _client(db, admin_user=None, admin_error=None):
    app = FastAPI()
    app.include_router(admin_people.admin_people_router)
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


def _logs(db, action):
    return (db.query(models.AdminLog)
            .filter(models.AdminLog.action == action).all())


def _seed_cast(db):
    """张三（tmdb 111，作品 1/2）、李四（无 id，作品 2/3）、王五（无 id，作品 1）。"""
    m1, m2, m3 = _item(db, "片一"), _item(db, "片二"), _item(db, "片三")
    _credit(db, m1, "张三", role="甲", image="http://img/z1.jpg", tmdb="111")
    _credit(db, m2, "张三", role="乙", image=None, tmdb="111")
    _credit(db, m2, "李四", role="丙", image="http://img/l2.jpg")
    _credit(db, m3, "李四", role="丁")
    _credit(db, m1, "王五", role="戊")
    return m1, m2, m3


# ==================== 列表 ====================

def test_list_aggregates_by_person_key(db, admin):
    _seed_cast(db)
    res = _client(db, admin).get("/api/admin/people")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    by_key = {row["key"]: row for row in body["items"]}
    assert by_key["tmdb:111"]["name"] == "张三"
    assert by_key["tmdb:111"]["tmdb_id"] == "111"
    assert by_key["tmdb:111"]["credits_count"] == 2
    assert by_key["tmdb:111"]["works_count"] == 2
    assert by_key["tmdb:111"]["image"] == "http://img/z1.jpg"
    assert by_key["name:李四"]["credits_count"] == 2
    assert by_key["name:李四"]["works_count"] == 2
    assert by_key["name:李四"]["tmdb_id"] is None
    assert by_key["name:王五"]["works_count"] == 1
    # 作品数多者靠前（2 部的在 1 部的前面）
    keys = [row["key"] for row in body["items"]]
    assert keys.index("name:王五") == 2


def test_list_search_and_pagination(db, admin):
    _seed_cast(db)
    client = _client(db, admin)
    res = client.get("/api/admin/people", params={"q": "李"})
    assert [r["key"] for r in res.json()["items"]] == ["name:李四"]
    res = client.get("/api/admin/people", params={"q": "111"})
    assert [r["key"] for r in res.json()["items"]] == ["tmdb:111"]
    res = client.get("/api/admin/people", params={"limit": 2, "offset": 0})
    assert res.json()["total"] == 3
    assert len(res.json()["items"]) == 2
    res = client.get("/api/admin/people", params={"limit": 2, "offset": 2})
    assert len(res.json()["items"]) == 1


# ==================== 改名 ====================

def test_rename_name_group_moves_favorites_and_logs(db, admin):
    _seed_cast(db)
    fan = _user(db)
    _favorite(db, fan, "name:李四", "李四", image="http://img/l2.jpg")
    res = _client(db, admin).post(
        "/api/admin/people/rename",
        json={"key": "name:李四", "new_name": "李四强"})
    assert res.status_code == 200
    body = res.json()
    assert body["renamed"] is True
    assert body["key"] == "name:李四强"
    assert body["favorites_migrated"] == 1

    rows = (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.name == "李四强").all())
    assert len(rows) == 2
    # 只改名：role / image / tmdb 绑定一律不动
    by_role = {r.role: r for r in rows}
    assert by_role["丙"].image == "http://img/l2.jpg"
    assert by_role["丙"].person_tmdb_id is None
    assert not db.query(em.EmbyPerson).filter(
        em.EmbyPerson.name == "李四").count()

    fav = db.query(em.UserPersonFavorite).filter(
        em.UserPersonFavorite.user_id == fan.id).one()
    assert fav.person_key == "name:李四强"
    assert fav.name == "李四强"

    logs = _logs(db, "person_rename")
    assert len(logs) == 1
    assert logs[0].admin_user_id == admin.id
    assert logs[0].details["old_name"] == "李四"
    assert logs[0].details["new_name"] == "李四强"
    assert logs[0].details["credits_count"] == 2


def test_rename_tmdb_group_keeps_key_and_upgrades_favorites(db, admin):
    _seed_cast(db)
    fan = _user(db)
    _favorite(db, fan, "name:张三", "张三")  # 旧姓名键收藏
    res = _client(db, admin).post(
        "/api/admin/people/rename",
        json={"key": "tmdb:111", "new_name": "张三丰"})
    assert res.status_code == 200
    body = res.json()
    assert body["key"] == "tmdb:111"  # 有 TMDB id 时规范键不随名变
    rows = (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.person_tmdb_id == "111").all())
    assert {r.name for r in rows} == {"张三丰"}
    fav = db.query(em.UserPersonFavorite).filter(
        em.UserPersonFavorite.user_id == fan.id).one()
    assert fav.person_key == "tmdb:111"  # 姓名键收藏升级为规范键
    assert fav.name == "张三丰"


def test_rename_conflict_with_other_person_409(db, admin):
    _seed_cast(db)
    res = _client(db, admin).post(
        "/api/admin/people/rename",
        json={"key": "name:李四", "new_name": "张三"})
    assert res.status_code == 409
    # 什么都没改、没落日志
    assert db.query(em.EmbyPerson).filter(
        em.EmbyPerson.name == "李四").count() == 2
    assert _logs(db, "person_rename") == []


def test_rename_same_name_is_idempotent(db, admin):
    _seed_cast(db)
    res = _client(db, admin).post(
        "/api/admin/people/rename",
        json={"key": "name:李四", "new_name": " 李四 "})
    assert res.status_code == 200
    assert res.json()["renamed"] is False
    assert _logs(db, "person_rename") == []


def test_rename_validation(db, admin):
    _seed_cast(db)
    client = _client(db, admin)
    assert client.post("/api/admin/people/rename", json={
        "key": "name:不存在", "new_name": "新人"}).status_code == 404
    assert client.post("/api/admin/people/rename", json={
        "key": "bogus", "new_name": "新人"}).status_code == 400
    assert client.post("/api/admin/people/rename", json={
        "key": "name:李四", "new_name": "  "}).status_code == 400


# ==================== 合并 ====================

def test_merge_preview_is_readonly_and_counts(db, admin):
    _seed_cast(db)
    fan = _user(db)
    _favorite(db, fan, "name:李四", "李四")
    res = _client(db, admin).get(
        "/api/admin/people/merge-preview",
        params={"source_key": "name:李四", "target_key": "tmdb:111"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"]["credits_count"] == 2
    assert body["target"]["credits_count"] == 2
    assert body["will_migrate"] == 1        # 片三的关联改指
    assert body["will_drop_duplicates"] == 1  # 片二与目标重复
    assert body["favorites_to_migrate"] == 1
    assert body["result"] == {
        "key": "tmdb:111", "credits_count": 3, "works_count": 3}
    # 只读：行数与日志都不变
    assert db.query(em.EmbyPerson).count() == 5
    assert _logs(db, "person_merge") == []


def test_merge_name_group_into_tmdb_group_reconciles(db, admin):
    m1, m2, m3 = _seed_cast(db)
    fan = _user(db)
    _favorite(db, fan, "name:李四", "李四", image="http://img/l2.jpg")
    res = _client(db, admin).post(
        "/api/admin/people/merge",
        json={"source_key": "name:李四", "target_key": "tmdb:111"})
    assert res.status_code == 200
    body = res.json()
    assert body["migrated"] == 1
    assert body["duplicates_dropped"] == 1
    assert body["favorites_migrated"] == 1
    assert body["credits_count"] == 3  # 目标原 2 + 迁移 1
    assert body["works_count"] == 3    # {片一, 片二, 片三} 并集

    # 源人物消灭：李四的行一行不剩
    assert not db.query(em.EmbyPerson).filter(
        em.EmbyPerson.name == "李四").count()
    # 对账：目标组关联数 = 原 2 + 迁移 1；作品为三部并集
    rows = (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.person_tmdb_id == "111").all())
    assert len(rows) == 3
    assert {r.item_id for r in rows} == {m1.id, m2.id, m3.id}
    assert {r.name for r in rows} == {"张三"}
    # 重复条目（片二）目标行优先，但缺的 image/role 从源行补入
    dup = next(r for r in rows if r.item_id == m2.id)
    assert dup.role == "乙"               # 目标行原角色保留
    assert dup.image == "http://img/l2.jpg"  # 目标行缺图 → 源行图补入

    fav = db.query(em.UserPersonFavorite).filter(
        em.UserPersonFavorite.user_id == fan.id).one()
    assert fav.person_key == "tmdb:111"
    assert fav.name == "张三"
    assert fav.person_tmdb_id == "111"

    logs = _logs(db, "person_merge")
    assert len(logs) == 1
    assert logs[0].details["migrated"] == 1
    assert logs[0].details["duplicates_dropped"] == 1
    assert logs[0].details["target_credits_before"] == 2
    assert logs[0].details["target_credits_after"] == 3


def test_merge_favorite_clash_deletes_source_favorite(db, admin):
    _seed_cast(db)
    fan = _user(db)
    _favorite(db, fan, "name:李四", "李四")
    _favorite(db, fan, "tmdb:111", "张三", tmdb="111")  # 已收藏目标
    res = _client(db, admin).post(
        "/api/admin/people/merge",
        json={"source_key": "name:李四", "target_key": "tmdb:111"})
    assert res.status_code == 200
    favs = (db.query(em.UserPersonFavorite)
            .filter(em.UserPersonFavorite.user_id == fan.id).all())
    assert len(favs) == 1  # 不撞唯一约束、不重复收藏
    assert favs[0].person_key == "tmdb:111"


def test_merge_name_into_name(db, admin):
    m1, m2, m3 = _seed_cast(db)
    res = _client(db, admin).post(
        "/api/admin/people/merge",
        json={"source_key": "name:王五", "target_key": "name:李四"})
    assert res.status_code == 200
    body = res.json()
    assert body["key"] == "name:李四"
    assert body["migrated"] == 1
    assert body["duplicates_dropped"] == 0
    assert body["credits_count"] == 3
    assert body["works_count"] == 3
    rows = (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.name == "李四").all())
    assert {r.item_id for r in rows} == {m1.id, m2.id, m3.id}


def test_merge_validation(db, admin):
    _seed_cast(db)
    client = _client(db, admin)
    assert client.post("/api/admin/people/merge", json={
        "source_key": "name:李四",
        "target_key": "name:李四"}).status_code == 400
    assert client.post("/api/admin/people/merge", json={
        "source_key": "name:不存在",
        "target_key": "tmdb:111"}).status_code == 404
    assert client.post("/api/admin/people/merge", json={
        "source_key": "name:李四",
        "target_key": "tmdb:999"}).status_code == 404
    assert client.get("/api/admin/people/merge-preview", params={
        "source_key": "bogus",
        "target_key": "tmdb:111"}).status_code == 400
    # 失败不落库：行数不变、无日志
    assert db.query(em.EmbyPerson).count() == 5
    assert _logs(db, "person_merge") == []


# ==================== 鉴权 ====================

def test_auth_required(db):
    _seed_cast(db)
    assert _client(db, admin_error=401).get(
        "/api/admin/people").status_code == 401
    assert _client(db, admin_error=403).post(
        "/api/admin/people/rename",
        json={"key": "name:李四", "new_name": "x"}).status_code == 403
