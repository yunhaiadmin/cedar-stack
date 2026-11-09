"""回归测试：Drive 源站 401 时坏 token 必须能被按值失效（单套缓存）。

覆盖 401 毒化修复：relay 层拿到 401 之后，必须能把坏 token 从
drive_auth 单套 _token_cache 中清除。收敛前另有一套 drive_changes
内层缓存，只清外层会被内层架空——现内层缓存已删，失效只清这一套。
"""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("DATABASE_TYPE", "sqlite")

import pytest

try:
    from backend.emby_server import drive_auth
except ImportError:  # 兼容以 backend 为根的扁平导入路径
    from emby_server import drive_auth


SA_EMAILS = ("sa1@x", "sa2@x", "sa3@x")

DRIVE_MEDIA_URL = "https://www.googleapis.com/drive/v3/files/abc?alt=media"


def _make_sa(email: str) -> dict:
    """构造一个可被 _is_sa_dict 认可的假 SA 字典。"""
    return {"client_email": email, "private_key": f"private-key-for-{email}"}


@pytest.fixture(autouse=True)
def reset_drive_auth_state():
    """每个用例前后清空 drive_auth 的模块级缓存，避免用例间串味。"""
    drive_auth._reset_state()
    yield
    drive_auth._reset_state()


@pytest.fixture
def fake_drive_changes(monkeypatch):
    """挂载假 drive_changes 模块。"""
    fake = mock.MagicMock()
    fake._discover_sa_files.return_value = [_make_sa(e) for e in SA_EMAILS]
    fake._sa_access_token.side_effect = lambda sa, min_remaining=60: "token-for-" + sa["client_email"]
    # 固定选第一个 SA，便于断言 client_email 与内层 key 的对应关系。
    fake._sa_rotation_start.side_effect = lambda n: 0
    monkeypatch.setattr(drive_auth, "_load_drive_changes", lambda: fake)
    return fake


def test_invalidate_bad_token_then_reexchange(fake_drive_changes):
    """坏 token 被按值失效后，下一次取 token 必须重新换取。"""
    fake_drive_changes._sa_access_token.side_effect = iter(["BAD", "GOOD"])

    assert drive_auth.get_drive_bearer_token() == "BAD"
    assert fake_drive_changes._sa_access_token.call_count == 1

    assert drive_auth.note_drive_unauthorized(DRIVE_MEDIA_URL, "Bearer BAD") is True

    assert drive_auth.get_drive_bearer_token() == "GOOD"
    assert fake_drive_changes._sa_access_token.call_count == 2



def test_invalidate_is_value_matched_not_email(fake_drive_changes):
    """按值失效：值对不上就不删，绝不误删有效缓存。"""
    assert drive_auth.get_drive_bearer_token() == "token-for-sa1@x"
    assert fake_drive_changes._sa_access_token.call_count == 1

    assert drive_auth.invalidate_drive_bearer_token("STALE") is False

    assert drive_auth.get_drive_bearer_token() == "token-for-sa1@x"
    assert fake_drive_changes._sa_access_token.call_count == 1


def test_note_ignores_non_drive_url(fake_drive_changes):
    """非 Drive URL 的 401 不得触发失效。"""
    fake_drive_changes._sa_access_token.side_effect = iter(["BAD"])

    assert drive_auth.get_drive_bearer_token() == "BAD"
    assert fake_drive_changes._sa_access_token.call_count == 1

    assert drive_auth.note_drive_unauthorized("https://example.com/file", "Bearer BAD") is False

    assert drive_auth.get_drive_bearer_token() == "BAD"
    assert fake_drive_changes._sa_access_token.call_count == 1


def test_invalidate_never_raises(fake_drive_changes):
    """非法入参不得抛异常（relay 层容错路径）。"""
    assert drive_auth.invalidate_drive_bearer_token(None) is False
    assert drive_auth.invalidate_drive_bearer_token("") is False
    assert drive_auth.note_drive_unauthorized(None, None) is False
    assert drive_auth.note_drive_unauthorized("https://drive.google.com/x", "not-bearer") is False


def test_invalidate_clears_inner_cache(fake_drive_changes):
    """关键回归：单套缓存失效后，下次取 token 必须重新换取——
    不再有第二套缓存能把坏 token 原样吐回（收敛前 drive_changes
    内层 _token_cache 会架空外层失效，见旧版测试与证据脚本
    repro_inner_cache_defeats；现内层缓存已删，结构上亦无此可能）。"""
    try:
        from backend.emby_server import drive_changes
    except ImportError:  # 兼容以 backend 为根的扁平导入路径
        from emby_server import drive_changes

    assert not hasattr(drive_changes, "_token_cache")

    fake_drive_changes._sa_access_token.side_effect = lambda sa, min_remaining=60: "BAD"

    assert drive_auth.get_drive_bearer_token() == "BAD"

    assert drive_auth.invalidate_drive_bearer_token("BAD") is True
    assert "sa1@x" not in drive_auth._token_cache

    # 外层也被清：下次取 token 必须重新换取
    fake_drive_changes._sa_access_token.side_effect = lambda sa, min_remaining=60: "GOOD"
    assert drive_auth.get_drive_bearer_token() == "GOOD"
    assert fake_drive_changes._sa_access_token.call_count == 2

