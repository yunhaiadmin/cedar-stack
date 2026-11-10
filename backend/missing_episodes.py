# -*- coding: utf-8 -*-
"""缺集（补缺集求片）共享口径：detail JSON 结构与大白话文案。

缺集明细 detail 的规范形态是 ``{季号: [缺的集号, ...]}``（季号
为正整数、集号升序去重）。落库时序列化成 JSON（键变字符串）；
工人台账（emby_missing_episode_records.missing_detail）、求片行
快照（movie_requests.request_detail 里的 ``missing`` 字段）与
管理清单共用同一份结构，不许各写各的。

求片行 request_detail 的完整结构（补缺集类求片专用）：

    {"series_item_id": 123, "missing": {"1": [2, 5]},
     "missing_count": 2, "checked_at": "2026-11-10T08:00:00"}

整片求片（request_kind='seek'）的 request_detail 恒为空。

注意与 ``backend/emby_server/missing_episodes.py`` 区分：那是 EA
详情页的展示侧现算（只读 TMDB 磁盘缓存、按 episode_count 粗算、
不落库）；本模块是求片/台账侧的口径（按 air_date 精算、落库、
带求补闭环），两边互不复用、互不影响。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

#: MovieRequest.request_kind 取值：整片求片（存量与默认）。
KIND_SEEK = "seek"
#: MovieRequest.request_kind 取值：补缺集求片（本功能）。
KIND_MISSING = "missing_episodes"


def normalize_missing_detail(raw) -> dict:
    """任意输入 → 规范 detail ``{季号 int: [集号 int 升序]}``。

    接受 dict（键可为 str/int）、JSON 字符串或 None；脏数据一律
    丢弃对应条目，绝不抛错——台账/快照里混进脏值时读口要能降级
    成「不缺」而不是 500。
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, eps in raw.items():
        try:
            season = int(key)
        except (TypeError, ValueError):
            continue
        if season < 0 or not isinstance(eps, (list, tuple)):
            continue
        numbers = set()
        for ep in eps:
            try:
                n = int(ep)
            except (TypeError, ValueError):
                continue
            if n > 0:
                numbers.add(n)
        if numbers:
            out[season] = sorted(numbers)
    return out


def missing_detail_to_json(detail: dict) -> str:
    """规范 detail → 落库 JSON 字符串（键转字符串、季序稳定）。"""
    norm = normalize_missing_detail(detail)
    return json.dumps(
        {str(sn): norm[sn] for sn in sorted(norm)},
        ensure_ascii=False, separators=(",", ":"))


def missing_count(detail: dict) -> int:
    """规范 detail 的缺集总数。"""
    return sum(len(eps) for eps in normalize_missing_detail(detail).values())


def missing_text(detail: dict) -> str:
    """规范 detail → 大白话文案（用户端与管理端共用同一句）。

    例：``第 1 季缺第 2、5 集，第 2 季缺第 1 集``；单季缺太多
    集（>6）时只报数不逐个列；缺的季太多（>3）时只列前 3 季，
    尾巴折成「等共 N 集」。
    """
    norm = normalize_missing_detail(detail)
    if not norm:
        return ""
    parts = []
    for sn in sorted(norm):
        eps = norm[sn]
        if len(eps) <= 6:
            parts.append(f"第 {sn} 季缺第 {'、'.join(str(e) for e in eps)} 集")
        else:
            parts.append(f"第 {sn} 季缺 {len(eps)} 集")
    if len(parts) <= 3:
        return "，".join(parts)
    total = sum(len(eps) for eps in norm.values())
    return "，".join(parts[:3]) + f"，等共 {total} 集"


def build_request_detail(series_item_id: int, detail: dict,
                         checked_at: Optional[datetime] = None) -> str:
    """求片行快照 JSON：绑定剧行 + 缺集明细 + 核算时刻。"""
    norm = normalize_missing_detail(detail)
    payload = {
        "series_item_id": int(series_item_id),
        "missing": {str(sn): norm[sn] for sn in sorted(norm)},
        "missing_count": sum(len(eps) for eps in norm.values()),
        "checked_at": (checked_at or datetime.now()).isoformat(
            timespec="seconds"),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_request_detail(raw) -> dict:
    """求片行 request_detail → dict（脏值/空 → 空 dict，不抛）。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def request_detail_missing(raw) -> dict:
    """求片行快照里的缺集明细（规范形态）。"""
    return normalize_missing_detail(parse_request_detail(raw).get("missing"))
