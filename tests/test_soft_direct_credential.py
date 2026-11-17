# -*- coding: utf-8 -*-
"""网页软解直连凭据端点（2026-11-17 方案改版）授权矩阵回归。

背景：本人 Drive 副本的网页软解改走浏览器直连谷歌（Drive API
alt=media + 本人短时令牌），字节不过服务器。凭据端点
``soft_direct_credential`` 是令牌唯一出口，红线是「只发请求者本人
的令牌」：池贡献者令牌、共享 SA 令牌、他人令牌绝不下发。

本文件钉住：
- 本人 usercopy 副本 → 发本人令牌（user 级口）+ 副本 fid + 到期；
- 本人 byos 绑定 → 发绑定级令牌 + 条目 fid（不走 user 级口）；
- 池副本 / 挂载等变体 → 409，令牌口零调用；
- 他人副本行 / 服主副本行（resolve 规则②回落）→ 409，零令牌；
- byos 绑定行归属他人 → 403，零令牌；
- 未绑定（绑定门禁 + 池门禁双拒）→ 403，零令牌；
- 直连开关关 → 409，零令牌；无变体 400；变体不一致 404；限流 429；
- 鉴权依赖 get_play_user：签名被篡改 → 403（端点与 video_stream
  共用同一依赖，横切只许一套）；
- drive_oauth 带到期令牌口与 get_token_for_user 同源：无绑定
  not_found；有绑定返回 (令牌, 到期)。

全部离线：门禁/选路/令牌全桩，harness 仿
tests/test_stream_resolve_fastpath.py。
"""
import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import backend.security as sec

sec.SECRET_KEY = "test-secret-key-for-soft-direct-0000"

from backend.emby_server import api, auth, play_sign
from backend.emby_server import drive_oauth as do
from backend.emby_server import playback_gate as _gate
from backend.emby_server import pool_gate as _pool_gate
from backend.emby_server import transfer_variant as _tv
from backend.integrations import store as _store
from backend import ratelimit as _rl

ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"
BYOS_FID = "1ByosOwnFileId00000000000000000"
TOKEN_EXP = 1999999999


def _run(coro):
    return asyncio.run(coro)


class _FakeQuery:
    def __init__(self, row=None):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def query(self, *args, **kwargs):
        return _FakeQuery(None)


def _copy_row(**over):
    row = {"id": 5899, "owner_scope": "user", "owner_ref": 11,
           "variant_drive_id": "userdrive:11",
           "variant_file_id": COPY_FID, "status": "ready"}
    row.update(over)
    return SimpleNamespace(**row)


def _item(**over):
    item = {"id": 598385, "guid": "g-uc-stream", "name": "测试片",
            "drive_file_id": ORIG_FID, "container": "mkv",
            "file_path": "mount://3/MoviePilot/电影/测试片.mkv"}
    item.update(over)
    return SimpleNamespace(**item)


