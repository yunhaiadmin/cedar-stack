"""钱包批 1 专项测试（2026-11-14）：余额账本 + 只增不改流水。

覆盖任务书点名的五项：并发扣款防负数、幂等重放、哈希链完整性、
调账审计（operator_id 与原因）、兑换码 balance 入账只成交一次。
另加：入账/查询端点、配置默认值、余额码生成校验、非余额码不烧码分流。
用内存/文件 SQLite，不碰生产库。
"""
import threading
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import codes, config_schema, models
from backend import wallet as wallet_service
from backend.api import admin_ops, admin_wallet
from backend.api import wallet as wallet_api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main",
                                   is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _user(db, username, **kw):
    user = models.WebUser(username=username, password_hash="x",
                          is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def alice(db):
    return _user(db, "alice")


@pytest.fixture()
def bob(db):
    return _user(db, "bob")


@pytest.fixture()
def admin(db):
    return _user(db, "admin_test", is_staff=True)


# ==================== 入账 / 余额 / 哈希链 ====================

def test_credit_creates_balance_and_ledger(db, alice):
    entry, replayed = wallet_service.credit(
        db, alice.id, Decimal("50"), kind="gift_code", ref_id="code:1",
        note="兑换码入账")
    db.commit()
    assert replayed is False
    assert wallet_service.get_balance(db, alice.id) == Decimal("50.00")
    assert entry.balance_after == Decimal("50.00")
    assert entry.prev_hash == ""  # 链头
    assert entry.record_hash  # 已串链
    assert wallet_service.verify_chain(db, alice.id) is True


def test_credit_chain_links_and_detects_tamper(db, alice):
    wallet_service.credit(db, alice.id, Decimal("10"), kind="recharge")
    second, _ = wallet_service.credit(db, alice.id, Decimal("5"),
                                      kind="recharge")
    db.commit()
    assert second.prev_hash != ""
    assert wallet_service.verify_chain(db, alice.id) is True
    # 篡改历史行金额 → 链校验必须失败（只增不改的审计意义）
    row = db.query(models.WalletLedger).order_by(
        models.WalletLedger.id).first()
    row.amount = Decimal("999")
    db.commit()
    assert wallet_service.verify_chain(db, alice.id) is False


def test_balance_of_unknown_user_is_zero(db):
    assert wallet_service.get_balance(db, 99999) == Decimal("0")


def test_credit_rejects_non_positive(db, alice):
    with pytest.raises(wallet_service.WalletError):
        wallet_service.credit(db, alice.id, Decimal("0"), kind="recharge")
    db.rollback()


# ==================== 幂等重放 ====================

def test_credit_idempotent_replay(db, alice):
    first, replayed1 = wallet_service.credit(
        db, alice.id, Decimal("20"), kind="recharge",
        idempotency_key="topup-order-1")
    db.commit()
    second, replayed2 = wallet_service.credit(
        db, alice.id, Decimal("20"), kind="recharge",
        idempotency_key="topup-order-1")
    db.commit()
    assert replayed1 is False and replayed2 is True
    assert first.id == second.id
    assert wallet_service.get_balance(db, alice.id) == Decimal("20.00")
    assert db.query(models.WalletLedger).count() == 1


def test_idempotency_key_of_other_user_rejected(db, alice, bob):
    wallet_service.credit(db, alice.id, Decimal("20"), kind="recharge",
                          idempotency_key="shared-key")
    db.commit()
    with pytest.raises(wallet_service.WalletError):
        wallet_service.credit(db, bob.id, Decimal("20"), kind="recharge",
                              idempotency_key="shared-key")
    db.rollback()
    assert wallet_service.get_balance(db, bob.id) == Decimal("0")


def test_debit_idempotent_replay(db, alice):
    wallet_service.credit(db, alice.id, Decimal("100"), kind="recharge")
    db.commit()
    wallet_service.debit(db, alice.id, Decimal("30"),
                         kind="subscription_purchase",
                         idempotency_key="pay-sub-1")
    db.commit()
    _row, replayed = wallet_service.debit(
        db, alice.id, Decimal("30"), kind="subscription_purchase",
        idempotency_key="pay-sub-1")
    db.commit()
    assert replayed is True
    assert wallet_service.get_balance(db, alice.id) == Decimal("70.00")


# ==================== 扣款原语：防负数 ====================

def test_debit_insufficient_rejected_atomically(db, alice):
    wallet_service.credit(db, alice.id, Decimal("10"), kind="recharge")
    db.commit()
    with pytest.raises(wallet_service.InsufficientWalletBalance):
        wallet_service.debit(db, alice.id, Decimal("10.01"),
                             kind="subscription_purchase")
    db.rollback()
    assert wallet_service.get_balance(db, alice.id) == Decimal("10.00")
    # 失败不落流水：只有入账那一条
    assert db.query(models.WalletLedger).count() == 1


def test_concurrent_debits_never_go_negative(tmp_path):
    """20 个线程各扣 10 元，余额只有 100：恰好 10 笔成交、余额归 0、
    流水 10 条、哈希链仍完整（条件 UPDATE + 用户行锁串行化）。"""
    engine = create_engine(
        f"sqlite:///{tmp_path}/wallet_conc.db",
        connect_args={"timeout": 30})
    models.Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine)
    seed = maker()
    user = models.WebUser(username="conc", password_hash="x", is_active=True)
    seed.add(user)
    seed.commit()
    uid = user.id
    wallet_service.credit(seed, uid, Decimal("100"), kind="recharge")
    seed.commit()
    seed.close()

    results = {"ok": 0, "insufficient": 0, "errors": []}
    lock = threading.Lock()

    def worker(i):
        session = maker()
        try:
            wallet_service.debit(
                session, uid, Decimal("10"), kind="subscription_purchase",
                idempotency_key=f"conc-pay-{i}")
            session.commit()
            with lock:
                results["ok"] += 1
        except wallet_service.InsufficientWalletBalance:
            session.rollback()
            with lock:
                results["insufficient"] += 1
        except Exception as exc:  # noqa: BLE001 — 收集后统一断言
            session.rollback()
            with lock:
                results["errors"].append(repr(exc))
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["errors"] == []
    assert results["ok"] == 10
    assert results["insufficient"] == 10
    check = maker()
    try:
        assert wallet_service.get_balance(check, uid) == Decimal("0.00")
        debits = (check.query(models.WalletLedger)
                  .filter(models.WalletLedger.amount < 0).count())
        assert debits == 10
        assert wallet_service.verify_chain(check, uid) is True
    finally:
        check.close()


