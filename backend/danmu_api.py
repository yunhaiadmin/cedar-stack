"""弹幕 API 能力（第三方播放器，2026-11-18）：专职 Token、线路与下发。

与站内自发弹幕（backend/danmaku.py，网页播放器内发弹幕）是两个
功能：本模块管的是「给播放器填的弹幕 API 地址」——用户在概览页
生成专属 Token、选线路、复制形如
``https://<host>/api/v1/danmu/<token>/`` 的地址填进播放器，
播放器按 Danmu 插件约定（见 emby_server.compat_routes G7 注释：
Hills / Streama 系客户端，bilibili 风格 XML）在该地址后拼条目
Id 取弹幕。契约要点：

- Token：每用户恰一个（danmu_api_tokens.user_id 唯一），32 位
  十六进制，有效期一年、不自动续期，删除即失效；生成无强因子
  前置。明文落库只为服务端能为本人重拼地址——除本人地址接口
  外，任何响应、日志、审计都不出现 token 明文。
- 线路：SystemConfig 家族 danmu_api 的 danmu_api_lines（JSON
  数组 [{id,name,note,host,enabled}]），空/非法 = 代码默认两条；
  线路只换 host，路径与 token 不变。enabled 缺省视为启用
  （兼容旧配置）；enabled=false 的线路仍在列表里下发（用户端
  显示「暂停服务」不可选），但不为它拼地址（/address 回 409）。
  默认两条的 host 是施工时实测
  能路由到后端 /api 的前台域名：cdn.aetrix.love（CF 代理的
  对外客户端地址）与 www.aetrix.love（站点主域名，同走 CF）；
  emby.aetrix.love 也可达，但那是源站直连地址，不作用户面默认。
- 门槛（公开下发口服务端真校验）：token 有效 + 号主有有效
  套餐（个人有效订阅按有效账号解析，或团队播放资格）+ 有剩余
  流量（traffic_quota.usage_summary 的 exhausted 口径，未配月
  额度的套餐不算耗尽，与 traffic_gate 不拦同口径）。不达标按
  协议回空文档（播放器视为「没弹幕」）；弹幕请求不扣流量——
  本模块不写任何计量表（校验点：serve_item_danmaku 全程只读）。
- 内容来源：站内弹幕存量（danmaku_comments，按条目匹配）；
  外部弹幕源只留适配接口（fetch_external_danmaku），开关
  danmu_api_external_enabled 默认关，本期不接真外部源。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from backend import models as core
from backend import sub_accounts, subscriptions, traffic_quota
from backend.danmaku import list_danmaku
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

KEY_LINES = "danmu_api_lines"
KEY_EXTERNAL_ENABLED = "danmu_api_external_enabled"

TOKEN_VALIDITY_DAYS = 365
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_LINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

#: 默认线路（配置为空/非法时的回退；host 均为实测可达后端的前台域名）
DEFAULT_LINES: list[dict] = [
    {"id": "intl", "name": "国际方向",
     "note": "CloudFlare CDN 加速的优质海外线路",
     "host": "cdn.aetrix.love", "enabled": True},
    {"id": "intl_backup", "name": "国际方向-备用",
     "note": "国际主线路不稳定时可使用",
     "host": "www.aetrix.love", "enabled": True},
]

_MAX_DELIVERY_ITEMS = 5000
_EMPTY_XML = '<?xml version="1.0" encoding="utf-8"?><i></i>'


# ------------------------------------------------------------------ Token


def _valid_row(db: Session, user_id: int) -> core.DanmuApiToken | None:
    """用户当前有效的 token 行；过期行视为不存在（到期需重新生成）。"""
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None or row.expires_at <= datetime.now():
        return None
    return row


def token_status(db: Session, user_id: int) -> dict:
    """状态口径：只有未过期的 token 才算 exists；不回明文。"""
    row = _valid_row(db, user_id)
    if row is None:
        return {"exists": False, "expires_at": None}
    return {"exists": True, "expires_at": row.expires_at.isoformat()}


def generate_token(db: Session, user_id: int) -> core.DanmuApiToken:
    """生成 token：已有有效 token 直接回现有行（不重复生成）；
    只有过期行时替换为新值（一年期从生成时刻起算）。自带 commit。"""
    row = _valid_row(db, user_id)
    if row is not None:
        return row
    now = datetime.now()
    expires = now + timedelta(days=TOKEN_VALIDITY_DAYS)
    while True:
        value = secrets.token_hex(16)
        clash = (db.query(core.DanmuApiToken)
                 .filter(core.DanmuApiToken.token == value).first())
        if clash is None:
            break
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None:
        row = core.DanmuApiToken(user_id=user_id, token=value,
                                 created_at=now, expires_at=expires)
        db.add(row)
    else:  # 过期行原地换新值：一用户一行的不变量不破
        row.token = value
        row.created_at = now
        row.expires_at = expires
    db.commit()
    db.refresh(row)
    return row


def delete_token(db: Session, user_id: int) -> bool:
    """删除即失效（硬删行）；无行返回 False。自带 commit。"""
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def find_token_row(db: Session, token: str) -> core.DanmuApiToken | None:
    """按 token 值寻址（公开下发口）：形态非法/不存在/已过期一律 None。"""
    if not token or not _TOKEN_RE.match(token):
        return None
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.token == token).first())
    if row is None or row.expires_at <= datetime.now():
        return None
    return row


# ------------------------------------------------------------------ 线路


def validate_lines(raw) -> list[dict]:
    """线路表形状校验（管理端保存与读取共用）：非法抛 ValueError。

    每条必须是 {id,name,note,host}：id 为小写 slug 且全表唯一，
    host 为裸域名（不带协议/路径/端口），name 非空。可选字段
    enabled：缺省视为 True（旧配置无此字段照常启用）；出现时
    必须是布尔值。归一化输出恒带 enabled。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("线路表必须是非空数组")
    lines: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"第 {i + 1} 条线路不是对象")
        line_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        note = str(entry.get("note") or "").strip()
        host = str(entry.get("host") or "").strip().lower()
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"线路 {line_id} 的 enabled 必须是布尔值")
        if not _LINE_ID_RE.match(line_id):
            raise ValueError(f"第 {i + 1} 条线路 id 不合法：{line_id!r}")
        if line_id in seen:
            raise ValueError(f"线路 id 重复：{line_id}")
        if not name:
            raise ValueError(f"线路 {line_id} 缺少名称")
        if not _HOST_RE.match(host):
            raise ValueError(f"线路 {line_id} 的 host 不是裸域名：{host!r}")
        seen.add(line_id)
        lines.append({"id": line_id, "name": name,
                      "note": note, "host": host, "enabled": enabled})
    return lines


