"""邀请全套单回归（2026-11-14）：返利计提（flat/tiered）+ 福利试用 +
注册归属 + 履约钩子 + 换码 + 管理总览。

口径锚点：
- 返利只挂被邀请人的真实付费，注册不发可变现奖励；金额 Decimal 元、
  按分 ROUND_HALF_UP 取整（整笔算完取整一次）；
- 默认分档 tiered：按每位好友累计付费，前 ¥350（配置键，分）返 5%、
  超出返 1%；flat 模式保留可切换；
- 福利试用是体验权益：邀请关系建立时同事务发放，受总开关/天数/
  月次数/邀请人累计付费门槛约束，留痕防重；
- 本文件默认把钱包总开关关掉（fixture 置 wallet_enabled=false），
  锚定「无钱包/钱包关停时计提挂 pending」的路径；钱包开着时的即时
  入账路径由 test_record_rebate_credited_with_wallet 单独覆盖。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-invite-full-suite-key-32chars!")

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import invite_rebate, invite_trial, member_level as ml, models, wallet
from backend.api import economy, emby_portal
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
    session.commit()
    ml.ensure_member_levels_seeded(session)
    # 钱包默认开着会让返利即时入账；本文件锚定「无钱包时计提挂 pending」
    # 的路径，统一先把总开关关掉（即时入账另由
    # test_record_rebate_credited_with_wallet 覆盖）。
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


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       member_xp=0, member_level=1)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _code(db, inviter, code_str):
    code = models.InvitationCode(code=code_str, user_id=inviter.id,
                                 is_active=True, max_uses=0, use_count=0,
                                 reward_points=0)
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def _relation(db, inviter, invitee, code):
    row = models.InvitationRecord(inviter_id=inviter.id, invitee_id=invitee.id,
                                  code_id=code.id, reward_points=0)
    db.add(row)
    db.commit()
    return row


def _plan(db):
    plan = models.SubscriptionPlan(name="测试套餐", price=Decimal("45"),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _paid_sub_order(db, user, amount, ref):
    order = models.SubscriptionOrder(
        order_id=ref, user_id=user.id, plan_id=_plan(db).id,
        amount=Decimal(amount), status="paid")
    db.add(order)
    db.commit()
    return order


def _accruals(db, inviter_id):
    return (db.query(models.InviteRebateAccrual)
            .filter_by(inviter_id=inviter_id)
            .order_by(models.InviteRebateAccrual.id).all())


# ==================== 配置默认值 ====================

def test_rebate_config_defaults_are_tiered(db):
    snap = invite_rebate.config_snapshot(db)
    assert snap == {
        "mode": "tiered",
        "percent": 5,
        "cap_fen": 0,
        "tier_threshold_fen": 35000,
        "tier_percent": 1,
    }


def test_trial_config_defaults(db):
    cfg = invite_trial.config(db)
    assert cfg == {"enabled": True, "days": 0, "monthly_limit": 2,
                   "min_paid_fen": 1000}
    # 出厂不实际发放（天数 0）
    assert invite_trial.policy_payload(db)["active"] is False


# ==================== 返利计算（纯函数） ====================

def test_compute_rebate_flat_rounding():
    assert invite_rebate.compute_rebate(Decimal("45"), 5) == Decimal("2.25")
    # 1.6665 → 按分四舍五入 1.67
    assert invite_rebate.compute_rebate(Decimal("33.33"), 5) == Decimal("1.67")
    # 0.5005 → 0.50（第三位是 0）
    assert invite_rebate.compute_rebate(Decimal("10.01"), 5) == Decimal("0.50")
    assert invite_rebate.compute_rebate(Decimal("0"), 5) == Decimal("0")
    assert invite_rebate.compute_rebate(Decimal("100"), 0) == Decimal("0")


def test_compute_rebate_tiered_crossing_threshold():
    # 首付 ¥400：350×5% + 50×1% = 17.50 + 0.50
    got = invite_rebate.compute_rebate_tiered(
        Decimal("400"), 5, 1, Decimal("0"), Decimal("350"))
    assert got == Decimal("18.00")


def test_compute_rebate_tiered_exact_threshold():
    # 已付 ¥300 再付 ¥50 恰达阈值：整笔按 5%
    got = invite_rebate.compute_rebate_tiered(
        Decimal("50"), 5, 1, Decimal("300"), Decimal("350"))
    assert got == Decimal("2.50")


def test_compute_rebate_tiered_above_threshold():
    # 已付满 ¥350 后再付 ¥100：整笔按 1%
    got = invite_rebate.compute_rebate_tiered(
        Decimal("100"), 5, 1, Decimal("350"), Decimal("350"))
    assert got == Decimal("1.00")
    got = invite_rebate.compute_rebate_tiered(
        Decimal("100"), 5, 1, Decimal("900"), Decimal("350"))
    assert got == Decimal("1.00")


def test_compute_rebate_tiered_rounds_once():
    # 已付 ¥340 再付 ¥33.33：前段 10×5%=0.50，后段 23.33×1%=0.2333，
    # 合计 0.7333 → 整笔取整一次 = 0.73（不是分段各取整的 0.50+0.23=0.73 同值例，
    # 这里验证不爆不多算）
    got = invite_rebate.compute_rebate_tiered(
        Decimal("33.33"), 5, 1, Decimal("340"), Decimal("350"))
    assert got == Decimal("0.73")


# ==================== 返利计提 ====================

def test_record_rebate_pending_without_wallet(db):
    _set(db, "wallet_invite_rebate_mode", "flat")
    inviter = _user(db, "inviter-a")
    invitee = _user(db, "invitee-a")
    code = _code(db, inviter, "CODEAAAA")
    _relation(db, inviter, invitee, code)

    row = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-1",
        paid_amount=Decimal("45"))
    db.commit()
    assert row is not None
    assert row.rebate_amount == Decimal("2.25")
    # 钱包总开关已在 fixture 里关掉：保持待入账，不许报错、不许假装到账
    assert row.status == "pending"
    assert row.wallet_ledger_id is None


def test_record_rebate_credited_with_wallet(db):
    # 钱包开着时：同一笔返利即时入账（其余用例锚定 wallet-off 的挂账路径）。
    _set(db, "wallet_enabled", "true")
    _set(db, "wallet_invite_rebate_mode", "flat")
    inviter = _user(db, "inviter-w")
    invitee = _user(db, "invitee-w")
    code = _code(db, inviter, "CODEWALL")
    _relation(db, inviter, invitee, code)

    row = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-W1",
        paid_amount=Decimal("45"))
    db.commit()
    assert row is not None
    assert row.rebate_amount == Decimal("2.25")
    assert row.status == "credited"
    assert row.wallet_ledger_id is not None
    assert wallet.get_balance(db, inviter.id) == Decimal("2.25")


def test_record_rebate_idempotent(db):
    inviter = _user(db, "inviter-b")
    invitee = _user(db, "invitee-b")
    code = _code(db, inviter, "CODEBBBB")
    _relation(db, inviter, invitee, code)

    first = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-DUP",
        paid_amount=Decimal("45"))
    db.commit()
    second = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-DUP",
        paid_amount=Decimal("45"))
    db.commit()
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(_accruals(db, inviter.id)) == 1


def test_record_rebate_no_relation(db):
    payer = _user(db, "lonely-payer")
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="SO-X",
        paid_amount=Decimal("45"))
    assert row is None


def test_record_rebate_tiered_sequence(db):
    inviter = _user(db, "inviter-c")
    invitee = _user(db, "invitee-c")
    code = _code(db, inviter, "CODECCCC")
    _relation(db, inviter, invitee, code)

    amounts = []
    for i, paid in enumerate(("300", "100", "100")):
        row = invite_rebate.record_payment_rebate(
            db, invitee, source="subscription_order", source_ref=f"SO-T{i}",
            paid_amount=Decimal(paid))
        db.commit()
        amounts.append(row.rebate_amount)
    # 300 全前段 5% = 15.00；100 跨段 = 50×5% + 50×1% = 3.00；100 全后段 1% = 1.00
    assert amounts == [Decimal("15.00"), Decimal("3.00"), Decimal("1.00")]


def test_record_rebate_cap_truncates(db):
    _set(db, "wallet_invite_rebate_mode", "flat")
    _set(db, "wallet_invite_rebate_cap", "100")  # 上限 ¥1
    inviter = _user(db, "inviter-d")
    invitee = _user(db, "invitee-d")
    code = _code(db, inviter, "CODEDDDD")
    _relation(db, inviter, invitee, code)

    row = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-C1",
        paid_amount=Decimal("45"))
    db.commit()
    assert row.rebate_amount == Decimal("1.00")
    # 上限已占满：第二笔不再计提
    row2 = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-C2",
        paid_amount=Decimal("45"))
    assert row2 is None


def test_record_rebate_zero_percent(db):
    _set(db, "wallet_invite_rebate_percent", "0")
    inviter = _user(db, "inviter-e")
    invitee = _user(db, "invitee-e")
    code = _code(db, inviter, "CODEEEEE")
    _relation(db, inviter, invitee, code)
    row = invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-Z",
        paid_amount=Decimal("45"))
    assert row is None
    assert _accruals(db, inviter.id) == []


def test_settle_without_wallet_keeps_pending(db):
    inviter = _user(db, "inviter-f")
    invitee = _user(db, "invitee-f")
    code = _code(db, inviter, "CODEFFFF")
    _relation(db, inviter, invitee, code)
    invite_rebate.record_payment_rebate(
        db, invitee, source="subscription_order", source_ref="SO-S",
        paid_amount=Decimal("45"))
    db.commit()
    assert invite_rebate.settle_pending_rebates(db) == 0
    assert _accruals(db, inviter.id)[0].status == "pending"


# ==================== 福利试用（见面礼） ====================

def _eligible_inviter(db, name="inviter-t", paid="45"):
    inviter = _user(db, name)
    _paid_sub_order(db, inviter, paid, f"PAY-{name}")
    return inviter


def test_trial_granted_on_invite(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db)
    invitee = _user(db, "invitee-t")
    code = _code(db, inviter, "CODETTTT")

    result = invitation_api.apply_invitation(db, invitee, code.code)
    db.commit()
    assert result
    grants = db.query(models.InviteTrialGrant).filter_by(invitee_id=invitee.id).all()
    assert len(grants) == 1
    assert grants[0].days == 7
    assert grants[0].inviter_id == inviter.id
    sub = db.query(models.UserSubscription).filter_by(user_id=invitee.id).first()
    assert sub is not None


def test_trial_skipped_when_days_zero(db):
    inviter = _eligible_inviter(db, "inviter-t0")
    invitee = _user(db, "invitee-t0")
    code = _code(db, inviter, "CODET000")
    assert invitation_api.apply_invitation(db, invitee, code.code)
    db.commit()
    assert db.query(models.InviteTrialGrant).count() == 0


def test_trial_skipped_below_threshold(db):
    _set(db, "invite_trial_days", "7")
    inviter = _user(db, "inviter-poor")  # 没有任何已付订单
    invitee = _user(db, "invitee-poor")
    code = _code(db, inviter, "CODEPOOR")
    assert invitation_api.apply_invitation(db, invitee, code.code)
    db.commit()
    assert db.query(models.InviteTrialGrant).count() == 0


def test_trial_monthly_limit(db):
    _set(db, "invite_trial_days", "7")
    _set(db, "invite_welfare_monthly_limit", "1")
    inviter = _eligible_inviter(db, "inviter-lim")
    code = _code(db, inviter, "CODELIM1")
    first = _user(db, "invitee-lim1")
    second = _user(db, "invitee-lim2")
    assert invitation_api.apply_invitation(db, first, code.code)
    db.commit()
    assert invitation_api.apply_invitation(db, second, code.code)
    db.commit()
    grants = db.query(models.InviteTrialGrant).all()
    assert len(grants) == 1
    assert grants[0].invitee_id == first.id
    # 第二人的邀请关系照常成立，只是没试用
    assert db.query(models.InvitationRecord).filter_by(invitee_id=second.id).count() == 1


def test_trial_no_double_and_no_rebind(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-dbl")
    other = _eligible_inviter(db, "inviter-dbl2")
    invitee = _user(db, "invitee-dbl")
    code = _code(db, inviter, "CODEDBL1")
    code2 = _code(db, other, "CODEDBL2")
    assert invitation_api.apply_invitation(db, invitee, code.code)
    db.commit()
    # 已绑定：换谁的码都不能改绑、不能再领试用
    assert invitation_api.apply_invitation(db, invitee, code2.code)["applied"] is False
    db.rollback()
    assert db.query(models.InviteTrialGrant).filter_by(invitee_id=invitee.id).count() == 1
    record = db.query(models.InvitationRecord).filter_by(invitee_id=invitee.id).one()
    assert record.inviter_id == inviter.id


def test_self_invite_rejected(db):
    inviter = _user(db, "selfish")
    code = _code(db, inviter, "CODESELF")
    assert invitation_api.apply_invitation(db, inviter, code.code)["applied"] is False
    db.rollback()
    assert db.query(models.InvitationRecord).count() == 0


# ==================== 注册归属 ====================

def _scope(ip="1.2.3.4"):
    return {"type": "http", "method": "POST", "path": "/",
            "headers": [], "client": (ip, 1234)}


def _register(db, username, invite=None, reg_code=None, mode="open"):
    _set(db, "register_ratelimit_enabled", "false")
    _set(db, "registration_mode", mode)
    req = emby_portal.RegisterRequest(
        username=username, password="pass1234",
        invitation_code=invite, registration_code=reg_code)
    return emby_portal.register(Request(_scope()), req, db)


def test_register_open_with_invite_binds_and_grants_trial(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-reg")
    code = _code(db, inviter, "CODEREG1")
    _register(db, "newbie_reg", invite=code.code)
    newbie = db.query(models.WebUser).filter_by(username="newbie_reg").one()
    record = db.query(models.InvitationRecord).filter_by(invitee_id=newbie.id).one()
    assert record.inviter_id == inviter.id
    assert db.query(models.InviteTrialGrant).filter_by(invitee_id=newbie.id).count() == 1


def test_register_code_mode_both_codes(db):
    _set(db, "invite_trial_days", "7")
    inviter = _eligible_inviter(db, "inviter-both")
    code = _code(db, inviter, "CODEBOTH")
    db.add(models.RegistrationCode(
        code="REG-BOTH-0001", code_type=1, days=30, max_uses=1, use_count=0,
        is_active=True, source="admin", realm_id=1, reward_type="subscription"))
    db.commit()
    _register(db, "newbie_both", invite=code.code, reg_code="REG-BOTH-0001",
              mode="code")
    newbie = db.query(models.WebUser).filter_by(username="newbie_both").one()
    record = db.query(models.InvitationRecord).filter_by(invitee_id=newbie.id).one()
    assert record.inviter_id == inviter.id


def test_register_without_invite_no_relation(db):
    _register(db, "newbie_solo")
    newbie = db.query(models.WebUser).filter_by(username="newbie_solo").one()
    assert db.query(models.InvitationRecord).filter_by(invitee_id=newbie.id).count() == 0
    assert db.query(models.InviteTrialGrant).filter_by(invitee_id=newbie.id).count() == 0


# ==================== 履约钩子 ====================

def _plan_and_order(db, user, amount="45", ref="SO-FULFILL"):
    plan = models.SubscriptionPlan(name="月度", price=Decimal(amount),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    order = models.SubscriptionOrder(
        order_id=ref, user_id=user.id, plan_id=plan.id,
        amount=Decimal(amount), status="pending")
    db.add(order)
    db.commit()
    db.refresh(order)
    return plan, order


def test_fulfill_creates_pending_rebate(db):
    inviter = _user(db, "inviter-ful")
    invitee = _user(db, "invitee-ful")
    code = _code(db, inviter, "CODEFUL1")
    _relation(db, inviter, invitee, code)
    _, order = _plan_and_order(db, invitee)

    pending = economy._fulfill_order(db, subscription_order=order)
    db.commit()
    rows = _accruals(db, inviter.id)
    assert len(rows) == 1
    assert rows[0].paid_amount == Decimal("45")
    # 默认 tiered、累计 0 → 全前段 5%
    assert rows[0].rebate_amount == Decimal("2.25")
    assert rows[0].status == "pending"
    assert any(p.get("event_type") == "economy.subscription_success" for p in pending)

    # 重复履约（回调重放）：claim 失败，不重复计提
    pending2 = economy._fulfill_order(db, subscription_order=order)
    db.commit()
    assert pending2 == []
    assert len(_accruals(db, inviter.id)) == 1


def test_fulfill_without_relation_no_rebate(db):
    buyer = _user(db, "buyer-solo")
    _, order = _plan_and_order(db, buyer, ref="SO-SOLO")
    economy._fulfill_order(db, subscription_order=order)
    db.commit()
    assert db.query(models.InviteRebateAccrual).count() == 0


# ==================== 换码 ====================

def test_regenerate_rotates_code(db):
    user = _user(db, "rotator")
    first = invitation_api.my_code(user, db)
    old_code = first["code"]
    out = invitation_api.regenerate_my_code(user, db)
    assert out["code"] != old_code
    old = db.query(models.InvitationCode).filter_by(code=old_code).one()
    assert old.is_active is False
    # 旧码不能再绑定新关系
    newcomer = _user(db, "newcomer-rot")
    assert invitation_api.apply_invitation(db, newcomer, old_code)["applied"] is False
    db.rollback()
    # 新码可以
    assert invitation_api.apply_invitation(db, newcomer, out["code"])
    db.commit()


def test_my_code_payload_has_totals_and_welfare(db):
    user = _user(db, "payload-user")
    out = invitation_api.my_code(user, db)
    assert out["rebate_totals"] == {"credited": "0.00", "pending": "0.00", "count": 0}
    assert out["welfare"]["monthly_limit"] == 2
    assert out["config"]["rebate"]["mode"] == "tiered"
    assert out["config"]["trial"]["days"] == 0


# ==================== 管理总览 ====================

def test_admin_overview_and_burst(db):
    admin = _user(db, "admin-ov")
    inviter = _user(db, "burst-inviter")
    code = _code(db, inviter, "CODEBURST")
    for i in range(20):
        invitee = _user(db, f"burst-invitee-{i}")
        _relation(db, inviter, invitee, code)
    invitee_paid = db.query(models.WebUser).filter_by(username="burst-invitee-0").one()
    _paid_sub_order(db, invitee_paid, "45", "PAY-BURST")
    invite_rebate.record_payment_rebate(
        db, invitee_paid, source="subscription_order", source_ref="PAY-BURST",
        paid_amount=Decimal("45"))
    db.commit()

    out = admin_invitation_api.invite_overview(admin, db)
    assert out["relations"] == 20
    assert out["paid_invitees"] == 1
    assert out["rebate"]["pending"] == "2.25"
    assert out["rebate"]["pending_count"] == 1
    assert [a["inviter_id"] for a in out["burst_alerts"]] == [inviter.id]
    assert out["rebate_config"]["mode"] == "tiered"


def test_admin_trial_policy_roundtrip(db):
    admin = _user(db, "admin-tp")
    payload = admin_invitation_api.TrialPolicyUpdate(
        invite_trial_days=14, invite_welfare_monthly_limit=3,
        invite_welfare_min_paid_fen=2000)
    out = admin_invitation_api.update_trial_policy(payload, admin, db)
    assert out["success"] is True
    assert out["policy"]["days"] == 14
    assert out["policy"]["monthly_limit"] == 3
    assert out["policy"]["min_paid_fen"] == 2000
    assert out["policy"]["active"] is True
    got = admin_invitation_api.get_trial_policy(admin, db)
    assert got["policy"]["days"] == 14
