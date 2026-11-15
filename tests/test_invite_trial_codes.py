"""福利试用码机制回归（2026-11-15 改版）：邀请人生成码 → 好友凭码领取。

口径锚点（照友站 UHDNow「生成试用邀请码」机制，我方数值全走配置键）：
- 生成即占当月次数：月度计数 = 当月生成数（pending/used 都算，
  expired 不返还）；资格 = 累计实付 ≥ 门槛（默认 $10）；
- 码面快照：天数/套餐按生成时配置快照，领取按快照发放；
- 码生成后 30 天未用过期；领取一人一份、码不可重复领、不能领自己的码；
- 邀请注册不再自动发试用：只建关系/记返利，发放只走凭码领取
  （注册带 trial_code 时在注册成功后走同一领取路径）；
- 子账号不能生成码（403，子账号禁经济动作口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-invite-trial-codes-key-32chars!")

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import invite_trial, models
from backend.api import emby_portal
from backend.api import invitation as invitation_api
from backend.api import admin_invitation as admin_invitation_api
from backend.integrations import store


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main", is_active=True))
    session.add(models.SystemConfig(key="wallet_enabled", value="false"))
    session.commit()
    store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        store.invalidate()


def _set(db, key, value):
    row = db.query(models.SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()
    store.invalidate(key)


def _user(db, username, parent=None):
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       parent_user_id=parent.id if parent else None)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _plan(db, name="测试套餐"):
    plan = models.SubscriptionPlan(name=name, price=Decimal("45"),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _eligible_inviter(db, name="inviter-c", paid="45"):
    """累计实付达标（默认门槛 $10）的邀请人；付费订单顺带建出默认套餐。"""
    inviter = _user(db, name)
    plan = _plan(db)
    order = models.SubscriptionOrder(
        order_id=f"PAY-{name}", user_id=inviter.id, plan_id=plan.id,
        amount=Decimal(paid), status="paid")
    db.add(order)
    db.commit()
    return inviter


def _generate(db, creator):
    row = invite_trial.generate_trial_code(db, creator)
    db.commit()
    db.refresh(row)
    return row


def _claim(db, code_str, claimer):
    grant = invite_trial.claim_trial_code(db, code_str, claimer)
    db.commit()
    return grant


# ==================== 生成：资格与快照 ====================

def test_generate_snapshots_config_and_ttl(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db)
    row = _generate(db, inviter)
    assert row.status == "pending"
    assert row.days == 7
    assert row.plan_id is None
    assert row.creator_id == inviter.id
    assert len(row.code) == 10
    # 有效期 = 生成 + 30 天（推导口径）
    assert row.expires_at - row.created_at == timedelta(days=30)


def test_generate_rejected_when_trial_inactive(db):
    inviter = _eligible_inviter(db, "inviter-off")
    # 出厂天数 0 = 不实际发放，生成同样拒绝
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.generate_trial_code(db, inviter)
    assert exc.value.code == "trial_inactive"
    db.rollback()
    assert db.query(models.InviteTrialCode).count() == 0


def test_generate_rejected_below_threshold(db):
    _set(db, "invite_trial_days", "7")
    poor = _user(db, "inviter-poor-c")  # 无任何已付订单
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.generate_trial_code(db, poor)
    assert exc.value.code == "below_threshold"
    db.rollback()
    assert db.query(models.InviteTrialCode).count() == 0


def test_generate_endpoint_rejects_sub_account(db):
    _set(db, "invite_trial_days", "7")
    parent = _eligible_inviter(db, "inviter-parent")
    sub = _user(db, "sub-c", parent=parent)
    with pytest.raises(HTTPException) as exc:
        invitation_api.generate_trial_code(sub, db)
    assert exc.value.status_code == 403
    db.rollback()
    assert db.query(models.InviteTrialCode).count() == 0


# ==================== 次数三态计数 ====================

def test_monthly_count_three_states(db):
    _set(db, "invite_trial_days", "7")
    _set(db, "invite_welfare_monthly_limit", "2")
    inviter = _eligible_inviter(db, "inviter-cnt")
    friend = _user(db, "friend-cnt")

    first = _generate(db, inviter)
    _generate(db, inviter)
    status = invite_trial.welfare_status(db, inviter)
    assert status["used_this_month"] == 2
    assert status["pending_this_month"] == 2
    assert status["used_codes_this_month"] == 0
    assert status["remaining_this_month"] == 0
    assert status["eligible"] is False

    # 生成即占次数：第 3 张直接拒
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.generate_trial_code(db, inviter)
    assert exc.value.code == "monthly_limit"
    db.rollback()

    # 领取一张：pending→used，总数与剩余不变（领取不释放次数）
    _claim(db, first.code, friend)
    status = invite_trial.welfare_status(db, inviter)
    assert status["used_this_month"] == 2
    assert status["pending_this_month"] == 1
    assert status["used_codes_this_month"] == 1
    assert status["remaining_this_month"] == 0


def test_expired_code_still_occupies_monthly_count(db):
    _set(db, "invite_trial_days", "7")
    _set(db, "invite_welfare_monthly_limit", "1")
    inviter = _eligible_inviter(db, "inviter-exp")
    now = datetime.now()
    stale = models.InviteTrialCode(
        code="STALE00001", creator_id=inviter.id, days=7, status="pending",
        created_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1))
    db.add(stale)
    db.commit()

    # 过期不返还：本月次数仍被这张过期码占着
    assert invite_trial.welfare_generated_this_month(db, inviter.id) == 1
    assert invite_trial.effective_status(stale) == "expired"
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.generate_trial_code(db, inviter)
    assert exc.value.code == "monthly_limit"
    db.rollback()

    # 过期码领不了，且领取尝试会把状态落库为 expired
    friend = _user(db, "friend-exp")
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.claim_trial_code(db, stale.code, friend)
    assert exc.value.code == "expired"
    db.rollback()
    assert db.query(models.InviteTrialGrant).count() == 0


# ==================== 领取：绑定/防重/自领 ====================

def test_claim_grants_by_snapshot_not_live_config(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-snap")
    friend = _user(db, "friend-snap")
    row = _generate(db, inviter)

    # 生成后管理员改天数：已发出的码仍按快照 7 天兑现
    _set(db, "invite_trial_days", "30")
    grant = _claim(db, row.code, friend)
    assert grant.days == 7
    assert grant.code_id == row.id
    assert grant.inviter_id == inviter.id
    sub = db.query(models.UserSubscription).filter_by(user_id=friend.id).first()
    assert sub is not None
    db.refresh(row)
    assert row.status == "used"
    assert row.used_by == friend.id
    assert row.used_at is not None


def test_claim_rejects_double_claim_and_second_code(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-dbl-c")
    other_inviter = _eligible_inviter(db, "inviter-dbl-d")
    friend = _user(db, "friend-dbl")
    second_friend = _user(db, "friend-dbl2")
    row = _generate(db, inviter)
    other_row = _generate(db, other_inviter)
    _claim(db, row.code, friend)

    # 同一张码换个人再领 → already_used
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.claim_trial_code(db, row.code, second_friend)
    assert exc.value.code == "already_used"
    db.rollback()

    # 同一个人换一张码再领 → already_granted（一人一份）
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.claim_trial_code(db, other_row.code, friend)
    assert exc.value.code == "already_granted"
    db.rollback()
    assert db.query(models.InviteTrialGrant).filter_by(invitee_id=friend.id).count() == 1
    # 第二张码没被消耗
    db.refresh(other_row)
    assert other_row.status == "pending"


def test_claim_rejects_self_claim_and_unknown_code(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-self-c")
    row = _generate(db, inviter)
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.claim_trial_code(db, row.code, inviter)
    assert exc.value.code == "self_claim"
    db.rollback()
    with pytest.raises(invite_trial.TrialCodeError) as exc:
        invite_trial.claim_trial_code(db, "NOSUCHCODE", inviter)
    assert exc.value.code == "not_found"
    db.rollback()
    db.refresh(row)
    assert row.status == "pending"


def test_claim_endpoint_and_my_codes_payload(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-ep")
    friend = _user(db, "friend-ep")
    out = invitation_api.generate_trial_code(inviter, db)
    assert out["code"]["link_path"] == f"/login?trial={out['code']['code']}"
    assert out["welfare"]["used_this_month"] == 1

    payload = invitation_api.claim_trial_code(
        invitation_api.TrialClaimRequest(code=out["code"]["code"]), friend, db)
    assert payload["claimed"] is True
    assert payload["days"] == 7
    assert payload["grant_id"] > 0

    mine = invitation_api.my_trial_codes(50, inviter, db)
    assert len(mine["codes"]) == 1
    assert mine["codes"][0]["status"] == "used"
    assert mine["codes"][0]["used_by"] == friend.id


# ==================== 注册：自动发放退役 + 带码领取 ====================

def _scope(ip="1.2.3.4"):
    return {"type": "http", "method": "POST", "path": "/",
            "headers": [], "client": (ip, 1234)}


def _register(db, username, invite=None, trial=None):
    _set(db, "register_ratelimit_enabled", "false")
    _set(db, "registration_mode", "open")
    req = emby_portal.RegisterRequest(
        username=username, password="pass1234",
        invitation_code=invite, trial_code=trial)
    return emby_portal.register(Request(_scope()), req, db)


def _invite_code(db, inviter, code_str):
    code = models.InvitationCode(code=code_str, user_id=inviter.id,
                                 is_active=True, max_uses=0, use_count=0,
                                 reward_points=0)
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def test_register_with_invite_no_longer_grants_trial(db):
    """改版核心断言：普通邀请注册只建关系，绝不自动发试用。"""
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-reg-c")
    code = _invite_code(db, inviter, "CODEREGC1")
    _register(db, "newbie_reg_c", invite=code.code)
    newbie = db.query(models.WebUser).filter_by(username="newbie_reg_c").one()
    record = db.query(models.InvitationRecord).filter_by(invitee_id=newbie.id).one()
    assert record.inviter_id == inviter.id
    assert db.query(models.InviteTrialGrant).filter_by(
        invitee_id=newbie.id).count() == 0
    assert db.query(models.UserSubscription).filter_by(
        user_id=newbie.id).count() == 0


def test_register_with_trial_code_claims(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-reg-t")
    row = _generate(db, inviter)
    _register(db, "newbie_reg_t", trial=row.code)
    newbie = db.query(models.WebUser).filter_by(username="newbie_reg_t").one()
    grant = db.query(models.InviteTrialGrant).filter_by(
        invitee_id=newbie.id).one()
    assert grant.code_id == row.id
    assert grant.days == 7
    db.refresh(row)
    assert row.status == "used"
    assert row.used_by == newbie.id


def test_register_with_used_trial_code_still_registers(db):
    """码领不了（已被用）不许拖垮注册：账号照常建成、只是没试用。"""
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-reg-u")
    first_friend = _user(db, "friend-reg-u")
    row = _generate(db, inviter)
    _claim(db, row.code, first_friend)
    _register(db, "newbie_reg_u", trial=row.code)
    newbie = db.query(models.WebUser).filter_by(username="newbie_reg_u").one()
    assert newbie.id is not None
    assert db.query(models.InviteTrialGrant).filter_by(
        invitee_id=newbie.id).count() == 0


# ==================== 管理端只读列表 ====================

def test_admin_trial_codes_list(db):
    _set(db, "invite_trial_days", "7")
    admin = _user(db, "admin-tc")
    inviter = _eligible_inviter(db, "inviter-adm")
    friend = _user(db, "friend-adm")
    row = _generate(db, inviter)
    _generate(db, inviter)
    _claim(db, row.code, friend)

    out = admin_invitation_api.invite_trial_codes(100, None, admin, db)
    assert out["total"] == 2
    used = [c for c in out["codes"] if c["status"] == "used"]
    assert len(used) == 1
    assert used[0]["creator_username"] == "inviter-adm"
    assert used[0]["used_by_username"] == "friend-adm"
    assert used[0]["code"] == row.code

    only_pending = admin_invitation_api.invite_trial_codes(
        100, "pending", admin, db)
    assert only_pending["total"] == 1
    assert only_pending["codes"][0]["status"] == "pending"
