"""EM 测速字节端点 /speedtest/bin：存在性 + mb 钳制 + 字节精确 + CORS。

缺陷背景（2026-11-24 用户实报）：用户端「线路测速」页对每条线路
fetch `${line.url}/speedtest/bin?mb=<32|64|100>` 逐字节计量，但本端点
此前只存在于 EA（emby_api），EM 后端从未实现——www/cdn 线路入口落到
aetrix-web 的 nginx 时无人应答、被 SPA try_files 回退成 index.html，
前端拿到 HTML 计不出速度，必然报「连接失败」。

钉死口径：
- mb 钳制 1–100 MiB；非法/缺失按 32；不许 500/422；
- 响应体恰好 mb MiB、不可压缩（随机块循环），流式输出；
- Content-Type octet-stream、Content-Length 精确、Cache-Control
  no-store、Access-Control-Allow-Origin: *（线路与站点不同源、前端
  裸 fetch 不带令牌，端点免鉴权，跨域靠这一条）；
- OPTIONS 预检有应答且口径一致。
"""
import os
import sys
import zlib

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi.testclient import TestClient

MB = 1024 * 1024


@pytest.fixture(scope="module")
def client():
    from backend.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_speedtest_bin_default_32mb_headers(client):
    with client.stream("GET", "/speedtest/bin") as r:
        assert r.status_code == 200, r.status_code
        assert r.headers["content-length"] == str(32 * MB)
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert "no-store" in r.headers["cache-control"]
        assert r.headers["access-control-allow-origin"] == "*"


def test_speedtest_bin_exact_bytes_and_incompressible(client):
    with client.stream("GET", "/speedtest/bin", params={"mb": 2}) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert len(body) == 2 * MB
    # 随机块循环复用：第二 MiB 与第一 MiB 逐字节相同
    assert body[:MB] == body[MB:]
    # 不可压缩：随机块 zlib 压不动（全零/可压填充会在测速时被中间层
    # 压缩失真，钉死这一条）
    sample = body[:256 * 1024]
    assert len(zlib.compress(sample, 9)) > len(sample) * 0.99
    assert body[:4096] != b"\x00" * 4096


def test_speedtest_bin_clamps_low_to_1mb(client):
    for raw in ("0", "-5"):
        with client.stream("GET", "/speedtest/bin", params={"mb": raw}) as r:
            assert r.status_code == 200, raw
            assert r.headers["content-length"] == str(1 * MB), raw


def test_speedtest_bin_clamps_high_to_100mb(client):
    with client.stream("GET", "/speedtest/bin", params={"mb": 101}) as r:
        assert r.status_code == 200
        assert r.headers["content-length"] == str(100 * MB)
        # 只读头与首块，不把 100MiB 全量读进内存
        got = 0
        for chunk in r.iter_bytes():
            got += len(chunk)
            if got >= MB:
                break
        assert got >= MB


def test_speedtest_bin_invalid_mb_falls_back_to_32(client):
    for raw in ("abc", "", "3.5"):
        with client.stream("GET", "/speedtest/bin", params={"mb": raw}) as r:
            assert r.status_code == 200, raw
            assert r.headers["content-length"] == str(32 * MB), raw


def test_speedtest_bin_options_preflight(client):
    r = client.options(
        "/speedtest/bin",
        headers={
            "Origin": "https://www.aetrix.love",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204), r.status_code
    assert r.headers["access-control-allow-origin"] == "*"
