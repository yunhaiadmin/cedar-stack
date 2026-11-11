"""全测试进程内状态隔离。

enrich_worker 里有几个模块级 60s TTL 缓存（豆瓣/altmeta/夜间配置、单集等父级
等待计数）：生产里这是有意的进程内缓存，但 pytest 同一进程跑全量 tests/ 时，
前一个测试文件 mock 进去的配置值会泄漏到后一个文件，造成顺序相关的假失败
（完美审查 R17 C-P2-5 引入）。

每个测试前把这些缓存清零，保证用例之间相互独立。只影响测试，生产代码无改动。

另含 pytest-xdist 的 per-worker 进程隔离（文件顶部、任何 backend import 之前
执行）：2026-11-02 实测 -n auto --dist loadfile 把 pytest 计算压约 48%，但
34 failed + 17 errors 横跨 16 文件——4 个 worker 共用 job 级 env 的同一个
ci-smoke.db 文件，种子数据被其他 worker 的写入/删除踩掉（证据与回滚记录见
reports/ci-speedup-20261102.md）。容量三期（2026-11-11）按该报告留的路径 1
补齐前置：每个 worker 改用独立 DB 文件、独立 TMPDIR、独立 provider socket
目录，串行跑法（无 PYTEST_XDIST_WORKER）行为逐字不变。
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest


def _xdist_worker_isolation() -> None:
    """xdist worker 进程的共享状态隔离；非 worker 进程直接返回。

    必须在 backend.database 被 import 前完成：它在 import 期读 DATABASE_URL
    建全局 engine。conftest 在收集前加载、且本模块不在顶层 import backend，
    故放在这里执行时机成立。
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return

    # 独立 TMPDIR：tempfile 与 pytest 的 tmp 工厂都从这里派生，避免多 worker
    # 在同一临时根下按同名路径互踩（pytest 自身的 basetemp 已按 worker 分开，
    # 这里管的是 tempfile.gettempdir() 一系）。
    base_tmp = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    worker_tmp = base_tmp / f"pytest-xdist-{worker}"
    worker_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(worker_tmp)
    tempfile.tempdir = str(worker_tmp)

    # 独立 DB 文件：仅改写指向文件的 sqlite 连接串（内存库与非 sqlite 不动），
    # 文件名插入 -<worker> 后缀，目录保持原样；-wal/-shm 会自然跟随新文件名。
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///") and ":memory:" not in url:
        raw = url[len("sqlite:///"):]
        path_part, sep, query = raw.partition("?")
        p = Path(path_part)
        if not p.is_absolute():
            p = Path.cwd() / p
        isolated = p.with_name(f"{p.stem}-{worker}{p.suffix}")
        os.environ["DATABASE_URL"] = f"sqlite:///{isolated}{sep}{query}"

    # 独立 provider socket 目录：115 sidecar 的 socket 路径由该 env 派生
    # （provider_runtime 每次调用时读 env），多 worker 共用会抢同一 socket。
    sock_dir = os.environ.get("PROVIDER_RUNTIME_SOCK_DIR")
    if sock_dir:
        os.environ["PROVIDER_RUNTIME_SOCK_DIR"] = f"{sock_dir}-{worker}"


_xdist_worker_isolation()


@pytest.fixture()
def uds_sock_dir():
    """AF_UNIX socket 路径上限约 107 字节：本地门 TMPDIR 很长时，
    tmp_path 下的 socket 路径会超限（bind 报 AF_UNIX path too long），
    与被测代码无关（2026-11-09 实证：门 TMPDIR 下 4 个 sidecar 测试
    全因此红，/tmp 下全绿）。socket 文件本身极小，放 /tmp 下的
    短路径独立目录，用完清理；生产代码与 CI 不受影响。
    """
    path = Path(tempfile.mkdtemp(prefix="uds-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_enrich_worker_caches():
    try:
        from backend.emby_server import enrich_worker as ew
    except Exception:
        yield
        return
    for _name in ("_douban_cfg_cache", "_altmeta_cfg_cache", "_night_cfg_cache",
                  "_enrich_tuning_cache"):
        _c = getattr(ew, _name, None)
        if isinstance(_c, dict):
            _c.clear()
            _c.update({"at": 0.0, "cfg": None})
    _w = getattr(ew, "_parent_wait_counts", None)
    if isinstance(_w, dict):
        _w.clear()
    yield


@pytest.fixture(autouse=True)
def _isolate_transfer_stage_dir(tmp_path, monkeypatch):
    """跨 provider 源字节暂存是 worker 本地盘状态：测试间必须隔离。

    暂存目录默认挂在 tempfile.gettempdir() 下，同 pytest 进程内跨文件
    共享会导致「前一文件暂存了同内容、后一文件断言源读取次数」类
    顺序假失败（2026-10-31 第三单引入时实证：multipart 文件内前一
    用例暂存了同 key、后一用例哈希遍零次碰源）。每测一个独立目录，
    且清空进程内 stage 注册表。
    """
    monkeypatch.setenv("TRANSFER_STAGE_DIR", str(tmp_path / "transfer-stage"))
    try:
        from backend.emby_server import transfer_variant as tv
        with tv._STAGE_REGISTRY_LOCK:
            tv._STAGE_REGISTRY.clear()
    except Exception:
        pass
    yield
