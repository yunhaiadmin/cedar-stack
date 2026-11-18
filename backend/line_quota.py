# -*- coding: utf-8 -*-
"""播放线路流量配额：判定侧（按线路计，2026-11-19 用户纠正粒度）。

三个维度分清（用户 2026-11-19 二次纠正）：服（``ServerRealm``）是
服务器本身；**大类是线路自己的分类**（``ServerRealmLine.category``，
如「国际线路」「国内线路」），每个大类下面挂多条线路（如一条
「主线路」＋一条「副线路」）。
配额、用量、重置都按最底层的**线路**计——配额与统计起点在线路行
上（``server_realm_lines.quota_bytes`` / ``period_started_at``），
用量在计量旁路日账 ``line_traffic_usage_daily``（记录侧见
``emby_server.traffic_meter``，与用户流量账同一计量口径：中继腿 +
本机文件腿的真实出字节；302 直链字节不过本机，不计）。

归属键 = **入口 host**：计量点在请求入口把「用户是从哪个对外地址
进来的」记下来（可信反代采信 X-Forwarded-Host、否则 Host 头，
见 emby_server.public_url.request_entry_host；两跳 nginx 均透传
原始 Host，证据在案），落库时按 host 匹配 url 的 host 相同的
线路。匹配不到任何线路的字节只记用户账、不记线路账——宁可漏计，
不许记错线。

周期口径：用户流量周期是按人锚点日滚动的（traffic_quota.
period_bounds），没有全局月界可对齐——线路配额按**累计总量**
算，管理端逐线手动重置。已用 = 日账总和 − 重置偏移：用量按日
落账、拆不开同一天内的重置前后，所以重置时把当刻总和快照进
线路行的 ``usage_offset_bytes`` 抵扣，重置当刻已用精确归零；
``period_started_at`` 只记这次重置的时间供展示「自何时起计」。
历史日行不删，对账可查。

分流口径（与现行架构对齐，不硬拦）：

- 已连上某条线路的客户端（外部播放器粘贴的地址）继续播，EA 侧
  **不因配额拒绝播放**——绝不因配额断播；
- 服务端能控制的「分配面」只有下发面：账号卡给每条线路带
  ``quota_exhausted`` 标记，用户端据此显示「暂停服务」且不可选，
  网页播放器选线/校验时把用尽线路排除、优先落到未用尽线路；
- **全线用尽回落**：当某用户能看到的线路全部用尽时，标记整体
  失效（``apply_card_flags`` 清空），一切照现行口径走——配额是
  分流手段，不是停播开关。

用量读走 60 秒 TTL 快照（与 realms 快照同一口径）：账号卡是
高频读路径，不许每次现算 SUM；重置/改配额后显式失效，同进程
立即生效，跨进程最长 60 秒收敛。host→线路归属表同 TTL。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

_USAGE_CACHE = store.TtlSnapshotCache()
_HOSTMAP_CACHE = store.TtlSnapshotCache()
_HOSTMAP_KEY = "hostmap"

#: 线路缺省大类：线路未标注、套餐未声明时都按它算（现行在售套餐
#: 与存量线路均为国际线路，缺省不得改变现状）。大类名本身不写死
#: 枚举——管理员可自建任意大类，这里只是「没写时算哪个」的缺省。
DEFAULT_CATEGORY = "国际线路"


# ==================== 线路大类 ====================

def category_of(line) -> str:
    """一条线路的大类名（空/脏值回退缺省大类）。"""
    value = (getattr(line, "category", None) or "").strip()
    return value or DEFAULT_CATEGORY


def normalize_category(value) -> str:
    """写路径的大类名收敛：去空白、空 = 缺省大类、超长拒绝。"""
    text = (value or "").strip() if isinstance(value, str) else ""
    if not text:
        return DEFAULT_CATEGORY
    if len(text) > 80:
        raise ValueError("大类名过长（≤80 字符）")
    return text


def _category_of_plan(db: Session, plan_id) -> Optional[str]:
    """订阅行指向套餐的大类（套餐已删/无从判定 → None）。"""
    if plan_id is None:
        return None
    plan = (db.query(models.SubscriptionPlan)
            .filter(models.SubscriptionPlan.id == plan_id).first())
    if plan is None:
        return None
    from backend import entitlements

    return entitlements.plan_line_category(plan)


def user_line_category(db: Session, user, realm_id: Optional[int] = None
                       ) -> Optional[str]:
    """该用户下发线路时应按哪个大类过滤；None = 不过滤、全显示。

    口径（用户 2026-11-19 亲令：大类与订阅打通）：
    1. 本服有效订阅 → 其套餐的大类（套餐未声明按缺省大类）；
    2. 团队播放资格 → 团队套餐的大类——团队套餐目前**没有**大类
       口径（``Team.plan_line`` 是 global/premium 线路等级，不是
       大类），故回退订阅口径：任意一份有效订阅套餐的大类；
    3. 两样都没有（纯浏览）→ None 不过滤（播放门禁另有拦截）。
    """
    from backend import subscriptions
    from backend import teams as teams_service

    user_id = user if isinstance(user, int) else user.id
    sub = subscriptions.active_subscription(db, user_id, realm_id)
    category = _category_of_plan(db, sub.plan_id if sub else None)
    if category:
        return category
    if teams_service.play_eligible_team(db, user_id) is not None:
        # 团队套餐无大类口径 → 回退订阅口径（任意有效订阅）
        sub = subscriptions.active_subscription(db, user_id, None)
        category = _category_of_plan(db, sub.plan_id if sub else None)
        if category:
            return category
    return None


# ==================== host 归属键 ====================

def _host_port_key(hostname: str, port: Optional[int], scheme: str = "") -> str:
    """host 归属键：小写主机名 + 非默认端口（默认端口不进键）。"""
    host = (hostname or "").strip().lower()
    if not host:
        return ""
    if port and not ((scheme == "https" and port == 443)
                      or (scheme == "http" and port == 80)):
        return f"{host}:{port}"
    return host


def host_key_of_url(url: str) -> str:
    """一条线路 url 的归属键（从 url 解析 host）；解析不出返回空串。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
        return _host_port_key(parts.hostname or "", parts.port,
                              (parts.scheme or "").lower())
    except ValueError:
        return ""


