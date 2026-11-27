# -*- coding: utf-8 -*-
"""goal-38 审计第一步：双盘审计台账只读口 GET /api/admin/audit/events。

钉住的口径：
- 无管理员鉴权 → 401/403（与管理端其他口同口径）；
- 三路记录（决策留痕 / Drive 报错 / 起播取用事件）归一成
  同一行式合并返回，时间倒序，total 为各路命中数之和，
  limit/offset 分页口径一致；
- 筛选：source / category / user_id / member_id / binding_id /
  item_id / outcome（归一结果组，查询层预过滤）/ start / end；
- 贡献池下线后成员维度不再经成员表解析：决策表 member:<id>
  历史行只按 member_id 文本键命中，不带出 user/binding；
- Drive 报错只有账号哈希、无人/盘/片维度：按 user 筛时恒不
  命中（如实口径）。

全部在隔离临时 SQLite 里跑（照 test_admin_readonly_endpoints
的模块夹具卫生口径：换绑只在本模块用例期间生效、结束还原）。
"""
import os
import tempfile
from datetime import datetime, timedelta

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


_TABLES = (em.TransferDecisionRound,
           em.DriveApiErrorEvent, em.DrivePlayAccessEvent,
           base_models.WebUser)

_BASE = datetime(2026, 11, 9, 10, 0, 0)


def _at(minutes):
    return _BASE + timedelta(minutes=minutes)


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


@pytest.fixture()
def admin(db):
    u = base_models.WebUser(username="auditadmin", is_staff=True,
                            is_active=True, password_hash="test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def client(db):
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


@pytest.fixture()
def seeded(db):
    """三路各塞已知行，时间从早到晚（分钟偏移见 _at 参数）。

    mid 是历史池决策行的成员文本键（成员表已随贡献池下线，
    这里只用裸 id 验文本键匹配）。"""
    mid = 4242
    db.add_all([
        # 决策：个人盘门禁放行（user 藏在 candidates 里）
        em.TransferDecisionRound(
            scope="transfer_gate", resource_key="item:100",
            policy_hash="",
            candidates_json=(
                '{"item_id": 100, "user_id": 7, '
                '"verdict": "proceed", "reason": "has_ready_variant", '
                '"job_id": 9}'),
            decision="proceed", reason="has_ready_variant",
            execution_ref="9", created_at=_at(1)),
        # 决策：池熔断（member 文本键）
        em.TransferDecisionRound(
            scope="pool_breaker", resource_key=f"member:{mid}",
            policy_hash="", candidates_json="[]",
            decision="trip", reason="quota_hits=3>=3",
            created_at=_at(2)),
        # 决策：池门禁失败（另一个用户）
        em.TransferDecisionRound(
            scope="pool_gate", resource_key="item:100",
            policy_hash="",
            candidates_json=(
                '{"item_id": 100, "user_id": 8, "member_id": null, '
                '"verdict": "failed", "reason": "no_member", '
                '"job_id": null}'),
            decision="failed", reason="no_member", created_at=_at(3)),
        # Drive 报错（无人/盘/片维度）
        em.DriveApiErrorEvent(
            status_code=429, reason="rateLimitExceeded",
            domain="usageLimits", caller="transfer_variant",
            account_hash="abc123def456", created_at=_at(6)),
        # 起播取用：副本命中（用户 7）
        em.DrivePlayAccessEvent(
            user_id=7, binding_id=55, item_id=100,
            entry="video_stream", action="copy_resolve", result="hit",
            detail='{"variant_id": 3, "owner_scope": "user"}',
            created_at=_at(7)),
        # 起播取用：门禁被拒（用户 8）
        em.DrivePlayAccessEvent(
            user_id=8, binding_id=None, item_id=200,
            entry="playback_info", action="gate_reject",
            result="denied", reason="请先绑定自己的网盘账号才能点播",
            created_at=_at(8)),
    ])
    db.commit()
    return {"member_id": mid}


def _get(authed, **params):
    r = authed.get("/api/admin/audit/events", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------

def test_audit_events_require_auth(client, seeded):
    r = client.get("/api/admin/audit/events")
    assert r.status_code in (401, 403)


def test_audit_events_merge_all_sources(authed, seeded):
    body = _get(authed)
    assert body["total"] == 6
    items = body["items"]
    assert len(items) == 6
    sources = [it["source"] for it in items]
    assert sources.count("decision") == 3
    assert sources.count("drive_error") == 1
    assert sources.count("play_access") == 2
    # 时间倒序：最新一行是用户 8 的门禁被拒
    assert items[0]["source"] == "play_access"
    assert items[0]["result"] == "denied"
    # 历史池决策行只剩 member 文本键：带 member_id，不再解析
    # 出 user/binding（成员表已下线）
    breaker = next(it for it in items if it["category"] == "pool_breaker")
    assert breaker["user_id"] is None
    assert breaker["binding_id"] is None
    assert breaker["member_id"] == seeded["member_id"]
    assert breaker["outcome"] == "changed"


def test_audit_events_source_and_category_filters(authed, seeded):
    assert _get(authed, source="decision")["total"] == 3
    assert _get(authed, source="play_access")["total"] == 2
    body = _get(authed, category="pool_breaker")
    assert body["total"] == 1
    assert body["items"][0]["category"] == "pool_breaker"
    assert _get(authed, category="gate_reject")["total"] == 1
    r = authed.get("/api/admin/audit/events",
                   params={"source": "nope"})
    assert r.status_code == 400


def test_audit_events_user_filter_crosses_sources(authed, seeded):
    body = _get(authed, user_id=7)
    # 决策 1（门禁 candidates）+ 取用 1；历史熔断行 candidates
    # 里没 user、成员表下线后不再解析；Drive 报错无人维度恒不命中
    assert body["total"] == 2
    assert all(it["user_id"] == 7 for it in body["items"])
    body = _get(authed, user_id=8)
    assert body["total"] == 2


def test_audit_events_binding_member_item_filters(authed, seeded):
    mid = seeded["member_id"]
    # binding 维度：决策路无成员表可互查恒不命中，只剩取用 1
    assert _get(authed, binding_id=55)["total"] == 1
    # member 维度：只命中历史熔断决策行（文本键）
    assert _get(authed, member_id=mid)["total"] == 1
    body = _get(authed, item_id=100)
    assert body["total"] == 3
    assert all(it["item_id"] == 100 for it in body["items"])


def test_audit_events_outcome_filter(authed, seeded):
    assert _get(authed, outcome="success")["total"] == 2
    assert _get(authed, outcome="rejected")["total"] == 2
    assert _get(authed, outcome="changed")["total"] == 1
    body = _get(authed, outcome="error")
    assert body["total"] == 1
    assert body["items"][0]["source"] == "drive_error"
    r = authed.get("/api/admin/audit/events",
                   params={"outcome": "nope"})
    assert r.status_code == 400


def test_audit_events_time_range_and_pagination(authed, seeded):
    body = _get(authed, start=_at(3).isoformat(),
                end=_at(6).isoformat())
    # [03:00, 06:00) → 历史池门禁失败决策 = 1
    assert body["total"] == 1
    page1 = _get(authed, limit=3, offset=0)
    page2 = _get(authed, limit=3, offset=3)
    assert page1["total"] == 6 and len(page1["items"]) == 3
    assert len(page2["items"]) == 3
    ids1 = {it["id"] for it in page1["items"]}
    ids2 = {it["id"] for it in page2["items"]}
    assert not (ids1 & ids2)
