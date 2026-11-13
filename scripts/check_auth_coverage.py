#!/usr/bin/env python3
"""写端点鉴权覆盖检查（v2.30.0）

**问题**：漏掉一个鉴权依赖不会让任何检查变红——路由照常注册、类型检查照常通过、
已有的冒烟测试也不会去点那个新端点（它测的是自己关心的那些路径）。结果就是一个
**既没登录也能写**的接口，而它往往要等到被人扫到才知道。

真实排摸（写这条检查时跑出来的）：112 个写端点里，只有 5 个没有鉴权依赖，
而这 5 个全是**本来就该免鉴权**的——注册 / 登录 / 刷新令牌、支付网关回调
（靠验签）、管理员登录（靠限流 + 人机验证 + 登录日志）。也就是说现在的口径是干净的，
但这份干净只靠「每个人都记得写 `Depends(get_current_user)`」维持。

因此把口径变成一条可以执行的规则：

    `/api/admin/*` 与 `/api/user/*` 下的每个写端点（POST/PUT/PATCH/DELETE）
    都必须带上鉴权依赖 —— 或者登记在 ``PRE_AUTH`` 里并写明「它靠什么别的机制挡住」

判定是源码级的：路由级 ``dependencies`` 或端点签名里出现下表中的任一依赖名即算覆盖。
免鉴权端点必须显式登记，而且必须说清替代机制（限流 / 验签 / 验证码…），
不能只写一句「故意的」。

**一种等价写法也算**（与审计覆盖检查 v2.39.0 同一份口径）：端点把鉴权交给
**同一模块里的函数**（如 EA 侧端点在函数体第一行调 ``_panel_auth(request)``，
里面才是 ``require_panel_key``）——跟一层：端点直接调用的本模块函数源码里
出现鉴权依赖名就算覆盖。只跟一层、只认本模块，不做完整调用图分析：
隔两层以上、或干脆没调的，照旧点名。

**扫哪些模块靠自动发现，不靠手名单**（GA-068）：这份检查原来有一份手维护的
``ROUTER_MODULES`` 名单，新域模块上线时没人登记，检查就在不全的名单上跑绿——
代理分销 / 软件授权 / 公共池 / 治理停权 / 合集等一批高敏写口全是从这个盲区
漏出去的（GA-069~073）。现在模块集合由 AST 枚举源码自动发现（见下方
``discover_router_modules``），手名单只许以「排除表」形式存在且逐条写理由；
``tests/test_coverage_discovery_guard.py`` 用一份独立枚举对账，漏扫必然红。

用法：python3 scripts/check_auth_coverage.py
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 算「已被鉴权」的依赖名（任一出现在路由级 dependencies 或端点源码里即可）
AUTH_DEPENDENCIES = (
    "get_current_user",
    "get_current_user_optional",
    "get_current_user_or_token",
    "get_current_admin",
    "get_admin_or_emby_user",
    "require_admin",
    "require_staff",
    "require_panel_key",       # EA ↔ EM 之间用共享密钥，不是管理员 JWT
)

# ---------------------------------------------------------------------------
# 覆盖范围：自动发现，不再手维护名单（GA-068，见文件头说明）
#
# AST 枚举 ``backend/`` 全部源码（只读语法树，不 import 业务模块）：
#   1. ``backend/api`` 与 ``backend/emby_server`` 两个包里，凡是定义了
#      ``APIRouter``、往某个 router 上注册了路由（装饰器）、或做了
#      ``include_router`` 的模块，一律纳入；
#   2. ``backend/`` 其他任何位置，只要以字面量 ``prefix=`` 定义了
#      ``/api/admin`` / ``/api/user`` 路由的模块，一律纳入。
# 纳入只是「候选」——真正判定仍按收集到的路由路径过滤，所以多纳不会误报，
# 漏纳才会放走写口；发现逻辑因此宁宽勿窄。
# ---------------------------------------------------------------------------

_DISCOVERY_PACKAGE_ROOTS = ("backend/api", "backend/emby_server")
_ROUTE_DECORATOR_ATTRS = frozenset({
    "get", "post", "put", "patch", "delete", "api_route", "websocket",
    "add_api_route", "add_api_websocket_route",
})
_BASE_PREFIXES = ("/api/admin", "/api/user")

# 自动发现的排除表：模块 → 为什么不扫。判断标准与免鉴权登记相同——必须写出
# 具体理由，不许只写一句「故意的」；守卫测试会逐条对账（理由为空、指向的
# 模块不存在都会红）。正常情况下这里应该是空的：发现到的模块默认全扫。
EXCLUDED_MODULES: dict[str, str] = {}


def _module_name_of(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _file_route_signals(path: Path) -> tuple[bool, set[str]]:
    """读一个源码文件的语法树 → (是否定义/注册了路由, 字面量 APIRouter 前缀集合)"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False, set()
    registers = False
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "APIRouter":
                registers = True
                for keyword in node.keywords:
                    value = keyword.value
                    if (keyword.arg == "prefix" and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        prefixes.add(value.value)
            elif isinstance(func, ast.Attribute) and func.attr == "include_router":
                registers = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in _ROUTE_DECORATOR_ATTRS):
                    registers = True
    return registers, prefixes


