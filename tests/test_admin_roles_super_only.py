"""管理员角色边界：哪些写操作只允许超管。

## 为什么要有这个文件

``SUPER_ONLY_PREFIXES`` 是**前缀名单**：漏一个前缀，那个域���的写操作就对
「运营」角色敞开，界面上的「仅超管」提示只剩提示——直接打接口就绕过去了。

真实缺陷就是这个形状：访问拦截（UA 黑白名单 + IP 地区封禁）是全站开关，
路由注释写着「仅超管可改」，前端也确实没有把它开放给运营，但名单里**漏了**
``/api/admin/access-guard``，于是运营角色能直接改。一个只读名单写歪就能把
所有人（包括别的管理员）挡在门外，而这种开关只能回这一页改。
"""
from types import SimpleNamespace

import pytest

from backend.admin_roles import (
    ROLE_OPERATOR,
    ROLE_SUPER,
    ROLE_VIEWER,
    blocked_reason,
    is_super,
)


def _admin(role: str):
    return SimpleNamespace(admin_role=role, is_staff=False)


def _req(path: str, method: str = "POST"):
    return SimpleNamespace(url=SimpleNamespace(path=path), method=method)


@pytest.mark.parametrize("path", [
    "/api/admin/access-guard/policy",
    "/api/admin/access-guard/preview",
    "/api/admin/share-guard/policy",
    "/api/admin/admins",
    "/api/admin/playback/policy",
    "/api/admin/api-keys",  # v2.58.0：Service Token 的创建/吊销（防运营自发 admin Key 提权）
])
def test_operator_cannot_touch_super_only_endpoints(path):
    """运营角色对这些写操作一律 403 —— 界面提示之外，服务端自己也要判一次"""
    assert blocked_reason(_req(path), _admin(ROLE_OPERATOR)), f"{path} 应仅限超管"


def test_super_can_still_do_it():
    """不能把超管自己挡在门外"""
    assert blocked_reason(_req("/api/admin/access-guard/policy"), _admin(ROLE_SUPER)) is None
    assert is_super(_admin(ROLE_SUPER))


def test_reads_are_never_super_only():
    """只读 GET 一律放行：审计与排查角色必须能看到这些配置长什么样"""
    for role in (ROLE_SUPER, ROLE_OPERATOR, ROLE_VIEWER):
        for path in ("/api/admin/access-guard", "/api/admin/share-guard"):
            assert blocked_reason(_req(path, "GET"), _admin(role)) is None, f"{role} 看 {path}"


def test_viewer_cannot_write_anywhere():
    """只读角色连防共享、访问拦截都改不了（也不只是超管专属那几项）"""
    assert blocked_reason(_req("/api/admin/access-guard/policy"), _admin(ROLE_VIEWER))
    assert blocked_reason(_req("/api/admin/users/1/ban"), _admin(ROLE_VIEWER))


def test_empty_role_is_treated_as_lowest_privilege():
    """S1（fail closed）：空 / 未知角色按 viewer 处理，不再按 super——
    升级上来的老管理员由启动期迁移显式写成 super（见 admin_roles.ensure_legacy_admin_roles）"""
    legacy = SimpleNamespace(admin_role=None, is_staff=True)
    assert blocked_reason(_req("/api/admin/access-guard/policy"), legacy)
    assert blocked_reason(_req("/api/admin/users/1", method="PUT"), legacy)
    assert blocked_reason(_req("/api/admin/users/1", method="GET"), legacy) is None
    bogus = SimpleNamespace(admin_role="root", is_staff=True)
    assert blocked_reason(_req("/api/admin/users/1", method="PUT"), bogus)


# ==================== R49 M2：凭据类路径后缀集中判定 ====================
# 散落的 _require_super 收进 admin_roles 的 SUPER_ONLY_SUFFIXES：读写一律只限超管。
# 口径补正（2026-10-19）：原 PR 文把 GET 读放宽成「人人可看」，冒烟测试确诊是回归
# （旧 _require_super 拦读写、冒烟断言「读写都被拒」），恢复成读也要拦。

