# -*- coding: utf-8 -*-
"""条目起播状态只读 API 测试（GET /api/play/items/{id}/status）。

隔离：内存 SQLite + 最小 FastAPI 应用，不碰开发库/生产库，
不 import backend.main。覆盖状态机五态：idle / ready(own) /
ready(pool) / preparing（排队/搬运中/带进度）/ failed（类别键），
以及剧集按集聚合与 404。
"""
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
from backend.api import play_status as ps_api


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    from backend.emby_server import models as em
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
    u = models.WebUser(username="ps_test", password_hash="x",
                       is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def client(db, user):
    app = FastAPI()

    def _get_db():
        yield db

    from backend.database import get_db
    from backend.api.user import get_current_user
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.include_router(ps_api.router)
    return TestClient(app)


def _item(db, **kw):
    import uuid
    from backend.emby_server import models as em
    kw.setdefault("guid", uuid.uuid4().hex)
    kw.setdefault("library_id", 44)
    it = em.MediaItem(name=kw.pop("name", "测试片"),
                      item_type=kw.pop("item_type", "movie"), **kw)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _variant(db, item_id, user_id, status="ready", drive="userdrive:1"):
    from backend.emby_server import models as em
    v = em.TransferVariant(
        item_id=item_id, source_file_id="src1", variant_file_id="cp1",
        variant_drive_id=drive, owner_scope="user",
        owner_ref=str(user_id), status=status)
    db.add(v)
    db.commit()
    return v


def _job(db, item_id, user_id, status, error_kind=None):
    from backend.emby_server import models as em
    j = em.TransferJob(item_id=item_id, trigger="play", provider="drive",
                       owner_scope="user", owner_ref=str(user_id),
                       status=status, error_kind=error_kind)
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _get(client, item_id):
    res = client.get(f"/api/play/items/{item_id}/status")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    return body["data"]


def test_unknown_item_404(client):
    res = client.get("/api/play/items/999999/status")
    assert res.status_code == 404


def test_idle_when_nothing(db, user, client):
    it = _item(db)
    data = _get(client, it.id)
    assert data["state"] == "idle"
    assert data["source"] is None
    assert data["progress_pct"] is None


def test_ready_own_copy(db, user, client):
    it = _item(db)
    _variant(db, it.id, user.id)
    data = _get(client, it.id)
    assert data["state"] == "ready"
    assert data["source"] == "own"


def test_ready_own_ignores_offline_provider_copy(db, user, client):
    it = _item(db)
    _variant(db, it.id, user.id, drive="quark:abc")
    data = _get(client, it.id)
    assert data["state"] == "idle"


def test_preparing_queued(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "pending")
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "queued"
    assert data["progress_pct"] is None


def test_preparing_with_progress(db, user, client):
    from backend.emby_server import models as em
    it = _item(db)
    job = _job(db, it.id, user.id, "running")
    db.add(em.TransferDelivery(job_id=job.id, content_key="k1",
                               size=1000, committed_offset=250))
    db.commit()
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "working"
    assert data["progress_pct"] == 25


def test_preparing_running_without_delivery_no_progress(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "running")
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "working"
    assert data["progress_pct"] is None


def test_failed_space_reason(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="insufficient_space")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "space"


def test_failed_generic_reason(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="retriable")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "generic"


def test_failed_source_dead_reason(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="dead")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "source_dead"


def test_ready_beats_failed(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="retriable")
    _variant(db, it.id, user.id)
    data = _get(client, it.id)
    assert data["state"] == "ready"


def test_preparing_beats_failed(db, user, client):
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="retriable")
    _job(db, it.id, user.id, "pending")
    data = _get(client, it.id)
    assert data["state"] == "preparing"


def test_other_users_jobs_do_not_leak(db, user, client):
    it = _item(db)
    _variant(db, it.id, user.id + 999)
    _job(db, it.id, user.id + 999, "running")
    _job(db, it.id, user.id + 999, "failed", error_kind="dead")
    data = _get(client, it.id)
    assert data["state"] == "idle"


