"""NFO 指纹持久缓存（谷歌盘调用削减 P1，审计报告 drive-call-audit-20261122）

背景：.nfo 经 rclone 被重复读（生产实测 12,109 次/日）。旧实现只有
``_ScanContext`` 内的内存缓存，一轮扫描结束即失效——下一轮扫描 / enrich
重试时，同一个 .nfo 文件又要发一次 GET，哪怕它一个字节都没变过。

钉住的行为：
- 列举时本来就能拿到 .nfo 的 size / ModTime：拼成指纹与上次落库的一致
  → 跳过 GET，直接复用落库的解析结果（新 ctx 模拟新一轮扫描）。
- 指纹变了（size 变或 ModTime 变）→ 照常重读重解析，拿到新内容。
- 解析失败的 NFO 同样按指纹缓存（阴性）：内容没变不重复 GET。
- 列举拿不到指纹（size 与 ModTime 都缺位）→ 回落旧行为，每轮照常读，
  绝不凭空跳过。

全程 mock 提供者计数，不碰任何真实 Drive / rclone / 谷歌接口。
"""
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

import pytest  # noqa: E402

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import init_db  # noqa: E402
from backend.emby_server.scanner import _find_nfos, _ScanContext, ScanFile  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """本文件独占临时库：模块内换绑、结束还原（与 test_scan_dir_due_scheduling 同款）。"""
    saved_engine = _dbmod.engine
    saved_factory = _dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = saved_engine
        _dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        engine.dispose()


NFO_V1 = """<?xml version="1.0" encoding="UTF-8" ?>
<movie>
  <title>指纹测试片</title>
  <plot>第一版简介</plot>
  <year>2024</year>
  <tmdbid>111</tmdbid>
</movie>
"""

NFO_V2 = """<?xml version="1.0" encoding="UTF-8" ?>
<movie>
  <title>指纹测试片·改</title>
  <plot>第二版简介</plot>
  <year>2024</year>
  <tmdbid>222</tmdbid>
</movie>
"""


class _Entry:
    """列举条目：与 MountEntry 同口径（name / size / mod_ts）。"""

    def __init__(self, name, size=0, mod_ts=0.0):
        self.name = name
        self.size = size
        self.mod_ts = mod_ts


class _CountingProvider:
    """计数型假提供者：tree = {dir_rel: {fname: [text, size, mod_ts]}}。

    ``read_text`` 就是经 rclone 的那次 GET——本文件所有断言都盯它计数。
    """

    def __init__(self, tree):
        self.tree = tree
        self.read_calls = 0
        self.list_calls = []

    def list_dir(self, dir_rel):
        self.list_calls.append(dir_rel)
        return [_Entry(n, size=v[1], mod_ts=v[2])
                for n, v in self.tree.get(dir_rel, {}).items()]

    def read_text(self, rel):
        self.read_calls += 1
        d, f = os.path.split(rel)
        return self.tree[d][f][0]


def _ctx():
    return _ScanContext(snap=SimpleNamespace(), lib_id=1, stats={})


def _movie_scanfile(provider, dir_rel):
    return ScanFile(
        stored_path=f"mount://9{dir_rel}/测试片.mkv", name="测试片.mkv",
        local_dir=None, dir_rel=dir_rel, mount_id=9, provider=provider,
    )


def _tree(dir_rel, text, size, mod_ts):
    return {dir_rel: {"测试片.nfo": [text, size, mod_ts]}}


def test_second_scan_skips_get_when_fingerprint_unchanged():
    """两轮扫描（两个新 ctx）指纹未变：第二轮零 GET，解析结果照常拿到"""
    dir_rel = "/电影/指纹未变"
    prov = _CountingProvider(_tree(dir_rel, NFO_V1, size=321, mod_ts=1_700_000_000.0))

    data1, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert data1 and data1["title"] == "指纹测试片"
    assert prov.read_calls == 1

    data2, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert prov.read_calls == 1, "指纹未变时第二轮不应再发 GET"
    assert data2 and data2["title"] == "指纹测试片"
    assert data2["tmdb_id"] == "111"


def test_changed_mtime_rereads():
    """ModTime 变了（同尺寸替换）→ 必须重读，拿到新内容"""
    dir_rel = "/电影/指纹mtime变"
    prov = _CountingProvider(_tree(dir_rel, NFO_V1, size=321, mod_ts=1_700_000_000.0))
    data1, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert data1["tmdb_id"] == "111"
    assert prov.read_calls == 1

    prov.tree[dir_rel]["测试片.nfo"] = [NFO_V2, 321, 1_700_000_100.0]
    data2, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert prov.read_calls == 2
    assert data2 and data2["title"] == "指纹测试片·改"
    assert data2["tmdb_id"] == "222"


def test_changed_size_rereads():
    """size 变了 → 必须重读"""
    dir_rel = "/电影/指纹size变"
    prov = _CountingProvider(_tree(dir_rel, NFO_V1, size=321, mod_ts=1_700_000_000.0))
    _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert prov.read_calls == 1

    prov.tree[dir_rel]["测试片.nfo"] = [NFO_V2, 999, 1_700_000_000.0]
    data2, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert prov.read_calls == 2
    assert data2 and data2["tmdb_id"] == "222"


def test_unparseable_nfo_negative_cached_by_fingerprint():
    """解析不出东西的 NFO：指纹未变时第二轮也不重复 GET"""
    dir_rel = "/电影/指纹阴性"
    prov = _CountingProvider(_tree(dir_rel, "这不是 XML", size=10, mod_ts=1_700_000_000.0))

    data1, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert data1 is None
    assert prov.read_calls == 1

    data2, _, _ = _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert data2 is None
    assert prov.read_calls == 1, "阴性结果也应按指纹复用，不重复 GET"


def test_no_fingerprint_falls_back_to_read_every_scan():
    """列举给不出 size / ModTime → 指纹缺位，回落旧行为：每轮照常读"""
    dir_rel = "/电影/指纹缺位"
    prov = _CountingProvider(_tree(dir_rel, NFO_V1, size=0, mod_ts=0.0))

    _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    _find_nfos(_ctx(), _movie_scanfile(prov, dir_rel), "movie")
    assert prov.read_calls == 2
