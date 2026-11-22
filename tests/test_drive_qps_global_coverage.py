# -*- coding: utf-8 -*-
"""全局 QPS 限速收口（谷歌盘调用削减第一批 #4 / 审计 P5）。

现状（改前）：8 QPS 令牌桶（drive_changes._api_limiter）只覆盖走
``_api_get`` 的 changes + mount_drive 两路；以下路径全部绕行：
- transfer 的 DriveApiClient._request / download_range /
  _RawTokenClient._request / UserDriveApiClient.resumable_upload
- drive_exists 的 httpx（api._drive_exists_http_get）
- 中继取流解析（streaming 的 Drive 腿：resolve + relay + 预读）
- mp4_faststart 的 peek / download / upload

本文件钉住收口后的口径：
- 横切只许一套：各路径都过 drive_changes 的同一令牌桶共享入口
  （阻塞型 acquire_api_token / 快速失败型 try_acquire_api_token），
  不许第二套限速器。
- 播放热路径（drive_exists / 中继）拿不到令牌时快速失败回退：
  不阻塞、不抛错，按现状直发（限速是防叠加打爆的闸，不是播放门禁）。
- 挂载中继腿（非 Drive）不进 Drive 限速口径。
- transfer / faststart 是沉睡路径（转存总闸关、绑盘下线）：只接线，
  业务语义（401 刷新重试、状态码映射、上传协议）逐字不变。

全 mock：绝不真实调用 Drive。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import contextlib
import io
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.emby_server import api
from backend.emby_server import drive_changes as dc
from backend.emby_server import mp4_faststart as fs
from backend.emby_server import streaming
from backend.emby_server import transfer_variant as tv


@pytest.fixture(autouse=True)
def _reset_limiter():
    dc.set_api_qps(8.0)
    dc._api_limiter._tokens = 8.0
    dc._api_qps_last_refresh = 0.0
    yield
    dc.set_api_qps(8.0)
    dc._api_limiter._tokens = 8.0
    dc._api_qps_last_refresh = 0.0


def _counting_acquire(monkeypatch):
    """阻塞型共享入口的计数替身：计数 + 真扣一个令牌（跳过配置复检）。"""
    calls = []

    def _acquire():
        calls.append(1)
        dc._api_limiter.acquire()

    monkeypatch.setattr(dc, "acquire_api_token", _acquire, raising=False)
    return calls


def _counting_try_acquire(monkeypatch):
    """快速失败型共享入口的计数替身：计数 + 真走非阻塞取令。"""
    calls = []

    def _try(*args, **kwargs):
        calls.append(1)
        return dc._api_limiter.try_acquire()

    monkeypatch.setattr(dc, "try_acquire_api_token", _try, raising=False)
    return calls


# ---------------------------------------------------------------------------
# 0. 共享入口本体：同一令牌桶的阻塞 / 非阻塞两张脸
# ---------------------------------------------------------------------------

class TestSharedLimiterEntry:
    def test_try_acquire_consumes_then_fails_fast(self):
        lim = dc.ApiRateLimiter(2.0)
        assert lim.try_acquire() is True
        assert lim.try_acquire() is True
        t0 = time.monotonic()
        assert lim.try_acquire() is False
        # 非阻塞：桶空立即返回，不许等一个令牌周期（2 QPS 时为 0.5s）
        assert time.monotonic() - t0 < 0.2

    def test_try_acquire_api_token_shares_singleton(self, monkeypatch):
        monkeypatch.setattr(dc, "refresh_api_qps",
                            lambda *a, **k: dc._api_limiter._qps)
        before = dc._api_limiter._tokens
        assert dc.try_acquire_api_token() is True
        assert dc._api_limiter._tokens == pytest.approx(before - 1.0, abs=0.2)

    def test_acquire_api_token_shares_singleton(self, monkeypatch):
        monkeypatch.setattr(dc, "refresh_api_qps",
                            lambda *a, **k: dc._api_limiter._qps)
        dc._api_limiter._tokens = 0.0
        dc.set_api_qps(20.0)  # 高 QPS：补令快，阻塞等待有界
        t0 = time.monotonic()
        dc.acquire_api_token()
        assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# 1. drive_exists（播放热路径）：过限速 + 拿不到令快速失败直发
# ---------------------------------------------------------------------------

class _FakeExistsClient:
    def __init__(self):
        self.get_calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls.append(url)
        return SimpleNamespace(status_code=200)


class TestDriveExistsRateLimit:
    def test_http_get_passes_limiter(self, monkeypatch):
        calls = _counting_try_acquire(monkeypatch)
        client = _FakeExistsClient()
        monkeypatch.setattr(api, "_drive_exists_client", lambda: client)
        resp = api._drive_exists_http_get(
            "https://www.googleapis.com/drive/v3/files/F1",
            params={"fields": "id"}, headers={}, timeout=3)
        assert resp.status_code == 200
        assert len(client.get_calls) == 1
        assert len(calls) >= 1, "drive_exists 未经过全局限速器（绕行未收口）"

    def test_http_get_proceeds_when_bucket_empty(self, monkeypatch):
        # 快速失败回退：拿不到令牌也照常直发，绝不阻塞播放热路径
        monkeypatch.setattr(dc, "try_acquire_api_token",
                            lambda *a, **k: False, raising=False)
        client = _FakeExistsClient()
        monkeypatch.setattr(api, "_drive_exists_client", lambda: client)
        t0 = time.monotonic()
        resp = api._drive_exists_http_get(
            "https://www.googleapis.com/drive/v3/files/F2",
            params={"fields": "id"}, headers={}, timeout=3)
        assert resp.status_code == 200
        assert len(client.get_calls) == 1
        assert time.monotonic() - t0 < 1.0


# ---------------------------------------------------------------------------
# 2. transfer DriveApiClient（沉睡路径）：每发一次请求过一次阻塞限速
# ---------------------------------------------------------------------------

def _fake_resp(status=200, content=b"{}", json_data=None, headers=None):
    return SimpleNamespace(
        status_code=status, content=content, headers=headers or {},
        json=lambda: (json_data or {}))


@pytest.fixture()
def _tv_token(monkeypatch):
    monkeypatch.setattr(tv, "get_token_for_drive",
                        lambda drive_id, force_refresh=False: "TOK")


class TestTransferRateLimit:
    def test_request_passes_limiter(self, monkeypatch, _tv_token):
        calls = _counting_acquire(monkeypatch)
        monkeypatch.setattr(tv.httpx, "request",
                            lambda *a, **k: _fake_resp(200))
        resp = tv.DriveApiClient()._request(
            "GET", "https://www.googleapis.com/drive/v3/files", "drive1")
        assert resp.status_code == 200
        assert len(calls) == 1, "transfer _request 未经过全局限速器"

    def test_request_401_retry_passes_limiter_twice(
            self, monkeypatch, _tv_token):
        calls = _counting_acquire(monkeypatch)
        seq = [_fake_resp(401), _fake_resp(200)]
        monkeypatch.setattr(tv.httpx, "request",
                            lambda *a, **k: seq.pop(0))
        resp = tv.DriveApiClient()._request(
            "GET", "https://www.googleapis.com/drive/v3/files", "drive1")
        assert resp.status_code == 200
        assert len(calls) == 2  # 每次实发都计数（含 401 刷新重试）

    def test_download_range_passes_limiter(self, monkeypatch, _tv_token):
        calls = _counting_acquire(monkeypatch)
        monkeypatch.setattr(
            tv.httpx, "request",
            lambda *a, **k: _fake_resp(206, content=b"abcd"))
        data = tv.DriveApiClient().download_range("drive1", "FID", 0, 3)
        assert data == b"abcd"
        assert len(calls) >= 1, "download_range 绕过 _request 直发，未过限速"

    def test_raw_token_client_passes_limiter(self, monkeypatch):
        calls = _counting_acquire(monkeypatch)
        monkeypatch.setattr(tv.httpx, "request",
                            lambda *a, **k: _fake_resp(200))
        resp = tv._RawTokenClient("RAWTOK")._request(
            "GET", "https://www.googleapis.com/drive/v3/files", "drive1")
        assert resp.status_code == 200
        assert len(calls) >= 1, "_RawTokenClient._request 未过限速"

    def test_resumable_upload_passes_limiter(self, monkeypatch):
        calls = _counting_acquire(monkeypatch)
        data = b"data"
        reader = io.BytesIO(data)

        def _fake_request(method, url, **kwargs):
            if method == "POST":
                return _fake_resp(
                    200, headers={"location": "https://upload.example/s"})
            return _fake_resp(200, content=b'{"id": "upfid"}',
                              json_data={"id": "upfid"})

        monkeypatch.setattr(tv.httpx, "request", _fake_request)
        monkeypatch.setattr(
            tv.UserDriveApiClient, "_headers",
            lambda self, d, force_refresh=False:
            {"Authorization": "Bearer TOK"})
        client = tv.UserDriveApiClient.__new__(tv.UserDriveApiClient)
        fid = client.resumable_upload(
            "myDrive:7", "a.mkv", "folder", len(data), reader)
        assert fid == "upfid"
        # 会话创建 POST + 分段 PUT 至少两次实发，都要计数
        assert len(calls) >= 2, "resumable_upload 未过限速"


# ---------------------------------------------------------------------------
# 3. mp4_faststart（沉睡路径）：peek / download / upload 过阻塞限速
# ---------------------------------------------------------------------------

class TestFaststartRateLimit:
    def test_peek_passes_limiter(self, monkeypatch):
        calls = _counting_acquire(monkeypatch)

        @contextlib.contextmanager
        def _fake_stream(*args, **kwargs):
            yield SimpleNamespace(
                status_code=206, iter_bytes=lambda: iter([b"\x00" * 64]))

        monkeypatch.setattr(httpx, "stream", _fake_stream)
        fs.peek_drive_moov("FID", "TOK", 0)
        assert len(calls) >= 1, "mp4_faststart peek 未过限速"

    def test_download_passes_limiter(self, monkeypatch, tmp_path):
        calls = _counting_acquire(monkeypatch)

        @contextlib.contextmanager
        def _fake_stream(*args, **kwargs):
            yield SimpleNamespace(
                status_code=200,
                iter_bytes=lambda n=None: iter([b"hello"]))

        monkeypatch.setattr(httpx, "stream", _fake_stream)
        dest = str(tmp_path / "out.mp4")
        fs.download_drive_file("FID", "TOK", dest)
        with open(dest, "rb") as fh:
            assert fh.read() == b"hello"
        assert len(calls) >= 1, "mp4_faststart download 未过限速"

    def test_upload_passes_limiter_per_request(self, monkeypatch, tmp_path):
        calls = _counting_acquire(monkeypatch)
        src = tmp_path / "out.mp4"
        src.write_bytes(b"abcdef")

        class _Resp:
            def __init__(self, code, headers=None):
                self.status_code = code
                self.headers = headers or {}

        def _request(method, url, **kwargs):
            if method == "PATCH":
                return _Resp(200, {"location": "https://upload.example/s"})
            return _Resp(200)

        fs.upload_drive_replace("fid-9", "tok", str(src), request=_request)
        # 会话创建 + 分段 PUT 两次实发都要计数
        assert len(calls) >= 2, "mp4_faststart upload 未过限速"


# ---------------------------------------------------------------------------
# 4. 中继（播放热路径）：Drive 腿过快速失败限速；挂载腿不进 Drive 口径
# ---------------------------------------------------------------------------

_FINAL = "https://storage-download.googleapis.com/final/file?sig=abc"


def _run(coro):
    return asyncio.run(coro)


class _FakeResp:
    def __init__(self, status=206, chunks=(b"0123456789",)):
        self.status_code = status
        self.headers = {"content-type": "video/mp4",
                        "content-range": "bytes 0-9/100",
                        "accept-ranges": "bytes"}
        self._chunks = list(chunks)

    async def aiter_bytes(self, _n):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b""

    async def aclose(self):
        pass


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture()
def _relay_clients(monkeypatch):
    pooled = _FakeClient()
    monkeypatch.setattr(streaming, "_drive_relay_client",
                        lambda: pooled, raising=False)
    monkeypatch.setattr(streaming, "_drive_redirect_client",
                        lambda: _FakeClient(), raising=False)
    monkeypatch.setattr(streaming, "_mount_relay_client",
                        lambda: pooled, raising=False)
    state = getattr(streaming, "_DRIVE_POOL_STATE", None)
    if isinstance(state, dict):
        state.update({"failures": 0, "open_until": 0.0})
    monkeypatch.delenv("DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC",
                       raising=False)
    return pooled


def _patch_follow_ok(monkeypatch):
    calls = []

    async def _follow(client, url, forward, silent=False):
        calls.append(url)
        if "www.googleapis.com" in url:
            return _FakeResp(206), _FINAL, 1
        return _FakeResp(206), url, 0

    monkeypatch.setattr(streaming, "_follow_redirects", _follow)
    return calls


class TestRelayRateLimit:
    def test_resolve_passes_limiter(self, monkeypatch, _relay_clients):
        calls = _counting_try_acquire(monkeypatch)
        _patch_follow_ok(monkeypatch)
        uc = "https://drive.google.com/uc?id=RLFILE1&export=download"
        out = _run(streaming.resolve_drive_final_url(
            uc, {"Authorization": "Bearer TOKA"}))
        assert out == _FINAL
        assert len(calls) >= 1, "中继解析 resolve 未经过全局限速器"

    def test_resolve_proceeds_when_bucket_empty(
            self, monkeypatch, _relay_clients):
        # 快速失败回退：拿不到令也照常解析，绝不阻塞/不抛错
        monkeypatch.setattr(dc, "try_acquire_api_token",
                            lambda *a, **k: False, raising=False)
        _patch_follow_ok(monkeypatch)
        uc = "https://drive.google.com/uc?id=RLFILE2&export=download"
        out = _run(streaming.resolve_drive_final_url(
            uc, {"Authorization": "Bearer TOKA"}))
        assert out == _FINAL

    def test_drive_relay_passes_limiter(self, monkeypatch, _relay_clients):
        calls = _counting_try_acquire(monkeypatch)
        monkeypatch.setattr(dc, "get_token_for_drive",
                            lambda drive_id, force_refresh=False: "TOK1")
        _patch_follow_ok(monkeypatch)

        async def _go():
            resp = await streaming.serve_drive_relay_async(
                SimpleNamespace(headers={}), "DRIVE9", "RFID1")
            return b"".join([c async for c in resp.body_iterator])

        assert _run(_go()) == b"0123456789"
        assert len(calls) >= 1, "Drive 中继腿未经过全局限速器"

    def test_mount_relay_not_rate_limited(self, monkeypatch, _relay_clients):
        calls = _counting_try_acquire(monkeypatch)
        _patch_follow_ok(monkeypatch)

        async def _go():
            resp = await streaming.serve_mount_relay_async(
                "https://example.com/file.mp4",
                SimpleNamespace(headers={}))
            return b"".join([c async for c in resp.body_iterator])

        assert _run(_go()) == b"0123456789"
        assert calls == [], "挂载中继腿（非 Drive）不许进 Drive 限速口径"
