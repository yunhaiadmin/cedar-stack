# -*- coding: utf-8 -*-
"""公共池群播报（用户 2026-11-10 亲点）：发到正式群、不置顶。

- 每晚战报（默认 21:20，bot_pool_report_*）：当天帮了几位不同
  观看者、被看了几次、当天新加入几块盘、现在共有几块盘在供片；
  当天既无播放又无新盘不发。自己看自己盘剔除（与每日小结同口径）。
- 每周英雄榜（默认周日 20:30，bot_pool_hero_*）：本周（周一至
  周日）帮人前 3 位贡献者，每人帮了几位朋友、被看了几次；
  并列都列，没数据不发。
- 两条都搭 tg-rankpush 分钟巡检；开关关不发；标记口径照
  playrank（成功/空才写 last_run，失败下个 tick 重试）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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
from backend.tg_bot import pool_report, sender

SH = ZoneInfo("Asia/Shanghai")
GROUP_ID = -1001234567890
# 2026-11-09 是周一，11-15 是周日
MONDAY = datetime(2026, 11, 9, 12, 0, tzinfo=SH)
SUNDAY_2030 = datetime(2026, 11, 15, 20, 30, tzinfo=SH)
TUESDAY_2120 = datetime(2026, 11, 10, 21, 20, tzinfo=SH)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    for tbl in (
        models.WebUser.__table__,
        models.SystemConfig.__table__,
        em.PoolMember.__table__,
        em.PoolMemberStatusEvent.__table__,
        em.PoolPlayEvent.__table__,
    ):
        tbl.create(engine, checkfirst=True)
    store.invalidate()
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _set_cfg(db, key, value):
    row = db.query(models.SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()
    store.invalidate()


@pytest.fixture(autouse=True)
def _group(db):
    _set_cfg(db, "bot_group_ids", str(GROUP_ID))


def _user(db, name):
    u = models.WebUser(username=name, password_hash="x", is_active=True)
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
    at_utc = at_sh.astimezone(timezone.utc).replace(tzinfo=None)
    ev = em.PoolPlayEvent(member_id=member_id, variant_id=1, item_id=1,
                          viewer_user_id=viewer_id, created_at=at_utc)
    db.add(ev)
    db.commit()
    return ev


def _status_event(db, member_id, to_status, reason, at_sh, from_status=None):
    at_utc = at_sh.astimezone(timezone.utc).replace(tzinfo=None)
    ev = em.PoolMemberStatusEvent(
        member_id=member_id, from_status=from_status,
        to_status=to_status, reason=reason, created_at=at_utc)
    db.add(ev)
    db.commit()
    return ev


def _stub_send(monkeypatch):
    calls = []

    def fake_ex(db_, chat_id, text, reply_markup=None, **kw):
        calls.append({"chat_id": chat_id, "text": text})
        return True, None, 777

    monkeypatch.setattr(sender, "send_message_ex", fake_ex)
    pins = []
    monkeypatch.setattr(
        sender, "pin_message",
        lambda *a, **kw: pins.append(a) or (True, None))
    return calls, pins


def test_daily_report_aggregates(db, monkeypatch):
    calls, pins = _stub_send(monkeypatch)
    owner = _user(db, "owner")
    other_owner = _user(db, "owner2")
    m1 = _member(db, owner.id)
    _member(db, other_owner.id)  # 第二块在供片的盘（无播放）
    suspended = _member(db, other_owner.id, status="suspended")
    a, b = _user(db, "va"), _user(db, "vb")
    day = TUESDAY_2120.replace(hour=10)
    _play(db, m1.id, a.id, day)
    _play(db, m1.id, a.id, day + timedelta(hours=1))
    _play(db, m1.id, b.id, day + timedelta(hours=2))
    _play(db, m1.id, owner.id, day + timedelta(hours=3))  # 自己看自己不算
    # 今天新加入：approve 一块、resume 不算新盘
    _status_event(db, m1.id, "active", "approve", day, from_status="pending")
    _status_event(db, suspended.id, "active", "resume",
                  day, from_status="suspended")
    # 昨天的播放不进当天
    _play(db, m1.id, a.id, day - timedelta(days=1))

    res = pool_report.push_daily_report(db, now_sh=TUESDAY_2120)

    assert res["pushed"] is True
    assert len(calls) == 1 and calls[0]["chat_id"] == GROUP_ID
    assert pins == []  # 不置顶
    text = calls[0]["text"]
    assert "帮了 2 位朋友" in text
    assert "被看了 3 次" in text
    assert "新加入 1 块盘" in text
    assert "共有 2 块盘在供片" in text


def test_daily_report_empty_day_no_send(db, monkeypatch):
    calls, _pins = _stub_send(monkeypatch)
    _member(db, _user(db, "owner").id)
    res = pool_report.push_daily_report(db, now_sh=TUESDAY_2120)
    assert res["pushed"] is False
    assert res["reason"] == "empty"
    assert calls == []


def test_daily_report_new_disk_only_still_sends(db, monkeypatch):
    calls, _pins = _stub_send(monkeypatch)
    owner = _user(db, "owner")
    m = _member(db, owner.id)
    _status_event(db, m.id, "active", "apply",
                  TUESDAY_2120.replace(hour=9), from_status="candidate")
    res = pool_report.push_daily_report(db, now_sh=TUESDAY_2120)
    assert res["pushed"] is True
    assert "新加入 1 块盘" in calls[0]["text"]


def test_hero_board_ranks_and_lists_ties(db, monkeypatch):
    calls, pins = _stub_send(monkeypatch)
    owners = {n: _user(db, n) for n in ("ana", "bob", "cy", "dan", "eve")}
    members = {n: _member(db, u.id) for n, u in owners.items()}
    viewers = [_user(db, f"v{i}") for i in range(6)]
    wed = MONDAY + timedelta(days=2, hours=2)
    # ana：3 位观看者各 2 次 = 6 次；bob 同数（并列第一）
    for i, v in enumerate(viewers[:3]):
        _play(db, members["ana"].id, v.id, wed + timedelta(minutes=i))
        _play(db, members["ana"].id, v.id, wed + timedelta(minutes=20 + i))
        _play(db, members["bob"].id, v.id, wed + timedelta(minutes=40 + i))
        _play(db, members["bob"].id, v.id, wed + timedelta(minutes=60 + i))
    # cy：2 位 2 次；dan：1 位 1 次（第四名不进榜）；eve：自己看自己不算
    _play(db, members["cy"].id, viewers[3].id, wed)
    _play(db, members["cy"].id, viewers[4].id, wed)
    _play(db, members["dan"].id, viewers[5].id, wed)
    _play(db, members["eve"].id, owners["eve"].id, wed)
    # 上周的播放不进本周
    _play(db, members["dan"].id, viewers[0].id, MONDAY - timedelta(days=1))

    board = pool_report.collect_heroes(db, SUNDAY_2030)
    by_name = {row["username"]: row for row in board}
    assert by_name["ana"]["viewers"] == 3 and by_name["ana"]["plays"] == 6
    assert by_name["bob"]["viewers"] == 3 and by_name["bob"]["plays"] == 6
    assert by_name["cy"]["viewers"] == 2 and by_name["cy"]["plays"] == 2
    assert "dan" not in by_name and "eve" not in by_name

    res = pool_report.push_weekly_heroes(db, now_sh=SUNDAY_2030)
    assert res["pushed"] is True
    assert pins == []
    text = calls[0]["text"]
    assert "ana" in text and "bob" in text and "cy" in text
    assert "dan" not in text
    assert "帮了 3 位朋友" in text and "被看了 6 次" in text


def test_hero_board_no_data_no_send(db, monkeypatch):
    calls, _pins = _stub_send(monkeypatch)
    _member(db, _user(db, "owner").id)
    res = pool_report.push_weekly_heroes(db, now_sh=SUNDAY_2030)
    assert res["pushed"] is False
    assert res["reason"] == "empty"
    assert calls == []


def test_ticks_respect_switches_and_clocks(db, monkeypatch):
    calls, _pins = _stub_send(monkeypatch)
    owner = _user(db, "owner")
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, TUESDAY_2120.replace(hour=10))
    _play(db, m.id, viewer.id, SUNDAY_2030.replace(hour=10))
    # 战报：21:19 不发，21:20 发一次，再 tick 不重发
    assert pool_report.run_scheduled_tick(
        db, now_sh=TUESDAY_2120.replace(minute=19)) == []
    actions = pool_report.run_scheduled_tick(db, now_sh=TUESDAY_2120)
    assert any(a.startswith("report:") for a in actions)
    assert pool_report.run_scheduled_tick(db, now_sh=TUESDAY_2120) == []
    assert len(calls) == 1
    # 英雄榜：周二不发（哪怕到点）；周日 20:29 不发、20:30 发
    assert pool_report.run_scheduled_tick(
        db, now_sh=SUNDAY_2030.replace(minute=29)) == []
    actions = pool_report.run_scheduled_tick(db, now_sh=SUNDAY_2030)
    assert any(a.startswith("hero:") for a in actions)
    assert len(calls) == 2
    # 开关关：都不发
    _set_cfg(db, "bot_pool_report_enabled", "false")
    _set_cfg(db, "bot_pool_hero_enabled", "false")
    nxt = TUESDAY_2120 + timedelta(days=7)
    _play(db, m.id, viewer.id, nxt.replace(hour=10))
    assert pool_report.run_scheduled_tick(db, now_sh=nxt) == []
    assert len(calls) == 2
