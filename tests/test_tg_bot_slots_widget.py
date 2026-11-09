"""TG Bot HTML 版老虎机（Bot API 10.4 Widgets）。

生产环境 widget 目前仅限私聊、自动 protected content、不可转发；
群聊继续走 sendDice 版（见 test_tg_bot_slots_group.py），不得拆除。

协议（与 ~/workspace/tg-slots-widget/ PoC 同款）：
- widget 点拉杆 → TelegramWidget.request(JSON '{"a":"spin","bet":N}')
  （≤64 字节），bot 收 widget_query 更新，15 秒内 answerWidgetQuery
  回 result JSON（≤1024 字节）。
- 成功：{"symbols": [...], "win": int, "balance": int, "pool": int}；
  失败：{"error": <slots.py reason 键>, "msg": <reason_message>}。
- publicData = {"pool": int, "pay": <赔付表版本>}；chatData（私聊）
  = {"balance": int, "pool": int}。
- 结果来源：服务端 secrets 生成 1–64 均匀随机数，走同一 decode_dice；
  结算复用 begin_spin / settle_spin 全套；spin_key = "tgwq:<query id>"。
"""
from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.tg_bot import callbacks, handlers, poller, router, sender

TG = 920001


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(
        name="公益服", slug="main", access_mode="free"))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db, tg=TG, name="alice", points=1000, created_at=None):
    row = models.WebUser(
        username=name, password_hash="x", telegram_id=tg,
        points=points, vitality=14, is_staff=False,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.commit()
    return row


def _set_config(db, key, value):
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if row is None:
        db.add(models.SystemConfig(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()


def _balance(db, user_id):
    return db.query(models.WebUser.points).filter(
        models.WebUser.id == user_id).scalar()


def _logs(db, user_id, type_):
    return db.query(models.PointsLog).filter(
        models.PointsLog.user_id == user_id,
        models.PointsLog.type == type_).all()


def _wq(query_id="wq-1", tg=TG, chat=None, data='{"a":"spin","bet":10}',
        message_id=4242):
    return {
        "id": query_id,
        "from": {"id": tg, "first_name": "Alice"},
        "chat": chat or {"id": tg, "type": "private"},
        "message_id": message_id,
        "chat_instance": "901234567890123456",
        "data": data,
    }


class _Calls:
    """记录 sender widget 三件套调用的打桩。"""

    def __init__(self):
        self.answers = []   # (widget_query_id, data)
        self.edits = []     # payload
        self.widgets = []   # payload
        self.panels = []    # (chat_id, text, markup)

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
            sender, "send_panel",
            lambda db_, chat_id, text, markup=None, **kw:
                self.panels.append((chat_id, text, markup)) or (True, None))
        return self


# ---------- widget 资产与状态 ----------

def test_widget_html_size_limits():
    from backend.tg_bot import slots_widget as sw

    stats = sw.widget_html_stats()
    assert stats["raw_bytes"] <= 32768
    assert stats["compressed_bytes"] <= 4096
    assert not stats["has_nul"]
    assert stats["raw_bytes"] == 6095  # 与 PoC 同款文件逐字节一致


def test_public_and_chat_data_shapes(db):
    from backend.tg_bot import slots_widget as sw

    pub = json.loads(sw.public_data_json(db))
    assert pub["pool"] == 100  # 种子池
    assert isinstance(pub["pay"], int)
    assert len(sw.public_data_json(db).encode()) <= 1024
    chat = json.loads(sw.chat_data_json(123, 100))
    assert chat == {"balance": 123, "pool": 100}
    assert len(sw.chat_data_json(123, 100).encode()) <= 1024


def test_pay_version_changes_with_pay_config(db):
    from backend.tg_bot import slots_widget as sw

    v1 = sw.pay_version(db)
    _set_config(db, "slots_pay_777", 25)
    assert sw.pay_version(db) != v1


# ---------- sender 三件套的线格式 ----------

def test_send_widget_payload(monkeypatch):
    captured = {}

    def fake_call_ex(db_, method, payload):
        captured["method"] = method
        captured["payload"] = payload
        return True, None, {"message_id": 4242, "widget": {"id": "777"}}

    monkeypatch.setattr(sender, "_call_ex", fake_call_ex)
    ok, err, mid = sender.send_widget(
        None, 123, "<h1>x</h1>", public_data='{"pool":100}',
        chat_data='{"balance":5}', caption="cap", allow_queries=True)
    assert ok and err is None and mid == 4242
    assert captured["method"] == "sendWidget"
    p = captured["payload"]
    assert p["chat_id"] == 123 and p["html"] == "<h1>x</h1>"
    assert p["width"] == 0 and p["height"] == 0  # 0 = 自动尺寸
    assert p["allow_queries"] is True
    assert p["public_data"] == '{"pool":100}'
    assert p["chat_data"] == '{"balance":5}'
    assert p["caption"] == "cap"


def test_answer_widget_query_payload(monkeypatch):
    captured = {}

    def fake_call(db_, method, payload):
        captured["method"] = method
        captured["payload"] = payload
        return True, None

    monkeypatch.setattr(sender, "_call", fake_call)
    ok, err = sender.answer_widget_query(None, "8123", '{"win":0}')
    assert ok and err is None
    assert captured["method"] == "answerWidgetQuery"
    assert captured["payload"] == {
        "widget_query_id": "8123", "data": '{"win":0}'}


def test_edit_message_widget_data_payload(monkeypatch):
    captured = {}

    def fake_call(db_, method, payload):
        captured["method"] = method
        captured["payload"] = payload
        return True, None

    monkeypatch.setattr(sender, "_call", fake_call)
    ok, err = sender.edit_message_widget_data(
        None, 123, 4242, public_data='{"pool":101}')
    assert ok and err is None
    assert captured["method"] == "editMessageWidgetData"
    assert captured["payload"] == {
        "chat_id": 123, "message_id": 4242, "public_data": '{"pool":101}'}


# ---------- widget_query 开局结算 ----------

def test_handle_widget_query_jackpot(db, monkeypatch):
    import secrets

    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 63)  # dice 64 = 777
    sw.handle_widget_query(db, _wq())
    assert len(calls.answers) == 1
    qid, data = calls.answers[0]
    assert qid == "wq-1"
    result = json.loads(data)
    assert result["symbols"] == ["seven", "seven", "seven"]
    assert result["win"] == 300  # 基础 200 + 整池 100
    assert result["balance"] == 390
    assert result["pool"] == 100  # 抱池后重置种子
    assert len(data.encode()) <= 1024
    assert _balance(db, user.id) == 390
    assert len(_logs(db, user.id, "slots_bet")) == 1
    assert len(_logs(db, user.id, "slots_jackpot")) == 1
    # 结算后更新 widget 公共状态（奖池摘要）
    assert calls.edits, "结算后须更新 publicData"
    assert json.loads(calls.edits[0]["public_data"])["pool"] == 100
    # 既有行为保留：私聊仍发一行结果卡
    assert len(calls.panels) == 1
    assert "777" in calls.panels[0][1] or "7️⃣" in calls.panels[0][1]


def test_handle_widget_query_uses_server_random(db, monkeypatch):
    import secrets

    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    seen = []
    monkeypatch.setattr(
        secrets, "randbelow", lambda n: seen.append(n) or 0)  # dice 1 = BAR×3
    sw.handle_widget_query(db, _wq())
    assert seen == [64]  # 1–64 均匀随机，随机源在服务端
    result = json.loads(calls.answers[0][1])
    assert result["symbols"] == ["bar", "bar", "bar"]
    assert result["win"] == 100
    assert _balance(db, user.id) == 100 - 10 + 100


def test_handle_widget_query_duplicate_replays_settled(db, monkeypatch):
    import secrets

    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 63)
    sw.handle_widget_query(db, _wq())
    first = calls.answers[0][1]
    sw.handle_widget_query(db, _wq())  # Telegram 重投递同一 query
    assert len(calls.answers) == 2
    assert calls.answers[1][1] == first  # 同一结果，不重摇
    assert _balance(db, user.id) == 390
    assert len(_logs(db, user.id, "slots_bet")) == 1
    assert len(_logs(db, user.id, "slots_win")) == 1
    assert len(calls.panels) == 1  # 重放不补发结果卡


