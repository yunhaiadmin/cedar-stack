"""支付设置保存 sanity 校验（2026-11-23）。

背景：空库重建后人工重填支付三键，payment_partner_key 被粘成 392 字符
长串（疑似粘错内容），落库零拦截，之后全部充值订单 MD5 验签失败。
校验在 backend/api/admin_economy.economy_update_settings 落库前：
- payment_partner_key 非空/非掩码时：首尾空白、内部空白或长度 >128 → 400；
  detail 只带长度数字，绝不带密钥原文；
- payment_gateway_url 非空时：必须以 http:// 或 https:// 开头，否则 400。

写法参照 tests/test_config_slim_b4e.py 的直调端点函数 + DB fixture：
直调层面 400 = 抛 HTTPException(400)，200 = 正常返回 success。
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

from backend import models
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


# ---------- payment_partner_key ----------

def test_partner_key_392_chars_rejected_400(db):
    s, admin = db
    bad_key = "x" * 392
    with pytest.raises(HTTPException) as ei:
        _save(s, admin, {"payment_partner_key": bad_key})
    assert ei.value.status_code == 400
    # detail 只带长度数字，绝不带密钥原文
    assert "392" in ei.value.detail
    assert bad_key not in ei.value.detail
    # 拦截发生在落库前：库里没有这行
    assert _row(s, "payment_partner_key") is None


def test_partner_key_32_chars_saved(db):
    s, admin = db
    good_key = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    assert len(good_key) == 32
    res = _save(s, admin, {"payment_partner_key": good_key})
    assert res["success"] is True
    assert "payment_partner_key" in res["changed"]
    assert _row(s, "payment_partner_key").value == good_key


def test_partner_key_mask_keeps_original(db):
    s, admin = db
    _save(s, admin, {"payment_partner_key": "realkey1234567890realkey12345678"})
    res = _save(s, admin, {"payment_partner_key": "******"})
    assert res["success"] is True
    assert "payment_partner_key" not in res["changed"]
    assert _row(s, "payment_partner_key").value == "realkey1234567890realkey12345678"


def test_partner_key_with_whitespace_rejected_400(db):
    s, admin = db
    for bad in (" abcdef1234567890", "abcdef1234567890 ", "abcd ef1234567890"):
        with pytest.raises(HTTPException) as ei:
            _save(s, admin, {"payment_partner_key": bad})
        assert ei.value.status_code == 400
        assert bad not in ei.value.detail
    assert _row(s, "payment_partner_key") is None


def test_partner_key_empty_keeps_original(db):
    s, admin = db
    _save(s, admin, {"payment_partner_key": "realkey1234567890realkey12345678"})
    res = _save(s, admin, {"payment_partner_key": ""})
    assert res["success"] is True
    assert _row(s, "payment_partner_key").value == "realkey1234567890realkey12345678"


# ---------- payment_gateway_url ----------

def test_gateway_url_without_scheme_rejected_400(db):
    s, admin = db
    with pytest.raises(HTTPException) as ei:
        _save(s, admin, {"payment_gateway_url": "pay.example.com/submit"})
    assert ei.value.status_code == 400
    assert _row(s, "payment_gateway_url") is None


def test_gateway_url_with_https_saved(db):
    s, admin = db
    res = _save(s, admin, {"payment_gateway_url": "https://pay.example.com/submit"})
    assert res["success"] is True
    assert _row(s, "payment_gateway_url").value == "https://pay.example.com/submit"


def test_gateway_url_with_http_saved(db):
    s, admin = db
    res = _save(s, admin, {"payment_gateway_url": "http://pay.example.com/submit"})
    assert res["success"] is True
    assert _row(s, "payment_gateway_url").value == "http://pay.example.com/submit"


def test_gateway_url_empty_not_rejected(db):
    s, admin = db
    res = _save(s, admin, {"payment_gateway_url": ""})
    assert res["success"] is True
