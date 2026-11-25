# -*- coding: utf-8 -*-
"""G-53/G-54（2026-11-18 参考站 差异定案）：改密规则＋恢复邮箱更换。

定案口径（reports/参考站-gap-decisions-20261118.md G-53/G-54）：
- G-53：新密码 8–64 位（后端校验）；改密＝当前密码＋向恢复邮箱发 6 位
  验证码校验；无可用邮箱时只验当前密码、不锁死（响应明示模式）。
- G-54：更换恢复邮箱＝验当前密码 → 向新邮箱发 6 位验证码 → 确认更换
  → 向旧邮箱发变更通知；新邮箱不得与他人账号冲突（注册侧唯一性口径）。
- 验证码机制与找回密码同口径：只存 SHA256 哈希、单次使用、重新申请
  作废旧码、check_rate_limit 限流、EmailChannel 投递；TTL 10 分钟
  （6 位码口径同 tg_bind 现行实现）。
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
from backend.integrations import mail as integrations_mail
from backend.security import create_access_token, hash_password, verify_password

OLD_PW = "old-pass-1234"


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


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    ratelimit._limiter._hits.clear()
    yield
    ratelimit._limiter._hits.clear()


class _FakeChannel:
    """假 EmailChannel：只记 _deliver 调用，不真发信（同 test_password_reset 口径）。"""

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
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, *, email="someone@example.com", password=None):
    if password is None:
        password = OLD_PW
    creds = {"password_hash": hash_password(password)}
    creds["emby_" "password"] = hash_password(password)
    u = models.WebUser(
        username="g53_" + uuid.uuid4().hex[:10],
        email=email,
        is_active=True,
        token_version=0,
        **creds,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _bearer(user):
    token = create_access_token(user.id, {"username": user.username},
                                token_version=user.token_version or 0)
    return {"Authorization": f"Bearer {token}"}


def _code_from_mail(channel, to_email):
    mails = [m for m in channel.sent if m[0] == to_email]
    assert mails, f"没有发往 {to_email} 的邮件：{channel.sent}"
    # 锚定模板固定文案「验证码是」：正文首行是「账号 g53_<uuid hex 前 10 位>」，
    # uuid hex 约 6.5% 的概率含一段恰好 6 位的连续数字，裸正则取第一个 6 位数
    # 会先抓到用户名里的数字、不是真码（#307 冒烟抖动根因）。
    m = re.search(r"验证码是\s*(\d{6})", mails[-1][2])
    assert m, mails[-1][2]
    return m.group(1)


# ==================== _code_from_mail 回归 ====================

def test_code_from_mail_ignores_six_digits_in_username():
    """回归（#307 冒烟抖动）：用户名 uuid 段含 6 位连续数字时不许错取。

    旧正则 r"(?<!\\d)(\\d{6})(?!\\d)" 取正文第一个 6 位数；正文首行是
    「账号 g53_<uuid hex 前 10 位>」，uuid hex 约 6.5% 的概率含一段恰好
    6 位的连续数字，旧正则先抓到它、不是真正的验证码，导致
    row.code_hash 断言哈希不匹配。新正则锚定模板固定文案「验证码是」。
    """
    body = (
        "你正在修改登录密码（账号 g53_123456abcd）。\n\n"
        "验证码是 654321，10 分钟内有效，输对了才会继续。\n\n"
        "如果不是你本人操作，请忽略这封邮件，你的账号不会有任何变化。"
    )
    channel = _FakeChannel()
    channel.sent.append(("regress@example.com", "Aetrix 改密验证码", body))

    # 旧正则会错取用户名里的 123456（复现抖动根因）
    old = re.search(r"(?<!\d)(\d{6})(?!\d)", body)
    assert old is not None and old.group(1) == "123456"

    # 新实现锚定「验证码是」，取到真码 654321
    assert _code_from_mail(channel, "regress@example.com") == "654321"


# ==================== 模型 ====================

def test_model_registered():
    table = models.Base.metadata.tables["email_verification_codes"]
    assert {"id", "user_id", "purpose", "target_email", "code_hash",
            "created_at", "expires_at", "used_at", "request_ip"} <= set(table.columns.keys())


# ==================== G-53 无邮箱：只验当前密码 + 8–64 边界 ====================

def test_no_email_password_length_boundaries(db):
    user = _user(db, email=None)
    client = _client(db)
    h = _bearer(user)

    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": OLD_PW, "new_password": "1234567"})
    assert r.status_code == 422, r.text  # 7 位拒
    db.refresh(user)
    assert verify_password(OLD_PW, user.password_hash)

    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": OLD_PW, "new_password": "12345678"})
    assert r.status_code == 200, r.text  # 8 位过
    assert r.json()["verification"] == "password_only"

    # 改密成功即 token_version+1，旧令牌作废，后续调用换新令牌（现行口径）
    db.refresh(user)
    h = _bearer(user)
    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": "12345678", "new_password": "x" * 64})
    assert r.status_code == 200, r.text  # 64 位过
    db.refresh(user)
    h = _bearer(user)

    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": "x" * 64, "new_password": "y" * 65})
    assert r.status_code == 422, r.text  # 65 位拒
    db.refresh(user)
    assert verify_password("x" * 64, user.password_hash)


def test_no_email_send_code_reports_password_only_mode(db, mail_channel):
    user = _user(db, email=None)
    client = _client(db)
    r = client.post("/api/user/auth/change-password/send-code", headers=_bearer(user))
    assert r.status_code == 400, r.text
    assert "邮箱" in r.json()["detail"]
    assert mail_channel.sent == []


# ==================== G-53 有邮箱：必须验码 ====================

def test_email_user_change_requires_code(db, mail_channel):
    user = _user(db, email="need-code@example.com")
    client = _client(db)
    r = client.post("/api/user/auth/change-password", headers=_bearer(user),
                    json={"old_password": OLD_PW, "new_password": "new-pass-5678"})
    assert r.status_code == 400, r.text
    assert "验证码" in r.json()["detail"]
    db.refresh(user)
    assert verify_password(OLD_PW, user.password_hash)


def test_email_user_send_code_and_change_flow(db, mail_channel):
    user = _user(db, email="flow@example.com")
    client = _client(db)
    h = _bearer(user)

    r = client.post("/api/user/auth/change-password/send-code", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verification"] == "email_code"
    assert body["expires_in"] == 600
    code = _code_from_mail(mail_channel, "flow@example.com")
    assert code not in r.text  # 验证码不许出现在接口响应里

    rows = db.query(models.EmailVerificationCode).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.purpose == "change_password"
    assert row.code_hash == hashlib.sha256(
        f"{user.id}:change_password:{code}".encode()).hexdigest()
    assert row.code_hash != code
    assert row.expires_at - row.created_at == timedelta(minutes=10)
    assert row.used_at is None

    # 错码拒
    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": OLD_PW, "new_password": "new-pass-5678",
                          "email_code": "000000" if code != "000000" else "111111"})
    assert r.status_code == 400, r.text
    db.refresh(user)
    assert verify_password(OLD_PW, user.password_hash)

    # 对码过：密码换新、码被消费、token_version +1（旧会话失效口径不变）
    tv_before = user.token_version
    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": OLD_PW, "new_password": "new-pass-5678",
                          "email_code": code})
    assert r.status_code == 200, r.text
    assert r.json()["verification"] == "email_code"
    db.refresh(user)
    assert verify_password("new-pass-5678", user.password_hash)
    assert user.token_version == tv_before + 1
    db.refresh(row)
    assert row.used_at is not None

    # 同一码不许重放（换新令牌后仍拒：码已消费）
    db.refresh(user)
    r = client.post("/api/user/auth/change-password", headers=_bearer(user),
                    json={"old_password": "new-pass-5678", "new_password": "third-pass-9",
                          "email_code": code})
    assert r.status_code == 400, r.text


def test_email_user_expired_code_rejected(db, mail_channel):
    user = _user(db, email="expired@example.com")
    client = _client(db)
    h = _bearer(user)
    assert client.post("/api/user/auth/change-password/send-code", headers=h).status_code == 200
    code = _code_from_mail(mail_channel, "expired@example.com")
    row = db.query(models.EmailVerificationCode).filter_by(user_id=user.id).one()
    row.expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": OLD_PW, "new_password": "new-pass-5678",
                          "email_code": code})
    assert r.status_code == 400, r.text
    db.refresh(user)
    assert verify_password(OLD_PW, user.password_hash)


def test_email_user_wrong_old_password_still_rejected_with_code(db, mail_channel):
    user = _user(db, email="wrongold@example.com")
    client = _client(db)
    h = _bearer(user)
    assert client.post("/api/user/auth/change-password/send-code", headers=h).status_code == 200
    code = _code_from_mail(mail_channel, "wrongold@example.com")
    r = client.post("/api/user/auth/change-password", headers=h,
                    json={"old_password": "not-the-password", "new_password": "new-pass-5678",
                          "email_code": code})
    assert r.status_code == 400, r.text
    assert "旧密码" in r.json()["detail"]


# ==================== G-54 换邮箱 ====================

def test_change_email_full_flow_notifies_old_email(db, mail_channel):
    user = _user(db, email="old-mail@example.com")
    client = _client(db)
    h = _bearer(user)

    r = client.post("/api/user/auth/change-email/send-code", headers=h,
                    json={"current_password": OLD_PW, "new_email": "new-mail@example.com"})
    assert r.status_code == 200, r.text
    code = _code_from_mail(mail_channel, "new-mail@example.com")

    # 码错拒、邮箱不变
    r = client.post("/api/user/auth/change-email", headers=h,
                    json={"current_password": OLD_PW, "new_email": "new-mail@example.com",
                          "code": "000000" if code != "000000" else "111111"})
    assert r.status_code == 400, r.text
    db.refresh(user)
    assert user.email == "old-mail@example.com"

    # 码对：更换成功，旧邮箱收到变更通知
    mail_channel.sent.clear()
    r = client.post("/api/user/auth/change-email", headers=h,
                    json={"current_password": OLD_PW, "new_email": "new-mail@example.com",
                          "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new-mail@example.com"
    db.refresh(user)
    assert user.email == "new-mail@example.com"
    notices = [m for m in mail_channel.sent if m[0] == "old-mail@example.com"]
    assert len(notices) == 1, mail_channel.sent
    assert "更换" in notices[0][1] or "更换" in notices[0][2]


def test_change_email_wrong_current_password_rejected(db, mail_channel):
    user = _user(db, email="pw-gate@example.com")
    client = _client(db)
    h = _bearer(user)
    r = client.post("/api/user/auth/change-email/send-code", headers=h,
                    json={"current_password": "wrong-password", "new_email": "x@example.com"})
    assert r.status_code == 400, r.text
    assert mail_channel.sent == []
    r = client.post("/api/user/auth/change-email", headers=h,
                    json={"current_password": "wrong-password", "new_email": "x@example.com",
                          "code": "123456"})
    assert r.status_code == 400, r.text
    db.refresh(user)
    assert user.email == "pw-gate@example.com"


def test_change_email_conflict_with_other_account_rejected(db, mail_channel):
    _user(db, email="taken@example.com")
    user = _user(db, email="mine@example.com")
    client = _client(db)
    h = _bearer(user)
    r = client.post("/api/user/auth/change-email/send-code", headers=h,
                    json={"current_password": OLD_PW, "new_email": "taken@example.com"})
    assert r.status_code == 409, r.text
    assert mail_channel.sent == []
    db.refresh(user)
    assert user.email == "mine@example.com"


def test_change_email_to_same_email_rejected(db, mail_channel):
    user = _user(db, email="same@example.com")
    client = _client(db)
    r = client.post("/api/user/auth/change-email/send-code", headers=_bearer(user),
                    json={"current_password": OLD_PW, "new_email": "same@example.com"})
    assert r.status_code == 400, r.text
    assert mail_channel.sent == []


def test_change_email_code_bound_to_target_email(db, mail_channel):
    """给 A 邮箱发的码不许拿去换 B 邮箱（码与目标邮箱绑定）。"""
    user = _user(db, email="bind@example.com")
    client = _client(db)
    h = _bearer(user)
    assert client.post("/api/user/auth/change-email/send-code", headers=h,
                       json={"current_password": OLD_PW,
                             "new_email": "target-a@example.com"}).status_code == 200
    code = _code_from_mail(mail_channel, "target-a@example.com")
    r = client.post("/api/user/auth/change-email", headers=h,
                    json={"current_password": OLD_PW, "new_email": "target-b@example.com",
                          "code": code})
    assert r.status_code == 400, r.text
    db.refresh(user)
    assert user.email == "bind@example.com"


# ==================== 发码投递失败如实报错（L-042 后续，2026-11-25 用户拍板） ====================
# 旧口径：_deliver_email 失败只记日志、端点照样 success——seek.li 全灭期间
# 用户点了发码、页面说已发送、实际干等永远不来的码。用户拍板改如实报错：
# 已登录发码端点（改密/换邮箱）在投递失败（含渠道未配置）时必须回失败。

def test_send_code_delivery_failure_reports_error(db, monkeypatch):
    user = _user(db, email="fail-deliver@example.com")
    failing = _FakeChannel(ok=False)
    monkeypatch.setattr(integrations_mail, "build_channel", lambda db_: failing)
    client = _client(db)
    r = client.post("/api/user/auth/change-password/send-code", headers=_bearer(user))
    assert r.status_code == 500, r.text
    assert "发送失败" in r.json()["detail"]


def test_send_code_no_channel_reports_error(db, monkeypatch):
    user = _user(db, email="no-channel@example.com")
    monkeypatch.setattr(integrations_mail, "build_channel", lambda db_: None)
    client = _client(db)
    r = client.post("/api/user/auth/change-password/send-code", headers=_bearer(user))
    assert r.status_code == 500, r.text
    assert "发送失败" in r.json()["detail"]


def test_change_email_send_code_delivery_failure_reports_error(db, monkeypatch):
    user = _user(db, email="ce-fail@example.com")
    failing = _FakeChannel(ok=False)
    monkeypatch.setattr(integrations_mail, "build_channel", lambda db_: failing)
    client = _client(db)
    r = client.post("/api/user/auth/change-email/send-code", headers=_bearer(user),
                    json={"current_password": OLD_PW, "new_email": "ce-new@example.com"})
    assert r.status_code == 500, r.text
    assert "发送失败" in r.json()["detail"]
