"""
管理后台 · 弹幕设置（2026-11-15）

弹幕的全部可调项都在 SystemConfig 家族 danmaku 里（config_schema
DANMAKU_CONFIG_SCHEMA 登记，后台本卡片读写）：
- danmaku_enabled：站点开关，默认关；
- danmaku_blocked_words：敏感词表，逗号分隔，默认空 = 不过滤；
- danmaku_min_interval_seconds：同一用户最小发送间隔，默认 5 秒；
- danmaku_max_text_length：单条字数上限，默认 100。
消费方是用户端弹幕两口（backend/api/danmaku.py），读侧走
backend/danmaku.py 的唯一实现。本文件只做运营面：读当前生效值、
保存（走 config_service 统一校验通道，非法值 400）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, danmaku as danmaku_core, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_danmaku_router = APIRouter(
    prefix="/api/admin/danmaku", tags=["管理后台·弹幕设置"])


class DanmakuConfigRequest(BaseModel):
    enabled: bool = False
    blocked_words: str = ""
    min_interval_seconds: int = danmaku_core.DEFAULT_MIN_INTERVAL_SECONDS
    max_text_length: int = danmaku_core.DEFAULT_MAX_TEXT_LENGTH


@admin_danmaku_router.get("/config")
def danmaku_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取当前弹幕设置（默认值已代入，与用户端生效口径一致）。"""
    return danmaku_core.get_settings(db)


@admin_danmaku_router.put("/config")
def danmaku_update_config(
    request: DanmakuConfigRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存弹幕设置：整批走 schema 校验，非法值 400；写时失效，用户端下次读取即生效。"""
    values = {
        danmaku_core.KEY_ENABLED: request.enabled,
        danmaku_core.KEY_BLOCKED_WORDS: request.blocked_words,
        danmaku_core.KEY_MIN_INTERVAL: request.min_interval_seconds,
        danmaku_core.KEY_MAX_TEXT_LENGTH: request.max_text_length,
    }
    try:
        saved = config_service.save_config(
            db, values, schema="danmaku",
            descriptions={
                danmaku_core.KEY_ENABLED: "站内弹幕站点开关（默认关）",
                danmaku_core.KEY_BLOCKED_WORDS: "弹幕敏感词表（逗号分隔，空 = 不过滤）",
                danmaku_core.KEY_MIN_INTERVAL: "弹幕最小发送间隔（秒）",
                danmaku_core.KEY_MAX_TEXT_LENGTH: "弹幕单条字数上限",
            },
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = danmaku_core.get_settings(db)
    _audit(db, current_admin, "danmaku_update_config", "system", None,
           {"keys": saved, "enabled": payload["enabled"]},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
