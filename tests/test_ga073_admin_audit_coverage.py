"""GA-073：管理员写接口操作审计留痕回归。

PR #58 的覆盖脚本实跑揪出 20 个管理员写口调用后 AdminLog 零记录
（media_sets_api 15 + panel 2 + governance_report 3；其中 media_sets
部分已随合集功能于 2026-11-17 删除）。本文件逐个
端点直调，断言每个动作落一行 AdminLog：actor=当前管理员、
action/target 与既有统一口径（backend.api.admin_core._audit）一致。

隔离口径：本文件自建内存 SQLite + 自有 session，不碰全局 engine /
SessionLocal（导入期更不换绑，防 #1668 同族污染）。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import subscriptions
from backend.api import governance_report as gov_api
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    previous = subscriptions._realm_resolver
    subscriptions.set_process_realm_resolver(None)
    try:
        yield session
    finally:
        subscriptions.set_process_realm_resolver(previous)
        session.close()
        engine.dispose()


@pytest.fixture()
def admin(db):
    user = models.WebUser(username="ga073-admin", password_hash="x",
                          is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _logs(db, action):
    return (db.query(models.AdminLog)
              .filter(models.AdminLog.action == action).all())


def _assert_one_log(db, admin, action, target_type, target_id):
    rows = _logs(db, action)
    assert len(rows) == 1, f"{action} 应落且只落一行 AdminLog，实得 {len(rows)}"
    row = rows[0]
    assert row.admin_user_id == admin.id
    assert row.target_type == target_type
    assert row.target_id == target_id
    return row


# ---------------------------------------------------------------- governance

def _seed_governance(db):
    realm = models.ServerRealm(name="公益服", slug="main",
                               access_mode="free", is_active=True)
    db.add(realm)
    db.commit()
    db.add(models.SystemConfig(key="active_realm_id", value=str(realm.id)))
    reporter = models.WebUser(username="reporter", password_hash="x",
                              is_active=True)
    target = models.WebUser(username="target", password_hash="x",
                            is_active=True)
    db.add_all([reporter, target])
    db.commit()
    db.refresh(reporter)
    db.refresh(target)
    return reporter, target


def test_governance_report_decide_audits(db, admin):
    reporter, _target = _seed_governance(db)
    report = models.GovernanceReport(
        reporter_id=reporter.id, target_type="media_seek", target_id=1,
        reason="spam", status="escalated")
    db.add(report)
    db.commit()
    db.refresh(report)
    gov_api.admin_decide_report(
        report.id, gov_api.DecideIn(decision="accepted"), db, admin)
    row = _assert_one_log(db, admin, "governance_report_decide",
                          "governance_report", report.id)
    assert row.details["decision"] == "accepted"


def test_governance_suspension_create_audits(db, admin):
    _reporter, target = _seed_governance(db)
    out = gov_api.admin_create_suspension(
        gov_api.SuspendIn(user_id=target.id, days=7), db, admin)
    row = _assert_one_log(db, admin, "governance_suspension_create",
                          "user", target.id)
    assert row.details["days"] == 7
    assert row.details["suspension_id"] == out["id"]


def test_governance_suspension_revoke_audits(db, admin):
    _reporter, target = _seed_governance(db)
    out = gov_api.admin_create_suspension(
        gov_api.SuspendIn(user_id=target.id, days=7), db, admin)
    db.query(models.AdminLog).delete()
    db.commit()
    gov_api.admin_revoke_suspension(out["id"], db, admin)
    _assert_one_log(db, admin, "governance_suspension_revoke",
                    "governance_suspension", out["id"])
