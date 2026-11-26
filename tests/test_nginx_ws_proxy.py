"""守卫：docker/nginx.conf 必须有 /ws/ 的 WebSocket 代理 location。

缺陷背景（2026-11-26 用户实报）：后端 /ws/{user_id}（backend/
websocket.py，通知推送＋一起看信令共用）一直都在，但 aetrix-web 的
nginx 没有 /ws/ 的 location，请求掉进 location / 被 try_files 回退
成 SPA 的 index.html（200 + HTML），浏览器 WS 握手拿不到 101
Switching Protocols，一起看永远建不了房。生产 access log 实证：
32 小时内 /ws/ 仅 1 次请求、状态 200（2629 字节 = 回退的 HTML），
101 响应 0 次；同窗口绕过 nginx 直连后端 127.0.0.1:8000 握手即
101、建房/入房/房主独控广播全通——病灶只在 nginx 这一层。

钉死形态：
- location /ws/ 存在且 proxy_pass 到 aetrix_backend 上游；
- proxy_http_version 1.1 且透传 Upgrade / Connection: upgrade
  （缺一，握手就升不了级）；
- proxy_read_timeout 放长（长连不许被默认 60s 掐断）；
- X-Real-IP 沿 A-068 口径发 $remote_addr；
- 顺带守 #118/#460 纪律：两个 index.html 的 no-cache 块与 upstream
  的 resolver 动态解析形态不许被动。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
NGINX_CONF = REPO / "docker" / "nginx.conf"


def _block(text: str, header: str) -> str | None:
    """取指定块全文（花括号计数；块内注释不含花括号）。"""
    start = text.find(header)
    if start < 0:
        return None
    brace = text.find("{", start)
    if brace < 0:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
    return None


def _directive_lines(block: str, name: str) -> list[str]:
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(rf"{re.escape(name)}\b", stripped):
            lines.append(stripped)
    return lines


def _conf() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def test_ws_location_exists_and_proxies_to_backend():
    block = _block(_conf(), "location /ws/")
    assert block is not None
    passes = _directive_lines(block, "proxy_pass")
    assert len(passes) == 1
    assert "http://aetrix_backend" in passes[0]


def test_ws_location_upgrades_protocol():
    block = _block(_conf(), "location /ws/")
    assert block is not None
    versions = _directive_lines(block, "proxy_http_version")
    assert len(versions) == 1 and "1.1" in versions[0]
    headers = _directive_lines(block, "proxy_set_header")
    upgrade = [h for h in headers if h.startswith("proxy_set_header Upgrade")]
    assert len(upgrade) == 1 and "$http_upgrade" in upgrade[0]
    conn = [h for h in headers if h.startswith("proxy_set_header Connection")]
    assert len(conn) == 1 and "upgrade" in conn[0].lower()


def test_ws_location_long_read_timeout():
    block = _block(_conf(), "location /ws/")
    assert block is not None
    timeouts = _directive_lines(block, "proxy_read_timeout")
    assert len(timeouts) == 1
    m = re.search(r"(\d+)s", timeouts[0])
    assert m is not None and int(m.group(1)) >= 3600


def test_ws_location_realip_follows_a068():
    block = _block(_conf(), "location /ws/")
    assert block is not None
    headers = _directive_lines(block, "proxy_set_header")
    realip = [h for h in headers if h.startswith("proxy_set_header X-Real-IP")]
    assert len(realip) == 1 and "$remote_addr" in realip[0]


def test_index_no_cache_blocks_untouched():
    text = _conf()
    for header in ("location = /index.html", "location = /admin/index.html"):
        block = _block(text, header)
        assert block is not None
        cc = _directive_lines(block, "add_header Cache-Control")
        assert len(cc) == 1 and "no-cache" in cc[0]


def test_upstream_resolver_form_untouched():
    text = _conf()
    block = _block(text, "upstream aetrix_backend")
    assert block is not None
    assert "resolver 127.0.0.11" in block
    servers = _directive_lines(block, "server")
    assert any("resolve" in s for s in servers)
