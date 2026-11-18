"""弹幕 API · 用户端接口 + 公开下发口（2026-11-18）。

用户端（登录态，/api/user/danmu）：
- GET    /token            状态：{exists, expires_at}，不回明文
- POST   /token            生成（已有有效 token 回现有状态，不重复生成）
- DELETE /token            删除即失效
- GET    /lines            线路列表（管理员配置，默认两条）
- GET    /address?line=id  为本人拼好的完整地址（唯一出现 token
  明文的响应）；未生成 409、线路不存在 404

公开下发（无登录态，token 在路径，/api/v1/danmu）：
- GET /{token}/ [{item_ref}[/raw]]  按 Danmu 插件约定回 bilibili
  风格 XML；token 无效 404；门槛不达标（无有效套餐/流量耗尽）
  按协议回空文档；未知条目回空文档。弹幕请求不扣流量：全程
  只读，不写任何计量表（校验见 backend/danmu_api.check_access）。

核心实现在 backend/danmu_api.py（唯一实现），本文件只做端点层。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from backend import danmu_api as core_api
from backend.api.user import get_current_user
from backend.database import get_db

logger = logging.getLogger(__name__)

user_router = APIRouter(prefix="/api/user/danmu", tags=["danmu-api"])
public_router = APIRouter(tags=["danmu-api-public"])

_XML_MEDIA = "text/xml; charset=utf-8"


# ------------------------------------------------------------ 用户端


@user_router.get("/token")
def danmu_token_status(user=Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """弹幕 Token 状态：是否存在有效 token 及其到期时刻（不回明文）。"""
    return core_api.token_status(db, user.id)


@user_router.post("/token")
def danmu_token_generate(user=Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """生成弹幕 Token（一键，无强因子前置）；已有有效 token 不重复生成。"""
    before = core_api.token_status(db, user.id)
    core_api.generate_token(db, user.id)
    after = core_api.token_status(db, user.id)
    return {**after, "created": not before["exists"]}


@user_router.delete("/token")
def danmu_token_delete(user=Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """删除弹幕 Token：删除即失效，旧地址随之不可用。"""
    core_api.delete_token(db, user.id)
    return {"exists": False, "expires_at": None}


@user_router.get("/lines")
def danmu_lines(user=Depends(get_current_user),
                db: Session = Depends(get_db)):
    """弹幕线路列表：{id, name, note, host}，顺序即展示顺序。"""
    return {"lines": core_api.get_lines(db)}


@user_router.get("/address")
def danmu_address(line: str = Query(...),
                  user=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """为本人拼完整弹幕地址：线路只换 host，路径与 token 不变。"""
    lines = core_api.get_lines(db)
    if not any(ln["id"] == line for ln in lines):
        raise HTTPException(status_code=404, detail="线路不存在")
    address = core_api.build_address(db, user.id, line)
    if address is None:
        raise HTTPException(status_code=409, detail="尚未生成弹幕 Token")
    return {"line": line, "address": address}


# ------------------------------------------------------------ 公开下发


def _serve(db: Session, token: str, item_ref: str | None) -> Response:
    row = core_api.find_token_row(db, token)
    if row is None:
        raise HTTPException(status_code=404, detail="弹幕 Token 不存在或已失效")
    try:
        allowed, reason = core_api.check_access(db, row.user_id)
    except Exception:  # 门槛判定异常不许把内容漏出去：记日志、回空文档
        logger.exception("danmu gate check failed (owner=%s)", row.user_id)
        allowed, reason = False, "gate_error"
    if not allowed:
        logger.debug("danmu delivery gated: reason=%s", reason)
        return Response(content=core_api.empty_xml(), media_type=_XML_MEDIA)
    if not item_ref:
        return Response(content=core_api.empty_xml(), media_type=_XML_MEDIA)
    item = core_api._resolve_item(db, item_ref)
    if item is None:
        return Response(content=core_api.empty_xml(), media_type=_XML_MEDIA)
    return Response(
        content=core_api.render_item_danmaku_xml(db, item, row.user_id),
        media_type=_XML_MEDIA)


@public_router.get("/api/v1/danmu/{token}")
@public_router.get("/api/v1/danmu/{token}/")
def danmu_public_root(token: str, id: str | None = Query(None),
                      db: Session = Depends(get_db)):
    """弹幕地址本体：无条目段回空文档；个别客户端用 ?id= 拼条目，一并承接。"""
    return _serve(db, token, id)


@public_router.get("/api/v1/danmu/{token}/{item_ref}")
@public_router.get("/api/v1/danmu/{token}/{item_ref}/raw")
def danmu_public_item(token: str, item_ref: str,
                      db: Session = Depends(get_db)):
    """按条目取弹幕（Danmu 插件约定：地址后拼条目 Id，/raw 为原始 XML 形态）。"""
    return _serve(db, token, item_ref)
