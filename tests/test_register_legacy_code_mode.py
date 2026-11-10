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


# ---------- 注册门禁：code 档（2026-11-10 定版恢复为正式档位） ----------

def test_register_code_mode_requires_code(db):
    """DB 里的 "code" 不再是待归一的历史残值，而是凭码注册档：
    没带注册码的注册必须被拒（400）、不落库；完整两步回归见
    test_reg_code_two_step.py。未知值仍旧 fail-closed 按 closed。"""
    from starlette.requests import Request
    from backend.api import emby_portal

    db.add(models.SystemConfig(key="registration_mode", value="code"))
    db.add(models.SystemConfig(key="register_ratelimit_enabled", value="false"))
    db.commit()
    scope = {"type": "http", "method": "POST", "path": "/", "headers": [],
             "client": ("1.2.3.4", 1234)}
    req = emby_portal.RegisterRequest(username="newbie", password="secret123")
    with pytest.raises(HTTPException) as exc:
        emby_portal.register(Request(scope), req, db)
    assert exc.value.status_code == 400
    assert db.query(models.WebUser).filter_by(username="newbie").first() is None
    cfg = emby_portal.register_config(db)
    assert cfg["registration_mode"] == "code"
    assert cfg["registration_code_required"] is True


def test_normalize_registration_mode():
    from backend.api.emby_portal import _normalize_registration_mode as norm

    assert norm(None) == "open"
    assert norm("") == "open"
    assert norm("open") == "open"
    assert norm("closed") == "closed"
    assert norm("code") == "code"
    assert norm("garbage") == "closed"
