"""TG Bot 死按钮接真内容：「🏅 成就」menu:achievements（账号组）与
「🏆 我的抽奖」menu:mylottery（福利组）。

此前两个 callback 在 _render_menu 里没有任何渲染分支，点到被静默
忽略（_UnknownMenuAction）。本文件钉住新行为：
- 私聊已绑定：渲染本人真卡（成就与账号卡同源口径；我的抽奖与
  /lottery 私聊分支同一构建器）＋返回所属分组的按钮；
- 私聊未绑定：回绑定指引；
- 群里点到：只回私聊提示卡，个人数据绝不铺进群共享消息。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.tg_bot import callbacks, ephemeral, handlers, sender

TG = 920001
GROUP_CHAT = {"id": -100999}


class _FakeTimer:
    """假 Timer：只登记、永不真挂线程（堵群面板自毁真 Timer 泄漏）。"""

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _no_real_timers(monkeypatch):
    monkeypatch.setattr(ephemeral, "Timer", _FakeTimer)


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


def _user(db, tg=TG, name="alice"):
    row = models.WebUser(
        username=name, password_hash="x", telegram_id=tg,
        points=42, vitality=14, is_staff=False,
    )
    db.add(row)
    db.commit()
    return row


def _patch_panel(monkeypatch):
    state = {"edits": []}
    monkeypatch.setattr(
        sender, "answer_callback_query",
        lambda db_, cq_id, text=None, show_alert=False: (True, None))
    monkeypatch.setattr(
        sender, "edit_panel",
        lambda db_, chat_id, mid, text, markup=None, has_photo=False:
            state["edits"].append((text, markup)) or (True, None))
    return state


def _cq(action, tg=TG, chat=None):
    return {
        "id": "cq-dead-1",
        "from": {"id": tg, "first_name": "Alice"},
        "data": f"menu:{action}",
        "message": {"message_id": 55, "chat": chat or {"id": tg}},
    }


def _flat(markup):
    return [b["callback_data"] for r in markup["inline_keyboard"] for b in r
            if "callback_data" in b]


def _seed_achievements(db, user, unlocked=("first_night",)):
    defs = [
        ("first_night", "初夜", "第一次在深夜看片（23:00–05:00）", "🌙", 10),
        ("streak7", "连击", "连续签到 7 天", "🔥", 20),
    ]
    for key, name, desc, icon, sort in defs:
        db.add(models.PanelAchievementDef(
            key=key, name=name, desc=desc, icon=icon, sort=sort, enabled=True))
    for key in unlocked:
        db.add(models.PanelAchievementUnlock(
            user_id=user.id, ach_key=key,
            unlocked_at=datetime(2026, 11, 2, 23, 30)))
    db.commit()


def _seed_lottery(db, user):
    """一场进行中（已参加）、一场已开奖且本人中奖。"""
    open_round = models.LotteryRound(
        title="周末观影抽奖", chat_id=-100999, status="open",
        draw_at=datetime.now() + timedelta(days=1))
    done_round = models.LotteryRound(
        title="开服纪念抽奖", chat_id=-100999, status="done",
        drawn_at=datetime.now() - timedelta(days=2))
    db.add_all([open_round, done_round])
    db.flush()
    e1 = models.LotteryRoundEntry(
        round_id=open_round.id, user_id=user.id, telegram_id=TG)
    e2 = models.LotteryRoundEntry(
        round_id=done_round.id, user_id=user.id, telegram_id=TG)
    prize = models.LotteryRoundPrize(
        round_id=done_round.id, name="公益天数 7 天", type="days",
        value=7, quantity=1)
    db.add_all([e1, e2, prize])
    db.flush()
    db.add(models.LotteryRoundWinner(
        round_id=done_round.id, entry_id=e2.id, prize_id=prize.id))
    db.commit()
    return open_round, done_round


# ---------- 成就卡 ----------

def test_achievements_private_renders_real_card(db, monkeypatch):
    user = _user(db)
    _seed_achievements(db, user)
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("achievements"))
    assert len(state["edits"]) == 1
    text, markup = state["edits"][0]
    assert "我的成就" in text
    assert "已点亮 1／共 2 个" in text
    assert "初夜" in text and "2026-11-02 点亮" in text
    assert "还有 1 个成就没点亮" in text
    # 返回按钮回账号组
    assert _flat(markup) == ["menu:group_account"]


def test_achievements_private_empty_state(db, monkeypatch):
    user = _user(db)
    _seed_achievements(db, user, unlocked=())
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("achievements"))
    text, markup = state["edits"][0]
    assert "已点亮 0／共 2 个" in text
    assert "还没有点亮任何成就" in text
    assert _flat(markup) == ["menu:group_account"]


def test_achievements_card_truncates_long_list(db, monkeypatch):
    user = _user(db)
    for i in range(12):
        db.add(models.PanelAchievementDef(
            key=f"ach_{i}", name=f"成就{i}", desc="说明", icon="🏅",
            sort=i, enabled=True))
        db.add(models.PanelAchievementUnlock(
            user_id=user.id, ach_key=f"ach_{i}",
            unlocked_at=datetime(2026, 11, 1)))
    db.commit()
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("achievements"))
    text, _markup = state["edits"][0]
    assert "已点亮 12／共 12 个" in text
    assert "去网站账号页看" in text
    assert "成就10" not in text  # 只列前 10 条


def test_achievements_unbound_gets_bind_guide(db, monkeypatch):
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("achievements", tg=TG + 77))
    assert len(state["edits"]) == 1
    text, _markup = state["edits"][0]
    assert "如何绑定账号" in text


def test_achievements_group_chat_private_only(db, monkeypatch):
    user = _user(db)
    _seed_achievements(db, user)
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("achievements", chat=GROUP_CHAT))
    assert len(state["edits"]) == 1
    text, _markup = state["edits"][0]
    assert "私聊" in text
    assert "初夜" not in text and "已点亮" not in text


# ---------- 我的抽奖卡 ----------

def test_mylottery_private_renders_records(db, monkeypatch):
    user = _user(db)
    _seed_lottery(db, user)
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("mylottery"))
    assert len(state["edits"]) == 1
    text, markup = state["edits"][0]
    assert "我的抽奖记录" in text
    assert "周末观影抽奖" in text and "进行中" in text
    assert "开服纪念抽奖" in text and "已中奖" in text
    # 返回按钮回福利组
    assert _flat(markup) == ["menu:group_welfare"]


def test_mylottery_private_no_entries(db, monkeypatch):
    _user(db)
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("mylottery"))
    text, markup = state["edits"][0]
    assert "还没有参加过抽奖" in text
    assert _flat(markup) == ["menu:group_welfare"]


def test_mylottery_matches_lottery_command_card(db):
    # 与 handle_lottery 私聊分支同一构建器：同一用户两处文本逐字一致。
    user = _user(db)
    _seed_lottery(db, user)
    tg_user = {"id": TG, "first_name": "Alice"}
    via_command = handlers.handle_lottery(db, tg_user, TG, "", False)
    assert handlers.my_lottery_card(db, user) == via_command


def test_mylottery_unbound_gets_bind_guide(db, monkeypatch):
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("mylottery", tg=TG + 77))
    assert len(state["edits"]) == 1
    text, _markup = state["edits"][0]
    assert "如何绑定账号" in text


def test_mylottery_group_chat_private_only(db, monkeypatch):
    user = _user(db)
    _seed_lottery(db, user)
    state = _patch_panel(monkeypatch)
    callbacks.handle_callback(db, _cq("mylottery", chat=GROUP_CHAT))
    assert len(state["edits"]) == 1
    text, _markup = state["edits"][0]
    assert "私聊" in text
    assert "周末观影抽奖" not in text and "已中奖" not in text
