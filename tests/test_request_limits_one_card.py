"""求片额度收拢（2026-10-23 运营参数去重 #5）回归测试。

此前三个求片额度散在两处：日限 media_seek_daily_limit 在运营参数，
公益月限 welfare_request_monthly 在公益页，付费月限 paid_request_monthly
没有任何后台入口（只能改库）。去重 #5 把两个月限登记进 ECONOMY 注册表
与 schema（保存路径）。2026-11-14 福利服役族删除后，welfare_request_monthly
的唯一消费方（media_seek 公益分支）已摘除，该键随之从两处注册表摘除；
paid_request_monthly 保留。这里只钉「登记 + 可保存 + 福利键已摘」。
"""
from backend import config_schema
from backend.api import admin_economy


def test_monthly_limits_registered_for_ops_settings():
    assert admin_economy.ECONOMY_CONFIG_KEYS["paid_request_monthly"] == "int"
    assert config_schema.ECONOMY_CONFIG_SCHEMA["paid_request_monthly"] == ("int", 0, 10000)
    # 福利月限键已随福利服役族摘除：两处注册表都不再有
    assert "welfare_request_monthly" not in admin_economy.ECONOMY_CONFIG_KEYS
    assert "welfare_request_monthly" not in config_schema.ECONOMY_CONFIG_SCHEMA


def test_monthly_limits_normalize_like_other_ints():
    spec = config_schema.ECONOMY_CONFIG_SCHEMA["paid_request_monthly"]
    assert config_schema.normalize("paid_request_monthly", spec, "010") == "10"


def test_monthly_limits_reject_out_of_range():
    import pytest

    schema = config_schema.ECONOMY_CONFIG_SCHEMA
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize("paid_request_monthly",
                                schema["paid_request_monthly"], "10001")
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize("paid_request_monthly",
                                schema["paid_request_monthly"], "-1")


def test_monthly_limits_still_valid_for_welfare_endpoint():
    # 福利端点侧同样不再登记该键（福利配置表早已摘除）
    assert "welfare_request_monthly" not in config_schema.WELFARE_CONFIG_SCHEMA
