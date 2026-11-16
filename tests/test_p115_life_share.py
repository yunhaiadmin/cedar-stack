"""115 sidecar life_poll / share_full_sync：轮询差分与分享根全量同步。

只用 mock 的 _raw_list/_cid_of，不碰真 115、不读库、不起 UDS。
诚实口径：life_poll 是轮询差分不是原生推送；share_full_sync 未调用
任何 115 分享接口（transfer115 没有分享 API），只对分享根做全量同步。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import transfer115
from backend.emby_server.p115_sidecar import P115SidecarHandler
from backend.emby_server.provider_runtime import ProviderRuntimeError


@pytest.fixture()
def handler():
    """假客户端 + mock _raw_list/_cid_of 的 handler，会话已建好。"""
    h = P115SidecarHandler(client_factory=lambda cookie, ua: object())
    h.session_upsert("s1", {"cookie": "cookie-123", "ua": "", "root_cid": "0"})
    session = SimpleNamespace(session_id="s1")
    tree = {
        "0": [
            {"cid": "10", "name": "Movies", "is_dir": True, "size": 0, "pickcode": ""},
            {"cid": "f1", "name": "a.mkv", "is_dir": False, "size": 10, "pickcode": "pc-a"},
            {"cid": "f2", "name": "readme.txt", "is_dir": False, "size": 5, "pickcode": "pc-r"},
        ],
        "10": [
            {"cid": "f3", "name": "film.mkv", "is_dir": False, "size": 100, "pickcode": "pc-film"},
        ],
    }
    cid_map = {"/": "0", "/Movies": "10"}

    def fake_raw_list(state, cid):
        return [dict(e) for e in tree.get(cid or "0", [])]

    def fake_cid_of(state, rel):
        rel = "/" + (rel or "").lstrip("/")
        rel = rel.rstrip("/") or "/"
        if rel in cid_map:
            return cid_map[rel]
        raise ProviderRuntimeError("not_found", f"115 上找不到目录：{rel}")

    h._raw_list = fake_raw_list  # noqa: SLF001 — 测试 mock
    h._cid_of = fake_cid_of  # noqa: SLF001 — 测试 mock
    return h, session, tree


def test_life_poll_first_is_baseline_second_reports_added_deleted_changed(handler):
    h, session, tree = handler
    first = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert first["baseline"] is True
    assert first["events"] == []
    assert first["source"] == "poll_diff"
    assert first["is_poll_diff"] is True
    # 第二次：新增 b.mkv、删除 a.mkv、readme.txt 改大小（变更）。
    tree["0"] = [
        {"cid": "10", "name": "Movies", "is_dir": True, "size": 0, "pickcode": ""},
        {"cid": "f2", "name": "readme.txt", "is_dir": False, "size": 6, "pickcode": "pc-r"},
        {"cid": "f4", "name": "b.mkv", "is_dir": False, "size": 20, "pickcode": "pc-b"},
    ]
    second = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert second["baseline"] is False
    assert second["generation"] > first["generation"]
    by_path = {e["path"]: e["type"] for e in second["events"]}
    assert by_path["/b.mkv"] == "added"
    assert by_path["/a.mkv"] == "deleted"
    assert by_path["/readme.txt"] == "changed"
    # 事件里不编造 115 没有的字段：只有 type/path/entry/previous。
    for event in second["events"]:
        assert set(event) == {"type", "path", "entry", "previous"}
    changed = next(e for e in second["events"] if e["path"] == "/readme.txt")
    assert changed["previous"]["size"] == 5
    assert changed["entry"]["size"] == 6


def test_share_full_sync_returns_full_entries_with_share_root(handler):
    h, session, _tree = handler
    result = h.call_extension(session, "p115.share_full_sync", {"root_cid": "0"})
    assert result["share_root_cid"] == "0"
    assert result["share_mode"] == "full_sync_via_root"
    assert result["truncated"] is False
    assert isinstance(result["generation"], int)
    paths = {e["path"] for e in result["entries"]}
    # 全量 walk 含嵌套目录下的文件。
    assert "/a.mkv" in paths
    assert "/Movies/film.mkv" in paths


def test_share_full_sync_by_share_path_resolves_cid(handler):
    h, session, _tree = handler
    result = h.call_extension(session, "p115.share_full_sync", {"path": "/Movies"})
    assert result["share_root_cid"] == "10"
    paths = {e["path"] for e in result["entries"]}
    assert paths == {"/Movies/film.mkv"}


def test_unknown_extension_still_errors_and_not_implemented_is_gone(handler):
    h, session, _tree = handler
    # 两个新扩展不再报 not_implemented。
    assert h.call_extension(session, "p115.life_poll", {"directories": ["/"]})["source"] == "poll_diff"
    assert h.call_extension(session, "p115.share_full_sync", {"root_cid": "0"})["share_mode"] == "full_sync_via_root"
    # 未知扩展仍 fail-closed。
    with pytest.raises(ProviderRuntimeError) as excinfo:
        h.call_extension(session, "p115.no_such_extension", {})
    assert excinfo.value.kind == "unknown_method"


# ---- 失败 / 空结果回归（闭环 #12 补测）----
# 这一组走真实 _raw_list（假客户端 list_dir），让 115 异常分级
# （cookie_expired / remote_unavailable）也被覆盖，而不是绕过映射层。


class _FakeClient:
    """可编程假 115 客户端：tree 给目录内容，exc 非空时 list_dir 抛它。"""

    def __init__(self):
        self.tree: dict[str, list[dict]] = {"0": []}
        self.exc: Exception | None = None

    def list_dir(self, cid="0"):
        if self.exc is not None:
            raise self.exc
        return [dict(e) for e in self.tree.get(cid or "0", [])]

    def download_url(self, pickcode):  # pragma: no cover - 本组用例不取链
        return "https://example.invalid/dl"


@pytest.fixture()
def client_handler():
    fake = _FakeClient()
    h = P115SidecarHandler(client_factory=lambda cookie, ua: fake)
    h.session_upsert("s1", {"cookie": "UID=1;CID=2;SEID=3", "ua": "", "root_cid": "0"})
    return h, fake, SimpleNamespace(session_id="s1")


def test_life_poll_empty_directory_baseline_then_quiet(client_handler):
    h, _fake, session = client_handler
    first = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert first["baseline"] is True
    assert first["snapshot_size"] == 0
    assert first["events"] == []
    second = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert second["baseline"] is False
    assert second["events"] == []


def test_life_poll_failure_does_not_advance_snapshot(client_handler):
    h, fake, session = client_handler
    # 首次轮询就撞上 Cookie 失效：按 cookie_expired 报错且不建基线。
    fake.exc = transfer115.Pan115AuthError("cookie dead")
    with pytest.raises(ProviderRuntimeError) as excinfo:
        h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert excinfo.value.kind == "cookie_expired"
    # 恢复后仍是基线轮（失败没把空快照存进去）。
    fake.exc = None
    fake.tree = {"0": [
        {"cid": "f1", "name": "a.mkv", "is_dir": False, "size": 10, "pickcode": "pc-a"},
    ]}
    first = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert first["baseline"] is True and first["events"] == []
    # 基线之后再失败：remote_unavailable，且快照不前进。
    fake.exc = transfer115.Pan115Error("upstream boom")
    with pytest.raises(ProviderRuntimeError) as excinfo:
        h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    assert excinfo.value.kind == "remote_unavailable"
    # 恢复后按失败前的基线出差分（a.mkv 删除、b.mkv 新增）。
    fake.exc = None
    fake.tree = {"0": [
        {"cid": "f2", "name": "b.mkv", "is_dir": False, "size": 20, "pickcode": "pc-b"},
    ]}
    second = h.call_extension(session, "p115.life_poll", {"directories": ["/"]})
    by_path = {e["path"]: e["type"] for e in second["events"]}
    assert by_path == {"/a.mkv": "deleted", "/b.mkv": "added"}


def test_life_poll_missing_directory_is_reported_not_fatal(client_handler):
    h, _fake, session = client_handler
    result = h.call_extension(session, "p115.life_poll", {"directories": ["/gone"]})
    assert result["missing"] == ["/gone"]
    assert result["baseline"] is True


def test_share_full_sync_empty_root(client_handler):
    h, _fake, session = client_handler
    result = h.call_extension(session, "p115.share_full_sync", {"root_cid": "0"})
    assert result["entries"] == []
    assert result["truncated"] is False
    assert result["share_root_cid"] == "0"


def test_share_full_sync_bad_path_and_upstream_failure(client_handler):
    h, fake, session = client_handler
    with pytest.raises(ProviderRuntimeError) as excinfo:
        h.call_extension(session, "p115.share_full_sync", {"path": "/Nope"})
    assert excinfo.value.kind == "not_found"
    fake.exc = transfer115.Pan115Error("upstream boom")
    with pytest.raises(ProviderRuntimeError) as excinfo:
        h.call_extension(session, "p115.share_full_sync", {"root_cid": "0"})
    assert excinfo.value.kind == "remote_unavailable"
