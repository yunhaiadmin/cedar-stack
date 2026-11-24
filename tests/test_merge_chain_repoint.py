# -*- coding: utf-8 -*-
"""L-013 合并链改指回归测试。

背景（生产实锤）：17 条 merged_into_id 指向的行自身也有 merged_into_id
（合并链 A→B→C）。成因：merge_group 对已合并条目直接跳过，且主记录 B
被并入新主记录 C 时不改指 B 的子行 A；get_alternate_versions 只查一跳。
结果 A 既不挂在 C 的版本列表里、B 又是隐藏态，副本在下发层等于丢了。

修复口径：
- 写侧：merge_group 把一个主记录并入新主记录时，同一事务内把该主记录
  的全部子行改指到新主记录，杜绝新链产生；
- 读侧防御：get_alternate_versions 做传递闭包（BFS、visited 防环、
  深度上限 ALTERNATE_VERSIONS_MAX_DEPTH），存量链也能读全。

覆盖：
- A→B 后再 B→C：A 改指 C、C 的版本列表含 A；
- 存量链（手工造 A→B→C）读侧传递闭包读全；
- 异常数据成环时 get_alternate_versions 不死循环、不重复。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.emby_server import merge_versions_worker as mvw
from backend.emby_server import models as em
import backend.models  # noqa: F401 — 注册 web_users 等表，供 create_all 建表


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/merge_chain.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _movie(db, name, poster=None, overview=None):
    it = em.MediaItem(
        guid=f"m-{name}-{os.urandom(3).hex()}",
        library_id=1, item_type="movie", name=name,
        tmdb_id="999", file_path="/x",
        poster_path=poster, overview=overview,
    )
    db.add(it)
    db.commit()
    return it


def test_chain_merge_repoints_children_to_new_primary(db):
    """A→B 后再把 B 并入 C：A 必须改指 C，C 的版本列表含 A、B。"""
    a = _movie(db, "电影")                      # 无海报无简介：首轮落选
    b = _movie(db, "电影", poster="/p.jpg")     # 有海报：首轮主记录
    assert mvw.merge_group(db, [a, b]) == 1
    db.commit()
    db.refresh(a)
    assert a.merged_into_id == b.id

    # 次轮：C 有海报+简介，评分压过只有海报的 B，成为新主记录
    c = _movie(db, "电影", poster="/p.jpg", overview="简介")
    assert mvw.merge_group(db, [b, c]) == 1
    db.commit()
    db.refresh(b)
    db.refresh(a)
    assert b.merged_into_id == c.id
    # 关键断言（修复前 A 仍指向 B，形成 A→B→C 链）
    assert a.merged_into_id == c.id
    got = {v.id for v in mvw.get_alternate_versions(db, c.id)}
    assert got == {a.id, b.id, c.id}


def test_get_alternate_versions_transitive_closure_on_existing_chain(db):
    """存量链（手工造 A→B→C）：读侧传递闭包应把 A 也带出来。"""
    a = _movie(db, "电影")
    b = _movie(db, "电影", poster="/p.jpg")
    c = _movie(db, "电影", poster="/p.jpg", overview="简介")
    a.merged_into_id = b.id
    b.merged_into_id = c.id
    db.commit()
    got = {v.id for v in mvw.get_alternate_versions(
        db, c.id, apply_display_switches=False)}
    assert got == {a.id, b.id, c.id}


def test_get_alternate_versions_cycle_does_not_loop(db):
    """异常数据成环（A↔B 互指）：不死循环、不重复返回。"""
    a = _movie(db, "电影")
    b = _movie(db, "电影", poster="/p.jpg")
    a.merged_into_id = b.id
    b.merged_into_id = a.id
    db.commit()
    got = mvw.get_alternate_versions(db, a.id, apply_display_switches=False)
    assert [v.id for v in got] == [a.id, b.id]


def test_get_alternate_versions_self_loop_does_not_loop(db):
    """自环（A→A）：只返回 A 自己一次。"""
    a = _movie(db, "电影")
    a.merged_into_id = a.id
    db.commit()
    got = mvw.get_alternate_versions(db, a.id, apply_display_switches=False)
    assert [v.id for v in got] == [a.id]
