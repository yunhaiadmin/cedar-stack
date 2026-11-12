# -*- coding: utf-8 -*-
"""公共池「条目级源熔断」回归（2026-11-12 生产定案）。

生产实证：《奥德赛》（item 2483492）源文件 anyone-reader 分享
坏死，池转存 24h 产生 236 个 failed/copy_forbidden（19 个池
成员、匀速 15–18 个/小时）。2026-11-10 的换盘机制本身没坏：
单轮内排除已试盘、全员试过落终态都对；漏的是**条目级记忆**——
「已试过」集合只活在每条失败行自己的短冷却窗里（cooldown_
until = created_at + pool_copy_forbidden_cooldown_sec，默认
3600 秒），行一过期就掉出集合，下一轮点播又把全员当没试过
重开一整轮，19 块盘每小时轮一遍。

修法：条目级源熔断（本单）——同一条目 copy_forbidden 失败达
「全员试过」或窗口内阈值（pool_retry 子家族新键）即落阻断行
（pool_item_source_blocks，带 blocked_until）；阻断期内所有
建单入口（门禁/工人换盘/巡检补位）一律不再为该条目建池单，
门禁如实 failed/source_blocked（源文件不可读），不再冒充
preparing；冷却到期半开只放 1 个探测单，探测成功清阻断、
再 forbidden 重新阻断。个人盘（owner_scope=user）逐字不动。
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
from backend.emby_server import pool_gate
from backend.emby_server import transfer_decisions as td
from backend.emby_server import transfer_variant as tv
from backend.integrations import store

TB = 1000 ** 4
ORIG_FID = "origfid"
FORBIDDEN_DETAIL = ("成员令牌无权读取源文件（anyone-reader 未传播"
                    "或源已撤分享），不重试")


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
def _transfer_master_on(monkeypatch):
    monkeypatch.setenv("TRANSFER_VARIANT_ENABLED", "1")


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
        user_id=user_id, provider=provider, account_label="贡献盘",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _item(db, size=1_000_000):
    existing = db.query(em.StorageMount).filter(
        em.StorageMount.id == 3).first()
    if existing is None:
        db.add(em.StorageMount(id=3, name="m3", mount_type="drive",
                               path="drive:drive1"))
        db.commit()
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


def _member(db, user_id, status="active"):
    binding = _binding(db, user_id)
    row = em.PoolMember(
        binding_id=binding.id, user_id=user_id, provider="drive",
        status=status, joined_at=datetime.now())
    db.add(row)
    db.commit()
    return row


def _set_pool(db, **values):
    config_service.save_config(db, values, schema="pool")
    db.commit()


def _enable_pool(db, **overrides):
    values = {"pool_enabled": True}
    values.update(overrides)
    _set_pool(db, **values)


def _set_retry(db, **values):
    config_service.save_config(db, values, schema="pool_retry")
    db.commit()


def _quota_ok(db, binding):
    return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}


def _job(db, item_id, member_id, status="pending", error_kind=None,
         error_detail=None):
    row = em.TransferJob(
        item_id=item_id, trigger="playback", drive_id="drive1",
        provider="drive", owner_scope="pool", owner_ref=str(member_id),
        dest_drive_id=f"pooldrive:{member_id}", status=status,
        error_kind=error_kind, error_detail=error_detail)
    db.add(row)
    db.commit()
    return row


def _pool_jobs(db, item_id):
    return (db.query(em.TransferJob)
            .filter_by(item_id=item_id, owner_scope="pool")
            .order_by(em.TransferJob.id).all())


def _block(db, item_id):
    return (db.query(em.PoolItemSourceBlock)
            .filter_by(item_id=item_id).first())


class _FakeMemberDrive:
    def __init__(self, copy_exc=None):
        self.copy_exc = copy_exc
        self.copy_calls = []

    def ensure_folder(self, drive_id, dir_name):
        return "pfolder"

    def list_file_ids_by_name(self, drive_id, folder_id, name):
        return ["poolcopy"]

    def copy_file(self, drive_id, source_fid, folder_id, name):
        self.copy_calls.append(source_fid)
        if self.copy_exc is not None:
            raise self.copy_exc
        return {"id": "poolcopy", "size": 1_000_000}

    def add_anyone_reader(self, drive_id, fid):
        pass

    def about_storage(self, drive_id):
        return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}


class _FakeSA:
    def ensure_folder(self, drive_id, dir_name):
        return "tfolder"


def _run_forbidden(db, monkeypatch, job):
    fake = _FakeMemberDrive(copy_exc=tv._CopyForbidden("copy 被拒：403"))
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    return tv.run_transfer_job_once(db, job, _FakeSA())


def _storm_to_block(db, monkeypatch, member_uids=(11, 12)):
    """把一部片打到熔断：建首单 → 工人 forbidden → 自动换盘 →
    工人再 forbidden → 全员试过触发阻断。返回 (item, jobs)。"""
    item = _item(db)
    members = [_member(db, uid) for uid in member_uids]
    _enable_pool(db)
    job = _job(db, item.id, members[0].id, status="pending")
    res = _run_forbidden(db, monkeypatch, job)
    assert res["error_kind"] == "copy_forbidden"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2  # 工人已自动换盘到第二块盘
    res = _run_forbidden(db, monkeypatch, jobs[1])
    assert res["error_kind"] == "copy_forbidden"
    return item, _pool_jobs(db, item.id)


# ---------------------------------------------------------------- 熔断触发


def test_storm_trips_block_when_all_members_tried(db, monkeypatch):
    """全员盘都 forbidden：最后一块盘失败时当场落条目级阻断，
    工人不再自动换盘建第三单（旧行为是等短冷却过期后重开整轮）。"""
    item, jobs = _storm_to_block(db, monkeypatch)
    assert len(jobs) == 2
    block = _block(db, item.id)
    assert block is not None
    assert block.blocked_until is not None
    delta = (block.blocked_until - datetime.now()).total_seconds()
    assert 23 * 3600 < delta <= 24 * 3600 + 60  # 默认冷却 24h
    assert block.probe_job_id is None


def test_single_failure_below_threshold_no_block(db, monkeypatch):
    """只坏一块盘、还有没试过的盘：照常换盘，不许误熔断。"""
    item = _item(db)
    member_a = _member(db, 11)
    _member(db, 12)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    res = _run_forbidden(db, monkeypatch, job)
    assert res["error_kind"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 2  # 已换盘
    assert _block(db, item.id) is None


def test_threshold_trips_before_all_members_tried(db, monkeypatch):
    """窗口内失败数达配置阈值（成员 churn 时全员口径永远凑不
    齐）：第 2 次失败即熔断，没试过的第三块盘也不再建单。"""
    _set_retry(db, pool_source_block_max_failures=2)
    item, jobs = _storm_to_block(db, monkeypatch,
                                 member_uids=(11, 12, 13))
    assert len(jobs) == 2
    assert _block(db, item.id) is not None


# ---------------------------------------------------------------- 阻断期


def test_gate_reports_source_blocked_and_creates_nothing(db):
    """阻断期内门禁如实 failed/source_blocked（源文件不可读），
    不报 preparing、不再建单；判定点在门禁换盘口上触发落阻断。"""
    item = _item(db)
    member_a = _member(db, 11)
    member_b = _member(db, 12)
    _enable_pool(db)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    _job(db, item.id, member_b.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "source_blocked"
    assert "源文件" in (gate.get("detail") or "")
    assert len(_pool_jobs(db, item.id)) == 2
    assert _block(db, item.id) is not None
    # 再点一次：仍是如实终态，仍不建单（旧行为会在短冷却过期
    # 后把全员重开一轮）。
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "source_blocked"
    assert len(_pool_jobs(db, item.id)) == 2


def test_create_pool_job_raises_source_blocked(db, monkeypatch):
    """建单总口（巡检补位/门禁⑥⑦ 共用）阻断期直接抛
    source_blocked，不许任何入口绕开总闸另建池单。"""
    item, _jobs = _storm_to_block(db, monkeypatch)
    with pytest.raises(tv.TransferError) as excinfo:
        pool_gate.create_pool_job(db, item, trigger="refill",
                                  quota_fn=_quota_ok)
    assert excinfo.value.kind == "source_blocked"
    assert len(_pool_jobs(db, item.id)) == 2


# ---------------------------------------------------------------- 半开探测


def _expire_block(db, item_id):
    block = _block(db, item_id)
    block.blocked_until = datetime.now() - timedelta(seconds=1)
    db.commit()
    return block


def test_half_open_allows_exactly_one_probe(db, monkeypatch):
    """冷却到期半开：只放 1 个探测单；探测在飞时门禁如实
    preparing（真有单在飞），任何入口不许再建第二个。"""
    item, _jobs = _storm_to_block(db, monkeypatch)
    _expire_block(db, item.id)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "preparing"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 3
    probe = jobs[2]
    assert probe.status == "pending"
    block = _block(db, item.id)
    assert block.probe_job_id == probe.id
    # 探测在飞：门禁走在途口径，不再建单。
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "preparing"
    assert gate["reason"] == "job_in_flight"
    assert len(_pool_jobs(db, item.id)) == 3
    # 建单总口同样拒绝第二个探测。
    with pytest.raises(tv.TransferError) as excinfo:
        pool_gate.create_pool_job(db, item, quota_fn=_quota_ok)
    assert excinfo.value.kind == "source_blocked"
    assert len(_pool_jobs(db, item.id)) == 3


def test_probe_success_clears_block(db, monkeypatch):
    """探测单办成（done）：阻断解除，之后建单恢复正常。"""
    item, _jobs = _storm_to_block(db, monkeypatch)
    _expire_block(db, item.id)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    probe = _pool_jobs(db, item.id)[2]
    assert gate["job_id"] == probe.id
    probe.status = "done"
    db.commit()
    # force 走门禁⑥「done 无副本重建」同口径（非 force 建单会被
    # 成员 24h 节流挡住，那是既有语义、与源熔断无关）。
    job = pool_gate.create_pool_job(db, item, force=True,
                                    quota_fn=_quota_ok)
    assert job is not None and job.status == "pending"
    assert _block(db, item.id) is None


def test_probe_forbidden_failure_retrips(db, monkeypatch):
    """探测单又 forbidden：当场重新阻断（新一轮 24h 冷却、
    探测标记清掉），工人不许顺手再换盘建单。"""
    item, _jobs = _storm_to_block(db, monkeypatch)
    _expire_block(db, item.id)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    probe = _pool_jobs(db, item.id)[2]
    assert gate["job_id"] == probe.id
    res = _run_forbidden(db, monkeypatch, probe)
    assert res["error_kind"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 3  # 没有第四单
    block = _block(db, item.id)
    assert block is not None
    assert block.probe_job_id is None
    delta = (block.blocked_until - datetime.now()).total_seconds()
    assert 23 * 3600 < delta <= 24 * 3600 + 60
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "source_blocked"


# ---------------------------------------------------------------- 不误伤


def test_healthy_item_unaffected(db):
    """没坏过的条目：门禁照常建单，阻断行不凭空出现。"""
    item = _item(db)
    _member(db, 11)
    _enable_pool(db)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "preparing"
    assert gate["reason"] == "job_created"
    assert len(_pool_jobs(db, item.id)) == 1
    assert _block(db, item.id) is None


def test_user_scope_job_unaffected_by_block(db, monkeypatch):
    """个人盘口径不动：条目被池阻断时，用户盘建单照常成功，
    阻断只管 owner_scope=pool 的建单口。"""
    item, _jobs = _storm_to_block(db, monkeypatch)
    assert _block(db, item.id) is not None
    user99 = _user(db, "u99")
    _binding(db, user99.id)
    job = tv.create_user_job(db, item, user99.id, "drive")
    assert job.owner_scope == "user"
    assert job.status == "pending"


def test_source_block_config_defaults(db):
    """pool_retry 子家族新键默认值：窗口内失败阈值 20、计数
    窗口 24h、阻断冷却 24h（与生产风暴量级对齐）；源自愈默认开。"""
    payload = pool_gate.retry_config_payload(db)
    assert payload["pool_source_block_max_failures"] == 20
    assert payload["pool_source_block_window_sec"] == 86400
    assert payload["pool_source_block_cooldown_sec"] == 86400
    assert payload["pool_source_heal_enabled"] is True


# ---------------------------------------------------------------- 源自愈（2026-11-12 补充）
# 根因实证：MP 换源文件后新文件没带 anyone-reader，池 Path A
# 成员令牌直拷全员 forbidden。熔断是兜底，治本在执行点：池单
# copy_forbidden 先经 SA 通道给源文件补 anyone-reader（现成
# 方法、幂等）再原地重试一次；补不上/重试仍拒才落终态归熔断。


class _FakeSAHeal:
    """SA 通道桩：记录 add_anyone_reader 调用，可指定补权限失败。"""

    def __init__(self, heal_exc=None):
        self.heal_exc = heal_exc
        self.heal_calls = []

    def ensure_folder(self, drive_id, dir_name):
        return "tfolder"

    def add_anyone_reader(self, drive_id, fid):
        self.heal_calls.append((drive_id, fid))
        if self.heal_exc is not None:
            raise self.heal_exc


class _FakeMemberDriveFlaky(_FakeMemberDrive):
    """成员盘桩：copy 前 forbidden_failures 次抛 _CopyForbidden，
    之后成功（模拟补完 anyone-reader 后成员令牌能读源）。"""

    def __init__(self, forbidden_failures=1):
        super().__init__()
        self.forbidden_failures = forbidden_failures

    def copy_file(self, drive_id, source_fid, folder_id, name):
        self.copy_calls.append(source_fid)
        if len(self.copy_calls) <= self.forbidden_failures:
            raise tv._CopyForbidden("copy 被拒：403")
        return {"id": "poolcopy", "size": 1_000_000}


def test_worker_forbidden_heals_source_and_retries(db, monkeypatch):
    """池单 copy 被拒 → SA 给源文件补 anyone-reader → 原地重试
    成功办成：不落失败、不触发熔断、自愈只调一次且只对源 fid。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDriveFlaky(forbidden_failures=1)
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    sa = _FakeSAHeal()
    res = tv.run_transfer_job_once(db, job, sa)
    assert res["outcome"] == "done"
    assert sa.heal_calls == [(job.drive_id, ORIG_FID)]
    assert len(fake.copy_calls) == 2
    row = db.query(em.TransferJob).filter_by(id=job.id).one()
    assert row.status == "done"
    assert _block(db, item.id) is None


