# -*- coding: utf-8 -*-
"""公共池「已失败转存任务冒充准备中」缺陷回归（2026-11-10 生产定案）。

生产实证：池单 job 4208 早已 failed/copy_forbidden（不重试＋24h
冷却），pool_gate 却对该条目持续给 preparing/throttled 判定，取流
层据此回 409，观众重试多少次都播不了。修法三条：
① 门禁面对 failed 终态任务如实给 failed（reason 带 error_kind），
   不再冒充 preparing；
② copy_forbidden 在门禁/工人两侧换盘重试：排除已 forbidden 的
   成员盘、换另一块可用盘重建单，全部可用盘都试过才落终态；
③ copy_forbidden 终态冷却不再无条件 24h，走 pool_retry 子家族
   键 pool_copy_forbidden_cooldown_sec（默认 3600 秒）。
个人盘（owner_scope=user）copy_forbidden 语义不在本单范围。
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


# ---------------------------------------------------------------- 门禁：换盘重试


def test_gate_forbidden_swaps_to_other_member(db):
    """A 盘 forbidden 失败、B 盘可用：门禁须重建到 B 盘并如实报
    「已换盘重触发」，而不是拿着死单报 preparing/throttled。"""
    item = _item(db)
    member_a = _member(db, 11)
    member_b = _member(db, 12)
    _enable_pool(db)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "preparing"
    assert gate["reason"] == "job_retriggered_forbidden"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2
    assert jobs[1].status == "pending"
    assert jobs[1].owner_ref == str(member_b.id)
    assert gate["job_id"] == jobs[1].id


def test_gate_forbidden_all_members_tried_terminal(db):
    """全部可用盘都 forbidden 过：门禁如实 failed，reason 带
    copy_forbidden，detail 透传任务行原因，且不再建单。"""
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
    assert gate["reason"] == "copy_forbidden"
    assert "无权读取源文件" in (gate.get("detail") or "")
    assert len(_pool_jobs(db, item.id)) == 2


def test_gate_forbidden_single_member_terminal(db):
    """只有一块盘且它 forbidden：无盘可换 → 如实终态失败，
    不许再报 preparing 让观众无限重试吃 409。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 1


def test_gate_forbidden_attempts_cap_terminal(db):
    """换盘总尝试上限（pool_copy_forbidden_max_attempts）达到后
    即使还有没试过的盘也落终态，防成员churn 造成无界重试。"""
    item = _item(db)
    member_a = _member(db, 11)
    _member(db, 12)
    _enable_pool(db)
    _set_retry(db, pool_copy_forbidden_max_attempts=1)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 1


def test_gate_failed_unclassified_kind_reports_failed(db):
    """未归类终态 kind（如 member_inactive）同样不许冒充
    preparing/throttled：如实 failed + reason 带 kind。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="member_inactive",
         error_detail="池成员已 suspended，不执行池转存")
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "member_inactive"
    assert len(_pool_jobs(db, item.id)) == 1


# ---------------------------------------------------------------- 工人：换盘重试


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


def test_worker_forbidden_creates_swap_job(db, monkeypatch):
    """工人跑池单遇 copy_forbidden：本单终态失败，同时立刻给
    另一块没试过的成员盘建换盘单（不等观众下次点播）。"""
    item = _item(db)
    member_a = _member(db, 11)
    member_b = _member(db, 12)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDrive(copy_exc=tv._CopyForbidden("copy 被拒：403"))
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    res = tv.run_transfer_job_once(db, job, _FakeSA())
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2
    assert jobs[1].status == "pending"
    assert jobs[1].owner_ref == str(member_b.id)


def test_worker_forbidden_no_other_member_no_swap(db, monkeypatch):
    """只有一块盘 forbidden：只落本单终态，不建换盘单。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDrive(copy_exc=tv._CopyForbidden("copy 被拒：403"))
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    res = tv.run_transfer_job_once(db, job, _FakeSA())
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 1


def test_worker_forbidden_short_cooldown(db, monkeypatch):
    """copy_forbidden 终态行的 cooldown_until 按新键（默认
    3600 秒）落字，不再是建单时的 24h。"""
    item = _item(db)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    created = datetime.now()
    job.created_at = created
    job.cooldown_until = created + timedelta(seconds=86400)
    db.commit()
    fake = _FakeMemberDrive(copy_exc=tv._CopyForbidden("copy 被拒：403"))
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    res = tv.run_transfer_job_once(db, job, _FakeSA())
    assert res["error_kind"] == "copy_forbidden"
    row = db.query(em.TransferJob).filter_by(id=job.id).one()
    assert row.cooldown_until is not None
    delta = (row.cooldown_until - created).total_seconds()
    assert 0 < delta <= 3600 + 60


def test_retry_config_defaults(db):
    """pool_retry 子家族默认值：冷却 3600、尝试上限 0（以可用
    成员盘数为界）。"""
    payload = pool_gate.retry_config_payload(db)
    assert payload["pool_copy_forbidden_cooldown_sec"] == 3600
    assert payload["pool_copy_forbidden_max_attempts"] == 0
