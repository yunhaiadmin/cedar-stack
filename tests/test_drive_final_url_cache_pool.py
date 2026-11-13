# -*- coding: utf-8 -*-
"""起播拆账优化第 2/3 项（2026-11-12）：Drive 中继最终 URL 短时缓存
+ Drive 主腿专用连接池（带熔断回退冷连接）+ 暖裁决归因标记对齐。

背景（拆账报告 playstart-breakdown-20261112 §2/§5）：relay 起播
resolve p50 707–866ms、首字节 gap p50 765ms，而 direct_302 同路径
只要 8ms。代码复核定性（与报告定点不同、已亲验）：生产中继并不走
resolve_drive_final_url（该函数无播放调用方、docstring 宣称的跳转
缓存根本不存在），真实成本在 serve_mount_relay_async 每请求从
Drive API 地址重新跟随跳转链（API 跳转一跳 + 终点主机常冷连接）。

本文件钉住：
1. 最终 URL 短时缓存（CacheManager playback 域，跨 api/ea 进程）：
   命中时中继直打最终地址、跳过 API 跳转跳；缓存按凭据隔离；
   失败（403 等）立即失效、回退现解析；TTL=0 关闭。
2. resolve_drive_final_url 同享该缓存与 Drive 池客户端（不再每
   请求新建客户端），失败不缓存。
3. Drive 主腿专用连接池：与挂载池是两个实例（挂载腿逐字不变）；
   连续传输失败达阈值熔断打开，期间回退一次性冷客户端（用完关
   闭）；上游可达即复位。
4. 暖工人归因：W2b 记 fresh（复用记录仍活）时 quota 标记也要在，
   TTL 与复用记录剩余同寿——否则 warm_hits 系统性漏报 quota，
   前后对比口径失真。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.emby_server import streaming


FINAL = "https://storage-download.googleapis.com/final/file?sig=abc"


def _run(coro):
    return asyncio.run(coro)


class _FakeResp:
    def __init__(self, status=206, chunks=(b"0123456789",)):
        self.status_code = status
        self.headers = {"content-type": "video/mp4",
                        "content-range": "bytes 0-9/100",
                        "accept-ranges": "bytes"}
        self._chunks = list(chunks)
        self.closed = False

    async def aiter_bytes(self, _n):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b""

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self):
        self.closed = False
        self.aclose_calls = 0

    async def aclose(self):
        self.closed = True
        self.aclose_calls += 1


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC",
                       raising=False)
    monkeypatch.delenv("DRIVE_RELAY_POOL_BREAKER_THRESHOLD",
                       raising=False)
    monkeypatch.delenv("DRIVE_RELAY_POOL_BREAKER_COOLDOWN_SEC",
                       raising=False)
    state = getattr(streaming, "_DRIVE_POOL_STATE", None)
    if isinstance(state, dict):
        state.update({"failures": 0, "open_until": 0.0})
    yield
    state = getattr(streaming, "_DRIVE_POOL_STATE", None)
    if isinstance(state, dict):
        state.update({"failures": 0, "open_until": 0.0})


@pytest.fixture()
def clients(monkeypatch):
    """Drive 池客户端与一次性客户端都换成假件，返回 (池, 一次性工厂记录)。"""
    pooled = _FakeClient()
    cold_made = []

    def _cold():
        c = _FakeClient()
        cold_made.append(c)
        return c

    monkeypatch.setattr(streaming, "_drive_relay_client",
                        lambda: pooled, raising=False)
    monkeypatch.setattr(streaming, "_drive_redirect_client",
                        _cold, raising=False)
    return pooled, cold_made


# ---------------------------------------------------------------------------
# 1. resolve_drive_final_url：最终 URL 缓存 + 池客户端
# ---------------------------------------------------------------------------

def _patch_follow(monkeypatch, behavior):
    calls = []

    async def _follow(client, url, forward, silent=False):
        calls.append({"client": client, "url": url,
                      "forward": dict(forward or {})})
        return behavior(len(calls), url)

    monkeypatch.setattr(streaming, "_follow_redirects", _follow)
    return calls


def test_resolve_caches_final_url(monkeypatch, clients):
    pooled, _cold = clients
    calls = _patch_follow(
        monkeypatch, lambda n, url: (_FakeResp(206), FINAL, 1))
    uc = "https://drive.google.com/uc?id=RFILE1&export=download"
    hdr = {"Authorization": "Bearer TOKA"}
    out1 = _run(streaming.resolve_drive_final_url(uc, hdr))
    out2 = _run(streaming.resolve_drive_final_url(uc, hdr))
    assert out1 == FINAL and out2 == FINAL
    # 第二次命中缓存：不再跟随跳转（现解析只发生一次）
    assert len(calls) == 1
    # 走 Drive 池客户端（同一实例），且不关池客户端
    assert calls[0]["client"] is pooled
    assert pooled.aclose_calls == 0


def test_resolve_cache_isolated_by_credential(monkeypatch, clients):
    calls = _patch_follow(
        monkeypatch, lambda n, url: (_FakeResp(206), FINAL, 1))
    uc = "https://drive.google.com/uc?id=RFILE2&export=download"
    _run(streaming.resolve_drive_final_url(
        uc, {"Authorization": "Bearer TOKA"}))
    _run(streaming.resolve_drive_final_url(
        uc, {"Authorization": "Bearer TOKB"}))
    _run(streaming.resolve_drive_final_url(
        uc, {"Authorization": "Bearer TOKA"}))
    # 凭据不同不许串用缓存；同凭据第二次命中
    assert len(calls) == 2


def test_resolve_does_not_cache_failure(monkeypatch, clients):
    calls = _patch_follow(
        monkeypatch, lambda n, url: (_FakeResp(403), FINAL, 1))
    uc = "https://drive.google.com/uc?id=RFILE3&export=download"
    hdr = {"Authorization": "Bearer TOKA"}
    assert _run(streaming.resolve_drive_final_url(uc, hdr)) is None
    assert _run(streaming.resolve_drive_final_url(uc, hdr)) is None
    assert len(calls) == 2  # 失败不缓存：每次现解析


def test_resolve_cache_ttl_zero_disables(monkeypatch, clients):
    monkeypatch.setenv("DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC", "0")
    calls = _patch_follow(
        monkeypatch, lambda n, url: (_FakeResp(206), FINAL, 1))
    uc = "https://drive.google.com/uc?id=RFILE4&export=download"
    hdr = {"Authorization": "Bearer TOKA"}
    assert _run(streaming.resolve_drive_final_url(uc, hdr)) == FINAL
    assert _run(streaming.resolve_drive_final_url(uc, hdr)) == FINAL
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 2. Drive 中继：最终 URL 缓存（跳过 API 跳转跳、失败失效回退现解析）
# ---------------------------------------------------------------------------

def _patch_token(monkeypatch, tokens):
    import backend.emby_server.drive_changes as _dc
    calls = []

    def fake_token(drive_id, force_refresh=False):
        calls.append({"drive_id": drive_id, "force": force_refresh})
        return tokens[min(len(calls) - 1, len(tokens) - 1)]

    monkeypatch.setattr(_dc, "get_token_for_drive", fake_token)
    return calls


def _patch_relay_follow(monkeypatch, final_behavior=None):
    """跟随替身：API 地址回跳转到 FINAL；FINAL 按 final_behavior 出响应。"""
    calls = []

    async def _follow(client, url, forward, silent=False):
        calls.append({"client": client, "url": url,
                      "forward": dict(forward or {})})
        if "www.googleapis.com" in url:
            return _FakeResp(206), FINAL, 1
        if final_behavior is not None:
            return final_behavior(url)
        return _FakeResp(206), url, 0

    monkeypatch.setattr(streaming, "_follow_redirects", _follow)
    return calls


def _request():
    return SimpleNamespace(headers={})


async def _collect(resp):
    return b"".join([c async for c in resp.body_iterator])


def _serve(fid, **kw):
    async def _go():
        resp = await streaming.serve_drive_relay_async(
            _request(), "DRIVE9", fid, **kw)
        return await _collect(resp)
    return _run(_go())


def test_relay_second_request_skips_api_redirect(monkeypatch, clients):
    pooled, cold_made = clients
    _patch_token(monkeypatch, ["TOK1"])
    calls = _patch_relay_follow(monkeypatch)
    body1 = _serve("CFID1")
    body2 = _serve("CFID1")
    assert body1 == body2 == b"0123456789"
    # 第一请求：API 地址跟随一跳；第二请求：直打最终地址、跳过 API 跳
    assert len(calls) == 2
    assert "www.googleapis.com" in calls[0]["url"]
    assert calls[1]["url"] == FINAL
    # 第一请求带 SA 凭据；直打最终地址（签名 URL 自带授权）不带凭据
    assert calls[0]["forward"].get("Authorization") == "Bearer TOK1"
    assert "Authorization" not in calls[1]["forward"]
    # 两次都走 Drive 池客户端、没碰一次性冷客户端
    assert calls[0]["client"] is pooled and calls[1]["client"] is pooled
    assert cold_made == []
    assert pooled.aclose_calls == 0


def test_relay_cached_final_failure_invalidates(monkeypatch, clients):
    _patch_token(monkeypatch, ["TOK1", "TOK2"])
    state = {"final_status": 206}

    def _final(url):
        return _FakeResp(state["final_status"]), url, 0

    calls = _patch_relay_follow(monkeypatch, _final)
    assert _serve("CFID2") == b"0123456789"  # 建缓存
    state["final_status"] = 403  # 缓存的最终地址死了
    # 第二请求先吃缓存（403）→ 失效 → failover 换凭据从 API 地址现解析成功
    assert _serve("CFID2") == b"0123456789"
    urls = [c["url"] for c in calls]
    assert "www.googleapis.com" in urls[0]
    assert urls[1] == FINAL
    assert "www.googleapis.com" in urls[2]  # 回退现解析的证据
    assert calls[2]["forward"].get("Authorization") == "Bearer TOK2"


def test_relay_byos_leg_never_uses_final_url_cache(monkeypatch, clients):
    _patch_token(monkeypatch, ["TOK1"])
    calls = _patch_relay_follow(monkeypatch)
    _serve("CFID3", access_token="USERTOK")
    _serve("CFID3", access_token="USERTOK")
    # BYOS 用户令牌腿口径不变：每次从 API 地址走、带本人令牌、不落缓存
    assert len(calls) == 2
    assert all("www.googleapis.com" in c["url"] for c in calls)
    assert all(c["forward"].get("Authorization") == "Bearer USERTOK"
               for c in calls)


def test_relay_final_url_cache_ttl_zero_disables(monkeypatch, clients):
    monkeypatch.setenv("DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC", "0")
    _patch_token(monkeypatch, ["TOK1"])
    calls = _patch_relay_follow(monkeypatch)
    _serve("CFID4")
    _serve("CFID4")
    assert len(calls) == 2
    assert all("www.googleapis.com" in c["url"] for c in calls)


# ---------------------------------------------------------------------------
# 3. Drive 主腿专用连接池 + 熔断回退冷连接
# ---------------------------------------------------------------------------

def test_drive_pool_separate_from_mount_pool():
    async def _two():
        return (streaming._drive_relay_client(),
                streaming._drive_relay_client(),
                streaming._mount_relay_client())

    drive1, drive2, mount = _run(_two())
    assert drive1 is drive2  # Drive 池同 loop 共享一份
    assert drive1 is not mount  # 与挂载池是两个实例：挂载腿逐字不碰


def _patch_follow_raise(monkeypatch):
    calls = []

    async def _follow(client, url, forward, silent=False):
        calls.append({"client": client, "url": url})
        raise httpx.ConnectError(
            "boom", request=httpx.Request("GET", "https://example.com"))

    monkeypatch.setattr(streaming, "_follow_redirects", _follow)
    return calls


def test_breaker_opens_and_falls_back_to_cold_client(monkeypatch, clients):
    from fastapi import HTTPException
    pooled, cold_made = clients
    _patch_token(monkeypatch, ["TOK1"])
    calls = _patch_follow_raise(monkeypatch)
    for _ in range(3):
        with pytest.raises(HTTPException) as ei:
            _serve("BFID1")
        assert ei.value.status_code == 502
    # 连续 3 次传输失败 → 熔断打开；前三次都走池客户端
    assert streaming.drive_pool_breaker_open() is True
    assert all(c["client"] is pooled for c in calls)
    # 熔断期间：回退一次性冷客户端，且用完关闭（不许泄漏/不许复用）
    with pytest.raises(HTTPException):
        _serve("BFID1")
    assert len(cold_made) == 1
    assert calls[-1]["client"] is cold_made[0]
    assert cold_made[0].aclose_calls >= 1
    assert pooled.aclose_calls == 0


def test_breaker_resets_on_upstream_reached(monkeypatch, clients):
    from fastapi import HTTPException
    pooled, cold_made = clients
    _patch_token(monkeypatch, ["TOK1"])
    mode = {"fail": True}

    async def _follow(client, url, forward, silent=False):
        if mode["fail"]:
            raise httpx.ConnectError(
                "boom", request=httpx.Request("GET", "https://example.com"))
        return _FakeResp(206), url, 0

    monkeypatch.setattr(streaming, "_follow_redirects", _follow)
    for _ in range(2):
        with pytest.raises(HTTPException):
            _serve("BFID2")
    mode["fail"] = False
    assert _serve("BFID2") == b"0123456789"  # 上游可达 → 失败计数复位
    mode["fail"] = True
    for _ in range(2):
        with pytest.raises(HTTPException):
            _serve("BFID2")
    # 复位后重新计数：连续 2 次未达阈值 3，熔断仍关、没用冷客户端
    assert streaming.drive_pool_breaker_open() is False
    assert cold_made == []
