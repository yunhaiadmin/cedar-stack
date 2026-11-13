"""EA 测速字节端点 /speedtest/bin：档位白名单 + 字节数精确。

用户端测速页从浏览器真下载本端点计量，字节数必须与档位严格
一致（32/64/100 MiB），档位外一律 400——端点是公网带宽出口，
不许任意大小。行为测试只读响应头与前几个分块，不把 32MiB
全量读进内存。
"""
import os
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi.testclient import TestClient

MB = 1024 * 1024


@pytest.fixture(scope="module")
def client():
    import emby_api.main as ea

    return TestClient(ea.app, raise_server_exceptions=False)


def test_speedtest_bin_32mb_headers_and_first_bytes(client):
    with client.stream("GET", "/speedtest/bin", params={"mb": 32}) as r:
        assert r.status_code == 200, r.status_code
        assert r.headers["content-length"] == str(32 * MB)
        assert r.headers["x-speedtest-bytes"] == str(32 * MB)
        assert r.headers["content-type"].startswith("application/octet-stream")
        got = 0
        for chunk in r.iter_bytes():
            got += len(chunk)
            if got >= 2 * MB:
                break
        assert got >= 2 * MB


def test_speedtest_bin_default_is_32mb(client):
    with client.stream("GET", "/speedtest/bin") as r:
        assert r.status_code == 200
        assert r.headers["content-length"] == str(32 * MB)


def test_speedtest_bin_rejects_unlisted_size(client):
    r = client.get("/speedtest/bin", params={"mb": 7})
    assert r.status_code == 400
    r = client.get("/speedtest/bin", params={"mb": 1024})
    assert r.status_code == 400
