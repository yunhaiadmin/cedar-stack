"""回归：docker/nginx.conf 必须放行「不带 /emby 前缀」的 Emby API 路径。

根因：后端网关（emby_router + search + preflight）对 /emby/* 与裸根
/Users/...、/System/... 两种形式都注册了路由，但 web 容器 nginx 此前
只有 /emby/ 一个协议 location，裸根路径掉进 SPA 的 location /：
GET 被 try_files 回退成 index.html（客户端拿到 HTML 当 JSON 解析失败），
POST 直接 405（静态文件不许 POST）。Reflix（UA ReflixiOS）2026-11-23
真机实证：POST /Users/AuthenticateByName → 405，请求根本没到鉴权层
（login_logs 无记录）；同窗口 WWPlayer、Hills 的裸根 GET 也在吃 SPA 的
HTML。真 Emby 服务器根路径与 /emby 双通，我们只通了 /emby。

修法：nginx 加一条锚定正则 location，段名单逐段来自网关路由表，
把裸根 Emby API 路径与 /emby/ 同口径反代到 aetrix_backend。
本测试把段名单、段边界、与 SPA/静态块的隔离关系钉死。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
NGINX_CONF = REPO / "docker" / "nginx.conf"

# 网关路由表（emby_router + search_router + preflight_router，含
# install_mount/session/image_routes 替换后）实际注册的 Emby API 顶级段。
# 增删段必须先改后端路由，再同步改这里与 nginx.conf。
EXPECTED_SEGMENTS = {
    "Activity", "Branding", "DisplayPreferences", "EpisodeGroups",
    "Genres", "Items", "Library", "LiveTV", "Localization",
    "MediaBackup", "Movies", "OfficialRatings", "Persons",
    "PlayPreflight", "Plugins", "ScheduledTasks", "Search",
    "Sessions", "Shows", "Studios", "System", "Tags", "Trailers",
    "UnifiedSearch", "UserViews", "Users", "Videos", "Web", "Years",
    # 小写段：流媒体裸根别名与 QuickConnect，网关同样注册了裸根路由
    "videos", "quickconnect",
    # 文件式端点：网关注册了裸根 /swagger.json
    "swagger.json",
}

# 明确不放行的段（后端裸根无对应路由，放行也只是到后端 404）：
# - system / branding 小写：仅 /emby 下有小写别名，裸根没有
# - Devices：网关未实现该段（/emby 下也没有）
EXCLUDED_SEGMENTS = {"system", "branding", "Devices", "api"}


def _bare_location(text: str) -> tuple[str, str] | None:
    """取裸根 Emby 正则 location 的 (正则, 块体)。"""
    m = re.search(r"location\s+~\s+(\S+)\s*\{", text)
    if not m:
        return None
    brace = text.find("{", m.start())
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return m.group(1), text[brace + 1:i]
    return None


def _segments_of(pattern: str) -> set[str]:
    m = re.search(r"\(\??:?([^)]+)\)", pattern)
    assert m, pattern
    return {alt.replace("\\.", ".") for alt in m.group(1).split("|")}


def test_bare_emby_location_exists_and_proxies_like_emby():
    text = NGINX_CONF.read_text(encoding="utf-8")
    found = _bare_location(text)
    assert found is not None
    _, block = found
    # 与 /emby/ 同口径：同一上游、keepalive 形态、流式关缓冲、长读超时
    assert "proxy_pass http://aetrix_backend;" in block
    assert "proxy_http_version 1.1;" in block
    assert 'proxy_set_header Connection "";' in block
    assert "proxy_buffering off;" in block
    assert "proxy_read_timeout 300s;" in block
    # X-Real-IP 发 realip 还原后的 $remote_addr（A-068：双层下即上层写好的
    # 客户端 IP、不是 docker 网关，2026-10-22 的纪律由 realip 承接；旧钉版
    # $http_x_real_ip 无条件透传可被单层部署的客户端伪造，已改判。realip
    # 配置本体由 tests/test_nginx_api_svg_and_realip.py 钉）
    assert "proxy_set_header X-Real-IP $remote_addr;" in block


def test_bare_emby_segment_set_matches_gateway_table():
    text = NGINX_CONF.read_text(encoding="utf-8")
    found = _bare_location(text)
    assert found is not None
    pattern, _ = found
    segments = _segments_of(pattern)
    assert segments == EXPECTED_SEGMENTS
    assert not (segments & EXCLUDED_SEGMENTS)


def test_bare_emby_pattern_matching_boundaries():
    text = NGINX_CONF.read_text(encoding="utf-8")
    found = _bare_location(text)
    assert found is not None
    pattern, _ = found
    rx = re.compile(pattern)
    should_match = [
        "/Users/AuthenticateByName",   # Reflix 实证路径
        "/System/Info/Public",         # 客户端探活
        "/System/Info",
        "/Items",                      # 段本身（带查询串的列表请求）
        "/Items/abc123/Items",
        "/Shows/NextUp",
        "/Sessions/abc/Playing",
        "/Videos/123/stream",
        "/videos/123/stream",
        "/Library/MediaFolders",
        "/Search/Hints",
        "/quickconnect/info",
        "/Web/DefaultRoutingMap",
        "/swagger.json",
    ]
    for path in should_match:
        assert rx.search(path), path
    should_not_match = [
        "/",                    # SPA 首页
        "/index.html",
        "/library",             # SPA 路由（小写，靠大小写隔离）
        "/library/movies",
        "/game/account",
        "/login",
        "/api/health",          # 站点自身 API 走 /api/ 块
        "/api/user/auth/me",
        "/admin/",
        "/UsersX",              # 段边界：段名后必须是 / 或结尾
        "/UsersX/AuthenticateByName",
        "/system/info/public",  # 小写 system 裸根无路由，不放行
        "/Devices/List",        # 网关未实现
        "/assets/index-abc123.js",
        "/wasm/decode/ffmpeg.wasm",
    ]
    for path in should_not_match:
        assert not rx.search(path), path


def test_bare_emby_location_precedes_static_asset_regex():
    # 正则 location 按文件顺序取首个命中：裸根 API 块必须排在静态资源
    # 正则块之前，否则带文件式后缀的 API 路径会被静态块抢走。
    text = NGINX_CONF.read_text(encoding="utf-8")
    bare_idx = text.find("location ~ ^/(")
    static_idx = text.find("location ~* \\.")
    assert bare_idx >= 0
    assert static_idx >= 0
    assert bare_idx < static_idx


def test_spa_fallback_still_intact():
    # 红线：SPA 回退与 /api/ 反代不许被这次改动撞坏。
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html;" in text
    assert "location ^~ /api/ {" in text  # A-067 起带 ^~（防静态正则劫持 /api/*.svg）
