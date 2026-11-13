"""账号安全三件套 API：TOTP / Passkey / 平台 API Key（用户端，前缀 /api/user/security）。

形态照 UHDNow 账号安全页：登录与恢复区的动态验证码行、Passkeys 区块、
活跃会话区的「创建 API Key」（创建前置＝已绑 TOTP 或 Passkey，后端硬校验）。
纯逻辑在 backend/account_security.py；登录链的接入（密码登录带码校验、
Passkey 登录、login-factors）在 backend/api/emby_portal.py。

另带平台只读路由（前缀 /api/platform）：用平台 API Key（X-API-Key 头）
调用的账户信息只读接口，是平台 Key 的最小可用集；新接口接入点见文末注释。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import account_security as sec
from backend import models
from backend.api.emby_portal import get_current_user_jwt
from backend.database import get_db
from backend.ratelimit import check_rate_limit
from backend.security import verify_password

security_router = APIRouter(prefix="/api/user/security", tags=["用户端-账号安全"])
platform_router = APIRouter(prefix="/api/platform", tags=["平台 API"])


def _rate_guard(request: Request, user_id: int, action: str, limit: int = 10) -> None:
    allowed, _retry = check_rate_limit(f"acctsec:{action}:{user_id}", limit, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")


# ==================== Schemas ====================

class TotpEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TotpDisableRequest(BaseModel):
    password: str


class PasskeyRegisterVerifyRequest(BaseModel):
    credential: dict
    challenge_token: str
    device_name: Optional[str] = None


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="", max_length=100)


# ==================== 序列化 ====================

def _passkey_out(row: models.UserPasskey) -> dict:
    return {
        "id": row.id,
        "device_name": row.device_name,
        "transports": row.transports,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _api_key_out(row: models.UserPlatformApiKey) -> dict:
    return {
        "id": row.id,
        "key_id": row.key_id,
        "name": row.name,
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


# ==================== 总览 ====================

@security_router.get("/overview")
def security_overview(
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """账号安全页一次拿全：TOTP 状态 / Passkey 列表与上限 / 平台 Key 列表与可创建标记。"""
    passkeys = sec.list_passkeys(db, current_user.id)
    keys = sec.list_platform_api_keys(db, current_user.id)
    return {
        "totp": {"enabled": sec.totp_enabled_for(db, current_user.id)},
        "passkeys": {
            "max": sec.passkey_max_per_user(db),
            "items": [_passkey_out(r) for r in passkeys],
        },
        "api_keys": {
            "can_create": sec.has_strong_factor(db, current_user.id),
            "validity_days": sec.platform_api_key_validity_days(db),
            "items": [_api_key_out(r) for r in keys],
        },
        "has_recovery_email": bool(current_user.email),
    }


# ==================== TOTP ====================

@security_router.post("/totp/setup")
def totp_setup(
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """生成待确认密钥，返回明文密钥 + otpauth URI（前端渲染二维码）。"""
    _rate_guard(request, current_user.id, "totp_setup")
    try:
        secret, uri = sec.start_totp_setup(db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"secret": secret, "otpauth_uri": uri}


@security_router.post("/totp/enable")
def totp_enable(
    req: TotpEnableRequest,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """输一次码确认启用。对不上不启用，防「扫了码但没保存成功」被锁在门外。"""
    _rate_guard(request, current_user.id, "totp_enable")
    if not sec.confirm_totp_setup(db, current_user.id, req.code):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期，请重新输入")
    return {"enabled": True}


@security_router.post("/totp/disable")
def totp_disable(
    req: TotpDisableRequest,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """关闭 TOTP：必须验登录密码（恢复邮箱渠道为后续扩展，本期先走密码）。"""
    _rate_guard(request, current_user.id, "totp_disable", limit=5)
    if not verify_password(req.password, current_user.password_hash):
        raise HTTPException(status_code=403, detail="密码不正确，无法关闭动态验证码")
    sec.disable_totp(db, current_user.id)
    return {"enabled": False}


# ==================== Passkey ====================

@security_router.post("/passkeys/register/options")
def passkey_register_options(
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    _rate_guard(request, current_user.id, "passkey_options")
    if len(sec.list_passkeys(db, current_user.id)) >= sec.passkey_max_per_user(db):
        raise HTTPException(
            status_code=400,
            detail=f"Passkey 数量已达上限（{sec.passkey_max_per_user(db)} 枚），请先删除一枚",
        )
    rp_id, _origin = sec.rp_context(request)
    options, token = sec.registration_options(db, current_user, rp_id)
    return {"options": options, "challenge_token": token}


@security_router.post("/passkeys/register/verify")
def passkey_register_verify(
    req: PasskeyRegisterVerifyRequest,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    _rate_guard(request, current_user.id, "passkey_verify")
    rp_id, origin = sec.rp_context(request)
    try:
        row = sec.verify_and_store_registration(
            db, current_user, req.credential, req.challenge_token,
            rp_id, origin, device_name=req.device_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _passkey_out(row)


@security_router.delete("/passkeys/{passkey_id}")
def passkey_delete(
    passkey_id: int,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    _rate_guard(request, current_user.id, "passkey_delete")
    row = (
        db.query(models.UserPasskey)
        .filter(
            models.UserPasskey.id == passkey_id,
            models.UserPasskey.user_id == current_user.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Passkey 不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True}


# ==================== 平台 API Key ====================

@security_router.post("/api-keys")
def api_key_create(
    req: ApiKeyCreateRequest,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """创建平台 Key：前置未满足（没绑 TOTP/Passkey）直接 403，明文只回这一次。"""
    _rate_guard(request, current_user.id, "apikey_create", limit=5)
    try:
        row, plaintext = sec.create_platform_api_key(db, current_user, req.name)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    out = _api_key_out(row)
    out["plaintext"] = plaintext  # 只显示一次，之后任何接口都不再返回
    return out


@security_router.get("/api-keys")
def api_key_list(
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    keys = sec.list_platform_api_keys(db, current_user.id)
    return {"items": [_api_key_out(r) for r in keys]}


@security_router.delete("/api-keys/{key_row_id}")
def api_key_revoke(
    key_row_id: int,
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    _rate_guard(request, current_user.id, "apikey_revoke")
    if not sec.revoke_platform_api_key(db, current_user.id, key_row_id):
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"revoked": True}


# ==================== 平台 API（X-API-Key 鉴权，最小集） ====================
# 平台 Key 能调的接口从这一组长出去：新增接口统一走 get_platform_key_user
# 依赖（校验 + 更新 last_used_at 一处收口），并在本文件内追加路由，
# 不要在别的 router 里另写一套 Key 校验。

def get_platform_key_user(
    request: Request,
    db: Session = Depends(get_db),
) -> models.WebUser:
    """平台 Key 鉴权依赖：X-API-Key → Key 行 → 归属用户。"""
    plaintext = (request.headers.get("X-API-Key") or "").strip()
    row = sec.verify_platform_api_key(db, plaintext) if plaintext else None
    if row is None:
        raise HTTPException(status_code=401, detail="API Key 无效、已过期或已吊销")
    user = db.query(models.WebUser).filter(models.WebUser.id == row.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="API Key 归属账号不存在或已禁用")
    request.state.platform_api_key = row
    return user


@platform_router.get("/me")
def platform_me(
    request: Request,
    current_user: models.WebUser = Depends(get_platform_key_user),
):
    """账户信息只读（平台 Key 最小可用集）：证明 Key 链路通，字段对齐 /auth/me 的基础子集。"""
    key_row: models.UserPlatformApiKey = request.state.platform_api_key
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_active": bool(current_user.is_active),
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "api_key": {
            "key_id": key_row.key_id,
            "name": key_row.name,
            "expires_at": key_row.expires_at.isoformat() if key_row.expires_at else None,
        },
    }
