"""图片交付改走 CF 边缘缓存（2026-11-23 用户亲选，F5 的 C＋D-lite 提前同批）

口径（本文件钉住）：
- 无凭据且过 anon gate 的图片响应改发 ``public`` 缓存头（CF 边缘可缓存），
  带凭据响应维持 ``private`` 不变；谁能取图的权限口径一律不动。
- F5-C：无凭据取图失败（条目不存在 / 库未公开 / 图缺失 / 类型不放行）
  统一同一种失败响应（404 + 同一 body），关掉存在性预言机；
  限流 429 不在此列（与条目存在性无关）。
- F5-D-lite：无凭据出图尺寸封顶——Primary ≤600px 宽、Backdrop ≤1280px 宽，
  沿用 image_store 现行缩放档位体系；带凭据不封顶。
- 本地化总开关 ``EMBY_LOCALIZE_IMAGES`` 默认关：回填 / 逐条入队 /
  取图按需自愈都不再新增落盘；已落盘文件由出图层经 landed_path
  只读命中照常出图（写侧 localize 关态恒空、不回写库）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import io
import json
import uuid

import pytest

PIL = pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import models  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.security import hash_password  # noqa: E402

init_db()

PW = "cf-edge-pw"
_SUFFIX = uuid.uuid4().hex[:8]
UNAME = f"cfedge_{_SUFFIX}"


def _guid():
    return uuid.uuid4().hex


def _width(content: bytes) -> int:
    from PIL import Image

    with Image.open(io.BytesIO(content)) as im:
        return im.size[0]


@pytest.fixture(scope="module")
def client():
    from emby_api.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    from PIL import Image

    tmp = tmp_path_factory.mktemp("cfedge")

    def _img(name, w, h, rgb):
        p = tmp / f"{name}_{_guid()}.jpg"
        Image.new("RGB", (w, h), rgb).save(p, "JPEG")
        return str(p)

    poster_wide = _img("pw", 1200, 1800, (160, 60, 60))
    backdrop_wide = _img("bw", 2400, 1350, (50, 80, 150))
    poster_r = _img("pr", 800, 1200, (40, 130, 70))

    db = SessionLocal()
    try:
        user = models.WebUser(
            username=UNAME, password_hash=hash_password(PW),
            is_active=True, is_staff=False)
        db.add(user)
        db.flush()

        lib_pub = em.Library(guid=_guid(), name=f"CF公开-{_SUFFIX}",
                             collection_type="tvshows", is_enabled=True)
        lib_res = em.Library(guid=_guid(), name=f"CF受限-{_SUFFIX}",
                             collection_type="movies", is_enabled=True)
        db.add_all([lib_pub, lib_res])
        db.flush()

        series = em.MediaItem(
            guid=_guid(), library_id=lib_pub.id, item_type="series",
            name="宽图剧", poster_path=poster_wide,
            backdrop_path=backdrop_wide)
        remote_movie = em.MediaItem(
            guid=_guid(), library_id=lib_pub.id, item_type="movie",
            name="远程图电影",
            primary_image_url="https://img.example/cfedge-wide.jpg")
        missing_movie = em.MediaItem(
            guid=_guid(), library_id=lib_pub.id, item_type="movie",
            name="缺图电影", poster_path=str(tmp / "no-such-file.jpg"))
        restricted = em.MediaItem(
            guid=_guid(), library_id=lib_res.id, item_type="movie",
            name="受限电影", poster_path=poster_r)
        db.add_all([series, remote_movie, missing_movie, restricted])
        db.flush()

        # 可见范围覆盖：只放公开库 → 受限库对 anon gate 不是全体可见
        from backend import library_scope
        from backend.integrations import store

        row = db.query(models.SystemConfig).filter(
            models.SystemConfig.key == library_scope.CONFIG_USER_OVERRIDES
        ).first()
        old_value = row.value if row else None
        overrides = {}
        if old_value:
            try:
                overrides = json.loads(old_value)
            except ValueError:
                overrides = {}
        overrides[str(user.id)] = {"enabled": True,
                                   "libraries": [lib_pub.id]}
        if row:
            row.value = json.dumps(overrides)
        else:
            db.add(models.SystemConfig(
                key=library_scope.CONFIG_USER_OVERRIDES,
                value=json.dumps(overrides)))
        db.commit()
        store.invalidate()

        data = {"user_id": user.id, "series": series.guid,
                "remote": remote_movie.guid, "missing": missing_movie.guid,
                "restricted": restricted.guid}
        yield data

        row = db.query(models.SystemConfig).filter(
            models.SystemConfig.key == library_scope.CONFIG_USER_OVERRIDES
        ).first()
        if row is not None:
            if old_value is None:
                db.delete(row)
            else:
                row.value = old_value
            db.commit()
        store.invalidate()
    finally:
        db.close()


@pytest.fixture(scope="module")
def token(client, seed):
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": UNAME, "Pw": PW},
        headers={"X-Emby-Authorization":
                 'MediaBrowser Client="CFEdge", Device="iOS", '
                 f'DeviceId="cfedge-dev-{_SUFFIX}", Version="1.0"'})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


def _h(token):
    return {"X-Emby-Token": token}


# ---------- 缓存头分面 ----------

def test_anon_public_poster_is_public_cacheable(client, seed):
    r = client.get(f"/emby/Items/{seed['series']}/Images/Primary")
    assert r.status_code == 200, r.text
    cc = r.headers.get("cache-control", "")
    assert cc.startswith("public"), cc
    assert "s-maxage" in cc, cc


def test_auth_poster_stays_private(client, seed, token):
    r = client.get(f"/emby/Items/{seed['series']}/Images/Primary",
                   headers=_h(token))
    assert r.status_code == 200, r.text
    assert "private" in r.headers.get("cache-control", "")


# ---------- D-lite 尺寸封顶 ----------

def test_anon_primary_capped_at_600(client, seed):
    # 原图 1200 宽、无尺寸参数：无凭据只许出 ≤600 宽
    r = client.get(f"/emby/Items/{seed['series']}/Images/Primary")
    assert r.status_code == 200, r.text
    assert _width(r.content) <= 600


def test_anon_backdrop_capped_at_1280(client, seed):
    # 原图 2400 宽：无凭据背景封顶 1280
    r = client.get(f"/emby/Items/{seed['series']}/Images/Backdrop")
    assert r.status_code == 200, r.text
    assert _width(r.content) <= 1280


def test_anon_smaller_request_not_inflated(client, seed):
    # 客户端只要 320：封顶是上限不是下限，不许放大
    r = client.get(f"/emby/Items/{seed['series']}/Images/Primary?maxWidth=320")
    assert r.status_code == 200, r.text
    assert _width(r.content) <= 320


def test_auth_primary_not_capped(client, seed, token):
    # 带凭据不封顶：无尺寸参数出原图 1200 宽
    r = client.get(f"/emby/Items/{seed['series']}/Images/Primary",
                   headers=_h(token))
    assert r.status_code == 200, r.text
    assert _width(r.content) == 1200


class _FakeRemoteResponse:
    def __init__(self, content, content_type="image/jpeg"):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = 200

    def raise_for_status(self):
        return None


def _wide_jpeg(w=2000, h=1000) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def test_anon_proxy_remote_capped_and_public(client, seed, monkeypatch):
    # 未本地化的远程图走代理回源：无凭据同样封顶 + public
    from backend.emby_server import media_routes

    monkeypatch.setattr(
        media_routes, "_proxy_remote_image",
        lambda src: _FakeRemoteResponse(_wide_jpeg()))
    r = client.get(f"/emby/Items/{seed['remote']}/Images/Primary")
    assert r.status_code == 200, r.text
    assert _width(r.content) <= 600
    assert r.headers.get("cache-control", "").startswith("public")


# ---------- C：失败响应统一（关存在性预言机） ----------

def test_anon_failures_are_byte_identical(client, seed):
    unknown = client.get(f"/emby/Items/{_guid()}/Images/Primary")
    restricted = client.get(f"/emby/Items/{seed['restricted']}/Images/Primary")
    missing = client.get(f"/emby/Items/{seed['missing']}/Images/Primary")
    wrong_type = client.get(f"/emby/Items/{seed['series']}/Images/Thumb")
    for r in (unknown, restricted, missing, wrong_type):
        assert r.status_code == 404, r.text
    bodies = {r.content for r in (unknown, restricted, missing, wrong_type)}
    assert len(bodies) == 1, bodies


def test_auth_failures_keep_distinct_statuses(client, seed, token):
    # 带凭据面不并入统一口径：受限 403、未知 404 维持 PA-036 形态
    r403 = client.get(f"/emby/Items/{seed['restricted']}/Images/Primary",
                      headers=_h(token))
    assert r403.status_code == 403, r403.text
    r404 = client.get(f"/emby/Items/{_guid()}/Images/Primary",
                      headers=_h(token))
    assert r404.status_code == 404, r404.text


# ---------- 本地化总开关默认关 ----------

def test_localize_switch_defaults_off(monkeypatch):
    from backend.emby_server import image_queue, image_store

    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    assert image_store.enabled() is False
    assert image_queue.enqueue_item_images(123456) is False


def test_localize_off_never_downloads(monkeypatch, tmp_path):
    from backend.emby_server import image_store

    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    monkeypatch.setattr(image_store, "image_dir", lambda: str(tmp_path))

    def _boom(url, attempts=None):
        raise AssertionError("开关关时不许发下载")

    monkeypatch.setattr(image_store, "_download", _boom)
    assert image_store.localize("https://img.example/never.jpg") == ""
    assert list(tmp_path.iterdir()) == []


def test_localize_off_still_resolves_existing_file(monkeypatch, tmp_path):
    # 已落盘的图：开关关时写侧 localize 恒空（关态契约不回写、不落盘），
    # 只读命中归出图层专用的 landed_path（存量 2.1G 是现成源站缓存）
    from backend.emby_server import image_store

    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    monkeypatch.setattr(image_store, "image_dir", lambda: str(tmp_path))
    url = "https://img.example/already.jpg"
    path = image_store.local_path(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_wide_jpeg(100, 100))
    assert image_store.localize(url) == ""
    assert image_store.landed_path(url) == path
    assert image_store.landed_path("https://img.example/not-there.jpg") == ""


def test_anon_serves_landed_file_while_switch_off(client, seed, monkeypatch,
                                                  tmp_path):
    # 关态 + 已落盘：出图层只读命中存量文件，200 + public，不走代理回源
    from backend.emby_server import image_store, media_routes

    monkeypatch.delenv("EMBY_LOCALIZE_IMAGES", raising=False)
    monkeypatch.setattr(image_store, "image_dir", lambda: str(tmp_path))
    url = "https://img.example/cfedge-wide.jpg"   # seed['remote'] 的远程图
    path = image_store.local_path(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    landed = _wide_jpeg(100, 100)
    with open(path, "wb") as fh:
        fh.write(landed)

    def _no_proxy(src):  # 命中存量就不许回源
        raise AssertionError("已落盘时不许走代理回源")

    monkeypatch.setattr(media_routes, "_proxy_remote_image", _no_proxy)
    r = client.get(f"/emby/Items/{seed['remote']}/Images/Primary")
    assert r.status_code == 200, r.text
    assert r.content == landed
    assert r.headers.get("cache-control", "").startswith("public")


def test_localize_on_still_downloads(monkeypatch, tmp_path):
    # 显式开（回滚路径）：localize 照常下载落盘
    from backend.emby_server import image_store

    monkeypatch.setenv("EMBY_LOCALIZE_IMAGES", "1")
    monkeypatch.setattr(image_store, "image_dir", lambda: str(tmp_path))
    monkeypatch.setattr(image_store, "_download",
                        lambda url, attempts=None: _wide_jpeg(100, 100))
    out = image_store.localize("https://img.example/back-on.jpg")
    assert out and os.path.isfile(out)