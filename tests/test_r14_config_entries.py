import os
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    try:
        from backend.emby_server import models as em
        em.Base.metadata.create_all(engine)
    except Exception:
        pass
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def admin_user(db):
    user = models.WebUser(
        username="r14admin",
        password_hash="x",
        is_active=True,
        is_staff=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_economy_whitelist_accepts_max_pending_orders(db, admin_user):
    from backend.api.admin_economy import (
        economy_update_settings,
        economy_get_settings,
        EconomySettingsRequest,
    )

    res = economy_update_settings(
        EconomySettingsRequest(settings={"max_pending_orders_per_user": "10"}),
        admin_user,
        db,
    )
    assert res.get("success") is True

    cfg = (
        db.query(models.SystemConfig)
        .filter(models.SystemConfig.key == "max_pending_orders_per_user")
        .first()
    )
    assert cfg is not None
    assert cfg.value == "10"

    got = economy_get_settings(admin_user, db)
    assert got["settings"]["max_pending_orders_per_user"] == "10"


def _make_portal_app(db, admin_user):
    from backend.emby_server import portal

    app = FastAPI()
    app.include_router(portal.admin_emby_router)

    def _gd():
        yield db

    app.dependency_overrides[portal.require_staff] = lambda: admin_user
    app.dependency_overrides[portal.get_db] = _gd
    return app


# view-unlock 接口已随公益服删除整套下线（配置家族 view_unlock 同步
# 摘除）：GET/PUT 一律 404，不再有读写入口。
def test_portal_view_unlock_endpoints_removed(db, admin_user):
    app = _make_portal_app(db, admin_user)
    client = TestClient(app)
    r = client.get("/api/admin/emby/view-unlock")
    assert r.status_code == 404
    r = client.put("/api/admin/emby/view-unlock", json={"days": 30})
    assert r.status_code == 404
