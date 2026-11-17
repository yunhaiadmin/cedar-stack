"""进程内滑动窗口限流器

用于保护登录/注册/认证等端点免遭暴力破解。
单实例部署下足够；如需多实例共享限流状态，可将存储替换为 Redis。
"""
from __future__ import annotations

import dataclasses
import ipaddress
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

# P2（完美审查 A-P2-3）：X-Real-IP 合法性校验复用 domain_guard 的 is_valid_ip，
# 横切能力只许一套，不另起。
from backend.domain_guard import is_valid_ip


class SlidingWindowLimiter:
    """滑动窗口计数限流：window 秒内最多 max_events 次"""

    # 超过这个 key 数就先做一次清理（清理是 O(keys)，别每次请求都做）
    _PURGE_THRESHOLD = 5000

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_window = 0.0

    def check(self, key: str, max_events: int, window_seconds: float) -> tuple[bool, int]:
        """返回 (是否放行, 剩余需等待秒数)"""
        now = time.monotonic()
        with self._lock:
            if window_seconds > self._max_window:
                self._max_window = window_seconds
            q = self._hits[key]
            cutoff = now - window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= max_events:
                retry_after = int(window_seconds - (now - q[0])) + 1
                return False, max(retry_after, 1)
            q.append(now)
            # 防止 key 无限增长：超阈值时清掉「已经不可能再命中」的桶。
            # 不能只看空队列——某个 IP 打过一次之后再没出现，队列里那条时间戳会永远留着。
            if len(self._hits) > self._PURGE_THRESHOLD:
                self._purge(now)
            return True, 0

    def _purge(self, now: float) -> int:
        """丢掉所有已过期的桶（调用方持锁）

        判定用**见过的最大窗口**：比任何还在生效的窗口都老的桶，留着也不会有用。
        """
        span = self._max_window or 60.0
        dead = [
            k for k, v in self._hits.items()
            if not v or v[-1] < now - span
        ]
        for k in dead:
            self._hits.pop(k, None)
        return len(dead)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_limiter = SlidingWindowLimiter()


def check_rate_limit(key: str, max_events: int, window_seconds: float) -> tuple[bool, int]:
    """模块级便捷入口"""
    return _limiter.check(key, max_events, window_seconds)


def reset_rate_limit(key: str) -> None:
    """模块级便捷入口：清掉某个键的全部计数（与 check_rate_limit 配对）。

    用于「成功后退还额度」语义（如注册成功后 reset register:{ip}）：
    失败尝试保留计数防爆破，成功不占后来者的额度。
    """
    _limiter.reset(key)


# ============ 网关限流规则表（横切能力只许一套）============
# backend/main.py（EM）与 emby_api/asgi_middleware.py（EA）的限流中间件共用此表，
# 不许各自手写一份——否则一处改了限额另一处会静默漂移（r38 实证：EA 侧曾漏跟进）。
# (路径前缀, 未认证限额/分钟, 认证用户限额/分钟)；(0, 0) = 不限流。
API_RATE_LIMITS = [
    ("/api/health", 0, 0),              # 健康检查：不限流
    ("/emby/Users/AuthenticateByName", 15, 15),  # Emby 客户端登录：每分钟 15 次/IP（防暴力破解，对标 go-emby）
    # 注意：不要再为小写别名 /emby/Users/authenticatebyname 另加条目。
    # 计数键含命中的前缀，另加条目 = 另建独立桶，大小写两条路各 15 次/分钟、
    # 合计 30 次/分钟（r4-auth 实测）。四条登录路径先经下面的
    # canonical_rate_limit_path() 归一到本条目，共用同一个桶。
    # 登录专档必须是**真实路由**：用户登录是 /api/user/auth/login、管理员登录是
    # /api/admin/auth/login。旧条目写的是 /api/user/login 与 /api/admin/emby/login，
    # 路由表里根本没有这两个路径 → 永远命中不到（死条目），登录请求实际落进
    # 下面的 /api/ 通用档（120/分钟），专档的 10 次/分钟从未生效。
    ("/api/user/auth/login", 10, 10),   # 用户登录：每分钟 10 次/IP（防暴力破解）
    ("/api/admin/auth/login", 10, 10),  # 管理员登录：每分钟 10 次/IP（防暴力破解）
    # 公开/认证前置路径拆独立桶（中间件的计数键含命中的前缀，拆条目即拆桶）：
    # 注册页一打开就要打 register-config/branding 等一串接口，混在
    # /api/ 通用桶里时，任何来源的流量高峰都会把注册链路连坐限流（429）。
    ("/api/user/auth/", 60, 300),       # 认证前置：注册配置/注册/刷新令牌
    ("/api/site/", 120, 600),           # 站点公开信息：branding/海报等每次页面加载都调
    ("/api/admin/", 60, 300),           # 管理接口：未认证 60，认证 300
    ("/api/", 120, 600),               # 普通 API：未认证 120，认证 600
]

