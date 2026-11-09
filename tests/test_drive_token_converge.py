"""回归测试：Drive SA 令牌缓存收敛到 drive_auth 一套（Drive 砍减第 6 项）。

钉死：
- 全站 SA 令牌只存在 drive_auth._token_cache 这一套缓存里；
  drive_changes 不再维护自己的 _token_cache（旧的内层缓存曾与外层
  各存一份、TTL 口径还不一样，401 失效要清两处、refresh_sa_token
  只清内层，drive_auth 会继续吐坏 token 最长 55 分钟）。
- 经 _sa_access_token / get_token_for_drive / refresh_sa_token 任一
  口径换到的 token，都进同一套缓存，任一口径都能命中、任一口径
  失效都对所有口径生效；同一 SA 不被重复换发。
- 个人盘 OAuth 缓存（_oauth_token_cache）是另一类凭据（用户
  OAuth refresh_token 流、按 remote 分键），不在本次收敛范围，
  保持原样。
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")

import pytest

try:
    from backend.emby_server import drive_auth
    from backend.emby_server import drive_changes
except ImportError:  # 兼容以 backend 为根的扁平导入路径
    from emby_server import drive_auth
    from emby_server import drive_changes


def _make_sa(email: str) -> dict:
    """生成带真实 RSA 私钥的假 SA 字典（JWT 签名走真实现）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return {"client_email": email, "private_key": pem}


class _FakeTokenResp:
    """oauth2 token 端点的假响应：raise_for_status + json。"""

    def __init__(self, token: str, expires_in: int = 3600):
        self._token = token
        self._expires_in = expires_in

    def raise_for_status(self):
        return None

    def json(self):
        return {"access_token": self._token,
                "expires_in": self._expires_in}


@pytest.fixture(autouse=True)
def reset_drive_auth_state():
    drive_auth._reset_state()
    yield
    drive_auth._reset_state()


@pytest.fixture
def fake_token_endpoint(monkeypatch):
    """把 drive_changes 的 httpx 换成假 token 端点，返回可配的 token 序列。"""
    calls = {"n": 0, "tokens": ["TOK-1", "TOK-2", "TOK-3", "TOK-4"]}

    def _post(url, data=None, timeout=None):
        calls["n"] += 1
        idx = min(calls["n"] - 1, len(calls["tokens"]) - 1)
        return _FakeTokenResp(calls["tokens"][idx])

    fake_httpx = SimpleNamespace(post=_post)
    monkeypatch.setattr(drive_changes, "httpx", fake_httpx)
    return calls


def test_no_second_token_cache_on_drive_changes():
    """结构守卫：drive_changes 不再有自己的 _token_cache。"""
    assert not hasattr(drive_changes, "_token_cache")


def test_sa_access_token_writes_single_store(fake_token_endpoint):
    """经 _sa_access_token 换到的 token 写进 drive_auth 单套缓存，
    第二次调用不再打 token 端点。"""
    sa = _make_sa("sa1@x")
    assert drive_changes._sa_access_token(sa) == "TOK-1"
    assert fake_token_endpoint["n"] == 1
    # 同一套缓存里能看到它（旧实现只写内层缓存，外层看不到）。
    assert drive_auth._token_cache["sa1@x"][0] == "TOK-1"
    assert drive_changes._sa_access_token(sa) == "TOK-1"
    assert fake_token_endpoint["n"] == 1


def test_drive_auth_reuses_token_minted_by_sa_access_token(
        fake_token_endpoint):
    """_sa_access_token 换到的 token，drive_auth 不经换取直接命中。"""
    sa = _make_sa("sa1@x")
    assert drive_changes._sa_access_token(sa) == "TOK-1"

    # 把 _sa_access_token 换成必炸的桩：命中必须全走单套缓存。
    def _boom(sa_, min_remaining=60):
        raise AssertionError("_sa_access_token 不应再被调用")

    real = drive_changes._sa_access_token
    drive_changes._sa_access_token = _boom
    try:
        assert drive_auth.get_drive_bearer_token(_sa=sa) == "TOK-1"
    finally:
        drive_changes._sa_access_token = real
    assert fake_token_endpoint["n"] == 1


