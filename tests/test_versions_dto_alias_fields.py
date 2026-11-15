# -*- coding: utf-8 -*-
"""详情 DTO Versions[] 别名字段契约（回归）。

前端 EmbyItemVersion 读 VersionLabel / SizeBytes，后端曾只下发
Name / Size，详情页版本卡标题与大小恒空、播放弹层大小恒空。
后端补别名后：每条 Versions 同时带 Name/Size（旧字段保留）与
VersionLabel/SizeBytes（新别名），且两两同值（Size 单位为字节，
与 SizeBytes 同口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import api
from backend.emby_server import models as em


@pytest.fixture()
def scene(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/ver_alias.db")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    lib = em.Library(guid=uuid.uuid4().hex, name="电影库",
                     collection_type="movies", is_enabled=True)
    db.add(lib)
    db.flush()
    user = models.WebUser(username="va", password_hash="x", is_active=True)
    db.add(user)
    db.flush()

    def mk(file_path, **kw):
        it = em.MediaItem(
            guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
            name="别名片", sort_name="别名片", file_path=file_path,
            is_hidden=False, **kw)
        db.add(it)
        db.flush()
        return it

    low = mk("/lib/Pic/Pic - 低码.mkv", bitrate=1_000_000,
             size=1_500_000_000)
    high = mk("/lib/Pic/Pic - 高码.mkv", bitrate=8_000_000,
              size=4_200_000_000)
    db.commit()
    yield db, user, low, high
    db.close()
    engine.dispose()


def test_versions_carry_frontend_alias_fields(scene):
    db, user, low, high = scene
    dto = api._item_dto(low, "http://test", user.id, db, full=True)
    versions = dto["Versions"]
    assert len(versions) == 2
    by_id = {v["Id"]: v for v in versions}
    for item, label in ((low, "低码"), (high, "高码")):
        v = by_id[item.guid]
        # 旧字段保留不删
        assert v["Name"] == label
        assert v["Size"] == item.size
        # 新别名与旧字段同值
        assert v["VersionLabel"] == v["Name"]
        assert v["SizeBytes"] == v["Size"]
