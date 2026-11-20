"""回归测试：公共池优化（2026-11-21）。

钉死两件事：
- 热门补池（playrank 信号）：巡检补位按 emby_play_start_events
  近 7 天点播排行给「从没进过池」的热门条目补缺副本，走既有
  refill 建单口；开关/取数上限走 janitor 配置键，默认开；
  已有可下发副本的不重复补；窗外点播不算。
- insufficient_space 换盘：池单空间不足终态后，排除已满盘
  换一个成员盘重建；试完所有可用盘 / 达配置上限才真终态。
  （copy_forbidden 的等价路径是既有 _after_pool_copy_forbidden
  + try_pool_forbidden_recover，本文件不重复钉。）
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
from backend.emby_server import transfer_decisions as td
from backend.emby_server import transfer_variant as tv
from backend.integrations import store

FULL_SCOPES = (
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/drive.readonly")
ORIG_FID = "optimize-orig-fid"
SRC_DRIVE = "drive1"


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
    monkeypatch.setattr(
        pool_gate, "pool_member_access_token",
        lambda db, member_id: "token-ok")


@pytest.fixture(autouse=True)
def _note_here(db_factory, monkeypatch):
    monkeypatch.setattr(td, "SessionLocal", db_factory)


@pytest.fixture(autouse=True)
def _transfer_master_on(monkeypatch):
    monkeypatch.setenv("TRANSFER_VARIANT_ENABLED", "1")


@pytest.fixture(autouse=True)
def _clean_caches():
    yield
    store.invalidate()
    pool_gate.clear_pool_quota_cache()


def _set_pool(db, **values):
    config_service.save_config(db, values, schema="pool")
    db.commit()


def _set_janitor(db, **values):
    config_service.save_config(db, values, schema="pool_janitor")
    db.commit()


def _set_retry(db, **values):
    config_service.save_config(db, values, schema="pool_retry")
    db.commit()


def _quota_ok(db, binding):
    return {"limit": 5 * (1024 ** 4), "usage": 1 * (1024 ** 4),
            "free": 4 * (1024 ** 4)}


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


def _member(db, user_id, status="active"):
    binding_id = _binding(db, user_id).id
    row = em.PoolMember(
        binding_id=binding_id, user_id=user_id,
        provider="drive", status=status)
    db.add(row)
    db.commit()
    return row


def _mount(db, mount_id=3, path="drive:drive1"):
    existing = (db.query(em.StorageMount)
                .filter(em.StorageMount.id == mount_id).first())
    if existing is not None:
        return existing
    row = em.StorageMount(id=mount_id, name=f"m{mount_id}",
                          mount_type="drive", path=path)
    db.add(row)
    db.commit()
    return row


def _item(db, *, name="优化片", size=1_000_000):
    _mount(db)
    lib = (db.query(em.Library).first()
           or em.Library(guid=uuid.uuid4().hex, name="优化测试库"))
    if lib.id is None:
        db.add(lib)
        db.commit()
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
        name=name,
        drive_file_id=f"{ORIG_FID}-{uuid.uuid4().hex[:6]}",
        file_path="mount://3/电影/a.mkv", size=size)
    db.add(item)
    db.commit()
    return item


def _variant(db, item_id, member_id, status="ready"):
    row = em.TransferVariant(
        item_id=item_id, source_file_id=ORIG_FID,
        variant_file_id=f"pool-fid-{member_id}-{item_id}-"
                        f"{uuid.uuid4().hex[:6]}",
        variant_drive_id=f"pooldrive:{member_id}",
        owner_scope="pool", owner_ref=str(member_id), status=status,
        size_bytes=1000)
    db.add(row)
    db.commit()
    return row


def _starts(db, item_id, count, *, days_ago=0):
    for _ in range(count):
        db.add(em.PlayStartEvent(
            user_id=1, item_id=item_id, provider="drive",
            method="resolve", result="ok",
            created_at=datetime.now() - timedelta(days=days_ago)))
    db.commit()


def _pool_job(db, item_id, member_id, *, status="failed",
              trigger="playback", error_kind=None):
    job = em.TransferJob(
        item_id=item_id, trigger=trigger, drive_id=SRC_DRIVE,
        provider="drive", owner_scope="pool", owner_ref=str(member_id),
        dest_drive_id=f"pooldrive:{member_id}", status=status,
        error_kind=error_kind)
    db.add(job)
    db.commit()
    return job


def _refill_jobs(db):
    return (db.query(em.TransferJob)
            .filter(em.TransferJob.owner_scope == "pool",
                    em.TransferJob.trigger == "refill")
            .order_by(em.TransferJob.id.asc()).all())


def _pool_enabled(db):
    _set_pool(db, pool_enabled=True, pool_global_concurrency=10)


def _sweep(db):
    return pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)

# ------------------------------------------------------------ playrank：配置


def test_playrank_config_defaults(db):
    """点播排行补池默认开、排行取前 100：默认开是用户口径
    （功能默认开），100 覆盖实测热门量级又不至于整库扫。"""
    payload = pool_gate.janitor_config_payload(db)
    assert payload["pool_refill_playrank_enabled"] is True
    assert payload["pool_refill_playrank_top_n"] == 100

# ------------------------------------------------------------ playrank：补单


def test_playrank_refills_hot_items_by_start_rank(db):
    """近 7 天点播多的条目优先补池：补单顺序按点播数降序，
    信号记 playrank，走 refill 建单口。"""
    _pool_enabled(db)
    _member(db, _user(db, "pr-a").id)
    hot = _item(db, name="热门片")
    warm = _item(db, name="次热片")
    _starts(db, hot.id, 5)
    _starts(db, warm.id, 3)
    stats = _sweep(db)
    assert stats["by_signal"]["playrank"] == 2
    assert [j.item_id for j in _refill_jobs(db)] == [hot.id, warm.id]


def test_playrank_respects_round_cap_and_rank_order(db):
    """每轮上限沿用 pool_refill_max_per_round：上限 1 时只补
    点播最多的那个，不按建单顺序乱补。"""
    _pool_enabled(db)
    _set_janitor(db, pool_refill_max_per_round=1)
    _member(db, _user(db, "pr-b").id)
    warm = _item(db, name="次热片")
    hot = _item(db, name="热门片")
    _starts(db, warm.id, 3)
    _starts(db, hot.id, 5)
    stats = _sweep(db)
    assert stats["created"] == 1
    assert [j.item_id for j in _refill_jobs(db)] == [hot.id]


def test_playrank_skips_item_with_ready_copy(db):
    """已有可下发池副本的热门条目不重复补（目标 1 份已达）。"""
    _pool_enabled(db)
    member = _member(db, _user(db, "pr-c").id)
    item = _item(db)
    _variant(db, item.id, member.id)
    _starts(db, item.id, 5)
    stats = _sweep(db)
    assert stats["created"] == 0
    assert _refill_jobs(db) == []


def test_playrank_ignores_events_outside_window(db):
    """窗外（8 天前）点播不算热：排行窗与既有热播信号同为
    近 7 天，老片翻红前不占补位名额。"""
    _pool_enabled(db)
    _member(db, _user(db, "pr-d").id)
    item = _item(db)
    _starts(db, item.id, 5, days_ago=8)
    stats = _sweep(db)
    assert stats["created"] == 0
    assert _refill_jobs(db) == []


def test_playrank_disabled_by_config(db):
    """配置关时不动作：排行再热也不补单（其余信号不受影响
    的口径由 layer2 测试钉，本条只钉 playrank 开关本身）。"""
    _pool_enabled(db)
    _set_janitor(db, pool_refill_playrank_enabled=False)
    _member(db, _user(db, "pr-e").id)
    item = _item(db)
    _starts(db, item.id, 5)
    stats = _sweep(db)
    assert stats["created"] == 0
    assert stats["by_signal"]["playrank"] == 0
    assert _refill_jobs(db) == []

# ------------------------------------------------------------ 空间换盘：配置


def test_space_swap_config_default(db):
    """换盘尝试上限默认 0 = 以试完可用盘为界（与 copy_forbidden
    的 pool_copy_forbidden_max_attempts 同口径）。"""
    assert pool_gate.retry_config_payload(db)[
        "pool_insufficient_space_max_attempts"] == 0

# ------------------------------------------------------------ 空间换盘：行为


def test_space_swap_rebuilds_on_other_member(db):
    """A 盘空间不足终态后换盘重建：新单落到没试过的 B 盘，
    状态 pending、归属池。"""
    _pool_enabled(db)
    ma = _member(db, _user(db, "sp-a").id)
    mb = _member(db, _user(db, "sp-b").id)
    item = _item(db)
    _pool_job(db, item.id, ma.id, error_kind="insufficient_space")
    job = pool_gate.try_pool_space_swap(db, item, quota_fn=_quota_ok)
    assert job is not None
    assert job.owner_ref == str(mb.id)
    assert job.dest_drive_id == f"pooldrive:{mb.id}"
    assert job.status == "pending"


def test_space_swap_exhausted_returns_none(db):
    """唯一成员盘已因空间不足失败过：试完所有可用盘 → 返回
    None 落终态，不再无界重建。"""
    _pool_enabled(db)
    ma = _member(db, _user(db, "sp-c").id)
    item = _item(db)
    _pool_job(db, item.id, ma.id, error_kind="insufficient_space")
    assert pool_gate.try_pool_space_swap(
        db, item, quota_fn=_quota_ok) is None
    assert db.query(em.TransferJob).count() == 1


def test_space_swap_max_attempts_cap(db):
    """配置上限 1：已有 1 次空间失败即到顶，别的盘再空也不
    重建（防成员名单 churn 让同一条目无界换盘）。"""
    _pool_enabled(db)
    _set_retry(db, pool_insufficient_space_max_attempts=1)
    ma = _member(db, _user(db, "sp-d").id)
    _member(db, _user(db, "sp-e").id)
    item = _item(db)
    _pool_job(db, item.id, ma.id, error_kind="insufficient_space")
    assert pool_gate.try_pool_space_swap(
        db, item, quota_fn=_quota_ok) is None
    assert db.query(em.TransferJob).count() == 1


def test_after_pool_insufficient_space_rebuilds(db):
    """工人后事接线：终态后事函数自动换盘重建一单（不等观众
    下次点播），且无盘可换时不抛异常、不建单。"""
    _pool_enabled(db)
    ma = _member(db, _user(db, "sp-f").id)
    mb = _member(db, _user(db, "sp-g").id)
    item = _item(db)
    job = _pool_job(db, item.id, ma.id,
                    error_kind="insufficient_space")
    tv._after_pool_insufficient_space(db, job, item)
    pend = (db.query(em.TransferJob)
            .filter(em.TransferJob.owner_scope == "pool",
                    em.TransferJob.status == "pending").all())
    assert len(pend) == 1
    assert pend[0].owner_ref == str(mb.id)


def test_after_pool_insufficient_space_no_member_left(db):
    """后事无盘可换：静默收口（只记日志），不抛、不建单。"""
    _pool_enabled(db)
    ma = _member(db, _user(db, "sp-h").id)
    item = _item(db)
    job = _pool_job(db, item.id, ma.id,
                    error_kind="insufficient_space")
    tv._after_pool_insufficient_space(db, job, item)
    assert db.query(em.TransferJob).count() == 1