# Emby 登录的规范限流键前缀：四条路由（有无 /emby 前缀 × 大小写）共用它。
EMBY_LOGIN_RATE_LIMIT_PREFIX = "/emby/Users/AuthenticateByName"


def canonical_rate_limit_path(path: str) -> str:
    """把 Emby 登录四条路径归一到同一限流桶。

    路由层大小写敏感（FastAPI 精确匹配），但限流层必须不敏感：
    不归一时，小写别名命中另一条目（或无条目），等于给爆破多开一条路。
    归一只认登录路径本身的全等（大小写不敏感），其它路径原样返回；
    尾巴带额外段的路径不归一（路由上本就 404，不进登录桶）。
    EM（backend/main.py::_check_rate_limit）与 EA
    （emby_api/asgi_middleware.py::_ea_check_limit）两处检查前都必须调它，
    不许各自手写归一逻辑。
    """
    lowered = (path or "").lower()
    if lowered in ("/emby/users/authenticatebyname", "/users/authenticatebyname"):
        return EMBY_LOGIN_RATE_LIMIT_PREFIX
    return path


def is_authenticated_request(request) -> bool:
    """限流档判定：该请求是否算「认证用户」。

    横切能力只许一套：backend/main.py 的 rate_limit_middleware 与
    emby_api 的 EaRateLimitMiddleware 都调这里，不许各自手写判定。
    只认合法的 access JWT（验签），不只看头在不在——旧实现只看
    Authorization 头有没有，攻击者发任意伪造头就能拿到认证档限额
    （r38 实证：EA 侧 _ea_is_auth 就是旧实现）。

    纯验签（无 DB 查询，内部吞异常），验签失败一律 False（fail-closed 降档）。
    X-Emby-Token / X-MediaBrowser-Token 是 Emby 凭据，门户 /api/ 鉴权不用它，
    不再计入认证档（/emby/ 路径的认证/匿名限额本就相同，无影响）。
    """
    # 函数内导入：backend.security 较重，避免模块级循环导入风险。
    from backend.security import resolve_jwt_user_id

    auth = request.headers.get("Authorization")
    if not auth:
        return False

    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
    else:
        # 无 scheme 时把整个头当 token；空头返回 False
        token = auth.strip()

    if not token:
        return False

    return resolve_jwt_user_id(token) is not None


# R4-2：TRUSTED_PROXIES 解析结果缓存——只在环境变量原始值变化时重解析。
# _trusted_proxies() 被每个请求的 get_client_ip 调用，解析本身（逐个构建
# ip_network）是纯开销。os.getenv 是廉价字典查找，逐请求调用无妨。
# 缓存键用原始值：测试用 monkeypatch.setenv 逐测改值时自动失效重算，
# 行为与不缓存完全一致。线程安全：值是原始值的纯函数，竞态最多多算一次。
_trusted_proxies_cache = None  # (raw_env, list[ip_network])，单进程内共享