def _request(query=b"variant=usercopy_relay"):
    scope = {"type": "http", "method": "GET",
             "path": "/emby/Videos/item1/soft-direct",
             "raw_path": b"/emby/Videos/item1/soft-direct",
             "query_string": query, "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _setup(monkeypatch, *, bound=True, direct_on=True,
           resolve_result=None, byos_row=None):
    """全链桩：门禁默认放行、resolve 默认回本人副本行、令牌口计数。"""
    item = _item()
    user = SimpleNamespace(id=11)
    counts = {"user_token": 0, "binding_token": 0, "resolve": 0}
    monkeypatch.setattr(api, "_require_visible_item",
                        lambda db, u, item_id: item)
    monkeypatch.setattr(api, "ensure_playback_allowed",
                        lambda db, u: None)
    monkeypatch.setattr(api, "_enforce_path_access",
                        lambda db, u, it: None)
    monkeypatch.setattr(api.play_sign, "check_referer",
                        lambda request, db: None)
    if bound:
        monkeypatch.setattr(_gate, "ensure_binding_playback_allowed",
                            lambda db, u, req=None: None)
    else:
        def _deny(*args, **kwargs):
            raise HTTPException(status_code=403, detail="未绑定")
        monkeypatch.setattr(_gate, "ensure_binding_playback_allowed", _deny)
        monkeypatch.setattr(
            _pool_gate, "ensure_playback_allowed_for_item", _deny)
    monkeypatch.setattr(_rl, "check_rate_limit",
                        lambda key, max_events, window: (True, 0))

    def fake_get_value(db, key, default="", **kwargs):
        if key == "web_softdecode_direct_enabled":
            return "true" if direct_on else "false"
        return default
    monkeypatch.setattr(_store, "get_value", fake_get_value)

    if resolve_result is None:
        resolve_result = (COPY_FID, _copy_row())

    def fake_resolve(db, it, u):
        counts["resolve"] += 1
        return resolve_result
    monkeypatch.setattr(_tv, "resolve_play_file_id", fake_resolve)

    def fake_user_token(db, user_id):
        counts["user_token"] += 1
        return f"TOKEN{user_id}", TOKEN_EXP
    monkeypatch.setattr(do, "get_user_drive_token_info", fake_user_token)

    def fake_binding_token(db, binding):
        counts["binding_token"] += 1
        return "TOKEN-BINDING", TOKEN_EXP
    monkeypatch.setattr(do, "get_binding_drive_token_info",
                        fake_binding_token)
    if byos_row is not None:
        monkeypatch.setattr(api, "_byos_binding_row",
                            lambda db, it: (byos_row[0], byos_row[1]))
    return item, user, counts


def _call(user, query=b"variant=usercopy_relay"):
    return _run(api.soft_direct_credential(
        "item1", _request(query), user, _FakeDB()))


# ---------- 发令牌：本人归属 ----------

def test_usercopy_own_copy_issues_own_token(monkeypatch):
    item, user, counts = _setup(monkeypatch)
    resp = _call(user)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["file_id"] == COPY_FID
    assert body["access_token"] == "TOKEN11"
    assert body["expires_at"] == TOKEN_EXP
    assert counts["user_token"] == 1 and counts["binding_token"] == 0
    assert resp.headers["cache-control"] == "no-store"


def test_byos_own_binding_issues_binding_token(monkeypatch):
    row = SimpleNamespace(id=7, user_id=11, provider="drive",
                          status="active")
    item, user, counts = _setup(monkeypatch, byos_row=(7, row))
    # byos 条目：fid 在条目自身（drive_file_id），路径是 byos://
    item.file_path = "byos://7/电影/测试片.mkv"
    item.drive_file_id = BYOS_FID
    resp = _call(user, b"variant=byos_drive_relay")
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["file_id"] == BYOS_FID
    assert body["access_token"] == "TOKEN-BINDING"
    # 钉住绑定行取令牌，绝不走「最早 active 绑定」的 user 级口
    assert counts["binding_token"] == 1 and counts["user_token"] == 0


# ---------- 不发令牌：非本人 / 非 Drive 来源 ----------

@pytest.mark.parametrize("variant", [
    b"variant=pool_copy", b"variant=mount_relay", b"variant=drive_relay",
    b"variant=drive_direct", b"variant=byos115_direct",
])
def test_non_own_variants_rejected_without_token(monkeypatch, variant):
    item, user, counts = _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _call(user, variant)
    assert ei.value.status_code == 409
    assert counts["user_token"] == 0 and counts["binding_token"] == 0


def test_other_users_copy_row_rejected(monkeypatch):
    """resolve 回了别人的副本行（owner_ref=12）→ 不是本人副本，拒。"""
    row = _copy_row(owner_ref=12, variant_drive_id="userdrive:12")
    item, user, counts = _setup(
        monkeypatch, resolve_result=(COPY_FID, row))
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 409
    assert counts["user_token"] == 0


def test_source_owner_copy_row_rejected(monkeypatch):
    """resolve 规则②回落服主副本（owner_scope=source）→ 拒发令牌。"""
    row = _copy_row(owner_scope="source", owner_ref=1,
                    variant_drive_id="userdrive:1")
    item, user, counts = _setup(
        monkeypatch, resolve_result=(COPY_FID, row))
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 409
    assert counts["user_token"] == 0


