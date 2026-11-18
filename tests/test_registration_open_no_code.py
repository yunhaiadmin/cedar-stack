"""注册码下线回归（2026-11-15 用户亲令）。

口径：
- 注册码概念整体去掉：注册模式只剩 open / closed，存量值 code
  一律归一为 open（生产配置此刻就是 code，部署即开放，不许关死）；
- 注册不再有验码步：请求里就算带了注册码字段也整体忽略——不校验、
  不核销、不发码附带的订阅；register-config 不再下发验码/提示字段，
  验码端点删除；
- 注册不再送体验：register_gift_view_* 即使开着，注册也不发查看行
  （发放逻辑已删，配置键就此失效、留库不管）；
- 码体系里 code_type=1 退出一切创建/发放/购买：管理端生成 400、
  代理进货 400、已登录兑换明确拒绝（存量码行与表保留）；
- 邀请码是另一套概念，不受影响（见 test_invite_full_suite）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-open-reg-key-32chars-minimum")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
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


# 遗留表 emby_view_unlocks 的模型定义已删（2026-11-19）：原生 SQL 计数，
# 断言口径不变（注册不发查看行，查到即失败）。
_LEGACY_UNLOCK_DDL = (
    "CREATE TABLE IF NOT EXISTS emby_view_unlocks ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, realm_id INTEGER NOT NULL, "
    "unlocked_at DATETIME, expires_at DATETIME, "
    "points_spent INTEGER DEFAULT 0, UNIQUE (user_id, realm_id))")


def _legacy_unlock_count(db, user_id):
    db.execute(text(_LEGACY_UNLOCK_DDL))
    return db.execute(
        text("SELECT COUNT(*) FROM emby_view_unlocks"
             " WHERE user_id = :user_id"),
        {"user_id": user_id}).scalar()


def _scope(ip="1.2.3.4"):
    return {"type": "http", "method": "POST", "path": "/",
            "headers": [], "client": (ip, 1234)}


def _set_mode(db, mode):
    db.add(models.SystemConfig(key="register_ratelimit_enabled", value="false"))
    db.add(models.SystemConfig(key="registration_mode", value=mode))
    db.commit()


def _set_config(db, key, value):
    db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    store.invalidate(key)


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


def _register(db, username, **kw):
    req = emby_portal.RegisterRequest(
        username=username, password="<redacted>", **kw)
    return emby_portal.register(Request(_scope()), req, db)


def _user(db, username):
    return db.query(models.WebUser).filter_by(username=username).first()


# ==================== 模式归一 / 配置下发 ====================

def test_normalize_code_becomes_open():
    norm = emby_portal._normalize_registration_mode
    assert norm("code") == "open"
    assert norm(" Code ") == "open"
    assert norm("open") == "open"
    assert norm("closed") == "closed"
    assert norm("garbage") == "closed"  # 未知值口径不回归：仍 fail-closed
    assert norm(None) == "open"
    assert norm("") == "open"


def test_register_config_code_stored_reports_open_without_code_fields(db):
    _set_mode(db, "code")
    cfg = emby_portal.register_config(db)
    assert cfg["registration_mode"] == "open"
    assert "registration_code_required" not in cfg
    assert "registration_code_hint" not in cfg
    assert "invitation_enabled" in cfg


def test_verify_endpoint_removed():
    assert not hasattr(emby_portal, "verify_register_code")


# ==================== 注册主流程 ====================

def test_register_in_stored_code_mode_succeeds_without_code(db):
    """生产存量 registration_mode=code：部署即开放，无码直接注册成功。"""
    _set_mode(db, "code")
    resp = _register(db, "newbie")
    assert resp.access_token
    user = _user(db, "newbie")
    assert user is not None
    assert user.register_channel == "open"


def test_register_ignores_presented_registration_code(db):
    """请求里带了注册码字段也整体忽略：不核销、不发订阅、不落核销流水。"""
    _set_mode(db, "open")
    row = _make_code(db)
    _register(db, "newbie", registration_code="REG-TEST-0001")
    user = _user(db, "newbie")
    assert user is not None
    db.refresh(row)
    assert row.use_count == 0
    assert db.query(models.CodeRedemption).count() == 0
    assert db.query(models.UserSubscription).filter_by(
        user_id=user.id).count() == 0


def test_register_grants_no_view_unlock_even_when_gift_switch_on(db):
    """不送体验：register_gift_view_* 开着也不发查看行（发放逻辑已删）。"""
    _set_mode(db, "open")
    _set_config(db, "register_gift_view_enabled", "true")
    _set_config(db, "register_gift_view_days", "30")
    _register(db, "newbie")
    user = _user(db, "newbie")
    assert user is not None
    assert _legacy_unlock_count(db, user.id) == 0
    assert db.query(models.UserSubscription).filter_by(
        user_id=user.id).count() == 0


def test_closed_mode_still_rejects(db):
    _set_mode(db, "closed")
    with pytest.raises(HTTPException) as exc:
        _register(db, "newbie")
    assert exc.value.status_code == 403
    assert _user(db, "newbie") is None


# ==================== 兑换侧：type 1 拒绝、type 2 照常 ====================

def _member(db, username="member"):
    owner = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return owner


def test_redeem_register_code_rejected_not_consumed(db):
    owner = _member(db)
    row = _make_code(db)
    result = codes.redeem_code(db, owner, "REG-TEST-0001")
    assert result["success"] is False
    assert result["error_code"] == codes.CODE_ERROR_REGISTER_ONLY
    assert result["message"] == codes.REGISTER_CODE_ONLY_MESSAGE
    assert "停止使用" in result["message"]
    db.rollback()
    db.refresh(row)
    assert row.use_count == 0  # 拒绝不许消耗
    assert db.query(models.UserSubscription).filter_by(
        user_id=owner.id).count() == 0


def test_preview_register_code_invalid(db):
    _make_code(db)
    preview = codes.preview_code(db, "REG-TEST-0001", username="member")
    assert preview["valid"] is False
    assert "停止使用" in preview["message"]


def test_redeem_renew_code_still_works(db):
    owner = _member(db)
    _make_code(db, code_str="REN-TEST-0001", code_type=2)
    result = codes.redeem_code(db, owner, "REN-TEST-0001")
    assert result["success"] is True
    db.commit()


# ==================== 管理端 ====================

@pytest.fixture()
def admin_user(db):
    user = models.WebUser(username="boss", password_hash="x",
                          is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_admin_settings_code_mode_normalized_to_open(db, admin_user):
    resp = admin_api.set_registration_mode(
        admin_api.RegistrationModeRequest(mode="code"), admin_user, db)
    assert resp["success"] is True
    assert resp["mode"] == "open"
    got = admin_api.get_registration_mode(admin_user, db)
    assert got["mode"] == "open"
    assert "registration_code_hint_text" not in got
    assert "registration_code_hint_url" not in got


def test_admin_settings_reject_unknown_mode(db, admin_user):
    with pytest.raises(HTTPException) as exc:
        admin_api.set_registration_mode(
            admin_api.RegistrationModeRequest(mode="bogus"), admin_user, db)
    assert exc.value.status_code == 400


def test_admin_generate_register_code_rejected(db, admin_user):
    with pytest.raises(HTTPException) as exc:
        admin_ops.generate_codes(
            admin_ops.CodeGenerateRequest(code_type=1, count=1),
            admin_user, db)
    assert exc.value.status_code == 400
    assert db.query(models.RegistrationCode).count() == 0


def test_admin_generate_renew_code_still_works(db, admin_user):
    resp = admin_ops.generate_codes(
        admin_ops.CodeGenerateRequest(code_type=2, count=2), admin_user, db)
    assert resp["success"] is True
    assert len(resp["codes"]) == 2


def test_agent_purchase_register_code_rejected(db, admin_user):
    from backend.api import agent_center, agents_admin

    agent_user = models.WebUser(username="agent_a", password_hash="x",
                                is_active=True)
    db.add(agent_user)
    db.commit()
    plan = models.SubscriptionPlan(
        name="月卡", price=100,
        duration_days=30, realm_id=1, is_active=True)
    db.add(plan)
    db.commit()
    level = agents_admin.create_level(
        agents_admin.LevelCreateRequest(
            name="金牌代理", discount_rate=0.8, sort_order=1),
        admin_user, db)["level"]
    agent = agents_admin.assign_agent(
        agents_admin.AgentAssignRequest(
            user_id=agent_user.id, level_id=level["id"]), admin_user, db)["agent"]
    # 先充够余额：保证被拒只能是因为 code_type，而不是余额不足
    agents_admin.adjust_balance(
        agent["id"], agents_admin.BalanceAdjustRequest(
            amount="500", note="进货款"), admin_user, db)
    with pytest.raises(HTTPException) as exc:
        agent_center.purchase(
            agent_center.PurchaseRequest(plan_id=plan.id, code_type=1,
                                         quantity=1),
            agent_user, db)
    assert exc.value.status_code == 400
    assert db.query(models.RegistrationCode).count() == 0


# ==================== 管理端列表/流水（自 test_reg_code_two_step.py 抢救改写） ====================

def test_admin_code_list_separation_filters(db, admin_user):
    """码列表的分区过滤口径不变：code_type=1 查存量注册码、
    exclude_code_type=1 是会员卡码页的排除口径。"""
    def _row(code_str, code_type=1, source="admin", created_by=None):
        row = models.RegistrationCode(
            code=code_str, code_type=code_type, days=30,
            max_uses=1, use_count=0, is_active=True, source=source,
            created_by=created_by)
        db.add(row)
        db.commit()
        return row

    _row("REG-A-0001", created_by=admin_user.id)
    _row("REG-B-0001", source="agent")
    _row("REN-A-0001", code_type=2)

    only_reg = admin_ops.list_codes(admin_user, db, code_type=1, realm_id=0)
    assert {c["code"] for c in only_reg["codes"]} == {"REG-A-0001", "REG-B-0001"}

    no_reg = admin_ops.list_codes(admin_user, db, exclude_code_type=1, realm_id=0)
    assert {c["code"] for c in no_reg["codes"]} == {"REN-A-0001"}

    agent_only = admin_ops.list_codes(admin_user, db, source="agent", realm_id=0)
    assert {c["code"] for c in agent_only["codes"]} == {"REG-B-0001"}

    by_code = {c["code"]: c for c in only_reg["codes"]}
    assert by_code["REG-A-0001"]["created_by_name"] == "boss"
    assert by_code["REG-B-0001"]["source"] == "agent"


def test_admin_code_redemptions_flow(db, admin_user):
    """单码核销流水端点照常：续期码被会员兑换后流水里能查到。"""
    owner = _member(db)
    row = _make_code(db, code_str="REN-FLOW-001", code_type=2)
    result = codes.redeem_code(db, owner, "REN-FLOW-001")
    assert result["success"] is True
    db.commit()
    flow = admin_ops.list_code_redemptions(
        code_id=row.id, current_admin=admin_user, db=db)
    assert flow["total"] == 1
    item = flow["items"][0]
    assert item["username"] == "member"
    assert item["redeemed_at"]
