# -*- coding: utf-8 -*-
"""TG 绑定码群内冒领回归测试（360° 审计 A-033）。

根因：
    router 对 6 位纯数字消息不分私聊/群聊一律走 handlers.verify_bind_code
    ——验过即把**发送者**的 TG 绑到码主的网站账号。绑定码的设计流程是
    私聊 Bot 发码（见 backend/tg_bind.py），但群里同样受理、且用户发出
    的码消息不删除：码在白名单群历史里 600 秒有效期内明文可见，码主
    本人尚未发送（或其消息被群限流丢弃）时，任何群成员抢先发送同一码
    即可把自己的 TG 绑到码主账号，进而冒领通知与 TG 一键登录。

修复：
    群里一律不验码。命中未用未过期的活码 → 当场作废（used_at 落章）
    ＋尽力删除原消息＋回自毁提示让码主重新生成并改走私聊；未命中
    （普通 6 位数字）静默忽略。私聊流程逐字不变。

全部用隔离的内存 SQLite，不碰生产库、不发真 TG 请求（sender 全桩）。
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.integrations import store
from backend.tg_bot import ephemeral, router, sender

GROUP_ID = -100777
OWNER_TG = 700001
ATTACKER_TG = 700002
CODE = "654321"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    router._user_cmd_limits.clear()
    router._group_windows.clear()
    yield session
    store.invalidate()
    session.close()
    engine.dispose()


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(
        sender, "send_message",
        lambda db_, chat_id, text, reply_markup=None, **kw: out.append(text) or (True, None))
    monkeypatch.setattr(
        sender, "send_message_ex",
        lambda db_, chat_id, text, reply_markup=None, **kw: out.append(text) or (True, None, 9001))
    monkeypatch.setattr(ephemeral, "schedule_delete", lambda *a, **kw: True)
    return out


@pytest.fixture()
def deleted(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sender, "delete_message",
        lambda db_, chat_id, message_id, **kw: calls.append((chat_id, message_id)) or True)
    return calls


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _live_code(db, owner, code=CODE):
    rec = models.TgBindCode(
        user_id=owner.id, code=code,
        expires_at=datetime.now() + timedelta(seconds=600))
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def _group_update(text, tg_id, message_id=42):
    return {"update_id": 1, "message": {
        "message_id": message_id,
        "from": {"id": tg_id, "first_name": "Poster"},
        "chat": {"id": GROUP_ID, "type": "supergroup"},
        "text": text,
    }}


def _private_update(text, tg_id, message_id=7):
    return {"update_id": 2, "message": {
        "message_id": message_id,
        "from": {"id": tg_id, "first_name": "Priv"},
        "chat": {"id": tg_id, "type": "private"},
        "text": text,
    }}


def test_group_code_is_invalidated_not_claimed(db, sent, deleted):
    """群里出现活码：作废＋删消息＋提示改走私聊，绝不给发送者绑定。"""
    owner = _user(db, "codeowner")
    rec = _live_code(db, owner)

    # 码主把码误发到群里（发送者是码主本人也一样处理：码已公开）
    router.dispatch(db, _group_update(CODE, OWNER_TG))

    db.refresh(rec)
    db.refresh(owner)
    assert rec.used_at is not None, "群里露过脸的活码必须当场作废"
    assert owner.telegram_id is None, "群里验码绝不许产生绑定"
    assert deleted == [(GROUP_ID, 42)], "应尽力删除群里的码消息"
    assert any("私聊" in t for t in sent), "应回提示让码主重新生成并私聊发送"


def test_group_exposed_code_cannot_be_claimed_privately(db, sent, deleted):
    """码在群里露脸作废后，攻击者再私聊发同一码也不能冒领。"""
    owner = _user(db, "victim")
    rec = _live_code(db, owner)

    router.dispatch(db, _group_update(CODE, OWNER_TG))
    router.dispatch(db, _private_update(CODE, ATTACKER_TG))

    db.refresh(rec)
    db.refresh(owner)
    assert owner.telegram_id is None, "已作废的码不许再绑到任何人"
    assert rec.telegram_id is None


def test_private_bind_still_works(db, sent, deleted):
    """私聊正道不变：码主私聊发码 → 绑定成功。"""
    owner = _user(db, "privateok")
    _live_code(db, owner)

    router.dispatch(db, _private_update(CODE, OWNER_TG))

    db.refresh(owner)
    assert owner.telegram_id == OWNER_TG


def test_group_random_digits_ignored(db, sent, deleted):
    """群里普通 6 位数字（无活码命中）：静默忽略，不回话不删消息。"""
    _user(db, "bystander")

    router.dispatch(db, _group_update("123456", OWNER_TG))

    assert sent == []
    assert deleted == []
