"""PA-029（R12-B12 机器人）回归测试。

PA-029：停用账号经机器人签到/兑码/群发言仍能获利（PA-019 博彩门之外的
同根缺口）。修法落机器人侧入口：handlers.handle_checkin/handle_redeem 与
router._award_group_chat_points 各加停用门，不改 tg_bot resolve 层与网站核心。
（PA-030 抽奖按钮用例随批 9 抽奖面板下线一并删除。）

全部用隔离的内存 SQLite，不碰生产库。先红后绿：本文件用例在修复前应为红。
"""
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.integrations import store
from backend.tg_bot import callbacks, handlers, router

GROUP = -1001234567
TG = 900001


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    try:
        yield session
    finally:
        session.close()
        store.invalidate()
        engine.dispose()


def _make_user(db, username, tg_id, points=0, is_active=True, is_welfare=True):
    u = models.WebUser(
        username=username, password_hash="x", telegram_id=tg_id,
        points=points, is_active=is_active)
    if hasattr(u, "is_welfare"):
        u.is_welfare = is_welfare
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


def _tg(tg=TG):
    return {"id": tg, "first_name": "Alice"}


def _chat_logs(db, user_id):
    return db.query(models.PointsLog).filter(
        models.PointsLog.user_id == user_id,
        models.PointsLog.type == "chat").all()


# ---------- PA-029：停用账号机器人三路获利门 ----------

def test_disabled_user_cannot_checkin_via_bot(db):
    _set_config(db, "checkin_enabled", "true")
    user = _make_user(db, "disabled_checkin", TG, points=0, is_active=False)
    text = handlers.handle_checkin(db, _tg(), TG, "")
    assert "账号已被停用" in text
    assert "签到成功" not in text
    db.refresh(user)
    assert user.points == 0
    assert db.query(models.CheckinRecord).filter(
        models.CheckinRecord.user_id == user.id).count() == 0


def test_active_user_checkin_still_works(db):
    _set_config(db, "checkin_enabled", "true")
    user = _make_user(db, "active_checkin", TG, points=0, is_active=True)
    text = handlers.handle_checkin(db, _tg(), TG, "")
    assert "签到成功" in text
    db.refresh(user)
    assert user.points > 0


def test_disabled_user_cannot_redeem_via_bot(db):
    user = _make_user(db, "disabled_redeem", TG, points=0, is_active=False)
    code = models.ExchangeCode(code="TEST100", type="points", points_value=100,
                               is_active=True, max_uses=1)
    db.add(code)
    db.commit()
    text = handlers.handle_redeem(db, _tg(), TG, "TEST100")
    assert "账号已被停用" in text
    db.refresh(user)
    assert user.points == 0
    assert db.query(models.CodeRedemption).filter(
        models.CodeRedemption.user_id == user.id).count() == 0


def test_active_user_redeem_still_works(db):
    user = _make_user(db, "active_redeem", TG, points=0, is_active=True)
    code = models.ExchangeCode(code="TEST101", type="points", points_value=100,
                               is_active=True, max_uses=1)
    db.add(code)
    db.commit()
    text = handlers.handle_redeem(db, _tg(), TG, "TEST101")
    assert "兑换成功" in text
    db.refresh(user)
    assert user.points == 100


def test_disabled_user_group_chat_earns_nothing(db):
    user = _make_user(db, "disabled_chat", TG, points=0, is_active=False)
    for _ in range(12):
        router._award_group_chat_points(db, _tg())
    db.refresh(user)
    assert user.points == 0
    assert _chat_logs(db, user.id) == []


def test_active_user_group_chat_still_earns(db):
    user = _make_user(db, "active_chat", TG, points=0, is_active=True)
    for _ in range(12):
        router._award_group_chat_points(db, _tg())
    db.refresh(user)
    assert user.points == 1
    assert len(_chat_logs(db, user.id)) >= 1
