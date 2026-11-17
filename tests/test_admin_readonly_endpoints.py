"""管理后台只读观测口（专线 B 第一阶段：补口）。

覆盖 GET 口：绑定列表（sidecar 状态口已随观测页删除、采样进度口已随
批 3 sample_hash 删除、映射列表口已在主线移除）。全部在隔离临时
SQLite 里跑，不连生产库。
"""

import os
import tempfile

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



@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原。

    导入期顶层换绑会让全局 engine/SessionLocal 在 collection 后停在本文件
    的临时库上，后面的文件/用例撞 no such table 或读错库（#1668 同族）；
    换绑只在本模块用例期间生效。
    """
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

@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        for table in (em.UserStorageBinding,
                      em.MediaItem, base_models.WebUser):
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        s.close()


@pytest.fixture()
def admin(db):
    u = base_models.WebUser(username="readonlyadmin", is_staff=True,
                            is_active=True, password_hash="test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def client(db):
    """真实 app 的 TestClient：get_db 指向隔离库，鉴权逐用例覆盖。"""
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: SessionLocal()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def authed(client, admin):
    from backend.api.admin_core import get_current_admin
    from backend.main import app

    app.dependency_overrides[get_current_admin] = lambda: admin
    try:
        yield client
    finally:
        del app.dependency_overrides[get_current_admin]


READ_PATHS = [
    "/api/admin/storage-bindings",
]


def test_read_paths_require_auth(client):
    """各口无鉴权头 → 401/403（与 playback_admin 同口径）。"""
    for path in READ_PATHS:
        r = client.get(path)
        assert r.status_code in (401, 403), path


def _binding(db, user_id, provider="pan115", label="b1", status="active"):
    row = em.UserStorageBinding(user_id=user_id, provider=provider,
                                account_label=label, credential_id=999,
                                status=status)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_bindings_list_filters_and_no_credential_leak(db, authed):
    _binding(db, 1, label="a", status="active")
    _binding(db, 2, label="b", status="invalid")
    _binding(db, 1, label="c", status="active")
    r = authed.get("/api/admin/storage-bindings")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert all("credential_id" not in item for item in body["items"])
    assert all(item["has_credential"] is True for item in body["items"])
    r = authed.get("/api/admin/storage-bindings", params={"user_id": 1})
    assert r.json()["total"] == 2
    assert all(item["user_id"] == 1 for item in r.json()["items"])
    r = authed.get("/api/admin/storage-bindings", params={"status": "invalid"})
    assert r.json()["total"] == 1
    r = authed.get("/api/admin/storage-bindings",
                   params={"limit": 999, "offset": -5})
    assert r.json()["limit"] == 200
    assert r.json()["offset"] == 0
