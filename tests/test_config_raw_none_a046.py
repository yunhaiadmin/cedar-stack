# -*- coding: utf-8 -*-
"""配置 null 存成 "None" 回归测试（360° 审计 A-046）。

根因：
    config_schema.normalize 的 ("raw",) 分支排在「空串/None → None」早退
    之前、无条件 str(value)：管理端传 JSON null 时，site_url /
    payment_partner_id 等 raw 键被存成字面字符串 "None"，与 save_config
    「None = 未设置（删行）」的全局约定相悖，读取侧会把 "None" 当真值
    （如站点地址变成 "None"）。

修复：
    raw 分支对 None 返回 None（走删行）；空串照旧存行——那是迁移时确认
    的历史口径（空白名单落库 ""、读侧空=不检查），不在本条范围内。

覆盖：
    a) normalize 单元：raw+None → None；raw+"" → ""（存行口径不变）；
       raw+普通字符串原样。
    b) save_config 集成：先存真值，再以 None 保存 → 行被删除而非变 "None"；
       对从未存过的键传 None → 不产生 "None" 行。
    c) 端点路径：economy 设置端点传 {"site_url": None} → 行删除。
    （test_config_slim_b4e 里钉「None 存 'None'」的迁移期断言已随本条改判。）
"""
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import config_schema, config_service, models
from backend.api import admin_economy
from backend.integrations import store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    store.invalidate()
    yield s
    store.invalidate()
    s.close()
    engine.dispose()


def _row(s, key):
    return s.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()


# ---------- a) normalize 单元 ----------

def test_raw_none_normalizes_to_unset():
    assert config_schema.normalize("site_url", ("raw",), None) is None


def test_raw_empty_string_still_stored():
    # 空串存行是确认过的历史口径，不许被本条修复顺手改掉
    assert config_schema.normalize("site_url", ("raw",), "") == ""


def test_raw_plain_value_unchanged():
    assert config_schema.normalize("site_url", ("raw",), "https://x.io") == "https://x.io"
    assert config_schema.normalize("site_url", ("raw",), "  padded  ") == "  padded  "


# ---------- b) save_config 集成 ----------

def test_save_config_none_deletes_raw_row(db):
    config_service.save_config(db, {"site_url": "https://example.com"}, schema="economy")
    db.commit()
    assert _row(db, "site_url").value == "https://example.com"

    config_service.save_config(db, {"site_url": None}, schema="economy")
    db.commit()
    assert _row(db, "site_url") is None, "None 应删行，而不是存成字面 'None'"


def test_save_config_none_on_unset_raw_key_creates_no_row(db):
    config_service.save_config(db, {"payment_partner_id": None}, schema="economy")
    db.commit()
    assert _row(db, "payment_partner_id") is None


# ---------- c) 端点路径 ----------

def test_economy_endpoint_null_deletes_row(db):
    admin = models.WebUser(username="admin", password_hash="x", is_staff=True)
    db.add(admin)
    db.commit()

    def _save(settings):
        return admin_economy.economy_update_settings(
            admin_economy.EconomySettingsRequest(settings=settings),
            current_admin=admin, db=db)

    _save({"site_url": "https://example.com"})
    assert _row(db, "site_url").value == "https://example.com"

    _save({"site_url": None})
    assert _row(db, "site_url") is None, "端点传 null 应删行，而不是存成字面 'None'"
