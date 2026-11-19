"""
管理后台 · 弹幕设置与内容治理

设置面（2026-11-15）：弹幕的全部可调项都在 SystemConfig 家族
danmaku 里（config_schema DANMAKU_CONFIG_SCHEMA 登记，后台本卡片读写）：
- danmaku_enabled：站点开关，默认关；
- danmaku_blocked_words：敏感词表，逗号分隔，默认空 = 不过滤；
- danmaku_min_interval_seconds：同一用户最小发送间隔，默认 5 秒；
- danmaku_max_text_length：单条字数上限，默认 100。
消费方是用户端弹幕两口（backend/api/danmaku.py），读侧走
backend/danmaku.py 的唯一实现。本文件只做运营面：读当前生效值、
保存（走 config_service 统一校验通道，非法值 400）。

内容治理面（2026-11-19）：弹幕列表（按条目/用户/关键词筛、分页）、
单条删除、用户禁言/解禁，端点挂在同前缀下（/comments、/mutes）。
口径（按最合理管理员行为推导）：
- 删除是硬删：danmaku_comments 没有软删字段（无 deleted_at），
  该表既有口径就是一行一条弹幕、删了即消失；删除快照（作者/
  条目/正文摘要）记进 AdminLog 供追溯。
- 禁言存 danmaku_mutes（一人一行，重复禁言更新同一行）：
  muted_until 为 NULL = 永久；到期自动失效不用人工解禁。
  发送口真查（api/danmaku.py 走 danmaku_core.get_active_mute），
  命中回 403。禁言/解禁/删除全部记 AdminLog。
- 治理面不受站点开关 danmaku_enabled 限制：开关只管用户侧两口，
  关着时存量弹幕与禁言仍要能看、能处置。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, danmaku as danmaku_core, models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.pagination import pagination_params
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.emby_server import models as em

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


# ==================== 内容治理：弹幕列表 / 删除 ====================

def _comment_out(row: em.DanmakuComment, username: Optional[str],
                 item: Optional[em.MediaItem]) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": username or "",
        "item_id": row.item_id,
        "item_name": item.name if item is not None else "",
        "item_guid": item.guid if item is not None else "",
        "position_seconds": row.position_seconds,
        "text": row.text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@admin_danmaku_router.get("/comments")
def list_danmaku_comments(
    item_id: Optional[int] = None,
    user_id: Optional[int] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """全站弹幕列表：按发送时间倒序，支持条目/用户精确筛 + 关键词筛。

    关键词同时匹配弹幕正文、用户名与条目名（任一命中即入列，与
    追剧订阅管理列表同一口径）。治理面不受站点开关限制。
    """
    limit, offset = pagination_params(limit, offset)

    query = db.query(em.DanmakuComment)
    if item_id:
        query = query.filter(em.DanmakuComment.item_id == item_id)
    if user_id:
        query = query.filter(em.DanmakuComment.user_id == user_id)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        user_ids = [uid for (uid,) in (
            db.query(models.WebUser.id)
            .filter(models.WebUser.username.like(kw)).all())]
        item_ids = [iid for (iid,) in (
            db.query(em.MediaItem.id)
            .filter(em.MediaItem.name.like(kw)).all())]
        query = query.filter(
            em.DanmakuComment.text.like(kw)
            | em.DanmakuComment.user_id.in_(user_ids)
            | em.DanmakuComment.item_id.in_(item_ids))
    total = query.count()
    rows = (query.order_by(em.DanmakuComment.created_at.desc(),
                           em.DanmakuComment.id.desc())
            .offset(offset).limit(limit).all())

    usernames: dict[int, str] = {}
    items: dict[int, em.MediaItem] = {}
    if rows:
        usernames = {u.id: u.username for u in (
            db.query(models.WebUser)
            .filter(models.WebUser.id.in_({r.user_id for r in rows}))
            .all())}
        items = {i.id: i for i in (
            db.query(em.MediaItem)
            .filter(em.MediaItem.id.in_({r.item_id for r in rows}))
            .all())}
    return {
        "total": total,
        "items": [_comment_out(r, usernames.get(r.user_id),
                               items.get(r.item_id)) for r in rows],
    }


@admin_danmaku_router.delete("/comments/{comment_id}")
def delete_danmaku_comment(
    comment_id: int,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """删除一条弹幕：硬删（表无软删字段，见文件头口径）。

    删除前把作者/条目/正文摘要快照进 AdminLog，删后仍可追溯；
    行不存在 404。
    """
    row = (db.query(em.DanmakuComment)
           .filter(em.DanmakuComment.id == comment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="弹幕不存在")
    _audit(db, current_admin, "danmaku_delete_comment",
           "danmaku_comment", row.id,
           details={
               "user_id": row.user_id,
               "item_id": row.item_id,
               "position_seconds": row.position_seconds,
               "text": row.text[:100],
           },
           ip=raw_request.client.host if raw_request.client else None)
    db.delete(row)
    db.commit()
    return {"success": True, "comment_id": comment_id}


# ==================== 内容治理：禁言 / 解禁 ====================

_MAX_MUTE_HOURS = 24 * 365  # 定时禁言最长一年，超过请用永久


class DanmakuMuteRequest(BaseModel):
    user_id: int
    # permanent=True 永久禁言；否则必须给 duration_hours（1~8760）
    permanent: bool = False
    duration_hours: Optional[int] = None
    reason: str = ""


def _mute_out(row: em.DanmakuMute, username: Optional[str],
              now: datetime) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": username or "",
        "muted_until": row.muted_until.isoformat() if row.muted_until else None,
        "permanent": row.muted_until is None,
        "active": danmaku_core.mute_is_active(row, now),
        "reason": row.reason or "",
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@admin_danmaku_router.get("/mutes")
def list_danmaku_mutes(
    limit: int = 50,
    offset: int = 0,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """禁言名单：按最近设置倒序；已自然到期的行保留展示（active=false）。"""
    limit, offset = pagination_params(limit, offset)
    query = db.query(em.DanmakuMute)
    total = query.count()
    rows = (query.order_by(em.DanmakuMute.updated_at.desc(),
                           em.DanmakuMute.id.desc())
            .offset(offset).limit(limit).all())
    usernames: dict[int, str] = {}
    if rows:
        usernames = {u.id: u.username for u in (
            db.query(models.WebUser)
            .filter(models.WebUser.id.in_({r.user_id for r in rows}))
            .all())}
    now = datetime.now()
    return {
        "total": total,
        "items": [_mute_out(r, usernames.get(r.user_id), now) for r in rows],
    }


@admin_danmaku_router.post("/mutes")
def create_danmaku_mute(
    request: DanmakuMuteRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """给用户设置弹幕禁言（一人一行，重复设置更新同一行）。

    permanent 与 duration_hours 二选一：永久时 muted_until 存 NULL；
    定时按当前时间 + 小时数算到期。用户不存在 404，时长非法 400。
    """
    user = (db.query(models.WebUser)
            .filter(models.WebUser.id == request.user_id).first())
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if request.permanent:
        muted_until = None
    else:
        hours = request.duration_hours
        if hours is None or not (1 <= hours <= _MAX_MUTE_HOURS):
            raise HTTPException(
                status_code=400,
                detail=f"定时禁言必须给 1~{_MAX_MUTE_HOURS} 小时的时长，"
                       "或选择永久禁言")
        muted_until = datetime.now() + timedelta(hours=hours)

    row = (db.query(em.DanmakuMute)
           .filter(em.DanmakuMute.user_id == user.id).first())
    if row is None:
        row = em.DanmakuMute(user_id=user.id)
        db.add(row)
    row.muted_until = muted_until
    row.reason = (request.reason or "").strip()[:255]
    row.created_by = current_admin.id
    _audit(db, current_admin, "danmaku_mute_user", "user", user.id,
           details={
               "username": user.username,
               "permanent": muted_until is None,
               "muted_until": muted_until.isoformat() if muted_until else None,
               "reason": row.reason,
           },
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    db.refresh(row)
    return _mute_out(row, user.username, datetime.now())


@admin_danmaku_router.delete("/mutes/{user_id}")
def delete_danmaku_mute(
    user_id: int,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """解除一个用户的弹幕禁言（删行）；该用户没有禁言行时 404。"""
    row = (db.query(em.DanmakuMute)
           .filter(em.DanmakuMute.user_id == user_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="该用户未被禁言")
    _audit(db, current_admin, "danmaku_unmute_user", "user", user_id,
           details={
               "muted_until": row.muted_until.isoformat()
               if row.muted_until else None,
               "reason": row.reason or "",
           },
           ip=raw_request.client.host if raw_request.client else None)
    db.delete(row)
    db.commit()
    return {"success": True, "user_id": user_id}
