# -*- coding: utf-8 -*-
"""公益服规则后台可配置（feat/welfare-admin-config）。

审计结论（2026-10-29）：公益服／经济规则绝大多数早已走标准口径
（SystemConfig 键＋config_schema＋管理端字段），本单只补缺口。
2026-11-14 更新：公益资格整族删除后，福利类键（welfare_vitality_renew_min、
register_gift_welfare_days 等）已从配置注册表摘除，本文件改测剩余的
注册赠送查看天数键（register_gift_view_days）与抽奖键的注册/校验/回读，
并钉死福利键不得再被注册。
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config_schema, models
from backend.api import admin_economy, welfare_admin
from backend.config_service import save_config
from backend.integrations import store as _store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # 活力配置走进程内 TTL 热缓存（store.get_value，60 秒，按键名全局共享，
    # 不区分数据库）：前序测试文件（如 test_watch_vitality_tiers.py 的
    # test_disabled_gets_zero）在自己 :memory: 库里写 vitality_enabled=false
    # 并读过一次后，该值会在 60 秒内串进本文件的全新 :memory: 库，导致
    # _do_checkin_core 读到 enabled=False、签到活力增益全程 +0（CI 全量
    # 下 test_checkin_gain_* 两用例实得 5 的真凶；单跑本文件时无污染故全过）。
    # 邻近活力测试文件均在 fixture 里先失效，本文件补上同一口径：
    # 每个用例开始前把 get_vitality_config 读到的全部 store 键失效，
    # 本库缺行时走代码缺省（enabled 默认开），不再继承别库的缓存值。
    _store.invalidate(
        "welfare_vitality_renew_min", "register_gift_welfare_days",
        "register_gift_view_days", "register_gift_welfare_enabled",
        "register_gift_view_enabled", "promotion_reward_days",
        "media_seek_daily_limit")
    yield session
    session.close()


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


def _make_user(db, username="u1", **kw):
    u = models.WebUser(username=username, password_hash="x", **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ==================== 1. 注册与默认值 ====================

def test_new_keys_registered_with_current_defaults():
    assert welfare_admin.WELFARE_CONFIG_KEYS["register_gift_view_days"] == "30"
    assert config_schema.WELFARE_CONFIG_SCHEMA["register_gift_view_days"] == (
        "int", 1, config_schema._BIG)
    # 公益资格整族键已摘除（2026-11-14），两处注册表都不许再出现
    for key in ("register_gift_welfare_days", "register_gift_welfare_enabled",
                "welfare_vitality_renew_min", "welfare_code_monthly_quota",
                "welfare_grace_days", "welfare_inactive_days"):
        assert key not in welfare_admin.WELFARE_CONFIG_KEYS
        assert key not in config_schema.WELFARE_CONFIG_SCHEMA


def test_preexisting_keys_for_ui_only_fields_untouched():
    # 抽奖键：键＋校验成套保留
    assert welfare_admin.WELFARE_CONFIG_KEYS["lottery_draw_interval_sec"] == "60"
    assert config_schema.WELFARE_CONFIG_SCHEMA["lottery_draw_interval_sec"] == (
        "int", 30, 86400)
    # 经济键集与注册表仍逐键对齐（config_slim_b4e 的契约）
    assert set(config_schema.ECONOMY_CONFIG_SCHEMA) == set(admin_economy.ECONOMY_CONFIG_KEYS)


# ==================== 2. 校验 ====================

def test_new_keys_normalize_valid_values():
    assert config_schema.normalize(
        "register_gift_view_days",
        config_schema.WELFARE_CONFIG_SCHEMA["register_gift_view_days"], 15) == "15"


def test_new_keys_normalize_rejects_invalid():
    # 查看天数 0 按 timedelta 是“立即过期”不是永久，管理端直接禁掉
    view_spec = config_schema.WELFARE_CONFIG_SCHEMA["register_gift_view_days"]
    for bad in (0, -1, "abc", 1.5, True):
        with pytest.raises(config_schema.ConfigValueError):
            config_schema.normalize("register_gift_view_days", view_spec, bad)


def test_welfare_keys_save_and_read_back(db):
    save_config(db, {"lottery_draw_interval_sec": "45",
                     "register_gift_view_days": "15"}, schema="welfare")
    db.commit()
    got = welfare_admin.welfare_config_get(db=db, current_admin=_make_user(db, "admin"))
    assert got["lottery_draw_interval_sec"] == "45"
    assert got["register_gift_view_days"] == "15"
    with pytest.raises(config_schema.ConfigValueError):
        save_config(db, {"lottery_draw_interval_sec": "0"}, schema="welfare")


def test_economy_keys_save_and_read_back(db):
    save_config(db, {"promotion_reward_days": "3",
                     "media_seek_daily_limit": "45"}, schema="economy")
    db.commit()
    for key, want in (("promotion_reward_days", "3"),
                      ("media_seek_daily_limit", "45")):
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
        assert row is not None and row.value == want
    with pytest.raises(config_schema.ConfigValueError):
        save_config(db, {"promotion_reward_days": "-1"}, schema="economy")


# ==================== 5. 新注册赠送走新键 ====================

def test_register_gifts_custom_days(db):
    from backend.api.emby_portal import _grant_register_gifts

    _set_config(db, "register_gift_view_enabled", "true")
    _set_config(db, "register_gift_view_days", "15")
    user = _make_user(db, "newbie")
    before = datetime.now()
    _grant_register_gifts(db, user)
    after = datetime.now()

    db.refresh(user)
    assert not user.is_welfare  # 资格赠送已删除，只剩查看行
    row = db.query(models.EmbyViewUnlock).filter(
        models.EmbyViewUnlock.user_id == user.id).one()
    assert before + timedelta(days=15) - timedelta(seconds=5) <= row.expires_at
    assert row.expires_at <= after + timedelta(days=15) + timedelta(seconds=5)


def test_register_gifts_invalid_rows_fall_back_to_30(db):
    from backend.api.emby_portal import _grant_register_gifts

    _set_config(db, "register_gift_view_enabled", "true")
    _set_config(db, "register_gift_view_days", "0")
    user = _make_user(db, "newbie2")
    before = datetime.now()
    _grant_register_gifts(db, user)
    after = datetime.now()

    db.refresh(user)
    assert not user.is_welfare
    row = db.query(models.EmbyViewUnlock).filter(
        models.EmbyViewUnlock.user_id == user.id).one()
    assert before + timedelta(days=30) - timedelta(seconds=5) <= row.expires_at
    assert row.expires_at <= after + timedelta(days=30) + timedelta(seconds=5)
