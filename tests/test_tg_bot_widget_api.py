# -*- coding: utf-8 -*-
"""Bot API 10.4 PR-2a：widget_query 分发器＋包3 sender 预埋＋包5 封装。

核对基线（官方 API 参考 core.telegram.org/bots/api，2026-11-11 亲读）：
- Widget 类型：sendWidget 返回 Message，其 widget 字段是 Widget 对象
  {id?, width?, height?, html, public_data?, chat_data?}；id 是 String，
  原文 "Unique identifier of the shared widget, which can be used to
  send the widget again using the method sendSharedWidget; omitted if
  the widget isn't shared" → widget_id 取得路径 = 返回 Message 的
  widget.id（仅 is_shared=True 创建的组件才有）。
- sendWidget：is_shared 为可选 Boolean（"Pass True to create a shared
  widget"）；sendSharedWidget：chat_id＋widget_id 均必填，可选
  chat_data/caption 等，无 html/public_data/allow_queries。
- editSharedWidgetData：widget_id 必填、public_data 必填（0–1024 字节，
  传空串清除）、cache_time 可选 5–3600（默认保持、缺省值 30）；
  共享组件编辑 ≤1 次/秒。返回 True。
- ReplyParameters.quote_rich_texts：Array of RichText，"Up to 32
  quoted texts with total length up to 1024 characters"；非空时
  quote/quote_parse_mode/quote_entities 被忽略。
- getUniqueGift：name（String，必填）→ 返回 UniqueGift 对象。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.tg_bot import gifts, router, sender, slots_widget


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _wq(query_id="wq-1", data='{"a":"spin","bet":10}'):
    return {
        "id": query_id,
        "from": {"id": 920001, "first_name": "Alice"},
        "chat": {"id": 920001, "type": "private"},
        "message_id": 4242,
        "chat_instance": "901234567890123456",
        "data": data,
    }


class _SendCapture:
    """打桩 sender._call/_call_ex，记录 (method, payload)。"""

    def __init__(self, monkeypatch, result=None):
        self.calls = []
        self.result = {"message_id": 4242} if result is None else result

        def fake_call(db_, method, payload):
            self.calls.append((method, payload))
            return True, None

        def fake_call_ex(db_, method, payload):
            self.calls.append((method, payload))
            return True, None, self.result

        monkeypatch.setattr(sender, "_call", fake_call)
        monkeypatch.setattr(sender, "_call_ex", fake_call_ex)


# ---------- 分发器：widget_query 按动作命名空间分发 ----------

def test_dispatcher_spin_goes_to_slots(db, monkeypatch):
    seen = []
    monkeypatch.setattr(
        slots_widget, "handle_widget_query",
        lambda db_, q: seen.append(q))
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    router._dispatch_widget_query(db, _wq())
    assert len(seen) == 1 and seen[0]["id"] == "wq-1"
    assert answers == []  # spin 由老虎机自己应答，分发器不插手


def test_dispatcher_unknown_action_gets_error_answer(db, monkeypatch):
    seen = []
    monkeypatch.setattr(
        slots_widget, "handle_widget_query",
        lambda db_, q: seen.append(q))
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    router._dispatch_widget_query(db, _wq(data='{"a":"pool_refresh"}'))
    assert seen == []  # 未登记动作绝不误落进老虎机
    assert len(answers) == 1
    qid, data = answers[0]
    assert qid == "wq-1"
    payload = json.loads(data)
    assert payload["error"] == "unknown_action"
    assert payload["msg"]
    assert len(data.encode()) <= 1024  # answerWidgetQuery data 上限


def test_dispatcher_malformed_data_gets_error_answer(db, monkeypatch):
    seen = []
    monkeypatch.setattr(
        slots_widget, "handle_widget_query",
        lambda db_, q: seen.append(q))
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    router._dispatch_widget_query(db, _wq(data="not json"))
    router._dispatch_widget_query(db, _wq(query_id="wq-2", data='{"bet":5}'))
    assert seen == []
    assert len(answers) == 2
    for _qid, data in answers:
        assert json.loads(data)["error"] == "unknown_action"


def test_dispatcher_missing_id_never_silent_crash(db, monkeypatch):
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    wq = _wq(data='{"a":"nope"}')
    del wq["id"]
    router._dispatch_widget_query(db, wq)  # 无 id 无从应答，只记日志不抛
    assert answers == []


def test_router_dispatch_unknown_widget_action(db, monkeypatch):
    answers = []
    monkeypatch.setattr(
        sender, "answer_widget_query",
        lambda db_, qid, data=None: answers.append((qid, data)) or (True, None))
    router.dispatch(db, {
        "update_id": 7,
        "widget_query": _wq(data='{"a":"pool_refresh"}')})
    assert len(answers) == 1
    assert json.loads(answers[0][1])["error"] == "unknown_action"


# ---------- 包3：send_widget is_shared 透传 ----------

def test_send_widget_default_payload_has_no_is_shared(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, mid = sender.send_widget(None, 123, "<p>x</p>")
    assert ok and mid == 4242
    method, payload = cap.calls[0]
    assert method == "sendWidget"
    assert "is_shared" not in payload  # 默认不传，老虎机调用逐字不变


def test_send_widget_is_shared_passthrough(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, mid = sender.send_widget(
        None, 123, "<p>x</p>", is_shared=True)
    assert ok and mid == 4242
    method, payload = cap.calls[0]
    assert method == "sendWidget"
    assert payload["is_shared"] is True


# ---------- 包3：send_shared_widget ----------

def test_send_shared_widget_payload(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, mid = sender.send_shared_widget(
        None, 123, "WID-9", chat_data='{"balance":5}', caption="cap")
    assert ok and err is None and mid == 4242
    method, payload = cap.calls[0]
    assert method == "sendSharedWidget"
    assert payload == {
        "chat_id": 123, "widget_id": "WID-9",
        "chat_data": '{"balance":5}', "caption": "cap"}


def test_send_shared_widget_minimal_payload(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, mid = sender.send_shared_widget(None, 123, "WID-9")
    assert ok and mid == 4242
    _method, payload = cap.calls[0]
    assert payload == {"chat_id": 123, "widget_id": "WID-9"}


def test_send_shared_widget_requires_widget_id(monkeypatch):
    cap = _SendCapture(monkeypatch)
    for bad in ("", None):
        ok, err, mid = sender.send_shared_widget(None, 123, bad)
        assert not ok and err and mid is None
    assert cap.calls == []  # 本地拒绝，不发 API 请求


# ---------- 包3：edit_shared_widget_data ----------

def test_edit_shared_widget_data_payload(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err = sender.edit_shared_widget_data(
        None, "WID-9", public_data='{"pool":101}', cache_time=60)
    assert ok and err is None
    method, payload = cap.calls[0]
    assert method == "editSharedWidgetData"
    assert payload == {
        "widget_id": "WID-9",
        "public_data": '{"pool":101}', "cache_time": 60}


def test_edit_shared_widget_data_no_cache_time(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err = sender.edit_shared_widget_data(
        None, "WID-9", public_data="")
    assert ok and err is None
    _method, payload = cap.calls[0]
    assert payload == {"widget_id": "WID-9", "public_data": ""}


def test_edit_shared_widget_data_requires_ids(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err = sender.edit_shared_widget_data(None, "", public_data="{}")
    assert not ok and err
    ok, err = sender.edit_shared_widget_data(None, None, public_data="{}")
    assert not ok and err
    ok, err = sender.edit_shared_widget_data(None, "WID-9")  # 缺 public_data
    assert not ok and err
    assert cap.calls == []


# ---------- 包5：reply_parameters 透传（回归锁） ----------

def test_send_message_default_payload_has_no_reply_parameters(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err = sender.send_message(None, 123, "hi", {"inline_keyboard": []})
    assert ok
    method, payload = cap.calls[0]
    assert method == "sendMessage"
    assert payload == {
        "chat_id": 123, "text": "hi", "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": []}}
    assert "reply_parameters" not in payload


def test_send_message_reply_parameters_passthrough(monkeypatch):
    cap = _SendCapture(monkeypatch)
    rp = {"message_id": 77, "quote_rich_texts": [{"type": "text", "text": "x"}]}
    ok, err = sender.send_message(None, 123, "hi", reply_parameters=rp)
    assert ok
    _method, payload = cap.calls[0]
    assert payload["reply_parameters"] == rp


def test_send_message_ex_reply_parameters(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, mid = sender.send_message_ex(None, 123, "hi")
    assert ok and mid == 4242
    _method, payload = cap.calls[0]
    assert "reply_parameters" not in payload  # 默认逐字不变
    rp = {"message_id": 88}
    ok, err, mid = sender.send_message_ex(
        None, 123, "hi", reply_parameters=rp)
    assert ok and mid == 4242
    _method, payload = cap.calls[1]
    assert payload["reply_parameters"] == rp


# ---------- 包5：rich_quote 本地先验 ----------

def test_rich_quote_ok():
    texts = [{"type": "text", "text": "第一段"},
             {"type": "bold", "text": {"type": "text", "text": "加粗"}}]
    out = sender.rich_quote(texts)
    assert out == {"quote_rich_texts": texts}


def test_rich_quote_segment_limit():
    texts = [{"type": "text", "text": "x"}] * 32
    assert sender.rich_quote(texts)["quote_rich_texts"] == texts
    with pytest.raises(ValueError):
        sender.rich_quote(texts + [{"type": "text", "text": "x"}])


def test_rich_quote_total_length_limit():
    exact = [{"type": "text", "text": "a" * 1024}]
    assert sender.rich_quote(exact)["quote_rich_texts"] == exact
    with pytest.raises(ValueError):
        sender.rich_quote([{"type": "text", "text": "a" * 1025}])
    # 嵌套段的文字也计入总长
    nested = [{"type": "bold", "text": [
        {"type": "text", "text": "b" * 600},
        {"type": "text", "text": "c" * 600}]}]
    with pytest.raises(ValueError):
        sender.rich_quote(nested)


def test_rich_quote_rejects_bad_input():
    with pytest.raises(ValueError):
        sender.rich_quote([])
    with pytest.raises(ValueError):
        sender.rich_quote("not-a-list")
    with pytest.raises(ValueError):
        sender.rich_quote([123])


# ---------- 包5：sender.get_unique_gift 传输 wrapper ----------

def test_get_unique_gift_payload(monkeypatch):
    gift = {"name": "PlushPepe", "rarity": "legendary"}
    cap = _SendCapture(monkeypatch, result=gift)
    ok, err, got = sender.get_unique_gift(None, "PlushPepe")
    assert ok and err is None and got == gift
    method, payload = cap.calls[0]
    assert method == "getUniqueGift"
    assert payload == {"name": "PlushPepe"}


def test_get_unique_gift_empty_name_local_reject(monkeypatch):
    cap = _SendCapture(monkeypatch)
    ok, err, got = sender.get_unique_gift(None, "")
    assert not ok and err and got is None
    assert cap.calls == []


# ---------- 包5：gifts 进程内 TTL 缓存层 ----------

@pytest.fixture()
def gift_counter(monkeypatch):
    calls = []
    state = {"fail": False}

    def fake_sender_get(db_, name):
        calls.append(name)
        if state["fail"]:
            return False, "gift 不存在", None
        return True, None, {"name": name}

    monkeypatch.setattr(sender, "get_unique_gift", fake_sender_get)
    gifts._cache.clear()
    yield calls, state
    gifts._cache.clear()


def test_gifts_cache_hit(gift_counter):
    calls, _state = gift_counter
    ok1, _e1, g1 = gifts.get_unique_gift(None, "Pepe")
    ok2, _e2, g2 = gifts.get_unique_gift(None, "Pepe")
    assert ok1 and ok2 and g1 == g2 == {"name": "Pepe"}
    assert calls == ["Pepe"]  # 第二次命中缓存，没再打 sender


def test_gifts_negative_cache(gift_counter):
    calls, state = gift_counter
    state["fail"] = True
    ok1, err1, g1 = gifts.get_unique_gift(None, "Nope")
    ok2, err2, g2 = gifts.get_unique_gift(None, "Nope")
    assert not ok1 and not ok2 and g1 is None and g2 is None
    assert calls == ["Nope"]  # 负缓存防刷：失败结果也缓存


def test_gifts_cache_per_name(gift_counter):
    calls, _state = gift_counter
    gifts.get_unique_gift(None, "A")
    gifts.get_unique_gift(None, "B")
    assert calls == ["A", "B"]


def test_gifts_cache_expiry(monkeypatch, gift_counter):
    calls, _state = gift_counter
    clock = {"t": 1000.0}
    monkeypatch.setattr(gifts, "_now", lambda: clock["t"])
    gifts.get_unique_gift(None, "Pepe")
    clock["t"] += 3599  # 命中 TTL 3600 内
    gifts.get_unique_gift(None, "Pepe")
    assert calls == ["Pepe"]
    clock["t"] += 2  # 超过 3600 秒 → 回源
    gifts.get_unique_gift(None, "Pepe")
    assert calls == ["Pepe", "Pepe"]
