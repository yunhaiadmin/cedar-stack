"""回归：docker/nginx.conf 的 /api/*.svg 劫持（A-067）与 X-Real-IP 透传（A-068）。

A-067 根因：
    后端下发的库封面占位图是 API URL（/api/user/media-library/libraries/
    <id>/cover-placeholder.svg，见 emby_server/library_covers.py 的
    _svg_url），但 nginx 的静态资源正则 location（~* \\.(...|svg|...)$）
    优先于无 ^~ 的前缀 location /api/，请求被静态块抢走、在 web 根目录
    找文件 → 404，占位图永远出不来。
    修法：location /api/ 加 ^~，命中前缀后不再评估正则 location。

A-068 根因：
    三个反代块都 `proxy_set_header X-Real-IP $http_x_real_ip;`——无条件
    透传客户端自带的 X-Real-IP。双层生产里上层（tls-bridge）会硬写此头，
    透传是对的；但单层部署（只有这台 nginx）时客户端可任意伪造此头，
    后端 ratelimit.get_client_ip 在 XFF 缺失/非法时回落采信它，限流键
    与登录日志的 IP 可被伪造轮换。
    修法：realip 模块——set_real_ip_from 只信自有代理层网段（与后端
    ratelimit._INFRA_PEER_NETS 同口径），real_ip_header X-Real-IP 让
    nginx 仅当直连对端可信时才用该头改写 $remote_addr；三个反代块改发
    $remote_addr（改写后的真值）。双层链路下后端收到的值与透传时相同
    （仍是上层写好的客户端 IP，不是 docker 网关地址，2026-10-22 的
    429 事故不复现）；单层时伪造头被忽略、发的是真直连 IP。

本测试是静态断言（与 tests/test_nginx_*.py 既有口径一致）：钉住
^~、realip 配置、可信网段与三处转发头形态，并顺带钉住不许误删的
两个 index.html no-cache 块与 upstream resolver 动态解析形态。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
NGINX_CONF = REPO / "docker" / "nginx.conf"


def _text() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _location_block(text: str, header_pattern: str) -> str:
    """取某个 location 块的块体（按花括号配平）。"""
    m = re.search(header_pattern, text)
    assert m, f"找不到 location：{header_pattern}"
    brace = text.find("{", m.start())
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
    raise AssertionError("location 块花括号不配平")


# ---------- A-067 ----------

def test_api_location_has_caret_tilde():
    text = _text()
    assert re.search(r"location\s+\^~\s+/api/\s*\{", text), (
        "location /api/ 必须带 ^~，否则静态资源正则会劫持 /api/*.svg 等 API 路径"
    )
    # 不许再留无 ^~ 的 /api/ 前缀块（双份定义 nginx 直接报错，这里防回退）
    assert not re.search(r"location\s+/api/\s*\{", text)


def test_static_regex_still_serves_spa_assets():
    # 静态正则块本身保留（SPA 带哈希资源靠它长缓存），只是不再抢 /api/
    text = _text()
    block = _location_block(text, r"location\s+~\*\s+\\\.\(js\|css")
    assert "immutable" in block


# ---------- A-068 ----------

TRUSTED_NETS = (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "169.254.0.0/16",
)


def test_realip_module_configured():
    text = _text()
    assert re.search(r"^\s*real_ip_header\s+X-Real-IP;", text, re.M), (
        "必须启用 real_ip_header X-Real-IP，让可信对端的 X-Real-IP 只经 realip 改写 $remote_addr"
    )
    for net in TRUSTED_NETS:
        assert re.search(rf"^\s*set_real_ip_from\s+{re.escape(net)};", text, re.M), (
            f"set_real_ip_from 缺自有代理层网段 {net}（须与后端 ratelimit._INFRA_PEER_NETS 同口径）"
        )


def test_no_raw_x_real_ip_passthrough():
    text = _text()
    assert "$http_x_real_ip" not in text, (
        "不许再无条件透传客户端自带的 X-Real-IP（$http_x_real_ip）"
    )


def test_all_proxy_blocks_send_realip_remote_addr():
    text = _text()
    blocks = [
        _location_block(text, r"location\s+\^~\s+/api/\s*\{"),
        _location_block(text, r"location\s+/emby/\s*\{"),
        _location_block(text, r"location\s+~\s+\^/\(Activity\|"),
    ]
    for block in blocks:
        assert "proxy_set_header X-Real-IP $remote_addr;" in block, (
            "反代块必须发 realip 改写后的 $remote_addr（双层=上层写好的客户端 IP，单层=真直连 IP）"
        )


# ---------- 守卫：不许误删的既有形态 ----------

def test_index_html_no_cache_blocks_intact():
    text = _text()
    for path in ("/index.html", "/admin/index.html"):
        block = _location_block(text, rf"location\s+=\s+{re.escape(path)}\s*\{{")
        assert 'add_header Cache-Control "no-cache";' in block


def test_upstream_resolver_form_intact():
    text = _text()
    assert "resolver 127.0.0.11 valid=10s" in text
    assert re.search(r"server\s+\S+\s+resolve;", text), "upstream server 行必须保留 resolve 动态解析"
