"""存量 Drive 绑定的令牌托管（绑盘拆除一期后的剩余职责）。

绑盘（用户绑定自带 Google Drive 供自己播）已于 2026-11-24 拆除
一期下线：新建绑定的全部入口（OAuth 发起/回调、建绑端点、管理
端开关、用户端绑盘页面）已删除，绑定开关收敛为代码常量关
（user_bindings.drive_binding_enabled）。本模块只剩存量绑定的
令牌托管三件事：

1. 取令牌唯一口：get_token_for_user / get_binding_drive_access_token
   只读本人 scope=user 凭据行，绝不回落 SA 池、rclone.conf、
   scope=source 行（回落会查错盘、跨用户）；
2. refresh 续期 + 验活 + 失效落态（复用绑定状态机 active /
   invalid / expired 与失效三档文案，不静默回落）；
3. 凭据维护工人：后台常驻刷新 / 过期标记 / 节流退避，开关门控、
   默认关；失败只记警告、不挡播放与扫描。

凭据纪律不变：本模块是唯一经手 Drive 用户令牌
明文的地方（刷新、验活时解密封后立即用、用完不存变量外泄）；
日志与返回值永远不出令牌原文、不出密封值。

服务器级 OAuth 应用凭据（client_id / client_secret）是部署者
在 Google Cloud 注册的应用凭据，走文件配置（env），不进库、
不按用户存；刷新时按绑定行记录的来源客户端（oauth_client）
选凭据——refresh_token 是签给具体应用的，用错客户端必失败，
不许靠“试一遍”猜。两套凭据（老应用 DRIVE_OAUTH_*、第二入口
DRIVE_OAUTH2_*）都只为存量绑定的刷新保留。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

from backend.emby_server import models as em
from backend.emby_server import user_bindings as ub
from backend.emby_server.drive_changes import (
    OAUTH_TOKEN_REFRESH_MARGIN_SEC,
    TOKEN_URL,
)
from backend.emby_server.env_util import env_int
from backend.emby_server.secret_sealing import (
    SealedSecretError,
    seal_secret,
    unseal_secret,
)

logger = logging.getLogger(__name__)

#: Drive about 端点（验活用；TOKEN_URL 复用 drive_changes 的唯一常量）。
DRIVE_ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
#: 只读 scope（Restricted）：读侧（共享源读取、存量绑定文件读取、
#: 用户盘任意文件读取）全靠它。写侧（转存目录建立、files.copy
#: 建副本）不走它。
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
#: 写 scope（Console 列为 Non-sensitive）：只覆盖“应用创建或打开的
#: 文件”——转存目录 aetrix-transfer 与转存副本都是应用创建的，
#: 正在此覆盖内；用户用 Drive 选择器明确交给应用的文件也在内。
#: 注意：单 file 不够读共享源文件与存量绑定文件（都不在 file
#: 覆盖里），故存量授权的完整范围集是两档并列（readonly + file）；
#: binding_needs_rebind 按此判定已授范围是否缺档。
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
#: 完整范围集（存量绑定已授范围的完整形态）。
DRIVE_OAUTH_SCOPES = (DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE)

#: 服务器级 OAuth 应用凭据（部署者文件配置，不进库）。
CLIENT_ID_ENV = "DRIVE_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "DRIVE_OAUTH_CLIENT_SECRET"
#: 第二入口（新应用）的服务器级凭据：与老应用并列存在、互不
#: 覆盖；刷新按绑定行记录选凭据，两套都保留。
CLIENT2_ID_ENV = "DRIVE_OAUTH2_CLIENT_ID"
CLIENT2_SECRET_ENV = "DRIVE_OAUTH2_CLIENT_SECRET"

#: 入口/客户端落字口径（绑定行 oauth_client 列）：
#: primary = 老应用（DRIVE_OAUTH_CLIENT_*）；oauth2 = 第二入口
#: （DRIVE_OAUTH2_*）。NULL（本功能上线前的老行）与未知值在读
#: 侧一律归一为 primary，与上线前行为一致。
CLIENT_PRIMARY = "primary"
CLIENT_OAUTH2 = "oauth2"

#: 维护工人开关与周期（默认关；周期是我方起步值，非 FMBY 常量）。
MAINT_ENABLED_ENV = "BYOS_DRIVE_CRED_MAINT_ENABLED"
MAINT_INTERVAL_ENV = "BYOS_DRIVE_CRED_MAINT_INTERVAL_SEC"
MAINT_INTERVAL_DEFAULT = 3600


class DriveOAuthError(ub.BindingError):
    """Drive 令牌托管失败（复用 BindingError 的 kind 体系，调用方
    同口径定状态码与文案；kind 取值与 user_bindings 一致）。"""


def _normalize_client(client: str | None) -> str:
    """入口/客户端值归一：只有字面 oauth2 才是第二入口，其余
    （primary / None / 未知值）一律按老应用，与上线前行为一致。"""
    return CLIENT_OAUTH2 if client == CLIENT_OAUTH2 else CLIENT_PRIMARY


def _client_id(client: str | None = None) -> str:
    if _normalize_client(client) == CLIENT_OAUTH2:
        return (os.getenv(CLIENT2_ID_ENV, "") or "").strip()
    return (os.getenv(CLIENT_ID_ENV, "") or "").strip()


def _client_secret(client: str | None = None) -> str:
    if _normalize_client(client) == CLIENT_OAUTH2:
        return (os.getenv(CLIENT2_SECRET_ENV, "") or "").strip()
    return (os.getenv(CLIENT_SECRET_ENV, "") or "").strip()


def binding_oauth_client(binding) -> str:
    """绑定行记录的来源客户端（NULL / 未知值归一 primary）。"""
    return _normalize_client(getattr(binding, "oauth_client", None))


def maint_enabled() -> bool:
    return env_int(MAINT_ENABLED_ENV, 0, 0, 1) == 1


def maint_interval_sec() -> int:
    return env_int(MAINT_INTERVAL_ENV, MAINT_INTERVAL_DEFAULT, lo=60)


# ---------------------------------------------------------------------------
# 令牌 JSON 编解码（密封值里存的就是这份 JSON，不存明文列）
# ---------------------------------------------------------------------------

def encode_drive_token(access_token: str, refresh_token: str,
                       expires_at: int, scope: str | None = None) -> str:
    """密封令牌 JSON：scope 存实授范围（Google token 响应原样回的
    空格分隔串；刷新时沿用旧存值，不许用请求串覆盖——刷新响应
    未必带 scope，拿请求串覆盖会把“用户只授了只读”谎报成已授
    写权限，needs_rebind 判定就全错）。None = 未知（老绑定），
    判定上一律按需重绑处理。"""
    return json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "token_type": "Bearer",
        "scope": scope,
    })


def decode_drive_token(text: str) -> dict:
    try:
        data = json.loads(text or "")
    except Exception as exc:
        raise DriveOAuthError(
            "auth_error", "Drive 凭据格式损坏，请重新绑定。") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise DriveOAuthError(
            "auth_error", "Drive 凭据缺少 access_token，请重新绑定。")
    return data



# ---------------------------------------------------------------------------

def _classify_http_failure(status: int | None, body) -> str | None:
    """HTTP 失败 → 三档 kind；一过性失败（5xx / 网络错）返回 None。

    - invalid_grant（refresh_token 被用户撤销或过期）→ expired；
    - invalid_client / unauthorized_client（服务器应用凭据错）→
      auth_error（部署者问题，用户重绑也修不好，但绑定确实不可用）；
    - 429 / rateLimitExceeded / userRateLimitExceeded → throttled；
    - 401（about 验活时令牌被拒）→ expired（调用方先刷新再定）；
    - 其余 403 → auth_error；5xx / 无状态码 → None（一过性）。
    """
    text = ""
    error_code = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            error_code = str(err.get("message", "") or "")
            text = str(err.get("message", "") or "")
            for detail in err.get("errors", []) or []:
                if isinstance(detail, dict):
                    text += " " + str(detail.get("reason", "") or "")
        else:
            error_code = str(err or "")
            text = error_code
    elif isinstance(body, str):
        text = body
    lowered = text.lower()
    if error_code == "invalid_grant" or "invalid_grant" in lowered:
        return "expired"
    if status == 429 or "ratelimitexceeded" in lowered.replace(" ", ""):
        return "throttled"
    if error_code in ("invalid_client", "unauthorized_client"):
        return "auth_error"
    if status == 401:
        return "expired"
    if status == 403:
        return "auth_error"
    return None


class _HttpResult:
    """httpx 响应的最小表面（测试桩只要实现这三个属性/方法）。"""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")

    @property
    def text(self) -> str:
        return self._payload if isinstance(self._payload, str) else ""


def _default_http_post(url, data=None, timeout=30):
    import httpx

    return httpx.post(url, data=data, timeout=timeout)


def _default_http_get(url, headers=None, timeout=30):
    import httpx

    return httpx.get(url, headers=headers, timeout=timeout)


def _read_error_body(resp) -> object:
    try:
        return resp.json()
    except Exception:
        try:
            return resp.text or ""
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# 令牌刷新与验活（存量绑定的续期口）
# ---------------------------------------------------------------------------

def refresh_access_token(refresh_token: str,
                         http_post=None,
                         client: str | None = None) -> dict:
    """refresh_token 换新 access_token（Google 可能轮换 refresh_token，
    回了新的就用新的，没回就沿用旧的；失败分类与换 token 同口径）。

    client 必须按绑定行记录传入（见 binding_oauth_client）：
    refresh_token 是签给具体应用的，用错客户端刷新必然失败。
    """
    post = http_post or _default_http_post
    try:
        resp = post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": _client_id(client),
            "client_secret": _client_secret(client),
            "refresh_token": refresh_token,
        }, timeout=30)
    except Exception as exc:
        raise DriveOAuthError(
            "transient", f"刷新令牌时网络失败（可重试）：{exc}") from exc
    if resp.status_code != 200:
        kind = _classify_http_failure(resp.status_code,
                                      _read_error_body(resp))
        if kind is None:
            raise DriveOAuthError(
                "transient", f"刷新令牌时上游失败（可重试）：HTTP {resp.status_code}")
        raise DriveOAuthError(kind, ub.error_message(kind))
    try:
        body = resp.json()
    except Exception as exc:
        raise DriveOAuthError(
            "transient", "刷新令牌返回非 JSON（可重试）。") from exc
    access = (body.get("access_token") or "").strip()
    if not access:
        raise DriveOAuthError("auth_error", "刷新令牌未返回 access_token。")
    new_refresh = (body.get("refresh_token") or "").strip() or refresh_token
    expires_in = int(body.get("expires_in", 3600) or 3600)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "expires_at": int(time.time()) + expires_in,
    }


def check_drive_liveness(access_token: str, http_get=None) -> dict:
    """Drive 验活：about 接口（只读 user 段，证明令牌真能读盘）。

    about.get 接受 drive.file 范围，故 file-only 令牌（2026-10-28
    临时默认授权）同样跑通本验活，不因缺 readonly 而误判失效；
    转存目录的建/验走应用自创路径，也在 file 覆盖内。
    401 → expired（调用方先刷新再定）；403 限流 → throttled；
    其余 403 → auth_error；5xx / 网络错 → transient（不落状态）。
    返回 about 的 user 段（仅证明用，不入库）。
    """
    get = http_get or _default_http_get
    try:
        resp = get(DRIVE_ABOUT_URL + "?fields=user",
                   headers={"Authorization": f"Bearer {access_token}"},
                   timeout=30)
    except Exception as exc:
        raise DriveOAuthError(
            "transient", f"验活时网络失败（可重试）：{exc}") from exc
    if resp.status_code != 200:
        kind = _classify_http_failure(resp.status_code,
                                      _read_error_body(resp))
        if kind is None:
            raise DriveOAuthError(
                "transient", f"验活时上游失败（可重试）：HTTP {resp.status_code}")
        raise DriveOAuthError(kind, ub.error_message(kind))
    try:
        body = resp.json()
    except Exception as exc:
        raise DriveOAuthError("transient", "验活返回非 JSON（可重试）。") from exc
    return body.get("user", {}) if isinstance(body, dict) else {}



def granted_scope_set(granted_scopes: str | None) -> set:
    """已授范围串（空格分隔）→ 集合；None/空串 → 空集。"""
    return set((granted_scopes or "").split()) - {""}


def binding_needs_rebind(granted_scopes: str | None) -> bool:
    """Drive 绑定是否需重绑：已授范围里缺 drive.file 即需重绑。
    老绑定无记录（None）一律按需重绑——老代码只申请过只读，
    不可能自带写权限，猜“已授”就是谎报。"""
    return DRIVE_FILE_SCOPE not in granted_scope_set(granted_scopes)


def reset_mkdir_failed_jobs(db: Session, user_id: int) -> int:
    """转存死单复活的 Drive 侧入口（历史名，保留转发口）。

    实现已下沉 transfer_variant.revive_binding_failed_jobs
    （全站唯一一份），本
    函数只转调，行为与下沉前一字不变：命中三类行（建目录
    失败前缀 / 列目录 401 前缀 / error_kind == "no_binding"）、
    字段口径（pending + attempts 清零 + 错误/重试/租约清掉）、
    只动本人行、重复重绑幂等，详见下沉处 docstring。返回重置
    行数（调用方提交）。"""
    from backend.emby_server import transfer_variant as _tv
    return _tv.revive_binding_failed_jobs(db, user_id)


#: Google 令牌吊销端点（重绑卫生用，成败不影响重绑结果）。
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _revoke_refresh_token_best_effort(refresh_token: str,
                                      http_post=None) -> None:
    """旧 refresh 尽力吊销：成败都不影响重绑结果（旧令牌即使未
    吊销，也会在用户撤销或达到 Google 上限时失效；吊销只是卫生，
    失败不许把已成功的重绑拖垮）。"""
    post = http_post or _default_http_post
    try:
        post(REVOKE_URL, data={"token": refresh_token}, timeout=30)
    except Exception:
        logger.warning("重绑旧 refresh 吊销失败（忽略）", exc_info=True)




# ---------------------------------------------------------------------------
# 凭据分离：用户 Drive 令牌唯一口（绝不回落 SA 池 / rclone.conf /
# scope=source 行；回落会查错盘、跨用户）
# ---------------------------------------------------------------------------

def _drive_credential_of(db: Session,
                         binding: em.UserStorageBinding) -> tuple[em.MountCredential, dict]:
    """绑定行 →（凭据行，解开的令牌 JSON）。

    凭据行归属（scope=user 且 owner=本人）不对 = 数据异常，响亮报错
    不 fallback；密封解不开 → auth_error（P1 同款：密钥轮换后旧密文
    解不开，须重绑）。
    """
    cred = ub._credential_of(db, binding)
    try:
        token_text = unseal_secret(cred.sealed_credential or "")
    except SealedSecretError as exc:
        raise DriveOAuthError(
            "auth_error", "Drive 凭据已失效（密钥轮换后旧密文解不开），请重新绑定。") from exc
    if not token_text:
        raise DriveOAuthError("auth_error", "Drive 凭据为空，请重新绑定。")
    return cred, decode_drive_token(token_text)


def _store_refreshed_token(db: Session, binding: em.UserStorageBinding,
                           cred: em.MountCredential, tokens: dict) -> None:
    """刷新结果写回（密封 JSON 全量替换；调用方提交）。
    scope 沿用旧存值：刷新响应未必带 scope，拿请求串覆盖会把
    “用户只授了只读”谎报成已授写权限，needs_rebind 判定就全错；
    旧存值解不开时存 None（未知→需重绑），不猜。"""
    old_scope = None
    try:
        old_scope = decode_drive_token(
            unseal_secret(cred.sealed_credential or "")).get("scope")
    except Exception:
        old_scope = None
    cred.sealed_credential = seal_secret(encode_drive_token(
        tokens["access_token"], tokens["refresh_token"],
        tokens["expires_at"], scope=old_scope))
    now = datetime.now()
    cred.status = "active"
    cred.last_verified_at = now
    cred.last_error_kind = None
    binding.status = "active"
    binding.last_verified_at = now
    binding.last_error_kind = None


def _apply_refresh_failure(db: Session, binding: em.UserStorageBinding,
                           kind: str) -> None:
    """刷新失败落态（调用方提交）：expired / auth_error 落行；
    throttled 不落行——token 端点 429 是上游节流，不是凭据死亡，
    落 invalid 会逼用户重绑，错杀（与 P1 的 115 直链映射有意不同，
    115 throttled 是账号被限、此处是端点被限，原因已写清）。
    防外流（2026-10-28）：落 invalid 时 best-effort 收回该绑定名
    下用户副本的 anyone-reader（只收权限，文件与行不动）。本钩子
    只用已存未过期令牌、绝不刷新——刷新失败正是本次调用的原因，
    再刷新会递归回本函数；令牌已死时直接跳过（等用户重绑后由
    TTL/解绑补收，见 PR 报告未知项）。throttled 不落行也不收回
    （绑定未死，收回会断正常播放）。本钩子不提交、不抛异常。
    """
    if kind in ("expired", "auth_error"):
        ub._mark_failed(db, binding, kind)
        try:
            from backend.emby_server import transfer_variant as _tv
            _tv.revoke_user_copy_sharing(
                db, binding.user_id, binding=binding, allow_refresh=False)
        except Exception:  # noqa: BLE001 — 收回失败绝不影响落态
            logger.warning("绑定失效收回副本分享失败 binding=%s",
                           binding.id, exc_info=True)
        # 死令牌待收回登记（2026-10-30 安全收口）：本次没收成的行
        # （令牌已死整批跳过 / 逐行失败）落 share_revoke_pending_at
        # 登记，重绑成功时优先补收、闲置清理周期兜底重试。登记
        # 失败同样不影响落态。
        try:
            from backend.emby_server import transfer_variant as _tv2
            _tv2.mark_share_revoke_pending(db, binding.user_id)
        except Exception:  # noqa: BLE001 — 登记失败绝不影响落态
            logger.warning("待收回登记失败 binding=%s",
                           binding.id, exc_info=True)


def get_binding_drive_access_token(db: Session, binding,
                                 http_post=None,
                                 force_refresh: bool = False) -> str:
    """指定绑定行的 Drive 令牌口（BYOS P3 按绑定扫描用）。

    与 get_token_for_user 同一套刷新/落态逻辑，只是不做
    “最早 active 绑定”查找：调用方已按绑定分账选好行，本口绝不
    替调用方换一行——换行就是跨绑定复用凭据。
    force_refresh=True 时跳过“未过期直接返回”捷径，真走
    refresh_token 换新令牌并写回：此前本口无此参数，401 重试方
    （transfer_variant.UserDriveApiClient._headers）纵使传了
    force_refresh 也被丢掉，拿回的仍是同一枚未过期旧令牌，
    重试必再 401——本参数就是修这个真 bug。
    """
    cred, token = _drive_credential_of(db, binding)
    expires_at = int(token.get("expires_at") or 0)
    if (not force_refresh
            and expires_at > int(time.time()) + OAUTH_TOKEN_REFRESH_MARGIN_SEC):
        return token["access_token"]
    refresh_token = (token.get("refresh_token") or "").strip()
    if not refresh_token:
        _apply_refresh_failure(db, binding, "expired")
        db.commit()
        raise DriveOAuthError("expired", ub.error_message("expired"))
    try:
        new_tokens = refresh_access_token(
            refresh_token, http_post=http_post,
            client=binding_oauth_client(binding))
    except DriveOAuthError as exc:
        if exc.kind in ("expired", "auth_error"):
            _apply_refresh_failure(db, binding, exc.kind)
            db.commit()
        elif exc.kind == "throttled":
            raise DriveOAuthError(
                "transient", "刷新令牌时上游限流（可重试，凭据未改）。") from exc
        raise
    _store_refreshed_token(db, binding, cred, new_tokens)
    db.commit()
    return new_tokens["access_token"]




def get_token_for_user(db: Session, user_id: int,
                       http_post=None,
                       force_refresh: bool = False) -> str:
    """该用户 Drive 令牌唯一口（播放 / 扫描 / 工人共用，不许各写一套）。

    规范名（闭环 #13 登记口径）：BYOS 用户令牌中继的取令牌口
    就叫这个名，video_stream 的中继选路经此名取当前用户令牌。
    旧名 get_user_drive_access_token 保留为同对象别名（见函数后
    赋值），不许另写第二套实现。

    - 无 active 绑定 → not_found（调用方按“无可用绑定”处理，绝不
      回落共享 SA——回落会把别人的盘或共享盘当成该用户的盘）；
    - 未过期（留 300 秒余量）→ 直接返回 access_token；
    - 快过期 → 刷新后返回新 token 并写回；刷新失败属 expired /
      auth_error → 落行并抛对应 kind；throttled / transient → 不改
      行，抛 transient（调用方重试，凭据本身没死）。
    - force_refresh=True → 直通绑定级口，真换令牌（401 重试用）。
    """
    binding = ub.get_active_binding(db, user_id, ub.PROVIDER_DRIVE)
    if binding is None:
        raise DriveOAuthError("not_found", "当前用户没有可用的 Drive 绑定。")
    return get_binding_drive_access_token(db, binding, http_post=http_post,
                                          force_refresh=force_refresh)


#: 旧名别名（横切只许一套）：与 get_token_for_user 是同一函数
#: 对象，既有调用方照旧可用；新接线一律用规范名 get_token_for_user。
get_user_drive_access_token = get_token_for_user


def refresh_drive_binding(db: Session, user_id: int, binding_id: int,
                          http_post=None, http_get=None,
                          ) -> em.UserStorageBinding:
    """Drive 绑定重验（失效后恢复口，P1 verify_binding 的 drive 版）。

    本人行才可重验；强制刷新 + 验活双通过才回 active；三档失败落
    行并返回该行；一过性失败不改状态。
    """
    binding = (
        db.query(em.UserStorageBinding)
        .filter(em.UserStorageBinding.id == binding_id,
                em.UserStorageBinding.user_id == user_id)
        .first()
    )
    if binding is None:
        raise DriveOAuthError("not_found", "绑定不存在或不属于当前用户。")
    if binding.provider != ub.PROVIDER_DRIVE:
        raise DriveOAuthError("unsupported", "该 provider 不走 Drive 重验。")
    try:
        cred, token = _drive_credential_of(db, binding)
    except DriveOAuthError as exc:
        # 失败态行（换 token 失败时建的空凭据行）重验：无 token 可刷，
        # 直接按 auth_error 落行并返回该行（与 P1 verify 返回失败行同口径）；
        # transient（凭据行缺失/归属异常）仍抛回调用方。
        if exc.kind == "auth_error":
            _apply_refresh_failure(db, binding, "auth_error")
            db.commit()
            return binding
        raise
    refresh_token = (token.get("refresh_token") or "").strip()
    if not refresh_token:
        _apply_refresh_failure(db, binding, "expired")
        db.commit()
        return binding
    try:
        new_tokens = refresh_access_token(
            refresh_token, http_post=http_post,
            client=binding_oauth_client(binding))
    except DriveOAuthError as exc:
        if exc.kind in ("expired", "auth_error"):
            _apply_refresh_failure(db, binding, exc.kind)
            db.commit()
            return binding
        raise
    try:
        check_drive_liveness(new_tokens["access_token"], http_get=http_get)
    except DriveOAuthError as exc:
        if exc.kind in ("expired", "auth_error"):
            _apply_refresh_failure(db, binding, exc.kind)
            db.commit()
            return binding
        raise
    _store_refreshed_token(db, binding, cred, new_tokens)
    db.commit()
    return binding


# ---------------------------------------------------------------------------
# 凭据维护工人：启动即跑一轮 + 周期跑（FMBY 工人口径）
# ---------------------------------------------------------------------------

def sweep_drive_credentials(db: Session, http_post=None,
                            http_get=None) -> dict:
    """扫一遍全部 Drive 用户绑定，刷新快过期令牌、标记死凭据。

    - 只扫 provider=drive 的绑定行经 credential_id 指到的凭据行；
      scope=source 行（SA 池等服级凭据）永远不进本循环、不读不写；
    - active 行且令牌快过期（300 秒余量内）→ 刷新；已 expired 行
      不再试刷（invalid_grant 刷了也白刷），计 auth_expired；
    - invalid 行跳过（须用户重验/重绑，工人不自动复活）；
    - 单行失败隔离：该行记数、记警告，继续扫下一行；
    - 汇总一行日志（scanned / refreshed / auth_expired / auth_error /
      throttled / transient_errors / skipped），与 FMBY 维护日志同款。
    返回汇总 dict（调用方 / 测试断言用）。
    """
    summary = {
        "scanned": 0, "refreshed": 0, "auth_expired": 0, "auth_error": 0,
        "throttled": 0, "transient_errors": 0, "skipped": 0,
    }
    rows = (
        db.query(em.UserStorageBinding)
        .filter(em.UserStorageBinding.provider == ub.PROVIDER_DRIVE)
        .order_by(em.UserStorageBinding.id.asc())
        .all()
    )
    for binding in rows:
        summary["scanned"] += 1
        cred = (
            db.query(em.MountCredential)
            .filter(em.MountCredential.id == binding.credential_id)
            .first()
        )
        if cred is None:
            summary["skipped"] += 1
            logger.warning("Drive 维护跳过绑定 #%s：凭据行缺失", binding.id)
            continue
        if cred.scope != "user" or cred.owner_user_id != binding.user_id:
            # 源级凭据行绝不由用户维护工人碰（分账铁律），只记警告。
            summary["skipped"] += 1
            logger.warning("Drive 维护跳过绑定 #%s：凭据行非本人 scope=user 行",
                           binding.id)
            continue
        if binding.status == "expired":
            summary["auth_expired"] += 1
            continue
        if binding.status != "active":
            summary["skipped"] += 1
            continue
        try:
            token = decode_drive_token(
                unseal_secret(cred.sealed_credential or ""))
        except (SealedSecretError, DriveOAuthError):
            _apply_refresh_failure(db, binding, "auth_error")
            db.commit()
            summary["auth_error"] += 1
            continue
        expires_at = int(token.get("expires_at") or 0)
        if expires_at > int(time.time()) + OAUTH_TOKEN_REFRESH_MARGIN_SEC:
            summary["skipped"] += 1
            continue
        refresh_token = (token.get("refresh_token") or "").strip()
        if not refresh_token:
            _apply_refresh_failure(db, binding, "expired")
            db.commit()
            summary["auth_expired"] += 1
            continue
        try:
            new_tokens = refresh_access_token(
                refresh_token, http_post=http_post,
                client=binding_oauth_client(binding))
        except DriveOAuthError as exc:
            if exc.kind == "expired":
                _apply_refresh_failure(db, binding, "expired")
                db.commit()
                summary["auth_expired"] += 1
            elif exc.kind == "auth_error":
                _apply_refresh_failure(db, binding, "auth_error")
                db.commit()
                summary["auth_error"] += 1
            elif exc.kind == "throttled":
                summary["throttled"] += 1
                logger.warning("Drive 维护绑定 #%s 刷新被限流（不落行，下轮再试）",
                               binding.id)
            else:
                summary["transient_errors"] += 1
                logger.warning("Drive 维护绑定 #%s 刷新一过性失败（下轮再试）",
                               binding.id)
            continue
        _store_refreshed_token(db, binding, cred, new_tokens)
        db.commit()
        summary["refreshed"] += 1
    logger.info("Drive 凭据维护完成 scanned=%(scanned)s refreshed=%(refreshed)s "
                "auth_expired=%(auth_expired)s auth_error=%(auth_error)s "
                "throttled=%(throttled)s transient_errors=%(transient_errors)s "
                "skipped=%(skipped)s", summary)
    return summary


_probe_thread = None
_probe_lock = threading.Lock()


def start_drive_cred_maintenance_worker(stop_event=None) -> bool:
    """启动 Drive 凭据维护工人（worker.py 调用，照 list_projection 写法）。

    开关关时返回 False、不启动；幂等：已有存活线程时直接返回 True。
    启动即跑一轮，之后按周期跑；单轮异常只记警告、不停循环。
    """
    global _probe_thread
    if not maint_enabled():
        return False
    with _probe_lock:
        alive = _probe_thread
        if alive is not None and alive.is_alive():
            return True

        def _loop() -> None:
            from backend.database import SessionLocal

            try:
                session = SessionLocal()
                try:
                    sweep_drive_credentials(session)
                finally:
                    session.close()
            except Exception as exc:  # noqa: BLE001 — 首轮失败不挡工人
                logger.warning("Drive 凭据维护首轮失败（可忽略）: %s", exc)
            interval = maint_interval_sec()
            while stop_event is None or not stop_event.is_set():
                try:
                    if stop_event is not None:
                        if stop_event.wait(timeout=interval):
                            break
                    else:
                        time.sleep(interval)
                except Exception:  # noqa: BLE001 — 睡眠被打断也继续
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    session = SessionLocal()
                    try:
                        sweep_drive_credentials(session)
                    finally:
                        session.close()
                except Exception as exc:  # noqa: BLE001 — 单轮失败不停循环
                    logger.warning("Drive 凭据维护单轮失败（可忽略）: %s", exc)

        thread = threading.Thread(target=_loop, daemon=True,
                                  name="drive-cred-maintenance")
        thread.start()
        _probe_thread = thread
        return True
