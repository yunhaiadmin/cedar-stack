"""弹幕 API 能力回归测试（专职 Token + 线路 + 公开下发）。

覆盖口径（与 backend/danmu_api.py 实现同源）：
- 配置家族 danmu_api 在 config_service.SCHEMAS 挂名：线路 JSON 与
  外部源开关两键，未知键拒绝；
- 专职 Token：每用户恰一个（重复生成不新增行、回同一到期时刻）、
  32 位十六进制、有效期一年、不回明文（状态/生成响应均不含 token
  本体，地址接口是唯一拼装出口）；
- 删除即失效：删后状态为空、地址 409、旧 token 公开下发 404；
- 过期 token 按未生成处理：状态 exists=False、重新生成换新值；
- 线路：默认两条（国际方向 cdn / 国际方向-备用 www，实测均可路由
  到后端 /api），配置覆盖后列表与地址随之变；地址拼装
  https://<host>/api/v1/danmu/<token>/ 只换 host；enabled 可选
  字段缺省启用（旧配置兼容），暂停线路仍在列表带 enabled=false、
  /address 回 409、公开下发不受线路状态影响；
- 公开下发：按 Danmu 插件约定（bilibili 风格 XML）回站内弹幕，
  按条目 guid 寻址、按时间轴升序；未知 token 404、未知条目空文档；
- 门槛服务端真校验：无有效套餐 / 流量耗尽 → 回空文档（弹幕内容
  不下发）；校验不产生流量计量（不写 TrafficUsageDaily）。
"""

import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

from backend import models as core  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

GB = 1024 ** 3
_KEYS = ("danmu_api_lines", "danmu_api_external_enabled")


def _invalidate():
    from backend import entitlements as ent
    from backend.integrations import store

    store.invalidate(*_KEYS)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()


# ------------------------------------------------------------ 测试基建

_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402

_TABLES = (core.DanmuApiToken, em.DanmakuComment, em.TrafficUsageDaily,
           em.MediaItem, em.Library, core.UserSubscription,
           core.SubscriptionPlan, core.WebUser, core.SystemConfig)


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """与 test_danmaku 同口径：模块内换绑临时库、结束还原。"""
    saved_engine = _dbmod.engine
    saved_factory = _dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = saved_engine
        _dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        engine.dispose()


@pytest.fixture()
def db():
    _invalidate()
    s = SessionLocal()
    try:
        for table in _TABLES:
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        try:
            for table in _TABLES:
                s.query(table).delete()
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()
            _invalidate()


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: SessionLocal()
    try:
        yield TestClient(app, raise_server_exceptions=False), app
    finally:
        app.dependency_overrides.clear()


def _as_user(app, user):
    from backend.api.user import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user


def _mk_user(db, username, **kw):
    user = core.WebUser(username=username, password_hash="x",
                        is_active=True, **kw)
    db.add(user)
    db.flush()
    return user


def _mk_item(db, name="电影Y"):
    import uuid

    lib = em.Library(guid="E" * 32, name="电影库",
                     collection_type="movies", paths="")
    db.add(lib)
    db.flush()
    item = em.MediaItem(
        guid=uuid.uuid4().hex[:32], library_id=lib.id,
        item_type="movie", name=name, sort_name=name, is_hidden=False,
    )
    db.add(item)
    db.flush()
    return item


def _give_plan(db, user, quota_bytes=100 * GB, days=90):
    from backend import entitlements as ent

    plan = core.SubscriptionPlan(
        name=f"plan-{user.username}", price=10, duration_days=days,
        entitlements={"monthly_traffic_bytes": quota_bytes})
    db.add(plan)
    db.flush()
    now = datetime.now()
    sub = core.UserSubscription(
        user_id=user.id, plan_id=plan.id,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=days), status="active")
    db.add(sub)
    db.commit()
    ent.invalidate(user.id)
    return sub