def test_worker_heal_failure_falls_back_to_terminal(db, monkeypatch):
    """SA 补不上权限（无权/源已删）：不再重试 copy，如实落
    copy_forbidden 终态（后续归源熔断记账）。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDriveFlaky(forbidden_failures=99)
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    sa = _FakeSAHeal(heal_exc=Exception("补 anyone 权限失败：404"))
    res = tv.run_transfer_job_once(db, job, sa)
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    assert len(sa.heal_calls) == 1
    assert len(fake.copy_calls) == 1


def test_worker_heal_ok_but_retry_still_forbidden(db, monkeypatch):
    """补权限成功但重试仍被拒（分享传播未到位等）：只重试一
    次就落终态，不许借自愈无限循环。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDriveFlaky(forbidden_failures=99)
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    sa = _FakeSAHeal()
    res = tv.run_transfer_job_once(db, job, sa)
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    assert len(sa.heal_calls) == 1
    assert len(fake.copy_calls) == 2


def test_worker_heal_disabled_by_config(db, monkeypatch):
    """pool_source_heal_enabled 关：一次都不许调 SA 补权限，
    直接落 copy_forbidden 终态。"""
    _set_retry(db, pool_source_heal_enabled=False)
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDriveFlaky(forbidden_failures=99)
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    sa = _FakeSAHeal()
    res = tv.run_transfer_job_once(db, job, sa)
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    assert sa.heal_calls == []
    assert len(fake.copy_calls) == 1


def test_user_job_never_heals_source(db, monkeypatch):
    """个人盘路径逐字不动：用户单 copy 被拒走它自己的既有口径
    （Path B 兜底等），绝不触发池源自愈。"""
    item = _item(db)
    user99 = _user(db, "u99")
    _binding(db, user99.id)
    _enable_pool(db)
    job = tv.create_user_job(db, item, user99.id, "drive")
    fake = _FakeMemberDriveFlaky(forbidden_failures=99)
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    sa = _FakeSAHeal()
    tv.run_transfer_job_once(db, job, sa)
    assert sa.heal_calls == []
