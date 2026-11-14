# -*- coding: utf-8 -*-
"""TG Bot 群-号一致性对账 /reconcile（embyboss 借鉴第 3 项）。

口径：
- 仅私聊、仅网站管理员（is_staff 现行判定，与 handle_admin 同一口径）；
  群里发命令由 router private_only 拦 + handler 层私聊 guard 双保险。
- 只报告、零处置：报告阶段不写任何行、不改任何字段。原唯一的处置
  （取消公益资格）已随福利服役族于 2026-11-14 删除，报告卡不再带
  处置按钮；存量报告卡上的 reconcile:ask / reconcile:do 回调一律
  静默忽略。
- 覆盖必须如实：Telegram Bot API 没有群成员枚举接口，「在群未绑号」
  不可全量统计；分类只覆盖已绑号用户且逐一 getChatMember 核验，
  接口失败（api_error）单独计「未核验」，绝不计入「不在群」。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.integrations import store
from backend.tg_bot import ephemeral, handlers, reconcile, router, sender

STAFF_TG = 700100
GROUP_ID = -100777

# tg_id -> (is_member, status)：模拟 getChatMember 结果
MEMBER_MAP = {
    STAFF_TG: (True, "administrator"),
    700101: (True, "member"),        # alice：在群
    700102: (False, "left"),         # bob：不在群、无资格 → ②
    700103: (False, "kicked"),       # carol：不在群、有资格 → ③
    700104: (None, "api_error"),     # dave：接口失败 → 未核验
}


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
    router._user_cmd_limits.clear()
    router._group_windows.clear()
    reconcile.clear_cache()
    yield session
    session.close()
    store.invalidate()


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(
        sender, "send_message",
        lambda db_, chat_id, text, reply_markup=None, **kw: out.append(text) or (True, None),
    )
    monkeypatch.setattr(
        sender, "send_message_ex",
        lambda db_, chat_id, text, reply_markup=None, **kw: out.append(text) or (True, None, 9001),
    )
    monkeypatch.setattr(ephemeral, "schedule_delete", lambda *a, **kw: True)
    return out


@pytest.fixture()
def edited(monkeypatch):
    out = []

    def _edit(db_, chat_id, message_id, text, reply_markup=None, **kw):
        out.append({"chat_id": chat_id, "message_id": message_id,
                    "text": text, "markup": reply_markup})
        return True, None

    monkeypatch.setattr(sender, "edit_panel", _edit)
    monkeypatch.setattr(sender, "answer_callback_query",
                        lambda db_, cq_id, text=None, show_alert=False: (True, None))
    return out


@pytest.fixture()
def member(monkeypatch):
    calls = []
    state = dict(MEMBER_MAP)

    def _check(db_, chat_id, user_id):
        calls.append((chat_id, user_id))
        return state.get(int(user_id), (False, "not_found"))

    monkeypatch.setattr(sender, "get_chat_member", _check)
    return {"calls": calls, "state": state}


def _user(db, username, tg_id=None, staff=False, **kw):
    u = models.WebUser(username=username, telegram_id=tg_id)
    u.password_hash = ""
    u.is_staff = staff
    # 生产 staff 必有角色；对账处置是写操作，测试管理员默认 operator，
    # kw 显式传 admin_role 时以 kw 为准。
    if staff:
        u.admin_role = "operator"
    for k, v in kw.items():
        setattr(u, k, v)
    db.add(u)
    db.commit()
    return u


def _seed(db, with_group=True):
    if with_group:
        db.add(models.SystemConfig(key="bot_group_ids", value=str(GROUP_ID)))
    admin = _user(db, "boss", STAFF_TG, staff=True)
    _user(db, "alice", 700101)
    _user(db, "bob", 700102)
    _user(db, "carol", 700103)
    _user(db, "dave", 700104)
    _user(db, "erin", None)  # 未绑 TG：不在对账总体内
    db.commit()
    return admin


def _snapshot(db):
    users = {
        u.id: (u.username,)
        for u in db.query(models.WebUser).all()
    }
    return (
        users,
        db.query(models.AdminLog).count(),
    )


def _msg_update(tg_id, chat_id, text, chat_type="private"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": text,
            "from": {"id": tg_id, "first_name": "T"},
            "chat": {"id": chat_id, "type": chat_type},
        },
    }


def _cq_update(tg_id, data, chat_id=None):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cq-1",
            "from": {"id": tg_id, "first_name": "T"},
            "data": data,
            "message": {
                "message_id": 55,
                "chat": {"id": chat_id if chat_id is not None else tg_id,
                         "type": "private"},
            },
        },
    }


def test_classification_and_report(db, member):
    _seed(db)
    text, markup = handlers.handle_reconcile(db, {"id": STAFF_TG}, STAFF_TG, "")
    # 总体：已绑号 5 人（erin 未绑 TG 不计）
    assert "<b>已绑号用户</b> | 5" in text
    # ② 已绑但不在群：bob + carol = 2
    assert "<b>已绑但不在群</b> | 2 人" in text
    assert "bob" in text and "carol" in text
    # 公益资格分类已随福利族删除：报告里不再出现
    assert "仍有公益资格" not in text
    assert "公益资格" not in text
    # 未核验：dave = 1，且不被算进「不在群」
    assert "<b>未核验</b> | 1 人" in text
    assert "dave" in text
    # ① 不可枚举的覆盖声明必须在报告里
    assert "无法全量统计" in text
    # 报告卡不再带任何处置入口按钮
    datas = [
        b["callback_data"]
        for row in markup["inline_keyboard"] for b in row
    ]
    assert not any(d.startswith("reconcile:") for d in datas)


def test_report_only_writes_nothing(db, member):
    _seed(db)
    before = _snapshot(db)
    handlers.handle_reconcile(db, {"id": STAFF_TG}, STAFF_TG, "")
    assert _snapshot(db) == before


def test_non_staff_rejected(db, member):
    _seed(db)
    text = handlers.handle_reconcile(db, {"id": 700101}, 700101, "")
    assert "仅管理员可用" in text
    assert member["calls"] == []  # 没过门就不许触发任何核验


def test_unbound_gets_bind_guide(db, member):
    _seed(db)
    text = handlers.handle_reconcile(db, {"id": 799999}, 799999, "")
    assert "如何绑定账号" in text
    assert member["calls"] == []


def test_group_chat_not_executed(db, sent, member):
    _seed(db)
    router.dispatch(db, _msg_update(STAFF_TG, GROUP_ID, "/reconcile", "group"))
    assert any("请私聊" in t for t in sent)
    assert member["calls"] == []


def test_legacy_dispose_callbacks_ignored(db, sent, edited, member):
    _seed(db)
    before = _snapshot(db)
    router.dispatch(db, _msg_update(STAFF_TG, STAFF_TG, "/reconcile"))
    assert _snapshot(db) == before  # 报告零写入

    # 存量报告卡上的两段确认按钮：分发已删，静默忽略、零写入零编辑
    router.dispatch(db, _cq_update(STAFF_TG, "reconcile:ask"))
    router.dispatch(db, _cq_update(STAFF_TG, "reconcile:do"))
    assert _snapshot(db) == before
    assert edited == []


def test_reconcile_module_has_no_dispose_api(db, member):
    assert not hasattr(reconcile, "execute_cancel_welfare")
    assert not hasattr(reconcile, "render_confirm")
    assert not hasattr(reconcile, "render_receipt")


def test_non_staff_callback_blocked(db, edited, member):
    _seed(db)
    handlers.handle_reconcile(db, {"id": STAFF_TG}, STAFF_TG, "")
    before = _snapshot(db)
    router.dispatch(db, _cq_update(700101, "reconcile:ask"))
    router.dispatch(db, _cq_update(700101, "reconcile:do"))
    assert _snapshot(db) == before


def test_no_groups_configured(db, member):
    _seed(db, with_group=False)
    result = handlers.handle_reconcile(db, {"id": STAFF_TG}, STAFF_TG, "")
    text = result[0] if isinstance(result, tuple) else result
    assert "未配置名单群" in text
    assert member["calls"] == []


def test_switch_off_replies_closed(db, sent, member):
    _seed(db)
    db.add(models.SystemConfig(key="bot_cmd_reconcile", value="false"))
    db.commit()
    router.dispatch(db, _msg_update(STAFF_TG, STAFF_TG, "/reconcile"))
    assert any("该功能已关闭" in t for t in sent)
    assert member["calls"] == []
