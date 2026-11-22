"""挂载库全量 walk 降频（谷歌盘调用削减 P3 / 清单 #19）

钉住的契约：

- 全量 walk（挂载逐目录列举）降为低频对账：间隔
  ``SCAN_FULL_WALK_RECONCILE_SEC``（默认 7 天）内，普通整库扫描**不再**
  逐目录 walk 挂载来源（列举计数为 0）；新片发现的主线是 Drive Changes
  定向扫描（不参与本闸门）+ 目录指纹复核（既有到期调度，不动）。
- 对账兜底保留、只是降频：过了间隔必须重新全量 walk；从没 walk 过
  （无时间戳）按到期处理（首次基线）；管理员手动「全量扫描」
  （force_full）永远立即 walk 并刷新对账时钟。
- 红线：降频轮次看不到挂载文件，**绝不许做删除清理**——条目一条不能
  因漏检被误删/软删，missing 计数也不许动；清理只在真 walk 的轮次做。
- 对账时钟只在 walk 真跑完（无来源失败）后推进：walk 失败的轮次不许
  把时钟推后，否则失败会被「降频」掩盖一周。
- 总开关 ``SCAN_FULL_WALK_LOW_FREQ=0`` 关掉降频，行为回到每轮全量 walk。
- 全部 mock：假远程提供者数列举次数，不碰真实 Drive / rclone / 网络。
"""
import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)
from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

import uuid
from dataclasses import replace as _dataclass_replace

import pytest