def test_get_token_for_drive_shares_single_store(
        fake_token_endpoint, monkeypatch):
    """get_token_for_drive（转存/挂载口径）换到的 token 同样进单套
    缓存，drive_auth 随后直接命中、不重复换发。"""
    sa = _make_sa("sa1@x")
    monkeypatch.setattr(drive_changes, "_next_sa", lambda: sa)
    assert drive_changes.get_token_for_drive("shared1") == "TOK-1"
    assert fake_token_endpoint["n"] == 1
    assert drive_auth._token_cache["sa1@x"][0] == "TOK-1"

    def _boom(sa_, min_remaining=60):
        raise AssertionError("_sa_access_token 不应再被调用")

    real = drive_changes._sa_access_token
    drive_changes._sa_access_token = _boom
    try:
        assert drive_auth.get_drive_bearer_token(_sa=sa) == "TOK-1"
    finally:
        drive_changes._sa_access_token = real
    assert fake_token_endpoint["n"] == 1


def test_refresh_sa_token_clears_single_store(fake_token_endpoint):
    """refresh_sa_token 弹掉的缓存，drive_auth 侧也立即失效——
    旧实现只清内层，drive_auth 会继续吐旧 token（挂载 401 续跑
    后仍拿坏 token 的真 bug）。"""
    sa = _make_sa("sa1@x")
    assert drive_changes._sa_access_token(sa) == "TOK-1"
    assert drive_auth.get_drive_bearer_token(_sa=sa) == "TOK-1"

    fresh = drive_changes.refresh_sa_token(sa)
    assert fresh == "TOK-2"
    assert fake_token_endpoint["n"] == 2
    # drive_auth 不再吐旧 token。
    assert drive_auth.get_drive_bearer_token(_sa=sa) == "TOK-2"
    assert fake_token_endpoint["n"] == 2


def test_sa_access_token_honors_min_remaining(fake_token_endpoint):
    """单套缓存的 min_remaining 语义：剩余寿命不够就重新换取，
    够就直接复用（接替旧的内外两层 TTL 联动测试）。"""
    sa = _make_sa("sa1@x")
    # 先种一个只剩 300 秒的缓存条目。
    drive_auth._token_cache["sa1@x"] = (
        "TOK-STALE", time.monotonic() + 300)
    # 索取剩余寿命 ≥3420 秒：不够，重新换取。
    assert drive_changes._sa_access_token(
        sa, min_remaining=3420) == "TOK-1"
    assert fake_token_endpoint["n"] == 1
    # 默认 min_remaining=60：新换的 token（3600 秒）直接复用。
    assert drive_changes._sa_access_token(sa) == "TOK-1"
    assert fake_token_endpoint["n"] == 1


def test_dc_sa_email_for_token_reads_single_store():
    """drive_changes.sa_email_for_token 反查的是单套缓存
    （中继 failover 排除已试 SA 用）。"""
    drive_auth._token_cache["sa1@x"] = ("TOK1", time.monotonic() + 3600)
    assert drive_changes.sa_email_for_token("TOK1") == "sa1@x"
    assert drive_changes.sa_email_for_token("NOPE") is None


def test_invalidate_clears_single_store_and_reexchanges(
        fake_token_endpoint):
    """按值失效清掉单套缓存后，下次取 token 重新换取。"""
    sa = _make_sa("sa1@x")
    assert drive_changes._sa_access_token(sa) == "TOK-1"
    assert drive_auth.invalidate_drive_bearer_token("TOK-1") is True
    assert "sa1@x" not in drive_auth._token_cache
    assert drive_auth.get_drive_bearer_token(_sa=sa) == "TOK-2"
    assert fake_token_endpoint["n"] == 2
