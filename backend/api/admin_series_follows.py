# -*- coding: utf-8 -*-
"""管理后台 · 追剧订阅管理（后台大改造二期缺口项 2，2026-11-19）。

    GET  /api/admin/series-follows               全站追剧订阅列表
                                                 （分页/状态/用户/关键词过滤）
    POST /api/admin/series-follows/{id}/cancel   管理员代取消订阅

数据源 emby_series_follows（emby_server.models.SeriesFollow），
用户侧口径见 backend/api/series_follow_api.py 与
emby_server/series_follow.py：一行 = 一个用户订阅的一部剧，
取消只置 status=cancelled、不删行，重订阅原地恢复。

管理口径（按最合理管理员行为推导）：
- 列表不受用户侧总开关 SERIES_FOLLOW_ENABLED 限制：该开关只管
  用户侧订阅口，关着时存量订阅行仍在，管理员要能看见并处置。
- 代取消复用用户侧同一语义（series_follow.unsubscribe：只置
  cancelled、不删行），不另起状态机；已 cancelled 的行重复代
  取消幂等返回，不重复记审计。动作记 AdminLog。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import series_follow as sf

logger = logging.getLogger(__name__)

admin_series_follows_router = APIRouter(
    prefix="/api/admin/series-follows", tags=["管理后台·追剧订阅管理"])

_STATUSES = ("active", "cancelled")


def _row_out(follow: em.SeriesFollow, username: Optional[str],
             item: Optional[em.MediaItem]) -> dict:
    return {
        "id": follow.id,
        "user_id": follow.user_id,
        "username": username or "",
        "series_item_id": follow.series_item_id,
        "series_name": item.name if item is not None else "",
        "series_year": item.production_year if item is not None else None,
        "status": follow.status,
        "created_at": follow.created_at.isoformat()
        if follow.created_at else None,
        "updated_at": follow.updated_at.isoformat()
        if follow.updated_at else None,
    }


@admin_series_follows_router.get("")
def list_series_follows(
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """全站追剧订阅列表：按订阅时间倒序，支持状态/用户/关键词过滤。

    关键词同时匹配用户名与剧名（任一命中即入列）。
    """
    if status is not None and status not in _STATUSES:
        raise HTTPException(
            status_code=400, detail="status 只支持 active / cancelled")
    limit, offset = pagination_params(limit, offset)

    query = db.query(em.SeriesFollow)
    if status:
        query = query.filter(em.SeriesFollow.status == status)
    if user_id:
        query = query.filter(em.SeriesFollow.user_id == user_id)
    if q and q.strip():
        kw = f"%{q.strip()}%"
        user_ids = [uid for (uid,) in (
            db.query(models.WebUser.id)
            .filter(models.WebUser.username.like(kw)).all())]
        item_ids = [iid for (iid,) in (
            db.query(em.MediaItem.id)
            .filter(em.MediaItem.name.like(kw)).all())]
        query = query.filter(
            em.SeriesFollow.user_id.in_(user_ids)
            | em.SeriesFollow.series_item_id.in_(item_ids))
    total = query.count()
    follows = (query.order_by(em.SeriesFollow.created_at.desc(),
                              em.SeriesFollow.id.desc())
               .offset(offset).limit(limit).all())

    usernames = {}
    if follows:
        usernames = {u.id: u.username for u in (
            db.query(models.WebUser)
            .filter(models.WebUser.id.in_({f.user_id for f in follows}))
            .all())}
        items = {i.id: i for i in (
            db.query(em.MediaItem)
            .filter(em.MediaItem.id.in_({f.series_item_id for f in follows}))
            .all())}
    else:
        items = {}
    return {
        "total": total,
        "items": [_row_out(f, usernames.get(f.user_id),
                           items.get(f.series_item_id)) for f in follows],
    }


@admin_series_follows_router.post("/{follow_id}/cancel")
def cancel_series_follow(
    follow_id: int,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """管理员代取消：只置 cancelled、不删行（与用户自助取消同语义）。

    已 cancelled 时幂等返回当前状态、不重复写审计；行不存在 404。
    """
    follow = (db.query(em.SeriesFollow)
              .filter(em.SeriesFollow.id == follow_id).first())
    if follow is None:
        raise HTTPException(status_code=404, detail="订阅记录不存在")
    if follow.status != "active":
        return {"success": True, "reason": "already_cancelled",
                "follow_id": follow.id, "status": follow.status}
    out = sf.unsubscribe(db, follow.user_id, follow.series_item_id)
    item = (db.query(em.MediaItem)
            .filter(em.MediaItem.id == follow.series_item_id).first())
    _audit(db, current_admin, "series_follow_admin_cancel",
           "series_follow", follow.id,
           details={
               "user_id": follow.user_id,
               "series_item_id": follow.series_item_id,
               "series_name": item.name if item is not None else "",
           },
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    db.refresh(follow)
    return {"success": True, "reason": out["reason"],
            "follow_id": follow.id, "status": follow.status}
