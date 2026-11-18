# -*- coding: utf-8 -*-
"""播放线路（服）流量配额：判定侧。

用户 2026-11-19 点选定版：每条播放线路可设流量配额，系统按配额
给用户分流。本模块只管「算」——配额与统计起点在服行上
（``server_realms.traffic_quota_bytes`` / ``traffic_period_started_at``），
用量在计量旁路日账 ``realm_traffic_usage_daily``（记录侧见
``emby_server.traffic_meter``，与用户流量账同一计量口径：中继腿 +
本机文件腿的真实出字节；302 直链字节不过本机，不计）。

周期口径：用户流量周期是按人锚点日滚动的（traffic_quota.
period_bounds），没有全局月界可对齐——线路配额按**累计总量**
算，管理端手动重置。已用 = 日账总和（relay+local）− 重置偏移：
用量按日落账、拆不开同一天内的重置前后，所以重置时把当刻总和
快照进服行的 ``traffic_usage_offset_bytes`` 抵扣，重置当刻已用
精确归零；``traffic_period_started_at`` 只记这次重置的时间供
展示「自何时起计」。历史日行不删，对账可查。

分流口径（与现行架构对齐，不硬拦）：

- 服与服之间内容/订阅不互通，EA 侧**不因配额拒绝播放**——已连上
  某条线路的客户端（外部播放器粘贴的地址）继续播，绝不因配额断播；
- 服务端能控制的「分配面」只有下发面：账号卡给每条线路带
  ``quota_exhausted`` 标记，用户端据此显示「暂停服务」且不可选，
  网页播放器选线/校验时把用尽线路排除、优先落到未用尽线路；
- **全线用尽回落**：当某用户能看到的线路全部用尽时，标记整体
  失效（``apply_card_flags`` 清空），一切照现行口径走——配额是
  分流手段，不是停播开关。

用量读走 60 秒 TTL 快照（与 realms 快照同一口径）：账号卡是
高频读路径，不许每次现算 SUM；重置/改配额后显式失效，同进程
立即生效，跨进程最长 60 秒收敛。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

_USAGE_CACHE = store.TtlSnapshotCache()


def quota_bytes_of(realm) -> int:
    """服的配额字节数；0（或脏值）= 不限额。"""
    try:
        value = int(getattr(realm, "traffic_quota_bytes", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _sum_all_bytes(db: Session, realm_id: int) -> int:
    """某服日账总和（relay+local，不走缓存——重置快照要用当刻真值）。"""
    total = db.query(
        func.coalesce(func.sum(
            em.RealmTrafficUsageDaily.relay_bytes
            + em.RealmTrafficUsageDaily.local_bytes), 0)
    ).filter(
        em.RealmTrafficUsageDaily.realm_id == int(realm_id)).scalar()
    return int(total or 0)


def _total_bytes(db: Session, realm_id: int) -> int:
    """日账总和（TTL 缓存：账号卡等高频读路径不许每次现算 SUM）。"""
    key = int(realm_id)
    hit, value = _USAGE_CACHE.lookup(key)
    if hit:
        return int(value or 0)
    total = _sum_all_bytes(db, key)
    _USAGE_CACHE.put(key, total)
    return total


def used_bytes(db: Session, realm) -> int:
    """某服当前已用字节 = 日账总和 − 重置偏移（下限 0）。"""
    offset = 0
    try:
        offset = int(getattr(realm, "traffic_usage_offset_bytes", 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    return max(0, _total_bytes(db, realm.id) - offset)


def quota_view(db: Session, realm) -> dict:
    """一个服的配额全貌（管理端与账号卡共用同一口径）。"""
    quota = quota_bytes_of(realm)
    used = used_bytes(db, realm)
    started = getattr(realm, "traffic_period_started_at", None)
    return {
        "quota_bytes": quota,
        "used_bytes": used,
        "remaining_bytes": (max(0, quota - used) if quota > 0 else None),
        "exhausted": bool(quota > 0 and used >= quota),
        "period_started_at": started.isoformat() if started else None,
    }


def is_exhausted(db: Session, realm) -> bool:
    return quota_view(db, realm)["exhausted"]


def apply_card_flags(cards: list[dict]) -> dict[int, bool]:
    """给账号卡的服卡片清单算 quota_exhausted（带全线用尽回落）。

    ``cards`` 是 ``_user_realm_cards`` 的产物（已带 quota_exhausted
    初值）。候选 = 有地址可连的卡（base_url 非空）；候选全部用尽
    时清空全部标记（回落现行口径，不断播）；没有候选时同样全清。
    返回 realm_id -> 是否用尽（只含候选）。
    """
    candidates = [c for c in cards if (c.get("base_url") or "").strip()]
    flags = {int(c["id"]): bool(c.get("quota_exhausted")) for c in candidates}
    if flags and all(flags.values()):
        flags = {rid: False for rid in flags}
    for card in cards:
        card["quota_exhausted"] = flags.get(int(card["id"]), False)
    return flags


def reset_period(db: Session, realm: models.ServerRealm) -> models.ServerRealm:
    """重置用量：当刻日账总和快照为偏移，已用归零重新计。

    历史日行不删（对账可查）；traffic_period_started_at 只记重置
    时刻供展示。总和绕开 TTL 缓存读当刻真值，否则快照会把重置前
    最后一段没进缓存的用量漏抵。
    """
    realm.traffic_usage_offset_bytes = _sum_all_bytes(db, realm.id)
    realm.traffic_period_started_at = datetime.now()
    db.commit()
    db.refresh(realm)
    invalidate(realm.id)
    logger.info("线路流量用量已重置：服 #%s（%s），偏移快照 %s 字节，起计 %s",
                realm.id, realm.name, realm.traffic_usage_offset_bytes,
                realm.traffic_period_started_at)
    return realm


def invalidate(realm_id: Optional[int] = None) -> None:
    """用量缓存失效（写路径提交后调用：同进程立即生效）。"""
    _USAGE_CACHE.invalidate()


__all__ = [
    "apply_card_flags",
    "invalidate",
    "is_exhausted",
    "quota_bytes_of",
    "quota_view",
    "reset_period",
    "used_bytes",
]
