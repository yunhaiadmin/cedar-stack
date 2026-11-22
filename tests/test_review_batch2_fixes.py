"""审查修复回归（第二批）：签名头、转码缓存 LRU。

（lottery seed 门控两用例已随群抽奖整族于 2026-11-23 下线删除。）
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-for-review")

import json
import tempfile
import time
from datetime import datetime, timedelta

from backend.database import SessionLocal, init_db
from backend import models

init_db()


def test_verify_signed_headers_accepts_valid(monkeypatch):
    """合法 HMAC 签名头通过。"""
    import secrets as pysecrets
    from backend import node_auth
    monkeypatch.setenv("NODE_SHARED_SECRET", "ci-test-shared-secret")
    key = node_auth.node_shared_secret()
    assert key, "测试需要 SECRET_KEY"
    ts = str(int(time.time()))
    nonce = pysecrets.token_hex(16)
    sig = node_auth._sign(key, ts, nonce, "GET", "/api/admin/stream-nodes/bundle")
    headers = {"X-Panel-Ts": ts, "X-Panel-Nonce": nonce, "X-Panel-Sign": sig}
    assert node_auth.verify_signed_headers(
        headers, "GET", "/api/admin/stream-nodes/bundle") is True


def test_verify_signed_headers_rejects_static_key(monkeypatch):
    """静态 X-Panel-Key 必须被拒绝（bundle 不再接受）。"""
    from backend import node_auth
    monkeypatch.setenv("NODE_SHARED_SECRET", "ci-test-shared-secret")
    key = node_auth.node_shared_secret()
    assert node_auth.verify_signed_headers(
        {"X-Panel-Key": key}, "GET", "/api/admin/stream-nodes/bundle") is False


def test_verify_signed_headers_rejects_stale_ts(monkeypatch):
    """过期时间戳被拒绝。"""
    import secrets as pysecrets
    from backend import node_auth
    monkeypatch.setenv("NODE_SHARED_SECRET", "ci-test-shared-secret")
    key = node_auth.node_shared_secret()
    ts = str(int(time.time()) - 3600)  # 1 小时前
    nonce = pysecrets.token_hex(16)
    sig = node_auth._sign(key, ts, nonce, "GET", "/api/admin/stream-nodes/bundle")
    headers = {"X-Panel-Ts": ts, "X-Panel-Nonce": nonce, "X-Panel-Sign": sig}
    assert node_auth.verify_signed_headers(
        headers, "GET", "/api/admin/stream-nodes/bundle") is False
