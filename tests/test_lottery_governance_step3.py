"""等级治理 Step 3：活动提案与直接发起（backend.lottery_governance）隔离测试。

沿用 tests/test_lottery_round.py 的内存 SQLite + 只建表模式。
覆盖：各等级提案/发起权限、月限、提案批准流（批准建轮次/拒绝/重复处理）、
事后审计记录（提案行/直接发起行/AdminLog）、free realm 门、原有抽奖路径不受影响、
提案动作不产生经验。
"""
import sys

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base  # noqa: F401 保持与其它测试相同的导入习惯
from backend import lottery_governance as gov
from backend import models


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    # 只建需要的表
    models.WebUser.__table__.create(engine, checkfirst=True)
    models.SystemConfig.__table__.create(engine, checkfirst=True)
    models.ServerRealm.__table__.create(engine, checkfirst=True)
    models.LotteryRound.__table__.create(engine, checkfirst=True)
    models.LotteryRoundPrize.__table__.create(engine, checkfirst=True)
    models.LotteryActivityProposal.__table__.create(engine, checkfirst=True)
    models.LotteryDirectLaunch.__table__.create(engine, checkfirst=True)
    # 停权接线后 submit/direct 入口会读停权表，未建表会在此夹具下报缺表。
    models.GovernanceFreeze.__table__.create(engine, checkfirst=True)
    models.AdminLog.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _set_config(db, key, value):
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row is None:
        db.add(models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _make_realm(db, access_mode="free"):
    realm = models.ServerRealm(
        name="公益服" if access_mode == "free" else "付费服",
        slug="main",
        access_mode=access_mode,
        is_active=True,
    )
    db.add(realm)
    db.commit()
    db.refresh(realm)
    _set_config(db, "active_realm_id", str(realm.id))
    return realm


def _make_user(db, username, xp=0, tg_id=None):
    # xp 参数保留兼容旧调用点：member_xp 列已删除、经验无落库来源，
    # user_level 恒为 Lv.1，本参数不再传入构造器。
    user = models.WebUser(
        username=username,
        password_hash="",
        telegram_id=tg_id if tg_id is not None else abs(hash(username)) % 10**9,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_proposal(db, user, status="pending", title="用户提案活动", draw_at=None):
    """直接播种提案行（提交入口已恒被等级门拦下，批准/拒绝/列表等
    下游流程改由播种行驱动，断言口径不变）。"""
    import json

    row = models.LotteryActivityProposal(
        user_id=user.id,
        title=title,
        chat_id=999,
        lottery_type="button",
        password_keyword=None,
        prizes_json=json.dumps(_payload()["prizes"], ensure_ascii=False),
        draw_at=draw_at,
        max_participants=0,
        level_snapshot=1,
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _enable_lottery(db, groups="999"):
    _set_config(db, "lottery_enabled", "1")
    _set_config(db, "lottery_group_ids", groups)


def _payload(**over):
    p = {
        "title": "用户提案活动",
        "chat_id": 999,
        "prizes": [{"name": "天数奖", "type": "days", "value": 30, "quantity": 1}],
        "draw_at": None,
        "max_participants": None,
        "lottery_type": "button",
        "password_keyword": None,
    }
    p.update(over)
    return p


def _setup_free(db):
    _make_realm(db, "free")
    _enable_lottery(db)


# ==================== 等级判定 ====================


def test_user_level_thresholds(db):
    # 等级体系删除后经验无落库来源：无论旧 xp 入参多大，user_level 恒 Lv.1
    # （原断言按阈值表 0/100/500/1500/5000/15000 分别期望 Lv.1–Lv.6）。
    _setup_free(db)
    cases = [(0, 1), (99, 1), (100, 1), (499, 1), (500, 1), (1499, 1),
             (1500, 1), (4999, 1), (5000, 1), (14999, 1), (15000, 1), (10**9, 1)]
    for xp, expected in cases:
        user = _make_user(db, f"u{xp}", xp=xp)
        assert gov.user_level(db, user.id) == expected


def test_user_level_always_lv1_without_xp_source(db):
    # 原 test_user_level_falls_back_to_defaults_when_table_empty：
    # member_levels 表已随 MemberLevel 模型删除，不再有「表空回落默认阈值」
    # 后按 xp=5000 得 Lv.5 的路径——恒 Lv.1。
    _make_realm(db, "free")
    _enable_lottery(db)
    user = _make_user(db, "lv5", xp=5000)
    assert gov.user_level(db, user.id) == 1


def test_user_level_does_not_write_back(db):
    _setup_free(db)
    user = _make_user(db, "lv5", xp=5000)
    assert gov.user_level(db, user.id) == 1
    db.refresh(user)
    # 纯计算不写回：member_level 列已删除，用户行上不再有该属性
    # （原断言 user.member_level == 1 的缓存列口径）。
    assert getattr(user, "member_level", None) is None


# ==================== 提案提交 ====================


def test_submit_lv1_forbidden(db):
    _setup_free(db)
    user = _make_user(db, "lv1", xp=0)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload())
    assert ei.value.status_code == 403


def test_submit_blocked_at_level_gate_no_proposal_no_round(db):
    # 原 test_submit_lv2_ok_pending_no_round_no_xp：xp=100 旧口径可达 Lv.2
    # 提交成功；现经验无来源恒 Lv.1，提交在等级门 403，不落提案行、
    # 不建轮次，同样不产生任何经验落点。
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload())
    assert ei.value.status_code == 403
    assert db.query(models.LotteryActivityProposal).count() == 0
    assert db.query(models.LotteryRound).count() == 0
    db.refresh(user)
    assert getattr(user, "member_xp", None) is None  # 经验列已删，无落点


def test_submit_paid_realm_forbidden(db):
    _make_realm(db, "paid")
    _enable_lottery(db)
    user = _make_user(db, "lv2", xp=100)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload())
    assert ei.value.status_code == 403


