# -*- coding: utf-8 -*-
"""external_failure_counts 熔断计数（PanSave 借鉴落地 B 线）回归。

锁定四件事：
1. 归一化键口径：query/fragment 整体丢弃（带签名的临时链接恒同
   键）、userinfo 丢弃、默认端口等价、host 小写、尾斜杠归一。
2. 计数行为：连续失败达阈值（默认 5）置 paused_until；成功清零；
   暂停到期即半开（再失败立即重新暂停）；scope 之间分账。
3. 非阻断：计数读写异常一律吞掉，绝不外泄到主流程。
4. 消费方接线已随上游聚合整族删除，本文件只守计数本体（1–3）。

DB 口径：计数单测用注入 session（本地内存 sqlite，只建本表），
不碰全局 engine/SessionLocal；接线单测用内存假熔断替换模块函数。
"""

from __future__ import annotations

import time
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.emby_server import external_failure_counts as efc


# ---------------------------------------------------------------------------
# 归一化键口径
# ---------------------------------------------------------------------------

def test_normalize_drops_signed_query_variants():
    a = efc.normalize_target_key(
        "https://cms.example.com/api/provide?ac=detail&ids=1"
        "&token=AAA&sign=S1&expires=1700000000")
    b = efc.normalize_target_key(
        "https://cms.example.com/api/provide?ac=list&pg=9"
        "&token=BBB&sign=S2&expires=1900000000")
    assert a == b == "https://cms.example.com/api/provide"


def test_normalize_drops_fragment_and_userinfo():
    url = "https://" + "user" + ":" + "pw" + "@cms.example.com/x#frag"
    key = efc.normalize_target_key(url)
    assert key == "https://cms.example.com/x"
    assert "pw" not in key and "@" not in key


def test_normalize_default_port_equivalence():
    assert efc.normalize_target_key("https://h.example.com:443/a") == \
        efc.normalize_target_key("https://h.example.com/a")
    assert efc.normalize_target_key("http://h.example.com:80/a") == \
        efc.normalize_target_key("http://h.example.com/a")
    assert efc.normalize_target_key("https://h.example.com:8443/a") == \
        "https://h.example.com:8443/a"


def test_normalize_host_case_and_trailing_slash():
    assert efc.normalize_target_key("https://EXAMPLE.com/api/") == \
        "https://example.com/api"
    assert efc.normalize_target_key("https://example.com") == \
        efc.normalize_target_key("https://example.com/")
    assert efc.normalize_target_key("https://example.com/a") != \
        efc.normalize_target_key("https://example.com/b")


# ---------------------------------------------------------------------------
# 计数行为（注入 session，本地内存库）
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    efc.ExternalFailureCount.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _fail(session, scope, key, n, now):
    for i in range(n):
        efc.record_failure(scope, key, now=now + i, session=session)


def test_pause_after_threshold_default_five(db_session):
    key = "https://dead.example.com"
    _fail(db_session, "cms_fetch", key, 4, now=1000.0)
    assert efc.is_paused("cms_fetch", key, now=1010.0,
                         session=db_session) is False
    _fail(db_session, "cms_fetch", key, 1, now=1004.0)
    assert efc.is_paused("cms_fetch", key, now=1010.0,
                         session=db_session) is True
    state = efc.pause_states_for_key(key, now=1010.0, session=db_session)
    assert state["paused"] is True
    assert state["scopes"]["cms_fetch"]["fail_count"] == 5
    assert state["scopes"]["cms_fetch"]["paused_until"] == \
        pytest.approx(1004.0 + efc.pause_seconds())


def test_pause_expires_then_half_open_refailure_repauses(db_session):
    key = "https://dead.example.com"
    _fail(db_session, "cms_fetch", key, 5, now=1000.0)
    until = efc.pause_states_for_key(
        key, session=db_session)["scopes"]["cms_fetch"]["paused_until"]
    assert efc.is_paused("cms_fetch", key, now=until - 1,
                         session=db_session) is True
    assert efc.is_paused("cms_fetch", key, now=until + 1,
                         session=db_session) is False
    # 半开试探再失败：计数已在阈值之上，立即重新暂停。
    efc.record_failure("cms_fetch", key, now=until + 2, session=db_session)
    assert efc.is_paused("cms_fetch", key, now=until + 3,
                         session=db_session) is True


def test_success_clears_count_and_pause(db_session):
    key = "https://flaky.example.com"
    _fail(db_session, "upstream_fetch", key, 5, now=1000.0)
    assert efc.is_paused("upstream_fetch", key, now=1001.0,
                         session=db_session) is True
    efc.record_success("upstream_fetch", key, session=db_session)
    assert efc.is_paused("upstream_fetch", key, now=1001.0,
                         session=db_session) is False
    state = efc.pause_states_for_key(key, session=db_session)
    assert state["scopes"]["upstream_fetch"]["fail_count"] == 0
    # 清零后重新计数：4 次失败仍不暂停。
    _fail(db_session, "upstream_fetch", key, 4, now=2000.0)
    assert efc.is_paused("upstream_fetch", key, now=2001.0,
                         session=db_session) is False


def test_scopes_are_independent(db_session):
    key = "https://dead.example.com"
    _fail(db_session, "cms_fetch", key, 5, now=1000.0)
    assert efc.is_paused("cms_fetch", key, now=1001.0,
                         session=db_session) is True
    assert efc.is_paused("upstream_probe", key, now=1001.0,
                         session=db_session) is False


def test_threshold_and_pause_seconds_configurable(db_session, monkeypatch):
    monkeypatch.setenv("EXTERNAL_FAILURE_PAUSE_THRESHOLD", "2")
    monkeypatch.setenv("EXTERNAL_FAILURE_PAUSE_SECONDS", "60")
    key = "https://dead.example.com"
    _fail(db_session, "cms_fetch", key, 2, now=1000.0)
    state = efc.pause_states_for_key(key, now=1001.0, session=db_session)
    assert state["paused"] is True
    # 最后一击在 now=1001（两次失败为 1000/1001），暂停 60 秒。
    assert state["paused_until"] == pytest.approx(1061.0)


class _BoomSession:
    """任何操作都抛错的坏 session：计数层必须吞掉、不许外泄。"""

    def query(self, *args, **kwargs):
        raise RuntimeError("db down")

    def commit(self):
        raise RuntimeError("db down")

    def rollback(self):
        raise RuntimeError("db down")


def test_public_api_accepts_now_kwarg(db_session):
    """公开函数的 now kwarg 口径必须与调用方一致（防签名漂移被
    调用方 try 吞掉、成功永远清不了零）。"""
    key = "https://api.example.com"
    efc.record_failure("cms_fetch", key, now=1000.0, session=db_session)
    assert efc.is_paused("cms_fetch", key, now=1000.0,
                         session=db_session) is False
    efc.record_success("cms_fetch", key, now=1001.0, session=db_session)
    state = efc.pause_states_for_key(key, now=1002.0, session=db_session)
    assert state["scopes"]["cms_fetch"]["fail_count"] == 0


def test_broken_session_never_raises():
    boom = _BoomSession()
    assert efc.is_paused("cms_fetch", "https://x.example.com",
                         session=boom) is False
    efc.record_failure("cms_fetch", "https://x.example.com", session=boom)
    efc.record_success("cms_fetch", "https://x.example.com", session=boom)
    state = efc.pause_states_for_key("https://x.example.com", session=boom)
    assert state["paused"] is False