_RCLONE = "/api/admin/servers/5/rclone-conf"

# 7 条服务器写路由：operator 仍可写（M2 未动它们，防回归）
_SERVER_WRITE_ROUTES = [
    ("POST", "/api/admin/servers"),
    ("PUT", "/api/admin/servers/5"),
    ("POST", "/api/admin/servers/test"),
    ("POST", "/api/admin/servers/5/test"),
    ("POST", "/api/admin/servers/5/activate"),
    ("POST", "/api/admin/servers/5/toggle"),
    ("DELETE", "/api/admin/servers/5"),
]


def test_rclone_conf_write_is_super_only():
    """写 rclone.conf 仍只限超管：operator/viewer 的 POST 一律 403"""
    for role in (ROLE_OPERATOR, ROLE_VIEWER):
        reason = blocked_reason(_req(_RCLONE, "POST"), _admin(role))
        assert reason, f"{role} POST {_RCLONE} 应被拦"
    assert blocked_reason(_req(_RCLONE, "POST"), _admin(ROLE_SUPER)) is None


def test_rclone_conf_read_is_super_only():
    """GET rclone-conf 也只限超管：operator/viewer 的读一律 403
    （2026-10-19 补正：rclone.conf 含网盘凭据，恢复旧 _require_super 的读写都拦口径；
    冒烟测试 scripts/smoke_test_servers.py 的「读写都被拒」是长约，之前放宽读是回归）"""
    for role in (ROLE_OPERATOR, ROLE_VIEWER):
        reason = blocked_reason(_req(_RCLONE, "GET"), _admin(role))
        assert reason, f"{role} GET {_RCLONE} 应被拦"
        assert "超级管理员" in reason
    assert blocked_reason(_req(_RCLONE, "GET"), _admin(ROLE_SUPER)) is None


@pytest.mark.parametrize("method,path", _SERVER_WRITE_ROUTES)
def test_server_write_routes_still_open_to_operator(method, path):
    """M2 未动服务器其余写路由：operator 仍可写，viewer 仍被拦（防回归）"""
    assert blocked_reason(_req(path, method), _admin(ROLE_OPERATOR)) is None, f"operator {method} {path}"
    assert blocked_reason(_req(path, method), _admin(ROLE_VIEWER)), f"viewer {method} {path} 应被拦"


def test_rclone_conf_suffix_does_not_overmatch():
    """后缀判定不误伤：路径里含 rclone-conf 但不是后缀结尾的不受影响"""
    assert blocked_reason(_req("/api/admin/servers/rclone-conf-list", "POST"), _admin(ROLE_OPERATOR)) is None
    assert blocked_reason(_req("/api/admin/mounts/health", "POST"), _admin(ROLE_OPERATOR)) is None


# ==================== 批 7 补后端批（2026-10-26） ====================
# 批 7 审计定档的 5 个 B 档写口：运营此前可写，现收进 SUPER_ONLY_PREFIXES。
# 判定走同一集中机制（get_current_admin → ensure_admin_allowed → blocked_reason，
# emby 路由的 require_staff 经 _bearer_admin 同样走 get_current_admin）。
# 前缀判定只拦非安全方法，各口 GET 读口不受影响——与名单内其它前缀同口径。

_BATCH7_SUPER_ONLY_WRITES = [
    ("POST", "/api/admin/emby/mounts/rclone/conf"),
    ("PUT", "/api/admin/settings/registration"),
    ("PUT", "/api/admin/hotlink-config"),
    ("POST", "/api/admin/login-logs/purge"),
    ("PUT", "/api/admin/items-dedup-cache-config"),
]

_BATCH7_READ_SIBLINGS = [
    "/api/admin/emby/mounts/rclone/conf",
    "/api/admin/settings/registration",
    "/api/admin/hotlink-config",
    "/api/admin/login-logs",
    "/api/admin/items-dedup-cache-config",
]


