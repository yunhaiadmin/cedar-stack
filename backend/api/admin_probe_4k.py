# -*- coding: utf-8 -*-
"""管理后台 · 4K 探针结果查看（后台大改造二期缺口，只读）。

写入侧是 ``backend/api/probe_4k.py``（探针页回传、仅 staff）；
本文件只补管理面读口：列表（按 created_at 倒序）+ 详情。
筛选只做两项：条目（item_guid 包含匹配）、结果（outcome 精确
匹配）——探针是一次性真机诊断，看结论够用，不自创复杂筛选。
列表不带 payload 原文（千字量级），只给字符数；详情才回全文，
与探针页当年「服务端不解析 payload」的口径一致，这里也不解析、
原样下发给页面展示。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import get_current_admin
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_probe_4k_router = APIRouter(
    prefix="/api/admin/probe-4k", tags=["管理后台·4K 探针"])

MAX_LIMIT = 200


def _row_out(row: models.Probe4kResult, username: str = "") -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": username,
        "item_guid": row.item_guid,
        "attempt": row.attempt,
        "outcome": row.outcome,
        "user_agent": row.user_agent,
        "payload_chars": len(row.payload or ""),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@admin_probe_4k_router.get("/results")
def list_probe_4k_results(
    limit: int = 50,
    offset: int = 0,
    item_guid: str = "",
    outcome: str = "",
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """探针结果列表：created_at 倒序（同刻 id 倒序兜底），两项筛选。"""
    query = db.query(models.Probe4kResult)
    if item_guid.strip():
        query = query.filter(
            models.Probe4kResult.item_guid.contains(item_guid.strip()))
    if outcome.strip():
        query = query.filter(
            models.Probe4kResult.outcome == outcome.strip())
    total = query.count()
    rows = (query.order_by(models.Probe4kResult.created_at.desc(),
                            models.Probe4kResult.id.desc())
            .limit(max(1, min(limit, MAX_LIMIT)))
            .offset(max(0, offset))
            .all())
    user_ids = {row.user_id for row in rows}
    usernames = {}
    if user_ids:
        usernames = {
            user.id: user.username or ""
            for user in db.query(models.WebUser).filter(
                models.WebUser.id.in_(list(user_ids))).all()
        }
    outcomes = [
        value for (value,) in db.query(models.Probe4kResult.outcome)
        .distinct().order_by(models.Probe4kResult.outcome).all() if value
    ]
    return {
        "total": total,
        "items": [_row_out(row, usernames.get(row.user_id, ""))
                  for row in rows],
        "outcome_options": outcomes,
    }


@admin_probe_4k_router.get("/results/{result_id}")
def get_probe_4k_result(
    result_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """探针结果详情：含 payload 原文（原样下发，页面只展示不解析）。"""
    row = db.query(models.Probe4kResult).filter(
        models.Probe4kResult.id == result_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="探针结果不存在")
    user = db.query(models.WebUser).filter(
        models.WebUser.id == row.user_id).first()
    out = _row_out(row, user.username if user else "")
    out["payload"] = row.payload or ""
    return out
