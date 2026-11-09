"""回归测试：池巡检（pool_janitor）验活抽样（Drive 砍减第 6 项）。

钉死：
- 死副本对账（reconcile）只验 6 小时内没验过的 ready 副本
  （last_checked_at 为空或早于 now-6h），刚验过的不重复打
  get_file；每轮最多 40 行（RECONCILE_BATCH）。
- 令牌探活（token_probe）每轮取 20 人，但窗口随轮次轮换，不再
  每轮只探 id 最小的 20 人（成员超过 20 后尾部成员永不被探）。
- 整轮 get_file 总量 ≤40（JANITOR_GET_FILE_BUDGET）：对账与残留
  收尾共用预算，对账吃满后残留收尾顺延到下一轮。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_service, models as base_models
from backend.emby_server import models as em
from backend.emby_server import pool_gate, pool_janitor
from backend.emby_server import transfer_variant as tv

FULL_SCOPES = (
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/drive.readonly")
ORIG_FID = "guardorigfid"


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _fake_token_ok(monkeypatch):
    """janitor 令牌探活默认打桩为可用。"""
    monkeypatch.setattr(
        pool_gate, "pool_member_access_token",
        lambda db, member_id: "token-ok")


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    return user


def _binding(db, user_id, status="active", provider="drive"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label="贡献盘",
        credential_id=cred.id, status=status,
        granted_scopes=FULL_SCOPES)
    db.add(binding)
    db.commit()
    return binding


def _member(db, user_id, status="active", **kw):
    binding_id = _binding(db, user_id).id
    row = em.PoolMember(
        binding_id=binding_id, user_id=user_id,
        provider="drive", status=status, **kw)
    db.add(row)
    db.commit()
    return row


def _item(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="抽样测试库")
    db.add(lib)
    db.commit()
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
        name="抽样片", drive_file_id=ORIG_FID,
        file_path="mount://3/电影/a.mkv", size=1000)
    db.add(item)
    db.commit()
    return item


def _variant(db, item_id, member_id, status="ready", stale_count=0,
             last_checked_at=None):
    row = em.TransferVariant(
        item_id=item_id, source_file_id=ORIG_FID,
        variant_file_id=f"pool-fid-{member_id}-{item_id}-"
                        f"{uuid.uuid4().hex[:6]}",
        variant_drive_id=f"pooldrive:{member_id}",
        owner_scope="pool", owner_ref=str(member_id), status=status,
        stale_count=stale_count, size_bytes=1000,
        last_checked_at=last_checked_at)
    db.add(row)
    db.commit()
    return row


class _FakeClient:
    """成员盘客户端替身：get/delete 行为可配，调用留痕。"""

    def __init__(self, *, get_error=None):
        self.get_error = get_error
        self.get_calls = []
        self.delete_calls = []

    def get_file(self, drive_id, fid):
        self.get_calls.append((drive_id, fid))
        if self.get_error is not None:
            raise self.get_error
        return {"id": fid}

    def delete_file_ignore_missing(self, drive_id, fid):
        self.delete_calls.append((drive_id, fid))


def _factory(client):
    return lambda db, binding: client


def test_reconcile_skips_recently_checked_rows(db):
    """刚验过的副本（1 小时前）本轮不再打 get_file；没验过的验。"""
    member = _member(db, _user(db, "rc1").id)
    item = _item(db)
    now = datetime.now()
    fresh = _variant(db, item.id, member.id,
                     last_checked_at=now - timedelta(hours=1))
    due = _variant(db, item.id, member.id, last_checked_at=None)
    fake = _FakeClient()
    stats = pool_janitor.run_pool_janitor_once(
        db, now=now, client_factory=_factory(fake))
    assert stats["reconcile"]["checked"] == 1
    assert len(fake.get_calls) == 1
    assert fake.get_calls[0][1] == due.variant_file_id
    db.refresh(fresh)
    assert fresh.last_checked_at == now - timedelta(hours=1)


def test_reconcile_due_rows_checked_after_6h(db):
    """7 小时前验过的副本已到期，本轮复验并刷新 last_checked_at。"""
    member = _member(db, _user(db, "rc2").id)
    item = _item(db)
    now = datetime.now()
    old = _variant(db, item.id, member.id,
                   last_checked_at=now - timedelta(hours=7))
    fake = _FakeClient()
    stats = pool_janitor.run_pool_janitor_once(
        db, now=now, client_factory=_factory(fake))
    assert stats["reconcile"]["checked"] == 1
    assert len(fake.get_calls) == 1
    db.refresh(old)
    assert old.last_checked_at is not None
    assert old.last_checked_at >= now - timedelta(seconds=5)


def test_reconcile_batch_cap_40(db):
    """45 份到期副本，一轮最多验 40 份（RECONCILE_BATCH），
    剩下的下一轮再验。"""
    member = _member(db, _user(db, "rc3").id)
    item = _item(db)
    for _ in range(45):
        _variant(db, item.id, member.id, last_checked_at=None)
    fake = _FakeClient()
    stats = pool_janitor.run_pool_janitor_once(
        db, client_factory=_factory(fake))
    assert stats["reconcile"]["checked"] == 40
    assert len(fake.get_calls) == 40


def test_token_probe_rotates_members(db, monkeypatch):
    """25 个成员时，两轮探活覆盖全部成员，不再每轮只探前 20 人。"""
    members = [_member(db, _user(db, f"tp{i}").id) for i in range(25)]
    seen_rounds: list[set] = []

    def _record_round(db_, member_id):
        seen_rounds[-1].add(member_id)
        return "token-ok"

    monkeypatch.setattr(pool_gate, "pool_member_access_token",
                        _record_round)
    now = datetime.now()
    seen_rounds.append(set())
    pool_janitor._sweep_token_probe(db, now)
    first = set(seen_rounds[-1])
    assert len(first) == 20
    seen_rounds.append(set())
    pool_janitor._sweep_token_probe(db, now + timedelta(seconds=1800))
    second = set(seen_rounds[-1])
    assert len(second) == 20
    assert second != first
    assert first | second == {m.id for m in members}


def test_janitor_round_get_file_budget_40(db):
    """整轮 get_file 总量 ≤40：对账吃满 40 后，残留收尾本轮
    不再打 get_file（顺延下一轮）。"""
    member = _member(db, _user(db, "gb1").id)
    item = _item(db)
    for _ in range(40):
        _variant(db, item.id, member.id, last_checked_at=None)
    gone = _member(db, _user(db, "gb2").id, status="exited")
    for _ in range(30):
        _variant(db, item.id, gone.id, status="released")
    fake = _FakeClient()
    stats = pool_janitor.run_pool_janitor_once(
        db, client_factory=_factory(fake))
    assert stats["reconcile"]["checked"] == 40
    assert stats["leftover_cleanup"]["cleaned"] == 0
    assert len(fake.get_calls) == 40