def test_submit_lottery_disabled_forbidden(db):
    _setup_free(db)
    _set_config(db, "lottery_enabled", "0")
    user = _make_user(db, "lv2", xp=100)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload())
    assert ei.value.status_code == 403


def test_submit_group_not_allowed_forbidden(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload(chat_id=777))
    assert ei.value.status_code == 403


def test_submit_auto_chat_single_group(db):
    # 提交入口已恒被等级门拦下，群解析改直测 resolve_chat_id：
    # 允许群恰好一个时 chat_id=None 自动取该群（原断言 proposal.chat_id == 999）。
    _setup_free(db)
    assert gov.resolve_chat_id(db, None) == 999


def test_submit_auto_chat_ambiguous_rejected(db):
    # 同上改直测 resolve_chat_id：允许群有两个时无法自动取，400。
    _setup_free(db)
    _set_config(db, "lottery_group_ids", "999,888")
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.resolve_chat_id(db, None)
    assert ei.value.status_code == 400


def test_submit_monthly_cap_lv2_one_lv4_two(db):
    # 月限数值口径不变（Lv.2/3 基础档 1、Lv.4+ 档 2），改直测
    # proposal_cap_for_level；本月计数改由播种行驱动 proposal_count_this_month
    # （提交入口已恒被等级门拦下，原「连提至上限 400」路径不可达）。
    from datetime import datetime

    _setup_free(db)
    assert gov.proposal_cap_for_level(db, 2) == 1
    assert gov.proposal_cap_for_level(db, 4) == 2
    lv2 = _make_user(db, "lv2", xp=100)
    _seed_proposal(db, lv2, title="提案1")
    assert gov.proposal_count_this_month(db, lv2.id, datetime.now()) == 1
    lv4 = _make_user(db, "lv4", xp=1500)
    _seed_proposal(db, lv4, title="提案A")
    _seed_proposal(db, lv4, title="提案B")
    assert gov.proposal_count_this_month(db, lv4.id, datetime.now()) == 2