# ==================== 管理员调账（审计） ====================

def test_admin_adjust_records_operator_and_reason(db, alice, admin):
    entry, _ = wallet_service.admin_adjust(
        db, alice.id, Decimal("88.5"), note="活动补偿", operator_id=admin.id)
    db.commit()
    assert entry.kind == "admin_adjust"
    assert entry.operator_id == admin.id
    assert entry.note == "活动补偿"
    assert wallet_service.get_balance(db, alice.id) == Decimal("88.50")

    entry2, _ = wallet_service.admin_adjust(
        db, alice.id, Decimal("-8.5"), note="误发回收", operator_id=admin.id)
    db.commit()
    assert entry2.amount == Decimal("-8.50")
    assert wallet_service.get_balance(db, alice.id) == Decimal("80.00")


def test_admin_adjust_validations(db, alice, admin):
    with pytest.raises(wallet_service.WalletError):
        wallet_service.admin_adjust(db, alice.id, Decimal("0"),
                                    note="x", operator_id=admin.id)
    db.rollback()
    with pytest.raises(wallet_service.WalletError):
        wallet_service.admin_adjust(db, alice.id, Decimal("1"),
                                    note="  ", operator_id=admin.id)
    db.rollback()
    with pytest.raises(wallet_service.InsufficientWalletBalance):
        wallet_service.admin_adjust(db, alice.id, Decimal("-1"),
                                    note="扣回", operator_id=admin.id)
    db.rollback()
    assert db.query(models.WalletLedger).count() == 0


def test_admin_adjust_endpoint(db, alice, admin):
    resp = admin_wallet.wallet_admin_adjust(
        admin_wallet.WalletAdjustRequest(
            user_id=alice.id, amount="25.00", reason="手工充值"),
        admin, db)
    assert resp["ok"] is True
    assert resp["balance"] == "25.00"
    row = db.query(models.WalletLedger).one()
    assert row.operator_id == admin.id and row.note == "手工充值"

    with pytest.raises(HTTPException) as excinfo:
        admin_wallet.wallet_admin_adjust(
            admin_wallet.WalletAdjustRequest(
                user_id=alice.id, amount="-999", reason="扣回"),
            admin, db)
    assert excinfo.value.status_code == 400
    assert wallet_service.get_balance(db, alice.id) == Decimal("25.00")


