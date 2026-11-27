"""仪表盘 ops part（2026-11-27 四块补缺口）+ trend 新增订阅口径。

- ops.subscriptions：与 economy_list_subscriptions 的 active/expiring
  筛选同口径（status='active' 且 end_date 在 (now, now+7d]）。
- ops.top_requests：待处理求片按作品聚合（tmdb 归并优先），降序前 5。
- ops.health/disk：observability.snapshot 产物的映射（level→status、
  warn/down 组件 message 原文进 reasons），开关关时两项为 None。
- ops.probe/enrich：worker 状态快照的字段映射；单子项抛错只降级该子项。
- trend series/totals 新增 new_subscriptions：按 UserSubscription.created_at
  与 new_users 同手法（+8h 上海日聚合），旧字段形状不变。

全部在隔离临时 SQLite 里跑，不连生产库、不碰网络。
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm


import pytest

from backend import models as base_models
from backend.api import admin_dashboard, admin_economy
from backend.database import SessionLocal, init_db


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """与 test_admin_dashboard 同手法：本文件独占临时库，模块内换绑、结束还原。"""
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
        for tbl in (base_models.UserSubscription, base_models.MovieRequest,
                    base_models.SubscriptionPlan, base_models.WebUser):
            s.query(tbl).delete()
        s.commit()
        yield s
    finally:
        s.close()


@pytest.fixture()
def admin(db):
    u = base_models.WebUser(username="opsadmin", is_staff=True, is_active=True,
                            password_hash="test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def plan(db):
    p = base_models.SubscriptionPlan(name="测试套餐", price=9.9, duration_days=30)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _mk_sub(db, user_id, plan_id, *, status="active", start=None, end=None,
            created=None):
    now = datetime.now()
    r = base_models.UserSubscription(
        user_id=user_id, plan_id=plan_id, status=status,
        start_date=start if start is not None else now - timedelta(days=1),
        end_date=end if end is not None else now + timedelta(days=30),
    )
    if created is not None:
        r.created_at = created
    db.add(r)
    db.commit()
    return r


# ==================== subscriptions ====================

def test_ops_subscriptions_counts(db, admin, plan):
    now = datetime.now()
    _mk_sub(db, admin.id, plan.id, end=now + timedelta(days=30))       # active
    _mk_sub(db, admin.id, plan.id, end=now + timedelta(days=3))        # active + expiring
    _mk_sub(db, admin.id, plan.id, end=now + timedelta(days=7, hours=-1))  # expiring 内侧
    _mk_sub(db, admin.id, plan.id, end=now + timedelta(days=7, hours=1))   # 7 天外
    _mk_sub(db, admin.id, plan.id, end=now - timedelta(hours=1))       # 已过期
    _mk_sub(db, admin.id, plan.id, status="expired", end=now + timedelta(days=30))
    _mk_sub(db, admin.id, plan.id, status="cancelled", end=now + timedelta(days=3))
    got = admin_dashboard._ops_subscriptions(db)
    assert got == {"active": 4, "expiring_7d": 2}


# ==================== top_requests ====================

def _mk_req(db, user_id, name, *, status="pending", tmdb_id=None):
    r = base_models.MovieRequest(user_id=user_id, movie_name=name,
                                 status=status, tmdb_id=tmdb_id)
    db.add(r)
    db.commit()
    return r


def test_ops_top_requests_aggregation(db, admin):
    for _ in range(2):
        _mk_req(db, admin.id, "片甲", tmdb_id="100")
    _mk_req(db, admin.id, "片甲（别名）", tmdb_id="100")   # 同 tmdb 归并
    for _ in range(2):
        _mk_req(db, admin.id, "片乙")
    _mk_req(db, admin.id, "", tmdb_id="200")              # 无片名 → 占位
    for _ in range(5):
        _mk_req(db, admin.id, "片甲", status="approved", tmdb_id="100")  # 非 pending 不计
    got = admin_dashboard._ops_top_requests(db)
    assert len(got) == 3
    assert got[0]["count"] == 3
    assert got[0]["title"] in ("片甲", "片甲（别名）")
    assert got[1] == {"title": "片乙", "count": 2}
    assert got[2] == {"title": "未命名求片", "count": 1}


# ==================== health / disk ====================

def _fake_snap(level, subsystems):
    return {"enabled": True, "level": level, "subsystems": subsystems}


def test_ops_health_disk_degraded(db, monkeypatch):
    from backend.emby_server import maintenance, observability
    subs = {
        "disk": {"key": "disk", "status": "warn",
                 "message": "磁盘余量偏低（10% 剩余）",
                 "metrics": {"disk_free_percent": 10.0}},
        "enrich": {"key": "enrich", "status": "warn",
                   "message": "补全队列积压 900 条", "metrics": {}},
        "api": {"key": "api", "status": "ok", "message": "API 指标正常",
                "metrics": {}},
    }
    monkeypatch.setattr(observability, "snapshot", lambda d: _fake_snap("warn", subs))
    monkeypatch.setattr(maintenance, "resource_report",
                        lambda: {"disk_free_bytes": 5 * 2 ** 30,
                                 "disk_free_percent": 10.0})
    health, disk = admin_dashboard._ops_health_disk(db)
    assert health == {"status": "degraded",
                      "reasons": ["磁盘余量偏低（10% 剩余）", "补全队列积压 900 条"]}
    assert disk == {"free_bytes": 5 * 2 ** 30, "free_percent": 10.0, "warn": True}


def test_ops_health_healthy_and_disabled(db, monkeypatch):
    from backend.emby_server import observability
    subs = {"disk": {"key": "disk", "status": "ok", "message": "磁盘余量正常",
                     "metrics": {"disk_free_percent": 55.0}}}
    monkeypatch.setattr(observability, "snapshot", lambda d: _fake_snap("ok", subs))
    health, disk = admin_dashboard._ops_health_disk(db)
    assert health == {"status": "healthy", "reasons": []}
    assert disk["warn"] is False
    assert disk["free_percent"] == 55.0

    monkeypatch.setattr(observability, "snapshot", lambda d: {"enabled": False})
    health, disk = admin_dashboard._ops_health_disk(db)
    assert health is None and disk is None


# ==================== probe / enrich 映射与降级 ====================

def test_ops_probe_mapping(db, monkeypatch):
    from backend.emby_server import probe_worker
    monkeypatch.setattr(probe_worker, "status_snapshot", lambda d, light=False: {
        "pending_ready": 7, "runtime": {"rate_per_min": 3.5},
        "eta_hours": 0.03, "paused": True})
    assert admin_dashboard._ops_probe(db) == {
        "pending_ready": 7, "rate_per_min": 3.5, "eta_hours": 0.03, "paused": True}
    monkeypatch.setattr(probe_worker, "status_snapshot", lambda d, light=False: {
        "pending_ready": 0, "runtime": None, "eta_hours": None, "paused": False})
    assert admin_dashboard._ops_probe(db) == {
        "pending_ready": 0, "rate_per_min": None, "eta_hours": None, "paused": False}


def test_ops_enrich_mapping(db, monkeypatch):
    from backend.emby_server import enrich_worker
    monkeypatch.setattr(enrich_worker, "get_progress", lambda light=False: {
        "enrich": {"pending": 10, "enriching": 2, "done": 0, "failed": 0,
                   "retrying": 0},
        "throughput": {"done_per_min": 4.0, "done_total": 9, "retry_total": 0,
                       "failed_total": 0},
        "enabled": True})
    assert admin_dashboard._ops_enrich(db) == {
        "pending_ready": 12, "rate_per_min": 4.0, "eta_hours": 0.05,
        "paused": False}


def test_ops_snapshot_subitem_isolation(db, admin, plan, monkeypatch):
    from backend.emby_server import enrich_worker, observability, probe_worker

    def _boom(d, light=False):
        raise RuntimeError("probe down")

    monkeypatch.setattr(probe_worker, "status_snapshot", _boom)
    monkeypatch.setattr(observability, "snapshot", lambda d: {"enabled": False})
    monkeypatch.setattr(enrich_worker, "get_progress", lambda light=False: {
        "enrich": {"pending": 1, "enriching": 0, "done": 0, "failed": 0,
                   "retrying": 0},
        "throughput": {"done_per_min": 0, "done_total": 0, "retry_total": 0,
                       "failed_total": 0},
        "enabled": False})
    _mk_sub(db, admin.id, plan.id)
    _mk_req(db, admin.id, "片乙")
    got = admin_dashboard.get_ops_snapshot(admin, db)
    assert got["probe"] is None                       # 抛错子项单独降级
    assert got["health"] is None and got["disk"] is None
    assert got["subscriptions"] == {"active": 1, "expiring_7d": 0}
    assert got["top_requests"] == [{"title": "片乙", "count": 1}]
    assert got["enrich"] == {"pending_ready": 1, "rate_per_min": None,
                             "eta_hours": None, "paused": True}


# ==================== trend new_subscriptions ====================

SH = ZoneInfo("Asia/Shanghai")
FROZEN_SH = datetime(2026, 10, 18, 14, 30, tzinfo=SH)
FROZEN_UTC_NAIVE = FROZEN_SH.astimezone(timezone.utc).replace(tzinfo=None)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_SH if tz is not None else FROZEN_UTC_NAIVE


def _utc(sh_dt):
    return sh_dt.astimezone(timezone.utc).replace(tzinfo=None)


def test_trend_new_subscriptions(db, admin, plan, monkeypatch):
    monkeypatch.setattr(admin_economy, "datetime", _FrozenDateTime)
    _mk_sub(db, admin.id, plan.id, created=_utc(datetime(2026, 10, 18, 10, 0, tzinfo=SH)))
    _mk_sub(db, admin.id, plan.id, created=_utc(datetime(2026, 10, 18, 23, 59, tzinfo=SH)))
    _mk_sub(db, admin.id, plan.id, created=_utc(datetime(2026, 10, 17, 10, 0, tzinfo=SH)))
    _mk_sub(db, admin.id, plan.id, created=_utc(datetime(2026, 10, 16, 10, 0, tzinfo=SH)))
    _mk_sub(db, admin.id, plan.id, created=_utc(datetime(2026, 10, 15, 10, 0, tzinfo=SH)))  # 窗口外
    resp = admin_economy.get_stats_trend(days=3, current_admin=None, db=db)
    assert [p["date"] for p in resp["series"]] == [
        "2026-10-16", "2026-10-17", "2026-10-18"]
    assert [p["new_subscriptions"] for p in resp["series"]] == [1, 1, 2]
    assert resp["totals"]["new_subscriptions"] == 4
    # 旧字段形状不变
    for p in resp["series"]:
        assert {"date", "new_users", "plays", "new_items", "revenue"} <= set(p)
    assert {"new_users", "plays", "new_items", "revenue"} <= set(resp["totals"])
