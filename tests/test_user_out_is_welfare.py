"""回归：/auth/me 出参不再带 is_welfare（福利服役族 2026-11-14 删除）。

历史背景（2026-10-22）：出参曾透出 is_welfare，前端据此跳过非公益
用户的活力值请求。活力与公益资格均已下线、WebUser 的 is_welfare 列
已摘，这里锁「字段不再回来」：UserOut 无该字段、_user_out 序列化不带它。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")

import pytest

portal_mod = pytest.importorskip("backend.api.emby_portal")


def _stub_user(**overrides):
    base = dict(
        id=7,
        username="u7",
        email=None,
        emby_username=None,
        is_vip=False,
        is_active=True,
        is_staff=False,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_user_out_has_no_is_welfare_field():
    assert "is_welfare" not in portal_mod.UserOut.model_fields


def test_user_out_serialization_excludes_is_welfare():
    out = portal_mod._user_out(_stub_user(), db=None)
    assert "is_welfare" not in out.model_dump()
