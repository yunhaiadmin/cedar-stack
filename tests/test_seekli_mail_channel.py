# -*- coding: utf-8 -*-
"""seek.li 发信通道（2026-11-24 用户提供开发者 API）：邮件在 SMTP 之外新增 seek.li 一路。

口径：
- 发信走 POST https://seek.li/openapi/v1/send，Authorization: Bearer sk_live_…，
  发件人由 mailboxId 指定（缺省 82449 = aetrix@seek.li，配置可改）；
- 收件人 to 必须是单个地址字符串（L-042 实测：to 传数组一律 400 BAD_REQUEST 参数错误，改字符串即 200 queued）；
- 每日 100 封硬上限：通知路径 send() 按 NotificationHistory 当日 sent 计数拦截，
  超限如实记 failed 并写明原因，不静默吞；
- API 错误（400 等）原样上报，错误文本里不许带 key；
- 配置走能力中心 mail 能力的新字段（email_provider / seekli_api_key /
  seekli_mailbox_id），key 回读只见掩码、提交掩码不修改；
- 找回密码经 integrations.mail.build_channel 构建渠道，切到 seek.li 后自动受益。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import json
import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import notifications
from backend.integrations import mail as integrations_mail
from backend.integrations import store as config_store
from backend.security import hash_password

FAKE_KEY = "sk_live_fake_test_key"
MAILBOX_ID = 82449


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    config_store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        config_store.invalidate()


def _seed(db, **kv):
    for key, value in kv.items():
        db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    config_store.invalidate()


def _seed_seekli(db, **overrides):
    values = {
        "email_enabled": "true",
        "email_provider": "seekli",
        "seekli_api_key": FAKE_KEY,
        "seekli_mailbox_id": str(MAILBOX_ID),
    }
    values.update(overrides)
    _seed(db, **values)


def _user(db, email="someone@example.com"):
    u = models.WebUser(
        username="sk_" + uuid.uuid4().hex[:10],
        password_hash=hash_password("Passw0rd!"),
        emby_password="Passw0rd!",
        email=email,
        is_active=True,
        token_version=1,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload if self._payload is not None else {}


class _FakeClient:
    """httpx.Client 替身（类级替换）：记录请求，按预设返回 / 抛错，绝不真出网。"""

    posts = []
    gets = []
    post_response = _FakeResponse(200, "")
    get_response = _FakeResponse(200, "", {"data": []})
    post_error = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        type(self).posts.append({"url": url, "json": json, "headers": headers})
        if type(self).post_error is not None:
            raise type(self).post_error
        return type(self).post_response

    def get(self, url, headers=None):
        type(self).gets.append({"url": url, "headers": headers})
        return type(self).get_response


@pytest.fixture()
def fake_http(monkeypatch):
    _FakeClient.posts = []
    _FakeClient.gets = []
    _FakeClient.post_response = _FakeResponse(200, "")
    _FakeClient.get_response = _FakeResponse(200, "", {"data": []})
    _FakeClient.post_error = None
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return _FakeClient


def _channel():
    return notifications.SeekLiMailChannel(api_key=FAKE_KEY, mailbox_id=MAILBOX_ID)


# ---------------- 投递层：请求拼装 / 成功 / 失败如实报 ----------------

def test_deliver_builds_request_and_succeeds(fake_http):
    ok, error = _channel()._deliver("user@example.com", "标题", "第一行\n第二行")
    assert ok is True and error is None
    assert len(fake_http.posts) == 1
    call = fake_http.posts[0]
    assert call["url"] == "https://seek.li/openapi/v1/send"
    assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    body = call["json"]
    assert body["mailboxId"] == MAILBOX_ID
    # L-042 回归：to 必须是单个地址字符串，数组会被 seek.li 以 400 拒绝
    assert isinstance(body["to"], str)
    assert body["to"] == "user@example.com"
    assert body["subject"] == "标题"
    assert body["bodyText"] == "第一行\n第二行"
    assert "第一行" in body["bodyHtml"]


def test_deliver_400_reports_error_without_key(fake_http):
    fake_http.post_response = _FakeResponse(400, "mailboxId invalid")
    ok, error = _channel()._deliver("user@example.com", "t", "c")
    assert ok is False
    assert "400" in error
    assert FAKE_KEY not in error


def test_deliver_network_error_reports(fake_http):
    fake_http.post_error = httpx.ConnectError("boom")
    ok, error = _channel()._deliver("user@example.com", "t", "c")
    assert ok is False
    assert "ConnectError" in error


# ---------------- 渠道构建：通道切换 / 缺省 / 缺 key ----------------

def test_build_channel_switches_to_seekli(db):
    _seed_seekli(db)
    channel = integrations_mail.build_channel(db)
    assert isinstance(channel, notifications.SeekLiMailChannel)
    assert channel.mailbox_id == MAILBOX_ID
    assert channel.enabled is True


def test_build_channel_seekli_mailbox_defaults_to_82449(db):
    _seed(db, email_enabled="true", email_provider="seekli",
          seekli_api_key=FAKE_KEY)
    channel = integrations_mail.build_channel(db)
    assert isinstance(channel, notifications.SeekLiMailChannel)
    assert channel.mailbox_id == 82449


def test_build_channel_seekli_missing_key_returns_none(db):
    _seed(db, email_enabled="true", email_provider="seekli",
          seekli_mailbox_id=str(MAILBOX_ID))
    assert integrations_mail.build_channel(db) is None


def test_build_channel_default_stays_smtp(db):
    _seed(db, email_enabled="true", email_smtp_host="smtp.example.com",
          email_smtp_user="noreply@example.com",
          email_smtp_password="smtp-secret")
    channel = integrations_mail.build_channel(db)
    assert isinstance(channel, notifications.EmailChannel)
    assert not isinstance(channel, notifications.SeekLiMailChannel)


def test_build_channel_disabled_returns_none(db):
    _seed_seekli(db, email_enabled="false")
    assert integrations_mail.build_channel(db) is None


def test_notification_service_loads_seekli_channel(db, monkeypatch):
    _seed_seekli(db)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: db)
    service = notifications.NotificationService()
    channel = service.channels.get("email")
    assert isinstance(channel, notifications.SeekLiMailChannel)
    assert channel.mailbox_id == MAILBOX_ID


# ---------------- 配置回读脱敏 ----------------

def test_capability_masks_seekli_key(db, monkeypatch):
    from backend import integrations

    monkeypatch.setattr(integrations_mail, "apply", lambda db_: None)
    _seed_seekli(db)
    card = integrations.get_capability(db, "mail")
    assert card["values"]["seekli_api_key"] == "******"
    assert FAKE_KEY not in json.dumps(card, ensure_ascii=False)
    assert card["item"]["configured"] is True
    assert card["item"]["enabled"] is True
    # 提交掩码 = 不修改已存的 key
    integrations.save_capability(db, "mail", {"seekli_api_key": "******"})
    assert integrations.raw_values(db, "mail")["seekli_api_key"] == FAKE_KEY


# ---------------- 每日 100 封上限 ----------------

def _seed_sent_history(db, count):
    for _ in range(count):
        db.add(models.NotificationHistory(
            notification_type="email", target="x@example.com",
            title="t", content="c", status="sent", sent_at=datetime.now()))
    db.commit()


def test_send_enforces_daily_limit(db, fake_http):
    user = _user(db)
    _seed_sent_history(db, notifications.SEEKLI_DAILY_LIMIT)
    ok = asyncio.run(_channel().send(user.id, "标题", "正文", db=db))
    assert ok is False
    assert fake_http.posts == []
    failed = db.query(models.NotificationHistory).filter_by(status="failed").all()
    assert failed and "100" in (failed[-1].error_message or "")


def test_send_under_limit_succeeds(db, fake_http):
    user = _user(db)
    ok = asyncio.run(_channel().send(user.id, "标题", "正文", db=db))
    assert ok is True
    assert len(fake_http.posts) == 1
    sent = db.query(models.NotificationHistory).filter_by(status="sent").all()
    assert len(sent) == 1 and sent[0].target == "someone@example.com"


# ---------------- 找回密码经同一渠道 ----------------

def test_password_reset_email_goes_through_seekli(db, fake_http):
    from backend.api import emby_portal

    _seed(db, site_url="https://www.aetrix.love")
    _seed_seekli(db)
    user = _user(db)
    emby_portal._send_reset_email(db, user, "raw-token-123")
    assert len(fake_http.posts) == 1
    body = fake_http.posts[0]["json"]
    assert body["to"] == "someone@example.com"
    assert "/login?reset=raw-token-123" in body["bodyText"]


# ---------------- 后台「测试」：验邮箱 / 真发一封 ----------------

def test_capability_test_verifies_mailbox(db, fake_http):
    _seed_seekli(db)
    fake_http.get_response = _FakeResponse(200, "", {"data": [
        {"id": MAILBOX_ID, "address": "aetrix@seek.li", "sendDisabled": False}]})
    result = integrations_mail.test(db, {})
    assert result["ok"] is True
    assert "aetrix@seek.li" in result["message"]
    assert fake_http.gets[0]["url"] == "https://seek.li/openapi/v1/mailboxes"
    assert fake_http.gets[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


def test_capability_test_sends_when_recipient_given(db, fake_http):
    _seed_seekli(db)
    result = integrations_mail.test(db, {"to": "boss@example.com"})
    assert result["ok"] is True
    assert fake_http.posts[0]["json"]["to"] == "boss@example.com"