def test_handle_widget_query_unbound(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(db, _wq(tg=TG + 55))
    assert len(calls.answers) == 1
    result = json.loads(calls.answers[0][1])
    assert result["error"] == "unbound"
    assert "绑定" in result["msg"]
    assert calls.edits == [] and calls.panels == []


def test_handle_widget_query_bad_tier(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(db, _wq(data='{"a":"spin","bet":7}'))
    result = json.loads(calls.answers[0][1])
    assert result["error"] == "bad_tier"
    assert _logs(db, 1, "slots_bet") == []


def test_handle_widget_query_bad_data(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(db, _wq(data="not json"))
    sw.handle_widget_query(
        db, _wq(query_id="wq-2", data='{"a":"refresh"}'))
    assert len(calls.answers) == 2
    for _qid, data in calls.answers:
        assert json.loads(data)["error"] == "bad_request"
    assert _balance(db, user.id) == 100  # 一分没扣


def test_handle_widget_query_insufficient(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    _user(db, points=3)
    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(db, _wq(data='{"a":"spin","bet":5}'))
    result = json.loads(calls.answers[0][1])
    assert result["error"] == "insufficient"
    assert "能量不足" in result["msg"]


def test_handle_widget_query_group_rejected(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(
        db, _wq(chat={"id": -100999, "type": "supergroup"}))
    result = json.loads(calls.answers[0][1])
    assert result["error"] == "private_only"
    assert _balance(db, user.id) == 100


def test_handle_widget_query_in_flight(db, monkeypatch):
    from backend import slots as slots_svc
    from backend.tg_bot import slots_widget as sw

    user = _user(db, points=100)
    assert slots_svc.begin_spin(db, user, 10, "other-key")["ok"]
    calls = _Calls().patch(monkeypatch)
    sw.handle_widget_query(db, _wq())
    result = json.loads(calls.answers[0][1])
    assert result["error"] == "in_flight"
    assert _balance(db, user.id) == 90  # 只扣了先开的那局


# ---------- 分发、轮询与入口 ----------

def test_router_dispatches_widget_query(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    seen = []
    monkeypatch.setattr(
        sw, "handle_widget_query",
        lambda db_, q: seen.append(q))
    router.dispatch(db, {"update_id": 1, "widget_query": _wq()})
    assert len(seen) == 1
    assert seen[0]["id"] == "wq-1"


def test_poll_once_allowed_updates_include_widget_query(monkeypatch):
    import httpx

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": []}

    class _Client:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            captured["params"] = params
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    assert poller._poll_once("TESTTOKEN", 1, timeout=30) == []
    allowed = captured["params"]["allowed_updates"]
    assert "widget_query" in allowed
    assert "shared_widget_query" in allowed


def test_slots_private_panel_has_widget_button(db):
    _user(db)
    text, markup = handlers.handle_slots(
        db, {"id": TG, "first_name": "Alice"}, TG, "")
    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert {"text": "🎰 HTML 版老虎机", "callback_data": "slots_widget"} in flat


def test_slots_group_panel_has_no_widget_button(db):
    _text, markup = handlers.handle_slots_group(
        db, {"id": TG, "first_name": "Alice"}, -100999, "")
    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "slots_widget" not in flat


def test_send_slots_widget_disabled(db, monkeypatch):
    from backend.tg_bot import slots_widget as sw

    _user(db)
    _set_config(db, "slots_enabled", "false")
    calls = _Calls().patch(monkeypatch)
    ok, err, mid = sw.send_slots_widget(db, TG, _user(db, tg=TG + 1, name="bob"))
    assert not ok and mid is None
    assert calls.widgets == []


def _cq_slots_widget(cq_id="cq-w-1", tg=TG, chat=None):
    return {
        "id": cq_id,
        "from": {"id": tg, "first_name": "Alice"},
        "data": "slots_widget",
        "message": {"message_id": 55, "chat": chat or {"id": tg}},
    }


def test_slots_widget_callback_sends_widget(db, monkeypatch):
    user = _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    answers = []
    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False:
            answers.append((text, show_alert)) or (True, None))
    callbacks.handle_callback(db, _cq_slots_widget())
    assert len(calls.widgets) == 1
    w = calls.widgets[0]
    assert w["chat_id"] == TG
    assert w["allow_queries"] is True
    assert json.loads(w["public_data"])["pool"] == 100
    assert json.loads(w["chat_data"])["balance"] == 100
    assert len(w["html"].encode()) <= 32768


def test_slots_widget_callback_group_rejected(db, monkeypatch):
    _user(db, points=100)
    calls = _Calls().patch(monkeypatch)
    answers = []
    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False:
            answers.append((text, show_alert)) or (True, None))
    callbacks.handle_callback(
        db, _cq_slots_widget(chat={"id": -100999}))
    assert calls.widgets == []
    assert answers and "私聊" in (answers[0][0] or "")


def test_slots_widget_callback_unbound(db, monkeypatch):
    calls = _Calls().patch(monkeypatch)
    answers = []
    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False:
            answers.append((text, show_alert)) or (True, None))
    callbacks.handle_callback(db, _cq_slots_widget(tg=TG + 55))
    assert calls.widgets == []
    assert answers and "绑定" in (answers[0][0] or "")
