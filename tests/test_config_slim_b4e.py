"""配置简化第 4 批 4-6：economy 家族迁移到 config_service.save_config。

钉住点（全部是与迁移前行为逐字一致的断言）：
1. 注册表与 ECONOMY_CONFIG_KEYS 逐键对齐，spec 口径与旧 economy_spec 一致；
2. 存库字节逐字一致：bool/int 走同一 normalize；str/secret 用 ("raw",) 原样存
   ——含"空串存行（不删行）""None 存成 'None'"这些历史口径；
3. 失效缺口补齐：旧代码只手动失效 stream_accel_*/vitality_*，其余键保存后
   60 秒内可能读旧值；迁移后全部键写时失效。
"""
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config_schema, models
from backend.api import admin_economy
from backend.integrations import store


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


@pytest.fixture(autouse=True)
def _clean_store_cache():
    yield
    store.invalidate()


def _save(s, admin, settings):
    return admin_economy.economy_update_settings(
        admin_economy.EconomySettingsRequest(settings=settings),
        current_admin=admin, db=s)


def _row(s, key):
    return s.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()


# ---------- 1. 注册表保真 ----------

def test_registry_covers_all_economy_keys():
    keys = set(admin_economy.ECONOMY_CONFIG_KEYS)
    assert set(config_schema.ECONOMY_CONFIG_SCHEMA) == keys, (
        "注册表必须与 ECONOMY_CONFIG_KEYS 逐键对齐")
    # 抽查：口径与旧 economy_spec 一致（货币键已随货币于 2026-11-13 下线，
    # invitation_rebate_percent / checkin_* / vitality_* / recharge_enabled
    # 均不在注册表内）
    for gone in ("invitation_rebate_percent", "checkin_penalty_pct",
                 "checkin_streak_bonus", "checkin_enabled", "recharge_enabled",
                 "vitality_max", "invitation_invitee_reward_points"):
        assert gone not in config_schema.ECONOMY_CONFIG_SCHEMA
    assert config_schema.ECONOMY_CONFIG_SCHEMA["series_merge_interval_sec"] == ("int", 3600, 604800)
    assert config_schema.ECONOMY_CONFIG_SCHEMA["series_merge_batch_limit"] == ("int", 10, 100000)
    assert config_schema.ECONOMY_CONFIG_SCHEMA["media_seek_fulfill_interval_minutes"] == ("int", 1, 1440)
    assert config_schema.ECONOMY_CONFIG_SCHEMA["subscription_purchase_enabled"] == ("bool",)
    assert config_schema.ECONOMY_CONFIG_SCHEMA["payment_gateway_url"] == ("raw",)
    assert config_schema.ECONOMY_CONFIG_SCHEMA["payment_partner_key"] == ("raw",)
    assert "economy" in __import__("backend.config_service", fromlist=["x"]).SCHEMAS


# ---------- 2. 存库字节逐字一致 ----------

def test_byte_exact_bool_and_int(db):
    # 2026-10-21 A 类摘面板后改用保留键做向量；货币键已随货币于 2026-11-13
    # 下线，int 向量改用存活的 media_seek_* 键
    s, admin = db
    res = _save(s, admin, {
        "subscription_purchase_enabled": True, "subscription_required": False,
        "media_seek_daily_limit": 1,  # int 1 存 "1"
        "media_seek_fulfill_interval_minutes": "007",  # 归一化成 "7"
        "media_seek_daily_limit": " 5 ",
    })
    assert _row(s, "subscription_purchase_enabled").value == "true"
    assert _row(s, "subscription_required").value == "false"
    assert _row(s, "media_seek_daily_limit").value == "5"
    assert _row(s, "media_seek_fulfill_interval_minutes").value == "7"
    assert res["changed"] == ["subscription_purchase_enabled", "subscription_required",
                              "media_seek_daily_limit", "media_seek_fulfill_interval_minutes"]


def test_str_raw_empty_stored_not_deleted(db):
    """str 键的空串是存行（不是删行）——与 ("str",) 的空串=删行不同，这是旧口径。"""
    s, admin = db
    _save(s, admin, {"site_url": "", "payment_gateway_url": "  https://x.io/  "})
    row = _row(s, "site_url")
    assert row is not None and row.value == ""
    # 原样存：前后空格不修
    assert _row(s, "payment_gateway_url").value == "  https://x.io/  "


def test_str_none_stored_as_none_string(db):
    """None 传进来：旧代码 str(None)="None" 存库——历史口径，原样保留。"""
    s, admin = db
    _save(s, admin, {"site_url": None})
    assert _row(s, "site_url").value == "None"


def test_secret_mask_keeps_row(db):
    s, admin = db
    _save(s, admin, {"payment_partner_key": "realkey"})
    res = _save(s, admin, {"payment_partner_key": "******"})
    assert _row(s, "payment_partner_key").value == "realkey"
    assert "payment_partner_key" not in res["changed"]
    # 空值同样保留
    res = _save(s, admin, {"payment_partner_key": ""})
    assert _row(s, "payment_partner_key").value == "realkey"
    assert "payment_partner_key" not in res["changed"]


