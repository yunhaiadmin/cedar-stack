"""服务器改名全链（2026-11-19 缺口修复）：

- 显示名解析器唯一口径：配置键 emby_server_name > env > 内置默认；
- PUT /api/admin/emby/server-name 落配置 + 审计，GET /emby/servers 回显；
- 连接保存（PUT /api/admin/emby/servers）带 name 时同步行改名，重名 400；
- 客户端 System/Info 的 ServerName 读到新名字。

隔离内存 SQLite + 真路由 + 真鉴权依赖，不重绑全局引擎（防 #1668 毒化）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import servers as registry
from backend.api import emby_servers as emby_servers_api
from backend.database import get_db
from backend.security import create_access_token, hash_password


@pytest.fixture()
def ctx(monkeypatch):
    monkeypatch.delenv("EMBY_SERVER_NAME", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = models.WebUser(username="boss", password_hash=hash_password("x" * 12),
                           is_staff=True, is_active=True, admin_role="super")
    realm = models.ServerRealm(name="Aetrix", slug="main", is_active=True)
    db.add_all([admin, realm])
    db.commit()
    db.refresh(admin)
    db.refresh(realm)
    srv = models.RemoteServer(name="云海emby", kind="ea",
                              url="http://127.0.0.1:9/ea",
                              realm_id=realm.id, is_enabled=True)
    db.add(srv)
    db.commit()
    db.refresh(srv)

    app = FastAPI()
    app.include_router(emby_servers_api.router)
    from backend.emby_server.api import emby_router
    app.include_router(emby_router)
    app.dependency_overrides[get_db] = lambda: db
    auth_value = "Bearer " + create_access_token(admin.id)
    headers = {"Authorization": auth_value}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"db": db, "client": client, "headers": headers,
               "realm": realm, "srv": srv}
    db.close()
    engine.dispose()


def test_display_name_fallback_chain(ctx, monkeypatch):
    db = ctx["db"]
    # 无配置无 env → 内置默认
    assert registry.server_display_name(db) == "Aetrix Media Server"
    # env 兜底
    monkeypatch.setenv("EMBY_SERVER_NAME", "环境变量名")
    assert registry.server_display_name(db) == "环境变量名"
    # 配置键优先于 env
    registry.set_config(db, registry.SERVER_NAME_CONFIG_KEY, "后台改的名", "test")
    db.commit()
    assert registry.server_display_name(db) == "后台改的名"
    # db=None 时只走 env/默认，不炸
    assert registry.server_display_name(None) == "环境变量名"


def test_put_server_name_writes_config_and_audit(ctx):
    c, h, db = ctx["client"], ctx["headers"], ctx["db"]
    r = c.put("/api/admin/emby/server-name", headers=h, json={"name": "夜海影院"})
    assert r.status_code == 200, r.text
    assert r.json()["server_name"] == "夜海影院"
    assert registry.server_display_name(db) == "夜海影院"
    log = (db.query(models.AdminLog)
           .filter(models.AdminLog.action == "save_emby_server_name").first())
    assert log is not None, "改名必须落审计"
    # GET 回显当前生效值
    r = c.get("/api/admin/emby/servers", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["server_name"] == "夜海影院"


def test_put_server_name_rejects_blank(ctx):
    r = ctx["client"].put("/api/admin/emby/server-name", headers=ctx["headers"],
                           json={"name": "   "})
    assert r.status_code == 400


def test_get_servers_echoes_entry_names(ctx):
    r = ctx["client"].get("/api/admin/emby/servers", headers=ctx["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["managed_ea"]["name"] == "云海emby"
    assert body["external"]["name"] == ""


def test_connection_save_with_name_renames_synced_row(ctx):
    c, h, db = ctx["client"], ctx["headers"], ctx["db"]
    # 探测必然失败（127.0.0.1:9 无服务），但保存仍应落库并改名
    r = c.put("/api/admin/emby/servers", headers=h, json={
        "mode": "managed_ea", "url": "http://127.0.0.1:9/ea",
        "enabled": True, "name": "连接面改的名"})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.query(models.RemoteServer).filter_by(id=ctx["srv"].id).first()
    assert row.name == "连接面改的名"
    # 只改显示名：kind / realm 关联不动
    assert row.kind == "ea" and row.realm_id == ctx["realm"].id


def test_connection_save_name_conflict_is_400(ctx):
    c, h, db = ctx["client"], ctx["headers"], ctx["db"]
    db.add(models.RemoteServer(name="别的服务器", kind="emby",
                                 url="http://127.0.0.1:9/x",
                                 realm_id=ctx["realm"].id))
    db.commit()
    r = c.put("/api/admin/emby/servers", headers=h, json={
        "mode": "managed_ea", "url": "http://127.0.0.1:9/ea",
        "enabled": True, "name": "别的服务器"})
    assert r.status_code == 400
    assert "已被占用" in r.json()["detail"]
    db.expire_all()
    row = db.query(models.RemoteServer).filter_by(id=ctx["srv"].id).first()
    assert row.name == "云海emby", "冲突时整事务回滚，原名不许被改掉"


def test_system_info_uses_configured_name(ctx):
    c, h = ctx["client"], ctx["headers"]
    r = c.put("/api/admin/emby/server-name", headers=h, json={"name": "夜海影院"})
    assert r.status_code == 200, r.text
    r = c.get("/emby/System/Info/Public")
    assert r.status_code == 200, r.text
    assert r.json()["ServerName"] == "夜海影院"