def test_admin_balance_and_ledger_endpoints(db, alice, admin):
    wallet_service.credit(db, alice.id, Decimal("12"), kind="gift_code")
    db.commit()
    resp = admin_wallet.wallet_admin_balance(alice.id, admin, db)
    assert resp["balance"] == "12.00"
    assert resp["ledger_total"] == 1
    page = admin_wallet.wallet_admin_ledger(
        user_id=alice.id, kind="", start="", end="", page=1, page_size=20,
        current_admin=admin, db=db)
    assert page["total"] == 1
    assert page["items"][0]["username"] == "alice"
    with pytest.raises(HTTPException):
        admin_wallet.wallet_admin_balance(99999, admin, db)


# ==================== 配置默认值（§10 定版） ====================

def test_wallet_config_defaults(db):
    config = wallet_service.wallet_config(db)
    assert config == {
        "wallet_enabled": True,
        "wallet_min_topup_amount": 1000,
        "wallet_topup_fee_pct": 0,
        "wallet_invite_rebate_percent": 5,
        "wallet_invite_rebate_cap": 0,
        # 返利分档三键：与 #175 invite_rebate 的 DEFAULT_* 逐字一致
        "wallet_invite_rebate_mode": "tiered",
        "wallet_invite_rebate_tier_threshold": 35000,
        "wallet_invite_rebate_tier_percent": 1,
        "traffic_balance_price_per_100gb": 300,
        "wallet_refund_to_balance": True,
        "wallet_auto_renew_enabled": False,
    }


def test_wallet_settings_endpoint_roundtrip(db, admin):
    resp = admin_wallet.wallet_admin_get_settings(admin, db)
    assert resp["settings"]["wallet_invite_rebate_percent"] == "5"
    # 返利分档三键（#175 invite_rebate 读）：无库行时回代码默认
    assert resp["settings"]["wallet_invite_rebate_mode"] == "tiered"
    assert resp["settings"]["wallet_invite_rebate_tier_threshold"] == "35000"
    assert resp["settings"]["wallet_invite_rebate_tier_percent"] == "1"
    out = admin_wallet.wallet_admin_update_settings(
        admin_wallet.WalletSettingsRequest(
            settings={"wallet_invite_rebate_percent": 8}),
        admin, db)
    assert out["ok"] is True
    assert wallet_service.wallet_config(db)["wallet_invite_rebate_percent"] == 8
    out = admin_wallet.wallet_admin_update_settings(
        admin_wallet.WalletSettingsRequest(settings={
            "wallet_invite_rebate_mode": "flat",
            "wallet_invite_rebate_tier_threshold": 20000,
            "wallet_invite_rebate_tier_percent": 2}),
        admin, db)
    assert out["ok"] is True
    config = wallet_service.wallet_config(db)
    assert config["wallet_invite_rebate_mode"] == "flat"
    assert config["wallet_invite_rebate_tier_threshold"] == 20000
    assert config["wallet_invite_rebate_tier_percent"] == 2
    # mode 只收 tiered/flat 两值：schema enum 校验拒绝其余取值
    with pytest.raises(config_schema.ConfigValueError):
        admin_wallet.wallet_admin_update_settings(
            admin_wallet.WalletSettingsRequest(
                settings={"wallet_invite_rebate_mode": "bogus"}),
            admin, db)
    with pytest.raises(HTTPException):
        admin_wallet.wallet_admin_update_settings(
            admin_wallet.WalletSettingsRequest(settings={"nope": 1}),
            admin, db)


# ==================== 兑换码 balance 入账（只成交一次） ====================

def _balance_code(db, code_text="BAL-TEST-001", value="50", max_uses=1):
    code = models.RegistrationCode(
        code=code_text, code_type=2, days=0, max_uses=max_uses, use_count=0,
        is_active=True, reward_type="balance",
        balance_value=Decimal(value), realm_id=1, source="admin")
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


