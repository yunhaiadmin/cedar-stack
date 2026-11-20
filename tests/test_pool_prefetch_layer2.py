"""回归测试：公共池等待优化第二层（热门预热）。

钉死：
- 成员×源盘失败记忆：池单 copy_forbidden 失败即记（成员×源盘
  粒度）；refill 选盘跳过已记组合；成功清、过期失效；只记
  copy_forbidden（源文件没了是条目级问题，不毒化整条源盘）。
- 热门预热信号：追剧 > 求片 > 热播（丢副本补位）；求片指到
  剧行时只预热首集 + 最新集，不整季扫；预热目标份数与每轮
  上限走 janitor 配置键。
- 补位只吃闲置产能：队列忙时整步跳过；单条目失败不拖垮整轮。
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
ORIG_FID = "prefetch-orig-fid"
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
    """决策留痕写进本测试库（照 test_pool_guard 口径）。"""
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


def _item(db, *, item_type="movie", name="预热片", fid=None,
          series_id=None, season=None, episode=None, date_added=None,
          size=1_000_000):
    _mount(db)
    lib = (db.query(em.Library).first()
           or em.Library(guid=uuid.uuid4().hex, name="预热测试库"))
    if lib.id is None:
        db.add(lib)
        db.commit()
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, drive_file_id=fid or f"{ORIG_FID}-{uuid.uuid4().hex[:6]}",
        file_path="mount://3/电影/a.mkv", size=size,
        series_id=series_id, season_number=season,
        episode_number=episode, date_added=date_added)
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


def _play(db, member_id, variant_id, item_id, *, days_ago=0):
    db.add(em.PoolPlayEvent(
        member_id=member_id, variant_id=variant_id, item_id=item_id,
        viewer_user_id=4242,
        created_at=datetime.now() - timedelta(days=days_ago)))
    db.commit()


def _pool_job(db, item_id, member_id, *, status="pending",
              trigger="playback", drive_id=SRC_DRIVE,
              error_kind=None):
    job = em.TransferJob(
        item_id=item_id, trigger=trigger, drive_id=drive_id,
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

# ------------------------------------------------------------ 失败记忆：配置


def test_bad_combo_ttl_default_7_days(db):
    """失败记忆 TTL 默认 7 天：覆盖跨天重复派单的观测窗口，
    成功即清所以 TTL 只影响无人修好的组合；每周重探一次。"""
    assert pool_gate.retry_config_payload(db)[
        "pool_bad_combo_ttl_days"] == 7


def test_refill_config_defaults(db):
    """补位上限与预热目标份数默认值：每轮 10 单、预热目标 1 份。
    10 单/轮 × 48 轮/天可在一日内清掉实测约 200 项的求片缺口，
    又远低于成员日字节上限；1 份即消除首播等待，第二份由
    丢副本补位按 pool_item_max_copies 另行补。"""
    payload = pool_gate.janitor_config_payload(db)
    assert payload["pool_refill_max_per_round"] == 10
    assert payload["pool_refill_prefetch_target_copies"] == 1


# ------------------------------------------------------------ 失败记忆：记/不记/清


def test_bad_combo_recorded_on_copy_forbidden(db):
    member = _member(db, _user(db, "bc-a").id)
    item = _item(db)
    job = _pool_job(db, item.id, member.id, status="running")
    tv._fail_job(db, job, "copy_forbidden", "not shared")
    row = (db.query(em.PoolBadDriveCombo)
           .filter(em.PoolBadDriveCombo.member_id == member.id,
                   em.PoolBadDriveCombo.source_drive_id == SRC_DRIVE)
           .first())
    assert row is not None
    assert row.failure_count == 1
    assert row.last_error_kind == "copy_forbidden"
    assert row.last_item_id == item.id
    assert row.expires_at > datetime.now() + timedelta(days=6)
    # 留痕：transfer_decisions 可查这次记忆。
    notes = (db.query(em.TransferDecisionRound)
             .filter(em.TransferDecisionRound.scope == "pool_bad_combo")
             .all())
    assert len(notes) == 1


def test_bad_combo_not_recorded_for_other_kind(db):
    member = _member(db, _user(db, "bc-b").id)
    item = _item(db)
    job = _pool_job(db, item.id, member.id, status="running")
    tv._fail_job(db, job, "retriable", "transient")
    assert db.query(em.PoolBadDriveCombo).count() == 0


def test_bad_combo_not_recorded_for_user_scope(db):
    member = _member(db, _user(db, "bc-c").id)
    item = _item(db)
    job = em.TransferJob(
        item_id=item.id, trigger="playback", drive_id=SRC_DRIVE,
        provider="drive", owner_scope="user", owner_ref="9",
        dest_drive_id="userdrive:9", status="running")
    db.add(job)
    db.commit()
    tv._fail_job(db, job, "copy_forbidden", "not shared")
    assert db.query(em.PoolBadDriveCombo).count() == 0


def test_bad_combo_cleared_on_success(db):
    member = _member(db, _user(db, "bc-d").id)
    item = _item(db)
    pool_gate.record_bad_drive_combo(
        db, member.id, SRC_DRIVE, "copy_forbidden", item.id)
    assert db.query(em.PoolBadDriveCombo).count() == 1
    job = _pool_job(db, item.id, member.id, status="running")
    tv._register_ready_variant(
        db, job, item, "new-fid", f"pooldrive:{member.id}")
    assert db.query(em.PoolBadDriveCombo).count() == 0
    db.refresh(job)
    assert job.status == "done"

# ------------------------------------------------------------ 失败记忆：选盘跳过


def _pool_enabled(db):
    _set_pool(db, pool_enabled=True, pool_global_concurrency=10)


def test_pick_skips_bad_combo_member(db):
    _pool_enabled(db)
    bad = _member(db, _user(db, "pk-a").id)
    good = _member(db, _user(db, "pk-b").id)
    item = _item(db)
    pool_gate.record_bad_drive_combo(
        db, bad.id, SRC_DRIVE, "copy_forbidden", item.id)
    member, candidates = pool_gate.pick_pool_member(
        db, item, quota_fn=_quota_ok, apply_bad_combo_memory=True)
    assert member is not None
    assert int(member.id) == int(good.id)
    bad_cand = next(c for c in candidates
                    if c["member_id"] == int(bad.id))
    assert bad_cand["eligible"] is False
    assert bad_cand["reason"] == "bad_drive_combo"


def test_pick_bad_combo_expired_member_eligible_again(db):
    _pool_enabled(db)
    member = _member(db, _user(db, "pk-c").id)
    item = _item(db)
    pool_gate.record_bad_drive_combo(
        db, member.id, SRC_DRIVE, "copy_forbidden", item.id)
    row = db.query(em.PoolBadDriveCombo).first()
    row.expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    picked, candidates = pool_gate.pick_pool_member(
        db, item, quota_fn=_quota_ok, apply_bad_combo_memory=True)
    assert picked is not None
    assert int(picked.id) == int(member.id)


def test_pick_without_memory_flag_ignores_bad_combo(db):
    """记忆只约束 refill：点播建单不走记忆过滤（换源流程另有
    forbidden 换盘口径管点播侧），缺省行为与旧版一致。"""
    _pool_enabled(db)
    member = _member(db, _user(db, "pk-d").id)
    item = _item(db)
    pool_gate.record_bad_drive_combo(
        db, member.id, SRC_DRIVE, "copy_forbidden", item.id)
    picked, _c = pool_gate.pick_pool_member(
        db, item, quota_fn=_quota_ok)
    assert picked is not None
    assert int(picked.id) == int(member.id)


# ------------------------------------------------------------ refill：坏组合整步


def test_refill_skips_all_bad_combo(db):
    _pool_enabled(db)
    member = _member(db, _user(db, "rf-a").id)
    item = _item(db)
    _variant(db, item.id, member.id, status="stale")
    _play(db, member.id, 1, item.id)
    pool_gate.record_bad_drive_combo(
        db, member.id, SRC_DRIVE, "copy_forbidden", item.id)
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["created"] == 0
    assert stats["skipped_bad_combo"] == 1
    assert _refill_jobs(db) == []

# ------------------------------------------------------------ refill：信号排序与窄切口


def _follow_setup(db):
    """追剧信号：一部剧 + 订阅 + 订阅后到货的新集。"""
    user = _user(db, "fol-u")
    series = _item(db, item_type="series", name="追剧")
    follow = em.SeriesFollow(
        user_id=user.id, series_item_id=series.id, status="active",
        created_at=datetime.now() - timedelta(days=1))
    db.add(follow)
    db.commit()
    ep = _item(db, item_type="episode", name="追剧S1E1",
               series_id=series.id, season=1, episode=1,
               date_added=datetime.now())
    return series, follow, ep


def test_refill_signal_order_follow_seek_hot(db):
    _pool_enabled(db)
    _member(db, _user(db, "ord-a").id)
    _member(db, _user(db, "ord-b").id)
    _series, _follow, follow_ep = _follow_setup(db)
    seek_user = _user(db, "ord-seek")
    seek_item = _item(db, name="求片电影")
    db.add(base_models.MovieRequest(
        user_id=seek_user.id, movie_name="求片电影", status="pending",
        request_kind="seek", tmdb_id="999",
        emby_item_id=str(seek_item.id)))
    db.commit()
    hot = _item(db, name="热播片")
    kept = _variant(db, hot.id, 1)
    _variant(db, hot.id, 2, status="stale")
    _play(db, 1, kept.id, hot.id)
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["created"] == 3
    assert stats["by_signal"] == {"follow": 1, "seek": 1, "hot": 1,
                                  "playrank": 0}
    assert [j.item_id for j in _refill_jobs(db)] == [
        follow_ep.id, seek_item.id, hot.id]


def test_refill_seek_series_narrow_cut(db):
    """求片指到剧行：只取首集 + 最新集，不整季扫（生产实测
    待办剧集级缺口 6,417 集，全量预热不可行）。"""
    _pool_enabled(db)
    _member(db, _user(db, "nar-a").id)
    series = _item(db, item_type="series", name="求片剧")
    eps = [_item(db, item_type="episode", name=f"求片剧S1E{i}",
                 series_id=series.id, season=1, episode=i,
                 date_added=datetime.now() - timedelta(days=5 - i))
           for i in range(1, 6)]
    seek_user = _user(db, "nar-seek")
    db.add(base_models.MovieRequest(
        user_id=seek_user.id, movie_name="求片剧", status="approved",
        request_kind="seek", tmdb_id="555",
        emby_item_id=str(series.id)))
    db.commit()
    cands = pool_janitor._refill_candidates(
        db, pool_gate.pool_config_payload(db), datetime.now())
    seek_items = [c["item"].id for c in cands if c["signal"] == "seek"]
    assert seek_items == [eps[0].id, eps[-1].id]


def test_refill_prefetch_target_respected(db):
    """预热目标 1 份：已有 1 份可下发副本的追剧集不再预热；
    但丢过副本的热播片仍按 pool_item_max_copies=2 补满。"""
    _pool_enabled(db)
    ma = _member(db, _user(db, "tgt-a").id)
    _member(db, _user(db, "tgt-b").id)
    _series, _follow, follow_ep = _follow_setup(db)
    _variant(db, follow_ep.id, ma.id)
    hot = _item(db, name="热播片2")
    kept = _variant(db, hot.id, ma.id)
    _variant(db, hot.id, ma.id, status="stale")
    _play(db, ma.id, kept.id, hot.id)
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["created"] == 1
    assert [j.item_id for j in _refill_jobs(db)] == [hot.id]


def test_refill_cap_from_config(db):
    _pool_enabled(db)
    _set_janitor(db, pool_refill_max_per_round=1)
    _member(db, _user(db, "cap-a").id)
    _series, _follow, _ep = _follow_setup(db)
    seek_user = _user(db, "cap-seek")
    seek_item = _item(db, name="求片电影2")
    db.add(base_models.MovieRequest(
        user_id=seek_user.id, movie_name="求片电影2", status="pending",
        request_kind="seek", tmdb_id="998",
        emby_item_id=str(seek_item.id)))
    db.commit()
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["created"] == 1
    assert len(_refill_jobs(db)) == 1

# ------------------------------------------------------------ refill：闲置产能与隔离


def test_refill_busy_queue_skipped(db):
    _pool_enabled(db)
    member = _member(db, _user(db, "bsy-a").id)
    _series, _follow, _ep = _follow_setup(db)
    db.add(em.TransferJob(
        item_id=999999, trigger="playback", provider="drive",
        owner_scope="user", owner_ref="7", status="pending"))
    db.commit()
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["skipped_busy"] == 1
    assert stats["created"] == 0
    assert _refill_jobs(db) == []


def test_refill_item_failure_isolated(db, monkeypatch):
    """单条目建单抛非 TransferError 也只记 blocked、不杀整步。"""
    _pool_enabled(db)
    _member(db, _user(db, "iso-a").id)
    _series, _follow, follow_ep = _follow_setup(db)
    seek_user = _user(db, "iso-seek")
    seek_item = _item(db, name="求片电影3")
    db.add(base_models.MovieRequest(
        user_id=seek_user.id, movie_name="求片电影3", status="pending",
        request_kind="seek", tmdb_id="997",
        emby_item_id=str(seek_item.id)))
    db.commit()
    real_create = pool_gate.create_pool_job

    def _flaky(db_, item, **kw):
        if item.id == follow_ep.id:
            raise RuntimeError("boom")
        return real_create(db_, item, **kw)

    monkeypatch.setattr(pool_gate, "create_pool_job", _flaky)
    stats = pool_janitor._sweep_refill(
        db, pool_gate.pool_config_payload(db), datetime.now(),
        quota_fn=_quota_ok)
    assert stats["created"] == 1
    assert stats["blocked"] == 1
    assert [j.item_id for j in _refill_jobs(db)] == [seek_item.id]


def test_refill_step_failure_does_not_break_round(db, monkeypatch):
    """补位整步抛错：巡检轮照常收尾，其余步不受影响。"""
    _pool_enabled(db)
    member = _member(db, _user(db, "brk-a").id)
    item = _item(db)
    stale = _variant(db, item.id, member.id, status="stale")
    _play(db, member.id, stale.id, item.id)

    def _boom(*args, **kwargs):
        raise RuntimeError("refill boom")

    monkeypatch.setattr(pool_janitor, "_sweep_refill", _boom)
    stats = pool_janitor.run_pool_janitor_once(db, quota_fn=_quota_ok)
    assert stats["refill"] == {"error": True}
    assert "breaker_trips" in stats
    assert "reconcile" in stats
