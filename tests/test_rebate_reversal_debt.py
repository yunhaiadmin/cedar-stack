"""r53 ⑧：订单退款时返利撤回欠账（2026-11-13 货币删除收口）。

积分冲正已随货币下线：orders_admin._reverse_points 是恒返 0 的桩，
_reverse_rebate 因此恒返 (0, False)、不再产生新欠账（退款口径如需
重定——例如返利改走余额后按人民币原路退——由 parent 另行拍板，
见 _reverse_points docstring）。本文件钉死当前行为：

- _reverse_rebate：有返利账本行也好、无也好，一律 (0, False)、不落欠账行；
- 欠账管理端点（列表/豁免/重试）仍在：欠账行改由测试直接落库覆盖，
  重试因冲正桩恒返 0 而标记 recovered、reversed_amount=0、不动余额。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import orders_admin
from backend.api.admin_core import get_current_admin
from backend.database import get_db


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _make_user(db, username="u1", points=1000):
    u = models.WebUser(username=username, password_hash="", points=points,
                       is_active=True, member_xp=0, member_level=1)
    db.add(u)
    db.commit()
    return u


def _seed_rebate(db, inviter_id, order_id="O1", amount=200):
    """履约时发给邀请人的返利账本行（ref_id = rebate:{order_id}，只读留存）"""
    db.add(models.PointsLog(user_id=inviter_id, amount=amount,
                            balance_after=amount, type="rebate",
                            description="邀请返利", ref_id=f"rebate:{order_id}"))
    db.commit()


def _seed_pending_debt(db, inviter_id, order_id, amount=200):
    """直接落一条 pending 欠账（_reverse_rebate 已不再产出欠账）。"""
    d = models.RebateReversalDebt(
        order_id=order_id, user_id=inviter_id, amount=amount,
        reason="邀请人余额不足", status="pending")
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _debts(db, order_id="O1"):
    return db.query(models.RebateReversalDebt).filter(
        models.RebateReversalDebt.order_id == order_id).all()


# ---------------- 撤回已是空操作（冲正桩恒返 0） ----------------

def test_reverse_rebate_is_noop_with_rebate_entry(db):
    """有返利账本行、余额不足：撤回不再扣回，也不再落欠账行。"""
    inviter = _make_user(db, "inviter", points=50)  # 余额 50 < 返利 200
    _seed_rebate(db, inviter.id, "O1", amount=200)

    reversed_amount, owed = orders_admin._reverse_rebate(db, "O1", allow_negative=False)
    db.commit()

    assert (reversed_amount, owed) == (0, False)
    assert _debts(db, "O1") == []


def test_reverse_rebate_repeated_calls_still_noop(db):
    """重复调用仍是空操作，不重复记账（也没有账可记）。"""
    inviter = _make_user(db, "inviter", points=50)
    _seed_rebate(db, inviter.id, "O2", amount=200)

    first = orders_admin._reverse_rebate(db, "O2", allow_negative=False)
    db.commit()
    second = orders_admin._reverse_rebate(db, "O2", allow_negative=False)
    db.commit()

    assert first == (0, False)
    assert second == (0, False)
    assert _debts(db, "O2") == []


def test_reverse_rebate_allow_negative_still_noop(db):
    """允许负数也不再扣回：冲正桩与 allow_negative 无关。"""
    inviter = _make_user(db, "inviter", points=50)
    _seed_rebate(db, inviter.id, "O3", amount=200)

    assert orders_admin._reverse_rebate(db, "O3", allow_negative=False) == (0, False)
    db.commit()
    reversed_amount, owed = orders_admin._reverse_rebate(db, "O3", allow_negative=True)
    db.commit()
    assert (reversed_amount, owed) == (0, False)
    assert _debts(db, "O3") == []


def test_reverse_rebate_never_reverses_points(db):
    """余额充足也不撤回：不报错、也不凭空造欠账行。"""
    inviter = _make_user(db, "inviter", points=1000)
    _seed_rebate(db, inviter.id, "O4", amount=200)

    assert orders_admin._reverse_rebate(db, "O4", allow_negative=False) == (0, False)
    db.commit()
    assert _debts(db, "O4") == []


def test_reverse_rebate_no_rebate_entry_no_debt(db):
    """这笔订单本来就没返利：不记账"""
    _make_user(db, "buyer", points=1000)
    assert orders_admin._reverse_rebate(db, "O5", allow_negative=False) == (0, False)
    db.commit()
    assert _debts(db, "O5") == []


# ---------------- 管理端只读列表＋豁免 ----------------

def _admin_app(db):
    app = FastAPI()
    app.include_router(orders_admin.admin_orders_router)
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=999)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_list_and_waive_rebate_debt(db):
    inviter = _make_user(db, "inviter", points=50)
    _seed_pending_debt(db, inviter.id, "O6", amount=200)

    client = _admin_app(db)
    resp = client.get("/api/admin/economy/rebate-debts", params={"status": "pending"})
    assert resp.status_code == 200
    recs = resp.json()["records"]
    assert len(recs) == 1
    assert recs[0]["order_id"] == "O6"
    assert recs[0]["amount"] == 200
    assert recs[0]["username"] == "inviter"
    debt_id = recs[0]["id"]

    w = client.post(f"/api/admin/economy/rebate-debts/{debt_id}/waive")
    assert w.status_code == 200
    assert w.json()["status"] == "waived"

    d = db.query(models.RebateReversalDebt).filter(
        models.RebateReversalDebt.id == debt_id).one()
    assert d.status == "waived"
    assert d.resolved_at is not None

    # 豁免过的欠账不能再豁免
    assert client.post(f"/api/admin/economy/rebate-debts/{debt_id}/waive").status_code == 404


# ---------------- r55：欠账重试收回端点 ----------------
# 冲正桩恒返 0：重试仍把 pending 标记 recovered、reversed_amount=0，
# 不动邀请人余额。余额不足分支已随积分腿下线（重试不再因余额不足而 pending）。

def _seed_pending_debt_for_retry(db, order_id="R1", amount=200, inviter_points=50):
    inviter = _make_user(db, f"inviter_{order_id}", points=inviter_points)
    d = _seed_pending_debt(db, inviter.id, order_id, amount=amount)
    return inviter, d.id


def test_retry_rebate_debt_success_marks_recovered(db):
    """重试：标记 recovered，reversed_amount=0、余额不动。"""
    inviter, debt_id = _seed_pending_debt_for_retry(db, "R1")
    client = _admin_app(db)

    resp = client.post(f"/api/admin/economy/rebate-debts/{debt_id}/retry",
                       json={"allow_negative": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "recovered"
    assert body["reversed_amount"] == 0

    db.expire_all()
    assert _debts(db, "R1")[0].status == "recovered"
    assert _debts(db, "R1")[0].resolved_at is not None
    assert db.query(models.WebUser).filter(
        models.WebUser.id == inviter.id).first().points == 50


def test_retry_rebate_debt_low_balance_still_recovers_zero(db):
    """余额不足也不再 pending：冲正桩不抛错，重试直接 recovered（0 元冲正）。"""
    inviter, debt_id = _seed_pending_debt_for_retry(db, "R2")
    client = _admin_app(db)

    resp = client.post(f"/api/admin/economy/rebate-debts/{debt_id}/retry",
                       json={"allow_negative": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "recovered"
    assert body["reversed_amount"] == 0

    db.expire_all()
    assert _debts(db, "R2")[0].status == "recovered"
    assert db.query(models.WebUser).filter(
        models.WebUser.id == inviter.id).first().points == 50


def test_retry_rebate_debt_second_retry_404(db):
    """已收回的欠账再重试：404，不重复处理。"""
    inviter, debt_id = _seed_pending_debt_for_retry(db, "R3")
    client = _admin_app(db)

    assert client.post(f"/api/admin/economy/rebate-debts/{debt_id}/retry",
                       json={"allow_negative": True}).status_code == 200
    resp = client.post(f"/api/admin/economy/rebate-debts/{debt_id}/retry",
                       json={"allow_negative": True})
    assert resp.status_code == 404

    db.expire_all()
    assert db.query(models.WebUser).filter(
        models.WebUser.id == inviter.id).first().points == 50  # 一分没动过


def test_retry_rebate_debt_nonexistent_404(db):
    client = _admin_app(db)
    resp = client.post("/api/admin/economy/rebate-debts/999999/retry",
                       json={"allow_negative": True})
    assert resp.status_code == 404
