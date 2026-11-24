"""L-022 回归：注册成功不得清零限流计数，成功注册另设独立桶封顶。

漏洞（审计实锤）：register 成功后调 reset_rate_limit(rl_key) 把该 IP 的
尝试桶整桶清零。限流本体默认 5 次/小时/IP，于是攻击者可以
「失败几次 → 成功一次 → 计数清零」无限循环，单 IP 批量建号；
新号自带求片额度与邀请身份，可与返利链组合刷号。

修复口径：
- 成功不再清零尝试桶；失败尝试口径保持不变（同一配置键、同一桶）。
- 成功另设独立桶 register-success:{ip}，入口 peek 预检封顶、
  成功后记一笔；上限/窗口走 SystemConfig 可配，默认 3 次/3600 秒。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-register-l022-key-32chars-min!!")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import models
from backend.api import emby_portal
from backend.ratelimit import _limiter


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _scope(ip):
    return {"type": "http", "method": "POST", "path": "/",
            "headers": [], "client": (ip, 1234)}


def _register(db, ip, username):
    return emby_portal.register(
        Request(_scope(ip)),
        emby_portal.RegisterRequest(username=username, password="<redacted>"),
        db,
    )


def _set_config(db, key, value):
    db.add(models.SystemConfig(key=key, value=value))
    db.commit()


def _reset_buckets(ip):
    _limiter.reset(f"register:{ip}")
    _limiter.reset(f"register-success:{ip}")


# ---------- 成功桶配置读取 ----------

def test_get_register_success_ratelimit_defaults(db):
    """无配置时回退默认：成功 3 次 / 3600 秒。"""
    from backend.api.emby_portal import _get_register_success_ratelimit

    assert _get_register_success_ratelimit(db) == (3, 3600)


def test_get_register_success_ratelimit_custom(db):
    _set_config(db, "register_success_ratelimit_max", "7")
    _set_config(db, "register_success_ratelimit_window", "600")
    from backend.api.emby_portal import _get_register_success_ratelimit

    assert _get_register_success_ratelimit(db) == (7, 600)


def test_get_register_success_ratelimit_bad_values_fallback(db):
    """脏数据容错：转不动/非正数回退默认，与尝试桶读法同口径。"""
    _set_config(db, "register_success_ratelimit_max", "abc")
    _set_config(db, "register_success_ratelimit_window", "0")
    from backend.api.emby_portal import _get_register_success_ratelimit

    assert _get_register_success_ratelimit(db) == (3, 3600)


# ---------- 限流器 peek 语义 ----------

def test_peek_rate_limit_does_not_record():
    """peek 只查不记：反复 peek 不消耗额度；check 记满后 peek 报满。"""
    from backend.ratelimit import check_rate_limit, peek_rate_limit

    key = "register-success:peek-unit-test"
    _limiter.reset(key)
    for _ in range(5):
        allowed, _ = peek_rate_limit(key, 1, 60)
        assert allowed is True
    allowed, _ = check_rate_limit(key, 1, 60)
    assert allowed is True
    allowed, retry = peek_rate_limit(key, 1, 60)
    assert allowed is False
    assert retry >= 1
    _limiter.reset(key)


# ---------- 端点行为 ----------

def test_success_bucket_caps_successful_registrations(db):
    """连续成功注册达到成功桶上限后被 429（尝试桶放大、不做绑定约束）。"""
    ip = "198.51.100.11"
    _reset_buckets(ip)
    _set_config(db, "registration_mode", "open")
    _set_config(db, "register_ratelimit_max", "50")
    _set_config(db, "register_success_ratelimit_max", "2")

    resp = _register(db, ip, "l022_cap_user1")
    assert resp.user.username == "l022_cap_user1"
    resp = _register(db, ip, "l022_cap_user2")
    assert resp.user.username == "l022_cap_user2"

    with pytest.raises(HTTPException) as exc:
        _register(db, ip, "l022_cap_user3")
    assert exc.value.status_code == 429
    _reset_buckets(ip)


def test_success_does_not_reset_attempt_bucket(db):
    """核心漏洞回归：失败计数不被随后的成功清零。

    默认尝试桶 5 次/小时：4 次失败（撞名 409）＋1 次成功后，桶已满，
    第 6 次尝试应 429。旧实现成功时整桶清零，第 6 次会放行。
    同时断言前 4 次失败仍是 409 而非 429（失败口径不回归）。
    """
    ip = "198.51.100.12"
    _reset_buckets(ip)
    _set_config(db, "registration_mode", "open")
    db.add(models.WebUser(username="l022_taken", password_hash="x", is_active=True))
    db.commit()

    for _ in range(4):
        with pytest.raises(HTTPException) as exc:
            _register(db, ip, "l022_taken")
        assert exc.value.status_code == 409

    resp = _register(db, ip, "l022_mixed_user")
    assert resp.user.username == "l022_mixed_user"

    with pytest.raises(HTTPException) as exc:
        _register(db, ip, "l022_after_full")
    assert exc.value.status_code == 429
    _reset_buckets(ip)


def test_normal_single_registration_unaffected(db):
    """正常单次注册不受影响：首注成功、同名重注仍是 409（不是 429）。"""
    ip = "198.51.100.13"
    _reset_buckets(ip)
    _set_config(db, "registration_mode", "open")

    resp = _register(db, ip, "l022_normal_user")
    assert resp.user.username == "l022_normal_user"

    with pytest.raises(HTTPException) as exc:
        _register(db, ip, "l022_normal_user")
    assert exc.value.status_code == 409
    _reset_buckets(ip)


def test_success_bucket_is_per_ip(db):
    """成功桶按 IP 隔离：一个 IP 注满不影响另一个 IP 正常注册。"""
    ip_a, ip_b = "198.51.100.14", "198.51.100.15"
    _reset_buckets(ip_a)
    _reset_buckets(ip_b)
    _set_config(db, "registration_mode", "open")
    _set_config(db, "register_ratelimit_max", "50")
    _set_config(db, "register_success_ratelimit_max", "1")

    resp = _register(db, ip_a, "l022_ip_a_user")
    assert resp.user.username == "l022_ip_a_user"
    with pytest.raises(HTTPException) as exc:
        _register(db, ip_a, "l022_ip_a_user2")
    assert exc.value.status_code == 429

    resp = _register(db, ip_b, "l022_ip_b_user")
    assert resp.user.username == "l022_ip_b_user"
    _reset_buckets(ip_a)
    _reset_buckets(ip_b)
