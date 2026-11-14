"""启动链不得删除退役表或退役配置，同时保留过期凭证与缓存清理。

覆盖 api / worker / 单体共用的 ``init_db`` → ``_auto_migrate``，以及
api / worker / EA 都会调用的 ``run_config_self_heal`` 与启动维护。
隔离在临时 SQLite 上，不连接生产库。
"""
from __future__ import annotations

import inspect
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker

from backend import config_self_heal
from backend import database as dbmod
from backend import models as base_models
from backend.database import init_db
from backend.emby_server import models as em
from backend.emby_server import maintenance
from backend.emby_server.play_sign import purge_expired_tokens

_RETIRED_NAMES = (
    "tmdb_dataset_items",
    "tmdb_dataset_meta",
    "uq_tmdb_dataset_tmdb_type",
    "idx_tmdb_dataset_title",
    "idx_tmdb_dataset_type_year",
    "ix_tmdb_dataset_items_tmdb_id",
    "tmdb_dataset_items_id_seq",
    "tmdb_dataset_meta_id_seq",
)


def _retired_destructive(statement: str) -> bool:
    """退役表/索引/序列的 DROP，或 system_configs 的 DELETE。"""
    compact = " ".join(statement.lower().split())
    head = compact.lstrip("(")
    if head.startswith("drop ") and any(name in compact for name in _RETIRED_NAMES):
        return True
    if head.startswith("delete ") and "system_configs" in compact:
        return True
    return False


