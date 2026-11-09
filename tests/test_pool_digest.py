# -*- coding: utf-8 -*-
"""公共池贡献者每日小结（用户 2026-11-10 亲点）：每晚 21:00（可配）
给当天有被看的贡献者发一条机器人私聊：帮了几位不同的观看者、
被看了几次、本月累计，外加一句感谢。

口径：
- 按用户聚合（多块盘只发一条）；自己看自己盘不算（与激励计量
  同口径剔除 viewer == 贡献者本人）；不出现观看者名字、不列片名；
- 当天 0 次不发；每人每天最多一条（proactive claim 台账锚：
  kind=pool_daily_digest、subject=日期 YYYYMMDD），重启不重发；
- 用户「贡献回响」TG 偏好（notify_prefs pool_digest 类）关了不发；
  没绑 TG 静默跳过；总开关 bot_pool_digest_enabled 默认开。
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
from backend.tg_bot import pool_digest, sender

SH = ZoneInfo("Asia/Shanghai")
NOW_SH = datetime(2026, 11, 10, 21, 0, tzinfo=SH)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    for tbl in (
        models.WebUser.__table__,
        models.SystemConfig.__table__,
        models.TgProactiveNotice.__table__,
        em.PoolMember.__table__,
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


def _user(db, name, tg_id=None, **kw):
    u = models.WebUser(username=name, password_hash="x",
                       telegram_id=tg_id, is_active=True, **kw)
    db.add(u)
    db.commit()
    return u


_member_seq = [1000]


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


def _stub_send(monkeypatch):
    calls = []

    def fake(db_, chat_id, text, reply_markup=None, **kw):
        calls.append({"chat_id": chat_id, "text": text})
        return True, None

    monkeypatch.setattr(sender, "send_message", fake)
    return calls


def _today(hour=10):
    return NOW_SH.replace(hour=hour, minute=0, second=0, microsecond=0)


def test_multi_disk_aggregated_into_single_message(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m1 = _member(db, owner.id)
    m2 = _member(db, owner.id)
    a = _user(db, "viewer-a")
    b = _user(db, "viewer-b")
    c = _user(db, "viewer-c")
    _play(db, m1.id, a.id, _today(9))
    _play(db, m1.id, a.id, _today(10))
    _play(db, m1.id, b.id, _today(11))
    _play(db, m2.id, a.id, _today(12))
    # 本月稍早的一次：只进本月累计、不进当天
    _play(db, m1.id, c.id, NOW_SH - timedelta(days=3))

    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)

    assert res["sent"] == 1
    assert len(calls) == 1
    assert calls[0]["chat_id"] == 111
    text = calls[0]["text"]
    assert "帮了 2 位朋友" in text
    assert "被看了 4 次" in text
    assert "本月累计被看 5 次" in text
    assert "viewer" not in text  # 不出现观看者名字


def test_self_view_excluded(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m = _member(db, owner.id)
    _play(db, m.id, owner.id, _today(9))
    _play(db, m.id, owner.id, _today(10))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 0
    assert calls == []
    # 混一场别人的：只算别人那一次
    other = _user(db, "someone")
    _play(db, m.id, other.id, _today(11))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 1
    assert "帮了 1 位朋友" in calls[0]["text"]
    assert "被看了 1 次" in calls[0]["text"]


def test_zero_plays_today_no_send(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, NOW_SH - timedelta(days=1))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 0
    assert calls == []


def test_repeat_run_no_resend(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, _today(9))
    first = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    second = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert len(calls) == 1
    rows = db.query(models.TgProactiveNotice).filter_by(
        kind="pool_daily_digest").all()
    assert len(rows) == 1
    assert rows[0].subject_id == 20261110
    # 台账行不许停在 pending（proactive 合并发送会误读它）
    assert rows[0].status != "pending"


def test_pref_off_skips(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111, tg_notify_pool_digest=False)
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, _today(9))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 0
    assert calls == []


def test_unbound_telegram_skips(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=None)
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, _today(9))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 0
    assert calls == []
    assert db.query(models.TgProactiveNotice).count() == 0


def test_exited_member_events_not_counted(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m = _member(db, owner.id, status="exited")
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, _today(9))
    res = pool_digest.push_daily_digests(db, now_sh=NOW_SH)
    assert res["sent"] == 0
    assert calls == []


def test_tick_waits_for_clock_and_switch(db, monkeypatch):
    calls = _stub_send(monkeypatch)
    owner = _user(db, "owner", tg_id=111)
    m = _member(db, owner.id)
    viewer = _user(db, "viewer")
    _play(db, m.id, viewer.id, _today(9))
    early = NOW_SH.replace(hour=20, minute=59)
    assert pool_digest.run_scheduled_tick(db, now_sh=early) == []
    assert calls == []
    actions = pool_digest.run_scheduled_tick(db, now_sh=NOW_SH)
    assert actions and len(calls) == 1
    _set_cfg(db, "bot_pool_digest_enabled", "false")
    assert pool_digest.run_scheduled_tick(db, now_sh=NOW_SH) == []