def test_pool_ready_for_unbound_user(db, user, client, monkeypatch):
    from backend.emby_server import pool_gate, playback_gate
    it = _item(db)
    monkeypatch.setattr(pool_gate, "pool_eligible",
                        lambda db_, u, i: "eligible")
    monkeypatch.setattr(pool_gate, "_servable_pool_copy_count",
                        lambda db_, iid: 1)
    monkeypatch.setattr(playback_gate, "has_active_binding",
                        lambda db_, uid: False)
    data = _get(client, it.id)
    assert data["state"] == "ready"
    assert data["source"] == "pool"


def test_pool_copy_not_ready_for_bound_user_small_file(
        db, user, client, monkeypatch):
    from backend.emby_server import pool_gate, playback_gate
    it = _item(db)
    monkeypatch.setattr(pool_gate, "pool_eligible",
                        lambda db_, u, i: "eligible")
    monkeypatch.setattr(pool_gate, "_servable_pool_copy_count",
                        lambda db_, iid: 1)
    monkeypatch.setattr(playback_gate, "has_active_binding",
                        lambda db_, uid: True)
    monkeypatch.setattr(pool_gate, "large_file_pool_route",
                        lambda db_, u, i: False)
    data = _get(client, it.id)
    assert data["state"] == "idle"


def test_series_aggregates_episode_ready(db, user, client):
    series = _item(db, item_type="series", name="测试剧")
    ep = _item(db, item_type="episode", series_id=series.id, name="E1")
    _variant(db, ep.id, user.id)
    data = _get(client, series.id)
    assert data["state"] == "ready"
    assert data["source"] == "own"


