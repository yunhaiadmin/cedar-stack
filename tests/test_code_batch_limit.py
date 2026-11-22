"""一人一批一码（2026-11-23 用户亲令）：同一批发放的卡码，同一用户只许核销一张。

背景：TG 群公开发 50 个余额码时，有用户手快抄走 2 个连兑。原有防线只有
「每人每码一次」（code_redemptions 唯一约束），挡不住同批不同码。
本批口径：
- 批次 = 一次批量生成动作（管理端生成 / 代理进货 / 用户礼品卡各自成批），
  生成时整批打同一个 batch_id；存量码 batch_id 为 NULL = 各自成批、不追溯。
- 核销校验走 codes.redeem_code 单口（钱包 redeem 与会员 redeem 共用），
  并发兜底靠 code_batch_claims 的 (batch_id, user_id) 唯一约束在同一
  事务里占位——只查不锁挡不住两个同批不同码同时提交。

用内存 SQLite，不碰生产库。
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import codes, models
from backend import wallet as wallet_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def grant_calls(db, monkeypatch):
    """会员天数发放打桩并计数：断言「被拒的那次没有发奖」靠它。"""
    calls = []

    def _fake_grant(db_, user, days, target):
        calls.append((user.id, days))
        return type("S", (), {"end_date": datetime.now()})()

    monkeypatch.setattr(codes, "grant_membership_days", _fake_grant)
    monkeypatch.setattr(codes, "code_realm_id", lambda *a, **k: None)
    monkeypatch.setattr(codes, "realm_label", lambda *a, **k: "主服")
    return calls


def _renew_code(db, raw, *, batch_id="batch-a", days=30):
    code = models.RegistrationCode(
        code=raw, code_type=codes.CODE_TYPE_RENEW, days=days,
        max_uses=1, is_active=True, batch_id=batch_id)
    db.add(code)
    db.commit()
    return code


def test_same_user_second_code_in_same_batch_rejected(db, grant_calls):
    """同批两张码：第一张成功，第二张被拒且不发奖、不烧码"""
    alice = _user(db, "alice")
    _renew_code(db, "REN-B1-001")
    second = _renew_code(db, "REN-B1-002")

    assert codes.redeem_code(db, alice, "REN-B1-001")["success"]
    db.commit()

    result = codes.redeem_code(db, alice, "REN-B1-002")
    assert not result["success"]
    assert result.get("error_code") == codes.CODE_ERROR_BATCH_LIMIT
    assert "每人只能领一个" in result["message"]
    db.rollback()

    assert len(grant_calls) == 1  # 只发了一次奖
    db.refresh(second)
    assert (second.use_count or 0) == 0  # 被拒的码没被消耗
    assert db.query(models.CodeRedemption).filter_by(user_id=alice.id).count() == 1


def test_same_user_codes_in_different_batches_both_ok(db, grant_calls):
    """不同批的两张码：同一用户可以各领一张"""
    alice = _user(db, "alice")
    _renew_code(db, "REN-B1-001", batch_id="batch-a")
    _renew_code(db, "REN-B2-001", batch_id="batch-b")

    assert codes.redeem_code(db, alice, "REN-B1-001")["success"]
    db.commit()
    assert codes.redeem_code(db, alice, "REN-B2-001")["success"]
    db.commit()
    assert len(grant_calls) == 2


def test_two_users_same_batch_both_ok(db, grant_calls):
    """同批不同码、不同用户：互不影响，各领一张"""
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    _renew_code(db, "REN-B1-001")
    _renew_code(db, "REN-B1-002")

    assert codes.redeem_code(db, alice, "REN-B1-001")["success"]
    db.commit()
    assert codes.redeem_code(db, bob, "REN-B1-002")["success"]
    db.commit()
    assert len(grant_calls) == 2


def test_legacy_codes_without_batch_unaffected(db, grant_calls):
    """存量码 batch_id 为空 = 各自成批：同一用户连领两张不受新限制影响"""
    alice = _user(db, "alice")
    _renew_code(db, "REN-OLD-001", batch_id=None)
    _renew_code(db, "REN-OLD-002", batch_id=None)

    assert codes.redeem_code(db, alice, "REN-OLD-001")["success"]
    db.commit()
    assert codes.redeem_code(db, alice, "REN-OLD-002")["success"]
    db.commit()
    assert len(grant_calls) == 2


def test_balance_codes_same_batch_second_rejected(db):
    """余额码同口径：同批第二张被拒，钱包只入账一次（今晚事故的原形态）"""
    alice = _user(db, "alice")
    for raw in ("BAL-B1-001", "BAL-B1-002"):
        db.add(models.RegistrationCode(
            code=raw, code_type=codes.CODE_TYPE_RENEW, days=0,
            max_uses=1, is_active=True, batch_id="batch-a",
            reward_type=codes.REWARD_BALANCE,
            balance_value=Decimal("1.20")))
    db.commit()

    assert codes.redeem_code(db, alice, "BAL-B1-001")["success"]
    db.commit()
    result = codes.redeem_code(db, alice, "BAL-B1-002")
    assert not result["success"]
    assert result.get("error_code") == codes.CODE_ERROR_BATCH_LIMIT
    db.rollback()
    assert wallet_service.get_balance(db, alice.id) == Decimal("1.20")


def test_batch_claim_unique_constraint_blocks_second_claim(db):
    """并发兜底：同批占位第二插必冲突——两个同批不同码同时提交只能成一单"""
    alice = _user(db, "alice")
    first = _renew_code(db, "REN-B1-001")
    second = _renew_code(db, "REN-B1-002")

    assert codes.claim_batch(db, first, alice.id)
    db.commit()
    assert not codes.claim_batch(db, second, alice.id)
    assert db.query(models.CodeBatchClaim).filter_by(user_id=alice.id).count() == 1


def test_claim_code_rejects_second_code_in_same_batch(db):
    """claim_code 本身带批次防线：第一张占位成功后，同批第二张占位被拒"""
    alice = _user(db, "alice")
    first = _renew_code(db, "REN-B1-001")
    second = _renew_code(db, "REN-B1-002")

    assert codes.claim_code(db, first, alice.id)
    db.commit()
    assert not codes.claim_code(db, second, alice.id)
    db.rollback()
    db.refresh(second)
    assert (second.use_count or 0) == 0


def test_admin_generation_stamps_one_batch_per_request(db):
    """管理端一次生成 = 一批：整批同 batch_id；再生成一次是另一批"""
    from backend.api import admin_ops as ops

    db.add(models.ServerRealm(name="默认服", slug="main", is_active=True))
    admin = models.WebUser(username="boss", password_hash="x",
                           is_staff=True, is_active=True)
    db.add(admin)
    db.commit()

    ops.generate_codes(ops.CodeGenerateRequest(count=3, code_type=2), admin, db)
    rows = db.query(models.RegistrationCode).all()
    assert len(rows) == 3
    batch_ids = {r.batch_id for r in rows}
    assert len(batch_ids) == 1 and None not in batch_ids

    ops.generate_codes(ops.CodeGenerateRequest(count=2, code_type=2), admin, db)
    rows = db.query(models.RegistrationCode).all()
    assert len({r.batch_id for r in rows}) == 2
