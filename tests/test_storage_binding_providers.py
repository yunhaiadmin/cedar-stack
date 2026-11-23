"""用户端“我的网盘”挂载驱动名单（GET /storage-bindings/providers 的查询本体）。

只测 list_binding_providers：挂载类型 → provider 映射、is_enabled 过滤、
不返回任何敏感字段（返回值只有 provider 名串）。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import user_bindings as ub
from backend.integrations import store


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
    # 绑盘拆除一期（2026-11-24）：drive_binding_enabled 已收敛为
    # 代码常量关。本套件故意播种历史配置行=true，钉住它不再
    # 打开名单——名单恒为空。
    session.add(base_models.SystemConfig(
        key="drive_binding_enabled", value="true"))
    session.commit()
    store.invalidate()
    yield session
    session.close()
    store.invalidate()


def _mount(db, name, mount_type, is_enabled=True):
    row = em.StorageMount(name=name, mount_type=mount_type, path="",
                          is_enabled=is_enabled)
    db.add(row)
    db.commit()
    return row


def test_no_mounts_returns_empty(db):
    assert ub.list_binding_providers(db) == []


def test_drive_mapped_115_removed_and_others_skipped(db):
    # 夸克/115 用户绑定线已下线（2026-11-02 删除单）；drive 型
    # 挂载自绑盘拆除一期（2026-11-24）起也不再映射出用户绑定
    # 入口（绑定总开关常量关）。挂载本身仍可作共享库来源。
    _mount(db, "m-drive", "drive")
    _mount(db, "m-115", "115")
    _mount(db, "m-local", "local")
    _mount(db, "m-rclone", "rclone")
    assert ub.list_binding_providers(db) == []


def test_alias_mount_types_mapped(db):
    # 别名型挂载（gdrive）同 drive 口径：绑盘拆除后不再映射。
    _mount(db, "m-gdrive", "gdrive")
    _mount(db, "m-pan115", "pan115")
    assert ub.list_binding_providers(db) == []


def test_disabled_mounts_not_counted(db):
    _mount(db, "m-drive-off", "drive", is_enabled=False)
    _mount(db, "m-115-on", "115", is_enabled=True)
    assert ub.list_binding_providers(db) == []


def test_result_contains_only_provider_names(db):
    _mount(db, "m-drive", "drive", )
    result = ub.list_binding_providers(db)
    assert result == []
    assert all(isinstance(p, str) for p in result)
