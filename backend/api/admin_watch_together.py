# -*- coding: utf-8 -*-
"""管理后台 · 一起看管理（后台大改造二期缺口）。

口径（按前端形态 + 最合理管理员行为推导）：
- 房间列表 / 详情只读：房间记录存 RoomStore（Redis ``wt:room:*``
  优先、进程内字典降级），成员名单来自本进程连接表。页面只看
  控制面信息——条目、房主、成员、播放态（播放中/进度/倍速）；
  **绝不下发流地址、play_sign 或任何令牌**（一起看红线，房间
  本来就不承载这些，管理面更不许绕过门禁去拼）。
- 强制关房是唯一的写动作：给全房广播 ``wt.ended``
  （reason=admin_closed，与房主解散的 dissolved 区分）、摘连接、
  删记录，并写 AdminLog 审计（action=watch_together_force_close）。
  关房只终止同步会话，不碰成员各自的播放页与凭证。
- 成员名单口径：连接表是进程内的，记录在而连接不在本进程时
  （如服务刚重启）成员显示为空，不编造。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.database import get_db
from backend.watch_together import watch_together_hub

logger = logging.getLogger(__name__)

admin_watch_together_router = APIRouter(
    prefix="/api/admin/watch-together", tags=["管理后台·一起看"])


def _item_names(db: Session, guids: set) -> dict:
    """条目 guid → 片名（查不到留空，前端回退显示 guid）。"""
    if not guids:
        return {}
    from backend.emby_server import models as em

    rows = db.query(em.MediaItem).filter(
        em.MediaItem.guid.in_(list(guids))).all()
    return {row.guid: row.name or "" for row in rows}


def _user_names(db: Session, user_ids: set) -> dict:
    """用户 id → 用户名（房主与成员显示用，查不到留空）。"""
    if not user_ids:
        return {}
    rows = db.query(models.WebUser).filter(
        models.WebUser.id.in_(list(user_ids))).all()
    return {row.id: row.username or "" for row in rows}


def _decorate(db: Session, view: dict) -> dict:
    """给管理视图补片名 / 房主名 / 成员名（原视图已带 username 时不覆盖）。"""
    names = _item_names(db, {view.get("item_id")} - {None, ""})
    view["item_name"] = names.get(view.get("item_id"), "")
    ids = {view.get("host_id")} - {None}
    ids |= {m.get("user_id") for m in view.get("members", [])} - {None}
    usernames = _user_names(db, ids)
    view["host_name"] = usernames.get(view.get("host_id"), "")
    for member in view.get("members", []):
        if not member.get("username"):
            member["username"] = usernames.get(member.get("user_id"), "")
    return view


@admin_watch_together_router.get("/rooms")
async def list_watch_together_rooms(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """一起看房间列表：全部活房间，按创建时间倒序。"""
    views = await watch_together_hub.list_rooms_admin()
    names = _item_names(
        db, {v.get("item_id") for v in views} - {None, ""})
    host_ids = {v.get("host_id") for v in views} - {None}
    host_ids |= {m.get("user_id") for v in views
                 for m in v.get("members", [])} - {None}
    usernames = _user_names(db, host_ids)
    for view in views:
        view["item_name"] = names.get(view.get("item_id"), "")
        view["host_name"] = usernames.get(view.get("host_id"), "")
        for member in view.get("members", []):
            if not member.get("username"):
                member["username"] = usernames.get(
                    member.get("user_id"), "")
    return {"total": len(views), "items": views}


@admin_watch_together_router.get("/rooms/{code}")
async def get_watch_together_room(
    code: str,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """一起看房间详情：成员名单 + 同步参数（播放态外推进度/倍速）。"""
    view = await watch_together_hub.get_room_admin(code)
    if view is None:
        raise HTTPException(status_code=404, detail="房间不存在或已结束")
    return _decorate(db, view)


@admin_watch_together_router.post("/rooms/{code}/close")
async def close_watch_together_room(
    code: str,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """强制关房：广播结束、摘连接、删记录，写 AdminLog 审计。"""
    admin_id = current_admin.id
    view = await watch_together_hub.force_close_room(code)
    if view is None:
        raise HTTPException(status_code=404, detail="房间不存在或已结束")

    def _audit_and_commit() -> None:
        # 同步 DB 写必须走线程池：async 路由里直接 commit() 会阻塞
        # 事件循环（阻塞路由契约 check_blocking_routes.py）。
        _audit(db, admin_id, "watch_together_force_close",
               "watch_together_room", None,
               {"code": view["code"], "item_id": view.get("item_id"),
                "host_id": view.get("host_id"),
                "member_count": view.get("member_count")},
               ip=raw_request.client.host if raw_request.client else None)
        db.commit()

    await run_in_threadpool(_audit_and_commit)
    return {"ok": True, "code": view["code"],
            "member_count": view.get("member_count")}
