"""回归：反代链下登录限流必须按真实客户端 IP 分桶，不能全站共用代理桶。

生产实证（2026-11-23）：Redis 键
``ratelimit:172.18.0.7:/emby/Users/AuthenticateByName:anon:<窗口>``——
172.18.0.7 是 aetrix-web（nginx）容器 IP。请求链是
Cloudflare → tls-bridge（宿主机 nginx）→ web 容器 nginx → api：
api 的直连对端永远是 web 容器，而生产未配 TRUSTED_PROXIES，
旧口径下转发头一概不采信，于是全站登录共用一个 15 次/分钟的桶，
个别客户端拿旧密码自动重试就能把所有人挤到 429。

真实客户端 IP 在这条链上的位置：X-Forwarded-For **首项**
（Cloudflare 写入，后续每跳代理往后追加；末项是最近一跳代理，
X-Real-IP 则被 tls-bridge 写成 Cloudflare 边缘 IP，都不是客户端）。

本文件用内存计数桩掉 backend.database.rate_limit_incr，跑真实的
_get_client_ip / _ea_get_ip + _check_rate_limit / _ea_check_limit，
不复制逻辑。
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-xff-bucket-key-32chars-minimum")

import pytest

from backend.ratelimit import get_client_ip

LOGIN_PATH = "/emby/Users/AuthenticateByName"
# 生产里 api 看到的直连对端：web 容器 nginx 的 IP。
PROXY_PEER = "172.18.0.7"
# 生产形态的 XFF 链：客户端（CF 写入）, CF 边缘, tls-bridge 回环。
XFF_A = "203.0.113.10, 172.70.189.32, 127.0.0.1"
XFF_B = "198.51.100.23, 172.70.189.32, 127.0.0.1"


@pytest.fixture(autouse=True)
def _no_trusted_proxies_env(monkeypatch):
    """复现生产：未配 TRUSTED_PROXIES（修复不得依赖部署侧补配才生效）。"""
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)


@pytest.fixture()
def fake_incr(monkeypatch):
    counts = {}

    def _incr(key, ttl=None):
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    monkeypatch.setattr("backend.database.rate_limit_incr", _incr)
    return counts


def _make_request(direct_ip, xff=None, x_real_ip=None):
    req = MagicMock()
    req.client.host = direct_ip
    headers = {}
    if xff:
        headers["x-forwarded-for"] = xff
    if x_real_ip:
        headers["x-real-ip"] = x_real_ip
    req.headers.get = lambda k, default="": headers.get(k.lower(), default)
    req.headers.__getitem__ = lambda self, k: headers[k.lower()]
    return req


class TestClientIpFromProxyChain:
    """同对端、不同 XFF 首项 → 不同客户端 IP（旧口径两者都回对端 IP）。"""

    def test_different_xff_first_hops_give_different_ips(self):
        ip_a = get_client_ip(_make_request(PROXY_PEER, xff=XFF_A))
        ip_b = get_client_ip(_make_request(PROXY_PEER, xff=XFF_B))
        assert ip_a == "203.0.113.10"
        assert ip_b == "198.51.100.23"
        assert ip_a != ip_b

    def test_no_xff_falls_back_to_direct_peer(self):
        assert get_client_ip(_make_request(PROXY_PEER)) == PROXY_PEER

    def test_invalid_xff_first_hop_falls_back_to_direct_peer(self):
        """首项非法（如代理写的 unknown）→ 回退直连对端，不拿它当键。"""
        req = _make_request(PROXY_PEER, xff="unknown, 203.0.113.10")
        assert get_client_ip(req) == PROXY_PEER

    def test_public_peer_still_ignores_forged_xff(self):
        """防伪造不变量不许丢：公网直连对端伪造 XFF 仍被忽略。"""
        req = _make_request("203.0.113.99", xff="1.2.3.4")
        assert get_client_ip(req) == "203.0.113.99"


class TestEmLoginBucketsSplitByClient:
    """EM（backend/main.py）登录桶：两个真实客户端互不连坐。"""

    def test_buckets_split_by_xff_first_hop(self, fake_incr):
        from backend.main import _check_rate_limit, _get_client_ip

        ip_a = _get_client_ip(_make_request(PROXY_PEER, xff=XFF_A))
        ip_b = _get_client_ip(_make_request(PROXY_PEER, xff=XFF_B))
        # A 打满自己的 15 次/分钟
        for _ in range(15):
            assert _check_rate_limit(ip_a, LOGIN_PATH, False)[0] is True
        assert _check_rate_limit(ip_a, LOGIN_PATH, False)[0] is False
        # 同一代理后面的 B 不受影响（旧口径下 B 与 A 共用代理桶，这里会是 False）
        assert _check_rate_limit(ip_b, LOGIN_PATH, False)[0] is True
        # 桶键里是真实客户端 IP，不是代理 IP
        login_keys = [k for k in fake_incr if "AuthenticateByName" in k]
        assert any("203.0.113.10" in k for k in login_keys)
        assert any("198.51.100.23" in k for k in login_keys)
        assert all(PROXY_PEER not in k for k in login_keys)


class TestEaLoginBucketsSplitByClient:
    """EA（emby_api/asgi_middleware.py）登录桶：与 EM 同口径。"""

    def test_buckets_split_by_xff_first_hop(self, fake_incr):
        from emby_api.asgi_middleware import _ea_check_limit, _ea_get_ip

        ip_a = _ea_get_ip(_make_request(PROXY_PEER, xff=XFF_A))
        ip_b = _ea_get_ip(_make_request(PROXY_PEER, xff=XFF_B))
        for _ in range(15):
            assert _ea_check_limit(ip_a, LOGIN_PATH, False)[0] is True
        assert _ea_check_limit(ip_a, LOGIN_PATH, False)[0] is False
        assert _ea_check_limit(ip_b, LOGIN_PATH, False)[0] is True
        login_keys = [k for k in fake_incr if "AuthenticateByName" in k]
        assert any("203.0.113.10" in k for k in login_keys)
        assert all(PROXY_PEER not in k for k in login_keys)
