# tests/test_invite_promoter_aff.py
"""推广者（AFF）后台管理回归（2026-11-20 全套单）：

- 推广者汇总聚合口径与邀请总览同源：邀请人数 = invitation_records
  计数；付费人数 = 与总览同一条 join（被邀请人名下 status=paid 的
  订阅订单去重计数）；累计付费 / 已入账 / 待入账锚定计提台账
  invite_rebate_accruals（pending+credited 计入，reversed 不计——
  与 invite_rebate._invitee_prior_paid / _accrued_total 同一状态集）。
- 专属费率覆盖：计提前查推广者档案，有覆盖值时以覆盖值替代全局
  tiered/flat 的比例口径（整笔一律按覆盖比例算），封顶照旧；
  计提行 percent 快照记覆盖值；只影响生效后的新计提，历史不动。
- 停用双拦截：邀请码绑定（apply_invitation）拒绝并给 reason/文案、
  不占码名额；返利计提（record_payment_rebate）不再产生新行；
  已入账不追回，停用可恢复。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-invite-promoter-key-32chars!!")

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import invite_promoter, invite_rebate, models
from backend.api import admin_economy
from backend.api import admin_invitation as admin_invitation_api
from backend.api import invitation as invitation_api
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
    # 钱包关停：计提挂 pending，断言不依赖钱包入账时机
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
    u = models.WebUser(username=username, password_hash="x", is_active=True)
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


def _paid_order(db, user, amount, ref):
    plan = models.SubscriptionPlan(name=f"套餐{ref}", price=Decimal(amount),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    order = models.SubscriptionOrder(
        order_id=ref, user_id=user.id, plan_id=plan.id,
        amount=Decimal(amount), status="paid")
    db.add(order)
    db.commit()
    return order


def _accrual(db, inviter, invitee, paid, rebate, status, ref):
    row = models.InviteRebateAccrual(
        inviter_id=inviter.id, invitee_id=invitee.id,
        source="subscription_order", source_ref=ref,
        paid_amount=Decimal(paid), rebate_amount=Decimal(rebate),
        percent=5, status=status)
    db.add(row)
    db.commit()
    return row


def _profile(db, user, *, override=None, disabled=False, note=""):
    row = models.InvitePromoterProfile(
        user_id=user.id, rebate_percent_override=override,
        is_disabled=disabled, note=note)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ==================== 推广者汇总聚合 ====================

def _summary_fixture(db):
    aff_a = _user(db, "affA")
    aff_b = _user(db, "affB")
    code_a = _code(db, aff_a, "AFFA0001")
    code_b = _code(db, aff_b, "AFFB0001")
    e1, e2, e3, e4 = (_user(db, n) for n in ("e1", "e2", "e3", "e4"))
    _relation(db, aff_a, e1, code_a)
    _relation(db, aff_a, e2, code_a)
    _relation(db, aff_a, e3, code_a)
    _relation(db, aff_b, e4, code_b)
    _paid_order(db, e1, "100", "ord-e1")
    _paid_order(db, e2, "40", "ord-e2")
    _accrual(db, aff_a, e1, "100", "5.00", "credited", "ord-e1")
    _accrual(db, aff_a, e1, "50", "2.50", "pending", "ord-e1b")
    # 已撤回的一笔：付费与返利都不许计入汇总（与计提模块口径一致）
    _accrual(db, aff_a, e2, "40", "2.00", "reversed", "ord-e2")
    return aff_a, aff_b


def test_promoter_summary_aggregation(db):
    aff_a, aff_b = _summary_fixture(db)
    profile_only = _user(db, "affC")
    _profile(db, profile_only, note="渠道合作")

    total, rows = invite_promoter.promoter_summaries(db)
    assert total == 3
    by_id = {r["user_id"]: r for r in rows}

    a = by_id[aff_a.id]
    assert a["username"] == "affA"
    assert a["invited_count"] == 3
    assert a["paid_invitees"] == 2
    assert a["total_paid"] == "150.00"
    assert a["rebate_credited"] == "5.00"
    assert a["rebate_pending"] == "2.50"
    assert a["rebate_total"] == "7.50"
    # 无档案：走全局（默认 tiered、基础比例 5）
    assert a["rate_source"] == "global"
    assert a["rate_percent"] == 5
    assert a["rebate_percent_override"] is None
    assert a["is_disabled"] is False

    b = by_id[aff_b.id]
    assert b["invited_count"] == 1
    assert b["paid_invitees"] == 0
    assert b["total_paid"] == "0.00"

    c = by_id[profile_only.id]
    assert c["invited_count"] == 0
    assert c["total_paid"] == "0.00"
    assert c["note"] == "渠道合作"


def test_promoter_summary_search_and_sort(db):
    aff_a, aff_b = _summary_fixture(db)
    total, rows = invite_promoter.promoter_summaries(db, search="affa")
    assert total == 1 and rows[0]["user_id"] == aff_a.id

    total, rows = invite_promoter.promoter_summaries(
        db, sort="total_paid", order="desc")
    assert [r["user_id"] for r in rows] == [aff_a.id, aff_b.id]
    total, rows = invite_promoter.promoter_summaries(
        db, sort="total_paid", order="asc")
    assert [r["user_id"] for r in rows] == [aff_b.id, aff_a.id]

    total, rows = invite_promoter.promoter_summaries(db, page=2, page_size=1)
    assert total == 2 and len(rows) == 1


# ==================== 专属费率覆盖计提 ====================

def _payer_setup(db, *, mode=None):
    inviter = _user(db, "promo1")
    payer = _user(db, "payer1")
    code = _code(db, inviter, "PROMO001")
    _relation(db, inviter, payer, code)
    if mode:
        _set(db, "wallet_invite_rebate_mode", mode)
    return inviter, payer


def test_rebate_override_flat(db):
    inviter, payer = _payer_setup(db, mode="flat")
    _profile(db, inviter, override=10)
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-flat-1",
        paid_amount=Decimal("100"))
    assert row is not None
    assert row.rebate_amount == Decimal("10.00")
    assert row.percent == 10  # 快照记覆盖值


def test_rebate_override_beats_tiered(db):
    inviter, payer = _payer_setup(db)  # 默认 tiered
    # 先按全局分档计提一笔 $60：50×5% + 10×1% = 2.60
    first = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-tier-1",
        paid_amount=Decimal("60"))
    assert first.rebate_amount == Decimal("2.60")
    # 设覆盖 10% 后再付 $100：整笔按 10%（分档口径本应只剩 1% 段）
    _profile(db, inviter, override=10)
    second = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-tier-2",
        paid_amount=Decimal("100"))
    assert second.rebate_amount == Decimal("10.00")
    assert second.percent == 10
    # 历史那笔不动
    db.refresh(first)
    assert first.rebate_amount == Decimal("2.60") and first.percent == 5


def test_rebate_override_cap_still_applies(db):
    inviter, payer = _payer_setup(db, mode="flat")
    _set(db, "wallet_invite_rebate_cap", "500")  # 上限 $5
    _profile(db, inviter, override=10)
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-cap-1",
        paid_amount=Decimal("100"))
    assert row.rebate_amount == Decimal("5.00")


def test_rebate_override_zero_blocks_accrual(db):
    inviter, payer = _payer_setup(db, mode="flat")
    _profile(db, inviter, override=0)
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-zero-1",
        paid_amount=Decimal("100"))
    assert row is None


def test_rebate_override_cleared_back_to_global(db):
    inviter, payer = _payer_setup(db, mode="flat")
    profile = _profile(db, inviter, override=10)
    profile.rebate_percent_override = None
    db.commit()
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="ov-clear-1",
        paid_amount=Decimal("100"))
    assert row.rebate_amount == Decimal("5.00") and row.percent == 5


# ==================== 停用双拦截 ====================

def test_disabled_promoter_blocks_binding(db):
    inviter = _user(db, "affOff")
    code = _code(db, inviter, "AFFOFF01")
    _profile(db, inviter, disabled=True)
    newcomer = _user(db, "newbie1")

    result = invitation_api.apply_invitation(db, newcomer, code.code)
    assert result["applied"] is False
    assert result.get("reason") == "inviter_disabled"
    assert result.get("message")  # 有明确文案
    assert db.query(models.InvitationRecord).filter_by(
        invitee_id=newcomer.id).count() == 0
    db.refresh(code)
    assert code.use_count == 0  # 拒绝不占码名额


def test_disabled_promoter_blocks_accrual(db):
    inviter, payer = _payer_setup(db, mode="flat")
    _profile(db, inviter, disabled=True)
    row = invite_rebate.record_payment_rebate(
        db, payer, source="subscription_order", source_ref="off-acc-1",
        paid_amount=Decimal("100"))
    assert row is None
    assert db.query(models.InviteRebateAccrual).count() == 0


def test_disabled_promoter_recoverable(db):
    inviter = _user(db, "affBack")
    code = _code(db, inviter, "AFFBACK1")
    profile = _profile(db, inviter, disabled=True)
    newcomer = _user(db, "newbie2")
    assert invitation_api.apply_invitation(
        db, newcomer, code.code)["applied"] is False

    profile.is_disabled = False
    db.commit()
    result = invitation_api.apply_invitation(db, newcomer, code.code)
    assert result["applied"] is True
    row = invite_rebate.record_payment_rebate(
        db, newcomer, source="subscription_order", source_ref="back-acc-1",
        paid_amount=Decimal("100"))
    assert row is not None


# ==================== 管理端端点 ====================

def test_admin_update_promoter_profile_and_audit(db):
    admin = _user(db, "rootadmin")
    target = _user(db, "affZ")
    payload = admin_invitation_api.PromoterProfileUpdate(
        rebate_percent_override=12, is_disabled=False, note="头部渠道")
    out = admin_invitation_api.update_invite_promoter(
        target.id, payload, admin, db)
    assert out["promoter"]["rebate_percent_override"] == 12
    assert out["promoter"]["rate_source"] == "promoter"
    assert out["promoter"]["rate_percent"] == 12
    assert out["promoter"]["note"] == "头部渠道"

    log = (db.query(models.AdminLog)
           .filter_by(action="invite_promoter_update",
                      target_id=target.id).first())
    assert log is not None

    # 再存一次 = 更新同一行（清空覆盖 → 回全局）
    payload2 = admin_invitation_api.PromoterProfileUpdate(
        rebate_percent_override=None, is_disabled=True, note="")
    out2 = admin_invitation_api.update_invite_promoter(
        target.id, payload2, admin, db)
    assert out2["promoter"]["rebate_percent_override"] is None
    assert out2["promoter"]["rate_source"] == "global"
    assert out2["promoter"]["is_disabled"] is True
    assert db.query(models.InvitePromoterProfile).filter_by(
        user_id=target.id).count() == 1


def test_admin_update_promoter_validation(db):
    admin = _user(db, "rootadmin2")
    target = _user(db, "affY")
    with pytest.raises(HTTPException) as exc:
        admin_invitation_api.update_invite_promoter(
            target.id,
            admin_invitation_api.PromoterProfileUpdate(
                rebate_percent_override=101),
            admin, db)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        admin_invitation_api.update_invite_promoter(
            999999,
            admin_invitation_api.PromoterProfileUpdate(),
            admin, db)
    assert exc.value.status_code == 404


def test_admin_promoters_endpoint_and_export(db):
    admin = _user(db, "rootadmin3")
    aff_a, aff_b = _summary_fixture(db)
    out = admin_invitation_api.invite_promoters(
        search=None, sort="total_paid", order="desc", page=1, page_size=20,
        current_admin=admin, db=db)
    assert out["total"] == 2
    assert out["promoters"][0]["user_id"] == aff_a.id

    resp = admin_invitation_api.invite_promoters_export(
        search=None, sort="total_paid", order="desc",
        current_admin=admin, db=db)
    text = resp.body.decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert "累计付费（美元）" in lines[0]
    assert any("affA" in ln and "150.00" in ln for ln in lines[1:])
    resp2 = admin_invitation_api.invite_promoters_export(
        search="affB", sort="total_paid", order="desc",
        current_admin=admin, db=db)
    text2 = resp2.body.decode("utf-8-sig")
    assert "affB" in text2 and "affA" not in text2


def test_economy_invitations_inviter_filter(db):
    admin = _user(db, "rootadmin4")
    aff_a, aff_b = _summary_fixture(db)
    out = admin_economy.economy_list_invitations(
        limit=100, inviter_id=aff_a.id, current_admin=admin, db=db)
    assert out["total"] == 3
    assert {r["inviter"] for r in out["records"]} == {"affA"}
    assert {r["inviter_id"] for r in out["records"]} == {aff_a.id}
    out_all = admin_economy.economy_list_invitations(
        limit=100, current_admin=admin, db=db)
    assert out_all["total"] == 4
