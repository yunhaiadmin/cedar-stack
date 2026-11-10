"""凭码注册两步版回归（2026-11-10 用户亲定版）。

口径：
- 注册模式三档 open / code / closed，code 恢复为合法档位；
- code 档两步：第一步公开验码只校验不消耗（带 IP 限流防撞码），
  第二步提交注册时服务端二次校验，核销与建号同一事务（CAS 占位）；
- 注册码只认 code_type=1，附带权益（订阅天数 / 资格等）注册成功时
  按现行核销语义发放，代理码注册后销售归属落出码代理；
- 已登录用户拿 code_type=1 走兑换入口被明确拒绝并提示去注册页。

测试风格与 test_register_closed_mode_no_enumeration 一致：直调端点函数。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-reg-code-key-32chars-minimum")

import threading
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import codes, models
from backend.api import admin as admin_api
from backend.api import admin_ops, emby_portal
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
    store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        store.invalidate()


def _scope(ip="1.2.3.4"):
    return {"type": "http", "method": "POST", "path": "/",
            "headers": [], "client": (ip, 1234)}


def _set_mode(db, mode):
    db.add(models.SystemConfig(key="register_ratelimit_enabled", value="false"))
    db.add(models.SystemConfig(key="registration_mode", value=mode))
    db.commit()


def _make_code(db, code_str="REG-TEST-0001", **kw):
    defaults = dict(
        code=code_str, code_type=1, days=30, max_uses=1, use_count=0,
        is_active=True, source="admin", realm_id=1, reward_type="subscription",
    )
    defaults.update(kw)
    row = models.RegistrationCode(**defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _register(db, username, code=None, ip="1.2.3.4", **kw):
    req = emby_portal.RegisterRequest(
        username=username, password="pw-123456",
        registration_code=code, **kw)
    return emby_portal.register(Request(_scope(ip)), req, db)


def _verify(db, code, ip="1.2.3.4"):
    req = emby_portal.RegisterCodeVerifyRequest(code=code)
    return emby_portal.verify_register_code(Request(_scope(ip)), req, db)


def _user(db, username):
    return db.query(models.WebUser).filter_by(username=username).first()


# ==================== 模式归一 / 配置下发 ====================

def test_normalize_code_mode_is_legal():
    norm = emby_portal._normalize_registration_mode
    assert norm("code") == "code"
    assert norm(" Code ") == "code"
    assert norm("open") == "open"
    assert norm("closed") == "closed"
    assert norm("garbage") == "closed"
    assert norm(None) == "open"


def test_register_config_reports_code_requirement_and_hint(db):
    _set_mode(db, "code")
    cfg = emby_portal.register_config(db)
    assert cfg["registration_mode"] == "code"
    assert cfg["registration_code_required"] is True
    # 提示位走配置键：未配置时给默认文案、链接为空（不写死具体链接）
    assert cfg["registration_code_hint"]["text"]
    assert cfg["registration_code_hint"]["url"] == ""

    db.add(models.SystemConfig(
        key="registration_code_hint_text", value="去官方群领取"))
    db.add(models.SystemConfig(
        key="registration_code_hint_url", value="https://example.com/group"))
    db.commit()
    cfg = emby_portal.register_config(db)
    assert cfg["registration_code_hint"]["text"] == "去官方群领取"
    assert cfg["registration_code_hint"]["url"] == "https://example.com/group"


def test_register_config_open_mode_needs_no_code(db):
    _set_mode(db, "open")
    cfg = emby_portal.register_config(db)
    assert cfg["registration_code_required"] is False


# ==================== code 档：无码 / 坏码注册被拒 ====================

def test_code_mode_register_without_code_rejected(db):
    _set_mode(db, "code")
    _make_code(db)
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie")
    assert exc.value.status_code == 400
    assert _user(db, "newbie") is None
    # 码没被消耗
    assert db.query(models.RegistrationCode).first().use_count == 0


def test_code_mode_register_with_unknown_code_rejected(db):
    _set_mode(db, "code")
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-NOPE-XXXX")
    assert exc.value.status_code == 400
    assert _user(db, "newbie") is None


def test_code_mode_register_with_renew_code_rejected(db):
    """续期码（code_type=2）不是注册码，注册页用它必须被拒。"""
    _set_mode(db, "code")
    _make_code(db, code_type=2)
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert _user(db, "newbie") is None


def test_code_mode_expired_code_rejected(db):
    _set_mode(db, "code")
    _make_code(db, expires_at=datetime.now() - timedelta(days=1))
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert "过期" in str(exc.value.detail)
    assert _user(db, "newbie") is None


def test_code_mode_disabled_code_rejected(db):
    _set_mode(db, "code")
    _make_code(db, is_active=False)
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert _user(db, "newbie") is None


def test_code_mode_used_up_code_rejected(db):
    _set_mode(db, "code")
    _make_code(db, max_uses=1, use_count=1)
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert exc.value.detail == codes.CODE_USED_UP_MESSAGE
    assert _user(db, "newbie") is None


# ==================== 第一步验码：只校验不消耗 ====================

def test_verify_endpoint_validates_without_consuming(db):
    _set_mode(db, "code")
    row = _make_code(db)
    for _ in range(2):
        res = _verify(db, "REG-TEST-0001", ip="10.0.0.1")
        assert res["valid"] is True
    db.refresh(row)
    assert row.use_count == 0
    assert db.query(models.CodeRedemption).count() == 0
    # 验完码仍可正常完成注册（验码不烧码）
    _register(db, "newbie", code="REG-TEST-0001")
    assert _user(db, "newbie") is not None


def test_verify_endpoint_rejects_bad_codes(db):
    _set_mode(db, "code")
    _make_code(db, code_str="REG-USED-0001", max_uses=1, use_count=1)
    assert _verify(db, "REG-NOPE", ip="10.0.0.2")["valid"] is False
    assert _verify(db, "REG-USED-0001", ip="10.0.0.2")["valid"] is False
    assert _verify(db, "", ip="10.0.0.2")["valid"] is False


def test_verify_endpoint_rate_limited(db):
    _set_mode(db, "code")
    ip = "10.9.9.9"
    limited = False
    for _ in range(40):
        try:
            _verify(db, "REG-WHATEVER", ip=ip)
        except HTTPException as exc:
            assert exc.status_code == 429
            limited = True
            break
    assert limited, "验码端点必须有 IP 限流防撞码"


# ==================== code 档：成功路径与权益 ====================

def test_code_mode_register_success_grants_subscription(db):
    _set_mode(db, "code")
    row = _make_code(db, days=30)
    resp = _register(db, "newbie", code="REG-TEST-0001")
    assert resp.access_token
    user = _user(db, "newbie")
    assert user is not None
    assert user.register_channel == "code"
    db.refresh(row)
    assert row.use_count == 1
    assert db.query(models.CodeRedemption).filter_by(
        code_kind="reg", code_id=row.id, user_id=user.id).count() == 1
    sub = db.query(models.UserSubscription).filter_by(
        user_id=user.id, status="active").first()
    assert sub is not None
    assert (sub.end_date - sub.start_date).days >= 29


def test_code_mode_second_user_cannot_reuse_single_use_code(db):
    _set_mode(db, "code")
    _make_code(db)
    _register(db, "first", code="REG-TEST-0001")
    with pytest.raises(HTTPException) as exc:
        _register(db, "second", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert _user(db, "second") is None


def test_code_mode_named_code_binds_username(db):
    _set_mode(db, "code")
    _make_code(db, target_username="future_user")
    with pytest.raises(HTTPException) as exc:
        _register(db, "someone_else", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert _user(db, "someone_else") is None
    # 码未被烧掉，指名本人仍可注册
    _register(db, "future_user", code="REG-TEST-0001")
    assert _user(db, "future_user") is not None


def test_code_mode_welfare_code_grants_welfare_and_view(db):
    _set_mode(db, "code")
    _make_code(db, reward_type="welfare", days=30, welfare_include_view=True)
    _register(db, "newbie", code="REG-TEST-0001")
    user = _user(db, "newbie")
    assert user is not None
    assert user.is_welfare
    assert user.welfare_expires_at is not None
    assert db.query(models.EmbyViewUnlock).filter_by(user_id=user.id).count() == 1


def test_code_consumed_between_verify_and_submit_blocks_second(db):
    """二次校验的牙齿：验码后码被他人用掉，提交时必须被拒。"""
    _set_mode(db, "code")
    _make_code(db)
    assert _verify(db, "REG-TEST-0001", ip="10.0.0.3")["valid"] is True
    _register(db, "early_bird", code="REG-TEST-0001")
    with pytest.raises(HTTPException) as exc:
        _register(db, "late_comer", code="REG-TEST-0001")
    assert exc.value.status_code == 400
    assert _user(db, "late_comer") is None


def test_single_use_code_concurrent_double_register_only_one_wins(tmp_path):
    """1 次码并发双注册只成一个（文件库 + 双线程 barrier 对齐）。"""
    db_file = tmp_path / "race.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    models.Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    setup = maker()
    setup.add(models.ServerRealm(id=1, name="默认服", slug="main", is_active=True))
    setup.add(models.SystemConfig(key="register_ratelimit_enabled", value="false"))
    setup.add(models.SystemConfig(key="registration_mode", value="code"))
    setup.add(models.RegistrationCode(
        code="REG-RACE-0001", code_type=1, days=30, max_uses=1, use_count=0,
        is_active=True, source="admin", realm_id=1, reward_type="subscription"))
    setup.commit()
    setup.close()
    store.invalidate()

    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        session = maker()
        try:
            barrier.wait(timeout=15)
            _register(session, name, code="REG-RACE-0001", ip="10.1.1.1")
            results[name] = "ok"
        except HTTPException as exc:
            results[name] = exc.status_code
        except Exception as exc:  # noqa: BLE001 — 失败形态要如实落进断言
            results[name] = repr(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(name,))
               for name in ("racer_a", "racer_b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sorted(str(v) for v in results.values()) == ["400", "ok"], results
    check = maker()
    try:
        users = check.query(models.WebUser).filter(
            models.WebUser.username.in_(["racer_a", "racer_b"])).count()
        assert users == 1
        row = check.query(models.RegistrationCode).filter_by(
            code="REG-RACE-0001").first()
        assert row.use_count == 1
        assert check.query(models.CodeRedemption).count() == 1
    finally:
        check.close()
        engine.dispose()
        store.invalidate()


# ==================== open / closed 档行为不变 ====================

def test_open_mode_register_without_code_still_works(db):
    _set_mode(db, "open")
    _register(db, "newbie")
    user = _user(db, "newbie")
    assert user is not None
    assert user.register_channel == "open"


def test_open_mode_ignores_registration_code_field(db):
    _set_mode(db, "open")
    row = _make_code(db)
    _register(db, "newbie", code="REG-TEST-0001")
    assert _user(db, "newbie") is not None
    db.refresh(row)
    assert row.use_count == 0  # open 档不核销注册码


def test_closed_mode_still_rejects_even_with_valid_code(db):
    _set_mode(db, "closed")
    _make_code(db)
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie", code="REG-TEST-0001")
    assert exc.value.status_code == 403
    assert _user(db, "newbie") is None


# ==================== 兑换入口拒绝注册码 ====================

def test_logged_in_redeem_of_register_code_rejected_with_hint(db):
    owner = models.WebUser(username="member", password_hash="x", is_active=True)
    db.add(owner)
    db.commit()
    row = _make_code(db)
    result = codes.redeem_code(db, owner, "REG-TEST-0001")
    assert result["success"] is False
    assert result["message"] == "注册码请在注册页使用"
    db.rollback()
    db.refresh(row)
    assert row.use_count == 0  # 拒绝不许消耗
    # 该用户也没有因此拿到会员
    assert db.query(models.UserSubscription).filter_by(
        user_id=owner.id).count() == 0


def test_logged_in_redeem_of_renew_code_still_works(db):
    owner = models.WebUser(username="member", password_hash="x", is_active=True)
    db.add(owner)
    db.commit()
    _make_code(db, code_str="REN-TEST-0001", code_type=2)
    result = codes.redeem_code(db, owner, "REN-TEST-0001")
    assert result["success"] is True
    db.commit()


def test_preview_of_register_code_points_to_register_page(db):
    _make_code(db)
    preview = codes.preview_code(db, "REG-TEST-0001", username="member")
    assert preview["valid"] is False
    assert "注册页" in preview["message"]


# ==================== 代理码注册归属 ====================

def test_agent_code_registration_records_sale(db):
    from backend import agents as agent_service  # noqa: F401  （与代理测试同口径导入）
    from backend.api import agent_center, agents_admin

    admin = models.WebUser(username="boss", password_hash="x",
                           is_active=True, is_staff=True)
    agent_user = models.WebUser(username="agent_a", password_hash="x",
                                is_active=True)
    db.add_all([admin, agent_user])
    db.commit()
    plan = models.SubscriptionPlan(
        name="月卡", price=100, points_price=None,
        duration_days=30, realm_id=1, is_active=True)
    db.add(plan)
    db.commit()

    level = agents_admin.create_level(
        agents_admin.LevelCreateRequest(
            name="金牌代理", discount_rate=0.8, sort_order=1), admin, db)["level"]
    agent = agents_admin.assign_agent(
        agents_admin.AgentAssignRequest(
            user_id=agent_user.id, level_id=level["id"]), admin, db)["agent"]
    agents_admin.adjust_balance(
        agent["id"], agents_admin.BalanceAdjustRequest(
            amount="500", note="进货款"), admin, db)
    resp = agent_center.purchase(
        agent_center.PurchaseRequest(plan_id=plan.id, code_type=1, quantity=1),
        agent_user, db)
    raw = resp["codes"][0]["code"]

    _set_mode(db, "code")
    _register(db, "buyer_new", code=raw)
    buyer = _user(db, "buyer_new")
    assert buyer is not None
    sales = db.query(models.AgentSale).all()
    assert len(sales) == 1
    assert sales[0].agent_id == agent["id"]
    assert sales[0].buyer_user_id == buyer.id
    # 附带权益（套餐 30 天）也到账
    assert db.query(models.UserSubscription).filter_by(
        user_id=buyer.id, status="active").count() == 1


# ==================== 管理端：模式三档 + 码列表分离 ====================

@pytest.fixture()
def admin_user(db):
    user = models.WebUser(username="boss", password_hash="x",
                          is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_admin_settings_accept_code_mode_and_hint(db, admin_user):
    resp = admin_api.set_registration_mode(
        admin_api.RegistrationModeRequest(
            mode="code",
            registration_code_hint_text="去官方群领取",
            registration_code_hint_url="https://example.com/group"),
        admin_user, db)
    assert resp["success"] is True
    assert resp["mode"] == "code"
    got = admin_api.get_registration_mode(admin_user, db)
    assert got["mode"] == "code"
    assert got["registration_code_hint_text"] == "去官方群领取"
    assert got["registration_code_hint_url"] == "https://example.com/group"


def test_admin_settings_reject_unknown_mode(db, admin_user):
    with pytest.raises(HTTPException) as exc:
        admin_api.set_registration_mode(
            admin_api.RegistrationModeRequest(mode="bogus"), admin_user, db)
    assert exc.value.status_code == 400


def test_admin_code_list_separation_filters(db, admin_user):
    _make_code(db, code_str="REG-A-0001", created_by=admin_user.id)
    _make_code(db, code_str="REG-B-0001", source="agent", agent_id=None)
    _make_code(db, code_str="REN-A-0001", code_type=2)

    only_reg = admin_ops.list_codes(admin_user, db, code_type=1, realm_id=0)
    assert {c["code"] for c in only_reg["codes"]} == {"REG-A-0001", "REG-B-0001"}

    no_reg = admin_ops.list_codes(admin_user, db, exclude_code_type=1, realm_id=0)
    assert {c["code"] for c in no_reg["codes"]} == {"REN-A-0001"}

    agent_only = admin_ops.list_codes(admin_user, db, source="agent", realm_id=0)
    assert {c["code"] for c in agent_only["codes"]} == {"REG-B-0001"}

    # 列表带出品人信息
    by_code = {c["code"]: c for c in only_reg["codes"]}
    assert by_code["REG-A-0001"]["created_by_name"] == "boss"
    assert by_code["REG-B-0001"]["source"] == "agent"


def test_admin_code_redemptions_flow(db, admin_user):
    _set_mode(db, "code")
    row = _make_code(db, created_by=admin_user.id)
    _register(db, "newbie", code="REG-TEST-0001")
    flow = admin_ops.list_code_redemptions(
        code_id=row.id, current_admin=admin_user, db=db)
    assert flow["total"] == 1
    item = flow["items"][0]
    assert item["username"] == "newbie"
    assert item["redeemed_at"]