@pytest.fixture()
def isolated(monkeypatch):
    """把全局 engine / SessionLocal 换到临时库，结束后还原。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine("sqlite:///" + path)
    saved_factory = dbmod.SessionLocal._factory
    monkeypatch.setattr(dbmod, "DATABASE_TYPE", "sqlite")
    monkeypatch.setattr(dbmod, "engine", eng)
    dbmod.configure_session_local(sessionmaker(bind=eng))
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(eng, "before_cursor_execute", _record)
    try:
        yield eng, statements
    finally:
        event.remove(eng, "before_cursor_execute", _record)
        dbmod.configure_session_local(saved_factory)
        eng.dispose()
        os.remove(path)


def _seed_retired_tables(eng) -> None:
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tmdb_dataset_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "tmdb_id VARCHAR(20) NOT NULL, "
            "media_type VARCHAR(20) NOT NULL, "
            "title VARCHAR(300) NOT NULL)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_tmdb_dataset_tmdb_type "
            "ON tmdb_dataset_items (tmdb_id, media_type)"
        ))
        conn.execute(text(
            "CREATE INDEX idx_tmdb_dataset_title ON tmdb_dataset_items (title)"
        ))
        conn.execute(text(
            "CREATE INDEX idx_tmdb_dataset_type_year "
            "ON tmdb_dataset_items (media_type)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_tmdb_dataset_items_tmdb_id "
            "ON tmdb_dataset_items (tmdb_id)"
        ))
        conn.execute(text(
            "CREATE TABLE tmdb_dataset_meta ("
            "id INTEGER PRIMARY KEY, total_rows INTEGER DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO tmdb_dataset_items (tmdb_id, media_type, title) "
            "VALUES ('550', 'movie', 'keep-dataset')"
        ))
        conn.execute(text(
            "INSERT INTO tmdb_dataset_meta (id, total_rows) VALUES (1, 9)"
        ))


def _dataset_title(eng) -> str | None:
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT title FROM tmdb_dataset_items WHERE tmdb_id = '550'"
        )).scalar()


def test_init_db_and_self_heal_keep_retired_records(isolated, tmp_path):
    eng, statements = isolated
    _seed_retired_tables(eng)
    init_db()
    dbmod._auto_migrate()

    assert _dataset_title(eng) == "keep-dataset"
    with eng.connect() as conn:
        meta = conn.execute(text(
            "SELECT total_rows FROM tmdb_dataset_meta WHERE id = 1"
        )).scalar()
    assert meta == 9
    index_names = {ix["name"] for ix in sa_inspect(eng).get_indexes("tmdb_dataset_items")}
    assert {
        "uq_tmdb_dataset_tmdb_type",
        "idx_tmdb_dataset_title",
        "idx_tmdb_dataset_type_year",
        "ix_tmdb_dataset_items_tmdb_id",
    } <= index_names

    db = dbmod.SessionLocal()
    try:
        for key in config_self_heal.DEAD_CONFIG_KEYS:
            db.add(base_models.SystemConfig(key=key, value="kept-" + key, description="retired"))
        db.add(base_models.SystemConfig(key="site_name", value="live", description="live"))
        db.commit()
        example = tmp_path / "env.example"
        example.write_text("EXAMPLE_ONLY=1\n")
        env_file = tmp_path / ".env"
        env_file.write_text("")
        config_self_heal.run_config_self_heal(
            db, example_path=example, env_path=env_file)
        rows = {row.key: row.value for row in db.query(base_models.SystemConfig).all()}
    finally:
        db.close()

    for key in config_self_heal.DEAD_CONFIG_KEYS:
        assert rows[key] == "kept-" + key
    assert rows["site_name"] == "live"
    assert _dataset_title(eng) == "keep-dataset"
    leaked = [sql for sql in statements if _retired_destructive(sql)]
    assert leaked == []


def test_startup_maintenance_still_clears_expired_tokens_and_cache(isolated, monkeypatch):
    eng, statements = isolated
    _seed_retired_tables(eng)
    init_db()

    db = dbmod.SessionLocal()
    try:
        user = base_models.WebUser(username="startup-keep", password_hash="x")
        db.add(user)
        db.commit()
        expired = em.EmbyApiToken(
            token="a" * 64, user_id=user.id,
            expires_at=datetime.now() - timedelta(days=2))
        live = em.EmbyApiToken(
            token="b" * 64, user_id=user.id,
            expires_at=datetime.now() + timedelta(days=2))
        legacy = em.EmbyApiToken(
            token="c" * 64, user_id=user.id, expires_at=None)
        db.add_all([expired, live, legacy])
        db.add(base_models.SystemConfig(
            key="welfare_redeem_self_earned_only", value="true", description="retired"))
        db.commit()
        assert purge_expired_tokens(db) == 1
    finally:
        db.close()

    calls: list[str] = []
    real_sub = maintenance.prune_subtitle_cache

    def _sub(*args, **kwargs):
        calls.append("subtitle")
        return real_sub(*args, **kwargs)

    def _tmdb(force=False):
        calls.append("tmdb:%s" % force)
        return {"expired_removed": 0, "evicted": 0}

    monkeypatch.setattr(maintenance, "prune_subtitle_cache", _sub)
    monkeypatch.setattr("backend.emby_server.tmdb_cache.prune", _tmdb)

    maintenance.run_startup_maintenance()
    summary = maintenance.janitor_tick(heavy=False)

    assert "subtitle" in calls
    assert "tmdb:True" in calls
    assert "emby_tokens_purged" in summary

    db = dbmod.SessionLocal()
    try:
        tokens = {row.token for row in db.query(em.EmbyApiToken).all()}
        retired = db.query(base_models.SystemConfig).filter(
            base_models.SystemConfig.key == "welfare_redeem_self_earned_only"
        ).one()
    finally:
        db.close()
    assert tokens == {"b" * 64, "c" * 64}
    assert retired.value == "true"
    assert _dataset_title(eng) == "keep-dataset"
    leaked = [sql for sql in statements if _retired_destructive(sql)]
    assert leaked == []


def test_startup_sources_have_no_retired_drop_path():
    database_src = inspect.getsource(dbmod)
    heal_src = inspect.getsource(config_self_heal)
    assert "DROP TABLE" not in database_src
    assert "DROP INDEX IF EXISTS" not in database_src
    assert "DROP SEQUENCE" not in database_src
    assert "_drop_retired_tmdb_dataset_tables" not in database_src
    assert "db.delete" not in heal_src
    assert "_prune_dead_config_keys" not in heal_src
    assert not hasattr(dbmod, "_drop_retired_tmdb_dataset_tables")
    assert not hasattr(config_self_heal, "_prune_dead_config_keys")

    from backend.main import lifespan as api_lifespan
    from backend.worker import main as worker_main
    from emby_api.main import lifespan as ea_lifespan

    api_src = inspect.getsource(api_lifespan)
    worker_src = inspect.getsource(worker_main)
    ea_src = inspect.getsource(ea_lifespan)
    guard = "if not _is_api_role"
    assert api_src.index("init_db()") < api_src.index(guard)
    assert "run_config_self_heal" in api_src
    assert api_src.index(guard) < api_src.index("run_startup_maintenance()")
    assert worker_src.index("init_db()") < worker_src.index("run_config_self_heal")
    assert "run_startup_maintenance()" in worker_src
    assert "run_config_self_heal" in ea_src
    assert "run_startup_maintenance" in ea_src

    janitor_src = inspect.getsource(maintenance.janitor_tick)
    startup_src = inspect.getsource(maintenance.run_startup_maintenance)
    assert "purge_expired_emby_tokens" in janitor_src
    assert "prune_subtitle_cache" in janitor_src
    assert "tmdb_cache.prune" in janitor_src
    assert "prune_subtitle_cache()" in startup_src
    assert "tmdb_cache.prune" in startup_src
