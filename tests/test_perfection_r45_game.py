# -*- coding: utf-8 -*-
"""r45 游戏化域修复回归测试：panel 端点。

隔离：内存 SQLite，不碰开发库/生产库，不 import backend.main。
成就引擎已于 2026-11-14 删除，F2/F3/F5 三项成就相关用例随之摘除；
F6 馆灵阶段形状校验、F4 口味有界已于 2026-11-15 随馆灵功能删除摘除；
本文件只保留 F7 section 回滚。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import panel as panel_api


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    try:
        from backend.emby_server import models as em
        em.Base.metadata.create_all(engine)
    except Exception:
        pass
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture()
def user(db):
    u = models.WebUser(username="r45_test", password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    return u


# ---------- F7: _section_or_none 异常后 rollback ----------

def test_section_or_none_rolls_back_session(db, user):
    def bad_block(db_, user_):
        db_.execute(text("select * from no_such_table_xyz"))

    assert panel_api._section_or_none(bad_block, db, user) is None
    # 同一 session 后续查询必须可用（原来会连带 PendingRollbackError）
    assert db.execute(text("select 1")).scalar() == 1


