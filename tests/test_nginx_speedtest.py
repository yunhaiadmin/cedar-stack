"""守卫：docker/nginx.conf 必须有 /speedtest/bin 的确切 location。

缺陷背景（2026-11-24）：EM 后端有 /speedtest/bin 端点还不够——www/cdn
线路入口先落 aetrix-web 的 nginx，此前没有这条 location，请求掉进
location / 被 try_files 回退成 SPA 的 index.html（200 + text/html），
前端测速页拿到 HTML 必然报「连接失败」。

钉死形态：
- location = /speedtest/bin 存在且 proxy_pass 到 aetrix_backend 上游；
- proxy_buffering off（流式直达，测速才准）、gzip off；
- limit_conn 限速在位（防把测速口当免费带宽刷）；
- 顺带守 #118 纪律：两个 index.html 的 no-cache 块不许被动。
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


def test_speedtest_location_exists_and_proxies_to_backend():
    block = _block(_conf(), "location = /speedtest/bin")
    assert block is not None
    passes = _directive_lines(block, "proxy_pass")
    assert len(passes) == 1
    assert "http://aetrix_backend" in passes[0]


def test_speedtest_location_streams_without_gzip():
    block = _block(_conf(), "location = /speedtest/bin")
    assert block is not None
    buffering = _directive_lines(block, "proxy_buffering")
    assert len(buffering) == 1 and "off" in buffering[0]
    gz = _directive_lines(block, "gzip")
    assert len(gz) == 1 and "off" in gz[0]


def test_speedtest_location_rate_limited():
    text = _conf()
    block = _block(text, "location = /speedtest/bin")
    assert block is not None
    conns = _directive_lines(block, "limit_conn")
    assert len(conns) == 1
    zone_name = conns[0].split()[1]
    # zone 必须在 http 级有定义，否则 nginx -t 直接 emerg
    assert f"limit_conn_zone" in text and f"zone={zone_name}:" in text


def test_index_html_no_cache_blocks_survive():
    text = _conf()
    for header in ("location = /index.html", "location = /admin/index.html"):
        block = _block(text, header)
        assert block is not None, header
        headers = _directive_lines(block, "add_header")
        assert any(
            "Cache-Control" in h and "no-cache" in h for h in headers
        ), header
