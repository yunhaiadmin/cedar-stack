"""MOUNT_TIMEOUT 缺省值守卫：未设环境变量时缺省必须 >= 60 秒。

背景（2026-11-22 重建实证）：媒体库 14（影库）根下 9,611 个子目录，
单次列举约 24 秒，而旧缺省 20 秒导致 worker 根列举必超时、扫描只能
partial。当时靠生产 .env 写 MOUNT_TIMEOUT=120 救急，但代码缺省仍是 20，
空库/新机重建会照样复发。本测试钉住代码缺省，防止再被改小。

实现：照 tests/test_perfection_r42_envparse.py 的子进程写法——在子进程里
scrub 掉 MOUNT_TIMEOUT 后全新 import backend.emby_server.mounts 读常量，
避免本进程已 import 的模块缓存与其它测试设置的环境变量干扰。
另加一条 AST 源码断言作为廉价兜底（不依赖 import 副作用，定位更直接）：
解析 mounts.py 里 env_float("MOUNT_TIMEOUT", <缺省>) 调用的缺省字面量。
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOUNTS_PY = REPO_ROOT / "backend" / "emby_server" / "mounts.py"


def _default_from_source() -> float:
    """从 mounts.py 源码解析 MOUNT_TIMEOUT 的 env_float 缺省字面量。"""
    tree = ast.parse(MOUNTS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "MOUNT_TIMEOUT" for t in node.targets):
            continue
        call = node.value
        assert isinstance(call, ast.Call), "MOUNT_TIMEOUT 应由 env_float(...) 赋值"
        assert isinstance(call.func, ast.Name) and call.func.id == "env_float"
        assert len(call.args) >= 2, "env_float 调用应带缺省值参数"
        default_node = call.args[1]
        assert isinstance(default_node, ast.Constant), "缺省值应为字面量常量"
        return float(default_node.value)
    raise AssertionError("mounts.py 中未找到 MOUNT_TIMEOUT = env_float(...) 赋值")


def test_mount_timeout_default_literal_at_least_60():
    assert _default_from_source() >= 60


def test_mount_timeout_default_in_fresh_process_without_env():
    code = (
        "import json; "
        "import backend.emby_server.mounts as m; "
        "print(json.dumps({'mount_timeout': m.MOUNT_TIMEOUT}))"
    )
    env = dict(os.environ)
    env.pop("MOUNT_TIMEOUT", None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=str(REPO_ROOT), env=env,
    )
    assert r.returncode == 0, f"子进程 import mounts 失败: {r.stderr[-500:]}"
    value = float(json.loads(r.stdout.strip().splitlines()[-1])["mount_timeout"])
    assert value >= 60, f"未设 MOUNT_TIMEOUT 时缺省应 >= 60，实际 {value}"
