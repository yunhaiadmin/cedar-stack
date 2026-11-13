# -*- coding: utf-8 -*-
"""TG Bot 临时消息包1 PR-2：群菜单 points 个人结果临时回复。

口径（计划 plan-20261110 包1 · 1c 定案）：
- 群里点菜单的能量（menu:points；并入口 menu:chatpoints 已随
  货币删除摘除，2026-11-13，其用例一并删除）是点击者本人的结果，
  改走按钮回调临时回复
  （sendMessage + ephemeral_message_parameters.callback_query_id），
  只给点击者看，不再编辑进群共享面板（人人可见）。
- 官方要求：临时回复仍必须照常 answerCallbackQuery 回执（现行
  _handle_menu 开头已先回执，顺序即「先回执再发临时消息」），且须
  在回调后 15 秒内发送。
- 临时结果卡只读、不带按钮：导航仍回共享面板点（临时消息里放
  menu:* 按钮，点了会落到不可编辑的消息上）。发送失败回落现行
  共享面板编辑路径，结果绝不丢。
- 私聊路径与群里其余菜单项逐字不变。
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

from backend import models
from backend.integrations import store
from backend.tg_bot import callbacks, ephemeral, sender

TG_ID = 800001
GROUP = -100555


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        models.WebUser.__table__,
        models.SystemConfig.__table__,
        models.CheckinRecord.__table__,
        models.PointsLog.__table__,
        models.VitalityLog.__table__,
    ):
        table.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        store.invalidate()


def _make_user(db, points=100):
    u = models.WebUser(username="menutest", telegram_id=TG_ID, points=points)
    u.password_hash = ""
    db.add(u)
    db.commit()
    return u


def _cq(action, chat_id=GROUP, cq_id="cq-menu-1"):
    return {
        "id": cq_id,
        "from": {"id": TG_ID, "first_name": "Menu"},
        "data": f"menu:{action}",
        "message": {"message_id": 55, "chat": {"id": chat_id}},
    }


@pytest.fixture()
def wire(monkeypatch):
    """打桩发送层：记录回执 / 临时发送 / 面板编辑三条路径。"""
    rec = {"answered": [], "eph": [], "edits": [], "rearmed": []}

    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False:
            rec["answered"].append(cq_id) or (True, None))

    def fake_eph(db_, chat_id, text, **kw):
        rec["eph"].append((chat_id, text, kw))
        return (True, None, 777)

    monkeypatch.setattr(sender, "send_ephemeral_message", fake_eph)
    monkeypatch.setattr(
        sender, "edit_panel",
        lambda db_, cid, mid, text, markup=None, **kw:
            rec["edits"].append((cid, mid, text)) or (True, None))
    monkeypatch.setattr(
        ephemeral, "rearm_delete",
        lambda db_, cid, mid: rec["rearmed"].append((cid, mid)) or True)
    return rec


def test_group_points_result_goes_ephemeral(db, wire):
    _make_user(db, points=250)
    callbacks.handle_callback(db, _cq("points"))
    assert wire["answered"] == ["cq-menu-1"]
    assert len(wire["eph"]) == 1
    _chat_id, text, kw = wire["eph"][0]
    assert kw.get("callback_query_id") == "cq-menu-1"
    assert "✨ <b>能量余额</b> | 250" in text
    assert wire["edits"] == []


def test_group_ephemeral_failure_falls_back_to_panel_edit(db, wire, monkeypatch):
    _make_user(db)
    monkeypatch.setattr(
        sender, "send_ephemeral_message",
        lambda db_, cid, text, **kw: (False, "api down", None))
    callbacks.handle_callback(db, _cq("points"))
    # 回落现行共享面板编辑：结果照常落地＋群面板续命口径不变
    assert len(wire["edits"]) == 1
    assert "能量余额" in wire["edits"][0][2]
    assert wire["rearmed"] == [(GROUP, 55)]


def test_private_points_unchanged(db, wire):
    _make_user(db)
    callbacks.handle_callback(db, _cq("points", chat_id=TG_ID))
    assert wire["eph"] == []
    assert len(wire["edits"]) == 1
    assert "能量余额" in wire["edits"][0][2]


def test_group_other_action_not_ephemeral(db, wire):
    _make_user(db)
    callbacks.handle_callback(db, _cq("main"))
    assert wire["eph"] == []
    assert len(wire["edits"]) == 1
