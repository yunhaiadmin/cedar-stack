"""管理后台 · 双盘审计台账（goal-38 审计第一步）。

一个查询面看个人盘的全程留痕：

    GET /api/admin/audit/events

把三路既有/新增记录归一成同一行式再合并分页（不新建汇总表，
读时归一）：

- ``decision``     转存决策留痕 transfer_decision_rounds
                   （transfer_gate 个人盘门禁 / upstream_aggregate；
                   贡献池已下线，其历史 scope 行仍按原样呈现）；
- ``drive_error``  Drive 报错事件 drive_api_error_events——只有
                   账号不可逆哈希，无 user/binding/item 维度，
                   按人/盘/片筛选时本路恒不命中（如实呈现）；
- ``play_access``  个人盘起播取用事件 drive_play_access_events
                   （门禁被拒 / 副本选路命中未命中 / 换线失败，
                   goal-38 第一步新增的旁路记录）。

归一行式：occurred_at、source、category（原始 scope/类别）、
action_label（大白话动作名）、user_id / member_id / binding_id /
item_id（能解析的才有）、result（原始结果）、outcome（归一
结果组：success 放行/完成、rejected 被拒/失败、in_progress
处理中、changed 状态变更、error 报错）、reason、detail（上下文
快照 dict）。

筛选：source / category / user_id / member_id / binding_id /
item_id / outcome / start / end（ISO 时间，含 start 不含 end）。
outcome 在各路查询层按同一映射预过滤（不是页内过滤），total
与分页口径一致。member_id 只对决策路的历史文本键行
（member:<id>）匹配，池成员表已随贡献池下线、不再解析。

分页：limit/offset 制（pagination_params 统一钳制）。合并口径：
每路先按自身筛选取 count 与 newest (offset+limit) 行，内存按
时间倒序合并后切片——每路取够 offset+limit 行保证全局序正确。
只读，不写库。鉴权与管理端其他口同口径（get_current_admin）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, not_, or_
from sqlalchemy.orm import Session

from backend.api.admin_core import get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db
from backend.emby_server import models as em

logger = logging.getLogger(__name__)

audit_router = APIRouter(prefix="/api/admin", tags=["管理后台·双盘审计"])

#: 决策 scope → 大白话动作名
_DECISION_ACTION_LABELS = {
    "transfer_gate": "转存门禁（个人盘）",
    "pool_gate": "池门禁（公共盘）",
    "pool_pick": "池选盘",
    "pool_breaker": "池熔断",
    "pool_evict": "池清理淘汰",
    "upstream_aggregate": "上游聚合",
}

#: 起播取用 action → 大白话动作名
_ACCESS_ACTION_LABELS = {
    "gate_reject": "起播门禁被拒",
    "copy_resolve": "副本选路",
    "switch_failed": "换线失败",
}

_SOURCES = ("decision", "drive_error", "play_access")

_OUTCOMES = ("success", "rejected", "in_progress", "changed", "error")


def _parse_dt(value: str, field: str):
    """ISO 时间解析；空串 = 不筛；解析不出回 400 明示。"""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field} 不是合法的时间（需 ISO 格式，如 2026-11-09T00:00:00）")


# ---------------------------------------------------------------------------
# outcome 归一映射（展示与查询预过滤共用同一口径，两处必须一致）
# ---------------------------------------------------------------------------

def _decision_outcome(decision: str) -> str:
    d = (decision or "").strip()
    if d in ("proceed", "serve_pool_copy", "serve_115_copy", "recover"):
        return "success"
    if d.startswith("member:"):
        return "success"
    if d == "preparing":
        return "in_progress"
    if d in ("failed", "error", "none"):
        return "rejected"
    # trip / retrip / evicted:* / round_opened 等 → 状态变更
    return "changed"


def _decision_outcome_cond(outcome: str):
    """决策路 outcome 的 SQL 预过滤条件（与 _decision_outcome
    同口径）；None = 该 outcome 在本路不存在。"""
    col = em.TransferDecisionRound.decision
    success = or_(
        col.in_(("proceed", "serve_pool_copy", "serve_115_copy",
                 "recover")),
        col.like("member:%"))
    if outcome == "success":
        return success
    if outcome == "in_progress":
        return col == "preparing"
    if outcome == "rejected":
        return col.in_(("failed", "error", "none"))
    if outcome == "changed":
        return and_(not_(success), col != "preparing",
                    not_(col.in_(("failed", "error", "none"))))
    return None  # error 组在决策路不存在


def _access_outcome(result: str) -> str:
    r = (result or "").strip()
    if r == "hit":
        return "success"
    if r == "denied":
        return "rejected"
    if r == "error":
        return "error"
    return "changed"


def _parse_resource_key(resource_key: str):
    """resource_key 文本键 → (item_id, member_id)，解析不出为 None。"""
    key = (resource_key or "").strip()
    for prefix, slot in (("item:", "item"), ("member:", "member")):
        if key.startswith(prefix):
            try:
                value = int(key[len(prefix):])
            except ValueError:
                return None, None
            return (value, None) if slot == "item" else (None, value)
    return None, None


def _parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _row(source, row_id, occurred_at, category, action_label,
         result, outcome, reason, detail,
         user_id=None, member_id=None, binding_id=None, item_id=None):
    return {
        "id": f"{source}:{row_id}",
        "occurred_at": (occurred_at.isoformat()
                        if occurred_at is not None else None),
        "source": source,
        "category": category,
        "action_label": action_label,
        "user_id": user_id,
        "member_id": member_id,
        "binding_id": binding_id,
        "item_id": item_id,
        "result": result,
        "outcome": outcome,
        "reason": reason,
        "detail": detail,
        "_sort_at": occurred_at,
    }


def _time_filtered(q, column, f):
    if f["start"] is not None:
        q = q.filter(column >= f["start"])
    if f["end"] is not None:
        q = q.filter(column < f["end"])
    return q


# ---------------------------------------------------------------------------
# 三路采集：各自返回 (total, [归一行])，行已按时间倒序取够 need 条
# ---------------------------------------------------------------------------

def _collect_decisions(db, f, need: int):
    if f["outcome"]:
        cond = _decision_outcome_cond(f["outcome"])
        if cond is None:
            return 0, []
    else:
        cond = None
    q = db.query(em.TransferDecisionRound)
    if cond is not None:
        q = q.filter(cond)
    if f["category"]:
        q = q.filter(em.TransferDecisionRound.scope == f["category"])
    q = _time_filtered(q, em.TransferDecisionRound.created_at, f)
    if f["item_id"] is not None:
        q = q.filter(
            em.TransferDecisionRound.resource_key == f"item:{f['item_id']}")
    if f["binding_id"] is not None and f["member_id"] is None:
        # 决策行没有 binding 列，历史上靠池成员表互查；成员表已
        # 随贡献池下线，本路在 binding 维度恒不命中（如实）。
        return 0, []
    if f["member_id"] is not None:
        key = f"member:{int(f['member_id'])}"
        q = q.filter(or_(
            em.TransferDecisionRound.resource_key == key,
            em.TransferDecisionRound.decision == key))
    elif f["user_id"] is not None:
        uid = int(f["user_id"])
        q = q.filter(or_(
            em.TransferDecisionRound.candidates_json.like(
                f'%"user_id": {uid},%'),
            em.TransferDecisionRound.candidates_json.like(
                f'%"user_id": {uid}}}%'),
        ))
    total = q.count()
    rows = (q.order_by(em.TransferDecisionRound.created_at.desc(),
                       em.TransferDecisionRound.id.desc())
            .limit(need).all())
    items = []
    for row in rows:
        item_id, member_id = _parse_resource_key(row.resource_key)
        candidates = _parse_json(row.candidates_json)
        cand = candidates if isinstance(candidates, dict) else {}
        if item_id is None and cand.get("item_id") is not None:
            try:
                item_id = int(cand["item_id"])
            except (TypeError, ValueError):
                pass
        if member_id is None:
            decision = (row.decision or "").strip()
            if decision.startswith("member:"):
                try:
                    member_id = int(decision[len("member:"):])
                except ValueError:
                    pass
            elif cand.get("member_id") is not None:
                try:
                    member_id = int(cand["member_id"])
                except (TypeError, ValueError):
                    pass
        user_id = cand.get("user_id")
        detail = {"scope": row.scope, "resource_key": row.resource_key,
                  "execution_ref": row.execution_ref,
                  "candidates": candidates}
        items.append(_row(
            "decision", row.id, row.created_at, row.scope,
            _DECISION_ACTION_LABELS.get(row.scope, row.scope),
            row.decision, _decision_outcome(row.decision), row.reason,
            detail, user_id=user_id, member_id=member_id,
            item_id=item_id))
    return total, items


def _collect_drive_errors(db, f, need: int):
    # 本路只有账号不可逆哈希：按人/盘/片筛时恒不命中（如实）
    if any(f[k] is not None for k in
           ("user_id", "member_id", "binding_id", "item_id")):
        return 0, []
    if f["outcome"] and f["outcome"] != "error":
        return 0, []
    q = db.query(em.DriveApiErrorEvent)
    q = _time_filtered(q, em.DriveApiErrorEvent.created_at, f)
    if f["category"]:
        q = q.filter(em.DriveApiErrorEvent.caller == f["category"])
    total = q.count()
    rows = (q.order_by(em.DriveApiErrorEvent.created_at.desc(),
                       em.DriveApiErrorEvent.id.desc())
            .limit(need).all())
    items = []
    for row in rows:
        items.append(_row(
            "drive_error", row.id, row.created_at, row.caller,
            "Drive 接口报错", str(row.status_code), "error", row.reason,
            {"domain": row.domain, "caller": row.caller,
             "status_code": row.status_code,
             "account_hash": row.account_hash}))
    return total, items


def _collect_play_access(db, f, need: int):
    if f["member_id"] is not None:
        return 0, []  # 个人盘取用事件无成员维度
    if f["outcome"]:
        result_cond = {
            "success": em.DrivePlayAccessEvent.result == "hit",
            "rejected": em.DrivePlayAccessEvent.result == "denied",
            "error": em.DrivePlayAccessEvent.result == "error",
            "changed": not_(em.DrivePlayAccessEvent.result.in_(
                ("hit", "denied", "error"))),
        }.get(f["outcome"])
        if result_cond is None:
            return 0, []
    else:
        result_cond = None
    q = db.query(em.DrivePlayAccessEvent)
    if result_cond is not None:
        q = q.filter(result_cond)
    q = _time_filtered(q, em.DrivePlayAccessEvent.created_at, f)
    if f["category"]:
        q = q.filter(em.DrivePlayAccessEvent.action == f["category"])
    if f["user_id"] is not None:
        q = q.filter(em.DrivePlayAccessEvent.user_id == int(f["user_id"]))
    if f["binding_id"] is not None:
        q = q.filter(
            em.DrivePlayAccessEvent.binding_id == int(f["binding_id"]))
    if f["item_id"] is not None:
        q = q.filter(em.DrivePlayAccessEvent.item_id == int(f["item_id"]))
    total = q.count()
    rows = (q.order_by(em.DrivePlayAccessEvent.created_at.desc(),
                       em.DrivePlayAccessEvent.id.desc())
            .limit(need).all())
    items = []
    for row in rows:
        items.append(_row(
            "play_access", row.id, row.created_at, row.action,
            _ACCESS_ACTION_LABELS.get(row.action, row.action),
            row.result, _access_outcome(row.result), row.reason,
            {"entry": row.entry, "detail": _parse_json(row.detail)},
            user_id=row.user_id, binding_id=row.binding_id,
            item_id=row.item_id))
    return total, items


_COLLECTORS = {
    "decision": _collect_decisions,
    "drive_error": _collect_drive_errors,
    "play_access": _collect_play_access,
}


@audit_router.get("/audit/events")
def audit_events(source: str = "", category: str = "",
                 user_id: int = None, member_id: int = None,
                 binding_id: int = None, item_id: int = None,
                 outcome: str = "", start: str = "", end: str = "",
                 limit: int = 50, offset: int = 0,
                 db: Session = Depends(get_db),
                 admin=Depends(get_current_admin)):
    """双盘审计台账：三路记录归一合并查询（只读）。筛选与归一
    口径见模块 docstring。"""
    limit, offset = pagination_params(limit, offset)
    start_dt = _parse_dt(start, "start")
    end_dt = _parse_dt(end, "end")
    outcome = (outcome or "").strip()
    if outcome and outcome not in _OUTCOMES:
        raise HTTPException(
            status_code=400,
            detail=f"outcome 不认识（可选 {'/'.join(_OUTCOMES)}）")
    f = {"category": (category or "").strip() or None,
         "user_id": user_id, "member_id": member_id,
         "binding_id": binding_id, "item_id": item_id,
         "outcome": outcome or None,
         "start": start_dt, "end": end_dt}
    source = (source or "").strip()
    if source and source not in _SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source 不认识（可选 {'/'.join(_SOURCES)}）")
    wanted = [s for s in _SOURCES if not source or s == source]
    need = offset + limit
    total = 0
    merged = []
    for name in wanted:
        sub_total, items = _COLLECTORS[name](db, f, need)
        total += sub_total
        merged.extend(items)
    merged.sort(
        key=lambda it: (it["_sort_at"] or datetime.min, it["id"]),
        reverse=True)
    page = merged[offset:offset + limit]
    for it in page:
        it.pop("_sort_at", None)
    return {"success": True, "items": page, "total": total,
            "limit": limit, "offset": offset}
