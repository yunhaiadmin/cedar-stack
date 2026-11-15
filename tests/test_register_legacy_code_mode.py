"""TG Bot / 注册门禁安全回归测试（be/core 审查）。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.integrations import store
from backend.tg_bot import handlers, login_token, poller


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    session.close()
    store.invalidate()


def _make_user(db, username="u1", tg_id=None):
    u = models.WebUser(username=username, telegram_id=tg_id, password_hash="x")
    db.add(u)
    db.commit()
    return u


# ---------- 注册门禁：存量 code 值归一 open（2026-11-15 注册码下线） ----------

def test_stored_code_mode_registers_open_without_code(db):
    """DB 里的存量 "code" 是待归一的历史残值：注册码已下线，归一为
    open——没带任何码的注册必须成功落库（归一成 closed 等于关死注册）；
    配置下发同样报 open 且不再有 registration_code_required 键。
    未知值仍旧 fail-closed 按 closed。完整新口径回归见
    test_registration_open_no_code.py。"""
    from starlette.requests import Request
    from backend.api import emby_portal

    db.add(models.SystemConfig(key="registration_mode", value="code"))
    db.add(models.SystemConfig(key="register_ratelimit_enabled", value="false"))
    db.commit()
    scope = {"type": "http", "method": "POST", "path": "/", "headers": [],
             "client": ("1.2.3.4", 1234)}
    req = emby_portal.RegisterRequest(username="newbie", password="secret123")
    resp = emby_portal.register(Request(scope), req, db)
    assert resp.access_token
    assert db.query(models.WebUser).filter_by(username="newbie").first() is not None
    cfg = emby_portal.register_config(db)
    assert cfg["registration_mode"] == "open"
    assert "registration_code_required" not in cfg


def test_normalize_registration_mode():
    from backend.api.emby_portal import _normalize_registration_mode as norm

    assert norm(None) == "open"
    assert norm("") == "open"
    assert norm("open") == "open"
    assert norm("closed") == "closed"
    assert norm("code") == "open"
    assert norm("garbage") == "closed"