def test_no_copy_row_rejected(monkeypatch):
    item, user, counts = _setup(
        monkeypatch, resolve_result=(ORIG_FID, None))
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 409
    assert counts["user_token"] == 0


def test_byos_binding_of_other_user_forbidden(monkeypatch):
    row = SimpleNamespace(id=7, user_id=12, provider="drive",
                          status="active")
    item, user, counts = _setup(monkeypatch, byos_row=(7, row))
    with pytest.raises(HTTPException) as ei:
        _call(user, b"variant=byos_drive_relay")
    assert ei.value.status_code == 403
    assert counts["binding_token"] == 0 and counts["user_token"] == 0


# ---------- 门禁 / 开关 / 限流 ----------

def test_unbound_user_forbidden_by_gate(monkeypatch):
    item, user, counts = _setup(monkeypatch, bound=False)
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 403
    assert counts["user_token"] == 0 and counts["resolve"] == 0


def test_direct_switch_off_rejected(monkeypatch):
    item, user, counts = _setup(monkeypatch, direct_on=False)
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 409
    assert counts["user_token"] == 0 and counts["resolve"] == 0


def test_missing_variant_bad_request(monkeypatch):
    item, user, counts = _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _call(user, b"")
    assert ei.value.status_code == 400
    assert counts["user_token"] == 0


def test_variant_mismatch_not_found(monkeypatch):
    item, user, counts = _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _call(user, b"variant=usercopy_relay&MediaSourceId=g:drive_relay")
    assert ei.value.status_code == 404
    assert counts["user_token"] == 0


def test_rate_limited(monkeypatch):
    item, user, counts = _setup(monkeypatch)
    monkeypatch.setattr(_rl, "check_rate_limit",
                        lambda key, max_events, window: (False, 7))
    with pytest.raises(HTTPException) as ei:
        _call(user)
    assert ei.value.status_code == 429
    assert counts["user_token"] == 0 and counts["resolve"] == 0


# ---------- 鉴权依赖：验签失败拒（与 video_stream 共用 get_play_user） ----------

def test_tampered_play_sign_rejected_by_dependency(monkeypatch):
    monkeypatch.setattr(play_sign, "hotlink_enabled", lambda db=None: True)
    exp = int(time.time()) + 3600
    good = play_sign.make_play_sign(11, "item1", exp)
    bad = good[:-1] + ("A" if good[-1] != "A" else "B")
    scope = {"type": "http", "method": "GET",
             "path": "/emby/Videos/item1/soft-direct",
             "raw_path": b"/emby/Videos/item1/soft-direct",
             "query_string": f"uid=11&exp={exp}&sign={bad}".encode(),
             "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": "",
             "path_params": {"item_id": "item1"}}
    with pytest.raises(HTTPException) as ei:
        auth.get_play_user(Request(scope), _FakeDB(), None)
    assert ei.value.status_code == 403


# ---------- drive_oauth 带到期令牌口 ----------

def test_user_token_info_no_binding_not_found(monkeypatch):
    from backend.emby_server import user_bindings as ub
    monkeypatch.setattr(ub, "get_active_binding",
                        lambda db, uid, provider: None)
    with pytest.raises(do.DriveOAuthError) as ei:
        do.get_user_drive_token_info(_FakeDB(), 11)
    assert ei.value.kind == "not_found"


def test_user_token_info_returns_token_and_expiry(monkeypatch):
    from backend.emby_server import user_bindings as ub
    binding = SimpleNamespace(id=3, user_id=11)
    monkeypatch.setattr(ub, "get_active_binding",
                        lambda db, uid, provider: binding)
    monkeypatch.setattr(do, "get_binding_drive_access_token",
                        lambda db, b, **kw: "TOK-ABC")
    monkeypatch.setattr(do, "_drive_credential_of",
                        lambda db, b: (None, {"access_token": "TOK-ABC",
                                              "expires_at": TOKEN_EXP}))
    token, expires = do.get_user_drive_token_info(_FakeDB(), 11)
    assert token == "TOK-ABC" and expires == TOKEN_EXP
