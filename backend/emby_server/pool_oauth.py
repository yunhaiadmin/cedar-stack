"""贡献池专属 Drive 授权（2026-11-24，绑盘拆除后的入池唯一授权腿）。

背景：绑盘拆除一期删了通用绑盘的 OAuth 发起/回调，但贡献池入池
前置是先有一条 active 绑定行（PoolMember.binding_id →
UserStorageBinding），新用户因此无路入池。本模块只补这一条腿：

- 授权流仅服务入池：start 建 state 行（purpose='pool_join'
  落字）→ 用户在 Google 同意 → callback 单次验讫换 token +
  验活 → 落绑定行；入池申请仍走 pool_gate.apply_pool_membership
  既有口（容量双闸 / 管理员审批都不在本模块，不重写）；
- 不复活通用绑盘：不查 drive_binding_enabled（绑盘开关已收敛
  恒关，只管已下线的通用绑盘）；绑定行只是池成员的凭据载体，
  用户端「我的网盘」与 byos 播放语义一律不回来（providers
  名单仍不含 drive、个人盘播放腿仍不可达）；
- 安全口径与原绑盘 OAuth 同级：PKCE S256 + access_type=offline
  + prompt=consent（拿 refresh_token 的必要条件）；state 只存
  sha256、单次有效、默认 10 分钟过期；verifier 密封存；回调先
  过浏览器会话校验（nonce cookie 绑回发起浏览器，会话用户再
  做纵深校验），不过烧行拒绝；本模块只消费 purpose='pool_join'
  的 state 行，异用途行不查不断不碰；
- 授权范围只给池取流所需：默认仅 drive.file（池副本的建/读都
  是应用自创文件，在 file 覆盖内；pool_gate 的入池范围查也只
  要求 file）；drive.readonly 是受限范围、谷歌验证未过，默认
  不请求，部署者显式设 DRIVE_OAUTH_INCLUDE_READONLY=1 才两档
  并列（与原绑盘口径一致，不扩权）。已授范围一律以 Google
  token 响应回的为准入库，不拿请求串冒充；
- 只走服务器主应用凭据（DRIVE_OAUTH_CLIENT_ID/SECRET）：回调
  地址 = EMBY_PUBLIC_URL + POOL_OAUTH_CALLBACK_PATH，部署者须
  在 Google Cloud 把该地址注册为授权重定向 URI；未配凭据 /
  对外地址时 start 响亮拒绝，不猜 localhost；
- 令牌落点与存量绑定同一托管面：MountCredential(scope='user'、
  owner=本人、密封）+ UserStorageBinding(oauth_client=
  'primary')，刷新 / 验活 / 取令牌走 drive_oauth 既有口。凭据
  纪律同 drive_oauth：令牌明文只在本模块与 drive_oauth 经手，
  日志与返回值永不出令牌原文、不出密封值、不出 verifier。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.emby_server import drive_oauth as _do
from backend.emby_server import models as em
from backend.emby_server import user_bindings as ub
from backend.emby_server.drive_changes import TOKEN_URL
from backend.emby_server.env_util import env_int
from backend.emby_server.public_url import configured_public_url
from backend.emby_server.secret_sealing import (
    SealedSecretError,
    seal_secret,
    unseal_secret,
)

logger = logging.getLogger(__name__)

#: HTTP 桩点（与 drive_oauth 同一批默认实现；本模块内解析，测试
#: 可在本模块边界打桩，不碰 drive_oauth 的存量托管路径）。
_default_http_post = _do._default_http_post
_default_http_get = _do._default_http_get

#: Google 授权端点（TOKEN_URL 复用 drive_changes 的唯一常量）。
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

#: 本授权流在 state 行上的用途落字：complete 只认这一用途的行。
POOL_OAUTH_PURPOSE = "pool_join"
#: 回调路径（挂在 portal 的 user_emby_router 下，前缀固定；与已
#: 下线的绑盘回调路径不同，是池自己的回调，部署者须单独注册）。
POOL_OAUTH_CALLBACK_PATH = "/api/user/emby/pool/drive/oauth/callback"
#: 回调浏览器会话 nonce 的 cookie 名（portal 下发/读取，本模块校验）。
OAUTH_NONCE_COOKIE = "aetrix_pool_oauth_nonce"

#: 授权 state 有效期（秒；与原绑盘同 env 键、同默认值）。
STATE_TTL_ENV = "DRIVE_OAUTH_STATE_TTL_SEC"
STATE_TTL_DEFAULT = 600
#: 新授权是否同时请求 readonly（默认关；与原绑盘同一 env 开关，
#: 开关只影响“新发起的授权请求串”，已授一律以回调实授为准）。
INCLUDE_READONLY_ENV = "DRIVE_OAUTH_INCLUDE_READONLY"


class PoolOAuthError(_do.DriveOAuthError):
    """池授权失败（kind 体系与 BindingError 一致，调用方同口径
    定状态码与文案）。"""


def state_ttl_sec() -> int:
    return env_int(STATE_TTL_ENV, STATE_TTL_DEFAULT, lo=60)


def include_readonly_in_request() -> bool:
    """新授权是否同时请求 readonly（默认关；非法值按关处理）。"""
    return env_int(INCLUDE_READONLY_ENV, 0, 0, 1) == 1


def requested_scopes() -> tuple[str, ...]:
    """本次授权请求发出的范围：默认仅 drive.file（池取流所需
    的最小集）；开关开时与完整范围集两档并列。"""
    if include_readonly_in_request():
        return _do.DRIVE_OAUTH_SCOPES
    return (_do.DRIVE_FILE_SCOPE,)


def requested_scope_string() -> str:
    """授权请求实际发出的范围串（空格分隔；Google 回的已授范围
    可能少于此串，回调以实授为准入库）。"""
    return " ".join(requested_scopes())


def redirect_uri() -> str:
    """池 OAuth 回调地址；EMBY_PUBLIC_URL 未配时返回空串
    （调用方响亮拒绝）。"""
    base = configured_public_url()
    if not base:
        return ""
    return base + POOL_OAUTH_CALLBACK_PATH


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_url(redirect_uri_value: str, state: str,
                            code_challenge: str) -> str:
    """Google 授权地址（主应用凭据；access_type=offline +
    prompt=consent 是拿 refresh_token 的必要条件，缺了
    refresh_token 的绑定无法维护，完成时直接判 auth_error）。"""
    from urllib.parse import urlencode

    params = {
        "client_id": _do._client_id(),
        "redirect_uri": redirect_uri_value,
        "response_type": "code",
        "scope": requested_scope_string(),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urlencode(params)


def exchange_code_for_tokens(code: str, redirect_uri_value: str,
                             code_verifier: str,
                             http_post=None) -> dict:
    """授权码换 token（主应用凭据 + PKCE verifier；单次调用，
    失败按三档分类，网络/5xx 抛 transient）。"""
    post = http_post or _default_http_post
    try:
        resp = post(TOKEN_URL, data={
            "code": code,
            "client_id": _do._client_id(),
            "client_secret": _do._client_secret(),
            "redirect_uri": redirect_uri_value,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }, timeout=30)
    except Exception as exc:
        raise PoolOAuthError(
            "transient", f"换 token 时网络失败（可重试）：{exc}") from exc
    if resp.status_code != 200:
        kind = _do._classify_http_failure(
            resp.status_code, _do._read_error_body(resp))
        if kind is None:
            raise PoolOAuthError(
                "transient",
                f"换 token 时上游失败（可重试）：HTTP {resp.status_code}")
        raise PoolOAuthError(kind, ub.error_message(kind))
    try:
        body = resp.json()
    except Exception as exc:
        raise PoolOAuthError(
            "transient", "换 token 返回非 JSON（可重试）。") from exc
    access = (body.get("access_token") or "").strip()
    if not access:
        raise PoolOAuthError("auth_error", "换 token 未返回 access_token。")
    refresh = (body.get("refresh_token") or "").strip()
    if not refresh:
        # 没拿到 refresh_token 的绑定无法维护（过期即死），不成行。
        raise PoolOAuthError(
            "auth_error",
            "Google 未返回 refresh_token（须离线访问授权），请重新授权。")
    expires_in = int(body.get("expires_in", 3600) or 3600)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + expires_in,
        # Google token 响应原样回的已授范围（空格分隔）；用户在
        # 同意屏上逐项取消时可能少于请求串，回调以此为准入库。
        "scope": (body.get("scope") or "").strip() or None,
    }


# ---------------------------------------------------------------------------
# 回调浏览器会话校验（与原绑盘同级强度，防“诱使他人点授权链接”链）
# ---------------------------------------------------------------------------

def oauth_nonce_cookie_domain(request_host: str | None) -> str | None:
    """nonce cookie 的 Domain 推导（回调会话校验配套）。

    发起端（前端同源 XHR）与回调端（Google 注册的对外地址）同
    host → 返回 None（host-only cookie 即可）；不同 host 但共享
    父域 → 返回父域，cookie 才能跟过子域跳转；无共享父域 →
    None（该部署下浏览器流程会 fail-closed，须让发起端与回调
    端同域）。
    """
    from urllib.parse import urlsplit

    callback_host = urlsplit(configured_public_url() or "").hostname or ""
    req_host = (request_host or "").split(":")[0].strip().lower()
    cb_host = callback_host.strip().lower()
    if not req_host or not cb_host or req_host == cb_host:
        return None
    req_labels = req_host.split(".")
    cb_labels = cb_host.split(".")
    common = []
    for a, b in zip(reversed(req_labels), reversed(cb_labels)):
        if a != b:
            break
        common.append(a)
    if len(common) < 2:
        return None
    return ".".join(reversed(common))


def _pool_state_row(db: Session, state: str):
    """按哈希取池用途的 state 行（异用途/不存在一律 None，
    不泄露 state 是否存在过）。"""
    return (
        db.query(em.DriveOAuthState)
        .filter(em.DriveOAuthState.state_hash == _hash_state(state or ""),
                em.DriveOAuthState.purpose == POOL_OAUTH_PURPOSE)
        .first()
    )


def assert_pool_callback_browser_binding(db: Session, state: str,
                                         nonce_cookie: str | None,
                                         session_user_id: int | None
                                         ) -> None:
    """回调会话校验：complete 消费 state 前把关。

    堵的是“攻击者用自己合法生成的 state 诱使受害者在 Google
    同意屏点同意，把受害者令牌落到攻击者名下”的链：回调必须
    绑回“发起授权的那个浏览器 + 那个用户”：

    - 池 state 行不存在 → 直接返回，交给 complete 按 not_found
      原口径处理（不泄露 state 是否存在过）；
    - 浏览器 nonce 与行内 nonce_hash 对不上（无 cookie / 换了
      浏览器）→ 烧掉 state 行并抛 auth_error，防反复试探；
    - 请求自带可解析会话时，会话用户必须等于 state 行
      user_id，对不上同样烧行拒绝（纵深）。浏览器回跳通常不
      带会话头，此时只靠 nonce 一条腿。
    nonce 比对走 sha256 后 compare_digest，原文永不入库、不进日志。
    """
    row = _pool_state_row(db, state)
    if row is None:
        return
    nonce_ok = bool(
        nonce_cookie and getattr(row, "nonce_hash", None)
        and hmac.compare_digest(_hash_state(nonce_cookie), row.nonce_hash))
    user_ok = session_user_id is None or session_user_id == row.user_id
    if nonce_ok and user_ok:
        return
    db.delete(row)
    db.commit()
    logger.warning("池 OAuth 回调会话校验失败（已烧 state 行）")
    raise PoolOAuthError(
        "auth_error",
        "授权会话校验失败，请在发起授权的浏览器里重新授权。")


# ---------------------------------------------------------------------------
# 发起与完成
# ---------------------------------------------------------------------------

def _validate_label_and_quota(db: Session, user_id: int,
                              account_label: str) -> str:
    """label 与上限校验（start 与 complete 共用，complete 时重查
    防并发抢名）：label 去空白后 1–100 字；同用户同 provider
    重名拒绝；上限走 pool_gate.binding_limit_for_user 收口判定
    （池资格用户走池上限键，其余走个人上限），本处不另算。"""
    label = (account_label or "").strip()
    if not label or len(label) > 100:
        raise PoolOAuthError("validation", "account_label 需为 1–100 字。")
    existing = (
        db.query(em.UserStorageBinding)
        .filter(em.UserStorageBinding.user_id == user_id,
                em.UserStorageBinding.provider == ub.PROVIDER_DRIVE,
                em.UserStorageBinding.account_label == label)
        .first()
    )
    if existing is not None:
        raise PoolOAuthError(
            "validation", f"同 provider 下已存在同名绑定「{label}」。")
    count = (
        db.query(em.UserStorageBinding)
        .filter(em.UserStorageBinding.user_id == user_id)
        .count()
    )
    from backend.emby_server import pool_gate as _pool_gate

    limit = _pool_gate.binding_limit_for_user(db, user_id)
    if count >= limit:
        raise PoolOAuthError(
            "validation",
            f"绑定已达上限（{limit} 个），请先解绑不用的账号。")
    return label


def start_pool_oauth(db: Session, user_id: int,
                     account_label: str) -> dict:
    """开始池授权：建 state 行、返回 Google 授权地址。

    此时不建绑定行与凭据行——授权完先验活，没换到 token 就不
    成行。前置缺失响亮拒绝：服务器应用凭据未配、对外地址未
    配、label 非法、重名、超上限，全部 validation（400），不
    生成 state。本口不查绑盘开关（已下线）与池总开关（成员
    先行、开闸后生效，与 apply/withdraw 同口径）。
    """
    if not _do._client_id() or not _do._client_secret():
        raise PoolOAuthError(
            "validation",
            "服务器未配置 Drive OAuth 应用凭据（DRIVE_OAUTH_CLIENT_ID/SECRET），"
            "部署者须先在 Google Cloud 注册应用并配好。")
    uri = redirect_uri()
    if not uri:
        raise PoolOAuthError(
            "validation",
            "服务器未配置对外地址（EMBY_PUBLIC_URL），部署者须先配好并在 "
            "Google Cloud 把池回调地址注册为授权重定向 URI。")
    label = _validate_label_and_quota(db, user_id, account_label)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    now = datetime.now()
    row = em.DriveOAuthState(
        state_hash=_hash_state(state), user_id=user_id,
        account_label=label, sealed_verifier=seal_secret(verifier),
        nonce_hash=_hash_state(nonce),
        created_at=now, expires_at=now + timedelta(seconds=state_ttl_sec()),
        oauth_client=_do.CLIENT_PRIMARY, purpose=POOL_OAUTH_PURPOSE)
    db.add(row)
    db.commit()
    return {
        "authorization_url": build_authorization_url(uri, state, challenge),
        "expires_in": state_ttl_sec(),
        # 浏览器会话 nonce 原文：只许 portal 用它下发 HttpOnly
        # cookie（portal 返回前端前会 pop 掉本键，绝不进 JSON）。
        "_oauth_nonce": nonce,
    }


def _create_failed_pool_rows(db: Session, user_id: int, label: str,
                             kind: str) -> em.UserStorageBinding:
    """三档失败落行（与原绑盘同语义：行在、状态是失败态、用户端
    展示原因并可重新走授权，不静默、不回落）。"""
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status="invalid")
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=ub.PROVIDER_DRIVE, account_label=label,
        credential_id=cred.id, status="invalid",
        oauth_client=_do.CLIENT_PRIMARY)
    db.add(binding)
    db.flush()
    ub._mark_failed(db, binding, kind)
    db.commit()
    return binding


def complete_pool_oauth(db: Session, state: str, code: str,
                        http_post=None, http_get=None,
                        ) -> em.UserStorageBinding:
    """回调完成：池 state 单次验讫 → 换 token → 验活 → 建双行。

    - state 查无（不存在 / 异用途）/ 过期 → not_found（404），
      不泄露 state 是否存在过；
    - 换 token / 验活属三档内 → 建失败态双行并返回该行（调用
      方按 200 + status 展示原因，与原绑盘同口径）；
    - 一过性失败 → 不落行，抛 transient（502），用户须重新走
      授权（Google 授权码本身也是一次性，旧码换不第二次）；
    - 成功 → 双行 active + last_verified_at，绑定行只作池成员
      凭据载体，不自动入池——申请走 apply 口过容量闸与审批。
    无论成败，state 行在首次使用后即删（单次有效）。
    """
    uri = redirect_uri()
    row = _pool_state_row(db, state)
    if row is None:
        raise PoolOAuthError(
            "not_found", "授权会话不存在或已使用，请重新授权。")
    user_id, label = row.user_id, row.account_label
    try:
        verifier = unseal_secret(row.sealed_verifier or "")
    except SealedSecretError as exc:
        db.delete(row)
        db.commit()
        raise PoolOAuthError(
            "auth_error", "授权会话已失效（密钥轮换），请重新授权。") from exc
    expired = row.expires_at is not None and row.expires_at <= datetime.now()
    db.delete(row)
    db.commit()
    if expired:
        raise PoolOAuthError("not_found", "授权会话已过期，请重新授权。")
    if not uri:
        raise PoolOAuthError(
            "validation", "服务器未配置对外地址（EMBY_PUBLIC_URL）。")
    label = _validate_label_and_quota(db, user_id, label)
    try:
        tokens = exchange_code_for_tokens(
            code, uri, verifier, http_post=http_post)
    except PoolOAuthError as exc:
        if exc.kind in ("auth_error", "expired", "throttled"):
            return _create_failed_pool_rows(db, user_id, label, exc.kind)
        raise
    try:
        _do.check_drive_liveness(
            tokens["access_token"], http_get=http_get or _default_http_get)
    except _do.DriveOAuthError as exc:
        if exc.kind in ("auth_error", "expired", "throttled"):
            return _create_failed_pool_rows(db, user_id, label, exc.kind)
        raise
    granted = tokens.get("scope")
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential=seal_secret(_do.encode_drive_token(
            tokens["access_token"], tokens["refresh_token"],
            tokens["expires_at"], scope=granted)),
        key_id="fernet-v1", status="active")
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=ub.PROVIDER_DRIVE, account_label=label,
        credential_id=cred.id, status="active", granted_scopes=granted,
        oauth_client=_do.CLIENT_PRIMARY)
    db.add(binding)
    db.flush()
    now = datetime.now()
    binding.last_verified_at = now
    binding.last_error_kind = None
    cred.last_verified_at = now
    cred.last_error_kind = None
    db.commit()
    return binding
