# -*- coding: utf-8 -*-
"""用户端家庭子账号 API（2026-11-15）：主账号管理子账号的唯一入口。

- GET    /api/user/sub-accounts：管理区数据（backend.sub_accounts
  的 management_payload：主账号看上限/清单，子账号只看到归属说明）；
- POST   /api/user/sub-accounts：创建一个子账号（校验全在服务层：
  调用者不是子账号、主账号订阅有效、套餐 sub_account_limit 未满、
  用户名规则与开放注册一致）；
- DELETE /api/user/sub-accounts/{id}：删除 = 软禁用（记录保留、
  登录立即失效），只能删自己的子账号。

子账号不走开放注册、不经本文件以外的任何建号口。业务口径见
backend/sub_accounts.py 模块 docstring（推导口径的唯一出处）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models
from backend import sub_accounts as sub_accounts_service
from backend.api.user import get_current_user
from backend.database import get_db
from backend.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)

sub_accounts_router = APIRouter(prefix="/api/user", tags=["用户端·子账号"])

#: 服务层错误 code → HTTP 状态码（资格类 403、撞名 409、格式 400）。
_STATUS_BY_CODE = {
    "is_sub_account": 403,
    "no_active_subscription": 403,
    "limit_reached": 403,
    "username_taken": 409,
    "bad_username": 400,
    "bad_password": 400,
    "not_found": 404,
}


def _error_response(exc: sub_accounts_service.SubAccountError) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS_BY_CODE.get(exc.code, 400),
        content={"detail": str(exc), "code": exc.code})


class SubAccountCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


@sub_accounts_router.get("/sub-accounts")
def get_sub_accounts(current_user: models.WebUser = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    return sub_accounts_service.management_payload(db, current_user)


@sub_accounts_router.post("/sub-accounts", status_code=201)
def create_sub_account(req: SubAccountCreateRequest,
                       current_user: models.WebUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    allowed, _ = check_rate_limit(
        f"sub_account_create:{current_user.id}", 10, 60)
    if not allowed:
        return JSONResponse(status_code=429,
                            content={"detail": "操作太频繁，请稍后再试"})
    try:
        user = sub_accounts_service.create_sub_account(
            db, current_user, req.username, req.password)
    except sub_accounts_service.SubAccountError as exc:
        return _error_response(exc)
    return {
        "ok": True,
        "item": {
            "id": user.id,
            "username": user.username,
            "is_active": bool(user.is_active),
            "created_at": (user.created_at.isoformat()
                           if user.created_at else None),
        },
    }


@sub_accounts_router.delete("/sub-accounts/{sub_user_id}")
def delete_sub_account(sub_user_id: int,
                       current_user: models.WebUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    try:
        child = sub_accounts_service.disable_sub_account(
            db, current_user, sub_user_id)
    except sub_accounts_service.SubAccountError as exc:
        return _error_response(exc)
    return {"ok": True, "id": child.id, "is_active": bool(child.is_active)}
