"""钱包批 3 专项测试（2026-11-19）：在线充值（易支付）+ 自动汇率。

覆盖任务书点名项（金额口径 2026-11-19 改定：美元计价）：
- 创建校验：低于 $1 拒、三键缺失 503、子账号 403、qqpay 门禁；
- 回调：验签失败不入账、金额对不上不入账、重复回调只入账一次；
- 状态查询：本人 pending/paid 可查、他人单与不存在单 404、未登录 401；
- 汇率快照：下单后改价不影响已下单的应付与入账额；
- 返利计提：source=wallet_topup 被计提并入账邀请人；
- 汇率解析：主源/备源/旧缓存/手填兜底/强制覆盖/缓冲计算（HTTP 全 mock，
  测试不许真外呼）；
- 换算纯函数：美元→应付人民币取整、到账＝选定美元额扣费，口径锁死。

用内存 SQLite（StaticPool），不碰生产库。
"""
import os
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-wallet-topup-key-32chars!!")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import invite_rebate, models
from backend import wallet as wallet_service
from backend import wallet_topup
from backend.api import economy
from backend.api import wallet as wallet_api
from backend.integrations import store

PID = "1001"
PKEY = "test-partner-key"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        store.invalidate()


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """汇率抓取默认全挂（个别测试再覆写）：测试不许真外呼。"""
    def _boom(url, timeout=3.0):
        raise RuntimeError("test: no real http")
    monkeypatch.setattr(wallet_topup, "_fetch_json", _boom)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """下单限流不是本单被测对象：打桩放行，避免同进程跨用例串扰。"""
    monkeypatch.setattr(wallet_api, "check_rate_limit",
                        lambda *a, **k: (True, 0))


