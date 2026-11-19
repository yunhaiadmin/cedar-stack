# -*- coding: utf-8 -*-
"""管理后台 · 用户收藏查看（后台大改造二期缺口项 6，2026-11-19）。

    GET /api/admin/users/{user_id}/favorites   按用户只读查看收藏

收藏现状（已核实）：作品收藏落在 emby_user_media_data.is_favorite
（UserMediaData，用户侧门户列表见 emby_server/portal.py 的
/favorites 口）；人员收藏落在 emby_user_person_favorites
（UserPersonFavorite，按人员键落行）。两类都在本口一并下发。

管理口径（按最合理管理员行为推导）：
- 纯只读：客服核对/投诉排查用，不提供删除或代改（收藏是用户个
  人数据，管理员代改没有正当场景）；本口没有任何写方法。
- 不套用户侧货架过滤（identity_gate）：门户口径是「标记不删、
  未绑定只是不展示、重绑后自动回来」，管理口要看的就是标记本
  身；条目已软删/不存在时如实标 item_deleted，不隐藏该行。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em

logger = logging.getLogger(__name__)

admin_user_favorites_router = APIRouter(
    prefix="/api/admin/users", tags=["管理后台·用户收藏查看"])


@admin_user_favorites_router.get("/{user_id}/favorites")
def get_user_favorites(
    user_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """某用户的全部收藏（作品＋人员），只读。用户不存在 404。"""
    user = (db.query(models.WebUser)
            .filter(models.WebUser.id == user_id).first())
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    fav_rows = (
        db.query(em.UserMediaData)
        .filter(em.UserMediaData.user_id == user_id,
                em.UserMediaData.is_favorite == True)  # noqa: E712
        .order_by(em.UserMediaData.updated_at.desc())
        .all())
    items_by_id = {}
    if fav_rows:
        items_by_id = {i.id: i for i in (
            db.query(em.MediaItem)
            .filter(em.MediaItem.id.in_({r.item_id for r in fav_rows}))
            .all())}
    items = []
    for row in fav_rows:
        item = items_by_id.get(row.item_id)
        items.append({
            "item_id": row.item_id,
            "guid": item.guid if item is not None else None,
            "name": item.name if item is not None else "",
            "type": item.item_type if item is not None else "",
            "year": item.production_year if item is not None else None,
            "rating": item.community_rating if item is not None else None,
            "item_deleted": item is None,
            "favorited_at": row.updated_at.isoformat()
            if row.updated_at else None,
        })

    person_rows = (
        db.query(em.UserPersonFavorite)
        .filter(em.UserPersonFavorite.user_id == user_id)
        .order_by(em.UserPersonFavorite.created_at.desc())
        .all())
    persons = [{
        "person_key": p.person_key,
        "name": p.name,
        "image": p.image,
        "person_tmdb_id": p.person_tmdb_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in person_rows]

    return {
        "user": {"id": user.id, "username": user.username},
        "items": items,
        "persons": persons,
        "counts": {"items": len(items), "persons": len(persons)},
    }