def normalize_entry_host(raw: str) -> str:
    """请求入口 host 归一成归属键（与 host_key_of_url 同口径）。

    入口 host 来自请求头（可信 X-Forwarded-Host 或 Host），可能带
    端口、带逗号多跳、带大小写。默认端口（80/443）一律剥掉——线路
    url 侧默认端口同样不进键，两边对齐。带路径/userinfo 等脏形态
    直接判空（归属宁空勿错）。
    """
    host = (raw or "").split(",")[0].strip().lower()
    if not host or len(host) > 255:
        return ""
    if any(ch in host for ch in " \t\r\n/@\\?#"):
        return ""
    if host.startswith("["):  # IPv6 字面量 [::1]:8001
        end = host.find("]")
        if end == -1:
            return ""
        name, rest = host[:end + 1], host[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
            return name if port in (80, 443) else f"{name}:{port}"
        return name
    if ":" in host:
        name, _, port_s = host.rpartition(":")
        if port_s.isdigit():
            port = int(port_s)
            return name if port in (80, 443) else f"{name}:{port}"
        return ""  # 冒号后不是端口号 = 脏形态
    return host


# ==================== 配额判定（按线路） ====================

def quota_bytes_of(line) -> int:
    """线路的配额字节数；0（或脏值）= 不限额。"""
    try:
        value = int(getattr(line, "quota_bytes", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _sum_all_bytes(db: Session, line_id: int) -> int:
    """某线路日账总和（relay+local，不走缓存——重置快照要用当刻真值）。"""
    total = db.query(
        func.coalesce(func.sum(
            em.LineTrafficUsageDaily.relay_bytes
            + em.LineTrafficUsageDaily.local_bytes), 0)
    ).filter(
        em.LineTrafficUsageDaily.line_id == int(line_id)).scalar()
    return int(total or 0)


def _total_bytes(db: Session, line_id: int) -> int:
    """日账总和（TTL 缓存：账号卡等高频读路径不许每次现算 SUM）。"""
    key = int(line_id)
    hit, value = _USAGE_CACHE.lookup(key)
    if hit:
        return int(value or 0)
    total = _sum_all_bytes(db, key)
    _USAGE_CACHE.put(key, total)
    return total


def used_bytes(db: Session, line) -> int:
    """某线路当前已用字节 = 日账总和 − 重置偏移（下限 0）。"""
    offset = 0
    try:
        offset = int(getattr(line, "usage_offset_bytes", 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    return max(0, _total_bytes(db, line.id) - offset)


def quota_view(db: Session, line) -> dict:
    """一条线路的配额全貌（管理端与账号卡共用同一口径）。"""
    quota = quota_bytes_of(line)
    used = used_bytes(db, line)
    started = getattr(line, "period_started_at", None)
    return {
        "quota_bytes": quota,
        "used_bytes": used,
        "remaining_bytes": (max(0, quota - used) if quota > 0 else None),
        "exhausted": bool(quota > 0 and used >= quota),
        "period_started_at": started.isoformat() if started else None,
    }


def is_exhausted(db: Session, line) -> bool:
    return quota_view(db, line)["exhausted"]


def line_view(db: Session, line, *, is_primary: bool) -> dict:
    """一条线路的完整序列化（管理端服卡线路列表用）。"""
    return {
        "id": line.id,
        "realm_id": line.realm_id,
        "category": category_of(line),
        "name": line.name or "",
        "url": line.url or "",
        "host": host_key_of_url(line.url or ""),
        "is_primary": bool(is_primary),
        "is_active": bool(line.is_active),
        "sort_order": line.sort_order or 0,
        "traffic": quota_view(db, line),
    }


def card_lines(db: Session, realm, *, category: Optional[str] = None
               ) -> list[dict]:
    """账号卡里某个服下发的线路清单（按序、主线路在前）。

    每条带 category（所属大类）与 quota_exhausted 初值（单线口径）；
    全线用尽回落由 ``apply_card_flags`` 在整卡组装完后统一修正。

    ``category`` 非空时按大类过滤（订阅打通口径，见
    ``user_line_category``）：只下发该大类的线路；过滤后一条不剩
    时返回空清单，用户端按现行口径回退 base_url 单行——回退行
    本身不受大类过滤影响。
    """
    from backend import realms

    lines = realms.ensure_realm_lines(db, realm)
    active = [line for line in lines if line.is_active]
    if category:
        active = [line for line in active
                  if category_of(line) == category]
    out = []
    for idx, line in enumerate(active):
        out.append({
            "id": line.id,
            "category": category_of(line),
            "name": line.name or "",
            "url": (line.url or "").strip().rstrip("/"),
            "is_primary": idx == 0,
            "quota_exhausted": is_exhausted(db, line),
        })
    return out


def apply_card_flags(cards: list[dict]) -> dict[int, bool]:
    """给账号卡各服的线路清单做「全线用尽回落」修正。

    ``cards`` 是 ``_user_realm_cards`` 的产物（每卡带 ``lines``，
    每线带 quota_exhausted 初值）。候选 = 全部卡里有地址的线路；
    候选全部用尽时清空全部标记（回落现行口径，不断播）；没有候选
    时同样全清。每张卡的 ``quota_exhausted`` 同步成「这张卡的
    主线路」的回落後标记（顶层卡与旧消费方读它）。返回
    line_id -> 是否用尽（只含候选）。
    """
    flags: dict[int, bool] = {}
    for card in cards:
        for line in card.get("lines") or []:
            if (line.get("url") or "").strip():
                flags[int(line["id"])] = bool(line.get("quota_exhausted"))
    if flags and all(flags.values()):
        flags = {lid: False for lid in flags}
    for card in cards:
        primary_flag = False
        for line in card.get("lines") or []:
            line["quota_exhausted"] = flags.get(int(line["id"]), False)
            if line.get("is_primary"):
                primary_flag = line["quota_exhausted"]
        card["quota_exhausted"] = primary_flag
    return flags


def reset_period(db: Session, line: models.ServerRealmLine) -> models.ServerRealmLine:
    """重置某条线路的用量：当刻日账总和快照为偏移，已用归零重新计。

    只动这一条线，同服其它线路的账一字不碰。历史日行不删（对账
    可查）；period_started_at 只记重置时刻供展示。总和绕开 TTL
    缓存读当刻真值，否则快照会把重置前最后一段没进缓存的用量漏抵。
    """
    line.usage_offset_bytes = _sum_all_bytes(db, line.id)
    line.period_started_at = datetime.now()
    db.commit()
    db.refresh(line)
    invalidate(line.id)
    logger.info("线路流量用量已重置：线路 #%s（%s，服 #%s），偏移快照 %s 字节，起计 %s",
                line.id, line.name, line.realm_id,
                line.usage_offset_bytes, line.period_started_at)
    return line


def invalidate(line_id: Optional[int] = None) -> None:
    """用量/归属缓存失效（写路径提交后调用：同进程立即生效）。"""
    _USAGE_CACHE.invalidate()
    _HOSTMAP_CACHE.invalidate()


# ==================== host → 线路归属 ====================

def _build_hostmap(db: Session) -> dict[str, int]:
    """全站启用线路的 host 归属表：归属键 -> line_id。

    同一 host 被多条线路占用时先到先得（按服序、线路序、id），
    后到的不抢——归属必须稳定，管理员把地址改开后自然让位。
    构建前先给每个启用服补齐自动主线路（迁移口径：realm.url
    生成的那条），保证刚部署完、还没人打开过账号卡/管理页时
    计量也归属得上。
    """
    from backend import realms

    mapping: dict[str, int] = {}
    rows = (db.query(models.ServerRealm)
            .filter(models.ServerRealm.is_active.is_(True))
            .order_by(models.ServerRealm.sort_order, models.ServerRealm.id)
            .all())
    for realm in rows:
        for line in realms.ensure_realm_lines(db, realm):
            if not line.is_active:
                continue
            key = host_key_of_url(line.url or "")
            if key and key not in mapping:
                mapping[key] = int(line.id)
    return mapping


def hostmap(db: Session) -> dict[str, int]:
    hit, value = _HOSTMAP_CACHE.lookup(_HOSTMAP_KEY)
    if hit:
        return dict(value or {})
    mapping = _build_hostmap(db)
    _HOSTMAP_CACHE.put(_HOSTMAP_KEY, mapping)
    return dict(mapping)


def match_line_id(db: Session, entry_host: str) -> Optional[int]:
    """按请求入口 host 找归属线路 id；匹配不到返回 None（不记线账）。"""
    key = normalize_entry_host(entry_host)
    if not key:
        return None
    return hostmap(db).get(key)


__all__ = [
    "DEFAULT_CATEGORY",
    "apply_card_flags",
    "card_lines",
    "category_of",
    "host_key_of_url",
    "hostmap",
    "invalidate",
    "is_exhausted",
    "line_view",
    "match_line_id",
    "normalize_category",
    "normalize_entry_host",
    "quota_bytes_of",
    "quota_view",
    "reset_period",
    "used_bytes",
    "user_line_category",
]
