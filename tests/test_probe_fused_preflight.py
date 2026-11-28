# -*- coding: utf-8 -*-
"""C-11：probe 单条目远端请求融合——快乐路径 3 次请求 → 2 次。

旧流程：独立预检（stream Range 0-0）＋头尾探测（头/尾两次 Range GET）
＝3 次远端请求。新流程：头尾探测适用时不再发独立预检，头/尾 Range
响应的 Content-Type 检查兼任预检（text/html → _LoginPageResponse →
probe_metadata 翻译成与旧预检逐字相同的 login_page 结果）。
头尾不适用（小文件等）时保留独立预检，行为不变。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from unittest import mock

import pytest

from backend.emby_server import scanner

URL = "http://rcd.example/media/big.mp4"  # L-045 起 mkv 只取头窗（1 次 GET），双取口径改用 mp4 钉
SIZE = 30 * 1024 * 1024  # > 头 4MiB + 尾 16MiB，走头尾探测


class _Resp:
    def __init__(self, status_code=206, content_type="video/x-matroska", body=b""):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, _n):
        yield self.content


class _CountingClient:
    """按 Range 头回对应长度的假响应，并数 get/stream 调用次数。"""

    def __init__(self, mode="media"):
        self.mode = mode
        self.get_calls = []
        self.stream_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _respond(self, headers):
        if self.mode == "login":
            return _Resp(200, "text/html", b"<html>login</html>")
        rng = (headers or {}).get("Range", "")
        if rng.startswith("bytes="):
            start_s, end_s = rng[6:].split("-", 1)
            n = int(end_s) - int(start_s) + 1
            return _Resp(206, "video/x-matroska", b"\x00" * n)
        return _Resp(200, "video/x-matroska", b"\x00" * 1024)

    def get(self, url, headers=None):
        self.get_calls.append((url, headers))
        return self._respond(headers)

    def stream(self, method, url, headers=None):
        self.stream_calls.append((method, url, headers))
        return self._respond(headers)


def _fake_ffprobe_ok(path, headers, size=0, **kw):
    return {"format": {"duration": "60", "size": str(size), "bit_rate": "1000"},
            "streams": []}


class TestFusedPreflight:
    def test_large_file_two_requests_no_preflight(self):
        client = _CountingClient("media")
        with mock.patch("httpx.Client", return_value=client), \
                mock.patch.object(scanner, "_ffprobe", _fake_ffprobe_ok):
            result = scanner.probe_metadata(URL, {}, SIZE)
        assert result.get("_probe_backend") == "headtail"
        assert len(client.get_calls) == 2, f"头尾两次 GET，实得 {len(client.get_calls)}"
        assert client.stream_calls == [], "头尾适用时不许再发独立预检"

    def test_login_page_detected_via_fused_check(self):
        client = _CountingClient("login")
        ff = mock.Mock(wraps=_fake_ffprobe_ok)
        with mock.patch("httpx.Client", return_value=client), \
                mock.patch.object(scanner, "_ffprobe", ff):
            result = scanner.probe_metadata(URL, {}, SIZE)
        assert result.get("_error") == "login_page"
        assert result.get("_http_code") == 200
        assert ff.call_count == 0, "判登录页后不许再跑 ffprobe"
        assert client.stream_calls == []

    def test_small_file_keeps_standalone_preflight(self):
        client = _CountingClient("media")
        with mock.patch("httpx.Client", return_value=client), \
                mock.patch.object(scanner, "_ffprobe", _fake_ffprobe_ok):
            result = scanner.probe_metadata(URL, {}, 1024 * 1024)
        assert result.get("_error") != "login_page"
        assert len(client.stream_calls) == 1, "小文件保留独立预检（行为不变）"
        assert client.get_calls == []

    def test_small_file_login_still_detected_by_preflight(self):
        client = _CountingClient("login")
        with mock.patch("httpx.Client", return_value=client), \
                mock.patch.object(scanner, "_ffprobe", _fake_ffprobe_ok):
            result = scanner.probe_metadata(URL, {}, 1024 * 1024)
        assert result.get("_error") == "login_page"
        assert len(client.stream_calls) == 1


class TestFetchRangeLoginSignal:
    def test_html_2xx_raises_login_signal(self):
        client = _CountingClient("login")
        with mock.patch("httpx.Client", return_value=client):
            with pytest.raises(scanner._LoginPageResponse):
                scanner._http_fetch_range(URL, {}, 0, 100)

    def test_media_206_returns_body(self):
        client = _CountingClient("media")
        with mock.patch("httpx.Client", return_value=client):
            body = scanner._http_fetch_range(URL, {}, 0, 99)
        assert body == b"\x00" * 100

    def test_http_error_is_runtime_not_login(self):
        class _ErrClient(_CountingClient):
            def _respond(self, headers):
                return _Resp(403, "text/html", b"forbidden")

        client = _ErrClient()
        with mock.patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError) as ei:
                scanner._http_fetch_range(URL, {}, 0, 100)
        assert not isinstance(ei.value, scanner._LoginPageResponse)
