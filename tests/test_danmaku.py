"""站内自发弹幕回归测试（网页播放器）。

覆盖口径：
- 配置家族 danmaku 在 config_service.SCHEMAS 挂名，四键逐键对齐
  （danmaku_enabled / danmaku_blocked_words /
  danmaku_min_interval_seconds / danmaku_max_text_length），
  未知键拒绝、int 越界拒绝；
- 站点开关默认关：关时用户端两口 404；
- 发送→拉取闭环：按条目 guid 拉时间窗内弹幕、按时间轴排序；
- 敏感词：词表走配置键，命中拒绝（400）且不落库，词表空不过滤；
- 字数上限与最小发送间隔都从配置读：超长 400、间隔内连发 429；
- 未登录 401；给不存在的条目发 404；
- 子账号按本人记 user_id（弹幕是发言行为，不归主账号）；
- 管理端配置读/写往返，默认值正确。
"""

import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

from backend import models as core  # noqa: E402
from backend.emby_server import models as em  # noqa: E402

_DANMAKU_KEYS = (
    "danmaku_enabled",
    "danmaku_blocked_words",
    "danmaku_min_interval_seconds",
    "danmaku_max_text_length",
)


def _invalidate_config_cache():
    from backend.integrations import store

    store.invalidate(*_DANMAKU_KEYS)


# ------------------------------------------------------------ 配置家族


