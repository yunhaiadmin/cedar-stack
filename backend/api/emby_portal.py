"""用户门户认证 API：注册 / 登录 / JWT 刷新 / 当前用户 / 改密

端点（前缀 /api/user/auth）：
- POST /register          注册新用户，返回 access_token + refresh_token + user
- POST /login             用户名密码登录，返回 access_token + refresh_token + user
- POST /refresh           用 refresh_token 换新 access_token
- GET  /me                当前用户信息（JWT 鉴权）
- POST /logout            登出（无状态 JWT：客户端清除 token 即可）
- POST /change-password   修改密码（JWT 鉴权，需验证旧密码）

邮箱找回密码在独立前缀的 password_reset_router（同文件）：
- POST /api/auth/password-reset/request   申请重置邮件（防枚举统一响应）
- POST /api/auth/password-reset/confirm   凭邮件令牌设置新密码
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models, register_channel
from backend.usernames import find_users_by_username_ci, username_conflict
from backend.integrations.store import read_value as _store_read_value
from backend.integrations.store import switch_env_value as _switch_env_value
from backend.db_retry import commit_with_retry
from backend.authlog import REASONS as AUTHLOG_REASONS, client_ip as log_ip, record_event, user_agent
from backend import share_guard
from backend.database import get_db
from backend.devices import device_limit
from backend.emby_server.auth import ensure_emby_credentials
from backend.subscriptions import (
    download_allowed,
    has_active_subscription,
    is_free_realm,
    realm_access_note,
    realm_requires_subscription,
)
from backend.ratelimit import (
    check_rate_limit,
    client_ip,
    login_lockout_check,
    login_lockout_config,
    login_lockout_record_failure,
    login_lockout_record_success,
    reset_rate_limit,
)
# 邮件（能力中心）：找回密码走与站内通知同一个 EmailChannel，不另造发信实现
from backend.integrations import mail as integrations_mail
from backend.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
    decode_token,
    DUMMY_PASSWORD_HASH,
    hash_password,
    resolve_jwt_user_id,
    verify_password,
)

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/user/auth", tags=["用户端-认证"])

# 邮箱找回密码（前端契约路径 /api/auth/password-reset/*，与门户 auth 前缀不同，
# 是 2026-11-13 立项时定死的对接路径，不要随手并进上面的前缀）
password_reset_router = APIRouter(prefix="/api/auth", tags=["用户端-认证"])

bearer_scheme = HTTPBearer(auto_error=False)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


# ==================== Schemas ====================

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    email: str | None = None
    invitation_code: str | None = None  # 邀请码（选填，双向奖励+返利绑定）
    trial_code: str | None = None  # 福利试用码（选填，注册成功后凭码领试用）


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = None  # 动态验证码（账号已启用 TOTP 时必填）


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    # G-53（2026-11-18 定案）：新密码 8–64 位，与密码行副文一致；
    # 注册/找回重置的下限不在本批范围，未动。
    new_password: str = Field(..., min_length=8, max_length=64)
    # 有恢复邮箱时必填：POST /change-password/send-code 发到现邮箱的 6 位码
    email_code: str | None = Field(default=None, max_length=6)


class WhitelistIdentityOut(BaseModel):
    # 白名单专属身份：创世编号（按成为白名单的先后，永不变更/复用）
    # + 固定专属称号 + 展示标签（No.N）；非白名单为 None，整个元素不渲染
    serial: int
    title: str
    label: str


class PoolIdentityOut(BaseModel):
    # 贡献池专属身份（goal-33 PR-6）：用户名下有 active 池成员行
    # 即出固定称号「片库贡献者」；状态驱动、无编号（资历看成员
    # joined_at），非在池贡献者为 None，整个元素不渲染
    title: str


class UserOut(BaseModel):
    id: int
    # 公开 ID（2026-11-15）：用户侧展示/复制用它，不再露数字 id；
    # 存量未回填（或老会话）时为 None，前端回落不显示那段
    public_id: str | None = None
    username: str
    email: str | None = None
    emby_username: str | None = None
    is_vip: bool = False
    is_active: bool = True
    # 是否管理员：用户端据此显示「管理后台」入口（管理后台与门户同一套 JWT）
    is_staff: bool = False
    # 付费墙是否开启（开启且非会员时播放会被拦截）；公益服恒为 false
    subscription_required: bool = False
    # 站点是否允许下载 + 每用户设备上限（0 表示不限）
    download_allowed: bool = True
    device_limit: int = 0
    # 接入方式（v2.7.0 公益服）：free = 本服免费开放，不需要订阅
    realm_access_mode: str = "paid"
    is_free_realm: bool = False
    # 公益服规则文案（用户端展示；付费服为空串）
    realm_access_note: str = ""
    # 白名单专属身份（创世编号+专属称号）；判定走 codes 唯一口径，
    # 非生效白名单为 None，用户端不渲染任何相关元素
    whitelist_identity: WhitelistIdentityOut | None = None
    # 贡献池专属身份（称号「片库贡献者」）；判定走 pool_reward
    # 状态驱动口径，非在池贡献者为 None，用户端不渲染
    pool_identity: PoolIdentityOut | None = None
    # 家庭子账号（2026-11-15）：本号是子账号时为 True，
    # parent_username 为主账号用户名。子账号的订阅/流量/播放
    # 门禁按主账号算（backend/sub_accounts 有效账号解析），
    # is_vip 也按主账号派生；前端据此显示「家庭子账号」标识
    # 并隐藏购买类入口。主账号恒 False / None。
    is_sub_account: bool = False
    parent_username: str | None = None
    created_at: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ==================== Helpers ====================

def _user_out(user: models.WebUser, db: Session | None = None) -> UserOut:
    """用户信息出参：is_vip 由生效中的订阅派生（无 db 时回退到库内标记）

    ``subscription_required`` 告知前端「这个服要不要会员才能看」，用于提前展示开通引导——
    公益服（``realm_access_mode='free'``）恒为 false，前端据此换一套「免费开放」的文案，
    而不是把一个不需要付费的服宣传成付费墙。
    """
    # 家庭子账号的 VIP 派生看主账号订阅（子账号没有自己的订阅）：
    # 经 backend/sub_accounts 有效账号解析，异常按本人处理不拖垮登录。
    _effective_id = user.id
    _is_sub = False
    _parent_username = None
    if db is not None:
        try:
            from backend import sub_accounts as _sub_accounts

            _is_sub = _sub_accounts.is_sub_account(user)
            if _is_sub:
                _effective_id = _sub_accounts.effective_user_id(db, user)
                _parent = _sub_accounts.effective_user(db, user)
                _parent_username = _parent.username if _parent else None
        except Exception:
            _effective_id, _is_sub, _parent_username = user.id, False, None
    is_vip = has_active_subscription(db, _effective_id) if db is not None else bool(user.is_vip)
    requires_sub = realm_requires_subscription(db) if db is not None else False
    free_realm = is_free_realm(db) if db is not None else False
    # 白名单专属身份同为纯展示：判定/编号走 codes 唯一口径，异常一律 None
    whitelist_identity = None
    if db is not None:
        try:
            from backend.codes import whitelist_identity_payload

            payload = whitelist_identity_payload(db, user)
            if payload:
                whitelist_identity = WhitelistIdentityOut(**payload)
        except Exception:
            whitelist_identity = None
    # 贡献池称号同为纯展示：状态驱动（pool_reward 唯一口径），异常一律 None
    pool_identity = None
    if db is not None:
        try:
            from backend.emby_server.pool_reward import (
                pool_identity_payload)

            payload = pool_identity_payload(db, user)
            if payload:
                pool_identity = PoolIdentityOut(**payload)
        except Exception:
            pool_identity = None
    return UserOut(
        id=user.id,
        public_id=getattr(user, "public_id", None),
        username=user.username,
        email=user.email,
        emby_username=user.emby_username,
        is_vip=is_vip,
        is_active=user.is_active,
        is_staff=bool(user.is_staff),
        subscription_required=requires_sub,
        download_allowed=download_allowed(db) if db is not None else True,
        device_limit=device_limit(db) if db is not None else 0,
        realm_access_mode="free" if free_realm else "paid",
        is_free_realm=free_realm,
        realm_access_note=realm_access_note(db) if db is not None else "",
        whitelist_identity=whitelist_identity,
        pool_identity=pool_identity,
        is_sub_account=_is_sub,
        parent_username=_parent_username,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


def _issue_auth_response(
    user: models.WebUser, db: Session, plain_password: str | None = None
) -> AuthResponse:
    """签发 access + refresh token。

    不再自动生成接线用户名，也不为还没有接线用户名的用户把门户密码写成播放密码。
    用户去接线员页自行设置（``POST /api/user/emby/password``）。

    兼容：已经有 ``emby_username``、但还没有 ``emby_password`` 的老用户，
    登录时用本次已验证的门户密码补一次（fill_only）。已有播放密码的不被覆盖。
    没带明文密码的路径（刷新后的换票、Telegram 免密登录）什么都不补。
    """
    if plain_password:
        ensure_emby_credentials(db, user, password=plain_password, fill_only=True)
    # F8：一次登录 = 一个 token 家族（fid）。access 与 refresh 同家族，
    # refresh 轮换保持家族不变；复用检测按家族吊销，不误杀同用户其它设备。
    fid = secrets.token_hex(16)
    access = create_access_token(user.id, {"username": user.username},
                                  token_version=user.token_version or 0, fid=fid)
    refresh = create_refresh_token(user.id, token_version=user.token_version or 0,
                                   fid=fid)

    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_user_out(user, db),
    )


def get_current_user_jwt(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.WebUser:
    """JWT 鉴权依赖：Authorization: Bearer <access_token>"""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证凭证")
    from backend.security import decode_token, enforce_token_freshness
    payload = decode_token(credentials.credentials, expected_type="access")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的凭证")
    user_id = resolve_jwt_user_id(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的凭证")
    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用")
    # P2/P3：吊销 + token_version 双检查（横切能力只许一套，见 security.enforce_token_freshness）
    enforce_token_freshness(db, payload, user)
    return user


# ==================== Endpoints ====================

def _normalize_registration_mode(raw) -> str:
    """注册模式归一：未配置 → open；open/closed 原样；未知值 → closed。

    注册码已于 2026-11-15 整体下线：模式只剩 open / closed 两档。
    存量值 code 一律归一为 open——生产配置在下线时就是 code，
    归一成 closed 等于把注册关死，不许。
    """
    value = (raw or "").strip().lower()
    if not value:
        return "open"
    if value == "code":
        return "open"
    return value if value in ("open", "closed") else "closed"


def _registration_mode(db: Session) -> str:
    """当前注册模式（注册端点 / 配置下发共用同一读取）。"""
    row = (
        db.query(models.SystemConfig)
        .filter(models.SystemConfig.key == "registration_mode")
        .first()
    )
    return _normalize_registration_mode(row.value if row else None)


@auth_router.get("/register-config")
def register_config(db: Session = Depends(get_db)):
    """公开：注册页需要的开关状态（未登录可调）。

    前端据此决定注册页形态：
    - registration_mode: open=开放注册 / closed=关闭注册
    - invitation_enabled: 邀请码功能开关
    """
    from backend.api.invitation import get_invite_config

    mode = _registration_mode(db)
    return {
        "registration_mode": mode,
        "invitation_enabled": get_invite_config(db)["enabled"],
    }


def _get_register_ratelimit(db: Session) -> tuple[bool, int, int]:
    """读取注册限流配置。

    配置键（均存于 SystemConfig 表）：
    - register_ratelimit_enabled：是否启用注册限流，值为 "true"（忽略首尾空格与大小写）时启用，默认启用；
    - register_ratelimit_max：限流时间窗口内允许的最大注册次数，默认 5；
    - register_ratelimit_window：限流时间窗口长度（秒），默认 3600。

    任何读取或转换异常均回退到默认值，保证注册流程不会因配置读取失败而报错。
    非法的非正数值同样回退默认值（管理后台写入时已有 >=1 / >=60 校验，
    这里防的是直接改库的脏数据）。
    """
    enabled = True
    max_events = 5
    window_seconds = 3600
    try:
        # 一次查询取回三个键，避免三次 round trip；每个键独立容错
        rows = (
            db.query(models.SystemConfig)
            .filter(models.SystemConfig.key.in_([
                "register_ratelimit_enabled",
                "register_ratelimit_max",
                "register_ratelimit_window",
            ]))
            .all()
        )
        values = {r.key: r.value for r in rows if r.value}
    except Exception:
        values = {}
    # A 类摘面板开关（2026-10-21）：env 显式设置即接管，口径见 integrations.store
    _env_v = _switch_env_value("register_ratelimit_enabled")
    if _env_v is not None:
        values["register_ratelimit_enabled"] = _env_v
    if "register_ratelimit_enabled" in values:
        enabled = values["register_ratelimit_enabled"].strip().lower() == "true"
    try:
        if "register_ratelimit_max" in values:
            v = int(values["register_ratelimit_max"])
            max_events = v if v >= 1 else 5
    except (TypeError, ValueError):
        pass
    try:
        if "register_ratelimit_window" in values:
            v = int(values["register_ratelimit_window"])
            window_seconds = v if v >= 1 else 3600
    except (TypeError, ValueError):
        pass
    return enabled, max_events, window_seconds


# 注册不再送任何体验/赠送（2026-11-15 用户亲令）：原新注册冷启动赠送
# （register_gift_view_* 控的查看行发放）已整段删除，register_gift_view_*
# 与 register_gift_welfare_* 配置键就此失效（留库不管，代码不再读）。


@auth_router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, req: RegisterRequest, db: Session = Depends(get_db)):
    """注册新用户（用户名唯一，密码 bcrypt 存储）。

    不自动生成接线用户名或播放密码；用户在接线员页自行设置。
    门户用户名也不能与别人已有的接线用户名相同：客户端登录两个字段都认。
    """
    rl_enabled, rl_max, rl_window = _get_register_ratelimit(db)
    rl_key = f"register:{client_ip(request)}"
    if rl_enabled:
        allowed, retry_after = check_rate_limit(rl_key, rl_max, rl_window)
        if not allowed:
            raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")
    username = req.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="用户名只能包含字母、数字、下划线，长度 3-32 位",
        )

    # ===== 注册模式开关（r4-auth：必须在撞名检查之前）=====
    # open: 开放注册 / closed: 关闭注册（注册码已于 2026-11-15 下线，
    # 存量 code 值在 _normalize_registration_mode 里归一为 open）。
    # 顺序是安全语义：旧顺序先查撞名、后查模式，关闭注册时探一个已占用的
    # 名字回 409、探未占用的名字回 403，等于用注册接口枚举门户名与接线名
    # 是否存在。先判模式，不把撞名信息泄露给无权注册的人。
    reg_mode = _registration_mode(db)

    if reg_mode == "closed":
        closed_msg_config = db.query(models.SystemConfig).filter(
            models.SystemConfig.key == "registration_closed_message"
        ).first()
        raise HTTPException(
            status_code=403,
            detail=(closed_msg_config.value if closed_msg_config and closed_msg_config.value
                    else "当前未开放注册"),
        )

    # 大小写不敏感撞名检查（门户名＋接线名都查）：只按精确大小写查会放行
    # Foo/foo 异壳账号，而转账、指名码等消费侧按不敏感匹配时会命中不确定，
    # 甚至冒充已有用户名。落库前一律 409。文案不新造、沿用本端点改动前的
    # 两条原句：撞门户名（精确或大小写变体）回「用户名已被注册」，撞接线名
    # 回「用户名已被占用」（与注册关闭模式测试锁定的既有口径一致）。
    if find_users_by_username_ci(db, username):
        raise HTTPException(status_code=409, detail="用户名已被注册")
    if username_conflict(db, username):
        raise HTTPException(status_code=409, detail="用户名已被占用")
    if req.email:
        email = req.email.strip()
        if "@" not in email:
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        existing_email = db.query(models.WebUser).filter(models.WebUser.email == email).first()
        if existing_email:
            raise HTTPException(status_code=409, detail="邮箱已被注册")

    user = models.WebUser(
        username=username,
        password_hash=hash_password(req.password),
        email=req.email.strip() if req.email else None,
        is_active=True,
        # 公开 ID（2026-11-15）：建号即分配、永不变更（唯一实现
        # 在 backend.models.new_public_id，查库防碰撞）
        public_id=models.new_public_id(db),
        # 注册渠道归因（v2.44.0）：注册码下线后进门凭据只剩邀请码，
        # 入口先记 open，邀请码真的建成了关系再由下方 resolve 改记
        # invitation（见 register_channel.resolve）。
        register_channel=register_channel.OPEN,
    )
    db.add(user)
    commit_with_retry(db, label="注册落库")  # 一次提交落盘
    db.refresh(user)

    # 邀请返利：注册时应用邀请码（双向发奖，失败静默不阻塞注册）
    if req.invitation_code:
        try:
            from backend.api.invitation import apply_invitation

            result = apply_invitation(db, user, req.invitation_code)
            # 归因收尾：码无效/被拒时这个号就是自己注册的，记成 invitation 会把
            # 渠道分析带偏。优先级（邀请码 > 开放）由 resolve 统一裁决。
            user.register_channel = register_channel.resolve(
                user.register_channel, bool((result or {}).get("applied")))
            db.commit()
        except Exception:  # noqa: BLE001 — 邀请奖励失败不阻塞注册
            db.rollback()
            # A-128：此处拿不到码行 id（apply 本身已抛错），只记掩码；
            # 掩码函数与 apply_invitation 同模块，沿用本块的懒导入写法。
            from backend.api.invitation import _mask_invite_code

            logger.warning("邀请码应用失败: user=%s code_mask=%s", user.id,
                           _mask_invite_code(req.invitation_code),
                           exc_info=True)

    # 福利试用码领取（2026-11-15 改版）：分享链接带 trial 参数注册的，
    # 注册成功后凭码领取试用（发放只走这条路，邀请注册不再自动发）。
    # 保存点隔离：领取失败（码已用/过期/已领过等）只回滚领取本身，
    # 注册与邀请关系照常成立；不许静默——记日志（码只记掩码，A-128）。
    if req.trial_code:
        try:
            with db.begin_nested():
                from backend import invite_trial

                invite_trial.claim_trial_code(db, req.trial_code, user)
            db.commit()
        except Exception:  # noqa: BLE001 — 领取失败不阻塞注册
            db.rollback()
            from backend.api.invitation import _mask_invite_code

            logger.warning("福利试用码领取失败: user=%s code_mask=%s", user.id,
                           _mask_invite_code(req.trial_code),
                           exc_info=True)

    logger.info("新用户注册: %s (id=%s, mode=%s)", username, user.id, reg_mode)
    # 计数语义：注册成功后退还该 IP 的注册限流计数，失败尝试（验证码错 /
    # 用户名已存在 / 邮箱已注册等）保留计数防爆破。否则同一 NAT 出口下
    # 一个人注册成功，就把同出口后来者的 5 次/小时额度白白烧掉一格。
    if rl_enabled:
        reset_rate_limit(rl_key)
    return _issue_auth_response(user, db, plain_password=req.password)


@auth_router.post("/login", response_model=AuthResponse)
def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)):
    """用户名密码登录，成功返回 JWT（同 IP 每分钟最多 8 次尝试）"""
    allowed, retry_after = check_rate_limit(f"login:{client_ip(request)}", 8, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    # F4（黑盒审计）：失败锁定。请求数限流只数总量，把尝试摊到多分钟
    # 里慢慢猜就能绕过；这里按账号＋IP 双维度只数失败，达阈值临时锁定
    # （阈值/窗口/锁长走 SystemConfig，见 ratelimit.login_lockout_config）。
    lockout_cfg = login_lockout_config(db)
    login_ip = client_ip(request)
    blocked, lock_retry = login_lockout_check(
        "portal", req.username, login_ip, lockout_cfg)
    if blocked:
        record_event(
            db, username=req.username.strip(), user_id=None,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="portal_login_failed",
            detail=f"登录失败次数过多，已临时锁定（{lock_retry}s）",
        )
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
    user = (
        db.query(models.WebUser)
        .filter(models.WebUser.username == req.username.strip())
        .first()
    )
    if user is None:
        # A-003（360° 审计 R1）：用户不存在也走一次等价 bcrypt 校验，
        # 抹平与存在用户之间的时序差，防止用户名被枚举。
        verify_password(req.password, DUMMY_PASSWORD_HASH)
        password_ok = False
    else:
        password_ok = verify_password(req.password, user.password_hash)
    if not password_ok:
        login_lockout_record_failure(
            "portal", req.username, login_ip, lockout_cfg)
        record_event(
            db, username=req.username.strip(), user_id=user.id if user else None,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="portal_login_failed", detail="用户名或密码错误",
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        record_event(
            db, username=user.username, user_id=user.id, ip=log_ip(request),
            agent=user_agent(request), success=False, reason="portal_login_failed",
            detail="账户已被禁用",
        )
        raise HTTPException(status_code=403, detail="账户已被禁用")

    # 动态验证码（TOTP）：密码对了才走到这一步。已启用的账号必须带码，
    # 缺码/错码与密码错误同走 401 + 安全日志（detail 区分），防第二因子被绕过。
    from backend import account_security as _acct_sec

    if _acct_sec.totp_enabled_for(db, user.id):
        code = (req.totp_code or "").strip()
        if not code:
            login_lockout_record_failure(
                "portal", req.username, login_ip, lockout_cfg)
            record_event(
                db, username=user.username, user_id=user.id, ip=log_ip(request),
                agent=user_agent(request), success=False,
                reason="portal_login_failed", detail="已启用动态验证码但未提供",
            )
            raise HTTPException(
                status_code=401,
                detail="该账号已启用动态验证码，请输入 6 位动态验证码",
            )
        if not _acct_sec.verify_login_totp(db, user.id, code):
            login_lockout_record_failure(
                "portal", req.username, login_ip, lockout_cfg)
            record_event(
                db, username=user.username, user_id=user.id, ip=log_ip(request),
                agent=user_agent(request), success=False,
                reason="portal_login_failed", detail="动态验证码不正确",
            )
            raise HTTPException(status_code=401, detail="动态验证码不正确，请重试")

    user.last_login_at = datetime.now()
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id, ip=log_ip(request),
        agent=user_agent(request), success=True, reason="portal_login",
    )
    # 防共享·跨城市轨迹（默认 off，不判定也不写库）。命中 enforce 时账号已被停用，
    # 这里直接拒绝本次登录——先落安全日志，管理员才能查到「为什么这个号突然登不上」
    verdict = share_guard.note_activity(db, user, log_ip(request))
    if verdict and verdict.get("blocked"):
        raise HTTPException(
            status_code=403,
            detail="检测到该账号在短时间内于多个城市登录，已被暂停使用，请联系管理员",
        )
    # 已有接线用户名但还没有播放密码的老用户：登录成功后用已验证的门户密码补一次。
    # 没有接线用户名的新用户什么都不写，去接线员页自己设。
    # F4：登录全程成功，清零该账号的失败计数（IP 维度保留不清）。
    login_lockout_record_success("portal", user.username)
    return _issue_auth_response(user, db, plain_password=req.password)


# E1（P2-6）：refresh 轮换容忍窗口——客户端成功换新后因网络重试/并发双发再次携带
# 旧 refresh 时，旧 jti 虽已在吊销表但属良性重复，不应触发应急吊销（误杀全部会话）。
REFRESH_ROTATION_GRACE_SEC = 120


def _refresh_rotation_in_grace(db, old_jti: str) -> bool:
    """判断旧 refresh 的 jti 是否处于轮换容忍窗口内（视为良性重复）。

    仅当吊销原因恰为 refresh_rotation，且吊销时间在 REFRESH_ROTATION_GRACE_SEC 内返回 True；
    行不存在 / 原因不符 / 已超窗 均返回 False。
    """
    if not old_jti:
        return False
    # 延迟导入，避免循环导入
    from datetime import datetime, timedelta
    from backend.models import RevokedJwt

    row = db.query(RevokedJwt).filter(RevokedJwt.jti == old_jti).first()
    if row is None:
        return False
    if row.reason != "refresh_rotation":
        return False
    return (datetime.now() - row.revoked_at) <= timedelta(seconds=REFRESH_ROTATION_GRACE_SEC)


@auth_router.post("/refresh")
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """用 refresh_token 换取新的 access_token（+ 可选轮换的 refresh_token）"""
    payload = decode_token(req.refresh_token, expected_type="refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="无效或已过期的刷新凭证")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="无效的刷新凭证")

    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    from backend.security import (
        enforce_token_freshness, is_family_revoked, is_jti_revoked,
        revoke_all_user_tokens, revoke_jti, revoke_token_family,
    )

    # F8：家族已吊销（此前已判定过复用）→ 本家族任何 refresh 都不再换发。
    fid = payload.get("fid")
    if fid and is_family_revoked(db, fid):
        raise HTTPException(status_code=401, detail="会话已吊销，请重新登录")

    # A-P1-1：refresh 复用检测——旧 refresh 的 jti 已在吊销表中，
    # 说明同一凭证被重复使用（疑似泄露），触发应急响应。
    # P2-6：轮换容忍窗口内的重试视为良性重复（网络重试/并发双发），放行不误杀。
    # F8：应急响应改为按家族吊销（只杀这台设备的会话链）；无 fid 的存量
    # token（升级前签发）没有家族可杀，回退全量吊销（A-P1-1 旧口径）。
    old_jti = payload.get("jti")
    reuse_suspected = bool(old_jti) and is_jti_revoked(db, old_jti)
    in_grace = reuse_suspected and _refresh_rotation_in_grace(db, old_jti)
    if reuse_suspected and not in_grace:
        if fid:
            revoke_token_family(db, fid, user.id,
                                reason="refresh_reuse_detected")
            logger.warning(
                "refresh 复用检测：已吊销 token 家族 user_id=%s fid=%s",
                user.id, fid)
        else:
            revoke_all_user_tokens(db, user.id, reason="refresh_reuse_detected")  # 内部原子 bump + commit
        raise HTTPException(status_code=401, detail="检测到异常的刷新凭证，相关会话已吊销")
    if in_grace:
        # P1（安全）：容忍窗口只豁免"旧 jti 已在吊销表"这一项，不能豁免改密/登出。
        # 注意：此处绝对不能调用 enforce_token_freshness——它内部会检查 payload["jti"] 是否在吊销表，
        # 而轮换时旧 refresh 的 jti 已被 revoke（refresh_rotation），直接调用会把合法的良性重试误杀。
        # 因此这里只单独比对 token 版本号，语义与 enforce_token_freshness 中的 tv 校验保持一致：
        # 改密/登出 bump token_version 后，容忍窗口内旧 refresh 重放必须 401，否则 120s 内
        # 旧凭证仍能换出新 access，违反"改密/登出后旧 token 立即作废"的安全契约。
        if int(payload.get("tv", 0) or 0) != int(getattr(user, "token_version", 0) or 0):
            raise HTTPException(status_code=401, detail="密码已变更，请重新登录")
        logger.warning("refresh 轮换容忍窗口内重试 jti=%s user_id=%s：视为良性重复，放行，不触发应急吊销",
                       old_jti, user.id)
    else:
        # P3（审查第八批）：改密/登出后旧 refresh_token 作废，否则换新 access 形同虚设
        enforce_token_freshness(db, payload, user)

    # A-P1-1：轮换——旧 refresh 立即进吊销表，避免同一 refresh 被无限重用
    if old_jti:
        revoke_jti(db, old_jti, user.id, reason="refresh_rotation")

    # 轮换保持家族不变；存量无 fid 的 token 从本次起成链（新签一族）。
    new_fid = fid or secrets.token_hex(16)
    return {
        "access_token": create_access_token(user.id, {"username": user.username},
                                          token_version=user.token_version or 0,
                                          fid=new_fid),
        "refresh_token": create_refresh_token(user.id, token_version=user.token_version or 0, fid=new_fid),  # 轮换
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _user_out(user, db),
    }


@auth_router.get("/me", response_model=UserOut)
def me(
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """当前登录用户信息（含派生的 VIP 状态）"""
    return _user_out(current_user, db)


@auth_router.post("/admin-handoff")
def admin_handoff(
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """前台「管理后台」入口（2026-11-17 用户亲点）：管理员账号登录前台时
    免密交接进管理后台。

    鉴权与 ``/me`` 同一个依赖（前台用户会话），不新造第二套；只有
    ``is_staff`` 的账号能拿到交换码，非 staff 回 403 且不签发任何码。
    签发复用 ``admin_magic.issue_exchange_code``（同一张交换码表、同一
    60 秒口径），前端拿到地址后跳 ``/admin/#magic=<code>``，管理端走
    既有 magic-exchange 换正式会话。限流与免登录链接入口同档
    （5 次/分钟/IP，复用现行 ratelimit）；成功签发写 AdminLog 审计，
    action 与链接入口的 ``admin_magic_login`` 区分来源。
    """
    if not current_user.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号没有管理权限",
        )
    ip = client_ip(request)
    allowed, _retry = check_rate_limit(
        f"admin_magic_frontend_handoff:{ip}", 5, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="操作太频繁，请稍后再试")
    from backend.api.admin_core import _audit
    from backend.api.admin_magic import issue_exchange_code

    code = issue_exchange_code(db, current_user.id, ip)
    _audit(db, current_user.id, "admin_magic_frontend_handoff", "self",
           current_user.id, ip=ip)
    db.commit()
    return {"url": f"/admin/#magic={code}"}


@auth_router.post("/logout")
def logout(request: Request,
           current_user: models.WebUser = Depends(get_current_user_jwt),
           db: Session = Depends(get_db)):
    """登出：吊销当前 access token 的 jti，并 bump token_version 使全部 refresh 失效。

    P2 修复（审查）：此前登出只是客户端清除，服务端无吊销能力，
    偷到的 token 在过期前一直有效。
    A-P1-1 修复：仅吊销 access 的 jti 时 refresh 仍可无限续签，登出形同虚设；
    此处同步 bump token_version，令该用户所有已签发的 refresh（及 access）立即失效。
    """
    from backend.security import decode_token, revoke_jti
    token = None
    try:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    except Exception:
        pass
    # 1) 吊销当前 access token，缩短其残余有效窗口
    if token:
        payload = decode_token(token, expected_type="access")
        if payload:
            revoke_jti(db, payload.get("jti"), current_user.id, reason="logout")
    # 2) A-P1-1：bump token_version —— 该用户全部已签发 refresh 立即失效，
    #    否则攻击者可用旧 refresh 无限换取新 access，登出形同虚设
    try:
        current_user.token_version = (current_user.token_version or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"success": True, "message": "已登出"}


class TgLoginRequest(BaseModel):
    token: str


@auth_router.post("/tg-login")
def tg_login(request: Request, req: TgLoginRequest, db: Session = Depends(get_db)):
    """bot 一键免密登录：一次性 token 换本站登录态（与密码登录一样记日志 + 防共享判定）"""
    from backend.tg_bot import login_token as _lt
    payload = _lt.consume_login_token((req.token or "").strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录链接无效或已过期，请在机器人中重新获取")
    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="登录链接无效")
    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    if user.telegram_id != payload.get("telegram_id"):
        raise HTTPException(status_code=401, detail="身份校验失败")
    user.last_login_at = datetime.now()
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id, ip=log_ip(request),
        agent=user_agent(request), success=True, reason="tg_login",
    )
    verdict = share_guard.note_activity(db, user, log_ip(request))
    if verdict and verdict.get("blocked"):
        raise HTTPException(
            status_code=403,
            detail="检测到该账号在短时间内于多个城市登录，已被暂停使用，请联系管理员",
        )
    return _issue_auth_response(user, db)


# ==================== 账号安全三件套：登录侧接入 ====================
# 管理侧（启用 TOTP / 绑 Passkey / 建平台 Key）在 backend/api/account_security.py；
# 这里只有登录链需要的三个公开端点：因素探测、Passkey 断言选项、Passkey 登录。
# 核心逻辑在 backend/account_security.py，本处不另写一套校验。

class PasskeyLoginOptionsRequest(BaseModel):
    username: str


class PasskeyLoginVerifyRequest(BaseModel):
    username: str
    credential: dict
    challenge_token: str


@auth_router.get("/login-factors")
def login_factors(request: Request, username: str, db: Session = Depends(get_db)):
    """登录页因素探测：该用户名是否要显示动态验证码字段 / Passkey 登录按钮。

    与 UHDNow 同口径（字段仅在已启用时出现）。按 IP 限流防批量探测；
    用户名不存在时返回全 false，与「存在但未启用」不可区分的只有时序——
    本端点不查密码、无 bcrypt，时序差不构成枚举面（用户名本身可由注册接口探测）。
    """
    allowed, _retry = check_rate_limit(f"loginfactors:{client_ip(request)}", 20, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    from backend import account_security as _acct_sec

    user = (
        db.query(models.WebUser)
        .filter(models.WebUser.username == (username or "").strip())
        .first()
    )
    if user is None:
        return {"totp_required": False, "passkey_available": False}
    return {
        "totp_required": _acct_sec.totp_enabled_for(db, user.id),
        "passkey_available": len(_acct_sec.list_passkeys(db, user.id)) > 0,
    }


@auth_router.post("/passkey/login/options")
def passkey_login_options(
    request: Request, req: PasskeyLoginOptionsRequest, db: Session = Depends(get_db)
):
    """Passkey 登录第一步：按用户名下发断言选项（allow 列表只含本人凭据）。"""
    allowed, _retry = check_rate_limit(f"passkeyopts:{client_ip(request)}", 10, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    from backend import account_security as _acct_sec

    user = (
        db.query(models.WebUser)
        .filter(models.WebUser.username == req.username.strip())
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="该账号尚未绑定 Passkey")
    rp_id, _origin = _acct_sec.rp_context(request)
    try:
        options, token = _acct_sec.authentication_options(db, user, rp_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"options": options, "challenge_token": token}


@auth_router.post("/passkey/login/verify", response_model=AuthResponse)
def passkey_login_verify(
    request: Request, req: PasskeyLoginVerifyRequest, db: Session = Depends(get_db)
):
    """Passkey 登录第二步：验断言 → 与密码登录同一套发令牌 + 日志 + 防共享判定。"""
    allowed, _retry = check_rate_limit(f"passkeyverify:{client_ip(request)}", 10, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    from backend import account_security as _acct_sec

    user = (
        db.query(models.WebUser)
        .filter(models.WebUser.username == req.username.strip())
        .first()
    )
    if user is None or not user.is_active:
        record_event(
            db, username=req.username.strip(), user_id=user.id if user else None,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="portal_login_failed", detail="Passkey 登录：用户不存在或已禁用",
        )
        raise HTTPException(status_code=401, detail="Passkey 登录失败，请重试")
    rp_id, origin = _acct_sec.rp_context(request)
    try:
        _acct_sec.verify_login_assertion(
            db, user, req.credential, req.challenge_token, rp_id, origin
        )
    except ValueError as exc:
        record_event(
            db, username=user.username, user_id=user.id, ip=log_ip(request),
            agent=user_agent(request), success=False,
            reason="portal_login_failed", detail="Passkey 登录校验未通过",
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user.last_login_at = datetime.now()
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id, ip=log_ip(request),
        agent=user_agent(request), success=True, reason="portal_login",
        detail="Passkey 登录",
    )
    verdict = share_guard.note_activity(db, user, log_ip(request))
    if verdict and verdict.get("blocked"):
        raise HTTPException(
            status_code=403,
            detail="检测到该账号在短时间内于多个城市登录，已被暂停使用，请联系管理员",
        )
    return _issue_auth_response(user, db)


def _apply_password_change(db: Session, user: models.WebUser, new_password: str) -> None:
    """改密落地：change_password 与邮箱找回重置共用同一套副作用，只改字段不提交。

    1. 门户密码哈希换新；
    2. 同步自建 Emby 播放密码（bcrypt 哈希存储），保持两端一致；
    3. token_version +1，旧门户 JWT 全部作废（P3 修复：防盗号后持续登录）；
    4. 吊销该用户全部 Emby 客户端令牌（A-085，360° 审计第二遍 R25-01）——
       只 bump 门户 JWT 的话，被盗的播放器令牌（历史令牌 expires_at 为空、
       永不过期）改密后仍可播放。复用 share_guard._disable_user 的现成写法。
    提交时机由调用方负责（与上面的 JWT bump 同一事务）。
    """
    user.password_hash = hash_password(new_password)
    user.emby_password = hash_password(new_password)
    user.token_version = int(user.token_version or 0) + 1
    from backend.emby_server import models as emby_models
    db.query(emby_models.EmbyApiToken).filter(
        emby_models.EmbyApiToken.user_id == user.id
    ).update({emby_models.EmbyApiToken.is_revoked: True}, synchronize_session=False)


# ==================== 邮箱验证码（G-53/G-54 共用一套） ====================
# 与找回密码同一套机制（password_reset_tokens 的姊妹表，见 models
# .EmailVerificationCode）：原始码只出现在邮件里、库里只存 SHA256、
# 单次使用、重新申请作废同用途旧码、check_rate_limit 限流、
# integrations_mail.build_channel + EmailChannel._deliver 投递。
# 6 位码与 10 分钟 TTL 同 tg_bind 现行实现口径。

EMAIL_CODE_TTL = timedelta(minutes=10)
EMAIL_CODE_PURPOSE_CHANGE_PASSWORD = "change_password"
EMAIL_CODE_PURPOSE_CHANGE_EMAIL = "change_email"


def mask_email(email: str | None) -> str:
    """邮箱脱敏展示：本地名只露首尾，域名原样（a***z@example.com）。"""
    raw = (email or "").strip()
    if "@" not in raw:
        return ""
    local, _, domain = raw.partition("@")
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = f"{local[:1]}***{local[-1:]}"
    return f"{masked_local}@{domain}"


def _email_code_hash(user_id: int, purpose: str, code: str) -> str:
    return hashlib.sha256(f"{user_id}:{purpose}:{code}".encode("utf-8")).hexdigest()


def _issue_email_code(
    db: Session, user: models.WebUser, purpose: str,
    target_email: str | None, ip: str | None,
) -> str:
    """签发 6 位码：作废该用户同用途全部未用旧码后落一行哈希。不 commit。"""
    now = datetime.now()
    db.query(models.EmailVerificationCode).filter(
        models.EmailVerificationCode.user_id == user.id,
        models.EmailVerificationCode.purpose == purpose,
        models.EmailVerificationCode.used_at.is_(None),
    ).update({models.EmailVerificationCode.used_at: now}, synchronize_session=False)
    raw_code = str(100000 + secrets.randbelow(900000))  # CSPRNG，同 tg_bind 口径
    db.add(models.EmailVerificationCode(
        user_id=user.id,
        purpose=purpose,
        target_email=target_email,
        code_hash=_email_code_hash(user.id, purpose, raw_code),
        created_at=now,
        expires_at=now + EMAIL_CODE_TTL,
        request_ip=ip,
    ))
    return raw_code


def _verify_email_code(
    db: Session, user: models.WebUser, purpose: str, code: str,
    target_email: str | None = None,
) -> bool:
    """验码：命中该用户同用途最新未用码、未过期、哈希一致（且目标邮箱
    一致，change_email 用）才算过；通过即标记已消费（调用方 commit）。"""
    code = (code or "").strip()
    if not code:
        return False
    row = (
        db.query(models.EmailVerificationCode)
        .filter(
            models.EmailVerificationCode.user_id == user.id,
            models.EmailVerificationCode.purpose == purpose,
            models.EmailVerificationCode.used_at.is_(None),
        )
        .order_by(models.EmailVerificationCode.id.desc())
        .first()
    )
    if row is None:
        return False
    if target_email is not None and (row.target_email or "") != target_email:
        return False
    if row.expires_at < datetime.now():
        return False
    if row.code_hash != _email_code_hash(user.id, purpose, code):
        return False
    row.used_at = datetime.now()
    return True


def _deliver_email(db: Session, to_email: str, title: str, content: str, what: str) -> None:
    """经现行 EmailChannel 投递一封纯文本邮件；失败只记日志（不记正文
    里的验证码），不改变调用方对外响应（同 _send_reset_email 口径）。"""
    channel = integrations_mail.build_channel(db)
    if channel is None:
        logger.warning("%s未发送：邮件渠道未启用或未配置 to=%s", what, mask_email(to_email))
        return
    try:
        ok, error = channel._deliver(to_email, title, content)
    except Exception as exc:  # noqa: BLE001 — 发信异常不许影响主流程
        logger.error("%s发送失败 to=%s: %s: %s", what, mask_email(to_email), type(exc).__name__, exc)
        return
    if not ok:
        logger.error("%s发送失败 to=%s: %s", what, mask_email(to_email), error)


def _send_email_code(db: Session, user: models.WebUser, to_email: str, raw_code: str, purpose: str) -> None:
    if purpose == EMAIL_CODE_PURPOSE_CHANGE_EMAIL:
        title = "Aetrix 换邮箱验证码"
        scene = "更换恢复邮箱"
    else:
        title = "Aetrix 改密验证码"
        scene = "修改登录密码"
    content = (
        f"你正在{scene}（账号 {user.username}）。\n\n"
        f"验证码是 {raw_code}，10 分钟内有效，输对了才会继续。\n\n"
        "如果不是你本人操作，请忽略这封邮件，你的账号不会有任何变化。"
    )
    _deliver_email(db, to_email, title, content, f"{scene}验证码邮件")


@auth_router.post("/change-password/send-code")
def change_password_send_code(
    request: Request,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """G-53 改密发码：向账号现邮箱（恢复邮箱）发 6 位验证码。

    无可用邮箱的账号明确回 password_only 模式（400 告知不用验证码），
    前端据此不渲染验证码字段、直接走当前密码改密，不锁死。
    限流沿用找回密码账号维度口径（3 次/小时）＋ IP 维度兜底。
    """
    email = (current_user.email or "").strip()
    if not email:
        raise HTTPException(
            status_code=400,
            detail="账号还没有绑定邮箱，不用验证码，填当前密码就能改密码",
        )
    ip = client_ip(request)
    allowed, _retry = check_rate_limit(
        f"change-password-code:ip:{ip}", 10, 3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    allowed, _retry = check_rate_limit(
        f"change-password-code:user:{current_user.id}", 3, 3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="验证码发得太频繁了，请过一会儿再试")
    raw_code = _issue_email_code(
        db, current_user, EMAIL_CODE_PURPOSE_CHANGE_PASSWORD, None, ip)
    db.commit()
    _send_email_code(db, current_user, email, raw_code, EMAIL_CODE_PURPOSE_CHANGE_PASSWORD)
    return {
        "success": True,
        "verification": "email_code",
        "email_masked": mask_email(email),
        "expires_in": int(EMAIL_CODE_TTL.total_seconds()),
    }


@auth_router.post("/change-password")
def change_password(
    request: Request,
    req: ChangePasswordRequest,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """修改密码（验证旧密码，同步更新自建 Emby 播放密码）

    A-004（360° 审计 R1）：旧密码校验按登录同口径限流（8 次/分钟，
    桶按 IP+用户），失败落安全日志。此前无限流、无记录，持被盗
    access 的人可不限速猜旧密码，猜中即改密完成持久接管。
    """
    allowed, retry_after = check_rate_limit(
        f"change-password:{client_ip(request)}:{current_user.id}", 8, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    if not verify_password(req.old_password, current_user.password_hash):
        record_event(
            db, username=current_user.username, user_id=current_user.id,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="password_change_failed", detail="旧密码错误",
        )
        raise HTTPException(status_code=400, detail="旧密码错误")
    if req.old_password == req.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

    # G-53：有恢复邮箱必须再过邮箱验证码（码由 /change-password
    # /send-code 发到现邮箱）；无邮箱只验当前密码、不锁死。响应明示
    # verification 模式，前端据此渲染。验码在改密落地前完成，码随
    # 本次改密同一事务消费。
    verification = "password_only"
    if (current_user.email or "").strip():
        verification = "email_code"
        if not (req.email_code or "").strip():
            raise HTTPException(status_code=400, detail="请输入邮箱验证码")
        if not _verify_email_code(
            db, current_user, EMAIL_CODE_PURPOSE_CHANGE_PASSWORD, req.email_code or "",
        ):
            record_event(
                db, username=current_user.username, user_id=current_user.id,
                ip=log_ip(request), agent=user_agent(request), success=False,
                reason="password_change_failed", detail="邮箱验证码不正确或已过期",
            )
            raise HTTPException(
                status_code=400, detail="验证码不正确或已过期，请重新获取")

    _apply_password_change(db, current_user, req.new_password)
    db.commit()
    record_event(
        db, username=current_user.username, user_id=current_user.id,
        ip=log_ip(request), agent=user_agent(request), success=True,
        reason="password_change",
        detail="经邮箱验证码改密" if verification == "email_code" else "仅当前密码改密（无恢复邮箱）",
    )
    return {"success": True, "message": "密码已更新", "verification": verification}


class ChangeEmailSendCodeRequest(BaseModel):
    current_password: str
    new_email: str = Field(..., min_length=3, max_length=255)


class ChangeEmailRequest(BaseModel):
    current_password: str
    new_email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6)


def _validate_new_recovery_email(db: Session, user: models.WebUser, new_email: str) -> str:
    """G-54 换邮箱前置校验：格式 → 与现邮箱相同 → 与他人冲突（注册侧
    唯一性口径：精确匹配查重，命中回 409）。返回归一后的新邮箱。"""
    email = (new_email or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if (user.email or "").strip() == email:
        raise HTTPException(status_code=400, detail="这就是你现在的邮箱，不用换")
    existing = (
        db.query(models.WebUser)
        .filter(models.WebUser.email == email, models.WebUser.id != user.id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="这个邮箱已经绑在别的账号上了，换个邮箱试试")
    return email


def _guard_change_email_password(
    request: Request, db: Session, current_user: models.WebUser, password: str,
) -> None:
    """换邮箱的当前密码门：限流同改密（8 次/分钟，IP+用户），错落安全日志。"""
    allowed, _retry = check_rate_limit(
        f"change-email:{client_ip(request)}:{current_user.id}", 8, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    if not verify_password(password, current_user.password_hash):
        record_event(
            db, username=current_user.username, user_id=current_user.id,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="email_change_failed", detail="当前密码不正确",
        )
        raise HTTPException(status_code=400, detail="当前密码不正确")


@auth_router.post("/change-email/send-code")
def change_email_send_code(
    request: Request,
    req: ChangeEmailSendCodeRequest,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """G-54 换邮箱发码：先验当前密码与新邮箱可用性，再向新邮箱发码
    （先证明新邮箱可达，换完找回链不断）。"""
    _guard_change_email_password(request, db, current_user, req.current_password)
    new_email = _validate_new_recovery_email(db, current_user, req.new_email)
    allowed, _retry = check_rate_limit(
        f"change-email-code:user:{current_user.id}", 3, 3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="验证码发得太频繁了，请过一会儿再试")
    raw_code = _issue_email_code(
        db, current_user, EMAIL_CODE_PURPOSE_CHANGE_EMAIL, new_email, client_ip(request))
    db.commit()
    _send_email_code(db, current_user, new_email, raw_code, EMAIL_CODE_PURPOSE_CHANGE_EMAIL)
    return {
        "success": True,
        "email_masked": mask_email(new_email),
        "expires_in": int(EMAIL_CODE_TTL.total_seconds()),
    }


@auth_router.post("/change-email")
def change_email(
    request: Request,
    req: ChangeEmailRequest,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """G-54 更换恢复邮箱：验当前密码 → 验新邮箱验证码（码与新邮箱
    绑定）→ 落新邮箱 → 向旧邮箱发一封变更通知。"""
    _guard_change_email_password(request, db, current_user, req.current_password)
    new_email = _validate_new_recovery_email(db, current_user, req.new_email)
    if not _verify_email_code(
        db, current_user, EMAIL_CODE_PURPOSE_CHANGE_EMAIL, req.code,
        target_email=new_email,
    ):
        record_event(
            db, username=current_user.username, user_id=current_user.id,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="email_change_failed", detail="新邮箱验证码不正确或已过期",
        )
        raise HTTPException(status_code=400, detail="验证码不正确或已过期，请重新获取")
    old_email = (current_user.email or "").strip()
    current_user.email = new_email
    db.commit()
    record_event(
        db, username=current_user.username, user_id=current_user.id,
        ip=log_ip(request), agent=user_agent(request), success=True,
        reason="email_change", detail="恢复邮箱已更换（旧邮箱已发通知）",
    )
    if old_email:
        _deliver_email(
            db, old_email, "Aetrix 恢复邮箱已更换",
            f"你的 Aetrix 账号（{current_user.username}）的恢复邮箱刚刚被更换为 "
            f"{mask_email(new_email)}。\n\n"
            "更换后，找回密码的邮件会发到新邮箱。如果不是你本人操作，"
            "请尽快登录账号改密码并检查账号安全。",
            "恢复邮箱变更通知邮件",
        )
    return {"success": True, "email": new_email, "email_masked": mask_email(new_email)}


class PasswordResetRequestIn(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=255)  # 用户名或邮箱


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    new_password: str = Field(..., min_length=6, max_length=64)


#: 找回密码申请的统一对外文案：查无此人 / 无邮箱 / 已发送三种情况一字不差，
#: 防止外部按响应差异枚举账号与邮箱绑定情况。
PASSWORD_RESET_GENERIC_RESPONSE = {
    "success": True,
    "message": "如果该账号存在且绑定了邮箱，重置邮件已发送，请查收",
}

PASSWORD_RESET_TOKEN_TTL = timedelta(minutes=30)


def _find_user_for_reset(db: Session, identifier: str) -> models.WebUser | None:
    """按用户名（精确 → 大小写不敏感）或邮箱（大小写不敏感）找找回密码的目标用户。"""
    user = (
        db.query(models.WebUser)
        .filter(models.WebUser.username == identifier)
        .first()
    )
    if user is not None:
        return user
    ci_users = find_users_by_username_ci(db, identifier)
    if ci_users:
        return ci_users[0]
    if "@" in identifier:
        return (
            db.query(models.WebUser)
            .filter(models.WebUser.email.isnot(None))
            .filter(models.WebUser.email != "")
            .filter(models.WebUser.email.ilike(identifier))
            .order_by(models.WebUser.id.asc())
            .first()
        )
    return None


def _invalidate_reset_tokens(db: Session, user_id: int, now: datetime) -> None:
    """作废某用户全部未用重置令牌（used_at 落值即视为失效，不单独设作废标记）。"""
    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user_id,
        models.PasswordResetToken.used_at.is_(None),
    ).update({models.PasswordResetToken.used_at: now}, synchronize_session=False)


def _send_reset_email(db: Session, user: models.WebUser, raw_token: str) -> None:
    """经现行 EmailChannel 发重置邮件；任何失败只记日志（含 user_id 与原因、
    绝不记令牌原文），不改变对外响应。"""
    base = (
        _store_read_value(db, "site_url", "")
        or _store_read_value(db, "site_base_url", "")
        or ""
    ).rstrip("/")
    if not base:
        # 与 tg 免密登录链接同口径：站点地址未配置时链接拼不出来，留告警痕迹。
        logger.warning(
            "找回密码邮件未发送：站点地址 site_url 未配置 user_id=%s", user.id)
        return
    channel = integrations_mail.build_channel(db)
    if channel is None:
        logger.warning(
            "找回密码邮件未发送：邮件渠道未启用或未配置 user_id=%s", user.id)
        return
    link = f"{base}/login?reset={raw_token}"
    content = (
        f"你正在重置 Aetrix 账号（{user.username}）的密码。\n\n"
        f"请在 30 分钟内打开下面的链接设置新密码：\n{link}\n\n"
        "如果不是你本人操作，请忽略这封邮件，你的密码不会有任何变化。"
    )
    try:
        # 与后台「邮件与模板」测试发信走同一段投递代码（EmailChannel._deliver）。
        ok, error = channel._deliver(user.email, "重置你的 Aetrix 密码", content)
    except Exception as exc:  # noqa: BLE001 — 发信异常不许影响统一响应
        logger.error(
            "找回密码邮件发送失败 user_id=%s: %s: %s",
            user.id, type(exc).__name__, exc)
        return
    if not ok:
        logger.error("找回密码邮件发送失败 user_id=%s: %s", user.id, error)


@password_reset_router.post("/password-reset/request")
def password_reset_request(
    request: Request,
    req: PasswordResetRequestIn,
    db: Session = Depends(get_db),
):
    """申请邮箱找回密码：命中且有邮箱才真发信，三种情况对外响应完全一致。"""
    ip = client_ip(request)
    allowed, _retry = check_rate_limit(f"password-reset-request:ip:{ip}", 5, 3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    identifier = (req.identifier or "").strip()

    user = _find_user_for_reset(db, identifier) if identifier else None
    if user is None or not (user.email or "").strip():
        record_event(
            db, username=identifier or None, user_id=user.id if user else None,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="password_reset_request", detail="账号不存在或未绑定邮箱",
        )
        return PASSWORD_RESET_GENERIC_RESPONSE

    allowed, _retry = check_rate_limit(
        f"password-reset-request:user:{user.id}", 3, 3600)
    if not allowed:
        logger.warning("找回密码申请触发账号维度限流 user_id=%s", user.id)
        record_event(
            db, username=user.username, user_id=user.id,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="password_reset_request", detail="账号维度限流，未发送",
        )
        return PASSWORD_RESET_GENERIC_RESPONSE

    now = datetime.now()
    _invalidate_reset_tokens(db, user.id, now)
    raw_token = secrets.token_urlsafe(32)  # 原始令牌只进邮件链接，库里只存哈希
    db.add(models.PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        created_at=now,
        expires_at=now + PASSWORD_RESET_TOKEN_TTL,
        request_ip=ip,
    ))
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id,
        ip=log_ip(request), agent=user_agent(request), success=True,
        reason="password_reset_request", detail="重置邮件已申请",
    )
    _send_reset_email(db, user, raw_token)
    return PASSWORD_RESET_GENERIC_RESPONSE


@password_reset_router.post("/password-reset/confirm")
def password_reset_confirm(
    request: Request,
    req: PasswordResetConfirmIn,
    db: Session = Depends(get_db),
):
    """凭邮件链接令牌设置新密码：改密副作用与 change_password 完全一致
    （共用 _apply_password_change），只跳过「验旧密码」一步。"""
    ip = client_ip(request)
    allowed, _retry = check_rate_limit(f"password-reset-confirm:ip:{ip}", 10, 3600)
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")

    token_hash = hashlib.sha256((req.token or "").strip().encode("utf-8")).hexdigest()
    row = (
        db.query(models.PasswordResetToken)
        .filter(models.PasswordResetToken.token_hash == token_hash)
        .first()
    )
    now = datetime.now()
    user = None
    if row is not None:
        user = db.query(models.WebUser).filter(models.WebUser.id == row.user_id).first()
    if row is None or row.used_at is not None or row.expires_at < now or user is None:
        # 不存在 / 已用 / 已过期一律同一句，不给外部区分原因的机会。
        record_event(
            db, username=user.username if user else None,
            user_id=user.id if user else None,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="password_reset_failed", detail="重置令牌无效或已过期",
        )
        raise HTTPException(status_code=400, detail="重置链接无效或已过期，请重新申请")
    if verify_password(req.new_password, user.password_hash):
        record_event(
            db, username=user.username, user_id=user.id,
            ip=log_ip(request), agent=user_agent(request), success=False,
            reason="password_reset_failed", detail="新密码与旧密码相同",
        )
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

    _apply_password_change(db, user, req.new_password)
    row.used_at = now
    _invalidate_reset_tokens(db, user.id, now)
    db.commit()
    record_event(
        db, username=user.username, user_id=user.id,
        ip=log_ip(request), agent=user_agent(request), success=True,
        reason="password_reset", detail="经邮箱令牌重置",
    )
    return {"success": True, "message": "密码已重置，请使用新密码登录"}


@auth_router.get("/login-history")
def get_login_history(
    limit: int = 20,
    offset: int = 0,
    current_user: models.WebUser = Depends(get_current_user_jwt),
    db: Session = Depends(get_db),
):
    """本人登录记录（只读）：只查自己名下的登录与安全事件，分页倒序。

    字段白名单：时间 / IP / 地区 / 设备（UA）/ 成败 / 事件类别。
    不返回 detail（内部备注口径）等白名单外字段；查询条件写死
    user_id == 当前登录用户，不接受任何指定他人的参数。
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    q = db.query(models.LoginLog).filter(
        models.LoginLog.user_id == current_user.id)
    total = q.count()
    rows = (
        q.order_by(models.LoginLog.created_at.desc(), models.LoginLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "ip": r.ip,
                "region": r.region or "",
                "user_agent": r.user_agent,
                "success": bool(r.success),
                "reason": r.reason,
                "reason_label": AUTHLOG_REASONS.get(r.reason or "", r.reason or "-"),
            }
            for r in rows
        ],
    }
