# tests/test_invitation_records_rebate.py
"""邀请记录「累计返利」归集回归测试（GET /api/user/invite/records）。

口径（2026-11-14 钱包时代起）：返利读计提台账 invite_rebate_accruals
（被邀请人付费时按 (inviter, invitee) 逐笔落行，pending+credited 合计，
单位元）。旧口径（PointsLog type=rebate 按充值订单号拆算）已随积分
货币下线作废——台账里再有旧流水也不许混进这个数字。
另：被邀请人用户名在记录里必须脱敏（只露首尾），并带「是否已付费」。
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api.invitation import my_invitation_records


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture()
def db():
    engine = _engine()
    models.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    session = SessionLocal()
    from backend.integrations import store

    store.invalidate()
    try:
        yield session
    finally:
        session.close()
        store.invalidate()


def _user(db, username: str) -> models.WebUser:
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _setup(db, tag):
    inviter = _user(db, f"alice{tag}")
    invitee1 = _user(db, f"bob{tag}")
    invitee2 = _user(db, f"carol{tag}")
    code = models.InvitationCode(
        code=f"RB{tag.upper()}", user_id=inviter.id,
        is_active=True, max_uses=0, use_count=2, reward_points=0,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    db.add_all([
        models.InvitationRecord(
            inviter_id=inviter.id, invitee_id=invitee1.id,
            code_id=code.id, reward_points=0,
        ),
        models.InvitationRecord(
            inviter_id=inviter.id, invitee_id=invitee2.id,
            code_id=code.id, reward_points=0,
        ),
    ])
    db.commit()
    return inviter, invitee1, invitee2


def _accrual(db, inviter, invitee, paid, rebate, status="pending", ref="SO-1"):
    row = models.InviteRebateAccrual(
        inviter_id=inviter.id, invitee_id=invitee.id,
        source="subscription_order", source_ref=ref,
        paid_amount=Decimal(paid), rebate_amount=Decimal(rebate),
        percent=5, status=status,
    )
    db.add(row)
    db.commit()
    return row


def test_rebate_total_from_accruals_per_invitee(db):
    tag = uuid.uuid4().hex[:6]
    inviter, invitee1, invitee2 = _setup(db, tag)
    _accrual(db, inviter, invitee1, "45.00", "2.25", ref="SO-A")
    _accrual(db, inviter, invitee1, "100.00", "5.00", status="credited", ref="SO-B")
    # 别人的计提行不许串进来
    _accrual(db, invitee2, invitee1, "999.00", "49.95", ref="SO-C")

    out = my_invitation_records(50, inviter, db)
    assert len(out["records"]) == 2
    totals = sorted(r["rebate_total"] for r in out["records"])
    assert totals == [0.0, 7.25]
    # 脱敏：完整用户名不许出现在记录里，只露首尾
    for r in out["records"]:
        assert tag not in r["invitee_username"]
        assert "***" in r["invitee_username"]


def test_paid_flag_from_subscription_orders(db):
    tag = uuid.uuid4().hex[:6]
    inviter, invitee1, invitee2 = _setup(db, tag)
    plan = models.SubscriptionPlan(name="p", price=Decimal("45"), duration_days=30)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    db.add(models.SubscriptionOrder(
        order_id=f"SO-{tag}", user_id=invitee1.id, plan_id=plan.id,
        amount=Decimal("45.00"), status="paid",
    ))
    db.add(models.SubscriptionOrder(
        order_id=f"SO2-{tag}", user_id=invitee2.id, plan_id=plan.id,
        amount=Decimal("45.00"), status="pending",
    ))
    db.commit()

    out = my_invitation_records(50, inviter, db)
    paid_flags = sorted(r["paid"] for r in out["records"])
    assert paid_flags == [False, True]


def test_legacy_pointslog_rebate_not_counted(db):
    """旧积分返利流水（PointsLog）不得混进新口径的累计返利。"""
    tag = uuid.uuid4().hex[:6]
    inviter, invitee1, _ = _setup(db, tag)
    db.add(models.PointsLog(
        user_id=inviter.id, amount=500, balance_after=0,
        type="rebate", description="旧返利", ref_id="rebate:RO-OLD",
    ))
    db.commit()

    out = my_invitation_records(50, inviter, db)
    assert all(r["rebate_total"] == 0 for r in out["records"])
