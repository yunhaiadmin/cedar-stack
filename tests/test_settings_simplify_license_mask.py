"""设置页开关从简（第二批）＋授权码掩码（2026-11-23）。

钉死口径：
- 本批 16 个键加入 PANEL_REMOVED_SWITCHES：面板摘开关后走
  env（文件配置）> DB 存量行 > 代码缺省；生产现值（2026-11-23
  只读查 system_configs）与代码缺省逐键对齐的，缺行时行为不变；
  缺省与生产现值不一致的两键（media_seek_auto_approve_enabled
  生产 true、wallet_auto_renew_enabled 生产 false）靠 DB 存量行
  保值（行不删），本文件把这条也钉住。
- drive_binding_enabled 生产 false、缺省 false：摘开关绝不复活绑盘。
- 授权码：管理端 GET 出口只回 ****** + license_code_set；PUT 收到
  ****** 不覆盖原值；PUT 真值能更新。license_client 内部明文路径
  （configured_code / admin_status 本体）不在本批改动面。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request as StarletteRequest

from backend import license_client as lc
from backend import media_seek, models, wallet
from backend.api import admin_license
from backend.emby_server import pool_gate, user_bindings
from backend.integrations import store

SECRET_CODE = "AX-SECRET-CODE-9876"

#: 本批摘面板的 16 键 → 生产现值（2026-11-23 只读查得；无行键记
#: 其代码缺省即生产生效值）
REMOVED_KEYS_PROD = {
    "media_seek_auto_approve_enabled": "true",
    "subscription_purchase_enabled": "true",
    "payment_qqpay_enabled": "false",
    "wallet_enabled": "true",
    "wallet_topup_rate_manual_override": "false",
    "wallet_refund_to_balance": "true",
    "wallet_auto_renew_enabled": "false",
    "cross_provider_transfer_enabled": "true",
    "transfer_series_preheat_enabled": "true",
    "pool_large_file_routing_enabled": "true",
    "mount_serving_enabled": "true",
    "drive_binding_enabled": "false",
    "web_softdecode_enabled": "false",
    "danmaku_enabled": "false",
    "danmu_api_external_enabled": "false",
    "login_lockout_enabled": "true",
}


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    from backend.emby_server import models as em
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    session.close()
    store.invalidate()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in REMOVED_KEYS_PROD:
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.delenv("AETRIX_LICENSE_ENFORCE", raising=False)
    monkeypatch.delenv("AETRIX_LICENSE_CODE", raising=False)
    lc.invalidate_state_cache()
    yield
    lc.invalidate_state_cache()


def _seed(db, key, value):
    db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    store.invalidate(key)


# ---------------- 开关摘面板：登记与保值 ----------------

def test_all_batch_keys_registered_panel_removed():
    assert set(REMOVED_KEYS_PROD) <= store.PANEL_REMOVED_SWITCH_KEYS
    # 37（首批）+ 16（本批）= 53，store.py 注释数字须与实际一致
    assert len(store.PANEL_REMOVED_SWITCHES) == 53


def test_env_overrides_db_for_removed_keys(db, monkeypatch):
    """文件配置承接路径真存在：env 显式设置即接管（以绑盘键为例）。"""
    _seed(db, "drive_binding_enabled", "false")
    assert user_bindings.drive_binding_enabled(db) is False
    monkeypatch.setenv("DRIVE_BINDING_ENABLED", "true")
    assert store.get_value(db, "drive_binding_enabled", "") == "true"


def test_drive_binding_default_stays_off(db):
    """无行无 env：绑盘开关缺省关，摘面板不复活绑盘（最终定版）。"""
    assert user_bindings.drive_binding_enabled(db) is False


def test_code_defaults_match_prod_for_aligned_keys(db):
    """缺行时各消费方的代码缺省 = 生产生效值（逐键）。"""
    from backend import danmaku, danmu_api
    from backend.api import admin_softdecode
    from backend.emby_server import mount_serving, transfer_variant
    from backend.ratelimit import login_lockout_config

    assert transfer_variant.cross_provider_enabled(db) is True
    assert transfer_variant.series_preheat_enabled(db) is True
    assert pool_gate.pool_config_payload(db)[
        "pool_large_file_routing_enabled"] is True
    assert mount_serving.serving_enabled(db) is True
    assert admin_softdecode.softdecode_enabled(db) is False
    assert danmaku.danmaku_enabled(db) is False
    assert danmu_api.external_enabled(db) is False
    assert login_lockout_config(db).enabled is True
    # 钱包两键缺行回默认：开 / 开（与生产 true 一致）
    cfg = wallet.wallet_config(db)
    assert cfg["wallet_enabled"] is True
    assert cfg["wallet_refund_to_balance"] is True
    assert cfg["wallet_topup_rate_manual_override"] is False


def test_prod_rows_preserve_mismatched_defaults(db):
    """缺省与生产现值不一致的两键靠存量行保值：行在，行为不变。"""
    _seed(db, "media_seek_auto_approve_enabled", "true")
    _seed(db, "wallet_auto_renew_enabled", "false")
    assert media_seek.auto_approve_enabled(db) is True
    assert wallet.wallet_config(db)["wallet_auto_renew_enabled"] is False


def test_login_lockout_env_takes_over(db, monkeypatch):
    """login_lockout 读侧原走直查 ORM，本批补 env 接管后才有文件配置路径。"""
    from backend.ratelimit import login_lockout_config

    monkeypatch.setenv("LOGIN_LOCKOUT_ENABLED", "false")
    assert login_lockout_config(db).enabled is False


# ---------------- 授权码掩码 ----------------

def _admin():
    return SimpleNamespace(id=1, username="root", role="super_admin")


def _request():
    scope = {"type": "http", "method": "PUT", "path": "/",
             "headers": [], "query_string": b"", "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 40000)}
    return StarletteRequest(scope)


def test_license_get_masks_code(db):
    _seed(db, lc.KEY_CODE, SECRET_CODE)
    out = admin_license.license_get_status(_admin(), db)
    assert out["code"] == "******"
    assert out["license_code_set"] is True
    assert SECRET_CODE not in str(out)


def test_license_get_no_code(db):
    out = admin_license.license_get_status(_admin(), db)
    assert out["code"] == ""
    assert out["license_code_set"] is False


def test_license_put_mask_does_not_overwrite(db, monkeypatch):
    _seed(db, lc.KEY_CODE, SECRET_CODE)
    monkeypatch.setattr(lc, "refresh", lambda *a, **k: {})
    out = admin_license.license_update_config(
        admin_license.LicenseConfigRequest(code="******"),
        _request(), _admin(), db)
    assert lc.configured_code(db) == SECRET_CODE
    assert out["code"] == "******"
    assert SECRET_CODE not in str(out)


def test_license_put_real_value_updates(db, monkeypatch):
    _seed(db, lc.KEY_CODE, SECRET_CODE)
    monkeypatch.setattr(lc, "refresh", lambda *a, **k: {})
    admin_license.license_update_config(
        admin_license.LicenseConfigRequest(code="ax-new-code-1234"),
        _request(), _admin(), db)
    assert lc.configured_code(db) == "AX-NEW-CODE-1234"
