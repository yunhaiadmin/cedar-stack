"""
管理后台 · 登录失败锁定设置（2026-11-18，F4/F8 补项）。

#286 只把五个阈值键注册进 config_schema 的 login_lockout 家族、
由 ratelimit.login_lockout_config 读、LoginLockoutGuard 在门户 /
Emby / 管理端三处登录入口执法，缺管理端读写口：本文件补
GET/PUT /api/admin/login-lockout/config。
- 读：直接复用 ratelimit.login_lockout_config（默认值已代入，
  与三处登录入口的生效口径一致）。
- 写：整批走 config_service 统一校验通道（schema="login_lockout"），
  端点层再收一层合理上下限，非法值一律 400、不落库；写操作记 AdminLog。
  保存后即时生效：守卫每次登录都重新读配置（缓存 TTL 内）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, models, ratelimit
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_login_lockout_router = APIRouter(
    prefix="/api/admin/login-lockout", tags=["管理后台·登录锁定设置"])

# 端点层合理上下限（schema 层只管 1/60 ~ 10**9 的宽口径）：
# 失败阈值超过 1000 次等于不限、窗口/锁长超过 1 天没有意义，收紧到 400。
MAX_FAILURES = 1000
MAX_WINDOW_SEC = 86400
MAX_LOCK_SEC = 86400

KEY_ENABLED = "login_lockout_enabled"
KEY_ACCOUNT_MAX = "login_lockout_account_max_failures"
KEY_IP_MAX = "login_lockout_ip_max_failures"
KEY_WINDOW = "login_lockout_window_sec"
KEY_LOCK = "login_lockout_lock_sec"


class LoginLockoutConfigRequest(BaseModel):
    enabled: bool = True
    account_max_failures: int = 10
    ip_max_failures: int = 30
    window_sec: int = 900
    lock_sec: int = 900


def _payload(db: Session) -> dict:
    """当前生效值（与登录入口同口径，窗口/锁长回整秒）。"""
    cfg = ratelimit.login_lockout_config(db)
    return {
        "enabled": cfg.enabled,
        "account_max_failures": cfg.account_max_failures,
        "ip_max_failures": cfg.ip_max_failures,
        "window_sec": int(cfg.window_sec),
        "lock_sec": int(cfg.lock_sec),
    }


@admin_login_lockout_router.get("/config")
def login_lockout_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """读取当前登录锁定设置（默认值已代入，与登录入口生效口径一致）。"""
    return _payload(db)


@admin_login_lockout_router.put("/config")
def login_lockout_update_config(
    request: LoginLockoutConfigRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存登录锁定设置：端点层先收上下限，再整批走 schema 校验，
    任一非法值 400、整批不落库；写时失效，登录入口下次读取即生效。"""
    if not 1 <= request.account_max_failures <= MAX_FAILURES:
        raise HTTPException(
            status_code=400,
            detail=f"account_max_failures 必须在 1~{MAX_FAILURES} 之间")
    if not 1 <= request.ip_max_failures <= MAX_FAILURES:
        raise HTTPException(
            status_code=400,
            detail=f"ip_max_failures 必须在 1~{MAX_FAILURES} 之间")
    if not 60 <= request.window_sec <= MAX_WINDOW_SEC:
        raise HTTPException(
            status_code=400,
            detail=f"window_sec 必须在 60~{MAX_WINDOW_SEC} 秒之间")
    if not 60 <= request.lock_sec <= MAX_LOCK_SEC:
        raise HTTPException(
            status_code=400,
            detail=f"lock_sec 必须在 60~{MAX_LOCK_SEC} 秒之间")
    values = {
        KEY_ENABLED: request.enabled,
        KEY_ACCOUNT_MAX: request.account_max_failures,
        KEY_IP_MAX: request.ip_max_failures,
        KEY_WINDOW: request.window_sec,
        KEY_LOCK: request.lock_sec,
    }
    try:
        saved = config_service.save_config(
            db, values, schema="login_lockout",
            descriptions={
                KEY_ENABLED: "登录失败锁定总开关（默认开）",
                KEY_ACCOUNT_MAX: "登录失败锁定：账号维度阈值（窗口内失败次数）",
                KEY_IP_MAX: "登录失败锁定：IP 维度阈值（窗口内失败次数）",
                KEY_WINDOW: "登录失败锁定：失败计数窗口（秒）",
                KEY_LOCK: "登录失败锁定：锁定持续时间（秒）",
            },
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = _payload(db)
    _audit(db, current_admin, "login_lockout_update_config", "system", None,
           {"keys": saved, "enabled": payload["enabled"]},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
