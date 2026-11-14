"""出站 SSRF 守卫回归（原审计 A-053，随上游全清迁到中立出站层）。

守卫现位于 backend/emby_server/outbound_http.py（image_store /
media_routes 等活功能共用）：发请求前校验协议、userinfo、字面 IP
与域名解析结果，任一非公网即拦截；opener 不装重定向处理器，
3xx 一律不跟随。本地服务器只绑 127.0.0.1，命中计数证明
「被拦的请求根本没发出去」。
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.emby_server import outbound_http as oh


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _json_handler(hits):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self):
            hits.append(self.path)
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _reply
        do_POST = _reply

    return Handler


def _redirect_handler(hits, location):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _port(server):
    return server.server_address[1]


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.9",
    "169.254.1.1", "0.0.0.0", "::1", "::ffff:127.0.0.1",
])
def test_verify_blocks_non_public_literal_ips(host):
    """字面非公网 IP（含 IPv4 映射 IPv6 形式）一律拦截。"""
    with pytest.raises(oh.OutboundBlockedError):
        oh.verify_outbound_url(f"http://{host}:8096/Items")


@pytest.mark.parametrize("host", ["0x7f.0.0.1", "2130706433"])
def test_verify_blocks_non_decimal_loopback_forms(host):
    """非十进制回环写法经解析复验拦截（ipaddress 不认、getaddrinfo 认）。"""
    with pytest.raises(oh.OutboundBlockedError):
        oh.verify_outbound_url(f"http://{host}:8096/Items")


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "http://user:pass@8.8.8.8:8096/Items",
    "http://user@8.8.8.8:8096/Items",
    "http://:8096/Items",
])
def test_verify_blocks_scheme_userinfo_and_missing_host(url):
    with pytest.raises(oh.OutboundBlockedError):
        oh.verify_outbound_url(url)


def test_verify_allows_public_literal_ip():
    """公网字面 IP 不走 DNS、直接放行（不实际发请求）。"""
    oh.verify_outbound_url("http://8.8.8.8:8096/Items")
    oh.verify_outbound_url("https://1.1.1.1/System/Info/Public")


def test_safe_urlopen_to_loopback_blocked_no_hit():
    """safe_urlopen 对回环目标先拦后发：服务器 0 命中。"""
    hits = []
    server = _serve(_json_handler(hits))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{_port(server)}/Items",
            headers={"Accept": "application/json"})
        with pytest.raises(oh.OutboundBlockedError):
            oh.safe_urlopen(req, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
    assert hits == []


def test_opener_does_not_follow_redirect(monkeypatch):
    """出站 opener 不跟随 3xx：302 以 HTTPError 抛出，目标 0 命中。"""
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    target_hits = []
    target = _serve(_json_handler(target_hits))
    source_hits = []
    source = _serve(_redirect_handler(
        source_hits, f"http://127.0.0.1:{_port(target)}/Items"))
    try:
        opener = oh._build_opener()
        req = urllib.request.Request(
            f"http://127.0.0.1:{_port(source)}/System/Info/Public")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            opener.open(req, timeout=5)
        assert excinfo.value.code == 302
    finally:
        source.shutdown()
        source.server_close()
        target.shutdown()
        target.server_close()
    assert len(source_hits) == 1
    assert target_hits == []
