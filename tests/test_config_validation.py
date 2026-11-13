"""公益配置 / 经济系统设置：数值与开关键类型校验（非法 400，空字符串=未设置）。

注：schema 层校验失败在直接调用层面抛 ConfigValueError；HTTP 400 的转换由
main.py 的统一 exception handler 承接（端到端见 test_config_value_error_400.py）。
"""
import sys

sys.path.insert(0, ".")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config_schema, models
from backend.api import admin_economy, welfare_admin


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    admin = models.WebUser(username="admin", password_hash="x", is_staff=True)
    s.add(admin)
    s.commit()
    yield s, admin
    s.close()


def _cfg(s, key):
    row = s.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    return row.value if row else None


@pytest.mark.parametrize("key,value", [
    # points_transfer_* 已随货币于 2026-11-13 下线：未知键静默忽略、无校验可测；
    # 以下改用现存同类校验键（范围/类型/枚举口径与原用例一一对应）。
    ("welfare_vitality_renew_min", -1),
    ("welfare_vitality_renew_min", 1001),
    ("welfare_streak_renew_days", "abc"),
    ("welfare_streak_renew_days", 0),
    ("welfare_vitality_renew_min", 1.5),
    ("recharge_ratio", "-1"),
    ("recharge_quick_amounts", "10,abc"),
    ("lottery_draw_interval_sec", "5"),
])
def test_welfare_config_rejects_invalid(db, key, value):
    s, admin = db
    with pytest.raises(config_schema.ConfigValueError) as ei:
        welfare_admin.welfare_config_set({key: value}, current_admin=admin, db=s)
    assert key in str(ei.value)
    assert _cfg(s, key) is None


def test_welfare_config_invalid_writes_nothing(db):
    s, admin = db
    with pytest.raises(config_schema.ConfigValueError):
        welfare_admin.welfare_config_set(
            {"welfare_vitality_renew_min": "100", "welfare_streak_renew_days": "-1"}, current_admin=admin, db=s)
    assert _cfg(s, "welfare_vitality_renew_min") is None


def test_welfare_config_accepts_valid_and_empty_unsets(db):
    s, admin = db
    welfare_admin.welfare_config_set(
        {"welfare_vitality_renew_min": 3, "welfare_require_tg_bind": True,
         "lottery_require_group_member": False,
         "recharge_ratio": "1.5"},
        current_admin=admin, db=s)
    assert _cfg(s, "welfare_vitality_renew_min") == "3"
    # 2026-10-21：原 chat_points_enabled / lottery_enabled 已摘面板，
    # 换保留的 digit 风格 bool 键（存 "1"/"0"）
    assert _cfg(s, "welfare_require_tg_bind") == "1"
    assert _cfg(s, "lottery_require_group_member") == "0"
    assert _cfg(s, "recharge_ratio") == "1.5"
    welfare_admin.welfare_config_set({"welfare_vitality_renew_min": ""}, current_admin=admin, db=s)
    assert _cfg(s, "welfare_vitality_renew_min") is None
    assert welfare_admin.welfare_config_get(current_admin=admin, db=s)["welfare_vitality_renew_min"] == "3"


def _save_econ(s, admin, settings):
    return admin_economy.economy_update_settings(
        admin_economy.EconomySettingsRequest(settings=settings), current_admin=admin, db=s)


@pytest.mark.parametrize("key,value", [
    # vitality_max / invitation_rebate_percent / recharge_enabled 已随货币下线；
    # 改用现存同类校验键（下限/上限/bool 非法值口径与原用例一一对应）。
    ("series_merge_interval_sec", 3599),
    ("media_seek_fulfill_interval_minutes", 0),
    ("device_limit_per_user", "x"),
    ("subscription_purchase_enabled", "perhaps"),
])
def test_economy_settings_rejects_invalid(db, key, value):
    s, admin = db
    # 端点层复合校验（如 vitality 范围联动）仍直接抛 HTTPException(400)；
    # schema 层校验失败抛 ConfigValueError（HTTP 400 由 main.py 统一 handler 转）。
    with pytest.raises((HTTPException, config_schema.ConfigValueError)) as ei:
        _save_econ(s, admin, {key: value})
    exc = ei.value
    if isinstance(exc, HTTPException):
        # 端点层复合校验的人话文案不一定带键名，只断状态码
        assert exc.status_code == 400
    else:
        # schema 层错误信息恒带键名
        assert key in str(exc)
    assert _cfg(s, key) is None


def test_economy_settings_empty_unsets(db):
    s, admin = db
    _save_econ(s, admin, {"series_merge_interval_sec": "7200"})
    assert _cfg(s, "series_merge_interval_sec") == "7200"
    _save_econ(s, admin, {"series_merge_interval_sec": ""})
    assert _cfg(s, "series_merge_interval_sec") is None
