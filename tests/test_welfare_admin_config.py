# -*- coding: utf-8 -*-
"""公益服规则后台可配置（feat/welfare-admin-config）。

审计结论（2026-10-29）：公益服／经济规则绝大多数早已走标准口径
（SystemConfig 键＋config_schema＋管理端字段），本单只补缺口。
2026-11-14 更新：公益资格整族删除后，福利类键（welfare_vitality_renew_min、
register_gift_welfare_days 等）已从配置注册表摘除。
2026-11-15 更新：注册赠送查看两键（register_gift_view_days /
register_gift_view_enabled）随「注册不送体验」一并摘除，注册赠送
行为测试已删除（新口径由 test_registration_open_no_code.py 覆盖）；
本文件改测剩余抽奖键的注册/校验/回读，并钉死已删键不得再被注册。
2026-11-23 更新：抽奖键与 welfare_require_tg_bind 随群抽奖/公益
门禁整族删除，本文件改测机器人 int 键（bot_update_digest_interval_
minutes）的注册/校验/回读，抽奖键并入「不得再被注册」名单。
"""

import os

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


def _make_user(db, username="u1", **kw):
    u = models.WebUser(username=username, password_hash="x", **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ==================== 1. 注册与默认值 ====================

def test_new_keys_registered_with_current_defaults():
    # 机器人键仍在册：默认值与 schema 口径不变
    assert welfare_admin.WELFARE_CONFIG_KEYS["bot_update_digest_interval_minutes"] == "30"
    assert config_schema.WELFARE_CONFIG_SCHEMA["bot_update_digest_interval_minutes"] == (
        "int", 1, 1440)
    # 公益资格整族键已摘除（2026-11-14）、注册赠送查看键已摘除
    # （2026-11-15）、抽奖键与 TG 门禁键已摘除（2026-11-23），
    # 两处注册表都不许再出现
    for key in ("register_gift_welfare_days", "register_gift_welfare_enabled",
                "register_gift_view_days", "register_gift_view_enabled",
                "welfare_vitality_renew_min", "welfare_code_monthly_quota",
                "welfare_grace_days", "welfare_inactive_days",
                "welfare_require_tg_bind",
                "lottery_group_ids", "lottery_draw_interval_sec",
                "lottery_require_group_member", "lottery_min_account_age_days",
                "lottery_max_joins_per_day", "lottery_announce_pin_enabled",
                "lottery_reminder_step", "lottery_final_call_minutes",
                "lottery_announce_edit_min_interval_sec"):
        assert key not in welfare_admin.WELFARE_CONFIG_KEYS
        assert key not in config_schema.WELFARE_CONFIG_SCHEMA


def test_preexisting_keys_for_ui_only_fields_untouched():
    # 机器人键：键＋校验成套保留
    assert welfare_admin.WELFARE_CONFIG_KEYS["bot_update_digest_interval_minutes"] == "30"
    assert config_schema.WELFARE_CONFIG_SCHEMA["bot_update_digest_interval_minutes"] == (
        "int", 1, 1440)
    # 经济键集与注册表仍逐键对齐（config_slim_b4e 的契约）
    assert set(config_schema.ECONOMY_CONFIG_SCHEMA) == set(admin_economy.ECONOMY_CONFIG_KEYS)


# ==================== 2. 校验 ====================

def test_new_keys_normalize_valid_values():
    assert config_schema.normalize(
        "bot_update_digest_interval_minutes",
        config_schema.WELFARE_CONFIG_SCHEMA["bot_update_digest_interval_minutes"], 45) == "45"


def test_new_keys_normalize_rejects_invalid():
    # 攒批间隔下限 1 分钟：0/负数/非数字/小数/布尔一律拒绝
    spec = config_schema.WELFARE_CONFIG_SCHEMA["bot_update_digest_interval_minutes"]
    for bad in (0, -1, "abc", 1.5, True):
        with pytest.raises(config_schema.ConfigValueError):
            config_schema.normalize("bot_update_digest_interval_minutes", spec, bad)


def test_welfare_keys_save_and_read_back(db):
    save_config(db, {"bot_update_digest_interval_minutes": "45",
                     "bot_rate_limit_seconds": "10"}, schema="welfare")
    db.commit()
    got = welfare_admin.welfare_config_get(db=db, current_admin=_make_user(db, "admin"))
    assert got["bot_update_digest_interval_minutes"] == "45"
    assert got["bot_rate_limit_seconds"] == "10"
    with pytest.raises(config_schema.ConfigValueError):
        save_config(db, {"bot_update_digest_interval_minutes": "0"}, schema="welfare")


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