def test_submit_rejected_proposal_still_counts_toward_cap(db):
    # 被拒绝的提案仍占月限（防 spam 刷提案）：计数口径改由播种行直测
    # proposal_count_this_month；提交本身现恒被等级门 403。
    from datetime import datetime

    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    gov.reject_proposal(db, admin.id, proposal.id)
    assert gov.proposal_count_this_month(db, user.id, datetime.now()) == 1
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.submit_proposal(db, user.id, _payload(title="再提"))
    assert ei.value.status_code == 403


def test_submit_payload_validation_messages(db):
    # 提交入口已恒被等级门拦下，载荷校验改直测 validate_round_payload
    # （提交/批准共用的同一校验函数），文案断言逐条不变。
    _setup_free(db)
    cases = [
        (_payload(title="  "), "活动标题不能为空"),
        (_payload(prizes=[]), "至少需要配置 1 个奖品"),
        (_payload(prizes=[{"name": "x", "type": "gold", "value": 1, "quantity": 1}]),
         "奖品类型只能是 days/whitelist"),
        # 能量（points）奖品已随货币删除下线：类型校验直接拒绝（2026-11-13）
        (_payload(prizes=[{"name": "x", "type": "points", "value": 100, "quantity": 1}]),
         "奖品类型只能是 days/whitelist"),
        (_payload(prizes=[{"name": "x", "type": "days", "value": 0, "quantity": 1}]),
         "天数奖品的价值必须大于 0"),
        (_payload(prizes=[{"name": "x", "type": "whitelist", "value": -1, "quantity": 1}]),
         "奖品价值不能为负数"),
        (_payload(prizes=[{"name": "x", "type": "days", "value": 1, "quantity": 0}]),
         "奖品数量至少为 1"),
        (_payload(lottery_type="password", password_keyword=None), "口令抽奖需要设置口令"),
        (_payload(max_participants=0), "最大参与人数至少为 1"),
        (_payload(draw_at="not-a-date"), "draw_at 时间格式无效"),
    ]
    for payload, message in cases:
        with pytest.raises(gov.LotteryGovernanceError) as ei:
            gov.validate_round_payload(payload, db)
        assert ei.value.status_code == 400
        assert ei.value.detail == message


# ==================== 提案批准 / 拒绝 ====================