def test_redeem_balance_code_credits_once(db, alice, bob):
    code = _balance_code(db)
    result = codes.redeem_code(db, alice, code.code)
    assert result["success"] is True
    db.commit()
    assert wallet_service.get_balance(db, alice.id) == Decimal("50.00")
    rows = db.query(models.WalletLedger).all()
    assert len(rows) == 1
    assert rows[0].kind == "gift_code"
    assert rows[0].ref_id == f"code:{code.id}"

    # 同一人再兑：每人每码一次，直接拒绝，余额不动
    again = codes.redeem_code(db, alice, code.code)
    assert again["success"] is False
    db.rollback()
    assert wallet_service.get_balance(db, alice.id) == Decimal("50.00")

    # 别人再兑：max_uses=1 已用尽，拒绝，余额不动
    other = codes.redeem_code(db, bob, code.code)
    assert other["success"] is False
    db.rollback()
    assert wallet_service.get_balance(db, bob.id) == Decimal("0")
    assert db.query(models.WalletLedger).count() == 1


def test_redeem_balance_code_bad_face_value_not_consumed(db, alice):
    code = _balance_code(db, code_text="BAL-BAD-001", value="0")
    result = codes.redeem_code(db, alice, code.code)
    assert result["success"] is False
    db.rollback()
    db.refresh(code)
    assert (code.use_count or 0) == 0  # 坏码不烧码
    assert wallet_service.get_balance(db, alice.id) == Decimal("0")


def test_wallet_redeem_endpoint_rejects_non_balance_code(db, alice):
    code = models.RegistrationCode(
        code="SUB-TEST-001", code_type=2, days=30, max_uses=1, use_count=0,
        is_active=True, reward_type="subscription", realm_id=1,
        source="admin")
    db.add(code)
    db.commit()
    resp = wallet_api.redeem_wallet_code(
        wallet_api.WalletRedeemRequest(code=code.code), alice, db)
    assert resp.status_code == 400
    db.refresh(code)
    assert (code.use_count or 0) == 0  # 分流拒绝不烧码


def test_wallet_redeem_endpoint_credits_balance_code(db, alice):
    _balance_code(db, code_text="BAL-API-001", value="66")
    resp = wallet_api.redeem_wallet_code(
        wallet_api.WalletRedeemRequest(code="BAL-API-001"), alice, db)
    assert resp["ok"] is True
    assert resp["balance"] == "66.00"
    assert wallet_service.get_balance(db, alice.id) == Decimal("66.00")


def test_user_wallet_endpoints(db, alice):
    wallet_service.credit(db, alice.id, Decimal("9.9"), kind="admin_adjust",
                          note="测试", operator_id=None)
    db.commit()
    resp = wallet_api.get_wallet(alice, db)
    assert resp["balance"] == "9.90"
    assert resp["wallet_enabled"] is True
    page = wallet_api.get_wallet_ledger(kind="", page=1, page_size=20,
                                        current_user=alice, db=db)
    assert page["total"] == 1
    assert page["items"][0]["kind"] == "admin_adjust"
    page2 = wallet_api.get_wallet_ledger(kind="recharge", page=1,
                                         page_size=20, current_user=alice,
                                         db=db)
    assert page2["total"] == 0


# ==================== 余额码生成校验 ====================

def _gen_request(**kw):
    base = dict(code_type=2, count=1, reward_type="balance",
                balance_value="50")
    base.update(kw)
    return admin_ops.CodeGenerateRequest(**base)


def test_generate_balance_code(db, admin):
    resp = admin_ops.generate_codes(_gen_request(), admin, db)
    assert resp["success"] is True
    code = db.query(models.RegistrationCode).one()
    assert code.reward_type == "balance"
    assert code.balance_value == Decimal("50.00")


def test_generate_balance_code_validations(db, admin):
    with pytest.raises(HTTPException):
        admin_ops.generate_codes(_gen_request(balance_value="0"), admin, db)
    db.rollback()
    with pytest.raises(HTTPException):
        admin_ops.generate_codes(_gen_request(balance_value="abc"), admin, db)
    db.rollback()
    with pytest.raises(HTTPException):
        admin_ops.generate_codes(_gen_request(code_type=3), admin, db)
    db.rollback()
    assert db.query(models.RegistrationCode).count() == 0


def test_preview_balance_code(db, alice):
    code = _balance_code(db, code_text="BAL-PREV-001", value="25")
    preview = codes.preview_code(db, code.code, alice.username)
    assert preview["valid"] is True
    assert preview["reward_type"] == "balance"
    assert preview["balance_value"] == "25.00"
