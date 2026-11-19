"""弹幕内容治理回归测试（管理端列表/删除/禁言 + 发送口真查禁言）。

覆盖口径：
- 治理列表：全站弹幕按时间倒序，条目/用户精确筛、关键词筛
  （正文/用户名/条目名任一命中）、分页 total 正确；
- 删除：硬删落库消失、快照记 AdminLog，不存在的弹幕 404；
- 禁言：POST 建/改（一人一行 upsert）、永久 muted_until=None、
  定时按小时算到期，列表带 active 标记；解禁删行记 AdminLog，
  无禁言行解禁 404；用户不存在 404、时长非法 400；
- 发送口真查：被禁言（永久/未到期）发送回 403 且不落库、文案
  写明永久/到期时间；已到期禁言不拦；未被禁言用户不受影响；
  禁言只拦发送、拉取照常；
- 禁言判定唯一实现 get_active_mute/mute_is_active：固定 now 注入，
  muted_until == now 视为已到期（不禁言）；
- 鉴权：治理口无凭证 401。

基建与 test_danmaku.py 同口径：模块内换绑临时文件库、结束还原。
"""

import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

from backend import models as core  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

_DANMAKU_KEYS = (
    "danmaku_enabled",
    "danmaku_blocked_words",
    "danmaku_min_interval_seconds",
    "danmaku_max_text_length",
)


def _invalidate_config_cache():
    from backend.integrations import store

    store.invalidate(*_DANMAKU_KEYS)


_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """与 test_danmaku 同口径：模块内换绑临时库、结束还原。"""
    saved_engine = _dbmod.engine
    saved_factory = _dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = saved_engine
        _dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        engine.dispose()


_TABLES = (em.DanmakuComment, em.DanmakuMute, em.MediaItem, em.Library,
           core.WebUser, core.SystemConfig, core.AdminLog)


@pytest.fixture()
def db():
    _invalidate_config_cache()
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
            _invalidate_config_cache()


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: SessionLocal()
    try:
        yield TestClient(app, raise_server_exceptions=False), app
    finally:
        app.dependency_overrides.clear()


def _as_user(app, user):
    from backend.api.user import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user


def _as_admin(app, admin):
    from backend.api.admin_core import get_current_admin

    app.dependency_overrides[get_current_admin] = lambda: admin


def _mk_item(db, name):
    import uuid

    lib = em.Library(guid=uuid.uuid4().hex[:32], name="电影库",
                     collection_type="movies", paths="")
    db.add(lib)
    db.flush()
    item = em.MediaItem(
        guid=uuid.uuid4().hex[:32], library_id=lib.id,
        item_type="movie", name=name, sort_name=name, is_hidden=False,
    )
    db.add(item)
    db.flush()
    return item


def _mk_user(db, username, **kw):
    user = core.WebUser(username=username, password_hash="x",
                        is_active=True, **kw)
    db.add(user)
    db.flush()
    return user


def _mk_comment(db, user, item, text, position=10.0):
    row = em.DanmakuComment(user_id=user.id, item_id=item.id,
                            position_seconds=position, text=text)
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def scene(db):
    item_a = _mk_item(db, "电影甲")
    item_b = _mk_item(db, "电影乙")
    alice = _mk_user(db, "alice")
    bob = _mk_user(db, "bob")
    admin = _mk_user(db, "gov_admin", is_staff=True)
    c1 = _mk_comment(db, alice, item_a, "好看", 10.0)
    c2 = _mk_comment(db, bob, item_a, "高能预警", 20.0)
    c3 = _mk_comment(db, alice, item_b, "好看", 30.0)
    db.commit()
    return SimpleNamespace(db=db, item_a=item_a, item_b=item_b,
                           alice=alice, bob=bob, admin=admin,
                           c1=c1, c2=c2, c3=c3)


def _enable(db):
    from backend import config_service

    config_service.save_config(
        db, {"danmaku_enabled": True, "danmaku_min_interval_seconds": 0},
        schema="danmaku")
    db.commit()
    _invalidate_config_cache()