def _set(db, key, value):
    row = db.query(models.SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()
    store.invalidate(key)


def _user(db, username, **kw):
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _gateway(db):
    _set(db, "payment_gateway_url", "https://pay.example.com")
    _set(db, "payment_partner_id", PID)
    _set(db, "payment_partner_key", PKEY)


def _manual_rate(db, rate="7.2"):
    """手填覆盖开 + 手填价：让下单汇率确定，不依赖自动抓价。"""
    _set(db, "wallet_topup_cny_rate", rate)
    _set(db, "wallet_topup_rate_manual_override", "true")


def _create(db, user, amount="9.80", method="alipay"):
    return wallet_api.create_topup_order(
        wallet_api.WalletTopupCreateRequest(
            amount_usd=amount, payment_method=method),
        user, db)


def _notify_raw(order_no, money="71.97", status="TRADE_SUCCESS", key=PKEY):
    # 默认金额对应标准单：$9.80 @生效汇率 7.344 → 应付 ¥71.97
    raw = {
        "pid": PID, "trade_no": "T202611190001", "out_trade_no": order_no,
        "type": "alipay", "name": "钱包充值", "money": money,
        "trade_status": status,
    }
    raw["sign"] = economy._yipay_sign(raw, key)
    raw["sign_type"] = "MD5"
    return raw


def _seed_cache(db, rate="7.50", age_hours=0):
    _set(db, "wallet_topup_rate_auto", rate)
    fetched = datetime.now() - timedelta(hours=age_hours)
    _set(db, "wallet_topup_rate_fetched_at", fetched.isoformat())


# ==================== 换算纯函数 ====================

def test_compute_topup_rounding_and_fee():
    # $9.80 @7.344 无手续费：应付 = 980 × 7.344 = 7197.12 → 7197 分，到账全额
    cny, fee, credit = wallet_topup.compute_topup(980, Decimal("7.344"), 0)
    assert (cny, fee, credit) == (7197, 0, 980)
    # $1 @7.2：应付 720 分 = ¥7.20
    cny, fee, credit = wallet_topup.compute_topup(100, Decimal("7.2"), 0)
    assert (cny, fee, credit) == (720, 0, 100)
    # 取整半入：$1.15 @7.25 → 115 × 7.25 = 833.75 → 834 分
    cny, fee, credit = wallet_topup.compute_topup(115, Decimal("7.25"), 0)
    assert (cny, fee, credit) == (834, 0, 115)
    # 手续费 10% 加在应付侧：$10 @7.2 → 无费应付 7200，含费 7200×1.1
    # = 7920，其中费 720 分；到账仍是选定额全额 1000 美分
    cny, fee, credit = wallet_topup.compute_topup(1000, Decimal("7.2"), 10)
    assert (cny, fee, credit) == (7920, 720, 1000)
    # $10 @7.344 费 10%：7344 × 1.1 = 8078.4 → 8078 分
    cny, fee, credit = wallet_topup.compute_topup(1000, Decimal("7.344"), 10)
    assert (cny, fee, credit) == (8078, 734, 1000)


# ==================== 汇率解析 ====================

def test_rate_fresh_cache_skips_fetch(db, monkeypatch):
    _seed_cache(db, "7.50", age_hours=0)
    called = []
    monkeypatch.setattr(wallet_topup, "_fetch_json",
                        lambda url, timeout=3.0: called.append(url) or {})
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.50")
    assert called == []


def test_rate_stale_cache_primary_source(db, monkeypatch):
    _seed_cache(db, "7.50", age_hours=2)
    def fake(url, timeout=3.0):
        assert "frankfurter" in url
        return {"rates": {"CNY": 7.31}}
    monkeypatch.setattr(wallet_topup, "_fetch_json", fake)
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.31")
    # 缓存已刷新为新价
    row = db.query(models.SystemConfig).filter_by(
        key="wallet_topup_rate_auto").first()
    assert row.value == "7.31"


def test_rate_primary_down_uses_backup(db, monkeypatch):
    _seed_cache(db, "7.50", age_hours=2)
    def fake(url, timeout=3.0):
        if "frankfurter" in url:
            raise RuntimeError("primary down")
        return {"result": "success", "rates": {"CNY": 7.42}}
    monkeypatch.setattr(wallet_topup, "_fetch_json", fake)
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.42")


def test_rate_both_down_uses_stale_cache(db):
    _seed_cache(db, "7.50", age_hours=5)
    # autouse fixture 已让 _fetch_json 全挂
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.50")


def test_rate_no_cache_falls_back_to_manual_row(db):
    _set(db, "wallet_topup_cny_rate", "7.05")
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.05")


def test_rate_no_cache_no_manual_uses_default(db):
    assert wallet_topup.resolve_market_rate(db) == Decimal("7.2")


def test_rate_manual_override_wins(db, monkeypatch):
    _seed_cache(db, "7.50", age_hours=0)
    _manual_rate(db, "6.66")
    called = []
    monkeypatch.setattr(wallet_topup, "_fetch_json",
                        lambda url, timeout=3.0: called.append(url) or {})
    assert wallet_topup.resolve_market_rate(db) == Decimal("6.66")
    assert called == []


def test_effective_rate_applies_buffer(db):
    _seed_cache(db, "7.50", age_hours=0)
    # 默认缓冲 2%：7.50 × 1.02 = 7.65
    assert wallet_topup.effective_rate(db) == Decimal("7.65")
    _set(db, "wallet_topup_rate_buffer_pct", "0")
    assert wallet_topup.effective_rate(db) == Decimal("7.50")


# ==================== 创建订单校验 ====================

def test_create_below_min_rejected(db):
    _gateway(db)
    _manual_rate(db)
    user = _user(db, "u-min")
    with pytest.raises(HTTPException) as exc:
        _create(db, user, amount="0.99")
    assert exc.value.status_code == 400
    # 最低额走配置：管理员调到 $5 后，$4.99 同样拒、$5 放行
    _set(db, "wallet_min_topup_amount", "500")
    with pytest.raises(HTTPException) as exc:
        _create(db, user, amount="4.99")
    assert exc.value.status_code == 400
    assert _create(db, user, amount="5.00")["ok"] is True


def test_create_gateway_unconfigured_503(db):
    _manual_rate(db)
    user = _user(db, "u-nogw")
    with pytest.raises(HTTPException) as exc:
        _create(db, user)
    assert exc.value.status_code == 503


def test_create_sub_account_forbidden(db):
    _gateway(db)
    _manual_rate(db)
    parent = _user(db, "u-parent")
    sub = _user(db, "u-sub", parent_user_id=parent.id)
    with pytest.raises(HTTPException) as exc:
        _create(db, sub)
    assert exc.value.status_code == 403


def test_create_qqpay_gated(db):
    _gateway(db)
    _manual_rate(db)
    user = _user(db, "u-qq")
    with pytest.raises(HTTPException) as exc:
        _create(db, user, method="qqpay")
    assert exc.value.status_code == 400
    _set(db, "payment_qqpay_enabled", "true")
    resp = _create(db, user, method="qqpay")
    assert resp["ok"] is True


def test_create_wallet_disabled_403(db):
    _gateway(db)
    _manual_rate(db)
    _set(db, "wallet_enabled", "false")
    user = _user(db, "u-off")
    with pytest.raises(HTTPException) as exc:
        _create(db, user)
    assert exc.value.status_code == 403


def test_create_success_snapshots(db):
    _gateway(db)
    _manual_rate(db, "7.2")  # 缓冲默认 2% → 生效 7.344
    user = _user(db, "u-ok")
    resp = _create(db, user, amount="9.80")
    assert resp["ok"] is True
    order = db.query(models.WalletTopupOrder).filter_by(
        order_no=resp["order_no"]).first()
    assert order is not None
    # 美元计价：选定 $9.80；应付 = 980 × 7.344 = 7197.12 → 7197 分
    assert order.amount_usd_fen == 980
    assert order.amount_cny_fen == 7197
    assert Decimal(str(order.rate_snapshot)) == Decimal("7.344")
    # 到账 ＝ 选定美元额（费 0 全额）
    assert order.credit_usd_fen == 980
    assert order.status == "pending"
    assert resp["amount_cny"] == "71.97"
    assert resp["credit_usd"] == "9.80"
    assert resp["pay_url"].startswith("https://pay.example.com/submit.php?")
    assert resp["order_no"] in resp["pay_url"]
    assert "money=71.97" in resp["pay_url"]


def test_create_with_fee_snapshots(db):
    _gateway(db)
    _manual_rate(db, "7.2")
    _set(db, "wallet_topup_fee_pct", "10")
    user = _user(db, "u-fee")
    resp = _create(db, user, amount="10.00")
    order = db.query(models.WalletTopupOrder).filter_by(
        order_no=resp["order_no"]).first()
    # 手续费加应付侧：1000 × 7.344 × 1.1 = 8078.4 → 8078 分；
    # 到账 ＝ 选定额全额 1000 美分（不扣费）
    assert order.amount_cny_fen == 8078
    assert order.credit_usd_fen == 1000
    assert order.fee_pct_snapshot == 10
    reply, _ = wallet_api._topup_notify_core(
        db, _notify_raw(resp["order_no"], money="80.78"))
    assert reply == "success"
    assert wallet_service.get_balance(db, user.id) == Decimal("10.00")


def test_create_above_max_rejected(db):
    _gateway(db)
    _manual_rate(db)
    user = _user(db, "u-max")
    # 默认上限 $70：$70 放行、$70.01 拒（服务端 enforce，不只前端）
    assert _create(db, user, amount="70.00")["ok"] is True
    with pytest.raises(HTTPException) as exc:
        _create(db, user, amount="70.01")
    assert exc.value.status_code == 400
    # 上限改 0 ＝ 不限：$100 放行
    _set(db, "wallet_topup_max_amount", "0")
    assert _create(db, user, amount="100.00")["ok"] is True


# ==================== 回调履约 ====================

def _paid_setup(db, username="u-pay"):
    _gateway(db)
    _manual_rate(db, "7.2")
    user = _user(db, username)
    resp = _create(db, user, amount="9.80")
    return user, resp["order_no"]


def test_notify_success_credits_once(db):
    user, order_no = _paid_setup(db)
    reply, _pending = wallet_api._topup_notify_core(db, _notify_raw(order_no))
    assert reply == "success"
    assert wallet_service.get_balance(db, user.id) == Decimal("9.80")
    order = db.query(models.WalletTopupOrder).filter_by(
        order_no=order_no).first()
    assert order.status == "paid"
    assert order.paid_at is not None
    # 重复回调：仍回 success，但绝不二次入账
    reply2, _ = wallet_api._topup_notify_core(db, _notify_raw(order_no))
    assert reply2 == "success"
    assert wallet_service.get_balance(db, user.id) == Decimal("9.80")
    rows = (db.query(models.WalletLedger)
            .filter_by(user_id=user.id, kind="recharge").all())
    assert len(rows) == 1
    assert rows[0].idempotency_key == order_no


def test_notify_bad_sign_no_credit(db):
    user, order_no = _paid_setup(db)
    raw = _notify_raw(order_no, key="wrong-key")
    reply, _ = wallet_api._topup_notify_core(db, raw)
    assert reply == "fail"
    assert wallet_service.get_balance(db, user.id) == Decimal("0")
    order = db.query(models.WalletTopupOrder).filter_by(
        order_no=order_no).first()
    assert order.status == "pending"


def test_notify_amount_mismatch_no_credit(db):
    user, order_no = _paid_setup(db)
    raw = _notify_raw(order_no, money="71.00")
    reply, _ = wallet_api._topup_notify_core(db, raw)
    assert reply == "fail"
    assert wallet_service.get_balance(db, user.id) == Decimal("0")


def test_notify_unknown_order_fail(db):
    _gateway(db)
    reply, _ = wallet_api._topup_notify_core(db, _notify_raw("WT-NOPE"))
    assert reply == "fail"


def test_notify_uses_order_snapshot_rate(db):
    user, order_no = _paid_setup(db)
    # 下单后管理员把汇率改掉：已下单的入账额不许跟着变
    _set(db, "wallet_topup_cny_rate", "9.9")
    reply, _ = wallet_api._topup_notify_core(db, _notify_raw(order_no))
    assert reply == "success"
    assert wallet_service.get_balance(db, user.id) == Decimal("9.80")


def test_notify_records_invite_rebate(db):
    _gateway(db)
    _manual_rate(db, "7.2")
    inviter = _user(db, "u-inviter")
    invitee = _user(db, "u-invitee")
    code = models.InvitationCode(code="TOPUPCODE", user_id=inviter.id,
                                 is_active=True, max_uses=0, use_count=0,
                                 reward_points=0)
    db.add(code)
    db.commit()
    db.add(models.InvitationRecord(inviter_id=inviter.id,
                                   invitee_id=invitee.id, code_id=code.id,
                                   reward_points=0))
    db.commit()
    resp = _create(db, invitee, amount="9.80")
    reply, _ = wallet_api._topup_notify_core(
        db, _notify_raw(resp["order_no"]))
    assert reply == "success"
    accrual = (db.query(models.InviteRebateAccrual)
               .filter_by(source=invite_rebate.SOURCE_WALLET_TOPUP,
                          source_ref=resp["order_no"]).first())
    assert accrual is not None
    # paid_amount ＝ 用户选定的美元额 $9.80（美元计价，天然美元口径）
    assert accrual.paid_amount == Decimal("9.80")
    # 分档默认：$50 内按 5% → $0.49，已入账邀请人钱包
    assert accrual.rebate_amount == Decimal("0.49")
    assert accrual.status == invite_rebate.STATUS_CREDITED
    assert wallet_service.get_balance(db, inviter.id) == Decimal("0.49")


# ==================== 钱包信息接口 ====================

def test_wallet_info_includes_topup(db):
    _gateway(db)
    _seed_cache(db, "7.50", age_hours=0)
    user = _user(db, "u-info")
    resp = wallet_api.get_wallet(user, db)
    topup = resp["topup"]
    assert topup["enabled"] is True
    assert topup["min_usd_fen"] == 100
    assert topup["max_usd_fen"] == 7000
    assert topup["min_usd"] == "1"
    assert topup["max_usd_epay"] == "70"
    assert topup["fee_rate"] == 0
    # 生效汇率含默认 2% 缓冲：7.50 × 1.02
    assert Decimal(topup["usd2cny"]) == Decimal("7.65")
    assert "alipay" in topup["methods"]


def test_wallet_info_topup_disabled_without_gateway(db):
    _seed_cache(db, "7.50", age_hours=0)
    user = _user(db, "u-info2")
    resp = wallet_api.get_wallet(user, db)
    assert resp["topup"]["enabled"] is False


# ==================== 充值单状态查询（等待屏轮询） ====================

def test_get_topup_order_pending(db):
    user, order_no = _paid_setup(db, "u-get-pending")
    resp = wallet_api.get_topup_order(order_no, user, db)
    assert resp["ok"] is True
    assert resp["order_no"] == order_no
    assert resp["status"] == "pending"
    # 金额与下单口径一致：$9.80 @7.344 → 应付 ¥71.97、到账全额 $9.80
    assert resp["amount_usd"] == "9.80"
    assert resp["amount_usd_fen"] == 980
    assert resp["amount_cny"] == "71.97"
    assert resp["amount_cny_fen"] == 7197
    assert resp["credit_usd"] == "9.80"
    assert resp["credit_usd_fen"] == 980
    assert resp["payment_method"] == "alipay"
    assert resp["paid_at"] is None
    assert resp["created_at"] is not None


def test_get_topup_order_paid(db):
    user, order_no = _paid_setup(db, "u-get-paid")
    reply, _ = wallet_api._topup_notify_core(db, _notify_raw(order_no))
    assert reply == "success"
    resp = wallet_api.get_topup_order(order_no, user, db)
    assert resp["status"] == "paid"
    assert resp["paid_at"] is not None


def test_get_topup_order_other_user_404(db):
    _owner, order_no = _paid_setup(db, "u-get-owner")
    other = _user(db, "u-get-other")
    with pytest.raises(HTTPException) as exc:
        wallet_api.get_topup_order(order_no, other, db)
    assert exc.value.status_code == 404


def test_get_topup_order_missing_404(db):
    user = _user(db, "u-get-missing")
    with pytest.raises(HTTPException) as exc:
        wallet_api.get_topup_order("WT-DOES-NOT-EXIST", user, db)
    assert exc.value.status_code == 404


def test_get_topup_order_http_auth(db):
    """HTTP 层：路由真实挂上、未登录 401、本人带 token 200。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.security import create_access_token

    user, order_no = _paid_setup(db, "u-get-http")
    app = FastAPI()
    app.include_router(wallet_api.wallet_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    url = f"/api/user/wallet/topup/orders/{order_no}"
    assert client.get(url).status_code == 401
    token = create_access_token(
        user.id, {"username": user.username},
        token_version=user.token_version or 0)
    resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