def get_lines(db: Session) -> list[dict]:
    """当前生效线路：配置合法用配置，空/非法回退默认两条（读侧不抛错）。"""
    raw = store.get_value(db, KEY_LINES, "")
    if raw and str(raw).strip():
        try:
            return validate_lines(json.loads(raw))
        except (ValueError, TypeError) as exc:
            logger.warning("danmu_api_lines 配置非法，回退默认线路：%s", exc)
    return [dict(line) for line in DEFAULT_LINES]


def build_address(db: Session, user_id: int, line_id: str) -> str | None:
    """为本人拼完整弹幕地址；线路不存在、已暂停或无有效 token 时返回 None。

    暂停线路永不拼地址是核心不变量（端点层另对暂停回 409 区分
    「线路暂停」与「未生成 token」，见 api/danmu_api.py）。
    """
    line = next((ln for ln in get_lines(db) if ln["id"] == line_id), None)
    if line is None or not line["enabled"]:
        return None
    row = _valid_row(db, user_id)
    if row is None:
        return None
    return f"https://{line['host']}/api/v1/danmu/{row.token}/"


# ------------------------------------------------------------------ 门槛


def check_access(db: Session, owner_id: int) -> tuple[bool, str]:
    """使用门槛（服务端真校验）：有效套餐 + 有剩余流量。

    有效套餐：个人有效订阅（家庭子账号按主账号解析，与全站
    有效账号口径一致）或团队播放资格（teams.play_eligible_team，
    与 usage_summary/can_play 同一口）。剩余流量：只认
    usage_summary 的 exhausted（套餐配了月额度且有效已用 >=
    额度）；未配月额度（enabled=False）不算耗尽、不拦（与
    traffic_gate 对未启用额度用户的口径一致）。返回 (放行, 原因码)。
    """
    effective_id = sub_accounts.effective_user_id(db, owner_id) or owner_id
    has_plan = (subscriptions.active_subscription(db, effective_id)
                is not None)
    if not has_plan:
        from backend import teams as teams_service  # 懒引：防循环依赖

        has_plan = teams_service.play_eligible_team(db, owner_id) is not None
    if not has_plan:
        return False, "no_active_plan"
    summary = traffic_quota.usage_summary(db, owner_id)
    if summary.get("exhausted"):
        return False, "traffic_exhausted"
    return True, ""


