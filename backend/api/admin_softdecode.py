"""
管理后台 · 网页软解开关（2026-11-14 批 2）

规则本体在 SystemConfig web_softdecode_enabled（word 风格布尔，
无行 = 关）；消费方是用户端 WebPlayerView（公开端点
``/api/site/web-softdecode`` 只读下发）。本文件只做运营面：
读当前开关、保存（走 config_service 统一校验通道，非法值 400）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import store

logger = logging.getLogger(__name__)

admin_softdecode_router = APIRouter(
    prefix="/api/admin/softdecode", tags=["管理后台·网页软解"])

CONFIG_KEY = "web_softdecode_enabled"


def softdecode_enabled(db: Session) -> bool:
    """读取开关：无行 = 关；只认 "true"（word 风格，与 welfare 口径一致）。"""
    return store.get_value(db, CONFIG_KEY, "false") == "true"


class SoftdecodeConfigRequest(BaseModel):
    enabled: bool = False


@admin_softdecode_router.get("/config")
def softdecode_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取当前网页软解开关（默认关）。"""
    return {"enabled": softdecode_enabled(db)}


@admin_softdecode_router.put("/config")
def softdecode_update_config(
    request: SoftdecodeConfigRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存网页软解开关：非法值 400；写时失效，播放页下次读取即生效。"""
    try:
        saved = config_service.save_config(
            db, {CONFIG_KEY: request.enabled}, schema="web_softdecode",
            descriptions={CONFIG_KEY: "网页软解开关（浏览器硬解失败时用站内 wasm 兜底）"},
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = {"enabled": softdecode_enabled(db)}
    _audit(db, current_admin, "softdecode_update_config", "system", None,
           {"keys": saved, "enabled": payload["enabled"]},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
