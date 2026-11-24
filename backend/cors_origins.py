"""CORS 来源白名单：全站只许这一套（横切只许一套）。

EM（backend/main.py）与 EA（emby_api/main.py）的 CORSMiddleware 共用
本模块的解析与参数，两边不许再各写一份。

口径：
- 环境变量 CORS_ORIGINS 配了就以它为准（逗号分隔），部署侧可覆盖；
- 没配时回落本站自有域清单 SITE_ORIGINS——网页端从 www 入口跨域调
  cdn 的 /emby 网关是既定形态（/api/user/emby/server 下发
  base_url=https://cdn.aetrix.love），预检没人应答，搜索/列表/详情/
  网页播放器就全线「网络暂时连不上」（L-037，2026-11-24）。旧口径
  「为空不注册中间件」正是这次事故的根因：生产从没配过这个 env，
  于是预检 OPTIONS 打到路由层拿 405。故默认清单必须在代码里，
  不能只靠部署时记得配 env。
- 绝不出现 "*"。白名单制 + 带凭证的既有口径不变。
- allow_headers 按前端实际发送的头放行（user_frontend 实查）：
  axios 固定 Content-Type: application/json、Emby 调用带
  X-Emby-Token；播放器 fetch 探测带 Range；Authorization /
  X-Emby-Authorization / X-MediaBrowser-Token / X-Device-Id 是
  Emby 客户端协议头，一并保留（与 EA 既有清单一致）。
"""
from __future__ import annotations

import os

#: 本站自有域：www 主站、根域、cdn 对外入口、emby 源站。
SITE_ORIGINS: list[str] = [
    "https://www.aetrix.love",
    "https://aetrix.love",
    "https://cdn.aetrix.love",
    "https://emby.aetrix.love",
]

CORS_ALLOW_METHODS: list[str] = [
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS",
]

CORS_ALLOW_HEADERS: list[str] = [
    "Content-Type", "Authorization", "X-Emby-Authorization", "X-Emby-Token",
    "X-MediaBrowser-Token", "X-Device-Id", "Range",
]

#: 直连流与 HLS 依赖这些响应头能被前端读取（EA 既有口径）。
CORS_EXPOSE_HEADERS: list[str] = [
    "Content-Range", "Accept-Ranges", "Content-Length",
]


def resolve_cors_origins() -> list[str]:
    """解析 CORS 来源白名单：CORS_ORIGINS 环境变量优先，缺省回落本站自有域。"""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return list(SITE_ORIGINS)


#: 全局 CORS 不覆盖的路径：免鉴权公网测速口，端点自带
#: Access-Control-Allow-Origin: * 与自有 OPTIONS 处理（线路页可能跑在
#: 授权部署的自有域名下，不在本站清单里；口径见 backend/main.py 测速
#: 段注释「不给公网测速口复用全局 CORS」）。白名单中间件若吞掉它的
#: 预检，测速口径就被改写——故装配层面对该路径整体放行给端点自己。
CORS_BYPASS_PATHS = frozenset({"/speedtest/bin"})


class SiteCorsMiddleware:
    """CORSMiddleware 的全站统一装配（EM 与 EA 共用，参数只此一份）。

    除 CORS_BYPASS_PATHS 的路径自管 CORS 外，其余请求一律走白名单
    CORSMiddleware（来源解析见 resolve_cors_origins）。
    """

    def __init__(self, app):
        from fastapi.middleware.cors import CORSMiddleware

        self._app = app
        self._cors = CORSMiddleware(
            app,
            allow_origins=resolve_cors_origins(),
            allow_credentials=True,
            allow_methods=CORS_ALLOW_METHODS,
            allow_headers=CORS_ALLOW_HEADERS,
            expose_headers=CORS_EXPOSE_HEADERS,
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") in CORS_BYPASS_PATHS:
            await self._app(scope, receive, send)
            return
        await self._cors(scope, receive, send)
