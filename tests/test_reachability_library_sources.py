"""媒体库「有没有来源」判定：来源只认本机路径与挂载两类。

CMS 无界片库下线（2026-11-14）后，``upstream_source_ids`` 不再算
来源：只配这一列的库与全空一样报 ``no_sources``；结果里的
``upstream_sources`` 键保留为空列表（面板形状不变）。

- 三种形态（paths / mount_ids / upstream_source_ids）只剩前两种
  算来源，全空 → 照常报 ``no_sources``；
- 普通库（本机路径 / 挂载）的口径一字不变。

不碰数据库：判定函数在传入现成 ``Context`` 时不查表，假库行 + 空上下文即可测。
"""
from types import SimpleNamespace

from backend.emby_server import reachability


def _ctx() -> reachability.Context:
    return reachability.Context(
        playback=reachability.PLAYBACK_PANEL,
        playback_label="面板本机（EM 一体化）",
    )


def _lib(**overrides):
    row = {
        "paths": "",
        "mount_ids": "",
        "upstream_source_ids": "",
        "kind": "local",
        "is_virtual": False,
        "node_id": None,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def _codes(result) -> list:
    return [p["code"] for p in result["problems"]]


def test_upstream_source_ids_no_longer_count_as_sources():
    """只配 upstream_source_ids（旧 CMS 库形态）：CMS 下线后照常报没来源"""
    result = reachability.library_reachability(
        None, _lib(kind="applecms", upstream_source_ids="7,9,10,11"), _ctx())
    assert result["level"] == reachability.LEVEL_WARN
    assert result["code"] == "no_sources"
    assert result["upstream_sources"] == []


def test_library_with_no_sources_at_all_still_warns():
    """全空：照常报「没有配置任何来源」"""
    result = reachability.library_reachability(None, _lib(), _ctx())
    assert result["level"] == reachability.LEVEL_WARN
    assert result["code"] == "no_sources"


def test_local_path_library_is_unchanged():
    """普通库填本机路径：老口径不变，面板本机出流时不报任何问题"""
    result = reachability.library_reachability(
        None, _lib(paths="/data/movies"), _ctx())
    assert result["level"] == reachability.LEVEL_OK
    assert result["problems"] == []
    assert result["local_sources"] == ["/data/movies"]


def test_missing_mount_still_reported_bad():
    """挂载引用了不存在的挂载：原有 bad 判定不许被误伤"""
    result = reachability.library_reachability(
        None, _lib(mount_ids="99"), _ctx())
    assert result["level"] == reachability.LEVEL_BAD
    assert "mount_missing" in _codes(result)