class TestSchemaRegistered:
    def test_family_in_config_service(self):
        from backend import config_service

        specs = config_service.SCHEMAS.get("danmaku")
        assert specs is not None
        assert set(specs) == set(_DANMAKU_KEYS)
        assert specs["danmaku_enabled"] == ("bool",)
        assert specs["danmaku_blocked_words"] == ("str",)
        assert specs["danmaku_min_interval_seconds"][0] == "int"
        assert specs["danmaku_max_text_length"][0] == "int"

    def test_unknown_key_rejected(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(db, {"danmaku_foo": 1}, schema="danmaku")

    def test_int_ranges_enforced(self, db):
        from backend import config_service
        from backend.config_schema import ConfigValueError

        with pytest.raises(ConfigValueError):
            config_service.save_config(
                db, {"danmaku_max_text_length": 0}, schema="danmaku")
        with pytest.raises(ConfigValueError):
            config_service.save_config(
                db, {"danmaku_min_interval_seconds": -1}, schema="danmaku")


# ------------------------------------------------------------ 测试基建

_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """与 test_nextup_dismissals 同口径：模块内换绑临时库、结束还原。"""
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
    _invalidate_config_cache()
    s = SessionLocal()
    try:
        for table in (em.DanmakuComment, em.MediaItem, em.Library,
                      core.WebUser, core.SystemConfig):
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        try:
            for table in (em.DanmakuComment, em.MediaItem, em.Library,
                          core.WebUser, core.SystemConfig):
                s.query(table).delete()
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()
            _invalidate_config_cache()


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


def _mk_item(db, name="电影X"):
    import uuid

    lib = em.Library(guid="D" * 32, name="电影库",
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


def _mk_user(db, username, **kw):
    user = core.WebUser(username=username, password_hash="x",
                        is_active=True, **kw)
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def scene(db):
    item = _mk_item(db)
    user = _mk_user(db, "dm_u1")
    other = _mk_user(db, "dm_u2")
    db.commit()
    return SimpleNamespace(db=db, item=item, user=user, other=other)


def _save(db, values):
    from backend import config_service

    config_service.save_config(db, values, schema="danmaku")
    db.commit()
    _invalidate_config_cache()


def _enable(db, **extra):
    values = {"danmaku_enabled": True, "danmaku_min_interval_seconds": 0}
    values.update(extra)
    _save(db, values)


def _post(client, guid, position, text):
    return client.post(f"/api/user/danmaku/{guid}",
                       json={"position_seconds": position, "text": text})


# ------------------------------------------------------------ 开关关


def test_endpoints_404_when_disabled_by_default(client, scene):
    c, app = client
    _as_user(app, scene.user)
    r = c.get(f"/api/user/danmaku/{scene.item.guid}")
    assert r.status_code == 404, r.text
    r = _post(c, scene.item.guid, 1.0, "你好")
    assert r.status_code == 404, r.text
    assert scene.db.query(em.DanmakuComment).count() == 0


# ------------------------------------------------------------ 闭环


def test_post_then_fetch_roundtrip(client, scene):
    c, app = client
    _enable(scene.db)
    _as_user(app, scene.user)
    r = _post(c, scene.item.guid, 12.5, "前面高能")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["ok"] is True
    assert body["item"]["text"] == "前面高能"
    assert body["item"]["position_seconds"] == 12.5
    assert body["item"]["mine"] is True

    r = c.get(f"/api/user/danmaku/{scene.item.guid}",
              params={"start": 0, "end": 60})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["enabled"] is True
    assert data["max_text_length"] == 100
    assert [it["text"] for it in data["items"]] == ["前面高能"]
    assert data["items"][0]["username"] == "dm_u1"

    # 窗口外不返回
    r = c.get(f"/api/user/danmaku/{scene.item.guid}",
              params={"start": 60, "end": 120})
    assert r.json()["items"] == []


def test_fetch_sorted_by_position(client, scene):
    c, app = client
    _enable(scene.db)
    _as_user(app, scene.user)
    _post(c, scene.item.guid, 30.0, "三十秒")
    _post(c, scene.item.guid, 10.0, "十秒")
    _as_user(app, scene.other)
    _post(c, scene.item.guid, 20.0, "二十秒")
    r = c.get(f"/api/user/danmaku/{scene.item.guid}",
              params={"start": 0, "end": 60})
    items = r.json()["items"]
    assert [it["position_seconds"] for it in items] == [10.0, 20.0, 30.0]
    # 别人发的 mine=False
    assert [it["mine"] for it in items] == [False, True, False]


def test_post_unknown_item_404(client, scene):
    c, app = client
    _enable(scene.db)
    _as_user(app, scene.user)
    r = _post(c, "9" * 32, 1.0, "你好")
    assert r.status_code == 404, r.text
    r = c.get(f"/api/user/danmaku/{'9' * 32}")
    assert r.status_code == 404, r.text


def test_post_empty_text_rejected(client, scene):
    c, app = client
    _enable(scene.db)
    _as_user(app, scene.user)
    r = _post(c, scene.item.guid, 1.0, "   ")
    assert r.status_code == 400, r.text
    assert scene.db.query(em.DanmakuComment).count() == 0


# ------------------------------------------------------------ 敏感词


def test_blocked_word_rejected_and_not_stored(client, scene):
    c, app = client
    _enable(scene.db, danmaku_blocked_words="违禁词,敏感")
    _as_user(app, scene.user)
    r = _post(c, scene.item.guid, 1.0, "这里有违禁词哦")
    assert r.status_code == 400, r.text
    assert "敏感词" in r.json()["detail"]
    assert scene.db.query(em.DanmakuComment).count() == 0
    # 干净文本照常发
    r = _post(c, scene.item.guid, 2.0, "干干净净")
    assert r.status_code in (200, 201), r.text


def test_empty_word_list_means_no_filter(client, scene):
    c, app = client
    _enable(scene.db)  # 词表默认空
    _as_user(app, scene.user)
    r = _post(c, scene.item.guid, 1.0, "违禁词三个字本身可以发")
    assert r.status_code in (200, 201), r.text


# ------------------------------------------------------------ 上限与限流


def test_max_text_length_from_config(client, scene):
    c, app = client
    _enable(scene.db, danmaku_max_text_length=5)
    _as_user(app, scene.user)
    r = _post(c, scene.item.guid, 1.0, "六个字弹幕哦")
    assert r.status_code == 400, r.text
    r = _post(c, scene.item.guid, 1.0, "五个字弹幕")
    assert r.status_code in (200, 201), r.text


def test_min_interval_rate_limit(client, scene):
    from backend.ratelimit import reset_rate_limit

    c, app = client
    _enable(scene.db, danmaku_min_interval_seconds=60)
    _as_user(app, scene.user)
    reset_rate_limit(f"danmaku_post:{scene.user.id}")
    r = _post(c, scene.item.guid, 1.0, "第一条")
    assert r.status_code in (200, 201), r.text
    r = _post(c, scene.item.guid, 2.0, "紧跟着的第二条")
    assert r.status_code == 429, r.text
    reset_rate_limit(f"danmaku_post:{scene.user.id}")


# ------------------------------------------------------------ 鉴权与归属


def test_unauthenticated_rejected(client, scene):
    c, _app = client
    _enable(scene.db)
    r = c.get(f"/api/user/danmaku/{scene.item.guid}")
    assert r.status_code == 401, r.text
    r = _post(c, scene.item.guid, 1.0, "你好")
    assert r.status_code == 401, r.text


def test_sub_account_posts_as_self(client, scene):
    c, app = client
    _enable(scene.db)
    sub = _mk_user(scene.db, "dm_sub", parent_user_id=scene.user.id)
    scene.db.commit()
    _as_user(app, sub)
    r = _post(c, scene.item.guid, 3.0, "子账号发的")
    assert r.status_code in (200, 201), r.text
    row = scene.db.query(em.DanmakuComment).one()
    assert row.user_id == sub.id


# ------------------------------------------------------------ 管理端


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "PUT", "path": "/",
                    "client": ("127.0.0.1", 12345), "headers": []})


def test_admin_config_defaults_and_roundtrip(db):
    from backend.api.admin_danmaku import (
        DanmakuConfigRequest,
        danmaku_get_config,
        danmaku_update_config,
    )

    _invalidate_config_cache()
    admin = _mk_user(db, "dm_admin", is_staff=True)
    db.commit()
    out = danmaku_get_config(admin, db)
    assert out == {
        "enabled": False,
        "blocked_words": "",
        "min_interval_seconds": 5,
        "max_text_length": 100,
    }
    out = danmaku_update_config(
        DanmakuConfigRequest(enabled=True, blocked_words="甲,乙",
                             min_interval_seconds=3, max_text_length=50),
        _request(), admin, db)
    assert out["enabled"] is True
    assert out["blocked_words"] == "甲,乙"
    assert out["min_interval_seconds"] == 3
    assert out["max_text_length"] == 50
    _invalidate_config_cache()
    assert danmaku_get_config(admin, db) == out
