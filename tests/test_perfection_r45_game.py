# -*- coding: utf-8 -*-
"""r45 游戏化域修复回归测试：panel 端点（馆灵阶段/口味）。

隔离：内存 SQLite，不碰开发库/生产库，不 import backend.main。
成就引擎已于 2026-11-14 删除，F2/F3/F5 三项成就相关用例随之摘除；
本文件只保留 F6 馆灵阶段形状校验、F7 section 回滚、F4 口味有界。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import panel_stats as pa
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


# 真实播放会话数据源（emby_playback_sessions）：每 (user, item) 一行，
# 观看秒数 = position_ticks/1e7（与 backend.panel_stats.playback_seconds 同口径）。
_session_seq = [0]


def _mk_session(db, user, name, minutes_ago=10, duration=300, genres=""):
    from backend.emby_server import models as em
    _session_seq[0] += 1
    n = _session_seq[0]
    item = em.MediaItem(
        guid=f"sess-r45-{n:06d}",
        library_id=1, item_type="movie", name=name,
        date_added=datetime.now(), genres=genres,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    s = em.PlaybackSession(
        session_key=f"sess-r45-key-{n:06d}",
        user_id=user.id,
        item_id=item.id,
        start_time=datetime.now() - timedelta(minutes=minutes_ago),
        last_update_at=datetime.now() - timedelta(minutes=minutes_ago),
        position_ticks=int(duration * 10_000_000),
    )
    db.add(s)
    db.commit()
    return s


# ---------- F6: 馆灵阶段配置形状校验 ----------

def test_read_pet_stages_invalid_shape_falls_back(monkeypatch, db):
    import backend.servers as servers

    cases = ['"oops"', '{"a": 1}', '[]', '[1, 2]', '[{"name": "x"}]',
             '[{"name": "a", "icon": "i", "hours": "not-a-number"}]']
    for raw in cases:
        monkeypatch.setattr(servers, "config_value", lambda db_, k, d="", _r=raw: _r)
        got = panel_api._read_pet_stages(db)
        assert got == [dict(s) for s in panel_api.PET_STAGES_DEFAULT], raw


def test_read_pet_stages_valid_passthrough(monkeypatch, db):
    import backend.servers as servers
    import json

    raw = json.dumps([{"name": "幼灵", "icon": "🌱", "hours": 0},
                      {"name": "灵狐", "icon": "🦊", "hours": 10}])
    monkeypatch.setattr(servers, "config_value", lambda db_, k, d="": raw)
    got = panel_api._read_pet_stages(db)
    assert [s["hours"] for s in got] == [0, 10]


# ---------- F7: _section_or_none 异常后 rollback ----------

def test_section_or_none_rolls_back_session(db, user):
    def bad_block(db_, user_):
        db_.execute(text("select * from no_such_table_xyz"))

    assert panel_api._section_or_none(bad_block, db, user) is None
    # 同一 session 后续查询必须可用（原来会连带 PendingRollbackError）
    assert db.execute(text("select 1")).scalar() == 1


# ---------- F4: user_tastes 有界（5000） ----------

def test_user_tastes_bounded_at_volume(db, user):
    for i in range(5200):
        _mk_session(db, user, f"movie-{i % 50}", minutes_ago=i, duration=300)
    db.commit()
    tastes = pa.user_tastes(db, user.id)
    assert set(tastes.keys()) == {t[0] for t in pa.TASTE_MAP}
    # 5200 条全部进内存也不会炸；口味计数正常返回
    assert all(isinstance(v["count"], int) for v in tastes.values())


def test_user_tastes_query_emits_limit(db, user):
    """哨兵：user_tastes 的口味口径依赖 .limit(5000) 掐住无界内存增长；
    将来有人删掉/挪动 limit 时，这里必须先红。"""
    from sqlalchemy import event

    statements = []

    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        pa.user_tastes(db, user.id)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert any("LIMIT" in s.upper() for s in statements), \
        "user_tastes 发出的 SQL 里没见到 LIMIT——limit(5000) 可能被删了"


