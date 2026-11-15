"""服务器页「雷达任务」接真源回归测试（2026-11-15）。

旧实现把 4 项状态写死：probe 恒 True、scan/night 恒 False、只有 enrich
随 backlog 变。本文件逐项证明下发状态跟着真源变、无真源/功能关闭时
整项不下发：

- probe ＝ probe_worker.status_snapshot（启用＋未暂停＋有在飞/探测中条目）
- scan ＝ ScanRun running 流水 / scanner.is_scan_active / Library.is_scanning
- night ＝ enrich_worker 单集夜间刮削配置（关→不下发；开→active=在窗口内）
- enrich ＝ 沿用既有 backlog 口径（锁定不回归）

全部用隔离的内存 SQLite，不碰网络、不碰生产库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture()
def user(db):
    u = models.WebUser(username="radaruser", password_hash="x",
                       is_active=True, is_staff=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _radar(db, user):
    from backend.api import panel as panel_api
    payload = panel_api.panel_server(db=db, user=user)["data"]
    return {t["key"]: t for t in payload["radar_tasks"]}


def _stub_probe(monkeypatch, **over):
    from backend.emby_server import probe_worker
    snap = {
        "enabled": True,
        "paused": False,
        "counts": {"pending": 0, "probing": 0},
        "runtime": {"running": True, "in_flight": 0},
    }
    snap.update(over)
    monkeypatch.setattr(probe_worker, "status_snapshot",
                        lambda db=None, light=False: snap)
    return snap


def _stub_night(monkeypatch, **over):
    from backend.emby_server import enrich_worker
    cfg = {"enabled": True, "night_start": "02:00", "night_end": "06:00",
           "night_tz": "Asia/Shanghai", "in_window": False}
    cfg.update(over)
    monkeypatch.setattr(enrich_worker, "get_episode_night_config",
                        lambda db: cfg)
    return cfg


def _stub_backlog(monkeypatch, backlog):
    import backend.main as main_mod
    monkeypatch.setattr(
        main_mod, "_collect_health",
        lambda: {"status": "healthy", "level": "ok",
                 "metrics": {"enrich_backlog": backlog}})


# ==================== probe：跟随状态快照 ====================

def test_probe_idle_when_no_work(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert tasks["probe"]["active"] is False, "无在飞任务时探针应待命（旧实现恒 True）"


def test_probe_active_when_in_flight(db, user, monkeypatch):
    _stub_probe(monkeypatch, runtime={"running": True, "in_flight": 3})
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert tasks["probe"]["active"] is True


def test_probe_active_when_probing_rows(db, user, monkeypatch):
    _stub_probe(monkeypatch, counts={"pending": 5, "probing": 2},
                runtime=None)
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert tasks["probe"]["active"] is True, "DB 里有 probing 条目也算在干活"


def test_probe_idle_when_paused(db, user, monkeypatch):
    _stub_probe(monkeypatch, paused=True,
                runtime={"running": True, "in_flight": 3})
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert tasks["probe"]["active"] is False, "暂停时即使有残留在飞也报待命"


def test_probe_omitted_when_disabled(db, user, monkeypatch):
    _stub_probe(monkeypatch, enabled=False)
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert "probe" not in tasks, "探测总开关关时整项不下发"


def test_probe_omitted_when_snapshot_raises(db, user, monkeypatch):
    from backend.emby_server import probe_worker

    def _boom(db=None, light=False):
        raise RuntimeError("snapshot down")

    monkeypatch.setattr(probe_worker, "status_snapshot", _boom)
    _stub_night(monkeypatch, enabled=False)
    tasks = _radar(db, user)
    assert "probe" not in tasks, "真源接不出时整项不下发"
    assert "enrich" in tasks and "scan" in tasks, "其余项不受影响"


# ==================== scan：跟随 ScanRun / 进程内状态 ====================

def _library(db, name="雷达库"):
    lib = em.Library(guid=f"radar-lib-{uuid.uuid4().hex[:8]}", name=name)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return lib


def test_scan_idle_without_running_run(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False)
    _library(db)
    tasks = _radar(db, user)
    assert tasks["scan"]["active"] is False


def test_scan_active_with_running_run(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False)
    lib = _library(db)
    db.add(em.ScanRun(library_id=lib.id, status="running",
                      started_at=datetime.now()))
    db.commit()
    tasks = _radar(db, user)
    assert tasks["scan"]["active"] is True, "有 running 流水时扫描应报进行中（旧实现恒 False）"


def test_scan_active_with_library_flag(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False)
    lib = _library(db)
    lib.is_scanning = True
    db.commit()
    tasks = _radar(db, user)
    assert tasks["scan"]["active"] is True


# ==================== night：单集夜间刮削配置 ====================

def test_night_omitted_when_disabled(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False, in_window=True)
    tasks = _radar(db, user)
    assert "night" not in tasks, "夜间单集模式关时整项不下发（旧实现恒下发 False）"


def test_night_active_in_window(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=True, in_window=True)
    tasks = _radar(db, user)
    assert tasks["night"]["active"] is True, "在夜间窗口内应报进行中（旧实现恒 False）"


def test_night_idle_outside_window(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=True, in_window=False)
    tasks = _radar(db, user)
    assert tasks["night"]["active"] is False


# ==================== enrich：backlog 口径锁定 ====================

def test_enrich_follows_backlog(db, user, monkeypatch):
    _stub_probe(monkeypatch)
    _stub_night(monkeypatch, enabled=False)
    _stub_backlog(monkeypatch, 0)
    assert _radar(db, user)["enrich"]["active"] is False
    _stub_backlog(monkeypatch, 7)
    assert _radar(db, user)["enrich"]["active"] is True
