"""安装应用页（2026-11-13）：下载链接配置家族 + 用户/管理端数据口径。

覆盖：
- config_service.SCHEMAS 里 "install_app" 家族已挂名，6 键全是 ("str",)；
- 用户端 payload：服务器地址只许 CDN 口径；没有代码级默认链接——
  未配的平台不下发（整行不显示），配了才出现；
- 管理端保存往返：配链接即时生效、清空取消配置、configured 标记；
- 保存侧拒绝：坏链接（非 http(s)）400、未知平台 400、家族未知键拒绝。
"""
import pytest
from fastapi import HTTPException
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
    from backend.api.install_app import PLATFORMS

    d = _fresh_db()
    # store 有进程级 TTL 缓存：跨用例先失效，避免读到上一个内存库的旧值
    store.invalidate(*[p["config_key"] for p in PLATFORMS])
    try:
        yield d
    finally:
        store.invalidate(*[p["config_key"] for p in PLATFORMS])
        d.close()


@pytest.fixture()
def admin(db):
    from backend import models

    user = models.WebUser(username="ia_admin", password_hash="x",
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

        specs = config_service.SCHEMAS.get("install_app")
        assert specs is not None
        assert set(specs) == {
            "install_app_ios_url", "install_app_android_url",
            "install_app_android_tv_url", "install_app_apple_tv_url",
            "install_app_windows_url", "install_app_macos_url",
        }
        assert all(spec == ("str",) for spec in specs.values())

    def test_unknown_key_rejected(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(
                db, {"install_app_vr_url": "https://example.com"},
                schema="install_app")


class TestUserPayloadDefaults:
    def test_defaults(self, db):
        import json
        from backend.api.install_app import user_payload

        payload = user_payload(db)
        # 对外地址只有一个口径：CDN 地址，绝不许出现源站地址
        # （源站域名在本文件里也只拼接出现，不落字面量）
        assert payload["server_address"] == "https://cdn.aetrix.love"
        assert "emby" + ".aetrix.love" not in json.dumps(payload)

        # 没有代码级默认链接：一个都没配时不下发任何平台（整行不显示）
        assert payload["platforms"] == []


class TestAdminRoundtrip:
    def test_update_then_reset(self, db, admin):
        from backend.api.install_app import (
            InstallAppConfigRequest, admin_payload, install_app_update_config,
            user_payload,
        )

        before = admin_payload(db)
        assert len(before["platforms"]) == 6
        assert all(p["configured"] is False for p in before["platforms"])

        out = install_app_update_config(
            InstallAppConfigRequest(
                urls={"ios": "https://example.com/emby-ios"}),
            _request(), admin, db)
        by_key = {p["key"]: p for p in out["platforms"]}
        assert by_key["ios"]["url"] == "https://example.com/emby-ios"
        assert by_key["ios"]["configured"] is True
        assert by_key["android"]["configured"] is False
        # 用户端立刻读到新值，且只下发已配的平台
        user_platforms = user_payload(db)["platforms"]
        assert [p["key"] for p in user_platforms] == ["ios"]
        assert user_platforms[0]["url"] == "https://example.com/emby-ios"

        # 清空 = 删行取消配置，用户端整行消失
        out = install_app_update_config(
            InstallAppConfigRequest(urls={"ios": ""}), _request(), admin, db)
        by_key = {p["key"]: p for p in out["platforms"]}
        assert by_key["ios"]["configured"] is False
        assert by_key["ios"]["url"] == ""
        assert user_payload(db)["platforms"] == []

    def test_apple_tv_configured(self, db, admin):
        from backend.api.install_app import (
            InstallAppConfigRequest, install_app_update_config, user_payload,
        )

        install_app_update_config(
            InstallAppConfigRequest(
                urls={"apple_tv": "https://example.com/emby-tvos"}),
            _request(), admin, db)
        by_key = {p["key"]: p for p in user_payload(db)["platforms"]}
        assert by_key["apple_tv"]["url"] == "https://example.com/emby-tvos"


class TestAdminValidation:
    def test_bad_url_rejected(self, db, admin):
        from backend.api.install_app import (
            InstallAppConfigRequest, install_app_update_config,
        )

        for bad in ("ftp://example.com/app", "example.com/app", "https://"):
            with pytest.raises(HTTPException) as ei:
                install_app_update_config(
                    InstallAppConfigRequest(urls={"ios": bad}),
                    _request(), admin, db)
            assert ei.value.status_code == 400

    def test_unknown_platform_rejected(self, db, admin):
        from backend.api.install_app import (
            InstallAppConfigRequest, install_app_update_config,
        )

        with pytest.raises(HTTPException) as ei:
            install_app_update_config(
                InstallAppConfigRequest(urls={"vr": "https://example.com"}),
                _request(), admin, db)
        assert ei.value.status_code == 400
