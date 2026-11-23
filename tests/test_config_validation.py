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
    # 福利族键（welfare_vitality_renew_min / welfare_streak_renew_days）
    # 已随福利整族于 2026-11-14 删除、注册赠送键（register_gift_view_days）
    # 已随注册不送体验于 2026-11-15 删除、抽奖键（lottery_draw_interval_sec）
    # 已随群抽奖整族于 2026-11-23 删除：改用福利配置端点现存同类
    # 校验键（范围/类型口径与原用例一一对应）。
    ("bot_update_digest_interval_minutes", 0),
    ("bot_update_digest_interval_minutes", 90000),
    ("bot_update_digest_interval_minutes", "abc"),
    ("recharge_ratio", "0"),
    ("bot_update_digest_interval_minutes", 1.5),
    ("recharge_ratio", "-1"),
    ("recharge_quick_amounts", "10,abc"),
    ("bot_update_digest_interval_minutes", "2000"),
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
            {"bot_rate_limit_seconds": "10", "bot_update_digest_interval_minutes": "0"}, current_admin=admin, db=s)
    assert _cfg(s, "bot_rate_limit_seconds") is None


def test_welfare_config_accepts_valid_and_empty_unsets(db):
    s, admin = db
    welfare_admin.welfare_config_set(
        {"bot_rate_limit_seconds": 10, "bot_rankpush_enabled": True,
         "bot_playrank_enabled": False,
         "recharge_ratio": "1.5"},
        current_admin=admin, db=s)
    assert _cfg(s, "bot_rate_limit_seconds") == "10"
    # bool 键走 word 风格（存 "true"/"false"）：welfare 家族原 digit
    # 风格 bool 键已随公益门禁/抽奖整族于 2026-11-23 删除
    assert _cfg(s, "bot_rankpush_enabled") == "true"
    assert _cfg(s, "bot_playrank_enabled") == "false"
    assert _cfg(s, "recharge_ratio") == "1.5"
    welfare_admin.welfare_config_set({"bot_rate_limit_seconds": ""}, current_admin=admin, db=s)
    assert _cfg(s, "bot_rate_limit_seconds") is None
    assert welfare_admin.welfare_config_get(current_admin=admin, db=s)["bot_rate_limit_seconds"] == "3"


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
