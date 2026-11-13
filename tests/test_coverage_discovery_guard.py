"""覆盖检查脚本不许再有名单盲区（GA-068 守卫）。

``scripts/check_auth_coverage.py`` 与 ``scripts/check_admin_audit_coverage.py``
过去靠手维护的 ``ROUTER_MODULES`` 名单决定扫哪些模块：新域模块上线时没人登记，
检查就在不全的名单上跑绿——GA-069~073 一批高敏写口（代理分销、软件授权、
公共池、治理停权、合集……）全是从这个盲区漏出去的，「绿」只是已登记子集的绿。

本守卫用**与脚本内部发现相互独立**的一份枚举对账：AST 扫全仓 ``backend/**``，
凡是以字面量 ``prefix=`` 定义了 ``/api/admin`` / ``/api/user`` 路由的模块，
必须出现在对应脚本的覆盖集合（``ROUTER_MODULES``）或排除表
（``EXCLUDED_MODULES``，且每条必须写明理由）里。以后新模块不被发现就红，
不许再靠「有人记得登记」维持覆盖完整。
"""

from __future__ import annotations

import ast
import importlib.util
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"

# GA-068 台账点名的盲区模块（auth 脚本漏 13 个、audit 脚本漏 11 个）
GA068_MODULES = (
    "backend.api.agents_admin",
    "backend.api.admin_license",
    "backend.api.admin_pool",
    "backend.api.admin_alerts",
    "backend.api.admin_tasks",
    "backend.api.admin_player_blacklist",
    "backend.api.media_sets_api",
    "backend.api.panel",
    "backend.api.governance_report",
    "backend.emby_server.playback_bus_admin",
    "backend.api.admin_services",
    "backend.api.admin_artwork",
)


@lru_cache(maxsize=None)
def _load_script(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename[:-3], REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _literal_prefix_map() -> dict[str, frozenset[str]]:
    """独立枚举：模块 → 它以字面量 prefix 定义的 APIRouter 命中的基础前缀。"""
    found: dict[str, set[str]] = {}
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "APIRouter"):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if (keyword.arg == "prefix"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    for base in ("/api/admin", "/api/user"):
                        if value.value.startswith(base):
                            rel = path.relative_to(REPO_ROOT).with_suffix("")
                            parts = list(rel.parts)
                            if parts[-1] == "__init__":
                                parts.pop()
                            found.setdefault(".".join(parts), set()).add(base)
    return {name: frozenset(bases) for name, bases in found.items()}


def _literal_prefix_modules(prefixes: tuple[str, ...]) -> set[str]:
    """独立枚举：以字面量前缀定义了目标 APIRouter 的全部模块。"""
    return {name for name, bases in _literal_prefix_map().items()
            if bases & set(prefixes)}


def _covered(script) -> set[str]:
    return set(script.ROUTER_MODULES) | set(getattr(script, "EXCLUDED_MODULES", {}))


def test_auth_discovery_covers_every_literal_admin_user_router():
    script = _load_script("check_auth_coverage.py")
    enumerated = _literal_prefix_modules(("/api/admin", "/api/user"))
    missing = enumerated - _covered(script)
    assert not missing, (
        "check_auth_coverage 的覆盖集合漏了这些定义了管理/用户路由的模块"
        f"（GA-068 盲区复发）：{sorted(missing)}")


def test_audit_discovery_covers_every_literal_admin_router():
    script = _load_script("check_admin_audit_coverage.py")
    enumerated = _literal_prefix_modules(("/api/admin",))
    missing = enumerated - _covered(script)
    assert not missing, (
        "check_admin_audit_coverage 的覆盖集合漏了这些定义了管理路由的模块"
        f"（GA-068 盲区复发）：{sorted(missing)}")


def test_ga068_blind_spot_modules_are_covered():
    auth = _load_script("check_auth_coverage.py")
    audit = _load_script("check_admin_audit_coverage.py")
    for name in GA068_MODULES:
        assert name in _covered(auth), f"鉴权覆盖仍漏 {name}"
        assert name in _covered(audit), f"审计覆盖仍漏 {name}"


def test_exclusions_carry_reasons_and_point_at_real_modules():
    for filename in ("check_auth_coverage.py", "check_admin_audit_coverage.py"):
        script = _load_script(filename)
        for name, reason in getattr(script, "EXCLUDED_MODULES", {}).items():
            assert isinstance(reason, str) and len(reason.strip()) >= 10, (
                f"{filename} 的排除项 {name} 必须逐条写明理由（不许只写一句「故意的」）")
            module_path = REPO_ROOT / Path(*name.split(".")).with_suffix(".py")
            package_path = REPO_ROOT / Path(*name.split(".")) / "__init__.py"
            assert module_path.exists() or package_path.exists(), (
                f"{filename} 的排除项 {name} 指向不存在的模块（死条目）")
