# -*- coding: utf-8 -*-
"""管理员外键纠偏回归：admin_logs / tickets / ticket_messages 的管理员列
必须指向 web_users(id)，不得再指向已废弃的 admin_users(id)。

根因（2026-11-22 新机空库重建实证）：统一账号体系后管理员就是 WebUser，
``_audit`` 等写入方一直写 WebUser.id，但三处外键仍指 admin_users；
旧库靠 admin_users 里恰有同 id 行蒙混，空库重建后 admin_users 为 0 行，
PG 强校验 → POST /api/user/auth/admin-handoff 每次 500（整请求回滚）。

本文件在 SQLite 上开 PRAGMA foreign_keys=ON 复现 PG 的强校验行为，
且全程不给 admin_users 插任何行（不依赖生产那行止血锚点）：
- handoff 端点在 admin_users 为空时成功并落审计；
- _audit 写路径（系统身份 NULL + 管理员 id）成功；
- tickets / ticket_messages 的 admin_id 写 WebUser.id 成功、关系可解析；
- 三处外键元数据指向 web_users，且对不存在的 id 仍强制拦截。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, ratelimit
from backend.api.emby_portal import auth_router
from backend.database import get_db
from backend.security import create_access_token, hash_password

PASSWORD = "fk-pass-123"  # secret-scan: allow —— 测试夹具假密码，非真实凭据
HANDOFF_URL = "/api/user/auth/admin-handoff"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.execute(text("PRAGMA foreign_keys=ON"))
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


def _user(db, *, staff: bool):
    u = models.WebUser(
        username="usr_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(PASSWORD),
        is_staff=staff,
        admin_role="super" if staff else None,
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _client(db):
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _assert_admin_users_empty(db):
    # 本文件的全部断言都建立在「admin_users 为空」之上：不许靠锚点行蒙混。
    assert db.query(models.AdminUser).count() == 0


def test_handoff_succeeds_with_empty_admin_users(db):
    """新库实景：admin_users 0 行时 staff 交接应 200 并落审计（原先 500）。"""
    _assert_admin_users_empty(db)
    admin = _user(db, staff=True)
    token = create_access_token(
        admin.id, {"username": admin.username},
        token_version=admin.token_version or 0)

    res = _client(db).post(HANDOFF_URL, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert res.json()["url"].startswith("/admin/#magic=")

    audits = db.query(models.AdminLog).filter_by(
        admin_user_id=admin.id, action="admin_magic_frontend_handoff").all()
    assert len(audits) == 1
    _assert_admin_users_empty(db)


def test_audit_write_path_with_empty_admin_users(db):
    """_audit 写路径：管理员 id 与系统身份（NULL）都应落库成功。"""
    from backend.api.admin_core import _audit

    _assert_admin_users_empty(db)
    admin = _user(db, staff=True)

    _audit(db, admin.id, "fk_probe_action", "self", admin.id)
    _audit(db, None, "fk_probe_system", "library", 1, {"actor": "system"})
    db.commit()

    rows = db.query(models.AdminLog).filter(
        models.AdminLog.action.like("fk_probe_%")).all()
    assert len(rows) == 2
    by_action = {r.action: r for r in rows}
    assert by_action["fk_probe_action"].admin_user_id == admin.id
    assert by_action["fk_probe_action"].admin_user.username == admin.username
    assert by_action["fk_probe_system"].admin_user_id is None
    _assert_admin_users_empty(db)


def test_ticket_admin_columns_accept_web_user_id(db):
    """tickets.admin_id / ticket_messages.admin_id 写 WebUser.id 应成功。"""
    _assert_admin_users_empty(db)
    admin = _user(db, staff=True)
    user = _user(db, staff=False)

    ticket = models.Ticket(
        user_id=user.id, title="t", status="open", admin_id=admin.id)
    db.add(ticket)
    db.flush()
    msg = models.TicketMessage(
        ticket_id=ticket.id, admin_id=admin.id,
        message="m", is_admin=True)
    db.add(msg)
    db.commit()
    db.refresh(ticket)
    db.refresh(msg)

    assert ticket.admin.username == admin.username
    assert msg.admin.username == admin.username
    _assert_admin_users_empty(db)


def test_admin_fk_targets_web_users_in_metadata():
    """三处外键的元数据目标必须是 web_users（新库 create_all 口径）。"""
    targets = {}
    for table, column in (
        ("admin_logs", "admin_user_id"),
        ("tickets", "admin_id"),
        ("ticket_messages", "admin_id"),
    ):
        col = models.Base.metadata.tables[table].columns[column]
        fks = list(col.foreign_keys)
        assert len(fks) == 1, f"{table}.{column} 应有且仅有一个外键"
        targets[f"{table}.{column}"] = fks[0].column.table.name
    assert targets == {
        "admin_logs.admin_user_id": "web_users",
        "tickets.admin_id": "web_users",
        "ticket_messages.admin_id": "web_users",
    }


def test_admin_fk_still_enforced(db):
    """纠偏不是拆外键：不存在的管理员 id 在强制校验下仍应被拒。"""
    _assert_admin_users_empty(db)
    db.add(models.AdminLog(admin_user_id=99999, action="fk_probe_bad"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