# ------------------------------------------------------------------ 下发


def external_enabled(db: Session) -> bool:
    """外部弹幕源适配开关：无行 = 关；只认 "true"（与 danmaku 口径一致）。"""
    return store.get_value(db, KEY_EXTERNAL_ENABLED, "false") == "true"


def fetch_external_danmaku(db: Session, item_id: int) -> list[dict]:
    """外部弹幕源适配接口：返回 [{position_seconds, text}]。

    本期不接真外部源：开关关或未接源时一律空。后续接源时在此
    实现（按条目向外部源取弹幕并归一到同一形态），下发口不变。
    """
    if not external_enabled(db):
        return []
    return []


def _resolve_item(db: Session, item_ref: str) -> em.MediaItem | None:
    """条目寻址：Emby Id（guid）优先；纯数字时兼认内部 id（客户端差异兜底）。"""
    item = (db.query(em.MediaItem)
            .filter(em.MediaItem.guid == item_ref).first())
    if item is None and item_ref.isdigit():
        item = (db.query(em.MediaItem)
                .filter(em.MediaItem.id == int(item_ref)).first())
    return item


def render_item_danmaku_xml(db: Session, item: em.MediaItem,
                            owner_id: int) -> str:
    """按条目拼 bilibili 风格弹幕 XML：站内弹幕（时间轴升序）+ 外部源合并。

    <d> 的 p 属性沿用 Danmu 插件约定：时间(秒),模式(1=滚动),
    字号(25),颜色(十进制白),发送时间戳,弹幕池,发送者,行 id。
    """
    rows = list_danmaku(db, item.id, 0.0, float(7 * 24 * 3600),
                        _MAX_DELIVERY_ITEMS, current_user_id=owner_id)
    merged = [(float(r["position_seconds"]), str(r["text"])) for r in rows]
    for ext in fetch_external_danmaku(db, item.id):
        try:
            merged.append((float(ext["position_seconds"]),
                           str(ext["text"])))
        except (KeyError, TypeError, ValueError):
            continue
    merged.sort(key=lambda pair: pair[0])
    if not merged:
        return _EMPTY_XML
    parts = ['<?xml version="1.0" encoding="utf-8"?><i>']
    for idx, (position, text) in enumerate(merged):
        pos = f"{position:.3f}".rstrip("0").rstrip(".")
        parts.append(
            f'<d p="{pos},1,25,16777215,0,0,danmu,{idx}">'
            f"{xml_escape(text)}</d>")
    parts.append("</i>")
    return "".join(parts)


def empty_xml() -> str:
    return _EMPTY_XML