def test_series_aggregates_episode_preparing(db, user, client):
    series = _item(db, item_type="series", name="测试剧2")
    ep = _item(db, item_type="episode", series_id=series.id, name="E1")
    _job(db, ep.id, user.id, "running")
    data = _get(client, series.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "working"


# ---------------------------------------------------------------------------
# guid 口径（回归：前端 DTO 的 Id 恒为 guid 字符串，路由曾只收 int，
# 正常路径恒 422、状态条永不出现；int 传法须继续兼容）
# ---------------------------------------------------------------------------

def test_status_by_guid_idle(db, user, client):
    it = _item(db)
    data = _get(client, it.guid)
    assert data["item_id"] == it.id
    assert data["state"] == "idle"


def test_status_by_guid_ready_own(db, user, client):
    it = _item(db)
    _variant(db, it.id, user.id)
    data = _get(client, it.guid)
    assert data["item_id"] == it.id
    assert data["state"] == "ready"
    assert data["source"] == "own"


def test_status_unknown_guid_404(client):
    res = client.get(
        "/api/play/items/00000000000000000000000000000000/status")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 等待优化第三层·进度可见（设计稿 ~/workspace/goals/v2/plans/
# layer3-progress-visibility.md）：play_status 扩展字段 route /
# job_id / elapsed_seconds / eta_seconds / slow，池 preparing 按最新
# 池单行给 stage，failed 分支补查最新池失败单。
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_eta_cache():
    # ETA 分位数是进程内缓存：每个用例前后复位，避免上一个内存库的
    # 旧值漏进下一个用例。
    ps_api._reset_eta_cache()
    yield
    ps_api._reset_eta_cache()


def _pool_job(db, item_id, status, error_kind=None, created_at=None,
              owner_ref="member-1"):
    from datetime import datetime
    from backend.emby_server import models as em
    j = em.TransferJob(item_id=item_id, trigger="play", provider="drive",
                       owner_scope="pool", owner_ref=owner_ref,
                       status=status, error_kind=error_kind)
    if created_at is not None:
        j.created_at = created_at
        j.updated_at = created_at
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _pool_context(monkeypatch):
    from backend.emby_server import pool_gate, playback_gate
    monkeypatch.setattr(pool_gate, "pool_eligible",
                        lambda db_, u, i: "eligible")
    monkeypatch.setattr(pool_gate, "_servable_pool_copy_count",
                        lambda db_, iid: 0)
    monkeypatch.setattr(playback_gate, "has_active_binding",
                        lambda db_, uid: False)


def _fixed_quantiles(monkeypatch):
    monkeypatch.setattr(
        ps_api, "_eta_quantiles",
        lambda db: {"pool": (23, 34), "user": (6, 26)})


def test_idle_fields_contract(db, user, client):
    it = _item(db)
    data = _get(client, it.id)
    assert data["state"] == "idle"
    assert data["route"] is None
    assert data["job_id"] is None
    assert data["elapsed_seconds"] is None
    assert data["eta_seconds"] is None
    assert data["slow"] is False


def test_pool_preparing_queued(db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _pool_context(monkeypatch)
    _fixed_quantiles(monkeypatch)
    it = _item(db)
    job = _pool_job(db, it.id, "pending",
                    created_at=datetime.now() - timedelta(seconds=10))
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "queued"
    assert data["route"] == "pool"
    assert data["job_id"] == job.id
    assert data["progress_pct"] is None
    assert 9 <= data["elapsed_seconds"] <= 11
    assert 12 <= data["eta_seconds"] <= 14
    assert data["slow"] is False


def test_pool_preparing_working(db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _pool_context(monkeypatch)
    _fixed_quantiles(monkeypatch)
    it = _item(db)
    _pool_job(db, it.id, "running",
              created_at=datetime.now() - timedelta(seconds=5))
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "working"
    assert data["route"] == "pool"
    assert 17 <= data["eta_seconds"] <= 19


def test_pool_preparing_no_inflight_not_preparing(
        db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it = _item(db)
    data = _get(client, it.id)
    assert data["state"] == "idle"


def test_own_preparing_eta_fields(db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _fixed_quantiles(monkeypatch)
    it = _item(db)
    job = _job(db, it.id, user.id, "running")
    # 把建单时间拨到 2 秒前：本人 p50=6，remaining=4，下限 5。
    job.created_at = datetime.now() - timedelta(seconds=2)
    db.commit()
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["route"] == "own"
    assert data["job_id"] == job.id
    assert data["eta_seconds"] == 5
    assert data["slow"] is False


def test_eta_slow_when_elapsed_beyond_p90(db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _pool_context(monkeypatch)
    _fixed_quantiles(monkeypatch)
    it = _item(db)
    _pool_job(db, it.id, "running",
              created_at=datetime.now() - timedelta(seconds=40))
    data = _get(client, it.id)
    assert data["state"] == "preparing"
    assert data["eta_seconds"] is None
    assert data["slow"] is True


def test_eta_fallback_to_config_when_no_history(
        db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _pool_context(monkeypatch)
    monkeypatch.setattr(ps_api, "_compute_eta_quantiles", lambda db: {})
    it = _item(db)
    _pool_job(db, it.id, "running",
              created_at=datetime.now() - timedelta(seconds=10))
    data = _get(client, it.id)
    # 无历史分位数时用配置兜底（池 23/34）：eta = 23 - 10 = 13。
    assert 12 <= data["eta_seconds"] <= 14
    assert data["slow"] is False


def test_eta_quantiles_computed_from_done_jobs(db):
    from datetime import datetime, timedelta
    base = datetime.now() - timedelta(days=1)
    for secs in (10, 20, 30, 40):
        # 池 done 单：持续 secs 秒（updated_at - created_at）
        j = _pool_job(db, 9001, "done", created_at=base,
                      owner_ref="m-%d" % secs)
        j.updated_at = base + timedelta(seconds=secs)
        db.commit()
    for secs in (2, 4, 6):
        j = _job(db, 9002, 1, "done")
        j.created_at = base
        j.updated_at = base + timedelta(seconds=secs)
        db.commit()
    got = ps_api._compute_eta_quantiles(db)
    assert got["pool"] == (20, 40)
    assert got["user"] == (4, 6)


def test_eta_quantiles_cached_within_ttl(db, monkeypatch):
    calls = {"n": 0}

    def _counting(db_):
        calls["n"] += 1
        return {"pool": (23, 34), "user": (6, 26)}

    monkeypatch.setattr(ps_api, "_compute_eta_quantiles", _counting)
    ps_api._reset_eta_cache()
    ps_api._eta_quantiles(db)
    ps_api._eta_quantiles(db)
    assert calls["n"] == 1
    ps_api._reset_eta_cache()
    ps_api._eta_quantiles(db)
    assert calls["n"] == 2


def test_pool_failed_copy_forbidden(db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it = _item(db)
    job = _pool_job(db, it.id, "failed", error_kind="copy_forbidden")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "pool_source"
    assert data["route"] == "pool"
    assert data["job_id"] == job.id


def test_pool_failed_space_uses_pool_route(db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it = _item(db)
    _pool_job(db, it.id, "failed", error_kind="insufficient_space")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "space"
    assert data["route"] == "pool"


def test_pool_failed_no_member(db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it = _item(db)
    _pool_job(db, it.id, "failed", error_kind="no_member")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "pool_no_member"
    assert data["route"] == "pool"


def test_own_failed_beats_pool_failed(db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it = _item(db)
    _job(db, it.id, user.id, "failed", error_kind="dead")
    _pool_job(db, it.id, "failed", error_kind="copy_forbidden")
    data = _get(client, it.id)
    assert data["state"] == "failed"
    assert data["reason"] == "source_dead"
    assert data["route"] == "own"


def test_pool_failed_hidden_for_bound_user(db, user, client, monkeypatch):
    from backend.emby_server import pool_gate, playback_gate
    monkeypatch.setattr(pool_gate, "pool_eligible",
                        lambda db_, u, i: "eligible")
    monkeypatch.setattr(pool_gate, "_servable_pool_copy_count",
                        lambda db_, iid: 0)
    monkeypatch.setattr(playback_gate, "has_active_binding",
                        lambda db_, uid: True)
    monkeypatch.setattr(pool_gate, "large_file_pool_route",
                        lambda db_, u, i: False)
    it = _item(db)
    _pool_job(db, it.id, "failed", error_kind="copy_forbidden")
    data = _get(client, it.id)
    assert data["state"] == "idle"


def test_pool_failed_other_item_does_not_leak(
        db, user, client, monkeypatch):
    _pool_context(monkeypatch)
    it_a = _item(db)
    it_b = _item(db)
    _pool_job(db, it_a.id, "failed", error_kind="copy_forbidden")
    data = _get(client, it_b.id)
    assert data["state"] == "idle"


def test_series_aggregates_episode_pool_preparing(
        db, user, client, monkeypatch):
    from datetime import datetime, timedelta
    _pool_context(monkeypatch)
    _fixed_quantiles(monkeypatch)
    series = _item(db, item_type="series", name="测试剧3")
    ep = _item(db, item_type="episode", series_id=series.id, name="E1")
    _pool_job(db, ep.id, "running",
              created_at=datetime.now() - timedelta(seconds=5))
    data = _get(client, series.id)
    assert data["state"] == "preparing"
    assert data["stage"] == "working"
    assert data["route"] == "pool"
    assert 17 <= data["eta_seconds"] <= 19


def test_pool_eta_config_family_registered():
    from backend import config_service

    specs = config_service.SCHEMAS.get("play_status_eta")
    assert specs is not None
    assert set(specs) == {
        "pool_eta_p50_sec", "pool_eta_p90_sec",
        "user_eta_p50_sec", "user_eta_p90_sec",
        "play_status_eta_cache_sec",
    }
    assert specs["pool_eta_p50_sec"] == ("int", 1, 3600)
    assert specs["pool_eta_p90_sec"] == ("int", 1, 3600)
    assert specs["user_eta_p50_sec"] == ("int", 1, 3600)
    assert specs["user_eta_p90_sec"] == ("int", 1, 3600)
    assert specs["play_status_eta_cache_sec"] == ("int", 60, 3600)
