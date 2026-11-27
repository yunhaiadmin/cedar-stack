# -*- coding: utf-8 -*-
"""C-06：emby_items 冗余索引清理迁移（_migrate_drop_redundant_item_indexes）。

钉住四件事：
1. 两套都在 → 删 ix_ 留 idx_，且幂等（再跑无变化、无重复日志）；
2. 孪生 idx_ 不在 → ix_ 保留（绝不让表丢索引）；
3. ix_ 形状与预期不符（多列/唯一）→ 保留；
4. 防长回来：models 三列不再带 index=True（create_all 不建 ix_），
   _LEGACY_INDEXES 仍含三条 idx_ spec（新库/老库都只有这一套）。
"""
import logging
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from sqlalchemy import create_engine, inspect, text

from backend import database

_PAIRS = (
    ("ix_emby_items_drive_file_id", "idx_item_drive_file_id",
     "drive_file_id"),
    ("ix_emby_items_content_hash", "idx_item_content_hash", "content_hash"),
    ("ix_emby_items_merged_into_id", "idx_item_merged_into_id",
     "merged_into_id"),
)


def _engine(tmp_path, with_idx=True, with_ix=True, ix_extra=""):
    eng = create_engine(f"sqlite:///{tmp_path / 'dropidx.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE emby_items (id INTEGER PRIMARY KEY, "
            "drive_file_id TEXT, content_hash TEXT, merged_into_id INTEGER)"))
        for ix_name, idx_name, col in _PAIRS:
            if with_ix:
                conn.execute(text(
                    f"CREATE INDEX {ix_name} ON emby_items ({col}{ix_extra})"))
            if with_idx:
                conn.execute(text(
                    f"CREATE INDEX {idx_name} ON emby_items ({col})"))
    return eng


def _names(eng):
    return {ix["name"] for ix in inspect(eng).get_indexes("emby_items")}


def test_drops_ix_keeps_idx_and_idempotent(tmp_path, monkeypatch, caplog):
    eng = _engine(tmp_path)
    monkeypatch.setattr(database, "engine", eng)
    with caplog.at_level(logging.INFO, logger=database.__name__):
        database._migrate_drop_redundant_item_indexes({"emby_items"})
    names = _names(eng)
    for ix_name, idx_name, _ in _PAIRS:
        assert ix_name not in names
        assert idx_name in names
    logs = "\n".join(r.getMessage() for r in caplog.records
                     if r.name == database.__name__)
    assert logs.count("删除冗余索引") == 3
    # 幂等：再跑一遍，无变化、无新日志
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=database.__name__):
        database._migrate_drop_redundant_item_indexes({"emby_items"})
    assert _names(eng) == names
    logs = "\n".join(r.getMessage() for r in caplog.records
                     if r.name == database.__name__)
    assert "删除冗余索引" not in logs
    eng.dispose()


def test_keeps_ix_when_twin_missing(tmp_path, monkeypatch):
    eng = _engine(tmp_path, with_idx=False)
    monkeypatch.setattr(database, "engine", eng)
    database._migrate_drop_redundant_item_indexes({"emby_items"})
    names = _names(eng)
    for ix_name, _, _ in _PAIRS:
        assert ix_name in names  # 孪生不在，一个都不许删
    eng.dispose()


def test_keeps_ix_when_shape_differs(tmp_path, monkeypatch):
    # ix_ 被建成两列复合索引（与 idx_ 不再等价）→ 不删
    eng = _engine(tmp_path, ix_extra=", id")
    monkeypatch.setattr(database, "engine", eng)
    database._migrate_drop_redundant_item_indexes({"emby_items"})
    names = _names(eng)
    for ix_name, idx_name, _ in _PAIRS:
        assert ix_name in names
        assert idx_name in names
    eng.dispose()


def test_missing_table_is_skipped(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.setattr(database, "engine", eng)
    database._migrate_drop_redundant_item_indexes(set())  # 不抛错
    assert len(_names(eng)) == 6
    eng.dispose()


def test_models_and_legacy_spec_do_not_regrow_ix():
    """防长回来：模型列不带 index=True，_LEGACY_INDEXES 保留 idx_ 一套。"""
    from backend.emby_server import models as em

    cols = em.MediaItem.__table__.columns
    for col in ("drive_file_id", "content_hash", "merged_into_id"):
        assert not cols[col].index, f"{col} 不应再带 index=True（C-06）"
    spec_names = {spec[1] for spec in database._LEGACY_INDEXES
                  if spec[0] == "emby_items"}
    for _, idx_name, _ in _PAIRS:
        assert idx_name in spec_names
