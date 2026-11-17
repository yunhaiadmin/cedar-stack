"""
管理后台 API · 管理员免登录链接（2026-11-17 用户亲点）

站长凭一条专属链接直进管理后台，不输账号密码：

- ``GET  /api/admin/magic-login?key=<token>``：链接入口（免鉴权）。校验通过
  后签发 60 秒一次性交换码，302 到 ``/admin/#magic=<code>``；失败一律 404，
  不区分 key 不存在 / 已停用 / 被限流，不给枚举留口径。没有生成过任何链接
  时本端点完全惰性（查无此行即 404，与不存在等同）。
- ``POST /api/admin/auth/magic-exchange``：前端拿交换码换正式会话。换出的
  access / refresh 与密码登录**同一签发函数、同一过期口径**（见
  ``_issue_admin_session``），不另起第二套鉴权；交换码单次使用、60 秒过期。
- ``GET/POST /api/admin/auth/magic-link[...]``：当前管理员管理自己的链接
  （状态 / 生成 / 停用），走 ``get_current_admin`` 同一鉴权依赖；生成与停用
  是写操作，且属「给自己发凭据」，与 API Key 同级收进超管名单
  （``admin_roles.SUPER_ONLY_PREFIXES``）。

令牌只存 SHA256（与 ``password_reset_tokens`` 同口径），明文只在生成那一刻
返回一次；链接绑定生成者本人，免登录进入的就是这个账号。
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import admin_roles, models
from backend.api.admin_core import _audit, admin_router, get_current_admin
from backend.authlog import client_ip as log_ip, record_event, user_agent
from backend.database import get_db
from backend.integrations import store as config_store
from backend.ratelimit import check_rate_limit, client_ip
from backend.security import create_access_token, create_refresh_token

# 链接令牌与交换码都是 32 字节密码学随机（token_urlsafe 编码后 43 字符）
_TOKEN_BYTES = 32
# 交换码有效期：够浏览器完成一次跳转 + 前端首屏换票，不给截获留窗口
_EXCHANGE_TTL_SECONDS = 60
# 入口与交换口同档限流：5 次 / 分钟 / IP（复用现行滑动窗口限流器）
_RATE_LIMIT = 5
_RATE_WINDOW = 60


def _sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_link_by_key(db: Session, key: str) -> models.AdminMagicLink | None:
    """按 key 找启用中的链接行：哈希索引定位后仍走常数时间比较再确认一遍。"""
    if not key:
        return None
    digest = _sha256_hex(key.strip())
    link = db.query(models.AdminMagicLink).filter(
        models.AdminMagicLink.token_hash == digest,
    ).first()
    if link is None or not link.is_active:
        return None
    if not hmac.compare_digest(link.token_hash, digest):
        return None
    return link


def _issue_admin_session(user: models.WebUser) -> dict:
    """签发与密码登录一字不差的会话响应（同一签发函数、同一过期口径）。

    响应形状与 ``admin_auth.admin_login`` 的返回值保持一致：管理端前端拿到
    后走与密码登录完全相同的 ``setSession`` 路径，不存在第二种会话。
    """
    access = create_access_token(user.id, {"username": user.username, "staff": True},
                                 token_version=user.token_version or 0)
    refresh = create_refresh_token(user.id, token_version=user.token_version or 0)
    perms = admin_roles.permission_payload(user)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "is_staff": True,
            "admin_role": perms["role"],
            "role_label": perms["role_label"],
            "can_write": perms["can_write"],
            "is_super": perms["is_super"],
        },
    }


# ==================== 链接入口（免鉴权） ====================

@admin_router.get("/magic-login")
def admin_magic_login(
    http_request: Request,
    key: str = "",
    db: Session = Depends(get_db),
):
    """免登录链接入口：校验 key → 发一次性交换码 → 302 进管理后台。

    失败一律 404 且不写区分性文案：key 不存在、已停用、账号被禁、被限流，
    对外完全一个样。失败尝试仍记登录日志（内部审计可见，对外不泄露）。
    """
    ip = client_ip(http_request)
    allowed, _retry = check_rate_limit(f"admin_magic_login:{ip}", _RATE_LIMIT, _RATE_WINDOW)
    link = _find_link_by_key(db, key) if allowed else None
    user = None
    if link is not None:
        candidate = db.query(models.WebUser).filter(
            models.WebUser.id == link.admin_user_id,
        ).first()
        # 账号在生成链接之后被停权 / 禁用：链接随之失效，同样回 404
        if candidate is not None and candidate.is_staff and candidate.is_active:
            user = candidate
    if link is None or user is None:
        record_event(
            db, username=None, user_id=link.admin_user_id if link else None,
            ip=log_ip(http_request), agent=user_agent(http_request),
            success=False, reason="admin_magic_login_failed",
            detail="免登录链接校验未通过",
        )
        raise HTTPException(status_code=404, detail="Not Found")

    code = secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now()
    db.add(models.AdminMagicExchangeCode(
        admin_user_id=user.id,
        code_hash=_sha256_hex(code),
        expires_at=now + timedelta(seconds=_EXCHANGE_TTL_SECONDS),
        request_ip=ip,
    ))
    db.commit()
    return RedirectResponse(url=f"/admin/#magic={code}", status_code=302)


# ==================== 交换码换会话（免鉴权） ====================

class MagicExchangeRequest(BaseModel):
    code: str


@admin_router.post("/auth/magic-exchange")
def admin_magic_exchange(
    http_request: Request,
    request: MagicExchangeRequest,
    db: Session = Depends(get_db),
):
    """一次性交换码换正式会话：与密码登录同一签发口径、同一响应形状。

    失败一律统一 401（与 tg 免密登录同口径），不区分码不存在 / 已用 / 已过期。
    成功时：消费交换码、刷新链接 last_used_at、更新 last_login_at、写审计
    （AdminLog）与登录日志，四件事同一事务口径落盘后再签发令牌。
    """
    ip = client_ip(http_request)
    allowed, _retry = check_rate_limit(
        f"admin_magic_exchange:{ip}", _RATE_LIMIT, _RATE_WINDOW)
    row = None
    if allowed and request.code:
        digest = _sha256_hex(request.code.strip())
        candidate = db.query(models.AdminMagicExchangeCode).filter(
            models.AdminMagicExchangeCode.code_hash == digest,
            models.AdminMagicExchangeCode.used_at.is_(None),
        ).first()
        if candidate is not None and hmac.compare_digest(candidate.code_hash, digest):
            row = candidate
    user = None
    if row is not None and row.expires_at > datetime.now():
        candidate = db.query(models.WebUser).filter(
            models.WebUser.id == row.admin_user_id,
        ).first()
        if candidate is not None and candidate.is_staff and candidate.is_active:
            user = candidate
    if row is None or user is None:
        record_event(
            db, username=None, user_id=row.admin_user_id if row else None,
            ip=log_ip(http_request), agent=user_agent(http_request),
            success=False, reason="admin_magic_login_failed",
            detail="免登录交换码校验未通过",
        )
        raise HTTPException(status_code=401, detail="登录链接无效或已过期")

    now = datetime.now()
    row.used_at = now
    link = db.query(models.AdminMagicLink).filter(
        models.AdminMagicLink.admin_user_id == user.id,
    ).first()
    if link is not None:
        link.last_used_at = now
    user.last_login_at = now
    _audit(db, user.id, "admin_magic_login", "self", user.id,
           details={"via": "magic_link"}, ip=ip)
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id, ip=log_ip(http_request),
        agent=user_agent(http_request), success=True, reason="admin_magic_login",
    )
    return _issue_admin_session(user)


# ==================== 链接管理（当前管理员管自己那条） ====================

def _link_status(db: Session, admin: models.WebUser) -> dict:
    link = db.query(models.AdminMagicLink).filter(
        models.AdminMagicLink.admin_user_id == admin.id,
    ).first()
    if link is None:
        state = "none"
    elif link.is_active:
        state = "active"
    else:
        state = "disabled"
    return {
        "state": state,
        "username": admin.username,
        "created_at": link.created_at.isoformat() if link and link.created_at else None,
        "last_used_at": link.last_used_at.isoformat() if link and link.last_used_at else None,
    }


@admin_router.get("/auth/magic-link")
def admin_magic_link_status(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """自己那条免登录链接的状态（永不回显明文与哈希）。"""
    return _link_status(db, current_admin)


@admin_router.post("/auth/magic-link/generate")
def admin_magic_link_generate(
    http_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """生成 / 重新生成自己的免登录链接：明文只在本响应里出现这一次。

    重新生成即换哈希，旧链接立即失效。链接拼装与找回密码邮件同口径：
    站点地址取 SystemConfig 的 site_url（缺失时回落 site_base_url），
    都没配时只回相对路径，前端按当前源补全展示。
    """
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now()
    link = db.query(models.AdminMagicLink).filter(
        models.AdminMagicLink.admin_user_id == current_admin.id,
    ).first()
    if link is None:
        link = models.AdminMagicLink(admin_user_id=current_admin.id)
        db.add(link)
    link.token_hash = _sha256_hex(raw)
    link.is_active = True
    link.created_at = now
    link.last_used_at = None
    _audit(db, current_admin.id, "admin_magic_link_generate", "self",
           current_admin.id, ip=client_ip(http_request))
    db.commit()

    base = (
        config_store.read_value(db, "site_url", "")
        or config_store.read_value(db, "site_base_url", "")
    ).rstrip("/")
    return {"url": f"{base}/api/admin/magic-login?key={raw}"}


@admin_router.post("/auth/magic-link/disable")
def admin_magic_link_disable(
    http_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """停用自己的免登录链接（幂等：没有链接时同样回成功）。"""
    link = db.query(models.AdminMagicLink).filter(
        models.AdminMagicLink.admin_user_id == current_admin.id,
    ).first()
    if link is not None and link.is_active:
        link.is_active = False
        _audit(db, current_admin.id, "admin_magic_link_disable", "self",
               current_admin.id, ip=client_ip(http_request))
        db.commit()
    return {"success": True}
