"""管理后台 · 弹幕 API 线路配置（2026-11-18）。

弹幕 API 的线路表走 SystemConfig 家族 danmu_api 的
danmu_api_lines（整份 JSON），后台本口读写：读回当前生效值
（未配置时代入默认两条，与用户端生效口径一致）；保存走
config_service 统一校验通道，逐项形状校验在本层
（validate_lines），非法 400、整批不落库。写时失效，用户端
下次读取即生效。审计只记线路 id/host，不涉及任何 token。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, danmu_api as danmu_api_core, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_danmu_api_router = APIRouter(
    prefix="/api/admin/danmu-api", tags=["管理后台·弹幕API"])


class DanmuLineIn(BaseModel):
    id: str
    name: str
    note: str = ""
    host: str
    # 启用/暂停：缺省启用（旧客户端不带此字段时按启用处理）；
    # 暂停的线路仍下发给用户端展示「暂停服务」，但不拼地址。
    enabled: bool = True


class DanmuLinesRequest(BaseModel):
    lines: list[DanmuLineIn]


@admin_danmu_api_router.get("/config")
def danmu_lines_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取当前生效线路（默认值已代入，与用户端一致）。"""
    return {"lines": danmu_api_core.get_lines(db)}


@admin_danmu_api_router.put("/config")
def danmu_lines_update_config(
    request: DanmuLinesRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存线路表：形状非法 400；保存后即时生效（store 写时失效）。"""
    try:
        lines = danmu_api_core.validate_lines(
            [ln.model_dump() for ln in request.lines])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        config_service.save_config(
            db, {danmu_api_core.KEY_LINES: json.dumps(
                lines, ensure_ascii=False)},
            schema="danmu_api",
            descriptions={
                danmu_api_core.KEY_LINES: "弹幕 API 线路表（JSON 数组）",
            },
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    payload = {"lines": danmu_api_core.get_lines(db)}
    _audit(db, current_admin, "danmu_api_update_lines", "system", None,
           {"lines": [{"id": ln["id"], "host": ln["host"]}
                      for ln in payload["lines"]]},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
