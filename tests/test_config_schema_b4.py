"""第 4 批 4-2：config_schema 注册表扩展（6 家族）+ normalize 新 kind 的单测。"""
import pytest

from backend import config_schema
from backend.config_schema import ConfigValueError, normalize


class TestNewKinds:
    def test_str_passthrough(self):
        assert normalize("k", ("str",), "https://x.example/hook") == "https://x.example/hook"
        assert normalize("k", ("str",), "") is None
        assert normalize("k", ("str",), None) is None

    def test_enum(self):
        assert normalize("mode", ("enum", ("open", "closed")), "open") == "open"
        with pytest.raises(ConfigValueError):
            normalize("mode", ("enum", ("open", "closed")), "half-open")
        # 空串按模块约定 = 未设置
        assert normalize("mode", ("enum", ("open", "closed")), "") is None

    def test_bool_style_from_spec(self):
        # spec 自带风格位优先于 bool_style 参数
        assert normalize("k", ("bool", "digit"), True) == "1"
        assert normalize("k", ("bool", "digit"), False) == "0"
        assert normalize("k", ("bool", "word"), True) == "true"
        assert normalize("k", ("bool",), True, bool_style="digit") == "1"
        # 既有行为：("bool",) 默认 word
        assert normalize("k", ("bool",), True) == "true"


class TestFamilySchemas:
    def test_alerts_schema(self):
        s = config_schema.ALERTS_CONFIG_SCHEMA
        assert normalize("system_alerts_enabled", s["system_alerts_enabled"], True) == "1"
        assert normalize("alert_check_interval_sec", s["alert_check_interval_sec"], 300) == "300"
        with pytest.raises(ConfigValueError):
            normalize("alert_check_interval_sec", s["alert_check_interval_sec"], 59)
        with pytest.raises(ConfigValueError):
            normalize("alert_check_interval_sec", s["alert_check_interval_sec"], 86401)
        assert normalize("webhook_alert_url", s["webhook_alert_url"], "http://x") == "http://x"

    def test_reminders_schema(self):
        s = config_schema.REMINDERS_CONFIG_SCHEMA
        # 2026-10-21 A 类摘面板：expiry_reminder_enabled 已摘除（env 控制）
        assert "expiry_reminder_enabled" not in s
        assert normalize("expiry_reminder_days", s["expiry_reminder_days"], "7,3,1") == "7,3,1"
        # 2026-10-17 第 4 批 4-4 裁决：days 今天端点原文存储、从不拒绝（读侧宽容），
        # 注册为 ("str",) 与今天逐字一致，非法档位不再整批拒绝。
        assert normalize("expiry_reminder_days", s["expiry_reminder_days"], "7,0") == "7,0"

    def test_coupons_schema(self):
        s = config_schema.COUPONS_CONFIG_SCHEMA
        # 2026-10-21 A 类摘面板：coupon_enabled 已摘除（env 控制，默认开）
        assert "coupon_enabled" not in s
        assert normalize("coupon_reserve_hours", s["coupon_reserve_hours"], 24) == "24"
        assert normalize("coupon_reserve_hours", s["coupon_reserve_hours"], 0) == "0"
        with pytest.raises(ConfigValueError):
            normalize("coupon_reserve_hours", s["coupon_reserve_hours"], 721)

    def test_registration_schema(self):
        s = config_schema.REGISTRATION_CONFIG_SCHEMA
        assert normalize("registration_mode", s["registration_mode"], "closed") == "closed"
        with pytest.raises(ConfigValueError):
            normalize("registration_mode", s["registration_mode"], "invite")
        # A-112：register_ratelimit_enabled 恢复登记（bool word 风格）。
        assert normalize("register_ratelimit_enabled", s["register_ratelimit_enabled"], True) == "true"
        assert normalize("register_ratelimit_enabled", s["register_ratelimit_enabled"], False) == "false"
        assert normalize("register_ratelimit_max", s["register_ratelimit_max"], 5) == "5"
        with pytest.raises(ConfigValueError):
            normalize("register_ratelimit_max", s["register_ratelimit_max"], 0)
        assert normalize("register_ratelimit_window", s["register_ratelimit_window"], 60) == "60"
        with pytest.raises(ConfigValueError):
            normalize("register_ratelimit_window", s["register_ratelimit_window"], 59)

    def test_welfare_lottery_keys_unregistered(self):
        # lottery_* 与 welfare_require_tg_bind 已随群抽奖/公益门禁整族
        # 于 2026-11-23 删除：不得再出现在 welfare 注册表里
        s = config_schema.WELFARE_CONFIG_SCHEMA
        for key in ("lottery_require_group_member", "lottery_min_account_age_days",
                    "lottery_max_joins_per_day", "lottery_draw_interval_sec",
                    "lottery_group_ids", "lottery_announce_pin_enabled",
                    "lottery_reminder_step", "lottery_final_call_minutes",
                    "lottery_announce_edit_min_interval_sec",
                    "welfare_require_tg_bind"):
            assert key not in s
