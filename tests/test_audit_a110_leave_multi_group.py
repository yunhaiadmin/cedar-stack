"""A-110 回归（口径已随福利服役族于 2026-11-14 变更）。

历史问题：退群事件只凭"离了当前群"就取消公益资格是错的——处置前
须逐群复核"在群"口径。2026-11-14 公益资格整族删除后，退群处置本身
已下线：_handle_chat_member 只做校验、零处置零通知。本文件把原四种
场景（仍在别群 / 已离全部群 / 接口失败 / 单群）全部钉成无动作，
防处置逻辑以任何形式复活。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.integrations import store
from backend.tg_bot import router, sender

GROUP_A = -100111
GROUP_B = -100222
USER_TG = 700201


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    for table in (
        models.WebUser.__table__,
        models.SystemConfig.__table__,
        models.AdminLog.__table__,
    ):
        table.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    session.close()
    store.invalidate()


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(
        sender, "send_message",
        lambda db_, chat_id, text, reply_markup=None, **kw:
            out.append((chat_id, text)) or (True, None),
    )
    return out


def _user(db, tg_id):
    u = models.WebUser(username="u%d" % tg_id, telegram_id=tg_id)
    u.password_hash = ""
    u.points = 321
    db.add(u)
    db.commit()
    return u


def _stub_members(monkeypatch, per_chat):
    def _check(db_, chat_id, user_id):
        return per_chat.get(int(chat_id), (False, "not_found"))

    monkeypatch.setattr(sender, "get_chat_member", _check)


def _leave_event(tg_id, chat_id):
    return {
        "chat": {"id": chat_id, "type": "supergroup"},
        "old_chat_member": {"user": {"id": tg_id, "is_bot": False}, "status": "member"},
        "new_chat_member": {"user": {"id": tg_id, "is_bot": False}, "status": "left"},
    }


def _cfg(groups):
    # 存量配置值 cancel_welfare 也必须无动作（动作已删、恒 none）
    return {"bot_group_ids": groups, "bot_leave_action": "cancel_welfare"}


def _assert_untouched(db, user, sent):
    db.expire(user)
    fresh = db.query(models.WebUser).filter(models.WebUser.id == user.id).first()
    assert fresh.points == 321
    assert not hasattr(fresh, "is_welfare")
    assert sent == []


def test_leave_one_group_still_member_elsewhere_noop(db, sent, monkeypatch):
    user = _user(db, USER_TG)
    _stub_members(monkeypatch, {GROUP_A: (False, "left"), GROUP_B: (True, "member")})
    router._handle_chat_member(db, _cfg(f"{GROUP_A},{GROUP_B}"), _leave_event(USER_TG, GROUP_A))
    _assert_untouched(db, user, sent)


def test_leave_all_groups_noop(db, sent, monkeypatch):
    user = _user(db, USER_TG)
    _stub_members(monkeypatch, {GROUP_A: (False, "left"), GROUP_B: (False, "left")})
    router._handle_chat_member(db, _cfg(f"{GROUP_A},{GROUP_B}"), _leave_event(USER_TG, GROUP_A))
    _assert_untouched(db, user, sent)


def test_leave_other_group_api_error_noop(db, sent, monkeypatch):
    user = _user(db, USER_TG)
    _stub_members(monkeypatch, {GROUP_A: (False, "left"), GROUP_B: (None, "api_error")})
    router._handle_chat_member(db, _cfg(f"{GROUP_A},{GROUP_B}"), _leave_event(USER_TG, GROUP_A))
    _assert_untouched(db, user, sent)


def test_leave_single_group_noop(db, sent, monkeypatch):
    user = _user(db, USER_TG)
    _stub_members(monkeypatch, {GROUP_A: (False, "left")})
    router._handle_chat_member(db, _cfg(f"{GROUP_A}"), _leave_event(USER_TG, GROUP_A))
    _assert_untouched(db, user, sent)
