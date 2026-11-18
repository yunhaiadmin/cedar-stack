"""EA 图片流量观测（F5 前置观测，2026-11-18）。

覆盖：UA 家族归类 / 尺寸分档 / 图片类型归类 / 凭据有无判定 /
进程内聚合计数 / flush 落表与二次 flush 累加 / 开关关闭时零计数 /
中间件只观测不改行为（状态码与响应体原样、非图片路径与非 GET
不计数）。

观测层纪律：只观测、不改变任何鉴权与放行行为。本文件的中间件
用例用最小 FastAPI 应用挂 ImageObsMiddleware + 假图片端点，
断言响应与无中间件时一致，计数只反映「发生过什么」。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import models  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import image_obs  # noqa: E402

DAY = date(2026, 11, 18)


@pytest.fixture(autouse=True)
def _obs_isolation():
    init_db()
    image_obs._reset_for_tests()
    with SessionLocal() as db:
        db.query(models.EmbyImageObsDaily).delete()
        db.commit()
    yield
    image_obs._reset_for_tests()


def _rows():
    with SessionLocal() as db:
        return db.query(models.EmbyImageObsDaily).all()


# ---------------------------------------------------------------------------
# 归类口径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ua,family", [
    ("Reflix/3.1 (iOS)", "reflix"),
    ("Lenna/1.0", "lenna"),
    ("Yamby/2.4 Android", "yamby"),
    ("Infuse/7.7", "infuse"),
    ("VidHub/1.2", "vidhub"),
    ("SenPlayer/4.0", "senplayer"),
    ("Fileball/2.0", "fileball"),
    ("Hills/1.0", "hills"),
    ("Emby/4.8.0 (Android)", "emby"),
    ("MediaBrowser/1.0", "emby"),
    ("Mozilla/5.0 (iPhone) AppleWebKit/605 Chrome/120", "browser"),
    ("curl/8.4.0", "other"),
    ("python-requests/2.31", "other"),
    ("", "other"),
    (None, "other"),
])
def test_classify_ua_family(ua, family):
    assert image_obs.classify_ua_family(ua) == family


@pytest.mark.parametrize("query,bucket", [
    ({}, "original"),
    ({"tag": ["1"]}, "original"),
    ({"maxHeight": ["600"]}, "standard"),
    ({"maxWidth": ["320"]}, "standard"),
    ({"w": ["1280"]}, "standard"),
    ({"maxWidth": ["3840"]}, "large"),
    ({"h": ["1281"]}, "large"),
    ({"maxWidth": ["abc"]}, "original"),
    ({"maxWidth": ["0"]}, "original"),
    ({"maxWidth": ["-5"]}, "original"),
    ({"maxWidth": ["600"], "maxHeight": ["2000"]}, "large"),
])
def test_classify_size_bucket(query, bucket):
    assert image_obs.classify_size_bucket(query) == bucket


@pytest.mark.parametrize("segment,expected", [
    ("Primary", "Primary"),
    ("Backdrop", "Backdrop"),
    ("Logo", "Logo"),
    ("Foo", "Other"),
    ("", "Other"),
    (None, "Other"),
])
def test_classify_image_type(segment, expected):
    assert image_obs.classify_image_type(segment) == expected


def test_request_has_credential():
    assert image_obs.request_has_credential([(b"x-emby-token", b"abc")], {})
    assert image_obs.request_has_credential(
        [(b"x-mediabrowser-token", b"abc")], {})
    assert image_obs.request_has_credential(
        [(b"x-emby-authorization", b'Emby Token="abc"')], {})
    assert image_obs.request_has_credential(
        [(b"authorization", b"Bearer abc")], {})
    assert image_obs.request_has_credential([], {"api_key": ["abc"]})
    # 没带任何形态 / 带了但为空 = 无凭据
    assert not image_obs.request_has_credential([], {})
    assert not image_obs.request_has_credential([(b"x-emby-token", b"  ")], {})
    assert not image_obs.request_has_credential([], {"api_key": [""]})
    assert not image_obs.request_has_credential(
        [(b"user-agent", b"Reflix")], {"tag": ["1"]})


# ---------------------------------------------------------------------------
# 聚合计数 + flush 落表
# ---------------------------------------------------------------------------


def test_record_and_flush_aggregates():
    for _ in range(3):
        image_obs.record(DAY, "reflix", False, "Primary", "standard", 200)
    image_obs.record(DAY, "other", False, "Primary", "original", 404)
    assert image_obs.flush_now() == 2

    rows = {(r.ua_family, r.status): r for r in _rows()}
    assert len(rows) == 2
    hit = rows[("reflix", 200)]
    assert hit.count == 3
    assert hit.day == DAY
    assert hit.has_credential is False
    assert hit.image_type == "Primary"
    assert hit.size_bucket == "standard"
    assert rows[("other", 404)].count == 1

    # 二次 flush 同维度 = 累加到同一行，不新增行
    image_obs.record(DAY, "reflix", False, "Primary", "standard", 200)
    image_obs.record(DAY, "reflix", False, "Primary", "standard", 200)
    assert image_obs.flush_now() == 1
    rows = _rows()
    assert len(rows) == 2
    assert {(r.ua_family, r.status): r.count for r in rows}[("reflix", 200)] == 5

    # 空计数 flush 是空转
    assert image_obs.flush_now() == 0


def test_disabled_records_nothing():
    image_obs._set_enabled_for_tests(False)
    image_obs.record(DAY, "reflix", False, "Primary", "standard", 200)
    assert image_obs._pending_for_tests() == {}
    assert image_obs.flush_now() == 0
    assert _rows() == []


def test_record_from_scope_garbage_does_not_raise():
    # scope 残缺也不能把观测异常带进响应路径
    image_obs.record_from_scope({}, 200)
    image_obs.record_from_scope({"path": "/Items/x/Images/Primary"}, 200)
    assert len(image_obs._pending_for_tests()) == 1


# ---------------------------------------------------------------------------
# 中间件：只观测、不改行为
# ---------------------------------------------------------------------------


def _obs_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(image_obs.ImageObsMiddleware)

    @app.get("/emby/Items/{item_id}/Images/{image_type}")
    @app.get("/Items/{item_id}/Images/{image_type}")
    def fake_image(item_id: str, image_type: str):
        if item_id == "missing":
            return JSONResponse({"detail": "Item not found"}, status_code=404)
        return PlainTextResponse("img-bytes")

    @app.get("/other")
    def other():
        return PlainTextResponse("other")

    return app


def test_middleware_counts_image_requests_and_passes_response_through():
    client = TestClient(_obs_app())

    r = client.get("/emby/Items/abc/Images/Primary",
                   params={"maxHeight": "600"},
                   headers={"user-agent": "Reflix/3.1"})
    assert r.status_code == 200
    assert r.text == "img-bytes"

    r = client.get("/Items/abc/Images/Backdrop",
                   params={"api_key": "k"},
                   headers={"user-agent": "Emby/4.8"})
    assert r.status_code == 200

    r = client.get("/emby/Items/missing/Images/Primary",
                   headers={"user-agent": "curl/8"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Item not found"}

    # 非图片路径与非 GET 不计数
    assert client.get("/other").status_code == 200
    assert client.post("/emby/Items/abc/Images/Primary").status_code in (404, 405)

    pending = image_obs._pending_for_tests()
    assert len(pending) == 3
    by_dims = {(k[1], k[2], k[3], k[4], k[5]): v for k, v in pending.items()}
    assert by_dims[("reflix", False, "Primary", "standard", 200)] == 1
    assert by_dims[("emby", True, "Backdrop", "original", 200)] == 1
    assert by_dims[("other", False, "Primary", "original", 404)] == 1


def test_middleware_disabled_counts_nothing():
    image_obs._set_enabled_for_tests(False)
    client = TestClient(_obs_app())
    r = client.get("/emby/Items/abc/Images/Primary",
                   headers={"user-agent": "Reflix/3.1"})
    assert r.status_code == 200
    assert image_obs._pending_for_tests() == {}
