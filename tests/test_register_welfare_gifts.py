"""支付方法列表跟购买开关走。

背景（2026-10-28 公益服货币系统设计）：购买开关关了，
GET /payment/methods 仍硬编码 alipay/wxpay enabled=True，前端显示
“可用”、用户点单才 503——列表必须跟开关走。

本文件原有的「新注册冷启动赠送」一组测试已于 2026-11-15 随注册
赠送逻辑整体删除而移除（注册不送体验的新口径由
tests/test_registration_open_no_code.py 覆盖）。

只测支付方法列表：不碰购买流程、不碰现有用户行、不碰积分扣减逻辑。
本地 Python 3.11 口径，全量以 GitHub CI 为准。
"""

import asyncio
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api.economy import payment_methods
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
    store.invalidate()  # store 缓存只按键名不分库，进场先清防串味
    yield session
    session.close()
    store.invalidate()


def _set_config(db, key, value):
    db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    store.invalidate(key)  # 与生产写时失效同口径


def _methods_by_id(db):
    return {m.id: m.enabled for m in asyncio.run(payment_methods(db))}


# ==================== 支付方法列表 ====================

def test_payment_methods_purchase_on_default(db):
    """开关未配（默认 true）：alipay/wxpay 启用，无 qqpay。"""
    by_id = _methods_by_id(db)
    assert by_id == {"alipay": True, "wxpay": True}


def test_payment_methods_purchase_on_explicit(db):
    """开关显式 true：行为与默认一致。"""
    _set_config(db, "subscription_purchase_enabled", "true")
    by_id = _methods_by_id(db)
    assert by_id == {"alipay": True, "wxpay": True}


def test_payment_methods_purchase_off_disables_all(db):
    """开关 false：alipay/wxpay 一律 enabled=False（购买口本身不动，只改列表）。"""
    _set_config(db, "subscription_purchase_enabled", "false")
    by_id = _methods_by_id(db)
    assert by_id == {"alipay": False, "wxpay": False}


def test_payment_methods_purchase_off_qqpay_also_disabled(db):
    """开关 false 且 qqpay 已配：qqpay 照常出现在列表里，但同样 disabled。"""
    _set_config(db, "subscription_purchase_enabled", "false")
    _set_config(db, "payment_qqpay_enabled", "true")
    by_id = _methods_by_id(db)
    assert by_id == {"alipay": False, "wxpay": False, "qqpay": False}


def test_payment_methods_purchase_on_qqpay_enabled(db):
    """开关 true 且 qqpay 已配：qqpay 启用（现有行为不变）。"""
    _set_config(db, "subscription_purchase_enabled", "true")
    _set_config(db, "payment_qqpay_enabled", "true")
    by_id = _methods_by_id(db)
    assert by_id == {"alipay": True, "wxpay": True, "qqpay": True}
