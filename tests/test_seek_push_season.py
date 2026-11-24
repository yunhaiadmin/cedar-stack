"""L-025 回归：求片推送 MoviePilot 时季号不得丢弃。

``MovieRequest.season`` 存 ``"1,2"`` / ``"all"``（落单时 normalize_season
归一），推送口径：

- 单季（如 ``"2"``）→ subscribe 传 ``season=2``；
- 多季（如 ``"1,2"``）→ 逐季各建一个带 season 的订阅；
- ``"all"`` → 整剧订阅（不传 season）；
- 电影（无季）→ 行为不变；
- 部分季失败 → 整体 ok=False，消息如实写明哪季失败，不假成功。

MP 客户端全 mock（替换 ``backend.moviepilot.subscribe`` 模块属性），不真连 MP。
"""

import asyncio
import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend import models, moviepilot, servers  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """文件库：push_media_seek 把同步查库下放线程池，内存库连接跨不了线程。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'seek_push.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _mp_server(db):
    row = models.RemoteServer(
        name="MoviePilot", kind="moviepilot", url="http://mp.local:3000",
        config=json.dumps({"username": "u", "password": "p",
                           "save_path": "/downloads"}),
        is_enabled=True, last_check_ok=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _request(db, *, type="series", season="all", name="某剧"):
    user = models.WebUser(username=f"u-{type}-{season}-{name}",
                          password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    row = models.MovieRequest(
        user_id=user.id, movie_name=name, year="2024", type=type,
        season=season, status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class _FakeSubscribe:
    """假 MP subscribe：记录每次调用的 kwargs，可按季指定失败。"""

    def __init__(self, fail_seasons=()):
        self.calls = []
        self.fail_seasons = set(fail_seasons)

    async def __call__(self, url, **kwargs):
        self.calls.append(kwargs)
        season = kwargs.get("season")
        if season in self.fail_seasons:
            return {"ok": False, "message": "MP 侧建订阅失败", "id": None}
        return {"ok": True, "message": "订阅已提交", "id": 100 + (season or 0)}


def _push(db, request):
    return asyncio.run(servers.push_media_seek(db, request, "moviepilot"))


def test_single_season_passed(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe()
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, season="2"))
    assert result["ok"] is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["season"] == 2
    assert fake.calls[0]["name"] == "某剧"
    assert "2" in result["message"]


def test_multi_season_one_subscribe_each(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe()
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, season="1,2"))
    assert result["ok"] is True
    assert [c["season"] for c in fake.calls] == [1, 2]
    assert "1" in result["message"] and "2" in result["message"]


def test_all_season_no_season_param(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe()
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, season="all"))
    assert result["ok"] is True
    assert len(fake.calls) == 1
    assert not fake.calls[0].get("season")


def test_movie_no_season_unchanged(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe()
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, type="movie", season="", name="某片"))
    assert result["ok"] is True
    assert len(fake.calls) == 1
    assert not fake.calls[0].get("season")
    assert fake.calls[0]["name"] == "某片"


def test_partial_season_failure_reported(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe(fail_seasons={2})
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, season="1,2"))
    assert result["ok"] is False, "部分季失败不许报整体成功"
    assert [c["season"] for c in fake.calls] == [1, 2]
    assert "2" in result["message"] and "失败" in result["message"]


def test_special_season_zero_passed(db, monkeypatch):
    _mp_server(db)
    fake = _FakeSubscribe()
    monkeypatch.setattr(moviepilot, "subscribe", fake)
    result = _push(db, _request(db, season="0"))
    assert result["ok"] is True
    assert len(fake.calls) == 1
    assert fake.calls[0].get("season") == 0


def test_subscribe_payload_includes_season_zero(monkeypatch):
    """moviepilot.subscribe 本体：season=0（特别篇）也要进请求体，不能被 falsy 吞掉。"""
    import httpx

    captured = {}

    async def fake_login(base_url, username, password, timeout=15.0):
        return {"ok": True, "token": "jwt", "message": ""}

    class _Resp:
        status_code = 200

        def json(self):
            return {"success": True, "message": "ok", "data": {"id": 7}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _Resp()

    monkeypatch.setattr(moviepilot, "login", fake_login)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = asyncio.run(moviepilot.subscribe(
        "http://mp.local:3000", username="u", password="p",
        name="某剧", season=0))
    assert result["ok"] is True
    assert captured["payload"]["season"] == 0
