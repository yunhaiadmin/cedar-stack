# -*- coding: utf-8 -*-
"""邮箱找回密码（2026-11-13 用户亲点）：POST /api/auth/password-reset/request|confirm。

口径：
- 防枚举：查无此人 / 无邮箱 / 已发送三种情况响应一字不差（200＋同一文案）；
- 令牌只存 SHA256，30 分钟过期、单次使用，重新申请作废旧令牌；
- 人机验证复用登录那道闸（captcha.guard，action=login 的保护开关）；
- 改密副作用与 change_password 共用 _apply_password_change：门户哈希 +
  Emby 播放密码同步 + token_version+1 + Emby 客户端令牌全吊销；
- 限流：申请 IP 5/小时、账号 3/小时；确认 IP 10/小时。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import hashlib
import re
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models, ratelimit
from backend.api import emby_portal
from backend.database import get_db
from backend.emby_server import models as emby_models
from backend.integrations import captcha
from backend.integrations import mail as integrations_mail
from backend.security import hash_password, verify_password

GENERIC = {
    "success": True,
    "message": "如果该账号存在且绑定了邮箱，重置邮件已发送，请查收",
}
SITE_URL = "https://www.aetrix.love"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # emby_server.models 与 backend.models 共用同一个 Base，create_all 已含 emby_api_tokens
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.SystemConfig(key="site_url", value=SITE_URL))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    # 限流器是进程级滑窗：不清的话本文件各用例会互相烧额度（与顺序耦合）。
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


class _FakeChannel:
    """假 EmailChannel：只记 _deliver 调用，不真发信。"""

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def _deliver(self, to_email, title, content):
        self.sent.append((to_email, title, content))
        return (True, None) if self.ok else (False, "SMTP down")


@pytest.fixture()
def mail_channel(monkeypatch):
    fake = _FakeChannel()
    monkeypatch.setattr(integrations_mail, "build_channel", lambda db: fake)
    return fake


def _client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.include_router(emby_portal.password_reset_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, *, email="someone@example.com", password="Old-pass-1"):
    u = models.WebUser(
        username="pr_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(password),
        emby_password=hash_password(password),
        email=email,
        is_active=True,
        token_version=3,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _ip():
    return {"X-Real-IP": "10.9." + ".".join(
        str(int(uuid.uuid4().hex[i:i + 2], 16) % 250 + 1) for i in (0, 2))}


def _request_reset(client, identifier, **kw):
    return client.post(
        "/api/auth/password-reset/request",
        json={"identifier": identifier, **kw},
        headers=_ip(),
    )


def _raw_from_mail(channel):
    assert len(channel.sent) == 1
    m = re.search(r"/login\?reset=([A-Za-z0-9_\-]+)", channel.sent[0][2])
    assert m, channel.sent[0][2]
    return m.group(1)


def test_model_registered():
    table = models.Base.metadata.tables["password_reset_tokens"]
    assert {"id", "user_id", "token_hash", "created_at", "expires_at",
            "used_at", "request_ip"} <= set(table.columns.keys())


def test_request_creates_hashed_token_and_sends_mail(db, mail_channel):
    user = _user(db)
    client = _client(db)
    resp = _request_reset(client, user.username)
    assert resp.status_code == 200
    assert resp.json() == GENERIC

    rows = db.query(models.PasswordResetToken).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    row = rows[0]
    raw = _raw_from_mail(mail_channel)
    # 库里只有哈希，原始令牌只出现在邮件链接里
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert row.token_hash != raw
    assert row.used_at is None
    assert row.expires_at - row.created_at == timedelta(minutes=30)
    to_email, title, content = mail_channel.sent[0]
    assert to_email == user.email
    assert title == "重置你的 Aetrix 密码"
    assert f"{SITE_URL}/login?reset={raw}" in content


def test_request_by_email_identifier(db, mail_channel):
    user = _user(db)
    client = _client(db)
    resp = _request_reset(client, user.email.upper())
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert db.query(models.PasswordResetToken).filter_by(user_id=user.id).count() == 1
    assert len(mail_channel.sent) == 1


def test_second_request_invalidates_first_token(db, mail_channel):
    user = _user(db)
    client = _client(db)
    assert _request_reset(client, user.username).status_code == 200
    assert _request_reset(client, user.username).status_code == 200
    rows = (db.query(models.PasswordResetToken).filter_by(user_id=user.id)
            .order_by(models.PasswordResetToken.id).all())
    assert len(rows) == 2
    assert rows[0].used_at is not None  # 旧令牌被作废
    assert rows[1].used_at is None
    assert len(mail_channel.sent) == 2


def test_unknown_and_no_email_are_indistinguishable(db, mail_channel):
    no_email_user = _user(db, email=None)
    client = _client(db)
    r_unknown = _request_reset(client, "no_such_user_xyz")
    r_no_email = _request_reset(client, no_email_user.username)
    assert r_unknown.status_code == r_no_email.status_code == 200
    assert r_unknown.json() == r_no_email.json() == GENERIC
    assert db.query(models.PasswordResetToken).count() == 0
    assert mail_channel.sent == []


def test_send_failure_still_generic(db, monkeypatch):
    user = _user(db)
    failing = _FakeChannel(ok=False)
    monkeypatch.setattr(integrations_mail, "build_channel", lambda db_: failing)
    client = _client(db)
    resp = _request_reset(client, user.username)
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert db.query(models.PasswordResetToken).filter_by(user_id=user.id).count() == 1


def test_confirm_happy_path_and_side_effects(db, mail_channel):
    user = _user(db)
    token_row = emby_models.EmbyApiToken(
        token="x" * 64, user_id=user.id, is_revoked=False)
    db.add(token_row)
    db.commit()

    client = _client(db)
    assert _request_reset(client, user.username).status_code == 200
    raw = _raw_from_mail(mail_channel)
    # 另一枚同用户的未用令牌：确认成功后必须一并作废
    other = models.PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(b"other-token").hexdigest(),
        created_at=datetime.now(),
        expires_at=datetime.now() + timedelta(minutes=30),
    )
    db.add(other)
    db.commit()

    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "New-pass-9"},
        headers=_ip(),
    )
    assert resp.status_code == 200

    db.refresh(user)
    db.refresh(other)
    used = db.query(models.PasswordResetToken).filter_by(
        token_hash=hashlib.sha256(raw.encode()).hexdigest()).one()
    # 与 change_password 同口径：门户密码 + Emby 播放密码都换新
    assert verify_password("New-pass-9", user.password_hash)
    assert not verify_password("Old-pass-1", user.password_hash)
    assert verify_password("New-pass-9", user.emby_password)
    # JWT 版本 +1（旧门户令牌作废）、Emby 客户端令牌全吊销
    assert user.token_version == 4
    db.refresh(token_row)
    assert token_row.is_revoked is True
    # 本令牌已用、同用户其他令牌作废
    assert used.used_at is not None
    assert other.used_at is not None

    # 旧密码登录失败、新密码成功（登录走的就是 verify_password 同一口径）
    r_old = client.post("/api/user/auth/login", json={
        "username": user.username, "password": "Old-pass-1"}, headers=_ip())
    assert r_old.status_code == 401
    r_new = client.post("/api/user/auth/login", json={
        "username": user.username, "password": "New-pass-9"}, headers=_ip())
    assert r_new.status_code == 200
    assert r_new.json().get("access_token")


@pytest.mark.parametrize("kind", ["nonexistent", "expired", "used"])
def test_confirm_invalid_tokens_uniform_400(db, kind):
    user = _user(db)
    client = _client(db)
    if kind == "nonexistent":
        raw = "never-issued-token"
    else:
        raw = "tok-" + kind
        db.add(models.PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            created_at=datetime.now() - timedelta(hours=2),
            expires_at=(datetime.now() - timedelta(minutes=1) if kind == "expired"
                        else datetime.now() + timedelta(minutes=30)),
            used_at=datetime.now() if kind == "used" else None,
        ))
        db.commit()
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "New-pass-9"},
        headers=_ip(),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "重置链接无效或已过期，请重新申请"
    db.refresh(user)
    assert verify_password("Old-pass-1", user.password_hash)


def test_request_ip_rate_limit(db, mail_channel):
    client = _client(db)
    headers = _ip()
    for _ in range(5):
        resp = client.post("/api/auth/password-reset/request",
                           json={"identifier": "nobody_here"}, headers=headers)
        assert resp.status_code == 200
    resp = client.post("/api/auth/password-reset/request",
                       json={"identifier": "nobody_here"}, headers=headers)
    assert resp.status_code == 429


def test_request_account_rate_limit(db, mail_channel):
    # 账号维度 3 次/小时：超限后仍回统一文案（不许用 429 泄露账号存在），
    # 但不再建新令牌、不再发信。每次请求换 IP，隔离 IP 维度限流的干扰。
    user = _user(db)
    client = _client(db)
    for _ in range(3):
        resp = _request_reset(client, user.username)
        assert resp.status_code == 200
        assert resp.json() == GENERIC
    resp = _request_reset(client, user.username)
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert db.query(models.PasswordResetToken).filter_by(user_id=user.id).count() == 3
    assert len(mail_channel.sent) == 3


def test_confirm_ip_rate_limit(db):
    client = _client(db)
    headers = _ip()
    for _ in range(10):
        resp = client.post("/api/auth/password-reset/confirm", json={
            "token": "nope", "new_password": "New-pass-9"}, headers=headers)
        assert resp.status_code == 400
    resp = client.post("/api/auth/password-reset/confirm", json={
        "token": "nope", "new_password": "New-pass-9"}, headers=headers)
    assert resp.status_code == 429


def test_captcha_required_when_login_protection_on(db, monkeypatch, mail_channel):
    for k, v in {
        "captcha_provider": "turnstile",
        "captcha_site_key": "site-key",
        "captcha_secret_key": "secret-key",
    }.items():
        db.add(models.SystemConfig(key=k, value=v))
    db.commit()

    def fake_verify(db_, cfg, token, ip=""):
        if token == "good-token":
            return True, ""
        if not (token or "").strip():
            return False, "请完成人机验证"
        return False, "人机验证失败（invalid-input-response）"

    monkeypatch.setattr(captcha, "_verify", fake_verify)
    user = _user(db)
    client = _client(db)

    resp = _request_reset(client, user.username)
    assert resp.status_code == 400  # 缺令牌被拒（与登录同口径）
    assert db.query(models.PasswordResetToken).count() == 0

    resp = _request_reset(client, user.username, captcha_token="good-token")
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert db.query(models.PasswordResetToken).filter_by(user_id=user.id).count() == 1
