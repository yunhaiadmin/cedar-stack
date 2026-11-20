# -*- coding: utf-8 -*-
"""用户自生成礼品卡（钱包页 ax 重做，2026-11-15）专项测试。

覆盖：生成扣款 + 码可被他人 redeem 入账闭环、余额不足 400、
valid_days 越界 400、金额格式/小数位 400、子账号 403、
过期退款只退一次、列表只见本人、状态筛选。
用内存 SQLite，不碰生产库。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models, ratelimit
from backend import wallet as wallet_service
from backend.api import wallet as wallet_api


@pytest.fixture()
def db():
    # 限流器是进程全局按 user_id 计数，各测试的用户 id 都会从 1 重来，
    # 不清会串扰（与 test_account_security 同一隔离写法）
    ratelimit._limiter._hits.clear()
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
        ratelimit._limiter._hits.clear()


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


def _fund(db, user, amount="100"):
    wallet_service.credit(db, user.id, Decimal(amount), kind="recharge")
    db.commit()


def _create(db, user, amount="25", days=7):
    return wallet_api.create_gift_code(
        wallet_api.WalletGiftCodeCreateRequest(amount=amount,
                                               valid_days=days),
        user, db)


# ==================== 生成 + 兑换闭环 ====================

def test_create_gift_code_debits_and_other_can_redeem(db, alice, bob):
    _fund(db, alice)
    resp = _create(db, alice, "25")
    assert resp["ok"] is True
    code_text = resp["code"]
    assert resp["amount"] == "25.00"
    assert resp["balance"] == "75.00"
    assert wallet_service.get_balance(db, alice.id) == Decimal("75.00")

    # 扣款流水：kind=gift_code、负数、ref 指向码行
    rows = (db.query(models.WalletLedger)
            .filter(models.WalletLedger.user_id == alice.id,
                    models.WalletLedger.kind == "gift_code").all())
    assert len(rows) == 1
    assert rows[0].amount == Decimal("-25.00")
    code_row = db.query(models.RegistrationCode).filter(
        models.RegistrationCode.code == code_text).one()
    assert rows[0].ref_id == f"code:{code_row.id}"
    assert code_row.source == "gift"
    assert code_row.reward_type == "balance"
    assert code_row.balance_value == Decimal("25.00")
    assert code_row.created_by == alice.id
    assert code_row.max_uses == 1

    # 他人兑换入账（走钱包页 redeem 端点）
    redeem = wallet_api.redeem_wallet_code(
        wallet_api.WalletRedeemRequest(code=code_text), bob, db)
    assert redeem["ok"] is True
    assert wallet_service.get_balance(db, bob.id) == Decimal("25.00")

    # 生成人列表里这张码是「已使用」
    listed = wallet_api.list_gift_codes("all", alice, db)
    mine = [i for i in listed["items"] if i["code"] == code_text]
    assert len(mine) == 1
    assert mine[0]["status"] == "used"
    assert mine[0]["amount"] == "25.00"


def test_create_gift_code_default_valid_days(db, alice):
    _fund(db, alice)
    resp = wallet_api.create_gift_code(
        wallet_api.WalletGiftCodeCreateRequest(amount="5"), alice, db)
    assert resp["ok"] is True
    assert resp["valid_days"] == 7


# ==================== 校验拒绝 ====================

def test_create_insufficient_balance_400(db, alice):
    _fund(db, alice, "10")
    resp = _create(db, alice, "25")
    assert resp.status_code == 400
    assert wallet_service.get_balance(db, alice.id) == Decimal("10.00")
    assert db.query(models.RegistrationCode).count() == 0


def test_create_valid_days_out_of_range_400(db, alice):
    _fund(db, alice)
    for bad in (0, 8, -1):
        resp = _create(db, alice, "5", days=bad)
        assert resp.status_code == 400, bad
    assert db.query(models.RegistrationCode).count() == 0
    assert wallet_service.get_balance(db, alice.id) == Decimal("100.00")


def test_create_bad_amount_400(db, alice):
    _fund(db, alice)
    for bad in ("0", "-5", "1.005", "abc", ""):
        resp = _create(db, alice, bad)
        assert resp.status_code == 400, bad
    assert db.query(models.RegistrationCode).count() == 0


def test_create_sub_account_forbidden(db, alice):
    sub = _user(db, "alice_sub", parent_user_id=alice.id)
    _fund(db, alice)
    with pytest.raises(HTTPException) as exc:
        _create(db, sub, "5")
    assert exc.value.status_code == 403


# ==================== 过期退款（惰性、只退一次） ====================

def test_expired_gift_code_refunded_once(db, alice):
    _fund(db, alice)
    resp = _create(db, alice, "30", days=1)
    code_text = resp["code"]
    assert wallet_service.get_balance(db, alice.id) == Decimal("70.00")

    code_row = db.query(models.RegistrationCode).filter(
        models.RegistrationCode.code == code_text).one()
    code_row.expires_at = datetime.now() - timedelta(hours=1)
    db.commit()

    # 第一次列表查询触发退款
    listed = wallet_api.list_gift_codes("all", alice, db)
    assert wallet_service.get_balance(db, alice.id) == Decimal("100.00")
    mine = [i for i in listed["items"] if i["code"] == code_text]
    assert mine[0]["status"] == "expired"
    db.refresh(code_row)
    assert code_row.is_active is False

    refunds = (db.query(models.WalletLedger)
               .filter(models.WalletLedger.user_id == alice.id,
                       models.WalletLedger.kind == "gift_code_refund").all())
    assert len(refunds) == 1
    assert refunds[0].amount == Decimal("30.00")

    # 再查不重复退
    wallet_api.list_gift_codes("all", alice, db)
    assert wallet_service.get_balance(db, alice.id) == Decimal("100.00")
    assert (db.query(models.WalletLedger)
            .filter(models.WalletLedger.kind == "gift_code_refund")
            .count()) == 1


def test_used_gift_code_not_refunded_after_expiry(db, alice, bob):
    _fund(db, alice)
    resp = _create(db, alice, "30", days=1)
    code_text = resp["code"]
    wallet_api.redeem_wallet_code(
        wallet_api.WalletRedeemRequest(code=code_text), bob, db)
    code_row = db.query(models.RegistrationCode).filter(
        models.RegistrationCode.code == code_text).one()
    code_row.expires_at = datetime.now() - timedelta(hours=1)
    db.commit()
    wallet_api.list_gift_codes("all", alice, db)
    # 已被兑换的码过期不退款：生成人余额保持扣后值
    assert wallet_service.get_balance(db, alice.id) == Decimal("70.00")
    assert (db.query(models.WalletLedger)
            .filter(models.WalletLedger.kind == "gift_code_refund")
            .count()) == 0


# ==================== 列表口径 ====================

def test_list_only_own_gift_codes(db, alice, bob):
    _fund(db, alice)
    _fund(db, bob)
    _create(db, alice, "11")
    _create(db, bob, "22")
    # 管理员发的余额码（source=admin）哪怕 created_by 是本人也不进列表
    db.add(models.RegistrationCode(
        code="ADM-BAL-001", code_type=2, days=0, max_uses=1, use_count=0,
        is_active=True, created_by=alice.id, source="admin",
        reward_type="balance", balance_value=Decimal("9")))
    db.commit()

    listed = wallet_api.list_gift_codes("all", alice, db)
    amounts = sorted(i["amount"] for i in listed["items"])
    assert amounts == ["11.00"]
    assert all(i["status"] == "unused" for i in listed["items"])


def test_list_status_filter(db, alice, bob):
    _fund(db, alice)
    _create(db, alice, "11")
    used = _create(db, alice, "22")
    wallet_api.redeem_wallet_code(
        wallet_api.WalletRedeemRequest(code=used["code"]), bob, db)

    assert wallet_api.list_gift_codes("all", alice, db)["total"] == 2
    unused = wallet_api.list_gift_codes("unused", alice, db)
    assert unused["total"] == 1 and unused["items"][0]["amount"] == "11.00"
    used_list = wallet_api.list_gift_codes("used", alice, db)
    assert used_list["total"] == 1 and used_list["items"][0]["amount"] == "22.00"
    assert wallet_api.list_gift_codes("expired", alice, db)["total"] == 0
