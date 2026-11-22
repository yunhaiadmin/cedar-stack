"""管理后台 · 弹幕 API 线路配置（2026-11-18）。

弹幕 API 的线路表走 SystemConfig 家族 danmu_api 的
danmu_api_lines（整份 JSON），后台本口读写：读回当前生效值
（未配置时代入默认两条，与用户端生效口径一致）；保存走
config_service 统一校验通道，逐项形状校验在本层
（validate_lines），非法 400、整批不落库。写时失效，用户端
下次读取即生效。审计只记线路 id/host，不涉及任何 token。

2026-11-19 起本口同时承载外部弹幕源两项（同家族的
danmu_api_external_enabled / danmu_api_external_url）：GET 一并
回当前值；PUT 带这两项才改（不带 = 不动，兼容只存线路的旧
调用）。上游地址含 token 路径，审计只记 host、不记全址。
2026-11-22 起 external_url 按全仓 secret 口径掩码：GET 非空只回
"******" + external_url_set 布尔；PUT 收到 "******" 保留原值。
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

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
    # 外部弹幕源两项：None = 本次不改（兼容只存线路的调用）。
    external_enabled: bool | None = None
    external_url: str | None = None


def _config_payload(db: Session) -> dict:
    external_url = danmu_api_core.external_url(db)
    return {
        "lines": danmu_api_core.get_lines(db),
        "external_enabled": danmu_api_core.external_enabled(db),
        # 地址含 token 路径，与全仓 secret 口径一致：非空只回掩码，
        # 另加 external_url_set 报是否已配置，真值不进任何管理端响应。
        "external_url": "******" if external_url else "",
        "external_url_set": bool(external_url),
    }


@admin_danmu_api_router.get("/config")
def danmu_lines_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取当前生效线路（默认值已代入，与用户端一致）+ 外部源两项。"""
    return _config_payload(db)


@admin_danmu_api_router.put("/config")
def danmu_lines_update_config(
    request: DanmuLinesRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存线路表（+ 带了才改的外部源两项）：形状非法 400、整批
    不落库；保存后即时生效（store 写时失效）。"""
    try:
        lines = danmu_api_core.validate_lines(
            [ln.model_dump() for ln in request.lines])
        external_url = None
        # 掩码＝保留原值不覆盖（与全仓 secret 保存口径一致）：GET 回的
        # "******" 被原样提交回来时，不当新地址校验、更不落库。
        if request.external_url is not None and request.external_url != "******":
            external_url = danmu_api_core.validate_external_url(
                request.external_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    values = {danmu_api_core.KEY_LINES: json.dumps(
        lines, ensure_ascii=False)}
    descriptions = {
        danmu_api_core.KEY_LINES: "弹幕 API 线路表（JSON 数组）",
    }
    if request.external_enabled is not None:
        values[danmu_api_core.KEY_EXTERNAL_ENABLED] = (
            request.external_enabled)
        descriptions[danmu_api_core.KEY_EXTERNAL_ENABLED] = (
            "弹幕 API 外部弹幕源开关")
    if external_url is not None:
        values[danmu_api_core.KEY_EXTERNAL_URL] = external_url
        descriptions[danmu_api_core.KEY_EXTERNAL_URL] = (
            "弹幕 API 外部弹幕源地址（LogVar 弹幕 API base）")
    try:
        config_service.save_config(
            db, values, schema="danmu_api", descriptions=descriptions)
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    payload = _config_payload(db)
    audit_detail = {"lines": [{"id": ln["id"], "host": ln["host"]}
                              for ln in payload["lines"]]}
    if request.external_enabled is not None:
        audit_detail["external_enabled"] = payload["external_enabled"]
    if external_url is not None:
        # 地址含 token 路径：审计只记 host，不落全址（payload 里的
        # external_url 已是掩码，host 必须取本次校验后的真值算）。
        audit_detail["external_url_host"] = urlparse(
            external_url).netloc or "(未配置)"
    _audit(db, current_admin, "danmu_api_update_lines", "system", None,
           audit_detail,
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
