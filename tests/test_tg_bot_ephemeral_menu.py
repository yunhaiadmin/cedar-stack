# -*- coding: utf-8 -*-
"""TG Bot 临时消息包1 PR-2：群菜单个人结果临时回复（收口后）。

口径：
- 群菜单 points 个人结果临时回复已随 /points 命令与 menu:points
  动作删除整条摘除（callbacks._EPHEMERAL_RESULT_ACTIONS 现为空），
  其专测用例一并删除。
- 本文件现只钉：群里其余菜单动作（如 menu:main）不走临时回复、
  照常编辑共享面板。
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


def _make_user(db):
    u = models.WebUser(username="menutest", telegram_id=TG_ID)
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


def test_group_other_action_not_ephemeral(db, wire):
    _make_user(db)
    callbacks.handle_callback(db, _cq("main"))
    assert wire["eph"] == []
    assert len(wire["edits"]) == 1