def test_approve_creates_round_and_audit(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    approved, round_obj = gov.approve_proposal(db, admin.id, proposal.id, note="通过")
    assert approved.status == "approved"
    assert approved.round_id == round_obj.id
    assert approved.reviewer_id == admin.id
    assert approved.reviewed_at is not None
    assert round_obj.status == "open"
    assert round_obj.created_by == user.id  # 轮次归属提案人，不是审批人
    assert round_obj.chat_id == 999
    prizes = db.query(models.LotteryRoundPrize).filter_by(round_id=round_obj.id).all()
    assert len(prizes) == 1 and prizes[0].type == "days" and prizes[0].value == 30
    # AdminLog 审计行
    logs = db.query(models.AdminLog).filter_by(action="lottery_proposal_approve").all()
    assert len(logs) == 1
    assert logs[0].admin_user_id == admin.id
    assert logs[0].target_id == proposal.id
    db.refresh(user)
    assert getattr(user, "member_xp", None) is None  # 批准不产生经验（经验列已删）


def test_approve_twice_rejected(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    gov.approve_proposal(db, admin.id, proposal.id)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.approve_proposal(db, admin.id, proposal.id)
    assert ei.value.status_code == 400


def test_approve_missing_proposal_404(db):
    _setup_free(db)
    admin = _make_user(db, "admin", xp=0)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.approve_proposal(db, admin.id, 99999)
    assert ei.value.status_code == 404


def test_approve_active_round_conflict(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    # 批准前同群已有进行中活动（另一轮次）-> 批准被拒，提案仍 pending
    from backend import lottery

    lottery.create_round(db, title="已有活动", chat_id=999, prizes=_payload()["prizes"])
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.approve_proposal(db, admin.id, proposal.id)
    assert ei.value.status_code == 400
    db.refresh(proposal)
    assert proposal.status == "pending"


def test_approve_paid_realm_forbidden(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    # 服切回付费后批准被拒（提案本身不被删除）
    realm = db.query(models.ServerRealm).first()
    realm.access_mode = "paid"
    db.commit()
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.approve_proposal(db, admin.id, proposal.id)
    assert ei.value.status_code == 403


def test_reject_flow_and_audit(db):
    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user)
    rejected = gov.reject_proposal(db, admin.id, proposal.id, note="奖品不合理")
    assert rejected.status == "rejected"
    assert rejected.review_note == "奖品不合理"
    assert rejected.round_id is None
    assert db.query(models.LotteryRound).count() == 0
    logs = db.query(models.AdminLog).filter_by(action="lottery_proposal_reject").all()
    assert len(logs) == 1
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.reject_proposal(db, admin.id, proposal.id)
    assert ei.value.status_code == 400


def test_approve_draw_at_not_shifted(db):
    """批准时 draw_at 不漂移：入库的上海 naive 时间原样进轮次（回归 +8h 隐患）。

    提案行直接播种为已入库的上海 naive 12:00（提交串 aware UTC 04:00 的
    入库结果）；批准若把已入库值再转一次，轮次会漂成 20:00，本测试钉死 12:00。
    """
    from datetime import datetime

    _setup_free(db)
    user = _make_user(db, "lv2", xp=100)
    admin = _make_user(db, "admin", xp=0)
    proposal = _seed_proposal(db, user, draw_at=datetime(2026, 11, 1, 12, 0))
    assert (proposal.draw_at.hour, proposal.draw_at.minute) == (12, 0)
    _, round_obj = gov.approve_proposal(db, admin.id, proposal.id)
    assert round_obj.draw_at is not None
    assert (round_obj.draw_at.hour, round_obj.draw_at.minute) == (12, 0)


# ==================== 直接发起 ====================


def test_direct_always_level_forbidden(db):
    # 原 test_direct_lv4_forbidden_lv5_ok：旧口径 Lv.4 拦、Lv.5 放行；
    # 现经验无来源恒 Lv.1，两档入参都在等级门 403，不建轮次、不落发起记录，
    # 同样不产生任何经验落点。
    _setup_free(db)
    lv4 = _make_user(db, "lv4", xp=1500)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.direct_launch(db, lv4.id, _payload())
    assert ei.value.status_code == 403

    lv5 = _make_user(db, "lv5", xp=5000)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.direct_launch(db, lv5.id, _payload())
    assert ei.value.status_code == 403
    assert db.query(models.LotteryRound).count() == 0
    assert db.query(models.LotteryDirectLaunch).count() == 0
    db.refresh(lv5)
    assert getattr(lv5, "member_xp", None) is None  # 经验列已删，无落点


def test_direct_monthly_cap_lv5_one_lv6_two(db):
    # 月限数值口径不变（Lv.5 档 1、Lv.6 档 2），改直测 direct_cap_for_level；
    # 本月计数改由播种的直接发起记录行驱动 direct_count_this_month
    # （直接发起入口已恒被等级门拦下，原「连发至上限 400」路径不可达）。
    from datetime import datetime

    from backend import lottery

    _setup_free(db)
    assert gov.direct_cap_for_level(db, 5) == 1
    assert gov.direct_cap_for_level(db, 6) == 2
    lv5 = _make_user(db, "lv5", xp=5000)
    rnd = lottery.create_round(db, title="直发1", chat_id=999, prizes=_payload()["prizes"])
    db.add(models.LotteryDirectLaunch(user_id=lv5.id, round_id=rnd.id, level_snapshot=1))
    db.commit()
    assert gov.direct_count_this_month(db, lv5.id, datetime.now()) == 1

    lv6 = _make_user(db, "lv6", xp=15000)
    for title in ("直发A", "直发B"):
        rnd = lottery.create_round(db, title=title, chat_id=888, prizes=_payload()["prizes"])
        db.add(models.LotteryDirectLaunch(user_id=lv6.id, round_id=rnd.id, level_snapshot=1))
        db.commit()
    assert gov.direct_count_this_month(db, lv6.id, datetime.now()) == 2


def test_direct_paid_realm_forbidden(db):
    _make_realm(db, "paid")
    _enable_lottery(db)
    user = _make_user(db, "lv5", xp=5000)
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.direct_launch(db, user.id, _payload())
    assert ei.value.status_code == 403


def test_direct_active_round_conflict(db):
    # 门禁顺序：等级门在同群进行中检查之前——现恒 Lv.1 以 403 被拒
    # （原断言为 Lv.5 用户走到冲突检查得 400）。
    _setup_free(db)
    user = _make_user(db, "lv5", xp=5000)
    from backend import lottery

    lottery.create_round(db, title="已有活动", chat_id=999, prizes=_payload()["prizes"])
    with pytest.raises(gov.LotteryGovernanceError) as ei:
        gov.direct_launch(db, user.id, _payload())
    assert ei.value.status_code == 403


# ==================== 列表与路由注册 ====================


def test_list_proposals_user_and_admin_scopes(db):
    _setup_free(db)
    u1 = _make_user(db, "u1", xp=100)
    u2 = _make_user(db, "u2", xp=100)
    _seed_proposal(db, u1, title="u1提案")
    _seed_proposal(db, u2, title="u2提案")
    total, items = gov.list_proposals(db, user_id=u1.id)
    assert total == 1 and items[0]["title"] == "u1提案"
    total, items = gov.list_proposals(db, user_id=None, status="pending")
    assert total == 2
    total, items = gov.list_proposals(db, user_id=None, status="approved")
    assert total == 0


def test_config_cap_override(db):
    # 配置覆盖口径不变，改直测 proposal_cap_for_level（提交入口已恒被
    # 等级门拦下，原「连提 2 次后第 3 次被拒」路径不可达）。
    _setup_free(db)
    _set_config(db, "lottery_governance_proposal_cap_base", "2")
    assert gov.proposal_cap_for_level(db, 2) == 2
    # 非法键值回落默认值 1
    _set_config(db, "lottery_governance_proposal_cap_base", "not-a-number")
    assert gov.proposal_cap_for_level(db, 2) == 1


def test_routes_registered():
    """端点已挂上路由（用户端已 include 的 lottery 路由 + 管理端 admin 路由）。"""
    from backend.api.lottery import router as lottery_router
    import backend.api.welfare_admin  # noqa: F401 导入即执行装饰器，把端点挂上 admin_router
    from backend.api.admin_core import admin_router

    lottery_paths = {(r.path, tuple(r.methods or [])) for r in lottery_router.routes}
    assert any(p == "/api/lottery/proposals" and "POST" in m for p, m in lottery_paths)
    assert any(p == "/api/lottery/proposals/mine" and "GET" in m for p, m in lottery_paths)
    assert any(p == "/api/lottery/rounds/direct" and "POST" in m for p, m in lottery_paths)
    # admin_router 自带 /api/admin 前缀（main.py 挂载口径）
    admin_paths = {(r.path, tuple(r.methods or [])) for r in admin_router.routes}
    assert any(
        p == "/api/admin/welfare/lottery/proposals" and "GET" in m for p, m in admin_paths
    )
    assert any(
        p == "/api/admin/welfare/lottery/proposals/{proposal_id}/approve" and "POST" in m
        for p, m in admin_paths
    )
    assert any(
        p == "/api/admin/welfare/lottery/proposals/{proposal_id}/reject" and "POST" in m
        for p, m in admin_paths
    )
