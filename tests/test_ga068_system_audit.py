# -*- coding: utf-8 -*-
"""GA-068 收尾：机器口记系统身份、C 类补进日志（#58）。

钉住的口径（用户 2026-11-12 08:30 定）：
- B 类 3 个 EA 机器口（节点扫描、流节点注册/注销）没有管理员可归属：
  ``_audit(db, None, ...)`` 记系统身份——admin_user_id 存 NULL（不触发
  admin_users 外键，绝不编假管理员 id），details 带 ``"actor": "system"``，
  操作日志列表把这类行显示为「系统」。
- C 类 1 个（播放总线发命令）：命令表已落 requested_by，再补进 AdminLog，
  与 #61 其余管理写口同口径（_audit + commit）。

全部在隔离临时 SQLite 里跑（照 test_admin_audit_api 的模块夹具卫生口径：
换绑只在本模块用例期间生效、结束还原）。
"""
import importlib.util
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

import pytest

from backend import models as base_models
from backend.api.admin_core import _audit
from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server.playback_bus import PlaybackControlCommand

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
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


_TABLES = (
    base_models.AdminLog,
    base_models.WebUser,
    base_models.SystemConfig,
    em.Library,
    PlaybackControlCommand,
)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        for table in _TABLES:
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _load_audit_script():
    spec = importlib.util.spec_from_file_location(
        "check_admin_audit_coverage",
        REPO_ROOT / "scripts" / "check_admin_audit_coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_scan_has_no_missing():
    """审计检查 0 缺口：4 个缺口（3 机器口 + 1 总线命令）都已补上。"""
    module = _load_audit_script()
    missing, stale, total = module.scan()
    assert total > 0
    assert missing == []
    assert stale == []


def test_audit_accepts_none_as_system_identity(db):
    """_audit(db, None, ...) 落库：admin_user_id 为 NULL，不抛错。"""
    _audit(db, None, "test_system_action", "test", None,
           {"actor": "system", "auth": "panel_key"})
    db.commit()
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "test_system_action").one()
    assert row.admin_user_id is None
    assert row.details["actor"] == "system"


def test_audit_int_admin_still_works(db):
    """整数管理员 id 路径不变：照旧存值。"""
    _audit(db, 7, "test_int_action", "test", 3, {"k": "v"})
    db.commit()
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "test_int_action").one()
    assert row.admin_user_id == 7
    assert row.target_id == 3


def test_stream_node_register_writes_system_audit(db, monkeypatch):
    """POST /api/admin/stream-nodes/register 写系统身份审计行。"""
    from backend.emby_server import stream_nodes

    monkeypatch.setattr(stream_nodes, "_panel_auth", lambda request: None)
    result = stream_nodes.api_register(
        None, {"url": "https://stream.example.com", "name": "n1"}, db)
    assert result["ok"] is True
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "stream_node_register").one()
    assert row.admin_user_id is None
    assert row.details["actor"] == "system"
    assert row.details["url"] == "https://stream.example.com"


def test_stream_node_unregister_writes_system_audit(db, monkeypatch):
    """DELETE /api/admin/stream-nodes 写系统身份审计行（含 removed）。"""
    from backend.emby_server import stream_nodes

    monkeypatch.setattr(stream_nodes, "_panel_auth", lambda request: None)
    stream_nodes.register_node(db, "https://stream.example.com", "n1")
    db.query(base_models.AdminLog).delete()
    db.commit()
    result = stream_nodes.api_unregister(None, "https://stream.example.com", db)
    assert result["ok"] is True
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "stream_node_unregister").one()
    assert row.admin_user_id is None
    assert row.details["actor"] == "system"
    assert row.details["removed"] is True


def test_node_scan_library_writes_system_audit(db):
    """POST /api/admin/nodes/libraries/{id}/scan 写系统身份审计行。"""
    from backend.emby_server import nodes

    lib = em.Library(guid="test-ga068-lib", name="测试库",
                     collection_type="movies", paths="")
    db.add(lib)
    db.commit()
    result = nodes.node_scan_library(lib.id, full=False, db=db)
    assert result["success"] is True
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "node_scan_library").one()
    assert row.admin_user_id is None
    assert row.target_type == "library"
    assert row.target_id == lib.id
    assert row.details["actor"] == "system"
    assert row.details["full"] is False


def test_send_command_writes_admin_audit(db, monkeypatch):
    """POST /api/admin/playback-bus/commands 补进 AdminLog（C 类）。"""
    from backend.emby_server import playback_bus as bus
    from backend.emby_server import playback_bus_admin

    monkeypatch.setattr(bus, "_ENABLED", True)
    admin = base_models.WebUser(username="ga068-admin", password_hash="x",
                                is_staff=True, is_active=True,
                                admin_role="operator")
    db.add(admin)
    db.commit()
    body = playback_bus_admin.CommandIn(
        session_key="s-ga068-nonexistent", command="pause")
    result = playback_bus_admin.send_command(
        body, None, current_admin=admin, db=db)
    assert result["command"] == "pause"
    row = db.query(base_models.AdminLog).filter(
        base_models.AdminLog.action == "playback_bus_command").one()
    assert row.admin_user_id == admin.id
    assert row.details["command"] == "pause"
    assert row.details["session_key"] == "s-ga068-nonexistent"
    assert row.details["command_id"] == result["command_id"]


def test_system_audit_row_renders_as_system(db):
    """操作日志列表：系统身份行显示「系统」，无 actor 的 NULL 行仍是「未知」。"""
    from backend.api.admin import get_admin_logs

    admin = base_models.WebUser(username="ga068-viewer", password_hash="x",
                                is_staff=True, is_active=True)
    db.add(admin)
    db.commit()
    db.add(base_models.AdminLog(
        admin_user_id=None, action="sys_row", target_type="test",
        details={"actor": "system"}))
    db.add(base_models.AdminLog(
        admin_user_id=None, action="unknown_row", target_type="test",
        details={}))
    db.add(base_models.AdminLog(
        admin_user_id=admin.id, action="admin_row", target_type="test",
        details={}))
    db.commit()
    rows = get_admin_logs(current_admin=admin, db=db)
    by_action = {r["action"]: r["admin_name"] for r in rows}
    assert by_action["sys_row"] == "系统"
    assert by_action["unknown_row"] == "未知"
    assert by_action["admin_row"] == "ga068-viewer"
