"""4K MSE 探针结果回传（一次性真机诊断，2026-11-17）。

探针页 ``/probe-4k`` 只对 staff 渲染，但页面藏起来不是权限——
本端点再验一次 is_staff，非 staff 一律 403。只收本页字段：
item_guid / attempt / outcome / payload（探针页拼的 JSON 串，
原样落库，服务端不解析、不信任其结构）。读结论走库表
``probe_4k_results``（按 created_at 倒序看最新一轮）。
路线定音后本端点与探针页一并退役。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models
from backend.api.user import get_current_user
from backend.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/probe-4k", tags=["4K 探针"])

# payload 是整轮尝试的结构化留痕（流清单 + 预检矩阵 + 每次尝试
# 结果），实测千字量级；上限防滥用，超限由 pydantic 直接 422。
MAX_PAYLOAD_CHARS = 20000


class Probe4kResultIn(BaseModel):
    item_guid: str = Field(default="", max_length=64)
    attempt: str = Field(default="", max_length=64)
    outcome: str = Field(default="", max_length=64)
    payload: str = Field(default="", max_length=MAX_PAYLOAD_CHARS)


@router.post("/result")
def post_probe_4k_result(
    body: Probe4kResultIn,
    request: Request,
    db: Session = Depends(get_db),
    user: models.WebUser = Depends(get_current_user),
) -> dict:
    """落一条探针结果（仅 staff）。"""
    if not user.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可用",
        )
    row = models.Probe4kResult(
        user_id=user.id,
        item_guid=body.item_guid.strip(),
        attempt=body.attempt.strip(),
        outcome=body.outcome.strip(),
        payload=body.payload,
        user_agent=(request.headers.get("user-agent") or "")[:400],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "probe-4k result id=%s user=%s item=%s attempt=%s outcome=%s",
        row.id, user.id, row.item_guid, row.attempt, row.outcome,
    )
    return {"ok": True, "id": row.id}