def discover_router_modules() -> tuple[str, ...]:
    """自动枚举要扫的路由模块（GA-068：代替原手维护的 ROUTER_MODULES 名单）。"""
    found: set[str] = set()
    for path in sorted((ROOT / "backend").rglob("*.py")):
        registers, prefixes = _file_route_signals(path)
        if not registers and not prefixes:
            continue
        in_package_roots = any(
            path.is_relative_to(ROOT / root) for root in _DISCOVERY_PACKAGE_ROOTS)
        if in_package_roots or any(p.startswith(_BASE_PREFIXES) for p in prefixes):
            found.add(_module_name_of(path))
    return tuple(sorted(found - set(EXCLUDED_MODULES)))


# 定义对外路由的模块（自动发现，见上方 GA-068 说明；不许改回手维护名单——
# 守卫测试 tests/test_coverage_discovery_guard.py 会把漏扫钉红）
ROUTER_MODULES = discover_router_modules()

# 免鉴权写端点：(方法, 路径) → 靠什么别的机制挡住。
# 判断标准：**必须写出替代机制**（限流 / 验签 / 一次性令牌 / 验证码…），
# 不能只写一句「故意的」——「免鉴权」是一个需要解释的决定。
PRE_AUTH: dict[tuple[str, str], str] = {
    ("POST", "/api/user/auth/register"):
        "注册本身就是取得身份前的动作；走注册模式开关（开放 / 凭码 / 关闭）+ 限流 + 人机验证",
    ("POST", "/api/user/auth/register-code/verify"):
        "凭码注册第一步验码：注册前动作，拿不到身份；只校验不消耗、不发任何权益，"
        "靠 IP 限流（20 次 / 10 分钟）防撞码，且不存在与蜜罐一律回「注册码无效」不给枚举线索；"
        "提交注册时服务端还会二次校验并同事务核销，本端点放行不是凭据",
    ("POST", "/api/user/auth/login"):
        "登录同理：限流（按 IP）+ 人机验证 + 登录日志（成功与失败都记）",
    ("POST", "/api/user/auth/refresh"):
        "刷新令牌：只认请求里带的那枚 refresh token 自己，无效即拒（限流同上）",
    ("POST", "/api/user/auth/tg-login"):
        "Bot 一键免密登录：登录本身就是取得身份前的动作；凭 32 字节随机一次性 token"
        "（10 分钟过期、一次消费、每用户每小时最多 10 个），且 token 内 user_id 与"
        " telegram_id 必须与 WebUser 记录一致，不一致即 401",
    ("POST", "/api/user/auth/passkey/login/options"):
        "Passkey 登录第一步：登录前动作，只按用户名下发断言选项（allow 列表仅含本人已注册"
        "凭据 id，不含任何密钥材料）；靠 IP 限流（10 次/分钟）；challenge 经 HMAC 无状态"
        "令牌绑定用户 + 5 分钟过期，下一步校验不符即拒",
    ("POST", "/api/user/auth/passkey/login/verify"):
        "Passkey 登录第二步：取得身份前的动作；靠 WebAuthn 断言验签（凭据私钥签名 + "
        "challenge 令牌绑定用户/用途/时效 + origin/rpId 校验 + 签名计数防克隆）+ IP 限流"
        "（10 次/分钟）+ 登录日志（成功与失败都记，reason 沿用 portal_login 口径）",
    ("POST", "/api/user/economy/payment/notify"):
        "支付网关回调：拿不到管理员身份（是网关在调），靠 HMAC 验签 + 金额与订单实付核对 + 订单幂等",
    ("POST", "/api/admin/auth/login"):
        "管理员登录：还没通过鉴权就没有身份可归属；限流（8 次/分钟/IP）+ 人机验证 + 登录日志",
    ("POST", "/api/admin/setup"):
        "首次运行向导：建第一个管理员（完成后永久 403）；还没通过鉴权就没有身份可归属；"
        "替代机制：限流（8 次/分钟/IP）+ 登录日志（成功与失败都记）+ 一次性——"
        "setup_completed 置 True 后入口永久关闭，库里已有管理员时自愈补旗并 403",
}


