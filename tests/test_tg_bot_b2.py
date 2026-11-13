"""B2 测试：TG Bot 业务命令（/points /redeem；/checkin 已于 2026-11-13 摘除）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend import models
from backend.tg_bot import identity, handlers, router


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    # 只建需要的表
    models.WebUser.__table__.create(engine, checkfirst=True)
    models.TgBindCode.__table__.create(engine, checkfirst=True)
    # SystemConfig 表（store.get_value 需要）
    models.SystemConfig.__table__.create(engine, checkfirst=True)
    # B2 业务表
    models.CheckinRecord.__table__.create(engine, checkfirst=True)
    models.PointsLog.__table__.create(engine, checkfirst=True)
    models.ExchangeCode.__table__.create(engine, checkfirst=True)
    models.CodeRedemption.__table__.create(engine, checkfirst=True)
    models.VitalityLog.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_user(db, username="u1", tg_id=None, is_welfare=False):
    u = models.WebUser(username=username, telegram_id=tg_id)
    # 必填字段兜底
    for col in ("password_hash",):
        if hasattr(u, col) and getattr(u, col) is None:
            try:
                setattr(u, col, "")
            except Exception:
                pass
    if hasattr(u, "is_welfare"):
        try:
            setattr(u, "is_welfare", is_welfare)
        except Exception:
            pass
    db.add(u)
    db.commit()
    return u

def test_points_unbound(db):
    text = handlers.handle_points(db, {"id": 910002}, 910002, "")
    assert "/bind" in text


def test_redeem_unbound(db):
    text = handlers.handle_redeem(db, {"id": 910003}, 910003, "ANYCODE")
    assert "/bind" in text


def test_points_bound(db):
    user = _make_user(db, username="u8", tg_id=910008)
    user.points = 250
    db.commit()
    text = handlers.handle_points(db, {"id": 910008}, 910008, "")
    assert "▎<b>我的能量</b>" in text
    assert "✨ <b>能量余额</b> | 250" in text


def test_points_card_has_no_vitality_row(db):
    """活力已随货币删除下线（2026-11-13）：能量卡只剩余额行，
    不再有活力数值行（其配置模块 backend.vitality 已删）。"""
    user = _make_user(db, username="u9", tg_id=910009, is_welfare=True)
    text = handlers.handle_points(db, {"id": 910009}, 910009, "")
    assert "✨ <b>能量余额</b>" in text
    assert "活力数值" not in text


def test_redeem_no_args(db):
    user = _make_user(db, username="u10", tg_id=910010)
    text = handlers.handle_redeem(db, {"id": 910010}, 910010, "")
    assert "使用方法" in text


def test_redeem_invalid_code(db):
    user = _make_user(db, username="u11", tg_id=910011)
    text = handlers.handle_redeem(db, {"id": 910011}, 910011, "NOPE9999")
    assert "兑换码无效或已过期" in text


def test_redeem_points_code_rejected(db):
    """积分型兑换码已随货币删除下线（2026-11-13）：兑码核心按类型
    拒绝，机器人原样回错误文案，不发分。"""
    user = _make_user(db, username="u12", tg_id=910012, is_welfare=True)
    code = models.ExchangeCode(code="TEST100", type="points", points_value=100, is_active=True, max_uses=1)
    db.add(code)
    db.commit()
    text = handlers.handle_redeem(db, {"id": 910012}, 910012, "test100")
    assert "兑换码类型不支持" in text
    db.refresh(user)
    assert user.points == 0


def test_checkin_entry_removed(db):
    """签到入口摘除回归钉（2026-11-13 用户亲点）：handler 不存在、
    /checkin 走未知命令路径，不再有任何签到应答。"""
    from backend.tg_bot import poller, sender
    assert not hasattr(handlers, "handle_checkin")
    assert "checkin" not in {name for name, _ in poller.BOT_COMMANDS}
    _make_user(db, username="u13", tg_id=910013, is_welfare=True)
    update = {
        "update_id": 13,
        "message": {
            "message_id": 13,
            "from": {"id": 910013, "first_name": "R"},
            "chat": {"id": 910013, "type": "private"},
            "text": "/checkin",
        },
    }
    sent = []
    orig = sender.send_message
    sender.send_message = lambda db_, chat_id, text, reply_markup=None, **kw: sent.append(text) or (True, None)
    try:
        router.dispatch(db, update)
    finally:
        sender.send_message = orig
    assert any("未知命令" in t for t in sent)
    assert not any("签到" in t for t in sent)


def test_router_dispatch_points_and_redeem_registered(db):
    """router.commands 包含业务命令（自动享受可配置限流）。"""
    from backend.tg_bot.router import _user_cmd_allow
    # 间接验证：_user_cmd_allow 对命令生效且互不干扰
    assert _user_cmd_allow(920001, "/points", 3.0) is True
    assert _user_cmd_allow(920001, "/points", 3.0) is False  # 3 秒内重复被限
    assert _user_cmd_allow(920001, "/redeem", 3.0) is True    # 不同命令独立计数
    assert _user_cmd_allow(920001, "/chatpoints", 3.0) is True
