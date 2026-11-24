"""Emby 网关 CORS（L-037）：www 入口跨域调 cdn /emby 网关的预检必须有人应答。

缺陷背景（2026-11-24 晚用户实报）：网页端从 https://www.aetrix.love 打开，
账号卡下发的 EA base_url 是 https://cdn.aetrix.love，搜索/列表/详情/网页
播放器全部跨域调 /emby/*。EM 与 EA 的 CORSMiddleware 都靠 CORS_ORIGINS
环境变量驱动、为空时不注册——生产没配这个 env，预检 OPTIONS 直接打到
路由层拿到 405（allow: GET）、无任何 Access-Control-* 头，浏览器把真
请求拦下，前端全线报「网络暂时连不上 / 搜索没完成 / 加载失败 /
Network Error」。原生客户端不发预检、不受影响。

钉死口径：
- 来源白名单全站一套（backend.cors_origins）：CORS_ORIGINS 配了以它
  为准；没配回落本站自有域（www/aetrix.love、aetrix.love、
  cdn.aetrix.love、emby.aetrix.love）；绝不出现 "*".
- EA 网关与 EM 单进程网关行为一致：本站来源的 OPTIONS 预检 200 且
  Allow-Origin/Methods/Headers 齐全；真实响应（列表/详情/PlaybackInfo/
  manifest/视频流）带 Allow-Origin；非本站来源不回任何 CORS 头；
  无 Origin 的普通请求行为不变（原生客户端零影响）。
"""
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi.testclient import TestClient

SITE = "https://www.aetrix.love"
SITE_CDN = "https://cdn.aetrix.love"
EVIL = "https://evil.example.org"


@pytest.fixture(scope="module", autouse=True)
def _cors_origins_unset():
    # 本文件钉的是「没配 CORS_ORIGINS 时的默认口径」：摘掉外部 env
    # 干扰后，再让两个 app 在下面的 fixture 里完成导入。放在运行期
    # fixture 而不是文件顶层，是因为导入期写 env 有专门守卫
    # （test_zz_env_import_hygiene：收集期污染同进程其他用例）。
    os.environ.pop("CORS_ORIGINS", None)
    yield


@pytest.fixture(scope="module")
def ea_client(_cors_origins_unset):
    from emby_api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def em_client(_cors_origins_unset):
    from backend.main import app

    return TestClient(app, raise_server_exceptions=False)


def _preflight(client, path, origin, method="GET", req_headers=None):
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": method,
    }
    if req_headers:
        headers["Access-Control-Request-Headers"] = req_headers
    return client.options(path, headers=headers)


def _assert_preflight_ok(r, origin):
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert r.headers.get("access-control-allow-origin") == origin
    methods = r.headers.get("access-control-allow-methods", "")
    assert "GET" in methods and "POST" in methods, methods


# ---------------- 解析器口径（全站一套） ----------------

def test_resolver_defaults_to_site_origins(monkeypatch):
    from backend.cors_origins import SITE_ORIGINS, resolve_cors_origins

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origins = resolve_cors_origins()
    assert origins == SITE_ORIGINS
    for expected in (
        "https://www.aetrix.love",
        "https://aetrix.love",
        "https://cdn.aetrix.love",
        "https://emby.aetrix.love",
    ):
        assert expected in origins
    assert "*" not in origins


def test_resolver_env_overrides_defaults(monkeypatch):
    from backend.cors_origins import resolve_cors_origins

    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    assert resolve_cors_origins() == ["https://a.example", "https://b.example"]


# ---------------- EA 网关：搜索/列表/详情面 ----------------

def test_ea_preflight_items_from_www(ea_client):
    r = _preflight(ea_client, "/emby/Users/me/Items", SITE,
                   req_headers="x-emby-token")
    _assert_preflight_ok(r, SITE)
    allow_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-emby-token" in allow_headers, allow_headers


def test_ea_preflight_items_from_cdn(ea_client):
    r = _preflight(ea_client, "/emby/Users/me/Items", SITE_CDN)
    _assert_preflight_ok(r, SITE_CDN)


def test_ea_real_get_carries_allow_origin(ea_client):
    r = ea_client.get("/emby/System/Ping", headers={"Origin": SITE})
    assert r.status_code == 200, r.text[:200]
    assert r.headers.get("access-control-allow-origin") == SITE


def test_ea_no_origin_request_unchanged(ea_client):
    r = ea_client.get("/emby/System/Ping")
    assert r.status_code == 200, r.text[:200]
    assert "access-control-allow-origin" not in r.headers


def test_ea_foreign_origin_gets_no_cors_headers(ea_client):
    r = _preflight(ea_client, "/emby/Users/me/Items", EVIL)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "access-control-allow-origin" not in r.headers
    r = ea_client.get("/emby/System/Ping", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in r.headers


# ---------------- EA 网关：播放链路面 ----------------

def test_ea_preflight_playback_info(ea_client):
    r = _preflight(ea_client, "/emby/Items/abc123/PlaybackInfo", SITE,
                   method="POST", req_headers="content-type,x-emby-token")
    _assert_preflight_ok(r, SITE)
    allow_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "content-type" in allow_headers, allow_headers
    assert "x-emby-token" in allow_headers, allow_headers


def test_ea_preflight_manifest_and_stream(ea_client):
    r = _preflight(ea_client, "/emby/Videos/abc123/master.m3u8", SITE)
    _assert_preflight_ok(r, SITE)
    r = _preflight(ea_client, "/emby/Videos/abc123/stream.mkv", SITE,
                   req_headers="range")
    _assert_preflight_ok(r, SITE)


def test_ea_real_stream_responses_carry_allow_origin(ea_client):
    # 未鉴权/不存在的 id 会拿到 401/404——CORS 头必须在真实响应上，
    # 否则 MSE 的 fetch 连错误状态都读不到、只报 Network Error。
    for path in (
        "/emby/Videos/abc123/master.m3u8",
        "/emby/Videos/abc123/stream.mkv",
        "/emby/Items/abc123/PlaybackInfo",
    ):
        r = ea_client.get(path, headers={"Origin": SITE})
        assert r.headers.get("access-control-allow-origin") == SITE, (
            path, r.status_code)


# ---------------- EM 单进程网关：同口径 ----------------

def test_em_preflight_items_from_www(em_client):
    r = _preflight(em_client, "/emby/Users/me/Items", SITE,
                   req_headers="x-emby-token")
    _assert_preflight_ok(r, SITE)


def test_em_real_get_carries_allow_origin(em_client):
    r = em_client.get("/emby/System/Ping", headers={"Origin": SITE})
    assert r.status_code == 200, r.text[:200]
    assert r.headers.get("access-control-allow-origin") == SITE


def test_em_foreign_origin_gets_no_cors_headers(em_client):
    r = _preflight(em_client, "/emby/Users/me/Items", EVIL)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "access-control-allow-origin" not in r.headers


# ---------------- 例外：测速口自管 CORS，不被白名单改写 ----------------

def test_em_speedtest_preflight_stays_endpoint_owned(em_client):
    # /speedtest/bin 是免鉴权公网测速口，端点自带 ACAO:* 与自有预检
    # （线路页可能跑在授权部署的自有域名下）；全局白名单中间件不许
    # 吞它的预检（backend.cors_origins.CORS_BYPASS_PATHS）。
    r = _preflight(em_client, "/speedtest/bin", SITE)
    assert r.status_code in (200, 204), r.status_code
    assert r.headers.get("access-control-allow-origin") == "*"
