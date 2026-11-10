# -*- coding: utf-8 -*-
"""TG Bot 临时消息（Bot API 10.2+/10.4 ephemeral）包1：/kk 群卡临时化。

口径（计划 plan-20261110 包1定案 + 官方文档核对）：
- sender 收成一套 wrapper：send_ephemeral_message /
  edit_ephemeral_message_text / delete_ephemeral_message，参数组装
  ephemeral_message_parameters（只放非空字段，replace 仅 True 时放）。
- 临时消息标识字段以官方 API 参考为准：发送结果 Message 与
  callback_query.message 上的 ephemeral_message_id（10.2 新增字段）。
- /kk 群路径定位到具体人时，面板首卡返回 ("__ephemeral__", text,
  markup, receiver_tg_id) 标记，receiver=发起命令的管理员本人；
  拒绝卡/绑定指引/用法/查无此人等无个人数据的卡维持现行公开路径。
- router 识别标记后只发给管理员本人；发送失败必须如实回落现行
  公开路径（发群里＋自毁口径不变），绝不静默丢卡。
- whois:* 回调若来自临时消息（message 带 ephemeral_message_id，
  或消息无 message_id 时查进程内登记兜底），翻页/编辑走
  editEphemeralMessageText 三元组；普通消息（带 message_id）永远
  走现行 editMessageText，登记兜底不许劫持普通面板。
- 临时消息面板不许带 login_url / web_app 按钮（官方限制）。
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
from backend.emby_server import models as em
from backend.integrations import store
from backend.tg_bot import callbacks, ephemeral, handlers, router, sender

STAFF_TG = 700001
PLAIN_TG = 700002
TARGET_TG = 700003
GROUP = -100123


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    ephemeral._eph_timers.clear()
    ephemeral._eph_latest.clear()
    router._user_cmd_limits.clear()
    router._group_windows.clear()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        store.invalidate()
        ephemeral._eph_timers.clear()
        ephemeral._eph_latest.clear()


def _user(db, tg, name, staff=False, **kw):
    fields = {"password_hash": "x", "telegram_id": tg, "points": 10,
              "vitality": 14, "is_active": True, "is_staff": staff}
    if staff and "admin_role" not in kw:
        fields["admin_role"] = "operator"
    fields.update(kw)
    u = models.WebUser(username=name, **fields)
    db.add(u)
    db.commit()
    return u


def _staff_target(db):
    staff = _user(db, STAFF_TG, "boss", staff=True)
    target = _user(db, TARGET_TG, "targetx")
    return staff, target


def _all_buttons(markup):
    for row in (markup or {}).get("inline_keyboard", []):
        for b in row:
            yield b


class _FakeTimer:
    """与 test_tg_bot_ephemeral 同款 FakeTimer（args/kwargs 接口面）。"""

    def __init__(self, timers, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        self.cancelled = False
        timers.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args, **self.kwargs)


@pytest.fixture()
def timers(monkeypatch):
    made = []
    monkeypatch.setattr(
        ephemeral, "Timer",
        lambda interval, fn, args=None, kwargs=None:
            _FakeTimer(made, interval, fn, args, kwargs))
    return made


# ---------- 1. sender wrapper：payload 形状 ----------

def _capture_call_ex(monkeypatch, result):
    seen = {}

    def fake_call_ex(db_, method, payload):
        seen["method"] = method
        seen["payload"] = payload
        return True, None, result

    monkeypatch.setattr(sender, "_call_ex", fake_call_ex)
    return seen


def test_send_ephemeral_receiver_only_payload(db, monkeypatch):
    seen = _capture_call_ex(monkeypatch, {
        "message_id": 9001, "ephemeral_message_id": 777,
        "chat": {"id": GROUP}})
    ok, err, eph_id = sender.send_ephemeral_message(
        db, GROUP, "卡", receiver_user_id=STAFF_TG)
    assert (ok, err, eph_id) == (True, None, 777)
    assert seen["method"] == "sendMessage"
    payload = seen["payload"]
    assert payload["chat_id"] == GROUP
    assert payload["text"] == "卡"
    # 指定接收人形态：只有 receiver_user_id，不带回调与 replace 键
    assert payload["ephemeral_message_parameters"] == {
        "receiver_user_id": STAFF_TG}


def test_send_ephemeral_callback_only_payload(db, monkeypatch):
    seen = _capture_call_ex(monkeypatch, {"ephemeral_message_id": 555})
    ok, err, eph_id = sender.send_ephemeral_message(
        db, GROUP, "结果", callback_query_id="cq-9")
    assert (ok, err, eph_id) == (True, None, 555)
    assert seen["payload"]["ephemeral_message_parameters"] == {
        "callback_query_id": "cq-9"}


def test_send_ephemeral_replace_flag_only_when_true(db, monkeypatch):
    seen = _capture_call_ex(monkeypatch, {"ephemeral_message_id": 556})
    sender.send_ephemeral_message(
        db, GROUP, "结果", callback_query_id="cq-9",
        replace_callback_query_message=True)
    assert seen["payload"]["ephemeral_message_parameters"] == {
        "callback_query_id": "cq-9", "replace_callback_query_message": True}


def test_send_ephemeral_reply_markup_passthrough(db, monkeypatch):
    seen = _capture_call_ex(monkeypatch, {"ephemeral_message_id": 557})
    markup = {"inline_keyboard": [[{"text": "x", "callback_data": "whois:main:1"}]]}
    sender.send_ephemeral_message(
        db, GROUP, "卡", receiver_user_id=STAFF_TG, reply_markup=markup)
    assert seen["payload"]["reply_markup"] == markup


def test_send_ephemeral_without_receiver_or_callback_refused(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sender, "_call_ex",
        lambda db_, m, p: calls.append((m, p)) or (True, None, {}))
    ok, err, eph_id = sender.send_ephemeral_message(db, GROUP, "卡")
    assert ok is False and err and eph_id is None
    assert calls == []  # 本地拒绝，绝不把无限定请求当普通消息发出去


def test_send_ephemeral_api_failure_propagates(db, monkeypatch):
    monkeypatch.setattr(
        sender, "_call_ex",
        lambda db_, m, p: (False, "网络请求失败", None))
    ok, err, eph_id = sender.send_ephemeral_message(
        db, GROUP, "卡", receiver_user_id=STAFF_TG)
    assert (ok, err, eph_id) == (False, "网络请求失败", None)


def test_send_ephemeral_result_without_id_returns_none(db, monkeypatch):
    _capture_call_ex(monkeypatch, {"message_id": 9002})
    ok, err, eph_id = sender.send_ephemeral_message(
        db, GROUP, "卡", receiver_user_id=STAFF_TG)
    assert ok is True and err is None and eph_id is None


def test_edit_ephemeral_payload(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        sender, "_call",
        lambda db_, m, p: seen.update(method=m, payload=p) or (True, None))
    markup = {"inline_keyboard": []}
    ok, err = sender.edit_ephemeral_message_text(
        db, GROUP, STAFF_TG, 777, "新页", reply_markup=markup)
    assert (ok, err) == (True, None)
    assert seen["method"] == "editEphemeralMessageText"
    assert seen["payload"]["chat_id"] == GROUP
    assert seen["payload"]["receiver_user_id"] == STAFF_TG
    assert seen["payload"]["ephemeral_message_id"] == 777
    assert seen["payload"]["text"] == "新页"
    assert seen["payload"]["reply_markup"] == markup


def test_delete_ephemeral_payload(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        sender, "_call",
        lambda db_, m, p: seen.update(method=m, payload=p) or (True, None))
    ok, err = sender.delete_ephemeral_message(db, GROUP, STAFF_TG, 777)
    assert (ok, err) == (True, None)
    assert seen["method"] == "deleteEphemeralMessage"
    assert seen["payload"] == {
        "chat_id": GROUP, "receiver_user_id": STAFF_TG,
        "ephemeral_message_id": 777}


# ---------- 2. ephemeral 调度：临时消息删除定时与登记 ----------

def test_schedule_ephemeral_delete_timer_and_fire(db, monkeypatch, timers):
    class DummySession:
        def close(self):
            pass

    monkeypatch.setattr("backend.database.SessionLocal", lambda: DummySession())
    deleted = []

    def fake_delete(db_, chat_id, receiver_user_id, eph_id):
        deleted.append((chat_id, receiver_user_id, eph_id))
        return True, None

    monkeypatch.setattr(sender, "delete_ephemeral_message", fake_delete)

    assert ephemeral.schedule_ephemeral_delete(GROUP, STAFF_TG, 777, 30) is True
    assert len(timers) == 1
    assert timers[0].interval == 30
    assert timers[0].args == (GROUP, STAFF_TG, 777)
    # 快照纪律：到期调调度时快照的删除函数
    assert timers[0].kwargs["delete_ephemeral_message"] is fake_delete
    assert timers[0].started is True

    timers[0].fire()
    assert deleted == [(GROUP, STAFF_TG, 777)]
    assert (GROUP, STAFF_TG, 777) not in ephemeral._eph_timers


def test_schedule_ephemeral_delete_guards(timers):
    assert ephemeral.schedule_ephemeral_delete(GROUP, STAFF_TG, 777, 0) is False
    assert ephemeral.schedule_ephemeral_delete(GROUP, None, 777, 30) is False
    assert ephemeral.schedule_ephemeral_delete(GROUP, STAFF_TG, None, 30) is False
    assert ephemeral.schedule_ephemeral_delete(None, STAFF_TG, 777, 30) is False
    assert timers == []


def test_schedule_ephemeral_delete_replacement_cancels_old(timers):
    assert ephemeral.schedule_ephemeral_delete(GROUP, STAFF_TG, 777, 30) is True
    assert ephemeral.schedule_ephemeral_delete(GROUP, STAFF_TG, 777, 45) is True
    assert len(timers) == 2
    assert timers[0].cancelled is True
    assert timers[1].cancelled is False
    assert len(ephemeral._eph_timers) == 1
    # 取消口径：有登记 True、再取消 False
    assert ephemeral.cancel_ephemeral_delete(GROUP, STAFF_TG, 777) is True
    assert timers[1].cancelled is True
    assert ephemeral.cancel_ephemeral_delete(GROUP, STAFF_TG, 777) is False


def test_rearm_ephemeral_delete_uses_config(db, timers):
    db.add(models.SystemConfig(
        key="bot_msg_autodelete_seconds", value="45"))
    db.commit()
    store.invalidate()
    assert ephemeral.rearm_ephemeral_delete(db, GROUP, STAFF_TG, 777) is True
    assert timers[-1].interval == 45

    # 配置改 0（关闭）：只取消不重挂
    row = db.query(models.SystemConfig).filter_by(
        key="bot_msg_autodelete_seconds").one()
    row.value = "0"
    db.commit()
    store.invalidate()
    assert ephemeral.rearm_ephemeral_delete(db, GROUP, STAFF_TG, 777) is False
    assert ephemeral._eph_timers == {}


def test_ephemeral_registry_note_and_lookup():
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) is None
    ephemeral.note_ephemeral_message(GROUP, STAFF_TG, 777)
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) == 777
    # 新面板顶替旧登记
    ephemeral.note_ephemeral_message(GROUP, STAFF_TG, 888)
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) == 888
    # 缺标识不登记
    ephemeral.note_ephemeral_message(GROUP, STAFF_TG, None)
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) == 888
    assert ephemeral.ephemeral_message_for(GROUP, None) is None


# ---------- 3. handlers：/kk 群路径标记 ----------

def test_kk_group_panel_returns_ephemeral_marker(db):
    _staff, target = _staff_target(db)
    reply = handlers.handle_whois(
        db, {"id": STAFF_TG}, GROUP, "targetx", in_group=True)
    assert isinstance(reply, tuple) and len(reply) == 4
    marker, text, markup, receiver = reply
    assert marker == "__ephemeral__"
    assert receiver == STAFF_TG  # 只发给发起命令的管理员本人
    assert "targetx" in text
    datas = [b.get("callback_data") for b in _all_buttons(markup)]
    assert f"whois:bind:{target.id}" in datas
    # 官方限制：临时消息面板不许带 login_url / web_app 按钮
    for b in _all_buttons(markup):
        assert "login_url" not in b
        assert "web_app" not in b


def test_kk_group_reply_lookup_returns_marker(db):
    _staff, target = _staff_target(db)
    reply = handlers.handle_whois(
        db, {"id": STAFF_TG}, GROUP, "", in_group=True,
        reply_from={"id": TARGET_TG, "first_name": "小明"})
    assert isinstance(reply, tuple) and reply[0] == "__ephemeral__"
    assert reply[3] == STAFF_TG
    assert "targetx" in reply[1]


def test_kk_group_refusals_stay_plain(db):
    _staff_target(db)
    _user(db, PLAIN_TG, "plainuser")
    # 非管理员：现行拒绝卡（str，不走标记）
    reply = handlers.handle_whois(
        db, {"id": PLAIN_TG}, GROUP, "targetx", in_group=True)
    assert isinstance(reply, str) and "仅管理员可用" in reply
    # 未绑定调用者：绑定指引（str）
    reply = handlers.handle_whois(
        db, {"id": 799999}, GROUP, "targetx", in_group=True)
    assert isinstance(reply, str) and "如何绑定" in reply
    # 查无此人 / 无参用法：明示卡（str）
    reply = handlers.handle_whois(
        db, {"id": STAFF_TG}, GROUP, "zzz_no_one", in_group=True)
    assert isinstance(reply, str) and "没查到" in reply
    reply = handlers.handle_whois(
        db, {"id": STAFF_TG}, GROUP, "", in_group=True)
    assert isinstance(reply, str) and "用法" in reply


def test_kk_private_panel_unchanged(db):
    _staff, target = _staff_target(db)
    reply = handlers.handle_whois(db, {"id": STAFF_TG}, STAFF_TG, "targetx")
    assert isinstance(reply, tuple) and len(reply) == 2
    assert "targetx" in reply[0]


# ---------- 4. router：标记 → 只发管理员 / 失败回落 ----------

def _group_update(text, tg_id=STAFF_TG):
    return {"update_id": 3, "message": {
        "message_id": 7,
        "from": {"id": tg_id, "first_name": "Boss"},
        "chat": {"id": GROUP, "type": "supergroup"},
        "text": text,
    }}


def _patch_router_senders(monkeypatch, eph_result):
    sent_eph, sent_public, scheduled = [], [], []

    def fake_eph(db_, chat_id, text, **kw):
        sent_eph.append((chat_id, text, kw))
        return eph_result

    monkeypatch.setattr(sender, "send_ephemeral_message", fake_eph)
    monkeypatch.setattr(
        sender, "send_message",
        lambda db_, cid, text, rm=None, **kw:
            sent_public.append(text) or (True, None))
    monkeypatch.setattr(
        sender, "send_message_ex",
        lambda db_, cid, text, rm=None, **kw:
            sent_public.append(text) or (True, None, 9001))
    monkeypatch.setattr(
        sender, "delete_message", lambda db_, cid, mid: (True, None))
    monkeypatch.setattr(ephemeral, "schedule_delete", lambda *a, **kw: True)
    monkeypatch.setattr(
        ephemeral, "schedule_ephemeral_delete",
        lambda *a, **kw: scheduled.append(a) or True)
    return sent_eph, sent_public, scheduled


def test_router_kk_group_sends_ephemeral_to_admin_only(db, monkeypatch):
    _staff_target(db)
    sent_eph, sent_public, scheduled = _patch_router_senders(
        monkeypatch, (True, None, 777))
    router.dispatch(db, _group_update("/kk targetx"))
    assert len(sent_eph) == 1
    chat_id, text, kw = sent_eph[0]
    assert chat_id == GROUP
    assert kw.get("receiver_user_id") == STAFF_TG
    assert "targetx" in text
    assert kw.get("reply_markup") is not None
    # 公开路径一条不发；登记与定时删除都挂上
    assert sent_public == []
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) == 777
    assert scheduled and scheduled[0][:3] == (GROUP, STAFF_TG, 777)
    assert scheduled[0][3] == 30  # 默认 bot_msg 延时


def test_router_kk_group_ephemeral_failure_falls_back_public(db, monkeypatch):
    _staff_target(db)
    sent_eph, sent_public, scheduled = _patch_router_senders(
        monkeypatch, (False, "api down", None))
    router.dispatch(db, _group_update("/kk targetx"))
    assert len(sent_eph) == 1
    # 回落现行公开路径：卡照常发到群里，绝不静默丢卡
    assert len(sent_public) == 1
    assert "targetx" in sent_public[0]
    assert ephemeral.ephemeral_message_for(GROUP, STAFF_TG) is None
    assert scheduled == []


def test_router_kk_private_never_ephemeral(db, monkeypatch):
    _staff_target(db)
    sent_eph, sent_public, _scheduled = _patch_router_senders(
        monkeypatch, (True, None, 777))
    update = {"update_id": 4, "message": {
        "message_id": 8,
        "from": {"id": STAFF_TG, "first_name": "Boss"},
        "chat": {"id": STAFF_TG, "type": "private"},
        "text": "/kk targetx",
    }}
    router.dispatch(db, update)
    assert sent_eph == []
    assert len(sent_public) == 1
    assert "targetx" in sent_public[0]


# ---------- 5. callbacks：whois 回调的临时消息分支 ----------

def _patch_whois_callback_senders(monkeypatch):
    edited, edited_eph, rearmed = [], [], []

    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False: (True, None))
    monkeypatch.setattr(
        sender, "edit_message_text",
        lambda db_, cid, mid, text, rm=None, **kw:
            edited.append((cid, mid, text)) or (True, None))
    monkeypatch.setattr(
        sender, "edit_ephemeral_message_text",
        lambda db_, cid, receiver, eph_id, text, rm=None, **kw:
            edited_eph.append((cid, receiver, eph_id, text)) or (True, None))
    monkeypatch.setattr(
        ephemeral, "rearm_ephemeral_delete",
        lambda db_, cid, receiver, eph_id:
            rearmed.append((cid, receiver, eph_id)) or True)
    monkeypatch.setattr(
        ephemeral, "rearm_delete", lambda *a, **kw: True)
    return edited, edited_eph, rearmed


def _whois_cq(target_id, message):
    return {
        "id": "cq1", "data": f"whois:bind:{target_id}",
        "from": {"id": STAFF_TG, "first_name": "Boss"},
        "message": message,
    }


def test_whois_callback_on_ephemeral_message_edits_ephemeral(db, monkeypatch):
    _staff, target = _staff_target(db)
    edited, edited_eph, rearmed = _patch_whois_callback_senders(monkeypatch)
    # 临时消息的回调 message 带 ephemeral_message_id、无普通 message_id
    cq = _whois_cq(target.id, {
        "chat": {"id": GROUP, "type": "supergroup"},
        "ephemeral_message_id": 555,
    })
    callbacks.handle_callback(db, cq)
    assert edited == []  # 绝不走普通编辑
    assert len(edited_eph) == 1
    chat_id, receiver, eph_id, _text = edited_eph[0]
    assert (chat_id, receiver, eph_id) == (GROUP, STAFF_TG, 555)
    assert rearmed == [(GROUP, STAFF_TG, 555)]


def test_whois_callback_ephemeral_registry_fallback(db, monkeypatch):
    _staff, target = _staff_target(db)
    edited, edited_eph, _rearmed = _patch_whois_callback_senders(monkeypatch)
    # message 两个 id 都没有：查进程内登记兜底
    ephemeral.note_ephemeral_message(GROUP, STAFF_TG, 999)
    cq = _whois_cq(target.id, {"chat": {"id": GROUP, "type": "supergroup"}})
    callbacks.handle_callback(db, cq)
    assert edited == []
    assert len(edited_eph) == 1
    assert edited_eph[0][2] == 999


def test_whois_callback_normal_message_not_hijacked_by_registry(db, monkeypatch):
    _staff, target = _staff_target(db)
    edited, edited_eph, _rearmed = _patch_whois_callback_senders(monkeypatch)
    # 登记里有同 (chat, 管理员) 的临时面板，也绝不许劫持普通面板点击
    ephemeral.note_ephemeral_message(GROUP, STAFF_TG, 999)
    cq = _whois_cq(target.id, {
        "message_id": 5, "chat": {"id": GROUP, "type": "supergroup"}})
    callbacks.handle_callback(db, cq)
    assert edited_eph == []
    assert len(edited) == 1
    assert edited[0][1] == 5