def test_secret_real_value_stored_and_masked_in_changed(db):
    s, admin = db
    res = _save(s, admin, {"payment_partner_key": "abc123"})
    assert _row(s, "payment_partner_key").value == "abc123"
    # changed 只记键列表；审计里的值是 "(已更新)"（见下个断言的端点代码口径）
    changed = res["changed"]
    assert changed == ["payment_partner_key"]


def test_secret_masked_in_audit(db, monkeypatch):
    """secret 的原文不进审计日志：审计 details 里只记 "(已更新)"。"""
    s, admin = db
    calls = []
    monkeypatch.setattr(admin_economy, "_audit",
                        lambda db_, admin_, action, ttype=None, tid=None, details=None, ip=None:
                        calls.append((action, details)))
    _save(s, admin, {"payment_partner_key": "topsecret", "media_seek_daily_limit": "5"})
    assert calls, "审计必须被调用"
    action, details = calls[-1]
    assert action == "economy_update_settings"
    assert details["payment_partner_key"] == "(已更新)"
    assert details["media_seek_daily_limit"] == "5"
    assert "topsecret" not in str(calls)


def test_empty_int_deletes_row(db):
    s, admin = db
    _save(s, admin, {"media_seek_daily_limit": "5"})
    assert _row(s, "media_seek_daily_limit") is not None
    res = _save(s, admin, {"media_seek_daily_limit": ""})
    assert _row(s, "media_seek_daily_limit") is None
    assert res["changed"] == ["media_seek_daily_limit"]


def test_invalid_rejected_atomically(db):
    s, admin = db
    # schema 层校验失败在直接调用层面抛 ConfigValueError；HTTP 400 的转换由
    # main.py 的统一 handler 承接（端到端见 test_config_value_error_400.py）。
    # series_merge_interval_sec 下限 3600，传 100 超范围
    with pytest.raises(config_schema.ConfigValueError):
        _save(s, admin, {"media_seek_fulfill_interval_minutes": "5",
                         "series_merge_interval_sec": "100"})  # 超范围
    # 整批不落库：合法的那个也没写进去
    assert _row(s, "media_seek_fulfill_interval_minutes") is None
    assert _row(s, "series_merge_interval_sec") is None


def test_unknown_key_ignored(db):
    s, admin = db
    res = _save(s, admin, {"no_such_key": "1", "media_seek_daily_limit": "5"})
    assert res["success"] is True
    assert res["changed"] == ["media_seek_daily_limit"]
    assert _row(s, "no_such_key") is None


# ---------- 3. 失效缺口补齐 ----------

def test_invalidation_full_coverage(db):
    """media_seek_daily_limit 此前从不失效（旧代码只管 stream_accel/vitality）：
    旧值灌进热缓存 → 保存 → 必须立刻读到新值。"""
    s, admin = db
    s.add(models.SystemConfig(key="media_seek_daily_limit", value="10"))
    s.commit()
    assert store.get_value(s, "media_seek_daily_limit", "nope") == "10"  # 灌缓存
    _save(s, admin, {"media_seek_daily_limit": "20"})
    assert store.get_value(s, "media_seek_daily_limit", "nope") == "20"


def test_invalidation_on_delete(db):
    s, admin = db
    s.add(models.SystemConfig(key="media_seek_daily_limit", value="5"))
    s.commit()
    assert store.get_value(s, "media_seek_daily_limit", "nope") == "5"
    _save(s, admin, {"media_seek_daily_limit": ""})  # 删行
    # 删行后读不到旧值（缺行 → 默认）
    assert store.get_value(s, "media_seek_daily_limit", "gone") == "gone"


# ---------- 4. 端点层复合校验原样保留 ----------

def test_vitality_cross_field_still_rejected(db):
    # 活力键已随货币于 2026-11-13 下线：端点静默忽略未知键，复合校验无从触发
    s, admin = db
    res = _save(s, admin, {"vitality_max": "10", "vitality_limit_threshold": "20"})
    assert res["success"] is True
    assert res["changed"] == []
    assert _row(s, "vitality_max") is None


def test_checkin_min_gt_max_still_rejected(db):
    # 签到键已随货币于 2026-11-13 下线：端点静默忽略未知键，不再校验上下限
    s, admin = db
    res = _save(s, admin, {"checkin_base_min": "9", "checkin_base_max": "2"})
    assert res["success"] is True
    assert res["changed"] == []
    assert _row(s, "checkin_base_min") is None


def test_stream_accel_domain_linkage_still_enforced(db):
    """开加速开关必须配合法域名——联动校验仍在端点层。"""
    s, admin = db
    with pytest.raises(HTTPException) as ei:
        _save(s, admin, {"stream_accel_enabled": True,
                         "stream_accel_domain": "not a domain!!"})
    assert ei.value.status_code == 400
    assert _row(s, "stream_accel_enabled") is None