def _trusted_proxies() -> list:
    """从环境变量 TRUSTED_PROXIES 解析可信代理列表。

    格式：逗号分隔的 IP 或 CIDR，如 "127.0.0.1,172.18.0.0/16"
    默认信任本地回环（127.0.0.0/8, ::1）。
    """
    global _trusted_proxies_cache
    raw_env = os.getenv("TRUSTED_PROXIES", "")
    cached = _trusted_proxies_cache
    if cached is not None and cached[0] == raw_env:
        return list(cached[1])
    raw = raw_env.strip()
    nets = []
    # 默认始终信任回环地址
    for default in ("127.0.0.0/8", "::1/128"):
        try:
            nets.append(ipaddress.ip_network(default))
        except ValueError:
            pass
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                # 单个 IP 自动转为 /32 或 /128
                if "/" not in part:
                    nets.append(ipaddress.ip_network(part + ("/128" if ":" in part else "/32")))
                else:
                    nets.append(ipaddress.ip_network(part))
            except ValueError:
                # 配置写错了就忽略，不让服务起不来
                continue
    _trusted_proxies_cache = (raw_env, nets)
    return list(nets)


def _is_trusted_proxy(ip: str) -> bool:
    """直连 IP 是否在可信代理列表里"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in _trusted_proxies():
        try:
            if addr in net:
                return True
        except TypeError:
            # IPv4 vs IPv6 不匹配就跳过
            continue
    return False


def get_client_ip(request) -> str:
    """获取真实客户端 IP（防伪造）。

    只有当直连方是可信代理（TRUSTED_PROXIES）时，才信任
    X-Forwarded-For / X-Real-IP 头；否则直接用 request.client.host。

    原理：XFF 和 X-Real-IP 都是客户端可伪造的请求头。只有当请求
    确实经过我们自己的反代（nginx）时，这些头才可信——而判断依据
    是 TCP 直连的对端 IP 是否在可信代理列表里（这个伪造不了）。

    可信代理场景下的优先级：
    1. X-Real-IP：nginx 按 $remote_addr 硬写
    2. X-Forwarded-For 最后一段：最近一跳代理追加的
    3. 直连 IP
    """
    if request is None:
        return "unknown"
    direct_ip = request.client.host if request.client else "unknown"

    # 直连方不是可信代理：所有转发头都不可信，直接用直连 IP
    if not _is_trusted_proxy(direct_ip):
        return direct_ip

    # 可信代理：X-Real-IP 优先（nginx 硬写，客户端伪造的会被覆盖）
    real = (request.headers.get("x-real-ip") or "").strip()
    # P2 修复（完美审查 A-P2-3）：必须是合法 IP 才采信——回环直连者可任意伪造
    # X-Real-IP 绕限流（每次换桶）并污染 LoginLog.ip。非法值回落到 XFF/直连 IP。
    if real and is_valid_ip(real):
        return real
    # 其次取 XFF 最后一段（最近一跳追加的才是真实客户端）
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        hops = [part.strip() for part in fwd.split(",") if part.strip()]
        if hops:
            # R4-1 修复：某些代理会写 "unknown" 等非 IP 值；不校验会被当限流键，
            # 导致不同客户端归入同一桶。非法值直接回落到直连 IP（与 X-Real-IP
            # 分支的回落语义一致）。
            if is_valid_ip(hops[-1]):
                return hops[-1]
    return direct_ip


# ============ 登录失败锁定（F4：账号＋IP 双维度失败计数）============
# 与上面的请求数限流是两层不同的防线：
# - 请求数限流（SlidingWindowLimiter / 网关 rate_limit_incr）数的是
#   「请求总数」，攻击者把尝试摊到多分钟里就能绕开（黑盒实证：连错
#   12 次无任何拦截）；
# - 这里数的是「失败次数」：同一账号在窗口内累计失败达阈值 → 临时
#   锁定该账号；同一 IP 跨账号累计失败达阈值 → 临时封该 IP 的登录。
#   两个维度任一命中即拦：只按 IP 会被换 IP 绕过，只按账号会让爆破
#   者在共享出口下无感。IP 阈值刻意高于账号阈值，降低 NAT 共享出口
#   的连坐面；成功登录只清账号维度（IP 维度保留，防「猜中一个号就
#   洗白整个 IP 的失败史」）。
# 存储与 SlidingWindowLimiter 同风格（进程内、单锁、有界清理）：
# handler 层既有登录防护全在本模块，横切能力只许一套，不另立系统。
# 多进程部署时各进程各算一份（与既有 handler 层限流同一性质）。


@dataclasses.dataclass(frozen=True)
class LoginLockoutConfig:
    enabled: bool = True
    account_max_failures: int = 10
    ip_max_failures: int = 30
    window_sec: float = 900
    lock_sec: float = 900


#: Emby Policy 下发口径的单一真源（_policy_dto 的 LoginAttemptsBeforeLockout
#: 读它；实际执法阈值走 SystemConfig，管理员改配置后该字段可能与执法值
#: 不一致——它只是给客户端看的策略声明，执法以服务端守卫为准）。
LOGIN_LOCKOUT_DEFAULT_ACCOUNT_MAX_FAILURES = 10

_LOCKOUT_CONFIG_KEYS = (
    "login_lockout_enabled",
    "login_lockout_account_max_failures",
    "login_lockout_ip_max_failures",
    "login_lockout_window_sec",
    "login_lockout_lock_sec",
)


def login_lockout_config(db) -> LoginLockoutConfig:
    """从 SystemConfig 读登录锁定配置（键见 config_schema 的
    LOGIN_LOCKOUT_CONFIG_SCHEMA）；缺行/非法值一律回退默认，读库异常
    也回退默认——配置层故障不能把登录入口整体打死。"""
    values: dict[str, str] = {}
    if db is not None:
        try:
            from backend import models

            rows = (
                db.query(models.SystemConfig)
                .filter(models.SystemConfig.key.in_(_LOCKOUT_CONFIG_KEYS))
                .all()
            )
            values = {row.key: (row.value or "") for row in rows}
        except Exception:  # noqa: BLE001 - 回退默认，不让登录挂掉
            values = {}

    def _int(key: str, default: int) -> int:
        try:
            v = int(str(values.get(key, "")).strip())
        except (TypeError, ValueError):
            return default
        return v if v >= 1 else default

    raw_enabled = str(values.get("login_lockout_enabled", "")).strip().lower()
    return LoginLockoutConfig(
        enabled=raw_enabled not in ("false", "0", "no", "off"),
        account_max_failures=_int(
            "login_lockout_account_max_failures",
            LOGIN_LOCKOUT_DEFAULT_ACCOUNT_MAX_FAILURES),
        ip_max_failures=_int("login_lockout_ip_max_failures", 30),
        window_sec=_int("login_lockout_window_sec", 900),
        lock_sec=_int("login_lockout_lock_sec", 900),
    )


class LoginLockoutGuard:
    """登录失败计数＋临时锁定（进程内）。

    键：``{scope}:acct:{账号小写}`` 与 ``{scope}:ip:{IP}`` 两族。
    scope 区分入口（portal / emby / admin）：三处凭据并不互通
    （门户密码与播放密码可不同），失败计数不跨 scope 串扰。
    """

    _PURGE_THRESHOLD = 5000

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _acct_key(scope: str, account: str) -> str:
        return f"{scope}:acct:{(account or '').strip().lower() or '-'}"

    @staticmethod
    def _ip_key(scope: str, ip: str) -> str:
        return f"{scope}:ip:{(ip or 'unknown').strip() or 'unknown'}"

    def check(self, scope: str, account: str, ip: str,
              cfg: LoginLockoutConfig, now: Optional[float] = None,
              ) -> tuple[bool, int]:
        """是否已被锁定（账号或 IP 任一维度）。返回 (锁定中, 还需等待秒数)。"""
        if not cfg.enabled:
            return False, 0
        now = time.monotonic() if now is None else now
        retry = 0
        with self._lock:
            for key in (self._acct_key(scope, account), self._ip_key(scope, ip)):
                until = self._locked_until.get(key)
                if until is not None:
                    if until > now:
                        retry = max(retry, int(until - now) + 1)
                    else:
                        self._locked_until.pop(key, None)
        return retry > 0, retry

    def record_failure(self, scope: str, account: str, ip: str,
                       cfg: LoginLockoutConfig, now: Optional[float] = None,
                       ) -> Optional[str]:
        """记一次登录失败；触发锁定时返回维度名（"account" / "ip"），否则 None。

        已在锁定中的维度不再累加（锁定不续期，防攻击者把受害者永久锁死）。
        """
        if not cfg.enabled:
            return None
        now = time.monotonic() if now is None else now
        locked_dim: Optional[str] = None
        with self._lock:
            for key, threshold, dim in (
                (self._acct_key(scope, account), cfg.account_max_failures, "account"),
                (self._ip_key(scope, ip), cfg.ip_max_failures, "ip"),
            ):
                until = self._locked_until.get(key)
                if until is not None:
                    if until > now:
                        continue  # 锁定中：不累加、不续期
                    self._locked_until.pop(key, None)
                q = self._fails[key]
                cutoff = now - cfg.window_sec
                while q and q[0] < cutoff:
                    q.popleft()
                q.append(now)
                if len(q) >= threshold:
                    self._locked_until[key] = now + cfg.lock_sec
                    q.clear()
                    if locked_dim is None:
                        locked_dim = dim
            if len(self._fails) > self._PURGE_THRESHOLD:
                self._purge(now, cfg)
        return locked_dim

    def record_success(self, scope: str, account: str,
                       now: Optional[float] = None) -> None:
        """登录成功：清零该账号维度的失败计数与锁定（IP 维度不动）。"""
        key = self._acct_key(scope, account)
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

    def _purge(self, now: float, cfg: LoginLockoutConfig) -> None:
        """丢掉已不可能再命中的桶（调用方持锁）。"""
        dead = [
            k for k, v in self._fails.items()
            if not v or v[-1] < now - cfg.window_sec
        ]
        for k in dead:
            self._fails.pop(k, None)
        for k in [k for k, u in self._locked_until.items() if u <= now]:
            self._locked_until.pop(k, None)

    def clear(self) -> None:
        """清空全部状态（测试隔离用）。"""
        with self._lock:
            self._fails.clear()
            self._locked_until.clear()


_login_lockout_guard = LoginLockoutGuard()


def login_lockout_check(scope: str, account: str, ip: str,
                         cfg: LoginLockoutConfig) -> tuple[bool, int]:
    """模块级便捷入口：该 (账号, IP) 当前是否被登录锁定拦下。"""
    return _login_lockout_guard.check(scope, account, ip, cfg)


def login_lockout_record_failure(scope: str, account: str, ip: str,
                                 cfg: LoginLockoutConfig) -> Optional[str]:
    """模块级便捷入口：记一次登录失败，返回刚触发的锁定维度或 None。"""
    return _login_lockout_guard.record_failure(scope, account, ip, cfg)


def login_lockout_record_success(scope: str, account: str) -> None:
    """模块级便捷入口：登录成功后清零该账号的失败计数。"""
    _login_lockout_guard.record_success(scope, account)


def client_ip(request) -> str:
    """提取客户端 IP（只能信任我们自己的反代重新写入的头）

    安全：此值同时用于登录/注册/核销的限流键与登录日志，之前取
    `X-Forwarded-For` 的**第一段**——而仓库里的 Nginx 用的是
    `$proxy_add_x_forwarded_for`（把客户端自带的头追加在前面），
    于是任何人只要每次伪造一个 `X-Forwarded-For: 1.2.3.4` 就能换一个限流桶，
    暴力破解与刷接口形同不设限。现在的优先级：

    1. `X-Real-IP`：Nginx 按 `$remote_addr` 硬写，客户端无法伪造
    2. `X-Forwarded-For` 的**最后一段**：由最近一跳代理追加，才是真实客户端
    3. 直连时的 `request.client.host`

    注意：已升级为可信代理校验版本（get_client_ip），保留此函数名做兼容。
    """
    return get_client_ip(request)