def _add_danmaku(db, item, user, position, text):
    row = em.DanmakuComment(user_id=user.id, item_id=item.id,
                            position_seconds=position, text=text)
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def scene(db):
    item = _mk_item(db)
    user = _mk_user(db, "dmu_owner")
    poster = _mk_user(db, "dmu_poster")
    db.commit()
    return SimpleNamespace(db=db, item=item, user=user, poster=poster)


def _gen_token(client, app, user):
    _as_user(app, user)
    r = client.post("/api/user/danmu/token")
    assert r.status_code == 200, r.text
    return r


def _token_value(db, user):
    row = (db.query(core.DanmuApiToken)
           .filter_by(user_id=user.id).one())
    return row.token


# ------------------------------------------------------------ 配置家族


class TestSchemaRegistered:
    def test_family_in_config_service(self):
        from backend import config_service

        specs = config_service.SCHEMAS.get("danmu_api")
        assert specs is not None
        assert set(specs) == set(_KEYS)

    def test_unknown_key_rejected(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(db, {"danmu_api_foo": 1},
                                       schema="danmu_api")


# ------------------------------------------------------------ Token 生命周期


def test_status_generate_singleton_and_no_plaintext(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.get("/api/user/danmu/token")
    assert r.status_code == 200, r.text
    assert r.json()["exists"] is False
    assert r.json()["expires_at"] is None

    r = _gen_token(c, app, scene.user)
    body = r.json()
    assert body["exists"] is True
    exp = datetime.fromisoformat(body["expires_at"])
    delta = exp - datetime.now()
    assert timedelta(days=364) < delta < timedelta(days=366)

    token = _token_value(scene.db, scene.user)
    assert len(token) == 32
    assert all(ch in "0123456789abcdef" for ch in token)
    # 明文不许出现在任何用户端响应里（地址接口除外，那是拼装出口）
    assert token not in r.text
    r = c.get("/api/user/danmu/token")
    assert r.json()["exists"] is True
    assert r.json()["expires_at"] == body["expires_at"]
    assert token not in r.text

    # 重复生成：不新增行、到期时刻不变（回现有状态）
    r = _gen_token(c, app, scene.user)
    assert r.json()["expires_at"] == body["expires_at"]
    rows = (scene.db.query(core.DanmuApiToken)
            .filter_by(user_id=scene.user.id).all())
    assert len(rows) == 1


def test_delete_invalidates_token(client, scene):
    c, app = client
    _gen_token(c, app, scene.user)
    token = _token_value(scene.db, scene.user)
    _give_plan(scene.db, scene.user)

    r = c.delete("/api/user/danmu/token")
    assert r.status_code == 200, r.text
    assert r.json()["exists"] is False
    r = c.get("/api/user/danmu/token")
    assert r.json()["exists"] is False
    r = c.get("/api/user/danmu/address", params={"line": "intl"})
    assert r.status_code == 409, r.text
    # 旧 token 公开下发立即失效
    r = c.get(f"/api/v1/danmu/{token}/{scene.item.guid}")
    assert r.status_code == 404, r.text


def test_expired_token_treated_as_absent(client, scene):
    c, app = client
    _as_user(app, scene.user)
    now = datetime.now()
    row = core.DanmuApiToken(
        user_id=scene.user.id, token="a" * 32,
        created_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=35))
    scene.db.add(row)
    scene.db.commit()

    r = c.get("/api/user/danmu/token")
    assert r.json()["exists"] is False
    r = c.get(f"/api/v1/danmu/{'a' * 32}/{scene.item.guid}")
    assert r.status_code == 404, r.text

    r = _gen_token(c, app, scene.user)
    assert r.json()["exists"] is True
    new_token = _token_value(scene.db, scene.user)
    assert new_token != "a" * 32
    rows = (scene.db.query(core.DanmuApiToken)
            .filter_by(user_id=scene.user.id).all())
    assert len(rows) == 1


def test_user_endpoints_require_login(client, scene):
    c, _app = client
    assert c.get("/api/user/danmu/token").status_code == 401
    assert c.post("/api/user/danmu/token").status_code == 401
    assert c.delete("/api/user/danmu/token").status_code == 401
    assert c.get("/api/user/danmu/lines").status_code == 401
    assert c.get("/api/user/danmu/address",
                 params={"line": "intl"}).status_code == 401


# ------------------------------------------------------------ 线路与地址


def test_default_lines(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.get("/api/user/danmu/lines")
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert [ln["id"] for ln in lines] == ["intl", "intl_backup"]
    assert lines[0]["name"] == "国际方向"
    assert lines[0]["host"] == "cdn.aetrix.love"
    assert lines[1]["name"] == "国际方向-备用"
    assert lines[1]["host"] == "www.aetrix.love"


def test_address_assembly_and_line_switch(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.get("/api/user/danmu/address", params={"line": "intl"})
    assert r.status_code == 409, r.text  # 未生成先 409

    _gen_token(c, app, scene.user)
    token = _token_value(scene.db, scene.user)
    r = c.get("/api/user/danmu/address", params={"line": "intl"})
    assert r.status_code == 200, r.text
    assert r.json()["address"] == (
        f"https://cdn.aetrix.love/api/v1/danmu/{token}/")
    r = c.get("/api/user/danmu/address", params={"line": "intl_backup"})
    assert r.json()["address"] == (
        f"https://www.aetrix.love/api/v1/danmu/{token}/")
    r = c.get("/api/user/danmu/address", params={"line": "nope"})
    assert r.status_code == 404, r.text


def test_lines_config_override(client, scene):
    from backend import config_service

    config_service.save_config(
        scene.db,
        {"danmu_api_lines": (
            '[{"id": "main", "name": "主线", "note": "注",'
            ' "host": "dm.example.com"}]')},
        schema="danmu_api")
    scene.db.commit()
    _invalidate()

    c, app = client
    _as_user(app, scene.user)
    lines = c.get("/api/user/danmu/lines").json()["lines"]
    assert [(ln["id"], ln["host"]) for ln in lines] == [
        ("main", "dm.example.com")]
    _gen_token(c, app, scene.user)
    token = _token_value(scene.db, scene.user)
    r = c.get("/api/user/danmu/address", params={"line": "main"})
    assert r.json()["address"] == (
        f"https://dm.example.com/api/v1/danmu/{token}/")


def test_default_lines_all_enabled(client, scene):
    """默认线路恒带 enabled=True（前端据此区分启用/暂停）。"""
    c, app = client
    _as_user(app, scene.user)
    lines = c.get("/api/user/danmu/lines").json()["lines"]
    assert [ln["enabled"] for ln in lines] == [True, True]


def test_lines_config_without_enabled_defaults_enabled(client, scene):
    """旧配置（无 enabled 字段）兼容：读回默认启用、地址照常拼。"""
    from backend import config_service

    config_service.save_config(
        scene.db,
        {"danmu_api_lines": (
            '[{"id": "main", "name": "主线", "note": "",'
            ' "host": "dm.example.com"}]')},
        schema="danmu_api")
    scene.db.commit()
    _invalidate()

    c, app = client
    _as_user(app, scene.user)
    lines = c.get("/api/user/danmu/lines").json()["lines"]
    assert lines[0]["enabled"] is True
    _gen_token(c, app, scene.user)
    token = _token_value(scene.db, scene.user)
    r = c.get("/api/user/danmu/address", params={"line": "main"})
    assert r.status_code == 200, r.text
    assert r.json()["address"] == (
        f"https://dm.example.com/api/v1/danmu/{token}/")


def test_paused_line_listed_but_address_rejected(client, scene):
    """暂停线路：/lines 照常列出带 enabled=false；/address 回 409
    （口径：线路存在但状态冲突，与未生成 token 的 409 同族、
    detail 区分）；启用线路不受影响；公开下发口与线路无关，
    已拼好地址照常取弹幕（本用例以启用线路地址验下发不受影响）。"""
    from backend import config_service

    config_service.save_config(
        scene.db,
        {"danmu_api_lines": (
            '[{"id": "main", "name": "主线", "note": "",'
            ' "host": "dm.example.com", "enabled": true},'
            ' {"id": "bak", "name": "备用", "note": "",'
            ' "host": "bak.example.com", "enabled": false}]')},
        schema="danmu_api")
    scene.db.commit()
    _invalidate()

    c, app = client
    _as_user(app, scene.user)
    lines = c.get("/api/user/danmu/lines").json()["lines"]
    assert [(ln["id"], ln["enabled"]) for ln in lines] == [
        ("main", True), ("bak", False)]

    _gen_token(c, app, scene.user)
    token = _token_value(scene.db, scene.user)
    r = c.get("/api/user/danmu/address", params={"line": "bak"})
    assert r.status_code == 409, r.text
    assert "暂停" in r.json()["detail"]
    r = c.get("/api/user/danmu/address", params={"line": "main"})
    assert r.status_code == 200, r.text
    assert r.json()["address"] == (
        f"https://dm.example.com/api/v1/danmu/{token}/")

    # 下发口不受线路暂停影响：token 有效即可取（无套餐回空文档，
    # 不是 404/409——证明下发路径没被线路状态拦）
    r = c.get(f"/api/v1/danmu/{token}/{scene.item.guid}")
    assert r.status_code == 200, r.text


def test_validate_lines_enabled_must_be_bool(db):
    from backend import danmu_api as core_api

    with pytest.raises(ValueError):
        core_api.validate_lines([{
            "id": "main", "name": "主线", "note": "",
            "host": "dm.example.com", "enabled": "yes"}])
    out = core_api.validate_lines([{
        "id": "main", "name": "主线", "note": "",
        "host": "dm.example.com"}])
    assert out[0]["enabled"] is True


# ------------------------------------------------------------ 公开下发


def _xml_items(text):
    root = ET.fromstring(text)
    assert root.tag == "i"
    return root.findall("d")


def test_delivery_returns_internal_danmaku(client, scene):
    c, app = client
    _gen_token(c, app, scene.user)
    _give_plan(scene.db, scene.user)
    _add_danmaku(scene.db, scene.item, scene.poster, 30.0, "三十秒")
    _add_danmaku(scene.db, scene.item, scene.poster, 10.5, "十秒半")
    token = _token_value(scene.db, scene.user)

    for suffix in ("", "/raw"):
        r = c.get(f"/api/v1/danmu/{token}/{scene.item.guid}{suffix}")
        assert r.status_code == 200, r.text
        items = _xml_items(r.text)
        assert [d.text for d in items] == ["十秒半", "三十秒"]
        assert items[0].get("p").split(",")[0] == "10.5"
    # 裸地址（无条目段）：门槛过 → 空文档
    r = c.get(f"/api/v1/danmu/{token}/")
    assert r.status_code == 200, r.text
    assert _xml_items(r.text) == []
    # 下发不产生流量计量
    assert scene.db.query(em.TrafficUsageDaily).count() == 0


def test_delivery_unknown_token_and_item(client, scene):
    c, app = client
    _gen_token(c, app, scene.user)
    _give_plan(scene.db, scene.user)
    token = _token_value(scene.db, scene.user)

    r = c.get(f"/api/v1/danmu/{'f' * 32}/{scene.item.guid}")
    assert r.status_code == 404, r.text
    r = c.get(f"/api/v1/danmu/not-a-token/{scene.item.guid}")
    assert r.status_code == 404, r.text
    # 有效 token + 未知条目 → 空文档（播放器口径：没弹幕）
    r = c.get(f"/api/v1/danmu/{token}/{'0' * 32}")
    assert r.status_code == 200, r.text
    assert _xml_items(r.text) == []


def test_delivery_gate_no_plan_returns_empty(client, scene):
    c, app = client
    _gen_token(c, app, scene.user)  # 不给套餐
    _add_danmaku(scene.db, scene.item, scene.poster, 5.0, "不该下发")
    token = _token_value(scene.db, scene.user)
    r = c.get(f"/api/v1/danmu/{token}/{scene.item.guid}")
    assert r.status_code == 200, r.text
    assert _xml_items(r.text) == []


def test_delivery_gate_traffic_exhausted_returns_empty(client, scene):
    c, app = client
    _gen_token(c, app, scene.user)
    _give_plan(scene.db, scene.user, quota_bytes=100)
    today = datetime.now().date()
    scene.db.add(em.TrafficUsageDaily(
        user_id=scene.user.id, day=today, relay_bytes=200,
        local_bytes=0, direct_plays=0))
    scene.db.commit()
    _invalidate()
    _add_danmaku(scene.db, scene.item, scene.poster, 5.0, "不该下发")
    token = _token_value(scene.db, scene.user)
    r = c.get(f"/api/v1/danmu/{token}/{scene.item.guid}")
    assert r.status_code == 200, r.text
    assert _xml_items(r.text) == []


def test_external_adapter_default_off(db):
    from backend import danmu_api as core_api

    assert core_api.external_enabled(db) is False
    assert core_api.fetch_external_danmaku(db, item_id=1) == []


# ------------------------------------------------------------ 管理端线路配置


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "PUT", "path": "/",
                    "client": ("127.0.0.1", 12345), "headers": []})


def test_admin_lines_roundtrip_and_validation(db):
    from backend.api.admin_danmu_api import (
        DanmuLinesRequest,
        danmu_lines_get_config,
        danmu_lines_update_config,
    )

    admin = _mk_user(db, "dmu_admin", is_staff=True)
    db.commit()
    out = danmu_lines_get_config(admin, db)
    assert [ln["id"] for ln in out["lines"]] == ["intl", "intl_backup"]

    out = danmu_lines_update_config(
        DanmuLinesRequest(lines=[{
            "id": "main", "name": "主线", "note": "注",
            "host": "dm.example.com"}]),
        _request(), admin, db)
    assert [(ln["id"], ln["host"]) for ln in out["lines"]] == [
        ("main", "dm.example.com")]
    _invalidate()
    assert danmu_lines_get_config(admin, db) == out

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        danmu_lines_update_config(
            DanmuLinesRequest(lines=[{
                "id": "bad id", "name": "x", "note": "",
                "host": "https://dm.example.com/path"}]),
            _request(), admin, db)
    assert exc.value.status_code == 400


def test_admin_lines_enabled_roundtrip(db):
    """管理端保存 enabled=false 落库并读回；不带 enabled 时默认启用。"""
    from backend.api.admin_danmu_api import (
        DanmuLinesRequest,
        danmu_lines_get_config,
        danmu_lines_update_config,
    )

    admin = _mk_user(db, "dmu_admin2", is_staff=True)
    db.commit()
    out = danmu_lines_update_config(
        DanmuLinesRequest(lines=[
            {"id": "main", "name": "主线", "note": "",
             "host": "dm.example.com", "enabled": True},
            {"id": "bak", "name": "备用", "note": "",
             "host": "bak.example.com", "enabled": False}]),
        _request(), admin, db)
    assert [(ln["id"], ln["enabled"]) for ln in out["lines"]] == [
        ("main", True), ("bak", False)]
    _invalidate()
    assert danmu_lines_get_config(admin, db) == out

    out = danmu_lines_update_config(
        DanmuLinesRequest(lines=[{
            "id": "main", "name": "主线", "note": "",
            "host": "dm.example.com"}]),
        _request(), admin, db)
    assert out["lines"][0]["enabled"] is True