def _audit_rows(db, action):
    return (db.query(core.AdminLog)
            .filter(core.AdminLog.action == action).all())


# ------------------------------------------------------------ 治理列表


def test_comments_list_filters_and_pagination(client, scene):
    c, app = client
    _as_admin(app, scene.admin)

    r = c.get("/api/admin/danmaku/comments")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3
    # created_at 同批落库可能同秒，次序键 id desc 保证确定性
    assert [it["id"] for it in data["items"]] == [
        scene.c3.id, scene.c2.id, scene.c1.id]
    first = data["items"][0]
    assert first["username"] == "alice"
    assert first["item_name"] == "电影乙"
    assert first["text"] == "好看"
    assert first["position_seconds"] == 30.0

    r = c.get("/api/admin/danmaku/comments", params={"keyword": "高能"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == scene.c2.id

    r = c.get("/api/admin/danmaku/comments", params={"keyword": "alice"})
    assert r.json()["total"] == 2

    r = c.get("/api/admin/danmaku/comments", params={"keyword": "电影乙"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == scene.c3.id

    r = c.get("/api/admin/danmaku/comments",
              params={"user_id": scene.bob.id})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == scene.c2.id

    r = c.get("/api/admin/danmaku/comments",
              params={"item_id": scene.item_a.id})
    assert r.json()["total"] == 2

    r = c.get("/api/admin/danmaku/comments",
              params={"limit": 2, "offset": 0})
    assert r.json()["total"] == 3
    assert len(r.json()["items"]) == 2
    r = c.get("/api/admin/danmaku/comments",
              params={"limit": 2, "offset": 2})
    assert len(r.json()["items"]) == 1


def test_comments_require_admin(client, scene):
    c, _app = client
    r = c.get("/api/admin/danmaku/comments")
    assert r.status_code == 401, r.text
    r = c.get("/api/admin/danmaku/mutes")
    assert r.status_code == 401, r.text


# ------------------------------------------------------------ 删除


def test_delete_comment_hard_deletes_and_audits(client, scene):
    c, app = client
    _as_admin(app, scene.admin)
    # 先把 id 抓成纯值：行删除后 scene 里的 ORM 对象已过期，再读
    # 属性会触发刷新并因行不存在而炸（ObjectDeletedError）
    c1_id = scene.c1.id
    admin_id = scene.admin.id

    r = c.delete(f"/api/admin/danmaku/comments/{c1_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "comment_id": c1_id}

    scene.db.expire_all()
    assert scene.db.query(em.DanmakuComment).filter(
        em.DanmakuComment.id == c1_id).first() is None
    assert scene.db.query(em.DanmakuComment).count() == 2

    logs = _audit_rows(scene.db, "danmaku_delete_comment")
    assert len(logs) == 1
    assert logs[0].target_id == c1_id
    assert logs[0].admin_user_id == admin_id

    r = c.delete(f"/api/admin/danmaku/comments/{c1_id}")
    assert r.status_code == 404, r.text


# ------------------------------------------------------------ 禁言


def test_mute_upsert_list_and_unmute(client, scene):
    c, app = client
    _as_admin(app, scene.admin)

    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": False,
        "duration_hours": 24, "reason": "刷屏",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == scene.alice.id
    assert body["username"] == "alice"
    assert body["permanent"] is False
    assert body["active"] is True
    assert body["muted_until"] is not None
    assert body["reason"] == "刷屏"
    first_id = body["id"]

    # 重复设置 = 更新同一行（改永久），不新增行
    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": True, "reason": "屡犯",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == first_id
    assert body["permanent"] is True
    assert body["muted_until"] is None
    scene.db.expire_all()
    assert scene.db.query(em.DanmakuMute).count() == 1

    r = c.get("/api/admin/danmaku/mutes")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["username"] == "alice"
    assert data["items"][0]["active"] is True

    assert len(_audit_rows(scene.db, "danmaku_mute_user")) == 2

    r = c.delete(f"/api/admin/danmaku/mutes/{scene.alice.id}")
    assert r.status_code == 200, r.text
    scene.db.expire_all()
    assert scene.db.query(em.DanmakuMute).count() == 0
    assert len(_audit_rows(scene.db, "danmaku_unmute_user")) == 1

    r = c.delete(f"/api/admin/danmaku/mutes/{scene.alice.id}")
    assert r.status_code == 404, r.text


def test_mute_validation(client, scene):
    c, app = client
    _as_admin(app, scene.admin)

    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": 999999, "permanent": True})
    assert r.status_code == 404, r.text

    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": False})
    assert r.status_code == 400, r.text

    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": False,
        "duration_hours": 0})
    assert r.status_code == 400, r.text

    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": False,
        "duration_hours": 24 * 365 + 1})
    assert r.status_code == 400, r.text

    scene.db.expire_all()
    assert scene.db.query(em.DanmakuMute).count() == 0


# ------------------------------------------------------------ 发送口真查禁言


def _post(client, guid, position, text):
    return client.post(f"/api/user/danmaku/{guid}",
                       json={"position_seconds": position, "text": text})


def test_muted_user_cannot_post(client, scene):
    c, app = client
    _enable(scene.db)
    _as_admin(app, scene.admin)
    _as_user(app, scene.alice)

    # 未禁言时可发
    r = _post(c, scene.item_a.guid, 5.0, "禁言前的一条")
    assert r.status_code == 200, r.text

    # 永久禁言：403、文案写明永久、不落库
    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": True})
    assert r.status_code == 200, r.text
    before = scene.db.query(em.DanmakuComment).count()
    r = _post(c, scene.item_a.guid, 6.0, "禁言中发言")
    assert r.status_code == 403, r.text
    assert "永久禁言" in r.json()["detail"]
    scene.db.expire_all()
    assert scene.db.query(em.DanmakuComment).count() == before

    # 拉取不受禁言影响
    r = c.get(f"/api/user/danmaku/{scene.item_a.guid}",
              params={"start": 0, "end": 60})
    assert r.status_code == 200, r.text

    # 改成已到期的定时禁言：不再拦
    r = c.post("/api/admin/danmaku/mutes", json={
        "user_id": scene.alice.id, "permanent": False,
        "duration_hours": 1})
    assert r.status_code == 200, r.text
    scene.db.expire_all()
    mute = scene.db.query(em.DanmakuMute).filter(
        em.DanmakuMute.user_id == scene.alice.id).first()
    mute.muted_until = datetime.now() - timedelta(minutes=1)
    scene.db.commit()

    r = _post(c, scene.item_a.guid, 7.0, "到期后发言")
    assert r.status_code == 200, r.text

    # 未到期的定时禁言：403 且文案带到期时间；他人不受影响
    mute.muted_until = datetime.now() + timedelta(hours=2)
    scene.db.commit()
    r = _post(c, scene.item_a.guid, 8.0, "又被禁言")
    assert r.status_code == 403, r.text
    assert "禁言至" in r.json()["detail"]

    _as_user(app, scene.bob)
    r = _post(c, scene.item_a.guid, 9.0, "我没被禁言")
    assert r.status_code == 200, r.text


# ------------------------------------------------------------ 判定唯一实现


def test_mute_active_semantics_fixed_now(db):
    from backend import danmaku as danmaku_core

    now = datetime(2026, 11, 19, 12, 0, 0)
    user = _mk_user(db, "sem_user")
    db.commit()

    assert danmaku_core.get_active_mute(db, user.id, now=now) is None

    row = em.DanmakuMute(user_id=user.id, muted_until=None, reason="")
    db.add(row)
    db.commit()
    assert danmaku_core.get_active_mute(db, user.id, now=now) is not None

    # muted_until == now：视为已到期，不禁言（边界口径钉死）
    row.muted_until = now
    db.commit()
    assert danmaku_core.get_active_mute(db, user.id, now=now) is None

    row.muted_until = now + timedelta(seconds=1)
    db.commit()
    assert danmaku_core.get_active_mute(db, user.id, now=now) is not None

    row.muted_until = now - timedelta(seconds=1)
    db.commit()
    assert danmaku_core.get_active_mute(db, user.id, now=now) is None
