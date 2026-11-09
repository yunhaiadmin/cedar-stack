"""回归测试：转存巡检（transfer_variant.patrol_once）验活抽样
（Drive 砍减第 6 项）。

钉死：
- 每行副本 6 小时内只验一次（last_checked_at 为空或早于
  now-6h 才到期）；刚验过的行本轮跳过，重启连跑也不重复打
  get_file。
- 每轮抽样上限 PATROL_BATCH 行（最旧的到期行优先），不再
  每轮全量 ready 行各打一次 get_file。
- 两次 strike 仍标 stale：第二轮用 now+7h 调用（行已到期）。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import transfer_variant as tv


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


_counter = [0]


def _item(db, **kw):
    _counter[0] += 1
    row = em.MediaItem(guid=f"pv-{_counter[0]}", library_id=1,
                       item_type="movie", name="巡检片",
                       drive_file_id="origfid",
                       file_path="mount://3/电影/a.mkv", size=1000)
    for key, value in kw.items():
        setattr(row, key, value)
    db.add(row)
    db.commit()
    return row


def _variant(db, item_id, fid="copyfid", status="ready",
             last_checked_at=None, stale_count=0):
    row = em.TransferVariant(item_id=item_id, source_file_id="origfid",
                             variant_file_id=fid, variant_drive_id="drive1",
                             owner_scope="user", owner_ref="1",
                             status=status, size_bytes=1000,
                             last_checked_at=last_checked_at,
                             stale_count=stale_count)
    db.add(row)
    db.commit()
    return row


class _StubDrive:
    """DriveApiClient 替身：get_file 成功/404 可配，调用留痕。"""

    def __init__(self, get_ok=True):
        self.get_ok = get_ok
        self.get_calls = []

    def get_file(self, drive_id, fid):
        self.get_calls.append((drive_id, fid))
        if not self.get_ok:
            raise tv._NotFound("gone")
        return {"id": fid}


def test_patrol_skips_recently_checked_row(db):
    """1 小时前刚验过的行本轮跳过，不打 get_file、不改 strike。"""
    item = _item(db)
    now = datetime.now()
    row = _variant(db, item.id, last_checked_at=now - timedelta(hours=1),
                   stale_count=1)
    stub = _StubDrive(get_ok=False)
    stats = tv.patrol_once(db, lambda: stub, now=now)
    assert stub.get_calls == []
    assert stats["checked"] == 0
    db.refresh(row)
    assert row.status == "ready" and row.stale_count == 1


def test_patrol_checks_due_row_after_6h(db):
    """7 小时前验过的行已到期，本轮复验（404 记一 strike）。"""
    item = _item(db)
    now = datetime.now()
    row = _variant(db, item.id, last_checked_at=now - timedelta(hours=7))
    stub = _StubDrive(get_ok=False)
    stats = tv.patrol_once(db, lambda: stub, now=now)
    assert len(stub.get_calls) == 1
    assert stats["checked"] == 1
    db.refresh(row)
    assert row.stale_count == 1
    assert row.last_checked_at is not None
    assert row.last_checked_at >= now - timedelta(seconds=5)


def test_patrol_two_strikes_across_due_rounds(db):
    """两次 strike 仍标 stale：第二轮在 7 小时后调用（行已到期）。"""
    item = _item(db)
    now = datetime.now()
    row = _variant(db, item.id)
    stub = _StubDrive(get_ok=False)
    tv.patrol_once(db, lambda: stub, now=now)
    db.refresh(row)
    assert row.status == "ready" and row.stale_count == 1
    tv.patrol_once(db, lambda: stub, now=now + timedelta(hours=7))
    db.refresh(row)
    assert row.status == "stale" and row.stale_count == 2


def test_patrol_batch_cap(db):
    """250 份到期副本，一轮最多验 PATROL_BATCH 份，最旧的优先。"""
    item = _item(db)
    old = datetime.now() - timedelta(days=2)
    rows = [_variant(db, item.id, fid=f"copyfid-{i}",
                     last_checked_at=old)
            for i in range(250)]
    stub = _StubDrive(get_ok=True)
    stats = tv.patrol_once(db, lambda: stub)
    assert stats["checked"] == tv.PATROL_BATCH
    assert len(stub.get_calls) == tv.PATROL_BATCH
    # 最旧的优先：被验的应为前 PATROL_BATCH 行（同 last_checked_at
    # 时按 id 升序）。
    checked_ids = {r.id for r in rows[:tv.PATROL_BATCH]}
    called_fids = {fid for _, fid in stub.get_calls}
    assert called_fids == {f"copyfid-{i}" for i in range(tv.PATROL_BATCH)}
    assert checked_ids  # 哨兵：行 id 确实取到了
