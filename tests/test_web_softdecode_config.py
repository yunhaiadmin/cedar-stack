"""网页软解开关（2026-11-14 批 2）：配置家族 + 管理端保存 + 公开端点口径。

覆盖：
- config_service.SCHEMAS 里 "web_softdecode" 家族已挂名，只有
  web_softdecode_enabled 一键，("bool",) word 风格；
- 保存往返：开/关即时生效；非法值（非布尔）400；未知键拒绝；
- 公开端点 /api/site/web-softdecode：无行 = 关；保存开后读到开；
- 管理端读写：默认关；保存开后读到开。
"""
import pytest
from starlette.requests import Request


def _fresh_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.database import Base
    from backend.emby_server import models as em  # noqa: F401 注册模型

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def db():
    from backend.integrations import store

    d = _fresh_db()
    # store 有进程级 TTL 缓存：跨用例先失效，避免读到上一个内存库的旧值
    store.invalidate("web_softdecode_enabled")
    try:
        yield d
    finally:
        store.invalidate("web_softdecode_enabled")
        d.close()


@pytest.fixture()
def admin(db):
    from backend import models

    user = models.WebUser(username="sd_admin", password_hash="x",
                          is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    return user


def _request() -> Request:
    return Request({"type": "http", "method": "PUT", "path": "/",
                    "client": ("127.0.0.1", 12345), "headers": []})


class TestSchemaRegistered:
    def test_family_in_config_service(self):
        from backend import config_service

        specs = config_service.SCHEMAS.get("web_softdecode")
        assert specs is not None
        assert set(specs) == {"web_softdecode_enabled"}
        assert specs["web_softdecode_enabled"] == ("bool",)

    def test_unknown_key_rejected(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(
                db, {"web_softdecode_foo": True}, schema="web_softdecode")

    def test_non_bool_rejected(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(
                db, {"web_softdecode_enabled": "maybe"}, schema="web_softdecode")


class TestSaveRoundTrip:
    def test_default_off_then_on(self, db):
        from backend.api.admin_softdecode import softdecode_enabled
        from backend import config_service

        assert softdecode_enabled(db) is False
        config_service.save_config(
            db, {"web_softdecode_enabled": True}, schema="web_softdecode")
        assert softdecode_enabled(db) is True
        config_service.save_config(
            db, {"web_softdecode_enabled": False}, schema="web_softdecode")
        assert softdecode_enabled(db) is False


class TestEndpoints:
    def test_public_endpoint_default_off(self, db):
        from backend.api.site import get_web_softdecode

        assert get_web_softdecode(db) == {"enabled": False}

    def test_public_endpoint_after_save_on(self, db):
        from backend import config_service
        from backend.api.site import get_web_softdecode

        config_service.save_config(
            db, {"web_softdecode_enabled": True}, schema="web_softdecode")
        assert get_web_softdecode(db) == {"enabled": True}

    def test_admin_get_default_off(self, db, admin):
        from backend.api.admin_softdecode import softdecode_get_config

        assert softdecode_get_config(admin, db) == {"enabled": False}

    def test_admin_put_round_trip(self, db, admin):
        from backend.api.admin_softdecode import (
            SoftdecodeConfigRequest,
            softdecode_get_config,
            softdecode_update_config,
        )

        out = softdecode_update_config(
            SoftdecodeConfigRequest(enabled=True), _request(), admin, db)
        assert out == {"enabled": True}
        assert softdecode_get_config(admin, db) == {"enabled": True}
        out = softdecode_update_config(
            SoftdecodeConfigRequest(enabled=False), _request(), admin, db)
        assert out == {"enabled": False}
