"""限流 IP 提取测试。

验证 get_client_ip 的口径（2026-11-23 改版，见 ratelimit.get_client_ip）：
1. 公网直连来源伪造 XFF → 忽略伪造头，用直连 IP（限流不被绕过）
2. 自有代理层（内网/回环）对端 → 取 XFF 首项为真实客户端 IP
   （生产链 Cloudflare → tls-bridge → web 容器下，客户端只在首项；
   旧口径取末段/X-Real-IP 拿到的是代理与 CF 边缘 IP，全站共用一桶）
3. XFF 首项缺失/非法 → 回落 X-Real-IP，再回落直连 IP
4. Emby 登录的限流桶按「出口 IP + 账号」建，NAT 下不互相连坐
"""
import os
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ratelimit import get_client_ip, client_ip, _is_trusted_proxy, _limiter


def _make_request(direct_ip, xff=None, x_real_ip=None):
    """构造一个模拟的 FastAPI Request"""
    req = MagicMock()
    req.client.host = direct_ip
    headers = {}
    if xff:
        headers["x-forwarded-for"] = xff
    if x_real_ip:
        headers["x-real-ip"] = x_real_ip
    req.headers.get = lambda k, default="": headers.get(k.lower(), default)
    # 兼容直接 headers[k] 访问
    req.headers.__getitem__ = lambda self, k: headers[k.lower()]
    return req


class TestUntrustedSource:
    """不可信来源：伪造头必须被忽略"""

    def test_forged_xff_ignored(self):
        """攻击者直连并伪造 XFF → 必须用直连 IP，不能用伪造的"""
        req = _make_request("203.0.113.99", xff="1.2.3.4")
        assert get_client_ip(req) == "203.0.113.99"

    def test_forged_xff_multi_hops_ignored(self):
        """伪造多段 XFF 也一样被忽略"""
        req = _make_request("203.0.113.99", xff="1.2.3.4, 5.6.7.8, 9.10.11.12")
        assert get_client_ip(req) == "203.0.113.99"

    def test_forged_x_real_ip_ignored(self):
        """伪造 X-Real-IP 也被忽略"""
        req = _make_request("203.0.113.99", x_real_ip="1.2.3.4")
        assert get_client_ip(req) == "203.0.113.99"

    def test_no_headers_uses_direct(self):
        """无头时用直连 IP"""
        req = _make_request("203.0.113.99")
        assert get_client_ip(req) == "203.0.113.99"

    def test_client_ip_alias_same(self):
        """兼容函数 client_ip 行为一致"""
        req = _make_request("203.0.113.99", xff="1.2.3.4")
        assert client_ip(req) == "203.0.113.99"


class TestTrustedProxy:
    """自有代理层对端：头可信，按 XFF 首项解析"""

    def test_trusted_proxy_xff_first_hop(self):
        """nginx 在 127.0.0.1（自有代理层），XFF 取首项。

        2026-11-23 口径改版：旧实现取末段——那是单层 nginx 直面客户端
        时的假设；生产链是 Cloudflare → tls-bridge → web 容器多跳，
        末段是最近一跳代理、X-Real-IP 是 CF 边缘 IP，客户端只在首项
        （链首写入者记录的地址，后续每跳往后追加）。
        """
        req = _make_request(
            "127.0.0.1",
            xff="1.2.3.4, 5.6.7.8",  # 首项是客户端，后段是链上代理
        )
        assert get_client_ip(req) == "1.2.3.4"

    def test_xff_first_hop_beats_x_real_ip(self):
        """XFF 首项优先于 X-Real-IP：生产里 X-Real-IP 被 tls-bridge
        写成 Cloudflare 边缘 IP，优先它等于按 CF 边缘分桶（仍连坐）。"""
        req = _make_request(
            "127.0.0.1",
            xff="9.9.9.9",
            x_real_ip="5.6.7.8",
        )
        assert get_client_ip(req) == "9.9.9.9"

    def test_x_real_ip_fallback_without_xff(self):
        """无 XFF 时回落 X-Real-IP（单层自有 nginx 硬写 $remote_addr 的链）"""
        req = _make_request("127.0.0.1", x_real_ip="5.6.7.8")
        assert get_client_ip(req) == "5.6.7.8"

    def test_trusted_proxy_no_headers(self):
        """可信代理但无头 → 用直连 IP（代理自己）"""
        req = _make_request("127.0.0.1")
        assert get_client_ip(req) == "127.0.0.1"


