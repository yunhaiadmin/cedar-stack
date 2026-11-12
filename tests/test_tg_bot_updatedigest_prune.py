"""TG「更新入库」播报已播记录保留期清理（tg_bot.updatedigest prune）测试。

口径：
- tg_update_digest_sent 只留近 90 天（去重回看窗口 14 天，90 天远
  大于窗口，删界不侵入窗口内行）。
- 清理分批删（每批 ≤ PRUNE_BATCH_SIZE 行、单轮 ≤ PRUNE_MAX_BATCHES
  批），挂在既有 updatedigest 调度 tick 上低频跑（24 小时一轮），
  不在播报主流程里同步删。
- 清理失败被吞掉记日志，绝不拖垮播报主流程。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

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
from backend.tg_bot import sender, updatedigest

GROUP_ID = -1001234567890
NOW = datetime(2026, 11, 12, 9, 30)

_seq = [1000]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for tbl in (
        models.SystemConfig.__table__,
        models.TgUpdateDigestSent.__table__,
        em.MediaItem.__table__,
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


def _sent(db, item_id, sent_at, kind="movie"):
    db.add(models.TgUpdateDigestSent(item_id=item_id, kind=kind, sent_at=sent_at))
    db.commit()


def _sent_ids(db):
    return {
        r.item_id
        for r in db.query(models.TgUpdateDigestSent).all()
    }


def test_prune_deletes_only_rows_older_than_retention(db):
    """91 天前的行删掉；10 天前、89 天前（窗口与保留期内）的行都在。"""
    _sent(db, 1, NOW - timedelta(days=91))
    _sent(db, 2, NOW - timedelta(days=200), kind="baseline")
    _sent(db, 3, NOW - timedelta(days=10))
    _sent(db, 4, NOW - timedelta(days=89))
    deleted = updatedigest.prune_sent_history(db, NOW)
    assert deleted == 2
    assert _sent_ids(db) == {3, 4}


def test_prune_batch_and_round_caps(db, monkeypatch):
    """每批 ≤ PRUNE_BATCH_SIZE、单轮 ≤ PRUNE_MAX_BATCHES 批：10 行旧
    记录、批大小 3、上限 2 批 → 本轮只删 6 行，剩 4 行下轮再删。"""
    monkeypatch.setattr(updatedigest, "PRUNE_BATCH_SIZE", 3)
    monkeypatch.setattr(updatedigest, "PRUNE_MAX_BATCHES", 2)
    for i in range(10):
        _sent(db, 100 + i, NOW - timedelta(days=120))
    deleted = updatedigest.prune_sent_history(db, NOW)
    assert deleted == 6
    assert len(_sent_ids(db)) == 4
    # 下一轮继续删剩余（批内未删完不丢）
    deleted2 = updatedigest.prune_sent_history(db, NOW)
    assert deleted2 == 4
    assert _sent_ids(db) == set()


def test_prune_noop_when_nothing_expired(db):
    _sent(db, 1, NOW - timedelta(days=1))
    assert updatedigest.prune_sent_history(db, NOW) == 0
    assert _sent_ids(db) == {1}


def test_tick_runs_prune_at_most_daily(db, monkeypatch):
    """调度 tick 挂清理：首拍删旧行并记动作；24 小时内第二拍不再清
    （低频），满 24 小时后才再清。"""
    _set_cfg(db, "bot_group_ids", str(GROUP_ID))
    _set_cfg(db, updatedigest.SEEDED_KEY, "1")
    _sent(db, 1, NOW - timedelta(days=91))
    actions = updatedigest.run_scheduled_tick(db, NOW)
    assert _sent_ids(db) == set()
    assert any(a.startswith("digest:pruned:1") for a in actions)
    # 24 小时内：新攒的旧行（模拟跨日攒批）不被第二拍清掉
    _sent(db, 2, NOW - timedelta(days=95))
    updatedigest.run_scheduled_tick(db, NOW + timedelta(hours=23))
    assert _sent_ids(db) == {2}
    # 满 24 小时：再清
    updatedigest.run_scheduled_tick(db, NOW + timedelta(hours=25))
    assert _sent_ids(db) == set()


def test_prune_failure_does_not_break_digest_tick(db, monkeypatch):
    """清理抛异常被吞：同一拍的播报主流程照常推送、照常返回动作。"""
    _set_cfg(db, "bot_group_ids", str(GROUP_ID))
    _set_cfg(db, updatedigest.SEEDED_KEY, "1")
    _seq[0] += 1
    db.add(em.MediaItem(
        guid=f"{_seq[0]:032x}", library_id=58, item_type="movie",
        name="清理坏了也要播", production_year=2026, tmdb_id="991",
        date_added=NOW - timedelta(hours=1),
    ))
    db.commit()
    sent = {"messages": []}

    def fake_message(db_, chat_id, text, reply_markup=None, parse_mode="HTML"):
        sent["messages"].append({"chat_id": chat_id, "text": text})
        return True, None

    monkeypatch.setattr(sender, "send_message", fake_message)
    monkeypatch.setattr(sender, "send_photo", lambda *a, **k: (True, None))

    def boom(db_, now=None):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(updatedigest, "prune_sent_history", boom)
    actions = updatedigest.run_scheduled_tick(db, NOW)
    assert actions == ["digest:ok"]
    assert len(sent["messages"]) == 1
    assert "清理坏了也要播" in sent["messages"][0]["text"]
