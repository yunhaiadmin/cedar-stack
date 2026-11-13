# tests/test_invitation_records_rebate.py
"""邀请记录「累计返利」归集回归测试（GET /api/user/invite/records）。

口径：返利流水（PointsLog type=rebate）的 ref_id 是 ``rebate:{充值订单号}``，
订单归属人就是被邀请人——按此把邀请人的返利台账拆回每个人头上。
归不上的（订单不存在/格式不对/负数冲正行）不计，宁可少算不许错算。
本接口只读台账，不发生任何发放。
"""

from __future__ import annotations

import sys
import uuid

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


def test_rebate_total_attributed_per_invitee(db):
    tag = uuid.uuid4().hex[:8]
    inviter = _user(db, f"rb-inviter-{tag}")
    invitee1 = _user(db, f"rb-invitee1-{tag}")
    invitee2 = _user(db, f"rb-invitee2-{tag}")

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
    pkg = models.RechargePackage(name="p", amount=100, price=10)
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    # invitee1 的充值订单（返利流水按它的订单号归集）
    db.add(models.RechargeOrder(
        order_id=f"RO-{tag}", user_id=invitee1.id, package_id=pkg.id,
        amount=1000, price=100, status="paid",
    ))
    db.commit()

    def _log(amount, type_, ref):
        return models.PointsLog(
            user_id=inviter.id, amount=amount, balance_after=0,
            type=type_, description="t", ref_id=ref,
        )

    db.add_all([
        _log(10, "rebate", f"rebate:RO-{tag}"),
        _log(5, "rebate", f"rebate:RO-{tag}"),
        _log(7, "rebate", "rebate:RO-NOT-MINE"),   # 订单不存在 → 不归集
        _log(-3, "rebate", f"rebate:RO-{tag}"),     # 冲正负数行 → 不计入累计
        _log(99, "invite", f"rebate:RO-{tag}"),     # 非返利类型 → 不计
    ])
    db.commit()

    out = my_invitation_records(50, inviter, db)
    by_name = {r["invitee_username"]: r for r in out["records"]}
    assert by_name[invitee1.username]["rebate_total"] == 15
    assert by_name[invitee2.username]["rebate_total"] == 0
