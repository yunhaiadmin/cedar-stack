"""账号安全三件套的服务层：TOTP 动态验证码 / Passkey（WebAuthn）/ 平台 API Key。

端点在 backend/api/account_security.py；登录链的接入在 backend/api/emby_portal.py
（密码登录的 TOTP 校验、Passkey 登录、login-factors 探测）。本模块只放可独立
测试的纯逻辑：配置读取、密钥密封、验证码校验（含防重放）、WebAuthn 选项生成与
断言校验、平台 Key 的生成/哈希/校验。

安全口径：
- TOTP 密钥只以 Fernet 密封值落库（复用 secret_sealing 的 seal_secret，
  密钥派生自 SECRET_KEY），明文永不落库、永不进日志；
- 平台 API Key 明文只在创建时返回一次，库里只存带 pepper 的 SHA256
  （与 backend/api_key.py 同派生口径、不同域标签，两套 Key 分域不混表）；
- WebAuthn challenge 用 HMAC 签名的无状态令牌下发（purpose + user_id +
  challenge + exp），服务端不存 challenge 状态；
- 配置走 SystemConfig（与 devices.py 同读取口径）：passkey_max_per_user
  （默认 5）、platform_api_key_validity_days（默认 365）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from backend import models

logger = logging.getLogger(__name__)

# TOTP 发行方名（验证器 App 里显示的账号前缀）；站点品牌名可配前先用产品名。
TOTP_ISSUER = "Aetrix"
# WebAuthn RP 展示名（系统弹窗里显示）。
WEBAUTHN_RP_NAME = "Aetrix"

_CONFIG_PASSKEY_MAX = "passkey_max_per_user"
_CONFIG_API_KEY_DAYS = "platform_api_key_validity_days"
DEFAULT_PASSKEY_MAX = 5
DEFAULT_API_KEY_VALIDITY_DAYS = 365

_CHALLENGE_TTL_SECONDS = 300
_CHALLENGE_LABEL = b"aetrix-webauthn-challenge"

_PLATFORM_KEY_PREFIX = "pak_"
_PLATFORM_PEPPER_ENV = "PLATFORM_API_KEY_PEPPER"
_PLATFORM_DERIVE_LABEL = b"aetrix-platform-api-key"


# ---------------------------------------------------------------------------
# 配置（SystemConfig，与 devices.py 的 _config_int 同读取口径）
# ---------------------------------------------------------------------------

def _config_int(db: Session, key: str, default: int) -> int:
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row is None or row.value is None:
        return default
    try:
        return int(str(row.value).strip())
    except (TypeError, ValueError):
        return default


def passkey_max_per_user(db: Session) -> int:
    """每人 Passkey 上限（配置 passkey_max_per_user，默认 5，至少 1）。"""
    return max(_config_int(db, _CONFIG_PASSKEY_MAX, DEFAULT_PASSKEY_MAX), 1)


def platform_api_key_validity_days(db: Session) -> int:
    """平台 API Key 固定有效期天数（配置 platform_api_key_validity_days，默认 365）。"""
    return max(_config_int(db, _CONFIG_API_KEY_DAYS, DEFAULT_API_KEY_VALIDITY_DAYS), 1)


# ---------------------------------------------------------------------------
# TOTP（RFC 6238，pyotp）
# ---------------------------------------------------------------------------

def _seal(plaintext: str) -> str:
    """密封 TOTP 密钥：复用全仓唯一的 Fernet 密封实现（懒导入防循环）。"""
    from backend.emby_server.secret_sealing import seal_secret

    return seal_secret(plaintext)


def _unseal(sealed: str) -> str:
    from backend.emby_server.secret_sealing import unseal_secret

    return unseal_secret(sealed)


def get_totp_row(db: Session, user_id: int) -> Optional[models.UserTotp]:
    return db.query(models.UserTotp).filter(models.UserTotp.user_id == user_id).first()


def totp_enabled_for(db: Session, user_id: int) -> bool:
    row = get_totp_row(db, user_id)
    return bool(row and row.enabled)


def start_totp_setup(db: Session, user: models.WebUser) -> tuple[str, str]:
    """开始启用流程：生成新密钥、密封落库为「待确认」，返回 (明文密钥, otpauth URI)。

    明文密钥只在这一步返回给本人（渲染二维码/手动录入用），库里只有密封值。
    已启用时拒绝重新 setup（须先关闭），防止已登录会话被静默换绑。
    """
    import pyotp

    row = get_totp_row(db, user.id)
    if row and row.enabled:
        raise ValueError("动态验证码已启用，请先关闭再重新设置")
    secret = pyotp.random_base32()
    sealed = _seal(secret)
    if row is None:
        row = models.UserTotp(user_id=user.id, secret_sealed=sealed, enabled=False)
        db.add(row)
    else:
        row.secret_sealed = sealed
        row.enabled = False
        row.last_used_counter = None
        row.confirmed_at = None
    db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=TOTP_ISSUER)
    return secret, uri


def _match_totp_counter(secret: str, code: str, after_counter: int) -> Optional[int]:
    """在 [当前步-1, 当前步+1] 窗口内找与 code 匹配的计数器步。

    只接受步数 > after_counter 的码（防重放：同一码第二次用时步数已不大于
    已用步，直接落选）。返回命中的步数，无命中返回 None。
    """
    import pyotp

    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now_counter = int(time.time()) // 30
    for counter in (now_counter - 1, now_counter, now_counter + 1):
        if counter <= after_counter:
            continue
        if hmac.compare_digest(totp.at(counter * 30), code):
            return counter
    return None


def confirm_totp_setup(db: Session, user_id: int, code: str) -> bool:
    """确认启用：用待确认密钥校验一次码，对了才置 enabled。

    命中的步数记入 last_used_counter——设置时用掉的这枚码不能再拿去登录。
    """
    row = get_totp_row(db, user_id)
    if row is None or row.enabled:
        return False
    secret = _unseal(row.secret_sealed)
    counter = _match_totp_counter(secret, code, row.last_used_counter or -1)
    if counter is None:
        return False
    row.enabled = True
    row.confirmed_at = datetime.now()
    row.last_used_counter = counter
    db.commit()
    return True


def verify_login_totp(db: Session, user_id: int, code: str) -> bool:
    """登录第二步校验：已启用才校验；成功后推进 last_used_counter 防重放。"""
    row = get_totp_row(db, user_id)
    if row is None or not row.enabled:
        return False
    secret = _unseal(row.secret_sealed)
    counter = _match_totp_counter(secret, code, row.last_used_counter or -1)
    if counter is None:
        return False
    row.last_used_counter = counter
    db.commit()
    return True


def disable_totp(db: Session, user_id: int) -> None:
    """关闭 TOTP：整行删除（密钥密封值一并销毁）。调用方负责先验密码。"""
    row = get_totp_row(db, user_id)
    if row is not None:
        db.delete(row)
        db.commit()


# ---------------------------------------------------------------------------
# WebAuthn challenge（HMAC 签名无状态令牌）
# ---------------------------------------------------------------------------

def _challenge_key() -> bytes:
    root = (os.getenv("SECRET_KEY") or "").strip()
    return hmac.new(root.encode("utf-8"), _CHALLENGE_LABEL, hashlib.sha256).digest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_challenge_token(purpose: str, user_id: int, challenge: bytes) -> str:
    """把一次 WebAuthn challenge 签成令牌交给客户端，verify 时原样带回。

    载荷只含 purpose/user_id/challenge/exp，不含任何凭据材料；HMAC 防篡改，
    purpose 与 user_id 双绑定——注册的令牌不能拿去登录、A 的不能给 B 用。
    """
    payload = {
        "p": purpose,
        "u": int(user_id),
        "c": _b64url_encode(challenge),
        "exp": int(time.time()) + _CHALLENGE_TTL_SECONDS,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_challenge_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def parse_challenge_token(token: str, purpose: str, user_id: int) -> bytes:
    """校验 challenge 令牌并取回 challenge 字节。任何不符抛 ValueError。"""
    try:
        body, sig_text = (token or "").split(".", 1)
        want = hmac.new(_challenge_key(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _b64url_decode(sig_text)):
            raise ValueError("challenge 令牌签名不对")
        payload = json.loads(_b64url_decode(body))
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — 格式错误统一按无效令牌处理
        raise ValueError("challenge 令牌格式不对") from exc
    if payload.get("p") != purpose or int(payload.get("u") or -1) != int(user_id):
        raise ValueError("challenge 令牌用途或用户不匹配")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("challenge 已过期，请重试")
    return _b64url_decode(str(payload.get("c") or ""))


def rp_context(request) -> tuple[str, str]:
    """从请求推导 (rp_id, origin)：优先浏览器 Origin 头（WebAuthn 的权威来源），
    缺失时回落请求 URL。反代部署下 Origin 才是用户实际访问的源。"""
    origin = (request.headers.get("origin") or "").strip() if request is not None else ""
    if origin:
        parts = urlsplit(origin)
        if parts.hostname:
            return parts.hostname, origin
    url = request.url
    host = url.hostname or "localhost"
    return host, f"{url.scheme}://{url.netloc}"


# ---------------------------------------------------------------------------
# Passkey（WebAuthn，py_webauthn）
# ---------------------------------------------------------------------------

def list_passkeys(db: Session, user_id: int) -> list[models.UserPasskey]:
    return (
        db.query(models.UserPasskey)
        .filter(models.UserPasskey.user_id == user_id)
        .order_by(models.UserPasskey.id.asc())
        .all()
    )


def get_passkey_by_credential_id(db: Session, credential_id: str) -> Optional[models.UserPasskey]:
    if not credential_id:
        return None
    return (
        db.query(models.UserPasskey)
        .filter(models.UserPasskey.credential_id == credential_id)
        .first()
    )


def registration_options(db: Session, user: models.WebUser, rp_id: str):
    """生成注册选项 + challenge 令牌。返回 (options_json_dict, challenge_token)。"""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    existing = list_passkeys(db, user.id)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=WEBAUTHN_RP_NAME,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username,
        user_display_name=user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id))
            for row in existing
        ],
    )
    token = issue_challenge_token("register", user.id, options.challenge)
    return json.loads(options_to_json(options)), token


def verify_and_store_registration(
    db: Session,
    user: models.WebUser,
    credential: dict,
    challenge_token: str,
    rp_id: str,
    origin: str,
    device_name: Optional[str] = None,
) -> models.UserPasskey:
    """校验注册响应并落库。超上限 / challenge 不符 / 校验失败抛 ValueError。"""
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    if len(list_passkeys(db, user.id)) >= passkey_max_per_user(db):
        raise ValueError(f"Passkey 数量已达上限（{passkey_max_per_user(db)} 枚）")
    challenge = parse_challenge_token(challenge_token, "register", user.id)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as exc:  # noqa: BLE001 — 库的校验异常统一转成业务错误
        logger.warning("Passkey 注册校验失败: user=%s", user.id)
        raise ValueError("Passkey 注册校验未通过，请重试") from exc
    cred = verified  # webauthn 3.x：VerifiedRegistration 直接带凭据字段（无 .credential 包装）
    credential_id = bytes_to_base64url(cred.credential_id)
    if get_passkey_by_credential_id(db, credential_id) is not None:
        raise ValueError("这枚 Passkey 已经绑定过了")
    transports = None
    try:
        raw_transports = (credential.get("response") or {}).get("transports")
        if isinstance(raw_transports, list) and raw_transports:
            transports = ",".join(str(t) for t in raw_transports)[:255]
    except Exception:  # noqa: BLE001 — transports 只是展示信息，取不到不阻塞
        transports = None
    row = models.UserPasskey(
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(cred.credential_public_key),
        sign_count=int(cred.sign_count or 0),
        transports=transports,
        device_name=(device_name or "").strip()[:100] or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def authentication_options(db: Session, user: models.WebUser, rp_id: str):
    """生成登录断言选项 + challenge 令牌。无 Passkey 时抛 ValueError。"""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
    )

    rows = list_passkeys(db, user.id)
    if not rows:
        raise ValueError("该账号尚未绑定 Passkey")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id))
            for row in rows
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    token = issue_challenge_token("auth", user.id, options.challenge)
    return json.loads(options_to_json(options)), token


def verify_login_assertion(
    db: Session,
    user: models.WebUser,
    credential: dict,
    challenge_token: str,
    rp_id: str,
    origin: str,
) -> models.UserPasskey:
    """校验登录断言：按 credential id 找行、验签、回写 sign_count 与最近使用时间。"""
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    credential_id = str((credential or {}).get("id") or "")
    row = get_passkey_by_credential_id(db, credential_id)
    if row is None or row.user_id != user.id:
        raise ValueError("这枚 Passkey 不属于该账号")
    challenge = parse_challenge_token(challenge_token, "auth", user.id)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(row.public_key),
            credential_current_sign_count=int(row.sign_count or 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Passkey 登录校验失败: user=%s", user.id)
        raise ValueError("Passkey 校验未通过，请重试") from exc
    row.sign_count = int(verified.new_sign_count or 0)
    row.last_used_at = datetime.now()
    db.commit()
    return row


# ---------------------------------------------------------------------------
# 平台 API Key（用户自助，与服务间 api_keys 分域）
# ---------------------------------------------------------------------------

def has_strong_factor(db: Session, user_id: int) -> bool:
    """平台 Key 创建前置：已启用 TOTP 或已绑至少一枚 Passkey。"""
    if totp_enabled_for(db, user_id):
        return True
    return len(list_passkeys(db, user_id)) > 0


def _platform_pepper() -> str:
    explicit = (os.getenv(_PLATFORM_PEPPER_ENV) or "").strip()
    if explicit:
        return explicit
    root = (os.getenv("SECRET_KEY") or "").strip()
    if not root:
        return ""
    return hmac.new(root.encode("utf-8"), _PLATFORM_DERIVE_LABEL, hashlib.sha256).hexdigest()


def _hash_platform_key(plaintext: str) -> str:
    return hashlib.sha256(f"{plaintext}{_platform_pepper()}".encode("utf-8")).hexdigest()


def create_platform_api_key(
    db: Session, user: models.WebUser, name: str
) -> tuple[models.UserPlatformApiKey, str]:
    """创建平台 Key。返回 (行, 明文)——明文只返回这一次。

    前置（已绑 TOTP 或 Passkey）由本函数硬校验，端点不许绕过。
    """
    if not has_strong_factor(db, user.id):
        raise PermissionError("请先启用动态验证码或绑定 Passkey，再创建 API Key")
    plaintext = _PLATFORM_KEY_PREFIX + secrets.token_urlsafe(36)
    key_id = plaintext[:12]
    for _ in range(5):
        exists = (
            db.query(models.UserPlatformApiKey)
            .filter(models.UserPlatformApiKey.key_id == key_id)
            .first()
        )
        if exists is None:
            break
        plaintext = _PLATFORM_KEY_PREFIX + secrets.token_urlsafe(36)
        key_id = plaintext[:12]
    row = models.UserPlatformApiKey(
        user_id=user.id,
        key_id=key_id,
        key_hash=_hash_platform_key(plaintext),
        name=(name or "").strip()[:100] or "未命名",
        expires_at=datetime.now() + timedelta(days=platform_api_key_validity_days(db)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def list_platform_api_keys(db: Session, user_id: int) -> list[models.UserPlatformApiKey]:
    return (
        db.query(models.UserPlatformApiKey)
        .filter(models.UserPlatformApiKey.user_id == user_id)
        .order_by(models.UserPlatformApiKey.id.desc())
        .all()
    )


def revoke_platform_api_key(db: Session, user_id: int, key_row_id: int) -> bool:
    """撤销本人的 Key（软删：is_active=False）。不是自己的返回 False。"""
    row = (
        db.query(models.UserPlatformApiKey)
        .filter(
            models.UserPlatformApiKey.id == key_row_id,
            models.UserPlatformApiKey.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return False
    row.is_active = False
    db.commit()
    return True


def verify_platform_api_key(db: Session, plaintext: str) -> Optional[models.UserPlatformApiKey]:
    """校验明文平台 Key（X-API-Key 头）。成功时更新 last_used_at。

    失败原因不区分（不存在/已撤销/已过期一律 None），防枚举。
    """
    if not plaintext or not plaintext.startswith(_PLATFORM_KEY_PREFIX):
        return None
    key_id = plaintext[:12]
    candidates = (
        db.query(models.UserPlatformApiKey)
        .filter(
            models.UserPlatformApiKey.key_id == key_id,
            models.UserPlatformApiKey.is_active == True,  # noqa: E712
        )
        .all()
    )
    want = _hash_platform_key(plaintext)
    for row in candidates:
        if hmac.compare_digest(row.key_hash or "", want):
            if row.expires_at and row.expires_at < datetime.now():
                return None
            try:
                row.last_used_at = datetime.now()
                db.commit()
            except Exception:  # noqa: BLE001 — 使用时间记不上不阻塞调用
                db.rollback()
            return row
    return None


# ---------------------------------------------------------------------------
# 活跃会话合并模型（G-60/G-62~G-66，2026-11-18）
# ---------------------------------------------------------------------------
# 三类合并列表：①网页登录会话（refresh 家族行 user_web_sessions）
# ②媒体客户端设备（user_devices）③弹幕 Token（平台 API Key
# user_platform_api_keys——唯一满足 G-66 的既有 Token 体系：有效期默认
# 365 天、可改名、可作废；站内没有单独的「弹幕 Token」表，G-19 盘点已
# 确认「弹幕 API 本站无此业务」，故第三类落到平台 Key，不新造业务）。
# 上限 N 读全站 device_limit_per_user（代码里没有套餐级设备上限权益键；
# 订阅页已明文「登录设备上限是全站口径」，定案「套餐 10」在代码里无此
# 口径，故以现行全站配置为准）。

SESSION_KIND_WEB = "web"
SESSION_KIND_DEVICE = "device"
SESSION_KIND_DANMAKU = "danmaku"


def _iso(dt):
    return dt.isoformat() if dt else None


def record_web_session(db: Session, user: models.WebUser, fid: str,
                       user_agent=None, ip=None) -> Optional[models.UserWebSession]:
    """登录时建网页会话行（一次登录＝一个 refresh 家族＝一行）。

    失败不抛：会话登记绝不能把登录本身弄坏。调用方（emby_portal
    _issue_auth_response）另有 try/except 双保险。
    """
    if not fid:
        return None
    try:
        from backend.security import REFRESH_TOKEN_EXPIRE_DAYS
        now = datetime.now()
        row = (
            db.query(models.UserWebSession)
            .filter(models.UserWebSession.user_id == user.id,
                    models.UserWebSession.fid == fid)
            .first()
        )
        if row is None:
            row = models.UserWebSession(
                user_id=user.id, fid=fid,
                user_agent=(user_agent or "")[:300] or None,
                ip=(ip or "")[:64] or None,
                created_at=now, last_seen_at=now,
                expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
                token_version=int(getattr(user, "token_version", 0) or 0),
            )
            db.add(row)
        else:
            row.last_seen_at = now
            row.expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        db.commit()
        db.refresh(row)
        return row
    except Exception:  # noqa: BLE001 — 登记失败只记日志，不阻塞登录
        db.rollback()
        logger.exception("record_web_session 失败 user_id=%s", user.id)
        return None


def touch_web_session(db: Session, user_id: int, fid: str) -> None:
    """refresh 轮换成功后刷新该家族行的最近活动与到期（登录＋30 天）。"""
    if not fid:
        return
    try:
        from backend.security import REFRESH_TOKEN_EXPIRE_DAYS
        row = (
            db.query(models.UserWebSession)
            .filter(models.UserWebSession.user_id == user_id,
                    models.UserWebSession.fid == fid)
            .first()
        )
        if row is None:
            return
        now = datetime.now()
        row.last_seen_at = now
        row.expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        db.commit()
    except Exception:  # noqa: BLE001 — 刷新失败不阻塞换票
        db.rollback()
        logger.exception("touch_web_session 失败 user_id=%s", user_id)


def clear_web_sessions(db: Session, user_id: int) -> None:
    """登出后清掉该用户全部网页会话行（登出 bump token_version，全家族已死）。"""
    try:
        db.query(models.UserWebSession).filter(
            models.UserWebSession.user_id == user_id).delete(
            synchronize_session=False)
        db.commit()
    except Exception:  # noqa: BLE001 — 清理失败不阻塞登出
        db.rollback()
        logger.exception("clear_web_sessions 失败 user_id=%s", user_id)


def _prune_stale_web_sessions(db: Session, user: models.WebUser) -> None:
    """剪掉已失效的网页会话行：家族已吊销 / token_version 已 bump（改密/
    登出）/ 已过期。列表每次读前剪一次，行不堆积。"""
    from backend.security import is_family_revoked
    now = datetime.now()
    current_tv = int(getattr(user, "token_version", 0) or 0)
    rows = db.query(models.UserWebSession).filter(
        models.UserWebSession.user_id == user.id).all()
    stale_ids = []
    for row in rows:
        stale = (
            row.expires_at is not None and row.expires_at < now
        ) or int(row.token_version or 0) != current_tv
        if not stale and row.fid:
            try:
                stale = is_family_revoked(db, row.fid)
            except Exception:  # noqa: BLE001 — 吊销表查不到时不剪（fail-open 于剪枝）
                stale = False
        if stale:
            stale_ids.append(row.id)
    if stale_ids:
        db.query(models.UserWebSession).filter(
            models.UserWebSession.id.in_(stale_ids)).delete(
            synchronize_session=False)
        db.commit()


def _device_expires_at(db: Session, user_id: int, device_id: str):
    """设备到期＝其最新未吊销 Emby 令牌的 expires_at；无令牌/无到期＝None
    （历史令牌视为永不过期，与 EmbyApiToken 列注释同口径）。"""
    from backend.emby_server import models as em
    row = (
        db.query(em.EmbyApiToken)
        .filter(em.EmbyApiToken.user_id == user_id,
                em.EmbyApiToken.device_id == device_id,
                em.EmbyApiToken.is_revoked == False)  # noqa: E712
        .order_by(em.EmbyApiToken.last_used_at.desc())
        .first()
    )
    return row.expires_at if row is not None else None


def list_active_sessions(db: Session, user: models.WebUser) -> dict:
    """会话聚合列表（G-60/G-62/G-64）：三类合并，按最近活动倒序。

    返回 {"limit", "total", "counts": {"all","web","danmaku"}, "items": [...]}。
    行字段：id（web:<fid> / device:<device_id> / token:<id>）、kind、
    display_name、user_agent（仅网页）、client/app_version（仅设备）、ip、
    created_at/last_seen_at/expires_at（ISO）、locked、can_lock。
    已禁用设备照常列出（带 is_blocked 标记）但不计入 total；过期 Token 不列。
    """
    from backend import devices as _devices
    _prune_stale_web_sessions(db, user)
    limit = _devices.device_limit(db)
    items: list[dict] = []

    for row in (
        db.query(models.UserWebSession)
        .filter(models.UserWebSession.user_id == user.id)
        .order_by(models.UserWebSession.last_seen_at.desc())
        .all()
    ):
        items.append({
            "id": f"{SESSION_KIND_WEB}:{row.fid}",
            "kind": SESSION_KIND_WEB,
            "display_name": row.display_name,
            "user_agent": row.user_agent,
            "client": None,
            "app_version": None,
            "ip": row.ip,
            "created_at": _iso(row.created_at),
            "last_seen_at": _iso(row.last_seen_at),
            "expires_at": _iso(row.expires_at),
            "locked": bool(row.locked),
            "can_lock": True,
        })

    for dev in _devices.list_devices(db, user.id):
        items.append({
            "id": f"{SESSION_KIND_DEVICE}:{dev.device_id}",
            "kind": SESSION_KIND_DEVICE,
            "display_name": dev.name,
            "user_agent": None,
            "client": dev.client,
            "app_version": dev.app_version,
            "ip": dev.ip,
            "created_at": _iso(dev.first_seen_at),
            "last_seen_at": _iso(dev.last_seen_at),
            "expires_at": _iso(_device_expires_at(db, user.id, dev.device_id)),
            "locked": bool(getattr(dev, "locked", False)),
            "can_lock": True,
            "is_blocked": bool(dev.is_blocked),
        })

    now = datetime.now()
    for key in list_platform_api_keys(db, user.id):
        if not key.is_active:
            continue
        if key.expires_at is not None and key.expires_at < now:
            continue
        items.append({
            "id": f"{SESSION_KIND_DANMAKU}:{key.id}",
            "kind": SESSION_KIND_DANMAKU,
            "display_name": key.name,
            "user_agent": None,
            "client": None,
            "app_version": None,
            "ip": None,
            "created_at": _iso(key.created_at),
            "last_seen_at": _iso(key.last_used_at),
            "expires_at": _iso(key.expires_at),
            "locked": False,
            "can_lock": False,  # G-66：弹幕 Token 无锁定
        })

    items.sort(key=lambda it: it["last_seen_at"] or "", reverse=True)
    total = sum(
        1 for it in items
        if not (it["kind"] == SESSION_KIND_DEVICE and it.get("is_blocked"))
    )
    return {
        "limit": limit,
        "total": total,
        "counts": {
            "all": len(items),
            "web": sum(1 for it in items if it["kind"] == SESSION_KIND_WEB),
            "danmaku": sum(1 for it in items if it["kind"] == SESSION_KIND_DANMAKU),
        },
        "items": items,
    }


def _split_session_id(session_id: str) -> tuple[str, str]:
    """opaque 会话 id 拆回 (kind, 原始标识)；格式不对抛 ValueError。"""
    kind, sep, raw = (session_id or "").partition(":")
    if not sep or not raw or kind not in (
        SESSION_KIND_WEB, SESSION_KIND_DEVICE, SESSION_KIND_DANMAKU,
    ):
        raise ValueError("会话不存在")
    return kind, raw


def set_session_lock(db: Session, user: models.WebUser, session_id: str,
                     locked: bool) -> Optional[dict]:
    """锁定开关（G-65）：只改防淘汰标记。弹幕 Token 无锁定，400 级拒绝
    （调用方转 ValueError→400）。返回更新后的行字典，未命中返回 None。"""
    kind, raw = _split_session_id(session_id)
    if kind == SESSION_KIND_DANMAKU:
        raise ValueError("弹幕 Token 没有锁定")
    if kind == SESSION_KIND_WEB:
        row = (
            db.query(models.UserWebSession)
            .filter(models.UserWebSession.user_id == user.id,
                    models.UserWebSession.fid == raw)
            .first()
        )
        if row is None:
            return None
        row.locked = bool(locked)
        db.commit()
        return {"id": session_id, "locked": bool(row.locked)}
    row = (
        db.query(models.UserDevice)
        .filter(models.UserDevice.user_id == user.id,
                models.UserDevice.device_id == raw)
        .first()
    )
    if row is None:
        return None
    row.locked = bool(locked)
    db.commit()
    return {"id": session_id, "locked": bool(row.locked)}


def rename_session(db: Session, user: models.WebUser, session_id: str,
                   display_name: str) -> Optional[dict]:
    """改名（G-65）：网页会话改备注名、设备改设备名、Token 改 Key 名。"""
    name = (display_name or "").strip()[:100]
    if not name:
        raise ValueError("名称不能为空")
    kind, raw = _split_session_id(session_id)
    if kind == SESSION_KIND_WEB:
        row = (
            db.query(models.UserWebSession)
            .filter(models.UserWebSession.user_id == user.id,
                    models.UserWebSession.fid == raw)
            .first()
        )
        if row is None:
            return None
        row.display_name = name
        db.commit()
        return {"id": session_id, "display_name": name}
    if kind == SESSION_KIND_DEVICE:
        row = (
            db.query(models.UserDevice)
            .filter(models.UserDevice.user_id == user.id,
                    models.UserDevice.device_id == raw)
            .first()
        )
        if row is None:
            return None
        row.name = name
        db.commit()
        return {"id": session_id, "display_name": name}
    row = (
        db.query(models.UserPlatformApiKey)
        .filter(models.UserPlatformApiKey.user_id == user.id,
                models.UserPlatformApiKey.id == int(raw))
        .first()
    )
    if row is None:
        return None
    row.name = name
    db.commit()
    return {"id": session_id, "display_name": name}


def revoke_session(db: Session, user: models.WebUser, session_id: str) -> bool:
    """删除＝立即吊销（G-65）：网页会话吊销整个 refresh 家族＋删行；
    设备移除＋吊销其 Emby 令牌（复用 devices.remove_device）；
    Token 作废（复用 revoke_platform_api_key）。未命中返回 False。"""
    from backend import devices as _devices
    kind, raw = _split_session_id(session_id)
    if kind == SESSION_KIND_WEB:
        from backend.security import revoke_token_family
        row = (
            db.query(models.UserWebSession)
            .filter(models.UserWebSession.user_id == user.id,
                    models.UserWebSession.fid == raw)
            .first()
        )
        if row is None:
            return False
        revoke_token_family(db, raw, user.id, reason="session_revoked")
        db.query(models.UserWebSession).filter(
            models.UserWebSession.id == row.id).delete(
            synchronize_session=False)
        db.commit()
        return True
    if kind == SESSION_KIND_DEVICE:
        return _devices.remove_device(db, user.id, raw, revoke_tokens=True)
    try:
        key_id = int(raw)
    except (TypeError, ValueError):
        return False
    return revoke_platform_api_key(db, user.id, key_id)


def evict_for_new_session(db: Session, user: models.WebUser,
                          exclude_fid: Optional[str] = None) -> list[str]:
    """新登录超限时淘汰（G-60）：上限>0 且活跃会话数已达上限时，逐个淘汰
    最久未活动的未锁定会话（跨三类），直到腾出位置或无可淘汰者。
    管理员（is_staff）沿用设备上限的放行口径，不淘汰。返回被淘汰的会话 id。
    """
    from backend import devices as _devices
    if getattr(user, "is_staff", False):
        return []
    limit = _devices.device_limit(db)
    if not limit:
        return []
    evicted: list[str] = []
    while True:
        body = list_active_sessions(db, user)
        if body["total"] <= limit:
            break
        candidates = [
            it for it in body["items"]
            if not it["locked"] and it["id"] != f"{SESSION_KIND_WEB}:{exclude_fid}"
            and not (it["kind"] == SESSION_KIND_DEVICE and it.get("is_blocked"))
        ]
        if not candidates:
            break
        oldest = min(candidates, key=lambda it: it["last_seen_at"] or "")
        if revoke_session(db, user, oldest["id"]):
            evicted.append(oldest["id"])
        else:
            break
    return evicted
