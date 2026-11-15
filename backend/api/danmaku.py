"""站内自发弹幕 · 用户端接口（网页播放器，2026-11-15）。

- GET  /api/user/danmaku/{item_guid}?start=&end=&limit=
  拉某条目时间窗内的弹幕（按时间轴升序）；同时下发当前字数上限与
  最小发送间隔，前端输入框据此提示，不必再猜。
- POST /api/user/danmaku/{item_guid}  {position_seconds, text}
  发一条弹幕：记登录者本人（子账号按本人记），落 danmaku_comments。

站点开关 danmaku_enabled 默认关：关时两口一律 404（前端据此隐藏
弹幕入口）。校验顺序：开关 → 条目存在 → 文本非空/字数 → 敏感词 →
发送间隔限流 → 落库；限流走 backend.ratelimit 既有滑动窗口，
间隔值从配置读、不写死。核心存取在 backend/danmaku.py（唯一实现）。
"""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import danmaku as danmaku_core
from backend.api.user import get_current_user
from backend.database import get_db
from backend.emby_server import models as em
from backend.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/danmaku", tags=["danmaku"])

# 时间轴位置上限（7 天秒数）：防垃圾值把弹幕打到不可能出现的位置。
_MAX_POSITION_SECONDS = 7 * 24 * 3600.0


def _require_enabled(db: Session) -> dict:
    settings = danmaku_core.get_settings(db)
    if not settings["enabled"]:
        raise HTTPException(status_code=404, detail="弹幕功能未开启")
    return settings


def _resolve_item(db: Session, item_guid: str) -> em.MediaItem:
    """由 guid 找条目（与 media_library 的 _resolve_resume_item 同口径）。"""
    item = db.query(em.MediaItem).filter(em.MediaItem.guid == item_guid).first()
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return item


@router.get("/{item_guid}")
def fetch_danmaku(
    item_guid: str,
    start: float = Query(0.0, ge=0),
    end: float | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """拉取时间窗内的弹幕：[start, end]，end 缺省 = start 起一小时。"""
    settings = _require_enabled(db)
    item = _resolve_item(db, item_guid)
    if end is None:
        end = start + 3600.0
    if end < start:
        raise HTTPException(status_code=400, detail="时间窗不合法")
    items = danmaku_core.list_danmaku(
        db, item.id, start, end, limit, current_user_id=user.id)
    return {
        "enabled": True,
        "max_text_length": settings["max_text_length"],
        "min_interval_seconds": settings["min_interval_seconds"],
        "items": items,
    }


class DanmakuPostRequest(BaseModel):
    position_seconds: float
    text: str


@router.post("/{item_guid}")
def post_danmaku(
    item_guid: str,
    body: DanmakuPostRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发一条弹幕：校验全过才落库，命中敏感词明确拒绝、不落库。"""
    settings = _require_enabled(db)
    item = _resolve_item(db, item_guid)

    position = body.position_seconds
    if not math.isfinite(position) or position < 0 or position > _MAX_POSITION_SECONDS:
        raise HTTPException(status_code=400, detail="弹幕时间点不合法")

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="弹幕内容不能为空")
    max_len = settings["max_text_length"]
    if len(text) > max_len:
        raise HTTPException(
            status_code=400, detail=f"弹幕最多 {max_len} 个字")

    words = danmaku_core.parse_blocked_words(settings["blocked_words"])
    if words:
        hit = danmaku_core.find_blocked_word(text, words)
        if hit is not None:
            raise HTTPException(
                status_code=400, detail="弹幕包含敏感词，发送失败")

    min_interval = settings["min_interval_seconds"]
    if min_interval > 0:
        allowed, _retry = check_rate_limit(
            f"danmaku_post:{user.id}", 1, float(min_interval))
        if not allowed:
            raise HTTPException(
                status_code=429, detail="发送太频繁，请稍后再试")

    row = danmaku_core.create_danmaku(db, user.id, item.id, position, text)
    return {
        "ok": True,
        "item": danmaku_core.serialize(row, user.username, user.id),
    }