@pytest.mark.parametrize("method,path", _BATCH7_SUPER_ONLY_WRITES)
def test_batch7_operator_and_viewer_cannot_write(method, path):
    """运营 / 只读调这 5 个写口一律被拦（返回拒绝的判定即 blocked_reason 非空）"""
    for role in (ROLE_OPERATOR, ROLE_VIEWER):
        reason = blocked_reason(_req(path, method), _admin(role))
        assert reason, f"{role} {method} {path} 应被拦"
        assert "超级管理员" in reason


@pytest.mark.parametrize("method,path", _BATCH7_SUPER_ONLY_WRITES)
def test_batch7_super_can_still_write(method, path):
    """超管调这 5 个写口仍放行：不能把超管自己挡在门外"""
    assert blocked_reason(_req(path, method), _admin(ROLE_SUPER)) is None, f"super {method} {path}"


@pytest.mark.parametrize("path", _BATCH7_READ_SIBLINGS)
def test_batch7_reads_stay_open_to_operator(path):
    """只改写口：各口 GET 读口运营仍可看（前缀判定只拦非安全方法）"""
    assert blocked_reason(_req(path, "GET"), _admin(ROLE_OPERATOR)) is None, f"operator GET {path}"
    assert blocked_reason(_req(path, "GET"), _admin(ROLE_VIEWER)) is None, f"viewer GET {path}"


# ==================== 全局审计 GA-069~072（2026-11-12） ====================
# 四条同族缺陷：角色名单漏收，运营 / 只读能碰凭据级与门禁级材料。
# - GA-069 整库备份、GA-070 软件授权：整子树任何方法只给超管
#   （SUPER_ONLY_ALL_PREFIXES）——下载与状态回显都是 GET，只拦写等于不拦。
# - GA-072 公共池配置：只收 4 个配置口的写（读不受影响），成员审核等日常
#   运营写口同前缀但不收（防误伤断言在下面）。
# - GA-071 上游密钥：只收刮削页的密钥子树写口；专线 reset-secret 走后缀
#   精确命中。重刮 / 绑 TMDB 等日常写口不收（防误伤断言在下面）。

_GA_ALL_METHOD_SUPER_ONLY = [
    # GA-069：备份整子树（下载是重灾区：viewer 凭 GET 拉走整库备份）
    ("GET", "/api/admin/system/backup"),
    ("PUT", "/api/admin/system/backup"),
    ("POST", "/api/admin/system/backup/run"),
    ("GET", "/api/admin/system/backup/files/aetrix-20261112.db.gz/info"),
    ("GET", "/api/admin/system/backup/files/aetrix-20261112.db.gz/download"),
    # GA-070：授权整子树（status 原样回显完整授权码，viewer 可读）
    ("GET", "/api/admin/license/status"),
    ("PUT", "/api/admin/license/config"),
    ("POST", "/api/admin/license/verify"),
    # GA-071 同类：专线入口密钥重置（后缀命中，方法无关）
    ("POST", "/api/admin/emby/exclusive-lines/7/reset-secret"),
]

_GA_WRITE_SUPER_ONLY = [
    # GA-072：池 4 个配置口
    ("PUT", "/api/admin/pool/config"),
    ("PUT", "/api/admin/pool/reward-config"),
    ("PUT", "/api/admin/pool/janitor-config"),
    ("PUT", "/api/admin/pool/retry-config"),
    # GA-071：TMDB 密钥池（整池保存 / 新增 / 删除）
    ("PUT", "/api/admin/emby/scrape/tmdb-keys"),
    ("POST", "/api/admin/emby/scrape/tmdb-keys/add"),
    ("DELETE", "/api/admin/emby/scrape/tmdb-keys/0"),
    # GA-071：多源密钥增删（{source_id}/keys 段）
    ("POST", "/api/admin/emby/scrape/meta-sources/douban/keys"),
    ("DELETE", "/api/admin/emby/scrape/meta-sources/douban/keys/0"),
]

