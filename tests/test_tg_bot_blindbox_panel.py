# -*- coding: utf-8 -*-
"""盲盒机器人面板（PR-2，公益服私聊入口）。

钉死口径：
- 入口挂在用户面板「福利活动」分组（menu:blindbox），三层骨架不变。
- 面板落公益 realm，走 backend.blindbox 同一套引擎与账；总开关/
  该服未开启时只出「暂未开启」卡，没有抽盒按钮。
- 档位页概率全公布；抽盒走 blindbox_draw:{tier} 回调，私聊限定
  （群里点到只 toast、不抽）；同一 callback 重投递不二次开盒。
- 抽盒先应答「开盒中…」停转圈，再执行抽盒（2026-11-11 卡顿修复）；
  成功另发结果卡；失败发私聊消息说原因，引擎保证不扣分。
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

from backend import blindbox as bb
from backend import models
from backend.database import Base
from backend.integrations import store as config_store
from backend.tg_bot import blindbox_panel, callbacks, handlers, sender

TG = 930001


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    config_store.invalidate()
    blindbox_panel._draw_seen.clear()
    try:
        yield session
    finally:
        config_store.invalidate()
        session.close()
        engine.dispose()


def _realm(db, access_mode="free"):
    r = models.ServerRealm(name="公益服", slug="free1", url="",
                           access_mode=access_mode, is_active=True)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _user(db, tg=TG, points=1000):
    u = models.WebUser(username="alice", password_hash="x",
                       telegram_id=tg, is_active=True, points=points)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _tier(key="t1", price=20):
    return {
        "key": key, "name": "尝鲜盒", "price": price, "enabled": True,
        "sort": 0,
        "fallback_prize": {"kind": "points", "weight": 1,
                           "params": {"amount": 5}},
        "prizes": [{"kind": "points", "weight": 1,
                    "params": {"amount": 10}}],
    }


def _enable(db, realm_id, tiers=None):
    bb.save_master_switch(db, True)
    bb.save_realm_config(db, realm_id, {
        "enabled": True, "starts_at": "", "ends_at": "",
        "daily_spend_cap": 0, "access_days_daily_budget": 0,
        "whitelist_stock_total": 0,
        "tiers": tiers if tiers is not None else [_tier()],
    })
    db.commit()
    config_store.invalidate()


def _all_callbacks(markup) -> list[str]:
    out = []
    for row in (markup or {}).get("inline_keyboard") or []:
        for btn in row:
            if btn.get("callback_data"):
                out.append(btn["callback_data"])
    return out


class _Calls:
    def __init__(self):
        self.toasts = []
        self.panels = []
        self.messages = []
        self.events = []

    def patch(self, monkeypatch):
        def _answer(db_, cb_id, text=None, show_alert=False):
            self.toasts.append((cb_id, text, show_alert))
            self.events.append("answer")
            return True, None

        monkeypatch.setattr(sender, "answer_callback_query", _answer)
        monkeypatch.setattr(
            sender, "send_panel",
            lambda db_, chat_id, text, markup=None, **kw:
                self.panels.append((chat_id, text, markup)) or (True, None))
        monkeypatch.setattr(
            sender, "send_message",
            lambda db_, chat_id, text, markup=None, **kw:
                self.messages.append((chat_id, text)) or (True, None))


def _cb(cb_id="cb-1", tg=TG, chat_id=TG, data="blindbox_draw:t1"):
    return {
        "id": cb_id,
        "from": {"id": tg, "first_name": "Alice"},
        "message": {"message_id": 55, "chat": {"id": chat_id}},
        "data": data,
    }


# ---------------------------------------------------------------------------
# 入口与渲染
# ---------------------------------------------------------------------------

def test_welfare_group_has_blindbox_entry():
    buttons = handlers.USER_PANEL_GROUPS["welfare"]["buttons"]
    assert {"text": "🎁 盲盒", "callback_data": "menu:blindbox"} in buttons


def test_home_closed_when_not_enabled(db):
    realm = _realm(db)
    user = _user(db)
    text, markup = blindbox_panel.render_home(db, user)
    assert "暂未开启" in text
    assert not any(c.startswith("blindbox_draw:")
                   for c in _all_callbacks(markup))
    assert not any(c.startswith("menu:blindbox_tier:")
                   for c in _all_callbacks(markup))
    assert realm.id == blindbox_panel.welfare_realm_id(db)


def test_home_lists_tiers_when_enabled(db):
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db)
    text, markup = blindbox_panel.render_home(db, user)
    assert "尝鲜盒" in text
    assert "20 能量" in text
    cbs = _all_callbacks(markup)
    assert "menu:blindbox_tier:t1" in cbs
    assert "menu:blindbox_codes" in cbs
    assert "menu:blindbox_draws" in cbs


def test_tier_page_shows_full_probability(db):
    realm = _realm(db)
    _enable(db, realm.id, tiers=[{
        "key": "t2", "name": "进阶盒", "price": 50, "enabled": True,
        "sort": 0,
        "fallback_prize": {"kind": "points", "weight": 1,
                           "params": {"amount": 10}},
        "prizes": [
            {"kind": "points", "weight": 3, "params": {"amount": 30}},
            {"kind": "points", "weight": 1, "params": {"amount": 90}},
        ],
    }])
    user = _user(db)
    text, markup = blindbox_panel.render_tier(db, user, "t2")
    assert "75%" in text and "25%" in text
    assert "兜底" in text
    assert "blindbox_draw:t2" in _all_callbacks(markup)


def test_codes_and_draws_pages_empty(db):
    _realm(db)
    user = _user(db)
    text, _ = blindbox_panel.render_codes(db, user)
    assert "还没有" in text
    text, _ = blindbox_panel.render_draws(db, user)
    assert "还没有" in text


# ---------------------------------------------------------------------------
# 抽盒
# ---------------------------------------------------------------------------

def test_draw_success_charges_and_pays(db, monkeypatch):
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db, points=1000)
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb())
    # 1000 - 20（售价） + 10（唯一奖品） = 990
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 990
    draws = db.query(models.BlindboxDraw).all()
    assert len(draws) == 1 and draws[0].status == "delivered"
    assert len(calls.panels) == 1
    assert "10 能量" in calls.panels[0][1]


def test_draw_answers_before_drawing(db, monkeypatch):
    """应答顺序（卡顿回归锁）：先 answer 停转圈，再执行抽盒。

    旧实现 resolve＋完整抽盒之后才应答，用户端转圈要等整条链路
    走完；连点时后面的回调排队等前一张结果卡的图片上传，回调
    甚至过期（query too old）。钉死：answer 事件必须在 draw 之前，
    且首个 toast 是「开盒中…」。
    """
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db, points=1000)
    calls = _Calls()
    calls.patch(monkeypatch)
    real_draw = blindbox_panel.do_draw

    def recording_draw(db_, user_, tier_key):
        calls.events.append("draw")
        return real_draw(db_, user_, tier_key)

    monkeypatch.setattr(blindbox_panel, "do_draw", recording_draw)
    callbacks.handle_callback(db, _cb())
    assert calls.events[0] == "answer"
    assert calls.events.index("answer") < calls.events.index("draw")
    assert calls.toasts[0][1] == "开盒中…"
    assert len(calls.panels) == 1
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 990


def test_draw_replayed_callback_does_not_double_charge(db, monkeypatch):
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db, points=1000)
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb(cb_id="cb-dup"))
    callbacks.handle_callback(db, _cb(cb_id="cb-dup"))
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 990
    assert db.query(models.BlindboxDraw).count() == 1
    assert len(calls.panels) == 1
    assert any("已经开过" in (t[1] or "") for t in calls.toasts)


def test_draw_group_chat_rejected(db, monkeypatch):
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db, points=1000)
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb(chat_id=-100123))
    assert db.query(models.BlindboxDraw).count() == 0
    assert calls.panels == []
    assert any("私聊" in (t[1] or "") for t in calls.toasts)
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 1000


def test_draw_insufficient_points_no_charge(db, monkeypatch):
    realm = _realm(db)
    _enable(db, realm.id)
    user = _user(db, points=10)
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb())
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 10
    delivered = db.query(models.BlindboxDraw).filter(
        models.BlindboxDraw.status == "delivered").count()
    assert delivered == 0
    assert calls.panels == []
    # 回调已先应答「开盒中…」(只能应答一次)，失败原因改走私聊消息
    assert len(calls.messages) == 1 and calls.messages[0][1]
    assert not any(t[2] for t in calls.toasts)


def test_draw_when_disabled_message_only(db, monkeypatch):
    _realm(db)
    user = _user(db, points=1000)
    calls = _Calls()
    calls.patch(monkeypatch)
    callbacks.handle_callback(db, _cb())
    assert db.query(models.BlindboxDraw).count() == 0
    assert calls.panels == []
    assert len(calls.messages) == 1 and calls.messages[0][1]
    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 1000


def test_codes_page_lists_won_code(db):
    realm = _realm(db)
    _enable(db, realm.id, tiers=[{
        "key": "tc", "name": "卡码盒", "price": 500, "enabled": True,
        "sort": 0,
        "fallback_prize": {"kind": "points", "weight": 1,
                           "params": {"amount": 5}},
        "prizes": [{"kind": "reg_code", "weight": 1,
                    "params": {"days": 30}}],
    }])
    user = _user(db, points=1000)
    ok, text, _markup = blindbox_panel.do_draw(db, user, "tc")
    assert ok
    codes = bb.my_card_pack(db, user)
    assert len(codes) == 1
    page, _ = blindbox_panel.render_codes(db, user)
    assert codes[0]["code"] in page
    assert "未使用" in page
    draws_page, _ = blindbox_panel.render_draws(db, user)
    assert "卡码盒" in draws_page and "已发放" in draws_page