def collect_write_endpoints() -> dict[tuple[str, str], tuple[object, bool]]:
    """{(方法, 路径): (端点函数, 路由级是否已鉴权)}"""
    found: dict[tuple[str, str], tuple[object, bool]] = {}
    for module_name in ROUTER_MODULES:
        module = importlib.import_module(module_name)
        for obj in list(vars(module).values()):
            if not hasattr(obj, "__dict__"):
                continue
            routes = vars(obj).get("routes")
            if not isinstance(routes, list):
                continue
            router_auth = False
            for dependency in vars(obj).get("dependencies") or ():
                name = getattr(getattr(dependency, "dependency", None), "__name__", "")
                if name in AUTH_DEPENDENCIES:
                    router_auth = True
                    break
            for route in routes:
                methods = set(getattr(route, "methods", None) or ()) & WRITE_METHODS
                path = getattr(route, "path", "")
                if not methods or not (path.startswith("/api/admin") or path.startswith("/api/user")):
                    continue
                for method in sorted(methods):
                    found.setdefault((method, path), (getattr(route, "endpoint", None), router_auth))
    return found


# 「把整段同步逻辑交给模块级函数」的下放调用（与 check_admin_audit_coverage 同一份口径）
OFFLOAD_CALLS = frozenset({"run_in_threadpool", "to_thread", "run_in_executor", "run_sync"})


def guarded(endpoint, router_auth: bool) -> bool:
    """这条路由是否已被鉴权（路由级依赖，或端点签名里出现鉴权依赖）"""
    if router_auth:
        return True
    if endpoint is None:
        return False
    try:
        source = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    return (any(name in source for name in AUTH_DEPENDENCIES)
            or _guarded_in_callees(endpoint, source))


def _guarded_in_callees(endpoint, source: str) -> bool:
    """端点直接调用的**本模块函数**里出现鉴权依赖（只跟一层）

    对应这种写法（见 ``backend/emby_server/stream_nodes.py``）：端点函数体里
    只调 ``_panel_auth(request)``，真正的 ``require_panel_key`` 在那个
    本模块 helper 里——鉴权语义与写在端点里完全一样，不跟这一层就会误报。
    """
    module = inspect.getmodule(endpoint)
    if module is None:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:  # 同步源码必然可解析；解析不了就按「未覆盖」处理
        return False
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if isinstance(func, ast.Name):
            called.add(func.id)
        # ``await run_in_threadpool(_helper, db, ...)``：下放目标是实参而不是被调用方
        if fname in OFFLOAD_CALLS:
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Name):
                    called.add(arg.id)
    for name in sorted(called):
        helper = getattr(module, name, None)
        if helper is None or helper is endpoint or not callable(helper):
            continue
        try:
            helper_source = inspect.getsource(helper)
        except (OSError, TypeError):
            continue
        if any(dep in helper_source for dep in AUTH_DEPENDENCIES):
            return True
    return False


def scan(endpoints: dict | None = None) -> tuple[list[str], list[str], int]:
    """→ (未鉴权的写端点, 名单里已失效的免鉴权条目, 写端点总数)

    ``endpoints`` 只给自检用：喂一份合成端点表，证明这条检查**真的会失败**。
    """
    endpoints = collect_write_endpoints() if endpoints is None else endpoints
    missing: list[str] = []
    for (method, path), (endpoint, router_auth) in sorted(
        endpoints.items(), key=lambda kv: kv[0][1]
    ):
        if (method, path) in PRE_AUTH:
            continue
        if not guarded(endpoint, router_auth):
            missing.append(f"{method:6s} {path:58s} ({getattr(endpoint, '__module__', '?')})")
    stale = [f"{m} {p}（免鉴权登记已失效，删掉它）" for (m, p) in PRE_AUTH if (m, p) not in endpoints]
    return missing, stale, len(endpoints)


def main() -> int:
    missing, stale, total = scan()
    if missing:
        print(f"❌ 有 {len(missing)} 个写端点没有鉴权（共 {total} 个写端点）：")
        for item in missing:
            print("   ", item)
        print("\n改法：给端点加 `Depends(get_current_user)`（用户端）/ `Depends(get_current_admin)`"
              "（管理端），\n或在路由上写 `dependencies=[Depends(...)]`；"
              "确实应该免鉴权（注册 / 登录 / 网关回调类）就登记到\n"
              "``PRE_AUTH`` 并写清「靠什么别的机制挡住」——限流、验签、一次性令牌，"
              "不能只写一句「故意的」。")
        return 1
    if stale:
        print(f"❌ 免鉴权名单里有 {len(stale)} 条死条目：")
        for item in stale:
            print("   ", item)
        return 1
    print(f"✅ 写端点鉴权覆盖完整（共 {total} 个写端点；"
          f"{len(PRE_AUTH)} 个已登记的免鉴权端点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
