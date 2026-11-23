"""钥匙类配置 env 回填（config_self_heal.heal_secrets_from_env）。

口径：
- DB 无行或值为空 + env 非空 → 回填；
- DB 已有非空值 → 绝不覆盖（管理面板是日常真源）；
- env 未设/为空 → 不写；
- 日志只记 key 名，绝不记值。
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine
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


def _rows(session):
    return {row.key: row.value for row in session.query(SystemConfig).all()}


def _add(session, key, value):
    session.add(SystemConfig(key=key, value=value, description="test"))
    session.commit()


def test_refill_when_db_has_no_row():
    engine, db = _db()
    try:
        healed = config_self_heal.heal_secrets_from_env(
            db, environ={"TMDB_API_KEYS": "key-a,key-b"})
        assert healed == ["tmdb_api_keys"]
        assert _rows(db)["tmdb_api_keys"] == "key-a,key-b"
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_refill_when_db_value_is_empty():
    engine, db = _db()
    try:
        _add(db, "telegram_bot_token", "")
        healed = config_self_heal.heal_secrets_from_env(
            db, environ={"TELEGRAM_BOT_TOKEN": "123:abc"})
        assert healed == ["telegram_bot_token"]
        assert _rows(db)["telegram_bot_token"] == "123:abc"
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_existing_db_value_is_never_overwritten():
    engine, db = _db()
    try:
        _add(db, "tmdb_api_keys", "panel-value")
        healed = config_self_heal.heal_secrets_from_env(
            db, environ={"TMDB_API_KEYS": "env-value"})
        assert healed == []
        assert _rows(db)["tmdb_api_keys"] == "panel-value"
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_empty_env_writes_nothing():
    engine, db = _db()
    try:
        healed = config_self_heal.heal_secrets_from_env(
            db, environ={"TMDB_API_KEYS": "", "EMAIL_SMTP_HOST": "  "})
        assert healed == []
        assert _rows(db) == {}
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_batch_refill_count_and_mix():
    engine, db = _db()
    try:
        _add(db, "email_smtp_host", "panel-smtp")  # 已有值：跳过
        _add(db, "email_smtp_user", "")            # 空值：回填
        environ = {
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "EMAIL_SMTP_HOST": "env-smtp",
            "EMAIL_SMTP_USER": "noreply@example.com",
            "EMAIL_SMTP_PASSWORD": "s3cret",
            "PAYMENT_PARTNER_KEY": "",             # env 空：跳过
        }
        healed = config_self_heal.heal_secrets_from_env(db, environ=environ)
        assert sorted(healed) == [
            "email_smtp_password", "email_smtp_user", "telegram_bot_token",
        ]
        rows = _rows(db)
        assert rows["email_smtp_host"] == "panel-smtp"
        assert rows["email_smtp_user"] == "noreply@example.com"
        assert rows["email_smtp_password"] == "s3cret"
        assert rows["telegram_bot_token"] == "123:abc"
        assert "payment_partner_key" not in rows
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_secret_values_never_appear_in_logs(caplog):
    engine, db = _db()
    try:
        with caplog.at_level(logging.INFO, logger="backend.config_self_heal"):
            config_self_heal.heal_secrets_from_env(
                db, environ={"EMAIL_SMTP_PASSWORD": "s3cret-value"})
        assert "s3cret-value" not in caplog.text
        assert "email_smtp_password" in caplog.text
    finally:
        db.close()
        store.invalidate()
        engine.dispose()


def test_registry_env_names_are_uppercased_db_keys():
    for key, env_name, _desc in config_self_heal.SECRET_REFILL:
        assert env_name == key.upper()
    keys = [k for k, _, _ in config_self_heal.SECRET_REFILL]
    for expected in (
        "telegram_bot_token", "payment_gateway_url", "payment_partner_id",
        "payment_partner_key", "email_enabled", "email_smtp_host",
        "email_smtp_port", "email_smtp_user", "email_smtp_password",
        "email_from_name", "email_from_email", "tmdb_api_keys",
        "meta_source_keys_omdb", "meta_source_keys_tvdb",
    ):
        assert expected in keys


def test_run_config_self_heal_refills_secrets(tmp_path, monkeypatch):
    engine, db = _db()
    try:
        monkeypatch.setenv("TMDB_API_KEYS", "key-a")
        example = tmp_path / "env.example"
        example.write_text("# comment\nSITE_TITLE=demo\n")
        env_file = tmp_path / ".env"
        env_file.write_text("")
        result = config_self_heal.run_config_self_heal(
            db, example_path=example, env_path=env_file)
        assert result["secrets_refilled"] == ["tmdb_api_keys"]
        assert _rows(db)["tmdb_api_keys"] == "key-a"
    finally:
        db.close()
        store.invalidate()
        engine.dispose()
