"""注册码一律自带公益资格（2026-11-10 用户拍板）回归。

口径：
- 凭码注册（code_type=1）成功的新用户一律持有公益资格，不看码面
  reward_type；只带资格，不另发查看行，码自身权益照旧；
- 福利型码本次核销已按码面发过资格的，不另发、不叠加；
- 非福利型码走赠送天数口径（register_gift_welfare_days，默认 30），
  但不受 register_gift_welfare_enabled 总开关限制，发放日志
  channel=register_code（与 register_gift / code_redeem 区分来源）；
- 能量型码已随货币于 2026-11-13 下线：注册核销直接 400 拒绝、不建号、不烧码。
- 开放注册路径不变：仍由总开关把关、channel=register_gift。

测试风格与 test_reg_code_two_step 一致：直调端点函数。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-reg-code-welfare-key-32chars")

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import models
from backend.api import emby_portal
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


def _set_config(db, key, value):
    db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    store.invalidate(key)


def _make_code(db, code_str="REG-AUTO-0001", **kw):
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


def _register(db, username, code=None, ip="1.2.3.4"):
    req = emby_portal.RegisterRequest(
        username=username, password="<redacted>", registration_code=code)
    return emby_portal.register(Request(_scope(ip)), req, db)


def _user(db, username):
    return db.query(models.WebUser).filter_by(username=username).first()


def _grant_logs(db, user_id):
    return (db.query(models.WelfareGrantLog)
            .filter_by(user_id=user_id)
            .order_by(models.WelfareGrantLog.id)
            .all())


def _view_rows(db, user_id):
    return db.query(models.EmbyViewUnlock).filter_by(user_id=user_id).all()


def _assert_expires_about(user, days, before, after):
    assert user.is_welfare is True
    assert user.welfare_expires_at is not None
    assert before + timedelta(days=days) - timedelta(seconds=10) <= user.welfare_expires_at
    assert user.welfare_expires_at <= after + timedelta(days=days) + timedelta(seconds=10)


# ==================== 非福利型码：无条件自带资格 ====================

def test_subscription_code_register_grants_welfare_no_view_row(db):
    """订阅型码注册（总开关默认关）→ 有资格 30 天、无新增查看行。"""
    _set_mode(db, "code")
    _make_code(db)
    before = datetime.now()
    _register(db, "sub_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "sub_user")
    _assert_expires_about(user, 30, before, after)
    assert user.welfare_grant_channel == "register_code"
    assert _view_rows(db, user.id) == []
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].channel == "register_code"
    assert logs[0].days == 30


def test_points_code_register_rejected_after_currency_removal(db):
    """能量型码注册已下线（2026-11-13 货币删除）：核销直接 400 拒绝，
    不建号、不烧码。"""
    _set_mode(db, "code")
    _make_code(db, reward_type="points", points_value=50)
    with pytest.raises(Exception) as exc:
        _register(db, "points_user", code="REG-AUTO-0001")
    assert getattr(exc.value, "status_code", None) == 400
    assert "能量卡码已下线" in str(getattr(exc.value, "detail", ""))
    assert _user(db, "points_user") is None
    code = db.query(models.RegistrationCode).filter_by(code="REG-AUTO-0001").first()
    assert code.use_count == 0


def test_discount_code_register_grants_welfare(db):
    """折扣型码注册 → 有资格。"""
    _set_mode(db, "code")
    _make_code(db, reward_type="discount", discount_pct=10)
    before = datetime.now()
    _register(db, "discount_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "discount_user")
    _assert_expires_about(user, 30, before, after)
    assert user.welfare_grant_channel == "register_code"


def test_code_register_uses_configured_gift_days(db):
    """register_gift_welfare_days=10 → 码注册发 10 天（走同一天数口径）。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_welfare_days", "10")
    _make_code(db)
    before = datetime.now()
    _register(db, "days_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "days_user")
    _assert_expires_about(user, 10, before, after)
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].days == 10


# ==================== 福利型码：按码面、不叠加 ====================

def test_welfare_code_register_uses_code_days_only(db):
    """福利型码 7 天注册 → 到期只按码面 7 天，不叠加赠送 30 天。"""
    _set_mode(db, "code")
    _make_code(db, reward_type="welfare", days=7)
    before = datetime.now()
    _register(db, "welfare_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "welfare_user")
    _assert_expires_about(user, 7, before, after)
    assert user.welfare_grant_channel == "code_redeem"
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].channel == "code_redeem"
    assert logs[0].days == 7


def test_welfare_code_with_gift_switch_on_still_code_days_only(db):
    """福利型码＋总开关开 → 仍只发码面天数一次，不走赠送路径叠加。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_welfare_enabled", "true")
    _make_code(db, reward_type="welfare", days=7)
    before = datetime.now()
    _register(db, "welfare_on_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "welfare_on_user")
    _assert_expires_about(user, 7, before, after)
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].channel == "code_redeem"


# ==================== 防叠加：非福利型码＋总开关开 ====================

def test_subscription_code_with_gift_switch_on_grants_once(db):
    """订阅型码＋总开关开 → 只发一次 30 天（不是 60 天）。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_welfare_enabled", "true")
    _make_code(db)
    before = datetime.now()
    _register(db, "sub_on_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "sub_on_user")
    _assert_expires_about(user, 30, before, after)
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].channel == "register_code"


# ==================== 开放注册：行为不变 ====================

def test_open_register_gift_switch_off_no_welfare(db):
    """开放注册＋总开关关（默认）→ 仍无资格（现状不变）。"""
    _set_mode(db, "open")
    _register(db, "open_user")

    user = _user(db, "open_user")
    assert user.is_welfare is False
    assert _grant_logs(db, user.id) == []


def test_open_register_gift_switch_on_grants_once_as_before(db):
    """开放注册＋总开关开 → 赠送照旧一次，channel=register_gift。"""
    _set_mode(db, "open")
    _set_config(db, "register_gift_welfare_enabled", "true")
    before = datetime.now()
    _register(db, "open_on_user")
    after = datetime.now()

    user = _user(db, "open_on_user")
    _assert_expires_about(user, 30, before, after)
    assert user.welfare_grant_channel == "register_gift"
    logs = _grant_logs(db, user.id)
    assert len(logs) == 1
    assert logs[0].channel == "register_gift"
