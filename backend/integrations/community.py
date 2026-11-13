"""能力：官网社区渠道

官网首页（营销面）移动端菜单面板里的两块可配内容：

- **文档外链**：导航行「文档」的跳转地址；
- **社区渠道**：面板中部 2×2 卡片网格，最多 4 格，每格一个标题、一个副标
  （通常是 @账号）、一个链接、一个图标种类，外加一个开关。

全部由管理员在后台自己填，**不内置任何渠道地址**（只提供能力、不写死内容）。
前端只渲染配好的项：开关关着或链接没填的格子不下发，四格全空时整个小节
在面板里就不出现；文档地址没填时文档行不出现。

链接只接受 ``http(s)://``（与站点 Logo 同口径）：其它协议（``javascript:``、
``data:`` 之类）一律视为未配，避免把危险值带进页面。

没有 ``test()``：没有可测的外部依赖，后台不显示「测试」按钮。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.integrations import store

CHANNEL_SLOTS = 4

# 图标种类：前端按这个名字映射到线性图标组件，未知值回落到 message
ICON_OPTIONS = ["bell", "users", "bot", "message", "send", "globe"]
_ICON_FALLBACK = "message"
# 每格的默认图标（纯占位形态，管理员可改）：通知 / 讨论 / 机器人 / 机器人
_SLOT_ICON_DEFAULTS = ["bell", "users", "bot", "bot"]


def _channel_fields(slot: int) -> list[dict]:
    prefix = f"community_ch{slot}"
    return [
        {"key": f"{prefix}_enabled", "label": f"渠道 {slot} · 显示", "type": "bool",
         "default": False, "hint": "关掉或不填链接时，这一格不在官网菜单里出现"},
        {"key": f"{prefix}_title", "label": f"渠道 {slot} · 标题", "type": "str",
         "default": "", "placeholder": "如：通知频道"},
        {"key": f"{prefix}_subtitle", "label": f"渠道 {slot} · 副标", "type": "str",
         "default": "", "placeholder": "如：@your_channel", "hint": "通常填 @账号，可留空"},
        {"key": f"{prefix}_url", "label": f"渠道 {slot} · 链接", "type": "str",
         "default": "", "placeholder": "https://t.me/your_channel",
         "hint": "只接受 http(s):// 链接，其它协议视为未配"},
        {"key": f"{prefix}_icon", "label": f"渠道 {slot} · 图标", "type": "select",
         "options": ICON_OPTIONS, "default": _SLOT_ICON_DEFAULTS[slot - 1],
         "hint": "bell 铃铛 / users 人群 / bot 机器人 / message 消息 / send 纸飞机 / globe 地球"},
    ]


SPEC = {
    "title": "官网社区渠道",
    "desc": "官网首页菜单面板里的文档外链与社区渠道卡片；未配的项不显示，全部留空时面板只剩固定导航。",
    "group": "站点与品牌",
    "docs_hint": "链接只接受 http(s)://；渠道格子需打开「显示」并填链接才会出现在官网菜单里。",
    "fields": [
        {"key": "landing_docs_url", "label": "文档外链地址", "type": "str", "default": "",
         "placeholder": "https://docs.example.com",
         "hint": "官网菜单「文档」行的跳转地址；留空则不显示文档行，只接受 http(s)://"},
        *[field for slot in range(1, CHANNEL_SLOTS + 1) for field in _channel_fields(slot)],
    ],
}

_FIELD_KEYS = [f["key"] for f in SPEC["fields"]]
_FIELD_DEFAULTS = {f["key"]: (f.get("default") if f.get("default") is not None else "") for f in SPEC["fields"]}


def read(db: Session) -> dict:
    """一次查询取回全部配置"""
    return store.read_values(db, _FIELD_KEYS, _FIELD_DEFAULTS)


def _truthy(raw) -> bool:
    return str(raw or "").strip().lower() in ("true", "1", "on", "yes")


def _safe_url(raw) -> str:
    """校验过的外链地址（非 http(s) 一律回落到空 = 未配）"""
    text = str(raw or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def public_info(db: Session) -> dict:
    """给前端的公开信息：只下发真正要显示的项，未配/关闭的一律不在返回里"""
    values = read(db)
    channels: list[dict] = []
    for slot in range(1, CHANNEL_SLOTS + 1):
        prefix = f"community_ch{slot}"
        if not _truthy(values.get(f"{prefix}_enabled")):
            continue
        url = _safe_url(values.get(f"{prefix}_url"))
        if not url:
            continue
        icon = str(values.get(f"{prefix}_icon") or "").strip()
        channels.append({
            "title": str(values.get(f"{prefix}_title") or "").strip(),
            "subtitle": str(values.get(f"{prefix}_subtitle") or "").strip(),
            "url": url,
            "icon": icon if icon in ICON_OPTIONS else _ICON_FALLBACK,
        })
    return {
        "docs_url": _safe_url(values.get("landing_docs_url")),
        "channels": channels,
    }


def is_configured(values: dict) -> bool:
    """配过任意一项（文档地址或任一渠道格的链接）就算配过"""
    if _safe_url(values.get("landing_docs_url")):
        return True
    return any(
        _safe_url(values.get(f"community_ch{slot}_url"))
        for slot in range(1, CHANNEL_SLOTS + 1)
    )
