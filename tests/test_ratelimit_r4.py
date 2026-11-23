"""R4 轮回归测试：限流 IP 提取的两项修复。

R4-1：被采信的 X-Forwarded-For 取值必须做合法性校验（如 "unknown"
     不能被当作限流键/日志 IP），非法时按回落链走。
     2026-11-23 口径改版：取值位置由末段改为首项（生产多跳代理链
     下客户端只在首项，见 ratelimit.get_client_ip），校验与回落
     语义不变，只是作用在首项上。
R4-2：_trusted_proxies() 解析缓存——同配置下不重复解析，
     环境变量一变立即失效（monkeypatch 逐测改值必须生效）。
"""
from unittest.mock import MagicMock

import pytest

import backend.ratelimit as ratelimit
from backend.ratelimit import get_client_ip, _is_trusted_proxy, _trusted_proxies


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


@pytest.fixture(autouse=True)
def _clear_cache():
    """每测前清空模块级解析缓存，避免用例间串扰。"""
    ratelimit._trusted_proxies_cache = None
    yield
    ratelimit._trusted_proxies_cache = None


class TestXffFirstHopValidation:
    """R4-1：XFF 首项合法性校验（2026-11-23 起取值位置为首项）"""

    def test_unknown_first_hop_falls_back_to_direct(self):
        """某些代理会把首项写成 unknown → 不许当键，回落到直连 IP"""
        req = _make_request("127.0.0.1", xff="unknown, 203.0.113.5")
        assert get_client_ip(req) == "127.0.0.1"

    def test_garbage_first_hop_falls_back_to_direct(self):
        """首项是垃圾字符串 → 回落到直连 IP"""
        req = _make_request("127.0.0.1", xff="not-an-ip!!, 1.2.3.4")
        assert get_client_ip(req) == "127.0.0.1"

    def test_valid_first_hop_used(self):
        """合法首项即客户端，后段是链上代理、与取值无关"""
        req = _make_request("127.0.0.1", xff="1.2.3.4, 5.6.7.8")
        assert get_client_ip(req) == "1.2.3.4"

    def test_valid_first_hop_ipv6(self):
        """合法 IPv6 首项也被采信"""
        req = _make_request("127.0.0.1", xff="2001:db8::1, 1.2.3.4")
        assert get_client_ip(req) == "2001:db8::1"

    def test_x_real_ip_fallback_when_first_hop_invalid(self):
        """首项非法但 X-Real-IP 合法 → 回落到 X-Real-IP（回落链不变）"""
        req = _make_request(
            "127.0.0.1", xff="unknown, 1.2.3.4", x_real_ip="5.6.7.8"
        )
        assert get_client_ip(req) == "5.6.7.8"

    def test_invalid_x_real_ip_and_invalid_xff(self):
        """X-Real-IP 非法 + XFF 首项非法 → 回落直连"""
        req = _make_request(
            "127.0.0.1", xff="garbage", x_real_ip="also-garbage"
        )
        assert get_client_ip(req) == "127.0.0.1"


class TestTrustedProxiesCache:
    """R4-2：解析缓存只在环境变量变化时失效"""

    def test_cache_hit_on_same_env(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        first = _trusted_proxies()
        assert ratelimit._trusted_proxies_cache is not None
        second = _trusted_proxies()
        assert first == second
        # 同一个 raw_env 下缓存对象被复用（返回的是拷贝，内容相等）
        assert ratelimit._trusted_proxies_cache[0] == "10.0.0.5"

    def test_env_change_invalidates_cache(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        assert _is_trusted_proxy("10.0.0.5") is True
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.6")
        # 缓存必须失效：旧值不再可信，新值可信
        assert _is_trusted_proxy("10.0.0.5") is False
        assert _is_trusted_proxy("10.0.0.6") is True

    def test_delenv_falls_back_to_loopback(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        assert _is_trusted_proxy("10.0.0.5") is True
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        assert _is_trusted_proxy("10.0.0.5") is False
        assert _is_trusted_proxy("127.0.0.1") is True

    def test_returned_list_is_copy(self, monkeypatch):
        """返回的 list 是拷贝：调用方改动不污染缓存"""
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        lst = _trusted_proxies()
        lst.clear()
        assert _is_trusted_proxy("10.0.0.5") is True

    def test_invalid_entries_still_ignored_with_cache(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "not-an-ip, 10.0.0.5")
        assert _is_trusted_proxy("10.0.0.5") is True
        assert _is_trusted_proxy("not-an-ip") is False