# 防误伤：这些日常运营写口必须仍然对 operator 放行（viewer 照旧全拦）。
_GA_OPERATOR_STILL_ALLOWED = [
    # 池：成员审核 / 清退 / 奖励结算 / 成员补丁（pool_admin.py，与配置同前缀）
    ("POST", "/api/admin/pool/members/3/approve"),
    ("POST", "/api/admin/pool/members/3/eject"),
    ("POST", "/api/admin/pool/rewards/settle"),
    ("PATCH", "/api/admin/pool/members/3"),
    # 刮削：重刮 / 绑 TMDB / 锁条目 / 探活暂停 / 缺失重入队
    ("POST", "/api/admin/emby/scrape/libraries/9/rescrape"),
    ("POST", "/api/admin/emby/scrape/items/9/bind-tmdb"),
    ("POST", "/api/admin/emby/scrape/items/9/lock"),
    ("POST", "/api/admin/emby/scrape/probe/pause"),
    ("POST", "/api/admin/emby/scrape/missing-metadata/requeue"),
    # 刮削：密钥连通性测试只报掩码与有效性，不是密钥材料
    ("POST", "/api/admin/emby/scrape/tmdb-test"),
    # 刮削：PUT meta-sources 经查实不动密钥池（开关 / 顺序 / 限速）
    ("PUT", "/api/admin/emby/scrape/meta-sources"),
    # 专线：线路本身的增删改是运营日常活，只有 reset-secret 收紧
    ("POST", "/api/admin/emby/exclusive-lines"),
    ("PUT", "/api/admin/emby/exclusive-lines/7"),
    ("DELETE", "/api/admin/emby/exclusive-lines/7"),
    # 后缀不误伤：reset-secret 不在结尾的不受影响
    ("POST", "/api/admin/emby/exclusive-lines/7/reset-secret-history"),
]


@pytest.mark.parametrize("method,path", _GA_ALL_METHOD_SUPER_ONLY)
def test_ga069_072_all_methods_blocked_for_non_super(method, path):
    """备份 / 授权 / 专线密钥重置：viewer 与 operator 任何方法都被拦"""
    for role in (ROLE_OPERATOR, ROLE_VIEWER):
        reason = blocked_reason(_req(path, method), _admin(role))
        assert reason, f"{role} {method} {path} 应被拦"
        assert "超级管理员" in reason


@pytest.mark.parametrize("method,path", _GA_ALL_METHOD_SUPER_ONLY + _GA_WRITE_SUPER_ONLY)
def test_ga069_072_super_still_allowed(method, path):
    """超管调这些口仍放行：不能把超管自己挡在门外"""
    assert blocked_reason(_req(path, method), _admin(ROLE_SUPER)) is None, f"super {method} {path}"


@pytest.mark.parametrize("method,path", _GA_WRITE_SUPER_ONLY)
def test_ga071_072_writes_blocked_for_operator_reads_open(method, path):
    """池配置 / 上游密钥：operator 写被拦、同路径 GET 读不受影响"""
    reason = blocked_reason(_req(path, method), _admin(ROLE_OPERATOR))
    assert reason, f"operator {method} {path} 应被拦"
    assert "超级管理员" in reason
    assert blocked_reason(_req(path, method), _admin(ROLE_VIEWER)), f"viewer {method} {path} 应被拦"
    assert blocked_reason(_req(path, "GET"), _admin(ROLE_OPERATOR)) is None, f"operator GET {path}"
    assert blocked_reason(_req(path, "GET"), _admin(ROLE_VIEWER)) is None, f"viewer GET {path}"


@pytest.mark.parametrize("method,path", _GA_OPERATOR_STILL_ALLOWED)
def test_ga069_072_operator_daily_writes_not_blocked(method, path):
    """防误伤：同前缀 / 同模块的日常运营写口 operator 仍可写，viewer 仍全拦"""
    assert blocked_reason(_req(path, method), _admin(ROLE_OPERATOR)) is None, f"operator {method} {path}"
    assert blocked_reason(_req(path, method), _admin(ROLE_VIEWER)), f"viewer {method} {path} 应被拦"