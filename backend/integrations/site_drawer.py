"""官网菜单抽屉能力（用户端落地页移动端菜单）。

官网落地页顶栏菜单打开的玻璃抽屉：导航列表、社区渠道卡片、底部
「进入后台」按钮，全部内容走配置（SystemConfig 单键
``site_drawer_config``，整份 JSON，结构校验在 config_schema 的
coerce_site_drawer_config），管理后台「系统设置 → 官网菜单抽屉」
卡片编辑，前端只做数据驱动渲染、不写死文案与链接。

读取口径：
- 存了 site_drawer_config 且结构可用 → 以它为准（管理员全权接管）；
- 没存 / 存量坏 JSON → 回落默认值：导航固定「首页 / 定价」（站内
  既有路由），渠道与文档行取旧「官网社区渠道」能力（community）的
  现值——生产里已配好的 TG 渠道与文档地址不因新抽屉上线而丢；
  渠道没有真实配置时就是空，不编造 @账号。

公开下发（public_info）只出 enabled 且链接合法的项：导航/底按钮
允许站内 / 路径，渠道只允许 http(s) 外链；底按钮链接为空串表示
「内置入口行为」（前端按登录态跳概览/登录页），关闭或标题为空
时 cta 为 None（前端不渲染）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from backend import config_schema
from backend.integrations import community as community_capability
from backend.integrations import store

logger = logging.getLogger(__name__)

KEY = "site_drawer_config"

#: 后台编辑器图标下拉的选项来源（与前端图标组件映射同一份名单）
ICON_OPTIONS: list[str] = list(config_schema.SITE_DRAWER_ICONS)

_DEFAULT_NAV: list[dict[str, Any]] = [
    {"icon": "home", "label": "首页", "url": "/",
     "external": False, "enabled": True},
    {"icon": "tag", "label": "定价", "url": "/pricing",
     "external": False, "enabled": True},
]

_DEFAULT_CTA: dict[str, Any] = {"label": "进入后台", "url": "", "enabled": True}


def default_config(db: Session) -> dict[str, Any]:
    """无抽屉配置时的默认整份结构：导航为站内既有路由，文档行与
    渠道回落旧 community 能力现值（无则空，绝不编造）。"""
    nav_items = [dict(item) for item in _DEFAULT_NAV]
    channels: list[dict[str, Any]] = []
    try:
        legacy = community_capability.public_info(db)
    except Exception:  # noqa: BLE001 - 默认值组装失败不许拖垮抽屉
        logger.warning("site_drawer legacy fallback failed", exc_info=True)
        legacy = {"docs_url": "", "channels": []}
    if legacy.get("docs_url"):
        nav_items.append({
            "icon": "book", "label": "文档", "url": legacy["docs_url"],
            "external": True, "enabled": True,
        })
    for ch in legacy.get("channels") or []:
        channels.append({
            "icon": ch.get("icon") or "message",
            "title": ch.get("title") or "",
            "subtitle": ch.get("subtitle") or "",
            "url": ch.get("url") or "",
            "enabled": True,
        })
    return {
        "nav_items": nav_items,
        "channels_title": config_schema.SITE_DRAWER_DEFAULT_CHANNELS_TITLE,
        "channels": channels,
        "cta": dict(_DEFAULT_CTA),
    }


def read_config(db: Session) -> dict[str, Any]:
    """读整份抽屉配置（管理端 GET 与公开下发共用）：存量行结构可用
    则以它为准，否则回落 default_config。直查 DB（与 community /
    branding 能力同口径），保存经 store.write_values 写时失效，
    不存在跨进程缓存延迟。"""
    raw = store.read_values(db, [KEY], {KEY: ""}).get(KEY) or ""
    if str(raw).strip():
        coerced = config_schema.coerce_site_drawer_config(raw, strict=False)
        if coerced is not None:
            return coerced
        logger.warning("site_drawer_config 存量值不可用，已回落默认")
    return default_config(db)


def public_info(db: Session) -> dict[str, Any]:
    """公开下发形状：只含 enabled 且链接合法的项；底按钮关闭或
    标题为空 → cta=None（前端整钮不渲染）。"""
    cfg = read_config(db)

    nav_items: list[dict[str, Any]] = []
    for item in cfg["nav_items"]:
        url = item["url"]
        if not item["enabled"] or not url:
            continue
        if not config_schema.site_drawer_url_ok(url, allow_internal=True):
            continue
        nav_items.append({
            "icon": item["icon"],
            "label": item["label"],
            "url": url,
            "external": bool(item["external"])
            or url.startswith(("http://", "https://")),
        })

    channels: list[dict[str, Any]] = []
    for ch in cfg["channels"]:
        url = ch["url"]
        if not ch["enabled"] or not url:
            continue
        if not url.startswith(("https://", "http://")):
            continue
        channels.append({
            "icon": ch["icon"],
            "title": ch["title"],
            "subtitle": ch["subtitle"],
            "url": url,
        })

    cta: dict[str, Any] | None = None
    raw_cta = cfg["cta"]
    if raw_cta["enabled"] and raw_cta["label"]:
        url = raw_cta["url"]
        if url and not config_schema.site_drawer_url_ok(
                url, allow_internal=True):
            url = ""
        cta = {"label": raw_cta["label"], "url": url}

    return {
        "nav_items": nav_items,
        "channels_title": cfg["channels_title"],
        "channels": channels,
        "cta": cta,
    }
