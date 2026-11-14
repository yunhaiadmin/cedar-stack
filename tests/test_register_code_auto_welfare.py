"""注册赠送口径回归（2026-11-14 福利整族删除后定版）。

口径：
- 凭码/开放注册一律不再发公益资格（is_welfare 恒 False、无
  WelfareGrantLog）；2026-11-10「注册码一律自带公益资格」已作废；
- 只剩查看凭证赠送：register_gift_view_enabled 开时按
  register_gift_view_days 发一张 EmbyViewUnlock 行（默认 30 天），
  开关默认关；
- 资格型码（reward_type=welfare）注册核销直接 400 拒绝、不建号、
  不烧码；能量型码已随货币于 2026-11-13 下线，同口径拒绝。

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


def _assert_no_welfare(user):
    """新口径：注册一律不发公益资格。"""
    assert user.is_welfare is False
    assert user.welfare_expires_at is None


def _assert_view_about(db, user, days, before, after):
    """查看凭证行恰一张、到期约 days 天后（误差 10 秒）。"""
    rows = _view_rows(db, user.id)
    assert len(rows) == 1
    exp = rows[0].expires_at
    assert exp is not None
    assert before + timedelta(days=days) - timedelta(seconds=10) <= exp
    assert exp <= after + timedelta(days=days) + timedelta(seconds=10)


# ==================== 订阅/折扣型码：只开会员，不发资格 ====================

def test_subscription_code_register_no_welfare_but_subscription(db):
    """订阅型码注册（查看开关默认关）→ 无资格、无查看行、无发放日志；
    码自身权益照旧：开 30 天订阅。"""
    _set_mode(db, "code")
    _make_code(db)
    _register(db, "sub_user", code="REG-AUTO-0001")

    user = _user(db, "sub_user")
    assert user is not None
    _assert_no_welfare(user)
    assert _view_rows(db, user.id) == []
    assert _grant_logs(db, user.id) == []
    sub = db.query(models.UserSubscription).filter_by(
        user_id=user.id, status="active").first()
    assert sub is not None
    assert (sub.end_date - sub.start_date).days >= 29


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


def test_discount_code_register_no_welfare(db):
    """折扣型码注册 → 建号成功、无资格、无发放日志。"""
    _set_mode(db, "code")
    _make_code(db, reward_type="discount", discount_pct=10)
    _register(db, "discount_user", code="REG-AUTO-0001")

    user = _user(db, "discount_user")
    assert user is not None
    _assert_no_welfare(user)
    assert _grant_logs(db, user.id) == []


def test_code_register_uses_configured_view_days(db):
    """register_gift_view_days=10＋查看开关开 → 码注册发一张 10 天查看行，
    不发资格（旧 register_gift_welfare_days 键已随福利族删除）。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_view_enabled", "true")
    _set_config(db, "register_gift_view_days", "10")
    _make_code(db)
    before = datetime.now()
    _register(db, "days_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "days_user")
    _assert_no_welfare(user)
    _assert_view_about(db, user, 10, before, after)
    assert _grant_logs(db, user.id) == []


# ==================== 资格型码：拒绝核销 ====================

def test_welfare_code_register_refused(db):
    """资格型码注册 → 直接 400 拒绝，不建号、不烧码。"""
    _set_mode(db, "code")
    _make_code(db, reward_type="welfare", days=7)
    with pytest.raises(Exception) as exc:
        _register(db, "welfare_user", code="REG-AUTO-0001")
    assert getattr(exc.value, "status_code", None) == 400
    assert "资格卡码已下线" in str(getattr(exc.value, "detail", ""))
    assert _user(db, "welfare_user") is None
    code = db.query(models.RegistrationCode).filter_by(code="REG-AUTO-0001").first()
    assert code.use_count == 0


def test_welfare_code_refused_even_with_view_switch_on(db):
    """资格型码＋查看开关开 → 仍 400 拒绝（查看赠送不替资格码放行）。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_view_enabled", "true")
    _make_code(db, reward_type="welfare", days=7)
    with pytest.raises(Exception) as exc:
        _register(db, "welfare_on_user", code="REG-AUTO-0001")
    assert getattr(exc.value, "status_code", None) == 400
    assert "资格卡码已下线" in str(getattr(exc.value, "detail", ""))
    assert _user(db, "welfare_on_user") is None
    code = db.query(models.RegistrationCode).filter_by(code="REG-AUTO-0001").first()
    assert code.use_count == 0


# ==================== 查看开关开：只发凭证一次 ====================

def test_subscription_code_with_view_switch_on_grants_view_once(db):
    """订阅型码＋查看开关开 → 只发一张 30 天查看行（默认天数），不发资格。"""
    _set_mode(db, "code")
    _set_config(db, "register_gift_view_enabled", "true")
    _make_code(db)
    before = datetime.now()
    _register(db, "sub_on_user", code="REG-AUTO-0001")
    after = datetime.now()

    user = _user(db, "sub_on_user")
    _assert_no_welfare(user)
    _assert_view_about(db, user, 30, before, after)
    assert _grant_logs(db, user.id) == []


# ==================== 开放注册：行为不变 ====================

def test_open_register_gift_switch_off_no_welfare(db):
    """开放注册＋总开关关（默认）→ 仍无资格（现状不变）。"""
    _set_mode(db, "open")
    _register(db, "open_user")

    user = _user(db, "open_user")
    assert user.is_welfare is False
    assert _grant_logs(db, user.id) == []


def test_open_register_view_switch_on_grants_view_once(db):
    """开放注册＋查看开关开 → 发一张 30 天查看行，不发资格。"""
    _set_mode(db, "open")
    _set_config(db, "register_gift_view_enabled", "true")
    before = datetime.now()
    _register(db, "open_on_user")
    after = datetime.now()

    user = _user(db, "open_on_user")
    _assert_no_welfare(user)
    _assert_view_about(db, user, 30, before, after)
    assert _grant_logs(db, user.id) == []
