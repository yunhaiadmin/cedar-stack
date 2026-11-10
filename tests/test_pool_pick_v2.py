# -*- coding: utf-8 -*-
"""公共贡献池选盘打分 v2（用户 2026-11-10 亲点全做）：五项加权
（容量/日字节余量/今日负载/健康度/新盘扶持）+ 副本分散硬规矩 +
播出选副本 v2（容量 + 今日已播负载）。

口径（与 pool_gate 实现注释同文）：
- 今日负载当量字节 = transfer_bytes + play_starts × 4GiB，负载比
  = 当量 / 日字节上限（上限 0 改用成员容量上限），负载分 = 1 − 比；
- 健康度：近 7 天不利点 = 熔断×3 + 失败转存×1 + 额度命中×0.25，
  健康度 = 1 − min(点数/10, 1)；近 7 天三源全无数据 → 0.8 中性；
- 新盘扶持：转 active 未满 pool_pick_new_member_boost_days 天，
  扶持分 = 1 − 已满天数/扶持天数（线性衰减，满期 0）；
- 分散：条目已有副本（可下发 ready 或在途池单）挂在贡献者 U
  名下且有 U 以外 eligible 成员时，候选收窄到 U 以外。

全部离线：SQLite + 假配额函数；真实 Drive 不碰。夹具沿
test_pool_intake 口径自带一份，不跨文件 import。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_service, models as base_models
from backend.emby_server import models as em
from backend.emby_server import pool_gate
from backend.emby_server import transfer_decisions as td
from backend.integrations import store

GIB = 1024 ** 3
TB = 1000 ** 4
ORIG_FID = "origfid"


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
def _note_here(db_factory, monkeypatch):
    monkeypatch.setattr(td, "SessionLocal", db_factory)


@pytest.fixture(autouse=True)
def _clean_store_cache():
    yield
    store.invalidate()


@pytest.fixture(autouse=True)
def _clean_quota_cache():
    pool_gate.clear_pool_quota_cache()
    yield
    pool_gate.clear_pool_quota_cache()


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
        user_id=user_id, provider=provider,
        # 绑定表 (user_id, provider, account_label) 唯一：一人多盘
        # 靠标签区分，夹具给每块盘一个唯一标签。
        account_label=f"贡献盘{uuid.uuid4().hex[:6]}",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _mount(db, mount_id=3):
    existing = (db.query(em.StorageMount)
                .filter(em.StorageMount.id == mount_id).first())
    if existing is not None:
        return existing
    row = em.StorageMount(id=mount_id, name=f"m{mount_id}",
                          mount_type="drive", path="drive:drive1")
    db.add(row)
    db.commit()
    return row


def _item(db, size=1_000_000):
    _mount(db)
    lib = em.Library(guid=uuid.uuid4().hex, name="池进片库")
    db.add(lib)
    db.commit()
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
        name="池测试片", drive_file_id=ORIG_FID,
        file_path="mount://3/电影/a.mkv", size=size)
    db.add(item)
    db.commit()
    return item


def _member(db, user_id, status="active", capacity_cap_bytes=None,
            joined_days_ago=0.0):
    binding = _binding(db, user_id)
    joined = datetime.now() - timedelta(days=joined_days_ago)
    row = em.PoolMember(
        binding_id=binding.id, user_id=user_id, provider="drive",
        status=status, capacity_cap_bytes=capacity_cap_bytes,
        joined_at=joined)
    db.add(row)
    db.commit()
    return row


def _set_pool(db, **values):
    config_service.save_config(db, values, schema="pool")
    db.commit()


def _only_weight(db, key):
    """只点亮一项权重（其余选盘权重清零），隔离验单项分量。"""
    values = {k: 0 for k in (
        "pool_pick_weight_capacity", "pool_pick_weight_daily",
        "pool_pick_weight_load", "pool_pick_weight_health",
        "pool_pick_weight_new")}
    values[key] = 100
    _set_pool(db, **values)


def _quota_ok(db, binding):
    return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}


def _quota_free(free_bytes):
    def _fn(db, binding):
        return {"limit": 5 * TB, "usage": 5 * TB - free_bytes,
                "free": free_bytes}
    return _fn


def _usage(db, member_id, day_offset=0, transfer_jobs=0,
           transfer_bytes=0, play_starts=0, quota_hits=0):
    row = em.PoolUsageDaily(
        member_id=member_id,
        day=date.today() - timedelta(days=day_offset),
        transfer_jobs=transfer_jobs, transfer_bytes=transfer_bytes,
        play_starts=play_starts, quota_hits=quota_hits)
    db.add(row)
    db.commit()
    return row


def _job(db, item_id, member_id, status="pending", created_at=None):
    row = em.TransferJob(
        item_id=item_id, trigger="playback", drive_id="drive1",
        provider="drive", owner_scope="pool", owner_ref=str(member_id),
        dest_drive_id=f"pooldrive:{member_id}", status=status)
    db.add(row)
    db.flush()
    if created_at is not None:
        row.created_at = created_at
        row.updated_at = created_at
        row.cooldown_until = created_at + timedelta(seconds=86400)
    db.commit()
    return row


def _variant(db, item_id, member_id, status="ready", stale_count=0,
             fid="poolcopy"):
    row = em.TransferVariant(
        item_id=item_id, source_file_id=ORIG_FID, variant_file_id=fid,
        variant_drive_id=f"pooldrive:{member_id}", owner_scope="pool",
        owner_ref=str(member_id), status=status, size_bytes=1_000_000)
    db.add(row)
    db.flush()
    if stale_count:
        row.stale_count = stale_count
    db.commit()
    return row


def _score(db, member, item, quota_fn=_quota_ok):
    cfg = pool_gate.pool_config_payload(db)
    eligible, score, detail = pool_gate._score_member(
        db, member, item, cfg, quota_fn=quota_fn)
    assert eligible, detail
    return score, detail


def _detail_of(candidates, member_id):
    return next(c for c in candidates
                if c["member_id"] == member_id)


# ---------------------------------------------------------------- 今日负载


def test_load_component_penalizes_busy_member(db):
    item = _item(db)
    idle = _member(db, 11, joined_days_ago=30)
    busy = _member(db, 12, joined_days_ago=30)
    # 忙盘：今日已转存 300GiB（日限 600GiB）→ 当量比 0.5
    _usage(db, busy.id, transfer_bytes=300 * GIB)
    _only_weight(db, "pool_pick_weight_load")
    _, idle_detail = _score(db, idle, item)
    _, busy_detail = _score(db, busy, item)
    assert idle_detail["load_ratio"] == 0.0
    assert idle_detail["load_score"] == 1.0
    assert busy_detail["load_ratio"] == pytest.approx(0.5)
    assert busy_detail["load_score"] == pytest.approx(0.5)
    # 只点亮负载权重时，总分就是负载分：忙盘严格低于闲盘
    idle_score, _ = _score(db, idle, item)
    busy_score, _ = _score(db, busy, item)
    assert busy_score < idle_score


def test_load_counts_play_starts_as_bytes_equiv(db):
    item = _item(db)
    member = _member(db, 11, joined_days_ago=30)
    # 75 次起播 × 4GiB 当量 = 300GiB → 与转存 300GiB 同比 0.5
    _usage(db, member.id, play_starts=75)
    _, detail = _score(db, member, item)
    assert detail["load_ratio"] == pytest.approx(0.5)


# ---------------------------------------------------------------- 健康度


def test_health_penalty_and_no_data_neutral(db):
    item = _item(db)
    clean = _member(db, 11, joined_days_ago=30)
    bad = _member(db, 12, joined_days_ago=30)
    fresh = _member(db, 13, joined_days_ago=30)
    # 干净盘：有数据（今日用量行全零）→ 无不利点，健康 1.0
    _usage(db, clean.id)
    # 问题盘：熔断 1 次（3 点）+ 失败转存 1 单（1 点）+ 近 7 天
    # 额度命中 8 次（2 点）= 6 点 → 健康 1 − 6/10 = 0.4
    db.add(em.PoolMemberStatusEvent(
        member_id=bad.id, from_status="active", to_status="suspended",
        reason="breaker"))
    _job(db, item.id, bad.id, status="failed")
    _usage(db, bad.id, quota_hits=5)
    _usage(db, bad.id, day_offset=3, quota_hits=3)
    # 无数据盘：三源皆无 → 中性 0.8，不判死
    _only_weight(db, "pool_pick_weight_health")
    _, clean_detail = _score(db, clean, item)
    _, bad_detail = _score(db, bad, item)
    _, fresh_detail = _score(db, fresh, item)
    assert clean_detail["health_score"] == pytest.approx(1.0)
    assert bad_detail["health_points"] == pytest.approx(6.0)
    assert bad_detail["health_score"] == pytest.approx(0.4)
    assert fresh_detail["health_score"] == pytest.approx(0.8)
    bad_score, _ = _score(db, bad, item)
    fresh_score, _ = _score(db, fresh, item)
    assert bad_score < fresh_score < _score(db, clean, item)[0]


def test_health_ignores_events_older_than_7_days(db):
    item = _item(db)
    member = _member(db, 11, joined_days_ago=30)
    old = datetime.now() - timedelta(days=8)
    event = em.PoolMemberStatusEvent(
        member_id=member.id, from_status="active",
        to_status="suspended", reason="breaker")
    db.add(event)
    db.flush()
    event.created_at = old
    _usage(db, member.id)  # 今日有数据行 → 有数据但窗口内无不利
    db.commit()
    _, detail = _score(db, member, item)
    assert detail["health_points"] == 0.0
    assert detail["health_score"] == pytest.approx(1.0)


# ---------------------------------------------------------------- 新盘扶持


def test_new_member_boost_decays_linearly(db):
    item = _item(db)
    today = _member(db, 11, joined_days_ago=0)
    mid = _member(db, 12, joined_days_ago=3.5)
    old = _member(db, 13, joined_days_ago=8)  # 默认扶持 7 天，第 8 天归零
    _only_weight(db, "pool_pick_weight_new")
    _, d_today = _score(db, today, item)
    _, d_mid = _score(db, mid, item)
    _, d_old = _score(db, old, item)
    assert d_today["new_boost"] > 0.99
    assert 0.45 < d_mid["new_boost"] < 0.55
    assert d_old["new_boost"] == 0.0
    # 扶持天数配 0 = 关闭扶持
    _set_pool(db, pool_pick_new_member_boost_days=0)
    _, d_off = _score(db, today, item)
    assert d_off["new_boost"] == 0.0


# ---------------------------------------------------------------- 副本分散


def test_diversity_second_copy_avoids_same_user(db):
    item = _item(db)
    user_u = _user(db, "holder_u")
    user_v = _user(db, "other_v")
    m1 = _member(db, user_u.id, joined_days_ago=30)
    m2 = _member(db, user_u.id, joined_days_ago=30)
    m3 = _member(db, user_v.id, joined_days_ago=30)
    _variant(db, item.id, m1.id, fid="copy_on_m1")
    picked, candidates = pool_gate.pick_pool_member(
        db, item, quota_fn=_quota_ok)
    # U 名下已有本片副本、V 可选 → 必须落 V，U 的两块盘都收窄掉
    assert picked is not None and picked.id == m3.id
    assert _detail_of(candidates, m1.id)["diversity_excluded"] == 1
    assert _detail_of(candidates, m2.id)["diversity_excluded"] == 1
    assert _detail_of(candidates, m3.id)["narrowed_for_diversity"] == 1


def test_diversity_fallback_when_only_holder_user_available(db):
    item = _item(db)
    user_u = _user(db, "only_u")
    m1 = _member(db, user_u.id, joined_days_ago=30)
    _member(db, user_u.id, joined_days_ago=30)
    _variant(db, item.id, m1.id, fid="copy_on_m1")
    picked, _ = pool_gate.pick_pool_member(db, item, quota_fn=_quota_ok)
    # 没别人可选 → 回落全候选，不许因为分散把片备不出去
    assert picked is not None and picked.user_id == user_u.id


def test_diversity_inflight_job_counts_as_holding(db):
    item = _item(db)
    user_u = _user(db, "inflight_u")
    user_v = _user(db, "inflight_v")
    m1 = _member(db, user_u.id, joined_days_ago=30)
    m3 = _member(db, user_v.id, joined_days_ago=30)
    # 在途池单（25 小时前建、仍 pending）：过了 24h 节流窗不算
    # recent，但仍是在途副本 → U 算持有方
    _job(db, item.id, m1.id, status="pending",
         created_at=datetime.now() - timedelta(hours=25))
    picked, candidates = pool_gate.pick_pool_member(
        db, item, quota_fn=_quota_ok)
    assert picked is not None and picked.id == m3.id
    d1 = _detail_of(candidates, m1.id)
    assert d1["eligible"] is True  # 不是被节流挡的
    assert d1["diversity_excluded"] == 1


# ---------------------------------------------------------------- 播出 v2


def test_serve_score_prefers_less_played_copy(db):
    item = _item(db)
    busy = _member(db, 11, joined_days_ago=30)
    idle = _member(db, 12, joined_days_ago=30)
    _variant(db, item.id, busy.id, fid="copy_busy")
    idle_variant = _variant(db, item.id, idle.id, fid="copy_idle")
    # 忙盘今日已被播 100 次（当量 400GiB / 日限 600GiB）
    _usage(db, busy.id, play_starts=100)
    busy_score = pool_gate._serve_score(db, busy, quota_fn=_quota_ok)
    idle_score = pool_gate._serve_score(db, idle, quota_fn=_quota_ok)
    assert busy_score < idle_score
    picked = pool_gate._pick_ready_pool_variant(
        db, item.id, quota_fn=_quota_ok)
    assert picked is not None
    assert picked[0].id == idle_variant.id


def test_serve_score_load_never_blocks_single_copy(db):
    item = _item(db)
    busy = _member(db, 11, joined_days_ago=30)
    only = _variant(db, item.id, busy.id, fid="copy_only")
    _usage(db, busy.id, play_starts=100000)
    # 负载再高也只是打分低，唯一副本照常下发（不新增播出拦截）
    picked = pool_gate._pick_ready_pool_variant(
        db, item.id, quota_fn=_quota_ok)
    assert picked is not None and picked[0].id == only.id


# ---------------------------------------------------------------- 权重可配


def test_weights_config_moves_score(db):
    item = _item(db)
    member = _member(db, 11, joined_days_ago=0)
    _usage(db, member.id, transfer_bytes=300 * GIB)
    quota_fn = _quota_free(2 * TB)
    score, detail = _score(db, member, item, quota_fn=quota_fn)
    expected = (
        35 * detail["capacity_ratio"] + 20 * detail["daily_ratio"]
        + 20 * detail["load_score"] + 15 * detail["health_score"]
        + 10 * detail["new_boost"]) / 100
    assert score == pytest.approx(expected, abs=1e-3)
    # 权重全压到容量：总分应等于容量比本身
    _only_weight(db, "pool_pick_weight_capacity")
    score2, detail2 = _score(db, member, item, quota_fn=quota_fn)
    assert detail2["capacity_ratio"] == detail["capacity_ratio"]
    assert score2 == pytest.approx(detail2["capacity_ratio"], abs=1e-3)


def test_new_config_defaults(db):
    payload = pool_gate.pool_config_payload(db)
    assert payload["pool_pick_weight_capacity"] == 35
    assert payload["pool_pick_weight_daily"] == 20
    assert payload["pool_pick_weight_load"] == 20
    assert payload["pool_pick_weight_health"] == 15
    assert payload["pool_pick_weight_new"] == 10
    assert payload["pool_pick_new_member_boost_days"] == 7
    assert payload["pool_serve_weight_capacity"] == 60
    assert payload["pool_serve_weight_load"] == 40
    assert len(payload) == 27