class TestTrustedProxiesConfig:
    """TRUSTED_PROXIES 环境变量配置"""

    def test_custom_trusted_cidr(self, monkeypatch):
        """自定义 CIDR 网段可信"""
        monkeypatch.setenv("TRUSTED_PROXIES", "172.18.0.0/16")
        assert _is_trusted_proxy("172.18.0.10") is True
        assert _is_trusted_proxy("172.19.0.1") is False

    def test_custom_trusted_single_ip(self, monkeypatch):
        """单个 IP 可信"""
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        assert _is_trusted_proxy("10.0.0.5") is True
        assert _is_trusted_proxy("10.0.0.6") is False

    def test_loopback_always_trusted(self, monkeypatch):
        """回环地址默认可信（无需配置）"""
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        assert _is_trusted_proxy("127.0.0.1") is True
        assert _is_trusted_proxy("::1") is True

    def test_invalid_config_ignored(self, monkeypatch):
        """写错的配置不炸服务"""
        monkeypatch.setenv("TRUSTED_PROXIES", "not-an-ip, 999.999.999.999")
        # 回环仍然可信，非法条目被忽略
        assert _is_trusted_proxy("127.0.0.1") is True
        assert _is_trusted_proxy("1.2.3.4") is False

    def test_docker_network_trusted(self, monkeypatch):
        """Docker 网段可信后，容器间调用的 XFF 生效"""
        monkeypatch.setenv("TRUSTED_PROXIES", "172.18.0.0/16")
        req = _make_request("172.18.0.5", xff="203.0.113.7")
        assert get_client_ip(req) == "203.0.113.7"


class TestEdgeCases:
    """边界情况"""

    def test_none_request(self):
        assert get_client_ip(None) == "unknown"

    def test_no_client(self):
        req = MagicMock()
        req.client = None
        req.headers.get = lambda k, default="": default
        assert get_client_ip(req) == "unknown"

    def test_empty_xff(self):
        req = _make_request("127.0.0.1", xff="  ,  ")
        assert get_client_ip(req) == "127.0.0.1"


class TestEmbyLoginBucketScope:
    """Emby 登录限流的桶范围：按账号建档，不能按纯 IP 连坐。

    NAT（家庭 / 宿舍 / 公司共用一个公网出口）下，纯按 IP 建桶意味着一个人把密码
    输错几次（或客户端拿着过期凭据反复重试），同一 IP 下的**所有人**都开始收
    429；客户端再按 Retry-After 退避，用户看到的就是「登录卡很久」。
    """

    @staticmethod
    def _session():
        from backend import models
        from backend.emby_server import models as emby_models

        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        emby_models.Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    @staticmethod
    def _login(db, username):
        from backend.emby_server.api import authenticate_by_name

        req = _make_request("203.0.113.7")
        req.headers.get = lambda k, default="": ({}).get(k.lower(), default)
        return authenticate_by_name(req, {"Username": username, "Pw": "wrong-password"}, db)

    def test_same_ip_different_accounts_do_not_share_bucket(self):
        suffix = uuid.uuid4().hex[:8]
        alice, bob = f"alice-{suffix}", f"bob-{suffix}"
        db = self._session()
        try:
            # alice 连错 10 次（额度用光），第 11 次被限流
            for _ in range(10):
                assert self._login(db, alice).status_code == 401
            assert self._login(db, alice).status_code == 429
            # 同一个出口 IP 的 bob 不受影响——老实现（纯 IP 桶）这里会是 429
            assert self._login(db, bob).status_code == 401
        finally:
            db.close()
            for name in (alice, bob, "-"):
                _limiter.reset(f"emby-auth:203.0.113.7:{name}")
