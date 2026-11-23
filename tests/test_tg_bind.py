"""TG 绑定机器测试（绑定码/状态/解绑/引导开关）。

原「公益服强制绑定门禁」一组用例已随门禁整族于 2026-11-23 删除。
全部用隔离的内存 SQLite，不碰生产库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend import tg_bind


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user(db, username, **kw):
    u = models.WebUser(username=username, password_hash="x", **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


def test_generate_code(db):
    user = _make_user(db, "frank")
    code1 = tg_bind.generate_bind_code(db, user)
    assert len(code1["code"]) == 6 and code1["code"].isdigit()
    code2 = tg_bind.generate_bind_code(db, user)
    assert len(code2["code"]) == 6 and code2["code"].isdigit()
    assert code2["code"] != code1["code"]
    old = db.query(models.TgBindCode).filter(models.TgBindCode.code == code1["code"]).first()
    assert old is not None
    assert old.used_at is not None


def test_unbind(db):
    user = _make_user(db, "grace", telegram_id=999)
    tg_bind.unbind(db, user)
    assert user.telegram_id is None
    db.commit()
    db.refresh(user)
    assert user.telegram_id is None


def test_get_bind_status(db):
    user = _make_user(db, "heidi")
    status = tg_bind.get_bind_status(db, user)
    assert status["bound"] is False
    assert "required" not in status and "in_grace" not in status
    user.telegram_id = 12345
    db.commit()
    db.refresh(user)
    status = tg_bind.get_bind_status(db, user)
    assert status["bound"] is True


def test_guide_enabled_default_true(db):
    user = _make_user(db, "alice")
    assert tg_bind.get_bind_status(db, user)["guide_enabled"] is True


def test_guide_enabled_off(db):
    user = _make_user(db, "bob")
    _set_config(db, "tg_bind_guide_enabled", "0")
    assert tg_bind.get_bind_status(db, user)["guide_enabled"] is False


def test_guide_enabled_value_variants(db):
    user = _make_user(db, "carol")
    for value in ("false", "no", "off"):
        _set_config(db, "tg_bind_guide_enabled", value)
        assert tg_bind.get_bind_status(db, user)["guide_enabled"] is False
    for value in ("1", "true"):
        _set_config(db, "tg_bind_guide_enabled", value)
        assert tg_bind.get_bind_status(db, user)["guide_enabled"] is True