from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import mounts as mount_lib
from backend.emby_server import scanner


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （与 test_scan_dir_due_scheduling 同款，避免全局 engine 污染）。"""
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    _hyg_engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = _hyg_saved_engine
        _dbmod.configure_session_local(_hyg_saved_factory)
        if _hyg_saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _hyg_saved_url
        _hyg_engine.dispose()


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _guid():
    return uuid.uuid4().hex


# ==================== 假远程挂载（数列举次数）====================

def _entry(name, rel, is_dir=False, size=1000):
    return mount_lib.MountEntry(name=name, rel=rel, is_dir=is_dir,
                                size=0 if is_dir else size, mod_ts=1000.0)


_TREE = {
    "/": [
        _entry("电影", "/电影", is_dir=True),
        _entry("动画", "/动画", is_dir=True),
    ],
    "/电影": [
        _entry("片A (2020).mkv", "/电影/片A (2020).mkv"),
        _entry("片B (2021).mkv", "/电影/片B (2021).mkv"),
    ],
    "/动画": [
        _entry("片C (2022).mkv", "/动画/片C (2022).mkv"),
        _entry("片D (2023).mkv", "/动画/片D (2023).mkv"),
    ],
}


class _FakeRemoteMount(mount_lib.RemoteMount):
    """假远程提供者：walk_media 用基类 BFS（逐目录 list_dir），
    list_dir 只读内存字典树并计数——与 rclone 挂载同一条遍历路径。"""

    mount_type = "walkfake"
    what = "假远程"
    list_calls: list = []          # 类级计数：提供者实例每轮重建，计数不能挂实例
    fail_root = False              # 置 True 时根目录列举抛错（模拟来源不可用）

    def capabilities(self):
        return mount_lib.ProviderCapabilities(
            can_list=True, can_random_read=True, can_read_sidecar=True,
            can_generate_play_target=True, can_refresh_credentials=False)

    def list_dir(self, rel="/"):
        type(self).list_calls.append(rel or "/")
        if type(self).fail_root:
            raise mount_lib.MountError("假远程根目录不可用")
        return list(_TREE.get(rel or "/", []))

    def resolve_play_target(self, rel):
        return mount_lib.PlayTarget(
            kind="url", value=f"https://example.invalid{rel}", headers={})


mount_lib.register_providers({"walkfake": _FakeRemoteMount})


@pytest.fixture()
def mount_env(db, monkeypatch):
    _FakeRemoteMount.list_calls = []
    _FakeRemoteMount.fail_root = False
    monkeypatch.setenv("SCAN_FULL_WALK_LOW_FREQ", "1")
    monkeypatch.delenv("SCAN_FULL_WALK_RECONCILE_SEC", raising=False)
    mount = em.StorageMount(name=f"walkfake-{_guid()[:8]}",
                            mount_type="walkfake", path="walkfake:/",
                            config="{}", is_enabled=True)
    db.add(mount)
    db.commit()
    lib = em.Library(guid=_guid(), name="降频库", collection_type="movies",
                     paths="", mount_ids=str(mount.id))
    db.add(lib)
    db.commit()
    return lib


def _items(db, lib_id):
    return db.query(em.MediaItem).filter_by(library_id=lib_id).all()


# ==================== 纯函数口径 ====================

def test_reconcile_interval_default_is_one_week(monkeypatch):
    monkeypatch.delenv("SCAN_FULL_WALK_RECONCILE_SEC", raising=False)
    assert scanner._full_walk_reconcile_after() == timedelta(days=7)


def test_reconcile_interval_configurable_and_clamped(monkeypatch):
    monkeypatch.setenv("SCAN_FULL_WALK_RECONCILE_SEC", "3600")
    assert scanner._full_walk_reconcile_after() == timedelta(hours=1)
    monkeypatch.setenv("SCAN_FULL_WALK_RECONCILE_SEC", "garbage")
    assert scanner._full_walk_reconcile_after() == timedelta(days=7)


# ==================== 闸门判定 ====================

def test_walk_due_matrix(db, mount_env):
    lib = mount_env
    snap = scanner.LibrarySnapshot.of(lib)
    # 从没 walk 过（无时间戳）→ 到期（首次基线必须真 walk）
    assert scanner._full_walk_due(db, snap, lib.id) is True
    # 刚 walk 完 → 未到期
    scanner._note_full_walk(db, lib.id)
    assert scanner._full_walk_due(db, snap, lib.id) is False
    # 时间戳推到间隔之外 → 到期
    scanner._note_full_walk(db, lib.id,
                            when=datetime.now() - timedelta(days=8))
    assert scanner._full_walk_due(db, snap, lib.id) is True


def test_walk_due_force_full_and_targeted_and_switch(db, mount_env, monkeypatch):
    lib = mount_env
    scanner._note_full_walk(db, lib.id)  # 未到期
    snap = scanner.LibrarySnapshot.of(lib)
    assert scanner._full_walk_due(db, snap, lib.id) is False
    # 管理员手动全量：永远到期（快照 frozen，用 replace 派生）
    snap_full = _dataclass_replace(snap, force_full=True)
    assert scanner._full_walk_due(db, snap_full, lib.id) is True
    # 定向扫描（Drive Changes 主线）不参与闸门：恒 True（不被降频挡住）
    snap_t = _dataclass_replace(snap, targeted=True,
                                limit_prefixes=("mount://x/电影",))
    assert scanner._full_walk_due(db, snap_t, lib.id) is True
    # 总开关关掉 → 回到每轮全量 walk
    monkeypatch.setenv("SCAN_FULL_WALK_LOW_FREQ", "0")
    assert scanner._full_walk_due(db, snap, lib.id) is True


# ==================== 整库集成：降频生命周期 ====================

def test_second_scan_within_interval_does_not_walk(db, mount_env):
    lib = mount_env
    cold = scanner.scan_library_sync(db, lib)
    assert cold["added"] == 4
    walk_calls_cold = len(_FakeRemoteMount.list_calls)
    assert walk_calls_cold > 0  # 冷扫真走了全量 walk

    _FakeRemoteMount.list_calls = []
    again = scanner.scan_library_sync(db, lib)
    # 对账间隔内：挂载来源一次列举都不许发生
    assert _FakeRemoteMount.list_calls == []
    assert again.get("walk_deferred") is True
    # 看不到文件 ≠ 文件已删：条目一条不少、没被软删、清理被跳过
    assert again["removed"] == 0
    assert again.get("removal_skipped") is True
    items = _items(db, lib.id)
    assert len(items) == 4
    assert all(it.deleted_at is None for it in items)
    assert all((it.missing_count or 0) == 0 for it in items)
    # 降频轮是预期内的跳过清理，不许误报 partial / “没有可用来源”
    db.refresh(lib)
    assert lib.scan_status == scanner.SCAN_STATUS_SUCCESS


def test_scan_walks_again_after_interval(db, mount_env):
    lib = mount_env
    scanner.scan_library_sync(db, lib)
    scanner._note_full_walk(db, lib.id,
                            when=datetime.now() - timedelta(days=8))
    _FakeRemoteMount.list_calls = []
    stats = scanner.scan_library_sync(db, lib)
    assert len(_FakeRemoteMount.list_calls) > 0  # 过了间隔：对账 walk 照常
    assert not stats.get("walk_deferred")
    assert stats["removed"] == 0
    assert len(_items(db, lib.id)) == 4


def test_force_full_walks_within_interval_and_resets_clock(db, mount_env):
    lib = mount_env
    scanner.scan_library_sync(db, lib)
    _FakeRemoteMount.list_calls = []
    snap = _dataclass_replace(scanner.LibrarySnapshot.of(lib), force_full=True)
    stats = scanner.scan_library_sync(db, lib, snapshot=snap)
    assert len(_FakeRemoteMount.list_calls) > 0
    assert not stats.get("walk_deferred")
    # force_full 刷新了对账时钟：紧接着的普通扫描重新进入降频
    _FakeRemoteMount.list_calls = []
    again = scanner.scan_library_sync(db, lib)
    assert _FakeRemoteMount.list_calls == []
    assert again.get("walk_deferred") is True


def test_failed_walk_does_not_advance_clock(db, mount_env):
    lib = mount_env
    _FakeRemoteMount.fail_root = True
    stats = scanner.scan_library_sync(db, lib)
    assert stats["added"] == 0
    # walk 失败 → 时钟不许推进（否则失败被降频掩盖一整周）
    snap = scanner.LibrarySnapshot.of(lib)
    assert scanner._full_walk_due(db, snap, lib.id) is True
    _FakeRemoteMount.fail_root = False
    _FakeRemoteMount.list_calls = []
    stats2 = scanner.scan_library_sync(db, lib)
    assert len(_FakeRemoteMount.list_calls) > 0  # 下一轮照常重试 walk
    assert stats2["added"] == 4


def test_low_freq_switch_off_walks_every_scan(db, mount_env, monkeypatch):
    lib = mount_env
    scanner.scan_library_sync(db, lib)
    monkeypatch.setenv("SCAN_FULL_WALK_LOW_FREQ", "0")
    _FakeRemoteMount.list_calls = []
    stats = scanner.scan_library_sync(db, lib)
    assert len(_FakeRemoteMount.list_calls) > 0
    assert not stats.get("walk_deferred")
