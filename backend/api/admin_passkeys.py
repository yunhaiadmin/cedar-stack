"""
管理后台 · 用户 Passkey 管理（后台大改造二期缺口 · 项 3）。

用户侧已有自助管理（backend/api/account_security.py：注册/查看/删除自己的
Passkey），但账号恢复场景——用户设备全丢、登不进来——只能由管理员代为
吊销。本文件补管理端口：

- GET    /api/admin/users/{user_id}/passkeys            按用户列凭据
- DELETE /api/admin/users/{user_id}/passkeys/{passkey_id}  吊销单个凭据

安全口径：
- 列表只回展示字段（设备名/传输方式/签名计数/创建与最近使用时间），
  不回 credential_id 与 public_key——管理员处置不需要凭据材料本身；
- 吊销写 AdminLog（action=user_passkey_revoke），details 只记凭据 id 与
  设备名，不记凭据材料；
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

admin_passkeys_router = APIRouter(
    prefix="/api/admin/users", tags=["管理后台·用户Passkey"])


def _get_user_or_404(db: Session, user_id: int) -> models.WebUser:
    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _passkey_admin_out(row: models.UserPasskey) -> dict:
    """管理端序列化：与用户侧 _passkey_out 同展示字段，另加 sign_count
    （克隆检测计数，管理员判断凭据是否活跃时有用）；不含凭据材料。"""
    return {
        "id": row.id,
        "device_name": row.device_name,
        "transports": row.transports,
        "sign_count": row.sign_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


@admin_passkeys_router.get("/{user_id}/passkeys")
def admin_list_user_passkeys(
    user_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """按用户列出 Passkey 凭据（账号恢复处置前的核对面）。"""
    user = _get_user_or_404(db, user_id)
    rows = sec.list_passkeys(db, user_id)
    return {
        "user_id": user.id,
        "username": user.username,
        "max_per_user": sec.passkey_max_per_user(db),
        "items": [_passkey_admin_out(r) for r in rows],
    }


@admin_passkeys_router.delete("/{user_id}/passkeys/{passkey_id}")
def admin_revoke_user_passkey(
    user_id: int,
    passkey_id: int,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """吊销单个凭据：凭据必须属于该用户，否则 404（不区分不存在/不归属，
    避免凭据 id 枚举）。吊销后该设备立即无法再用此 Passkey 登录。"""
    _get_user_or_404(db, user_id)
    row = (
        db.query(models.UserPasskey)
        .filter(
            models.UserPasskey.id == passkey_id,
            models.UserPasskey.user_id == user_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    device_name = row.device_name
    db.delete(row)
    _audit(
        db, current_admin, "user_passkey_revoke", "web_user", user_id,
        {"passkey_id": passkey_id, "device_name": device_name},
        ip=raw_request.client.host if raw_request.client else None,
    )
    db.commit()
    return {"success": True}
