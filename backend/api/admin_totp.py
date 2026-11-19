"""
管理后台 · 用户 TOTP 管理（后台大改造二期缺口 · 项 4）。

用户侧已有自助流程（backend/api/account_security.py：setup/enable/disable，
disable 须先验密码），但用户把验证器设备弄丢时连密码校验后的自助关闭都
走不通，只能由管理员强制重置。本文件补管理端口：

- GET  /api/admin/users/{user_id}/totp         查看 TOTP 状态
- POST /api/admin/users/{user_id}/totp/reset   强制重置（整行删除）

安全口径：
- 状态口只回「是否已启用 / 是否有待确认的设置 / 时间戳」，**绝不回
  secret_sealed**（密封值也不出接口，管理员处置不需要它）；
- 强制重置 = 删除 user_totp 行（与用户侧 disable_totp 同语义：密钥密封值
  一并销毁），用户之后可从零重新设置；写 AdminLog
  （action=user_totp_reset），details 只记重置前是否已启用；
- 鉴权走 get_current_admin 集中判定（viewer 只读自动被拦、operator 可写）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import account_security as sec
from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_totp_router = APIRouter(
    prefix="/api/admin/users", tags=["管理后台·用户TOTP"])


def _get_user_or_404(db: Session, user_id: int) -> models.WebUser:
    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _totp_status_out(user: models.WebUser, row: models.UserTotp | None) -> dict:
    """状态序列化：只有启用态与时间戳，没有任何密钥材料字段。"""
    return {
        "user_id": user.id,
        "username": user.username,
        "enabled": bool(row and row.enabled),
        # 已生成密钥但还没输对一次码确认 = 设置进行中，登录不要求验证码
        "pending_setup": bool(row and not row.enabled),
        "confirmed_at": (
            row.confirmed_at.isoformat() if row and row.confirmed_at else None),
        "created_at": (
            row.created_at.isoformat() if row and row.created_at else None),
        "updated_at": (
            row.updated_at.isoformat() if row and row.updated_at else None),
    }


@admin_totp_router.get("/{user_id}/totp")
def admin_get_user_totp(
    user_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """查看某用户的 TOTP 状态（不含密钥材料）。"""
    user = _get_user_or_404(db, user_id)
    return _totp_status_out(user, sec.get_totp_row(db, user_id))


@admin_totp_router.post("/{user_id}/totp/reset")
def admin_reset_user_totp(
    user_id: int,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """强制重置：删除该用户的 TOTP 绑定行，用户可重新设置。

    没有任何绑定行时 404（无可重置之物，不写审计）。删除与审计同一事务。
    """
    _get_user_or_404(db, user_id)
    row = sec.get_totp_row(db, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该用户未设置动态验证码")
    was_enabled = bool(row.enabled)
    db.delete(row)
    _audit(
        db, current_admin, "user_totp_reset", "web_user", user_id,
        {"was_enabled": was_enabled},
        ip=raw_request.client.host if raw_request.client else None,
    )
    db.commit()
    return {"success": True}
