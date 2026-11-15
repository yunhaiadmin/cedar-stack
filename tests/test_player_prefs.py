"""网页播放器设置后端同步回归测试（⚠️11，2026-11-15）。

覆盖口径：
- GET/PUT /api/user/player-preferences 须登录，未登录 401；
- 从未保存过：GET 回 preferences=null（与「存了空对象 {}」区分）；
- 存→读往返一致；PUT 是整对象覆盖，不是合并；
- 请求体必须是 JSON object：数组/字符串/数字/坏 JSON 一律 400 且不落库；
- 大小上限 16KB（序列化后字节数），超限 400；
- 两人隔离：A 存的设置 B 读不到；
- 子账号按本人存（设置是个人偏好、非经济动作，不走主账号归属，
  与弹幕发言同口径）。

存储：web_users.player_preferences JSON 列——与 /api/panel/privacy
（show_on_leaderboard 列）、通知偏好（tg_notify_* 列）同一套
「偏好即 WebUser 列」的既有 per-user 存储，不另起 KV 表。
"""

import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

from backend import models as core  # noqa: E402


# ------------------------------------------------------------ 测试基建

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


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        s.query(core.WebUser).delete()
        s.commit()
        yield s
    finally:
        try:
            s.query(core.WebUser).delete()
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()


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


def _mk_user(db, username, **kw):
    user = core.WebUser(username=username, password_hash="x",
                        is_active=True, **kw)
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def scene(db):
    user = _mk_user(db, "pp_u1")
    other = _mk_user(db, "pp_u2")
    parent = _mk_user(db, "pp_parent")
    child = _mk_user(db, "pp_child", parent_user_id=parent.id)
    db.commit()
    return SimpleNamespace(db=db, user=user, other=other,
                           parent=parent, child=child)


_URL = "/api/user/player-preferences"

_SAMPLE = {
    "danmaku": {
        "enabled": True, "area": "half", "opacity": 80,
        "fontSize": "small", "density": "low", "speed": "fast",
        "offsetSeconds": 2,
    },
    "rate": 1.5,
    "subtitle": {"fontSize": "large", "delayMs": 250},
}


# ------------------------------------------------------------ 鉴权


def test_unauthenticated_rejected(client, scene):
    c, _app = client
    r = c.get(_URL)
    assert r.status_code == 401, r.text
    r = c.put(_URL, json=_SAMPLE)
    assert r.status_code == 401, r.text


# ------------------------------------------------------------ 往返与语义


def test_get_null_when_never_saved(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.get(_URL)
    assert r.status_code == 200, r.text
    assert r.json()["preferences"] is None


def test_put_get_roundtrip(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.put(_URL, json=_SAMPLE)
    assert r.status_code == 200, r.text
    assert r.json()["preferences"] == _SAMPLE
    r = c.get(_URL)
    assert r.status_code == 200, r.text
    assert r.json()["preferences"] == _SAMPLE


def test_put_empty_object_is_saved_not_null(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.put(_URL, json={})
    assert r.status_code == 200, r.text
    r = c.get(_URL)
    assert r.json()["preferences"] == {}


def test_put_replaces_whole_object(client, scene):
    c, app = client
    _as_user(app, scene.user)
    assert c.put(_URL, json=_SAMPLE).status_code == 200
    r = c.put(_URL, json={"rate": 2})
    assert r.status_code == 200, r.text
    r = c.get(_URL)
    assert r.json()["preferences"] == {"rate": 2}


# ------------------------------------------------------------ 校验


def test_put_non_object_rejected(client, scene):
    c, app = client
    _as_user(app, scene.user)
    for bad in ([1, 2, 3], "just-a-string", 42, True):
        r = c.put(_URL, json=bad)
        assert r.status_code == 400, (bad, r.text)
    r = c.put(_URL, content="{not json",
              headers={"Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    # 拒绝后不许落库
    r = c.get(_URL)
    assert r.json()["preferences"] is None


def test_put_oversize_rejected(client, scene):
    c, app = client
    _as_user(app, scene.user)
    big = {"blob": "x" * (17 * 1024)}
    r = c.put(_URL, json=big)
    assert r.status_code == 400, r.text
    r = c.get(_URL)
    assert r.json()["preferences"] is None


def test_put_just_under_limit_accepted(client, scene):
    c, app = client
    _as_user(app, scene.user)
    ok = {"blob": "x" * (15 * 1024)}
    r = c.put(_URL, json=ok)
    assert r.status_code == 200, r.text


# ------------------------------------------------------------ 隔离与归属


def test_users_isolated(client, scene):
    c, app = client
    _as_user(app, scene.user)
    assert c.put(_URL, json=_SAMPLE).status_code == 200
    _as_user(app, scene.other)
    r = c.get(_URL)
    assert r.json()["preferences"] is None
    assert c.put(_URL, json={"rate": 0.5}).status_code == 200
    _as_user(app, scene.user)
    r = c.get(_URL)
    assert r.json()["preferences"] == _SAMPLE


def test_sub_account_saves_as_self(client, scene):
    c, app = client
    _as_user(app, scene.child)
    r = c.put(_URL, json=_SAMPLE)
    assert r.status_code == 200, r.text
    r = c.get(_URL)
    assert r.json()["preferences"] == _SAMPLE
    # 主账号读到的仍是「从未保存」，子账号的设置不许串到主账号名下
    _as_user(app, scene.parent)
    r = c.get(_URL)
    assert r.json()["preferences"] is None
    # 落库行核对：值在子账号行上，主账号行为空
    scene.db.expire_all()
    child_row = scene.db.query(core.WebUser).get(scene.child.id)
    parent_row = scene.db.query(core.WebUser).get(scene.parent.id)
    assert child_row.player_preferences == _SAMPLE
    assert parent_row.player_preferences is None
