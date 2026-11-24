"""回归：docker/nginx.conf 的 upstream 必须动态重解析后端主机名。

根因：upstream 里 `server ${BACKEND_HOST};` 只在 web 容器启动时解析一次，
后端容器重建换 IP 后 nginx 一直打旧 IP，公网持续 502，必须手动重启
aetrix-web 才恢复（2026-11-22～23 部署 #426/#433/#438/#440/#449/#455
后至少 5 次复发）。server 块里的 resolver 对 upstream 成员不生效。

修法（nginx 1.27.3+，运行镜像 1.30.7）：upstream 块内置
`resolver 127.0.0.11 valid=10s` + server 行加 `resolve` 参数，主机名按
10s 周期向 Docker 内置 DNS 重解析。`zone` 是 resolve 的硬性配套：
upstream 不在共享内存里 nginx 直接 emerg 拒启（2026-11-23 生产机
scratch 实测：补 zone 后后端同名重建换 IP，未重启的 web 在 15s 后
已把 /api/ 打到新 IP 并回 200）。

本测试把上述形态钉住，并顺带守住 #118 纪律（两个 index.html 的
no-cache 块不许被动）与 keepalive 形态（/api/、/emby/ 仍走 upstream
+ HTTP/1.1 + 空 Connection 头）。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
NGINX_CONF = REPO / "docker" / "nginx.conf"


def _block(text: str, header: str) -> str | None:
    """取指定块全文（花括号计数；块内注释不含花括号）。"""
    start = text.find(header)
    if start < 0 and header.startswith("location "):
        # A-067 起 /api/ 带 ^~ 修饰，找块兼容两种写法
        start = text.find("location ^~ " + header[len("location "):])
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


def _upstream_block(text: str) -> str | None:
    return _block(text, "upstream aetrix_backend")


def _directive_lines(block: str, name: str) -> list[str]:
    """取块内某指令的行（去掉注释行）。"""
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(rf"{re.escape(name)}\b", stripped):
            lines.append(stripped)
    return lines


def test_upstream_has_docker_dns_resolver():
    block = _upstream_block(NGINX_CONF.read_text(encoding="utf-8"))
    assert block is not None
    resolvers = _directive_lines(block, "resolver")
    assert len(resolvers) == 1
    assert "127.0.0.11" in resolvers[0]
    assert "valid=10s" in resolvers[0]


def test_upstream_server_line_uses_resolve_param():
    block = _upstream_block(NGINX_CONF.read_text(encoding="utf-8"))
    assert block is not None
    servers = _directive_lines(block, "server")
    assert len(servers) == 1
    assert "${BACKEND_HOST}" in servers[0]
    assert re.search(r"\bresolve\b", servers[0])


def test_upstream_has_shared_memory_zone():
    # 无 zone 时 nginx emerg 拒启：resolving names at run time requires
    # upstream ... to be in shared memory（scratch 实测）。
    block = _upstream_block(NGINX_CONF.read_text(encoding="utf-8"))
    assert block is not None
    zones = _directive_lines(block, "zone")
    assert len(zones) == 1
    assert zones[0].startswith("zone aetrix_backend")


def test_upstream_keeps_keepalive():
    block = _upstream_block(NGINX_CONF.read_text(encoding="utf-8"))
    assert block is not None
    assert _directive_lines(block, "keepalive") == ["keepalive 32;"]


def test_static_upstream_without_resolve_is_rejected():
    text = ("upstream aetrix_backend {\n"
            "    server ${BACKEND_HOST};\n"
            "    keepalive 32;\n"
            "}\n")
    block = _upstream_block(text)
    assert block is not None
    assert _directive_lines(block, "resolver") == []
    servers = _directive_lines(block, "server")
    assert not re.search(r"\bresolve\b", servers[0])


def test_both_index_html_locations_still_no_cache():
    text = NGINX_CONF.read_text(encoding="utf-8")
    for location in ("= /index.html", "= /admin/index.html"):
        block = _block(text, f"location {location}")
        assert block is not None, location
        assert 'add_header Cache-Control "no-cache"' in block, location


def test_api_and_emby_still_proxy_to_upstream_with_keepalive_form():
    text = NGINX_CONF.read_text(encoding="utf-8")
    for location in ("/api/", "/emby/"):
        block = _block(text, f"location {location}")
        assert block is not None, location
        assert "proxy_pass http://aetrix_backend;" in block, location
        # keepalive 生效形态：HTTP/1.1 + 空 Connection 头
        assert "proxy_http_version 1.1;" in block, location
        assert 'proxy_set_header Connection "";' in block, location


def _server_level_directives(text: str, name: str) -> list[str]:
    """取 server 块直属（不在任何 location 块内）的某指令行。

    先取 server 块全文，再逐行剥掉 location 块（花括号计数），
    剩下的非注释行里挑以指令名开头的。
    """
    server = _block(text, "server {")
    assert server is not None
    kept: list[str] = []
    depth = 0
    skipping = False
    for line in server.splitlines():
        stripped = line.strip()
        if not skipping and stripped.startswith("location"):
            depth = line.count("{") - line.count("}")
            skipping = depth > 0
            continue
        if skipping:
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                skipping = False
            continue
        if stripped.startswith("#"):
            continue
        if re.match(rf"{re.escape(name)}\b", stripped):
            kept.append(stripped)
    return kept


def test_yamby_absolute_url_rewrite_shim_at_server_level():
    """Yamby 垫片：/emby<绝对URL> 在 server 级被还原成真实路径（2026-11-24）。

    Yamby/2.0.5.5 把 PlaybackInfo 下发的绝对签名流地址当相对路径拼到
    它配置的服务器基址后，请求变成 /embyhttps://<host>/emby/Videos/...
    ——不命中 /emby/ 前缀，掉进 SPA 的 try_files 回 index.html
    （200 + HTML），客户端以为在播、实际黑屏（生产 access log 两例
    实证）。垫片必须是 server 级 rewrite（location 选定前执行），
    替换串 $1 不带 ?（原查询串与签名参数由 nginx 自动保留）、break。
    """
    text = NGINX_CONF.read_text(encoding="utf-8")
    rewrites = [r for r in _server_level_directives(text, "rewrite")
                if "/embyhttp" in r]
    assert len(rewrites) == 1, rewrites
    m = re.fullmatch(r"rewrite\s+(\S+)\s+\$1\s+break;", rewrites[0])
    assert m, rewrites[0]
    pattern = m.group(1)
    # nginx 在 rewrite 匹配前已按 merge_slashes（默认开）把 URI 里的
    # 连续斜杠并成一个：原始 https:// 与合并后的 https:/ 两种形态都
    # 必须命中，捕获组必须是真实路径；正常路径不许被垫片碰到。
    for uri in (
        "/embyhttps://cdn.aetrix.love/emby/Videos/g1/stream.mkv",
        "/embyhttps:/cdn.aetrix.love/emby/Videos/g1/stream.mkv",
        "/embyhttp://emby.aetrix.love/emby/Videos/g1/stream.mkv",
        "/embyhttp:/emby.aetrix.love/emby/Videos/g1/stream.mkv",
    ):
        hit = re.match(pattern, uri)
        assert hit, uri
        assert hit.group(1) == "/emby/Videos/g1/stream.mkv", uri
    assert not re.match(pattern, "/emby/Videos/g1/stream.mkv")
    assert not re.match(pattern, "/library")
