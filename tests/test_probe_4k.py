"""4K MSE 探针结果回传端点回归测试（一次性真机诊断，2026-11-17）。

覆盖口径：
- POST /api/user/probe-4k/result 须登录，未登录 401；
- 仅 staff 可写：普通用户 403 且不落库（页面只对 staff 渲染不是
  权限，端点才是）；
- staff 写入往返：字段（item_guid/attempt/outcome/payload）原样
  落库、user_id 与 user_agent 记对；
- payload 超上限（20000 字符）→ 422 且不落库。
"""

import os
import tempfile

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
    """与 test_player_prefs 同口径：模块内换绑临时库、结束还原。"""
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
        s.query(core.Probe4kResult).delete()
        s.query(core.WebUser).delete()
        s.commit()
        yield s
    finally:
        try:
            s.query(core.Probe4kResult).delete()
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


_URL = "/api/user/probe-4k/result"

_BODY = {
    "item_guid": "e5ddc1ecca84fbb218254fb4b21b5e31",
    "attempt": "default",
    "outcome": "mse-playing",
    "payload": '{"useMSE": true, "firstVideoRendered": true}',
}


# ------------------------------------------------------------ 鉴权


def test_unauthenticated_rejected(client, db):
    c, _app = client
    r = c.post(_URL, json=_BODY)
    assert r.status_code == 401, r.text


def test_non_staff_forbidden_and_not_stored(client, db):
    c, app = client
    user = _mk_user(db, "probe_plain")
    db.commit()
    _as_user(app, user)
    r = c.post(_URL, json=_BODY)
    assert r.status_code == 403, r.text
    assert db.query(core.Probe4kResult).count() == 0


# ------------------------------------------------------------ 写入往返


def test_staff_post_roundtrip(client, db):
    c, app = client
    staff = _mk_user(db, "probe_staff", is_staff=True)
    db.commit()
    _as_user(app, staff)
    r = c.post(_URL, json=_BODY, headers={"user-agent": "probe-ua/1.0"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    row = db.query(core.Probe4kResult).filter_by(id=body["id"]).one()
    assert row.user_id == staff.id
    assert row.item_guid == _BODY["item_guid"]
    assert row.attempt == "default"
    assert row.outcome == "mse-playing"
    assert row.payload == _BODY["payload"]
    assert row.user_agent == "probe-ua/1.0"
    assert row.created_at is not None


def test_oversized_payload_rejected(client, db):
    c, app = client
    staff = _mk_user(db, "probe_staff2", is_staff=True)
    db.commit()
    _as_user(app, staff)
    big = dict(_BODY, payload="x" * 20001)
    r = c.post(_URL, json=big)
    assert r.status_code == 422, r.text
    assert db.query(core.Probe4kResult).count() == 0
