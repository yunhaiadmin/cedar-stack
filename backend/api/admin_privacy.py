# -*- coding: utf-8 -*-
"""管理端 · 隐私政策正文编辑（运营参数）。

用户端公开页 /privacy 的正文存 SystemConfig 单键 privacy_policy_md
（家族 privacy_policy，整份 Markdown），本模块只做一件事：把管理员
编辑的正文经 config_service 校验落库、读回。保存后用户端经公开
端点 /api/site/privacy-policy 实时读取（直查口径，无缓存延迟）。

口径：
- 正文为空（清空保存）= 删除配置行，用户端回落 PrivacyView 内置
  的现行写死正文（一字不丢）；
- GET 在未存自定义正文时另附 default_markdown（现行正文的
  Markdown 转写）供编辑器起稿，起稿本身不落库、不生效。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import privacy_policy

admin_privacy_router = APIRouter(
    prefix="/api/admin/privacy-policy", tags=["管理-隐私政策"])


class PrivacyPolicyRequest(BaseModel):
    content: str = ""


def _response(db: Session) -> dict:
    content = privacy_policy.read_custom(db)
    return {
        "content": content,
        "has_custom": bool(content.strip()),
        "default_markdown": privacy_policy.DEFAULT_MARKDOWN,
    }


@admin_privacy_router.get("/content")
def get_privacy_policy(
    db: Session = Depends(get_db),
    _admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """当前已保存的自定义正文（未存过时 content 为空，另附现行
    正文的 Markdown 转写供起稿）。"""
    return _response(db)


@admin_privacy_router.put("/content")
def update_privacy_policy(
    payload: PrivacyPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """整份保存：空正文 = 删行回落内置正文；非空落库并回读实际
    生效的正文。审计只记长度与是否自定义，不记正文原文。"""
    try:
        config_service.save_config(
            db,
            {privacy_policy.KEY: payload.content},
            schema="privacy_policy",
        )
    except ConfigValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(
        db, current_admin, "privacy_policy_update", "system_config",
        details={
            "length": len(payload.content),
            "has_custom": bool(payload.content.strip()),
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return _response(db)
