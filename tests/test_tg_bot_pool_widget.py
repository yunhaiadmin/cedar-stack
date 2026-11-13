# -*- coding: utf-8 -*-
"""Bot API 10.4 PR-2b：贡献池私聊面板组件。

口径（计划 ~/workspace/goals/bot-api-10-4/hidden_files/plan-20261110.md 包2）：
- 仅私聊：发送口核对 chat_id＝本人 telegram_id；群聊 query 拒答；
  旧客户端只有 caption（须写明「点开看我的贡献池」类兜底文案）。
- 数据与 pool_digest.collect_digest_stats 同源（每日小结同一口径）；
  面板是小结的「随时可查」版，0 次也如实显示（与小结「0 次不发」不同）。
- HTML 压缩后 ≤4096 字节；chat_data ≤1024 字节；刷新防连点
  5 次/5 秒/每用户（组件内节流＋服务端滑动窗口）。
- 只读＋刷新，不引入资金路径。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.emby_server import models as em
from backend.integrations import store
from backend.tg_bot import callbacks, handlers, pool_digest, pool_widget, router, sender

SH = ZoneInfo("Asia/Shanghai")
NOW_SH = datetime(2026, 11, 10, 21, 0, tzinfo=SH)
TG = 920001


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    for tbl in (
        models.WebUser.__table__,
        models.SystemConfig.__table__,
        em.PoolMember.__table__,
        em.PoolPlayEvent.__table__,
    ):
        tbl.create(engine, checkfirst=True)
    store.invalidate()
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db, tg=TG, name="alice"):
    u = models.WebUser(username=name, password_hash="x",
                       telegram_id=tg, is_active=True)
    db.add(u)
    db.commit()
    return u


_member_seq = [2000]


def _member(db, user_id, status="active"):
    _member_seq[0] += 1
    m = em.PoolMember(binding_id=_member_seq[0], user_id=user_id,
                      provider="drive", status=status)
    db.add(m)
    db.commit()
    return m


def _play(db, member_id, viewer_id, at_sh):
    """在上海时间 at_sh 落一条池播出事件（存储帧是 UTC naive）。"""
    at_utc = at_sh.astimezone(timezone.utc).replace(tzinfo=None)
    ev = em.PoolPlayEvent(member_id=member_id, variant_id=1, item_id=1,
                          viewer_user_id=viewer_id, created_at=at_utc)
    db.add(ev)
    db.commit()
    return ev


class _Calls:
    """记录 sender widget 三件套＋回调 toast 的打桩。"""

    def __init__(self):
        self.answers = []   # (widget_query_id, data)
        self.edits = []     # payload
        self.widgets = []   # payload
        self.toasts = []    # (cb_id, text, show_alert)

    def patch(self, monkeypatch):
        monkeypatch.setattr(
            sender, "answer_widget_query",
            lambda db_, qid, data=None:
                self.answers.append((qid, data)) or (True, None))
        monkeypatch.setattr(
            sender, "edit_message_widget_data",
            lambda db_, chat_id=None, message_id=None, **kw:
                self.edits.append(
                    {"chat_id": chat_id, "message_id": message_id, **kw}
                ) or (True, None))
        monkeypatch.setattr(
            sender, "send_widget",
            lambda db_, chat_id, html, **kw:
                self.widgets.append(
                    {"chat_id": chat_id, "html": html, **kw}
                ) or (True, None, 4242))
        monkeypatch.setattr(
            sender, "answer_callback_query",
            lambda db_, cb_id, text=None, show_alert=False:
                self.toasts.append((cb_id, text, show_alert)) or (True, None))


@pytest.fixture(autouse=True)
def _clear_refresh_state():
    # 刷新限流是进程内按 WebUser.id 计的窗口；各用例用独立库但 id
    # 会重叠（都是 1…），用例间必须清窗，否则前一个用例的命中会
    # 污染后一个用例的计数。
    pool_widget._refresh_hits.clear()
    yield
    pool_widget._refresh_hits.clear()


def _wq(query_id="wq-1", tg=TG, chat=None,
        data='{"a":"pool_refresh"}', message_id=4242):
    return {
        "id": query_id,
        "from": {"id": tg, "first_name": "Alice"},
        "chat": chat or {"id": tg, "type": "private"},
        "message_id": message_id,
        "chat_instance": "901234567890123456",
        "data": data,
    }


# ---------- 资产与数据口径 ----------

def test_widget_html_stats_limits():
    stats = pool_widget.widget_html_stats()
    assert not stats["has_nul"]
    assert stats["raw_bytes"] <= pool_widget.WIDGET_HTML_MAX_BYTES
    assert stats["compressed_bytes"] <= 4096  # 硬约束：压缩后 ≤4096 字节


def test_chat_data_json_size():
    big = {"active": 99, "pending": 99, "suspended": 99,
           "viewers": 9999, "plays": 99999, "month": 999999}
    data = pool_widget.chat_data_json(big)
    assert len(data.encode()) <= 1024


def test_collect_user_summary_same_source_as_digest(db):
    u = _user(db)
    m1 = _member(db, u.id, "active")
    _member(db, u.id, "pending")
    _member(db, u.id, "suspended")
    _member(db, u.id, "active")
    viewer = _user(db, tg=920002, name="bob")
    # 今天 2 次（两位朋友各 1 次，本月再补 3 次）
    _play(db, m1.id, viewer.id,
          datetime(2026, 11, 10, 10, 0, tzinfo=SH))
    _play(db, m1.id, viewer.id,
          datetime(2026, 11, 10, 11, 0, tzinfo=SH))
    for day in (1, 2, 3):
        _play(db, m1.id, viewer.id,
              datetime(2026, 11, day, 10, 0, tzinfo=SH))
    # 自己看自己盘不算
    _play(db, m1.id, u.id, datetime(2026, 11, 10, 12, 0, tzinfo=SH))
    digest = pool_digest.collect_digest_stats(db, NOW_SH)
    summary = pool_widget.collect_user_summary(db, u.id, NOW_SH)
    assert summary["active"] == 2
    assert summary["pending"] == 1
    assert summary["suspended"] == 1
    assert summary["viewers"] == digest[u.id]["viewers"] == 1
    assert summary["plays"] == digest[u.id]["plays"] == 2
    assert summary["month"] == digest[u.id]["month_plays"] == 5


def test_collect_user_summary_zero_plays_still_shows(db):
    u = _user(db)
    _member(db, u.id, "active")
    summary = pool_widget.collect_user_summary(db, u.id, NOW_SH)
    assert summary == {"active": 1, "pending": 0, "suspended": 0,
                       "viewers": 0, "plays": 0, "month": 0}


# ---------- 发送口：私聊核对＋成员门槛 ----------

def test_send_requires_member(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    u = _user(db)
    ok, err, mid = pool_widget.send_pool_widget(db, TG, u)
    assert not ok and "还没有加入" in (err or "") and mid is None
    assert calls.widgets == []  # 非成员不发


def test_send_rejects_non_self_chat(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    u = _user(db)
    _member(db, u.id, "active")
    ok, err, mid = pool_widget.send_pool_widget(db, 999999, u)
    assert not ok and mid is None
    assert calls.widgets == []  # chat_id 非本人不发


def test_send_happy_path(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    u = _user(db)
    _member(db, u.id, "active")
    ok, err, mid = pool_widget.send_pool_widget(db, TG, u)
    assert ok and err is None and mid == 4242
    assert len(calls.widgets) == 1
    w = calls.widgets[0]
    assert w["chat_id"] == TG
    assert w["allow_queries"] is True
    assert "点开" in (w.get("caption") or "")  # 旧客户端兜底文案
    chat_data = json.loads(w["chat_data"])
    assert chat_data["active"] == 1
    assert len(w["chat_data"].encode()) <= 1024


def test_drive_group_has_pool_button():
    buttons = handlers.USER_PANEL_GROUPS["drive"]["buttons"]
    found = [b for b in buttons if b["callback_data"] == "pool_widget"]
    assert len(found) == 1
    assert "贡献池" in found[0]["text"]


# ---------- refresh 应答 ----------

def test_refresh_happy_path(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    # handle_widget_query 内部用真实上海时间取数；测试把「现在」
    # 钉在 NOW_SH（2026-11-10 21:00），种子播放才算「今天」。
    monkeypatch.setattr(pool_digest, "_now_shanghai", lambda: NOW_SH)
    u = _user(db, tg=920101)
    m = _member(db, u.id, "active")
    viewer = _user(db, tg=920102, name="bob")
    _play(db, m.id, viewer.id, datetime(2026, 11, 10, 10, 0, tzinfo=SH))
    pool_widget.handle_widget_query(
        db, _wq(query_id="wq-r1", tg=920101))
    assert len(calls.answers) == 1
    qid, data = calls.answers[0]
    assert qid == "wq-r1"
    payload = json.loads(data)
    assert payload["active"] == 1 and payload["plays"] == 1
    assert payload["viewers"] == 1 and payload["month"] == 1
    assert len(data.encode()) <= 1024
    assert len(calls.edits) == 1  # 刷新后更新 widget 的 chatData
    assert json.loads(calls.edits[0]["chat_data"])["plays"] == 1


def test_refresh_private_only(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    pool_widget.handle_widget_query(
        db, _wq(chat={"id": -100123, "type": "supergroup"}))
    assert len(calls.answers) == 1
    assert json.loads(calls.answers[0][1])["error"] == "private_only"


def test_refresh_unbound(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    pool_widget.handle_widget_query(db, _wq(tg=999999))
    assert len(calls.answers) == 1
    assert json.loads(calls.answers[0][1])["error"] == "unbound"


def test_refresh_bad_action(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    pool_widget.handle_widget_query(
        db, _wq(data='{"a":"bogus"}'))
    assert len(calls.answers) == 1
    assert json.loads(calls.answers[0][1])["error"] == "bad_request"


def test_refresh_rate_limit(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    u = _user(db, tg=920201)
    _member(db, u.id, "active")
    clock = {"t": 1000.0}
    monkeypatch.setattr(pool_widget, "_now", lambda: clock["t"])
    for i in range(5):
        pool_widget.handle_widget_query(
            db, _wq(query_id=f"wq-l{i}", tg=920201))
    assert len(calls.answers) == 5
    assert all("error" not in json.loads(d) for _q, d in calls.answers)
    pool_widget.handle_widget_query(
        db, _wq(query_id="wq-l5", tg=920201))
    assert len(calls.answers) == 6
    assert json.loads(calls.answers[5][1])["error"] == "rate_limited"
    clock["t"] += 5.0  # 窗口滑过 → 恢复
    pool_widget.handle_widget_query(
        db, _wq(query_id="wq-l6", tg=920201))
    assert "error" not in json.loads(calls.answers[6][1])


# ---------- 分发器路由＋入口回调 ----------

def test_dispatcher_pool_refresh_goes_to_pool(db, monkeypatch):
    seen = []
    monkeypatch.setattr(
        pool_widget, "handle_widget_query",
        lambda db_, q: seen.append(q))
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    router._dispatch_widget_query(db, _wq(data='{"a":"pool_refresh"}'))
    assert len(seen) == 1 and seen[0]["id"] == "wq-1"
    assert answers == []  # pool 由组件自己应答，分发器不插手


def _cb(cb_id="cb-1", tg=TG, chat_id=TG, data="pool_widget"):
    return {
        "id": cb_id,
        "from": {"id": tg, "first_name": "Alice"},
        "message": {"message_id": 55, "chat": {"id": chat_id}},
        "data": data,
    }


def test_entry_callback_group_rejected(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb(chat_id=-100123))
    assert len(calls.toasts) == 1
    assert "私聊" in (calls.toasts[0][1] or "")
    assert calls.widgets == []


def test_entry_callback_unbound(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    # chat_id 须等于 tg（私聊本人），否则先撞私聊门槛、测不到未绑定
    callbacks.handle_callback(db, _cb(tg=999999, chat_id=999999))
    assert len(calls.toasts) == 1
    assert "绑定" in (calls.toasts[0][1] or "")
    assert calls.widgets == []


def test_entry_callback_non_member(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    _user(db)
    callbacks.handle_callback(db, _cb())
    assert len(calls.toasts) == 1
    assert "还没有加入" in (calls.toasts[0][1] or "")
    assert calls.widgets == []


def test_entry_callback_happy(db, monkeypatch):
    calls = _Calls()
    calls.patch(monkeypatch)
    u = _user(db)
    _member(db, u.id, "active")
    callbacks.handle_callback(db, _cb())
    assert len(calls.widgets) == 1
    assert len(calls.toasts) == 1
    assert "已发出" in (calls.toasts[0][1] or "")
