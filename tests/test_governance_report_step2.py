"""等级公益服活动治理 Step 2：举报＋复核＋终审队列测试。

仿 tests/test_store_requests.py 的夹具范式：独立 FastAPI app＋TestClient＋
dependency_overrides（get_db／get_current_user／get_current_admin），
sqlite://＋StaticPool＋models.Base.metadata.create_all。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("WELCOME_FILE_DIR", "/tmp")

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import governance_report as gov
from backend import models
from backend import subscriptions
from backend.api.admin_core import get_current_admin
from backend.api.governance_report import admin_router, router
from backend.api.user import get_current_user
from backend.database import get_db


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine)()
    # 全量 pytest 里别的用例可能装过「本进程的服」，清掉解析器，结束再装回去。
    previous = subscriptions._realm_resolver
    subscriptions.set_process_realm_resolver(None)
    try:
        yield session
    finally:
        subscriptions.set_process_realm_resolver(previous)
        session.close()


@pytest.fixture()
def client(engine):
    application = FastAPI()
    application.include_router(router)
    application.include_router(admin_router)
    sessions = sessionmaker(bind=engine)

    def _override_get_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_get_db
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def _user(db, username, level=1, staff=False):
    # level 参数保留兼容旧调用点：member_level 列已删除，复核权重走
    # getattr(user, "member_level", None) 兜底恒为 Lv.1 权重 0，
    # 本参数不再传入构造器。
    user = models.WebUser(
        username=username,
        password_hash="x",
        is_active=True,
        is_staff=staff,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _realm(db, name, slug, access_mode, active=True):
    realm = models.ServerRealm(
        name=name, slug=slug, access_mode=access_mode, is_active=active
    )
    db.add(realm)
    db.commit()
    db.refresh(realm)
    return realm


def _free_realm(db):
    """公益服设为当前服（active_realm_id 指向它）。"""
    realm = _realm(db, "公益服", "main", "free")
    db.add(models.SystemConfig(key="active_realm_id", value=str(realm.id)))
    db.commit()
    return realm


def _paid_realm(db):
    return _realm(db, "付费服", "paid", "paid")


def _seek(db, user, name, realm_id=None):
    row = models.MovieRequest(
        user_id=user.id,
        movie_name=name,
        status="pending",
        realm_id=realm_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _set_config(db, key, value):
    row = (
        db.query(models.SystemConfig)
        .filter(models.SystemConfig.key == key)
        .first()
    )
    if row is None:
        db.add(models.SystemConfig(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()


def _as(client, user):
    client.app.dependency_overrides[get_current_user] = lambda: user
    return client


def _as_admin(client, admin):
    client.app.dependency_overrides[get_current_admin] = lambda: admin
    return client


def _report(client, user, seek_id, reason="spam", detail=None):
    resp = _as(client, user).post(
        "/api/user/governance/reports",
        json={"target_id": seek_id, "reason": reason, "detail": detail},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _vote(client, user, report_id, decision):
    return _as(client, user).post(
        f"/api/user/governance/reports/{report_id}/review-votes",
        json={"decision": decision},
    )


def _escalate(client, db, report_id, reviewers):
    """把举报行直接置 escalated 并返回状态 dict。

    原实现靠 3 名 Lv.3/4/5 复核员各投同意票达 quorum 升级；等级列删除后
    复核权重恒 0、投票恒 403，升级态改由直接播种状态驱动，终审/队列/
    停权等下游断言口径不变。
    """
    row = db.query(models.GovernanceReport).filter_by(id=report_id).first()
    row.status = "escalated"
    row.escalated_at = datetime.now()
    db.commit()
    return {"id": report_id, "status": "escalated"}


def _decide(client, admin, report_id, decision, note=None):
    return _as_admin(client, admin).post(
        f"/api/admin/governance/reports/{report_id}/decide",
        json={"decision": decision, "note": note},
    )


def _setup_free(db, client):
    """公益服＋管理员＋举报人＋3 名复核员（旧口径 Lv.3/Lv.4/Lv.5，等级列
    删除后三人权重恒 0）＋一条求片。"""
    realm = _free_realm(db)
    admin = _user(db, "admin", level=6, staff=True)
    reporter = _user(db, "reporter", level=1)
    reviewers = [
        _user(db, "rv3", level=3),
        _user(db, "rv4", level=4),
        _user(db, "rv5", level=5),
    ]
    seek = _seek(db, reporter, "求片A", realm_id=realm.id)
    return realm, admin, reporter, reviewers, seek


# ---------------- 举报创建与范围限制 ----------------


def test_create_report_ok(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id, reason="spam", detail="刷屏")
    assert body["status"] == "pending"
    assert body["reporter_id"] == reporter.id
    assert body["target_type"] == "media_seek"
    assert body["target_id"] == seek.id
    assert body["reason"] == "spam"
    assert body["detail"] == "刷屏"
    assert body["ballots"] == 0
    assert body["agree_weight"] == 0


def test_create_report_invalid_reason(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "rude"},
    )
    assert resp.status_code == 400


def test_create_report_target_missing(db, client):
    _, _, reporter, _, _ = _setup_free(db, client)
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": 99999, "reason": "spam"},
    )
    assert resp.status_code == 404


def test_create_report_duplicate(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    _report(client, reporter, seek.id)
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "duplicate"},
    )
    assert resp.status_code == 409


def test_create_report_paid_realm_forbidden(db, client):
    _free_realm(db)
    paid = _paid_realm(db)
    reporter = _user(db, "reporter", level=1)
    seek = _seek(db, reporter, "付费服求片", realm_id=paid.id)
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "spam"},
    )
    assert resp.status_code == 403


def test_create_report_switch_off(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    _set_config(db, "governance_report_enabled", "0")
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "spam"},
    )
    assert resp.status_code == 403


def test_create_report_suspended_forbidden(db, client):
    _, admin, reporter, _, seek = _setup_free(db, client)
    resp = _as_admin(client, admin).post(
        "/api/admin/governance/suspensions",
        json={"user_id": reporter.id, "days": 30, "note": "手动停权"},
    )
    assert resp.status_code == 201, resp.text
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "spam"},
    )
    assert resp.status_code == 403


# ---------------- 列表与详情可见性 ----------------


def test_my_reports_only_mine(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    other = _user(db, "other", level=1)
    _report(client, reporter, seek.id)
    _report(client, other, seek.id)
    mine = _as(client, reporter).get("/api/user/governance/reports").json()
    assert len(mine) == 1
    assert mine[0]["reporter_id"] == reporter.id


def test_report_detail_visibility(db, client):
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    report_id = body["id"]
    # 举报人可看，无 votes 明细
    mine = _as(client, reporter).get(
        f"/api/user/governance/reports/{report_id}"
    )
    assert mine.status_code == 200
    assert "votes" not in mine.json()
    # 无关用户 404（不泄露举报人）
    other = _user(db, "other", level=1)
    resp = _as(client, other).get(f"/api/user/governance/reports/{report_id}")
    assert resp.status_code == 404
    # 管理员可看，含复核票明细：复核投票已恒被权重门 403（无等级来源），
    # 票明细为空（原断言为投票成功后 votes 长度 1 且 reviewer 对应）
    resp = _vote(client, reviewers[0], report_id, "agree")
    assert resp.status_code == 403
    detail = _as_admin(client, admin).get(
        f"/api/admin/governance/reports/{report_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["votes"] == []


# ---------------- 复核投票 ----------------


def test_review_vote_low_level_forbidden(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    low = _user(db, "low", level=2)
    body = _report(client, reporter, seek.id)
    resp = _vote(client, low, body["id"], "agree")
    assert resp.status_code == 403


def test_review_vote_self_forbidden(db, client):
    _, _, reporter, _, seek = _setup_free(db, client)
    # 保留属性赋值（非构造 kwarg）：复核权重走 getattr(user, "member_level")
    # 兜底 seam，赋 6 后权重门可过、才能测到其后的自投禁令 403。
    reporter.member_level = 6
    db.commit()
    body = _report(client, reporter, seek.id)
    resp = _vote(client, reporter, body["id"], "agree")
    assert resp.status_code == 403


def test_review_vote_duplicate(db, client):
    # 原口径：首票 200、重票 409。等级列删除后权重恒 0，两次投票都在
    # 权重门 403、不落票行（重复票校验已不可达）。
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    assert _vote(client, reviewers[0], body["id"], "agree").status_code == 403
    resp = _vote(client, reviewers[0], body["id"], "disagree")
    assert resp.status_code == 403
    assert db.query(models.GovernanceReviewVote).count() == 0


def test_review_vote_bad_decision(db, client):
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    resp = _vote(client, reviewers[0], body["id"], "maybe")
    assert resp.status_code == 400


def test_review_vote_switch_off(db, client):
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    _set_config(db, "governance_report_enabled", "0")
    resp = _vote(client, reviewers[0], body["id"], "agree")
    assert resp.status_code == 403


def test_review_vote_paid_realm_forbidden(db, client):
    _free_realm(db)
    paid = _paid_realm(db)
    reporter = _user(db, "reporter", level=1)
    seek = _seek(db, reporter, "付费服求片", realm_id=paid.id)
    # 绕过创建门控直接插举报行，复核入口仍须按目标服判定
    row = models.GovernanceReport(
        reporter_id=reporter.id,
        target_type="media_seek",
        target_id=seek.id,
        reason="spam",
        status="pending",
    )
    db.add(row)
    db.commit()
    reviewer = _user(db, "rv3", level=3)
    resp = _vote(client, reviewer, row.id, "agree")
    assert resp.status_code == 403


# ---------------- quorum／阈值计票 ----------------


def test_escalation_all_agree(db, client):
    # 原口径：3 名复核员（Lv.3/4/5，权重 1+2+2=5）全同意达 quorum 升级、
    # 进终审队列。等级列删除后复核权重恒 0（getattr 兜底 None→Lv.1），
    # 所有复核投票都在权重门 403、不落票行，举报恒 pending、不进队列。
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    for reviewer in reviewers:
        resp = _vote(client, reviewer, body["id"], "agree")
        assert resp.status_code == 403
    assert db.query(models.GovernanceReviewVote).count() == 0
    row = db.query(models.GovernanceReport).filter_by(id=body["id"]).first()
    assert row.status == "pending"
    assert row.escalated_at is None
    queue = _as_admin(client, admin).get("/api/admin/governance/reports/queue")
    assert queue.status_code == 200
    assert queue.json() == []


def test_quorum_counts_people_not_weight(db, client):
    # 原口径：两名 Lv.6 同意（权重 6）人数不足 quorum 仍 pending，
    # 第三人否决后按权重比升级。现无等级来源：三票全在权重门 403，
    # quorum/权重计票已不可达，举报恒 pending。
    _, _, reporter, _, seek = _setup_free(db, client)
    lv6a = _user(db, "lv6a", level=6)
    lv6b = _user(db, "lv6b", level=6)
    lv3 = _user(db, "lv3", level=3)
    body = _report(client, reporter, seek.id)
    for voter, decision in ((lv6a, "agree"), (lv6b, "agree"), (lv3, "disagree")):
        resp = _vote(client, voter, body["id"], decision)
        assert resp.status_code == 403
    row = db.query(models.GovernanceReport).filter_by(id=body["id"]).first()
    assert row.status == "pending"
    assert db.query(models.GovernanceReviewVote).count() == 0


def test_disagree_majority_closes(db, client):
    # 原口径：3 名复核员全否决 → review_rejected 关闭、再投 409。
    # 现否决票同样恒 403 在权重门，举报不关闭、恒 pending。
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    for reviewer in reviewers:
        resp = _vote(client, reviewer, body["id"], "disagree")
        assert resp.status_code == 403
    row = db.query(models.GovernanceReport).filter_by(id=body["id"]).first()
    assert row.status == "pending"
    extra = _user(db, "extra", level=6)
    resp = _vote(client, extra, body["id"], "agree")
    assert resp.status_code == 403


def test_weight_snapshot_survives_level_change(db, client):
    # 原口径：复核员投票后升 Lv.6，其快照权重仍为投票时刻的 1，
    # 三票加权和 5 升级。现投票恒 403 在权重门、无票行可快照，
    # 管理员详情 votes 为空、举报恒 pending。
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    resp = _vote(client, reviewers[0], body["id"], "agree")
    assert resp.status_code == 403
    reviewers[0].member_level = 6
    db.commit()
    for reviewer in reviewers[1:]:
        resp = _vote(client, reviewer, body["id"], "agree")
        assert resp.status_code == 403
    detail = _as_admin(client, admin).get(
        f"/api/admin/governance/reports/{body['id']}"
    )
    assert detail.json()["votes"] == []
    assert detail.json()["status"] == "pending"


def test_config_custom_quorum(db, client):
    # 原口径：quorum 配置改 2 后两票即升级。现投票恒 403 在权重门，
    # quorum 配置不再能改变结果，举报恒 pending。
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    _set_config(db, "governance_review_quorum", "2")
    body = _report(client, reporter, seek.id)
    for reviewer in reviewers[:2]:
        resp = _vote(client, reviewer, body["id"], "agree")
        assert resp.status_code == 403
    row = db.query(models.GovernanceReport).filter_by(id=body["id"]).first()
    assert row.status == "pending"


# ---------------- 终审 ----------------


def test_decide_accept(db, client):
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    _escalate(client, db, body["id"], reviewers)
    resp = _decide(client, admin, body["id"], "accepted", note="属实")
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "accepted"
    assert out["admin_note"] == "属实"
    assert out["admin_id"] == admin.id
    assert out["decided_at"] is not None
    # 受理不自动处理求片本身
    db.expire_all()
    row = db.query(models.MovieRequest).filter_by(id=seek.id).first()
    assert row.status == "pending"


def test_decide_reject(db, client):
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    _escalate(client, db, body["id"], reviewers)
    resp = _decide(client, admin, body["id"], "rejected", note="不属实")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert "auto_suspension" not in resp.json()


def test_decide_non_escalated_rejected(db, client):
    _, admin, reporter, _, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    resp = _decide(client, admin, body["id"], "accepted")
    assert resp.status_code == 409


def test_decide_bad_decision(db, client):
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    _escalate(client, db, body["id"], reviewers)
    resp = _decide(client, admin, body["id"], "maybe")
    assert resp.status_code == 400


# ---------------- 停权：自动触发／到期恢复／手动 ----------------


def _reject_five(db, client, admin, reporter, reviewers):
    """举报人 5 次举报、逐条置升级态（_escalate 播种）并终审驳回；
    返回最后一次终审响应。"""
    last = None
    for i in range(5):
        seek = _seek(db, reporter, f"求片{i}", realm_id=None)
        body = _report(client, reporter, seek.id)
        _escalate(client, db, body["id"], reviewers)
        last = _decide(client, admin, body["id"], "rejected")
        assert last.status_code == 200, last.text
    return last


def test_abuse_auto_suspension_on_fifth_reject(db, client):
    _, admin, reporter, reviewers, _ = _setup_free(db, client)
    last = _reject_five(db, client, admin, reporter, reviewers)
    # 第 5 次驳回触发自动停权，随响应返回
    assert "auto_suspension" in last.json()
    susp = last.json()["auto_suspension"]
    assert susp["reason"] == "auto_abuse"
    assert susp["user_id"] == reporter.id
    # 停权期内新建举报与复核皆 403
    seek = _seek(db, reporter, "新求片", realm_id=None)
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "spam"},
    )
    assert resp.status_code == 403
    other_report = _report(client, _user(db, "other2", level=1), seek.id)
    resp = _vote(client, reporter, other_report["id"], "agree")
    assert resp.status_code == 403


def test_abuse_no_double_suspension(db, client):
    _, admin, reporter, reviewers, _ = _setup_free(db, client)
    _reject_five(db, client, admin, reporter, reviewers)
    # 已有生效停权时再驳回，不重复建、不顺延
    seek = _seek(db, reporter, "求片X", realm_id=None)
    row = models.GovernanceReport(
        reporter_id=reporter.id,
        target_type="media_seek",
        target_id=seek.id,
        reason="spam",
        status="escalated",
    )
    db.add(row)
    db.commit()
    resp = _decide(client, admin, row.id, "rejected")
    assert resp.status_code == 200
    assert "auto_suspension" not in resp.json()
    rows = (
        db.query(models.GovernanceSuspension)
        .filter(models.GovernanceSuspension.user_id == reporter.id)
        .all()
    )
    assert len(rows) == 1


def test_abuse_window_excludes_old_rejects(db, client):
    _, admin, reporter, reviewers, _ = _setup_free(db, client)
    # 4 次旧驳回（decided_at 回拨 31 天）＋1 次新驳回：窗口内仅 1 次，不触发
    for i in range(4):
        seek = _seek(db, reporter, f"旧求片{i}", realm_id=None)
        body = _report(client, reporter, seek.id)
        _escalate(client, db, body["id"], reviewers)
        resp = _decide(client, admin, body["id"], "rejected")
        assert resp.status_code == 200
        row = (
            db.query(models.GovernanceReport)
            .filter_by(id=body["id"])
            .first()
        )
        row.decided_at = datetime.now() - timedelta(days=31)
        db.commit()
    seek = _seek(db, reporter, "新求片", realm_id=None)
    body = _report(client, reporter, seek.id)
    _escalate(client, db, body["id"], reviewers)
    resp = _decide(client, admin, body["id"], "rejected")
    assert resp.status_code == 200
    assert "auto_suspension" not in resp.json()


def test_suspension_expiry_recovers(db, client):
    _, admin, reporter, _, seek = _setup_free(db, client)
    resp = _as_admin(client, admin).post(
        "/api/admin/governance/suspensions",
        json={"user_id": reporter.id, "days": 30},
    )
    assert resp.status_code == 201
    susp_id = resp.json()["id"]
    resp = _as(client, reporter).post(
        "/api/user/governance/reports",
        json={"target_id": seek.id, "reason": "spam"},
    )
    assert resp.status_code == 403
    # 停权过期后恢复
    row = (
        db.query(models.GovernanceSuspension).filter_by(id=susp_id).first()
    )
    row.ends_at = datetime.now() - timedelta(days=1)
    db.commit()
    body = _report(client, reporter, seek.id)
    assert body["status"] == "pending"


def test_manual_suspend_unsuspend(db, client):
    _, admin, reporter, _, seek = _setup_free(db, client)
    resp = _as_admin(client, admin).post(
        "/api/admin/governance/suspensions",
        json={"user_id": reporter.id, "days": 7, "note": "手动"},
    )
    assert resp.status_code == 201
    susp_id = resp.json()["id"]
    assert resp.json()["reason"] == "manual"
    # 已有生效停权时再停 → 409
    resp = _as_admin(client, admin).post(
        "/api/admin/governance/suspensions",
        json={"user_id": reporter.id, "days": 7},
    )
    assert resp.status_code == 409
    # 撤销后恢复，再撤销 → 409
    resp = _as_admin(client, admin).post(
        f"/api/admin/governance/suspensions/{susp_id}/revoke"
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None
    body = _report(client, reporter, seek.id)
    assert body["status"] == "pending"
    resp = _as_admin(client, admin).post(
        f"/api/admin/governance/suspensions/{susp_id}/revoke"
    )
    assert resp.status_code == 409


def test_manual_suspend_missing_user(db, client):
    _, admin, _, _, _ = _setup_free(db, client)
    resp = _as_admin(client, admin).post(
        "/api/admin/governance/suspensions",
        json={"user_id": 99999, "days": 7},
    )
    assert resp.status_code == 404


def test_admin_suspension_list(db, client):
    _, admin, reporter, reviewers, _ = _setup_free(db, client)
    _reject_five(db, client, admin, reporter, reviewers)
    rows = _as_admin(client, admin).get(
        "/api/admin/governance/suspensions",
        params={"user_id": reporter.id, "active_only": True},
    )
    assert rows.status_code == 200
    assert len(rows.json()) == 1
    assert rows.json()[0]["reason"] == "auto_abuse"


# ---------------- 队列过滤 ----------------


def test_admin_queue_default_and_filter(db, client):
    _, admin, reporter, reviewers, seek = _setup_free(db, client)
    pending = _report(client, reporter, seek.id)
    seek2 = _seek(db, reporter, "求片B", realm_id=None)
    escalated = _report(client, reporter, seek2.id)
    _escalate(client, db, escalated["id"], reviewers)
    queue = _as_admin(client, admin).get("/api/admin/governance/reports/queue")
    assert [r["id"] for r in queue.json()] == [escalated["id"]]
    pend = _as_admin(client, admin).get(
        "/api/admin/governance/reports/queue", params={"status": "pending"}
    )
    assert [r["id"] for r in pend.json()] == [pending["id"]]


# ---------------- 硬线：不产生经验 ----------------


def test_governance_actions_do_not_grant_xp(db, client):
    _, _, reporter, reviewers, seek = _setup_free(db, client)
    body = _report(client, reporter, seek.id)
    _vote(client, reviewers[0], body["id"], "agree")
    db.expire_all()
    reporter_row = db.query(models.WebUser).filter_by(id=reporter.id).first()
    reviewer_row = (
        db.query(models.WebUser).filter_by(id=reviewers[0].id).first()
    )
    # member_xp 列已删除：以 getattr 兜底断言两行均无经验落点（恒 0）
    assert (getattr(reporter_row, "member_xp", 0) or 0) == 0
    assert (getattr(reviewer_row, "member_xp", 0) or 0) == 0


# ---------------- 权重表 ----------------


def test_review_weight_table():
    assert gov.review_weight(1) == 0
    assert gov.review_weight(2) == 0
    assert gov.review_weight(3) == 1
    assert gov.review_weight(4) == 2
    assert gov.review_weight(5) == 2
    assert gov.review_weight(6) == 3
    assert gov.review_weight(7) == 3
    assert gov.review_weight(None) == 0
