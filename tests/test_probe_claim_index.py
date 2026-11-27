"""C-04（2026-11-28 优化扫荡）：探测抢单覆盖索引 idx_item_probe_claim。

深读结论：probe_worker._claim_rows 的过滤列（file_path / item_type /
video_codec / duration_ticks / last_probed_at / probe_next_retry_at /
library_id）全不在既有 idx_item_probe（probe_status, probe_priority,
id）里——pending 约 4.4 万行、调度器每约 3.3 秒抢一轮，每轮都要逐行
回表评估多路 OR（缺信息 / 轨道补探 NOT EXISTS）才能凑满 LIMIT，
pg_stat_statements 6.3 天该查询约 22.5 万次 × 19~47ms ≈ 5,935 秒
（全 DB 约 10.2%）。

本文件钉死：
① _LEGACY_INDEXES 规格与 models.py 声明同名同键同谓词同 INCLUDE；
② PG 渲染出部分索引 WHERE + INCLUDE；
③ _ensure_index 在老库（SQLite 口径）真能补建且幂等；
④ 索引只改执行计划不改语义：抢单分支矩阵（缺编码 / 缺时长未探 /
   轨道补探 / 信息齐全 / 未到期 / 已删 / 已合并 / 类型不对 / 无文件）
   的认领集合与顺序逐字不变。
"""
import os
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import database
from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import probe_worker

_INCLUDE = ("file_path, item_type, video_codec, duration_ticks, "
            "last_probed_at, probe_next_retry_at, library_id")
_WHERE = ("probe_status = 'pending' AND deleted_at IS NULL "
          "AND merged_into_id IS NULL")


# ---------- ①② 规格与模型声明一致、PG 渲染 ----------

def test_probe_claim_index_spec_and_pg_render():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    spec = next(s for s in database._LEGACY_INDEXES
                if s[1] == "idx_item_probe_claim")
    assert spec[0] == "emby_items"
    assert spec[2] == "probe_status, probe_priority, id"
    assert spec[5] == _WHERE
    assert spec[6] is None
    assert spec[7] == _INCLUDE

    ix = next(i for i in em.MediaItem.__table__.indexes
              if i.name == "idx_item_probe_claim")
    assert [c.name for c in ix.columns] == ["probe_status", "probe_priority", "id"]
    pg = ix.dialect_options.get("postgresql", {})
    assert str(pg.get("where")).strip() == _WHERE
    assert list(pg.get("include")) == _INCLUDE.split(", ")
    ddl = str(CreateIndex(ix).compile(dialect=postgresql.dialect()))
    assert f"INCLUDE ({_INCLUDE})" in ddl
    assert f"WHERE {_WHERE}" in ddl


# ---------- ③ 老库补建与幂等 ----------

def test_ensure_index_creates_probe_claim_and_idempotent(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    base_models.Base.metadata.create_all(eng)
    em.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("DROP INDEX idx_item_probe_claim"))
    assert "idx_item_probe_claim" not in {
        ix["name"] for ix in inspect(eng).get_indexes("emby_items")}

    monkeypatch.setattr(database, "engine", eng)
    spec = next(s for s in database._LEGACY_INDEXES
                if s[1] == "idx_item_probe_claim")
    database._ensure_index({"emby_items"}, *spec)
    created = {ix["name"]: ix["column_names"]
               for ix in inspect(eng).get_indexes("emby_items")}
    assert created["idx_item_probe_claim"] == (
        ["probe_status", "probe_priority", "id"] + _INCLUDE.split(", "))
    # 幂等：再跑一次不报错、索引仍在
    database._ensure_index({"emby_items"}, *spec)
    assert "idx_item_probe_claim" in {
        ix["name"] for ix in inspect(eng).get_indexes("emby_items")}
    eng.dispose()


# ---------- ④ 抢单语义逐字不变（分支矩阵） ----------

@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    base_models.Base.metadata.create_all(eng)
    em.Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def _item(db, lib, name, **kw):
    kw.setdefault("probe_status", "pending")
    kw.setdefault("item_type", "movie")
    kw.setdefault("file_path", f"/m/{uuid.uuid4().hex}.mkv")
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, name=name,
        sort_name=name, **kw)
    db.add(row)
    db.flush()
    return row


def test_claim_rows_branch_matrix_unchanged(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="测试库",
                     collection_type="movies")
    db.add(lib)
    db.flush()
    now = datetime.now()
    # 可抢：缺编码（优先级 100）
    a = _item(db, lib, "缺编码", probe_priority=100)
    # 可抢：有编码、缺时长且从未探过（同优先级、id 更大 → 排在 a 前）
    b = _item(db, lib, "缺时长未探", item_type="episode", probe_priority=100,
              video_codec="hevc", duration_ticks=0, last_probed_at=None)
    # 不可抢：信息齐全且本进程起来后探过（非补探候选）
    _item(db, lib, "信息齐全", video_codec="hevc", duration_ticks=10 ** 9,
          last_probed_at=now)
    # 可抢：轨道补探——有编码/时长、老版本探过（进程启动前）、无内封轨道
    d = _item(db, lib, "轨道补探", probe_priority=0, video_codec="h264",
              duration_ticks=10 ** 9,
              last_probed_at=now - timedelta(days=365))
    # 不可抢：退避未到期
    _item(db, lib, "未到期", probe_next_retry_at=now + timedelta(hours=1))
    # 不可抢：已合并 / 类型不对 / 无文件
    _item(db, lib, "已合并", merged_into_id=999)
    _item(db, lib, "剧", item_type="series")
    _item(db, lib, "无文件", file_path="")
    db.commit()

    rows = probe_worker._claim_rows(db, 10)
    ids = [r[0] for r in rows]
    assert ids == [b.id, a.id, d.id], "优先级降序、同级 id 降序，补探候选在队尾"
    db.expire_all()
    st = {r.id: r.probe_status for r in db.query(em.MediaItem).all()}
    assert st[b.id] == st[a.id] == st[d.id] == "probing"
    assert sum(1 for v in st.values() if v == "pending") == 5
