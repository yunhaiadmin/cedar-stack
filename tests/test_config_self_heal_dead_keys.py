"""启动自愈保留已退役配置行，不删除。

DEAD_CONFIG_KEYS 只作登记。heal_system_config / run_config_self_heal
不得 DELETE 这些行，也不得改写它们的值。活键仍可补缺。
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend import config_self_heal
from backend import models as base_models
from backend.integrations import store
from backend.models import SystemConfig


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    base_models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    return engine, session


def _add(session, key, value="true"):
    session.add(SystemConfig(key=key, value=value, description="test"))
    session.commit()


def test_heal_preserves_every_retired_config_row():
    engine, db = _db()
    try:
        for key in config_self_heal.DEAD_CONFIG_KEYS:
            _add(db, key, "kept")
        _add(db, "site_name", "x")
        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            config_self_heal.heal_system_config(db)
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        rows = {row.key: row.value for row in db.query(SystemConfig).all()}
        for key in config_self_heal.DEAD_CONFIG_KEYS:
            assert rows[key] == "kept"
        assert rows["site_name"] == "x"
        leaked = [
            sql for sql in statements
            if " ".join(sql.lower().split()).lstrip("(").startswith("delete ")
            and "system_configs" in sql.lower()
        ]
        assert leaked == []
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_run_config_self_heal_preserves_retired_rows(tmp_path):
    engine, db = _db()
    try:
        _add(db, "playback_transcode_enabled", "true")
        _add(db, "welfare_redeem_self_earned_only", "true")
        _add(db, "subscription_buy_self_earned_only", "1")
        example = tmp_path / "env.example"
        example.write_text("# comment\nSITE_TITLE=demo\n")
        env_file = tmp_path / ".env"
        env_file.write_text("")

        config_self_heal.run_config_self_heal(
            db, example_path=example, env_path=env_file)

        rows = {row.key: row.value for row in db.query(SystemConfig).all()}
        assert rows["playback_transcode_enabled"] == "true"
        assert rows["welfare_redeem_self_earned_only"] == "true"
        assert rows["subscription_buy_self_earned_only"] == "1"
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_retired_config_list_still_names_the_known_keys():
    for key in (
        "panel_auto_scan",
        "panel_douban_first",
        "panel_night_scrape",
        "panel_vitality_guard",
        "chase_new_strm_enabled",
        "local_cache_enabled",
        "playback_prewarm_enabled",
        "cdn_enabled",
        "playback_transcode_enabled",
        "chat_points_enabled",
        "welfare_redeem_self_earned_only",
        "subscription_buy_self_earned_only",
    ):
        assert key in config_self_heal.DEAD_CONFIG_KEYS
