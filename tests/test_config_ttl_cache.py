"""配置类高频直查的 TTL 缓存（减负 A4）

生产 pg_stat_statements 实证：storage_mounts 按 id 点查约 1207 万次、
server_realms 按 id 点查约 52 万次——都是极少变的配置小表，却每次请求现查。
本文件钉住新口径：

- ``mounts.load_mount`` 与 ``realms.realm_snapshot``（及经它的
  ``access_mode_of`` 等单口助手）命中进程内 TTL 缓存（与 integrations.store
  同一套口径，TTL 60 秒），重复读不再打数据库；
- 返回的是脱离 session 的只读快照：改它不落库、跨 session 安全；
- 写路径（create/update/delete）提交后显式失效，同进程立即读到新值；
- 不存在的结果同样缓存（负缓存），新建后失效能读到。
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, realms
from backend.emby_server import models as em
from backend.emby_server import mounts
from backend.integrations.store import TtlSnapshotCache


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    counts = {"storage_mounts": 0, "server_realms": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "from storage_mounts" in low:
            counts["storage_mounts"] += 1
        if "from server_realms" in low:
            counts["server_realms"] += 1

    mounts.invalidate_mount_cache()
    realms.invalidate_realm_cache()
    yield Session, counts
    mounts.invalidate_mount_cache()
    realms.invalidate_realm_cache()


def _mount(Session, **kw):
    db = Session()
    try:
        row = em.StorageMount(
            name=kw.get("name", "m1"), mount_type="local",
            path=kw.get("path", "/data/m1"), config="{}",
            is_enabled=kw.get("is_enabled", True))
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


# ==================== 通用缓存类 ====================

def test_snapshot_cache_ttl_and_invalidate():
    cache = TtlSnapshotCache(ttl=0.05)
    hit, _ = cache.lookup("k")
    assert hit is False
    cache.put("k", {"a": 1})
    hit, value = cache.lookup("k")
    assert hit is True and value == {"a": 1}
    cache.put("none", None)  # 负缓存：值就是 None 也算命中
    hit, value = cache.lookup("none")
    assert hit is True and value is None
    cache.invalidate("k")
    hit, _ = cache.lookup("k")
    assert hit is False
    time.sleep(0.07)
    hit, _ = cache.lookup("none")
    assert hit is False  # TTL 到期


# ==================== 挂载 ====================

def test_load_mount_second_read_hits_cache(env):
    Session, counts = env
    mid = _mount(Session)
    counts["storage_mounts"] = 0
    db = Session()
    try:
        first = mounts.load_mount(db, mid)
        assert first is not None and first.path == "/data/m1"
    finally:
        db.close()
    assert counts["storage_mounts"] == 1
    db = Session()
    try:
        second = mounts.load_mount(db, mid)
        assert second is not None and second.path == "/data/m1"
    finally:
        db.close()
    assert counts["storage_mounts"] == 1  # 第二次零查询


def test_load_mount_returns_detached_snapshot(env):
    Session, counts = env
    mid = _mount(Session)
    db = Session()
    try:
        snap = mounts.load_mount(db, mid)
        snap.path = "/tampered"
        db.commit()  # 快照不在 session 里，提交也落不了库
        row = db.query(em.StorageMount).filter(em.StorageMount.id == mid).first()
        assert row.path == "/data/m1"
    finally:
        db.close()


def test_load_mount_negative_cached_and_invalidated(env):
    Session, counts = env
    counts["storage_mounts"] = 0
    db = Session()
    try:
        assert mounts.load_mount(db, 424242) is None
        assert mounts.load_mount(db, 424242) is None
    finally:
        db.close()
    assert counts["storage_mounts"] == 1
    mid = _mount(Session)
    db = Session()
    try:
        assert mounts.load_mount(db, mid) is not None
    finally:
        db.close()


def test_load_mount_stale_until_invalidate(env):
    Session, counts = env
    mid = _mount(Session)
    db = Session()
    try:
        assert mounts.load_mount(db, mid).path == "/data/m1"
        row = db.query(em.StorageMount).filter(em.StorageMount.id == mid).first()
        row.path = "/data/m2"
        db.commit()
        db.expunge_all()
        # 未失效前读到缓存值（TTL 口径内的陈旧窗口）
        assert mounts.load_mount(db, mid).path == "/data/m1"
        mounts.invalidate_mount_cache(mid)
        assert mounts.load_mount(db, mid).path == "/data/m2"
    finally:
        db.close()


# ==================== 服 ====================

def _realm(Session, **kw):
    db = Session()
    try:
        realm = realms.create_realm(
            db, name=kw.get("name", "测试服"), slug=kw.get("slug", "testrealm"),
            access_mode=kw.get("access_mode", "free"))
        return realm.id
    finally:
        db.close()


def test_realm_helpers_hit_cache(env):
    Session, counts = env
    rid = _realm(Session)
    counts["server_realms"] = 0
    for _ in range(3):
        db = Session()
        try:
            assert realms.access_mode_of(db, rid) == "free"
            assert realms.is_free_realm(db, rid) is True
            assert realms.download_policy_of(db, rid) is None
        finally:
            db.close()
    assert counts["server_realms"] == 1  # 三轮助手调用只打一次库


def test_realm_snapshot_detached(env):
    Session, counts = env
    rid = _realm(Session)
    db = Session()
    try:
        snap = realms.realm_snapshot(db, rid)
        assert snap is not None and snap.name == "测试服"
        snap.name = "改了"
        db.commit()
        row = db.query(models.ServerRealm).filter(
            models.ServerRealm.id == rid).first()
        assert row.name == "测试服"
    finally:
        db.close()


def test_realm_update_invalidates_cache(env):
    Session, counts = env
    rid = _realm(Session)
    db = Session()
    try:
        assert realms.access_mode_of(db, rid) == "free"
        persistent = realms.get_realm(db, rid)
        realms.update_realm(db, persistent, access_mode="paid")
        # 写路径已失效：同进程立即读到新值，不等 TTL
        assert realms.access_mode_of(db, rid) == "paid"
    finally:
        db.close()


def test_realm_negative_cached_then_create_visible(env):
    Session, counts = env
    counts["server_realms"] = 0
    db = Session()
    try:
        assert realms.realm_snapshot(db, 987654) is None
        assert realms.realm_snapshot(db, 987654) is None
    finally:
        db.close()
    assert counts["server_realms"] == 1
    rid = _realm(Session, name="新服", slug="newrealm")
    db = Session()
    try:
        snap = realms.realm_snapshot(db, rid)
        assert snap is not None and snap.name == "新服"
    finally:
        db.close()


def test_realm_delete_invalidates_cache(env):
    Session, counts = env
    keep = _realm(Session, name="默认服", slug="keeprealm")
    gone = _realm(Session, name="待删服", slug="gonerealm")
    db = Session()
    try:
        assert realms.realm_snapshot(db, gone) is not None
        persistent = realms.get_realm(db, gone)
        realms.delete_realm(db, persistent, move_to=keep)
        assert realms.realm_snapshot(db, gone) is None
    finally:
        db.close()
