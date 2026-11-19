# -*- coding: utf-8 -*-
"""公共池 copy_forbidden「换源」兜底回归（失败等待优化第一层）。

生产实证（2026-11-19 parent 亲查）：近 7 天池转存 failed 263
单全是 copy_forbidden、attempts 均 1.0。既有恢复链只有「换盘」
（换成员盘读同一源）与「源自愈」（SA 给源补 anyone-reader）：
源文件本身读不到、且全体成员盘都读不到时，条目级源熔断直接
把条目锁 24 小时——哪怕库里躺着同作品的另一份源文件（合并
副本行/同集重复行/同 tmdb 行）从来没被试过。本单补第三条路：

- 换盘穷尽后，按同作品多源逐个换源重试（新单落
  TransferJob.source_item_id，副本仍登记在原条目名下）；
- 换源次数有上限（pool_copy_forbidden_source_max_swaps，
  默认 3，0 = 关），全源全灭才判死（源熔断照旧兜底）；
- 源熔断的「全员试过」触发条件改为「全员试过且无未试替代
  源」，不许在还有源没试时提前把条目锁死；
- 换源全程走 transfer_decisions 留痕（scope=pool_source_swap）。

红线：落点仍是池成员盘、成员令牌直拷，不回落共享 SA 中继、
不下发成员令牌；用户 scope（绑盘）路径逐字不动。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime

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
ALT_FID = "altfid"
ALT2_FID = "alt2fid"
FORBIDDEN_DETAIL = ("成员令牌无权读取源文件（anyone-reader 未传播"
                    "或源已撤分享）")


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


def _mount(db):
    existing = db.query(em.StorageMount).filter(
        em.StorageMount.id == 3).first()
    if existing is None:
        db.add(em.StorageMount(id=3, name="m3", mount_type="drive",
                               path="drive:drive1"))
        db.commit()


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


def _alt_source(db, primary, fid=ALT_FID, name="池测试片[副本]"):
    """同作品替代源行：合并副本（merged_into_id 指向主行）、
    不同 drive_file_id、同挂载同盘（生产占比最高的形态）。"""
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=primary.library_id,
        item_type=primary.item_type, name=name, drive_file_id=fid,
        file_path="mount://3/电影/b.mkv", size=primary.size,
        merged_into_id=primary.id)
    db.add(row)
    db.commit()
    return row


def _member(db, user_id, status="active"):
    binding = _binding(db, user_id)
    row = em.PoolMember(
        binding_id=binding.id, user_id=user_id, provider="drive",
        status=status, joined_at=datetime.now())
    db.add(row)
    db.commit()
    return row


def _enable_pool(db, **overrides):
    values = {"pool_enabled": True}
    values.update(overrides)
    config_service.save_config(db, values, schema="pool")
    db.commit()


def _set_retry(db, **values):
    config_service.save_config(db, values, schema="pool_retry")
    db.commit()


def _quota_ok(db, binding):
    return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}


def _job(db, item_id, member_id, status="pending", error_kind=None,
         error_detail=None, source_item_id=None):
    row = em.TransferJob(
        item_id=item_id, trigger="playback", drive_id="drive1",
        provider="drive", owner_scope="pool", owner_ref=str(member_id),
        dest_drive_id=f"pooldrive:{member_id}", status=status,
        error_kind=error_kind, error_detail=error_detail,
        source_item_id=source_item_id)
    db.add(row)
    db.commit()
    return row


def _pool_jobs(db, item_id):
    return (db.query(em.TransferJob)
            .filter_by(item_id=item_id, owner_scope="pool")
            .order_by(em.TransferJob.id).all())


# ---------------------------------------------------------------- 门禁：换源


def test_gate_source_swap_after_member_exhausted(db):
    """唯一成员盘读不到原源、库里有同作品替代源：门禁须换源
    重建一单（source_item_id 落替代源行、副本仍归原条目），
    报 preparing，而不是把条目锁进源熔断。"""
    item = _item(db)
    alt = _alt_source(db, item)
    member_a = _member(db, 11)
    _enable_pool(db)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "preparing"
    assert gate["reason"] == "job_retriggered_source_swap"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2
    assert jobs[1].status == "pending"
    assert jobs[1].item_id == item.id
    assert jobs[1].source_item_id == alt.id
    assert jobs[1].owner_ref == str(member_a.id)
    assert gate["job_id"] == jobs[1].id


def test_gate_source_swap_cap_respected(db):
    """换源上限（=1）已用满：即使还有没试过的替代源也落终态，
    不再建单——防同作品重复行很多时无界换源。"""
    item = _item(db)
    alt = _alt_source(db, item)
    _alt_source(db, item, fid=ALT2_FID, name="池测试片[副本2]")
    member_a = _member(db, 11)
    _enable_pool(db)
    _set_retry(db, pool_copy_forbidden_source_max_swaps=1)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL,
         source_item_id=alt.id)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert len(_pool_jobs(db, item.id)) == 2


def test_gate_source_swap_disabled_by_zero(db):
    """换源上限配 0 = 关：有替代源也不换，回到旧口径终态
    （单盘即全员试过 → 源熔断 source_blocked）。"""
    item = _item(db)
    _alt_source(db, item)
    member_a = _member(db, 11)
    _enable_pool(db)
    _set_retry(db, pool_copy_forbidden_source_max_swaps=0)
    _job(db, item.id, member_a.id, status="failed",
         error_kind="copy_forbidden", error_detail=FORBIDDEN_DETAIL)
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "source_blocked"
    assert len(_pool_jobs(db, item.id)) == 1


def test_source_swap_config_default(db):
    """pool_retry 子家族新键默认值：换源上限 3。"""
    payload = pool_gate.retry_config_payload(db)
    assert payload["pool_copy_forbidden_source_max_swaps"] == 3


# ---------------------------------------------------------------- 工人：换源全链


class _FakeMemberDrive:
    """按源 fid 分流的成员盘假客户端：forbidden_fids 里的源
    copy 必被拒，其余成功；登记目录核对恒命中回执 id。"""

    def __init__(self, forbidden_fids=()):
        self.forbidden_fids = set(forbidden_fids)
        self.copy_calls = []

    def ensure_folder(self, drive_id, dir_name):
        return "pfolder"

    def list_file_ids_by_name(self, drive_id, folder_id, name):
        return ["poolcopy"]

    def copy_file(self, drive_id, source_fid, folder_id, name):
        self.copy_calls.append(source_fid)
        if source_fid in self.forbidden_fids:
            raise tv._CopyForbidden("copy 被拒：403")
        return {"id": "poolcopy", "size": 1_000_000}

    def add_anyone_reader(self, drive_id, fid):
        pass

    def about_storage(self, drive_id):
        return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}


class _FakeSA:
    def ensure_folder(self, drive_id, dir_name):
        return "tfolder"


def test_worker_source_swap_eventually_serves(db, monkeypatch):
    """首源 forbidden → 工人终态后自动换源建单 → 换源单跑通：
    副本登记在原条目名下、source_file_id 如实记实际拷的替代
    源 fid，条目从此可播（池变体 ready）。"""
    item = _item(db)
    alt = _alt_source(db, item)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDrive(forbidden_fids={ORIG_FID})
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    res = tv.run_transfer_job_once(db, job, _FakeSA())
    assert res["outcome"] == "failed"
    assert res["error_kind"] == "copy_forbidden"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2
    assert jobs[1].source_item_id == alt.id
    assert jobs[1].status == "pending"
    res2 = tv.run_transfer_job_once(db, jobs[1], _FakeSA())
    assert res2["outcome"] == "done"
    assert fake.copy_calls == [ORIG_FID, ALT_FID]
    variant = (db.query(em.TransferVariant)
               .filter_by(item_id=item.id, owner_scope="pool",
                          status="ready")
               .first())
    assert variant is not None
    assert variant.source_file_id == ALT_FID
    picked = pool_gate.pool_serve_candidate(db, item)
    assert picked is not None


def test_worker_all_sources_forbidden_terminal(db, monkeypatch):
    """全源 forbidden：原源与替代源都被拒 → 只烧两单即止
    （不再建第三单），门禁如实终态（源熔断），不无限换源。"""
    item = _item(db)
    _alt_source(db, item)
    member_a = _member(db, 11)
    _enable_pool(db)
    job = _job(db, item.id, member_a.id, status="pending")
    fake = _FakeMemberDrive(forbidden_fids={ORIG_FID, ALT_FID})
    monkeypatch.setattr(tv, "UserDriveApiClient",
                        lambda db_arg, binding: fake)
    res = tv.run_transfer_job_once(db, job, _FakeSA())
    assert res["error_kind"] == "copy_forbidden"
    jobs = _pool_jobs(db, item.id)
    assert len(jobs) == 2
    res2 = tv.run_transfer_job_once(db, jobs[1], _FakeSA())
    assert res2["error_kind"] == "copy_forbidden"
    assert len(_pool_jobs(db, item.id)) == 2
    user = _user(db, "free")
    gate = pool_gate.ensure_pool_copy_gate(db, item, user,
                                           quota_fn=_quota_ok)
    assert gate["verdict"] == "failed"
    assert gate["reason"] == "source_blocked"
    assert len(_pool_jobs(db, item.id)) == 2
