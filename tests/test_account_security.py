# -*- coding: utf-8 -*-
"""账号安全三件套测试：TOTP / Passkey / 平台 API Key。

隔离：内存 SQLite + 最小 FastAPI 应用（auth_router + security_router +
platform_router），不碰开发库/生产库。限流滑窗在 fixture 里清零防用例互踩。

覆盖：
1. TOTP：setup 密钥只密封落库（明文不落库）；错码不能启用；对码启用；
   启用所用码不能重放登录；登录缺码/错码 401、对码 200；未启用用户登录不受影响；
   关闭须验密码（错 403、对则恢复无码登录）。
2. Passkey：软件认证器（真实 ES256 密钥对 + none 证明）完成注册往返；
   上限走配置（passkey_max_per_user=1 时第二枚被拒）；断言登录发令牌且
   sign_count 回写；login-factors 如实反映两因子状态；challenge 令牌
   篡改/串用途/串用户一律拒绝。
3. 平台 API Key：未绑任何强因子时创建 403（后端硬前置）；绑 TOTP 或
   Passkey 后可建；明文只回一次、列表不含明文与哈希、库里只存哈希；
   /api/platform/me 用 Key 可调通；错 Key/撤销/过期一律 401；
   有效期走配置 platform_api_key_validity_days。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import base64
import hashlib
import json
import struct
import time
import uuid
from datetime import datetime, timedelta

import cbor2
import pyotp
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import account_security as sec
from backend import models, ratelimit
from backend.api import account_security as sec_api
from backend.api import emby_portal
from backend.database import get_db
from backend.security import create_access_token, hash_password

ORIGIN = "https://example.com"
PASSWORD = "<redacted>"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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


def _client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.include_router(sec_api.security_router)
    app.include_router(sec_api.platform_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _user(db, password=PASSWORD):
    u = models.WebUser(
        username="sec_" + uuid.uuid4().hex[:10],
        password_hash=hash_password(password),
        email="sec@example.com",
        is_active=True,
        token_version=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth_headers(user) -> dict:
    token = create_access_token(user.id, {"username": user.username},
                                token_version=user.token_version or 0)
    return {"Authorization": f"Bearer {token}"}


def _enable_totp(client, db, user) -> str:
    """走完整启用流程，返回 TOTP 明文密钥（测试侧算码用）。"""
    r = client.post("/api/user/security/totp/setup", headers=_auth_headers(user))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    r = client.post("/api/user/security/totp/enable",
                    headers=_auth_headers(user), json={"code": code})
    assert r.status_code == 200, r.text
    return secret


def _fresh_totp_code(db, user, secret: str) -> str:
    """把防重放步数回滚到未用状态后取当前码（模拟时间已过、码未被用过）。"""
    row = sec.get_totp_row(db, user.id)
    row.last_used_counter = None
    db.commit()
    return pyotp.TOTP(secret).now()


# ==================== 软件认证器（真实 WebAuthn 往返） ====================

class SoftAuthenticator:
    """最小软件认证器：ES256 密钥对 + none 证明注册 + 断言签名。"""

    def __init__(self):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        pub = self.priv.public_key().public_numbers()
        self.cose = cbor2.dumps({
            1: 2, 3: -7, -1: 1,
            -2: pub.x.to_bytes(32, "big"), -3: pub.y.to_bytes(32, "big"),
        })
        self.cred_id = uuid.uuid4().bytes

    @staticmethod
    def _client_data(typ: str, challenge_b64: str) -> bytes:
        return json.dumps(
            {"type": typ, "challenge": challenge_b64,
             "origin": ORIGIN, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def registration_credential(self, challenge_b64: str) -> dict:
        rp_hash = hashlib.sha256(b"example.com").digest()
        flags = bytes([0x01 | 0x04 | 0x40])  # UP | UV | AT
        auth_data = (
            rp_hash + flags + struct.pack(">I", 0) + b"\x00" * 16
            + struct.pack(">H", len(self.cred_id)) + self.cred_id + self.cose
        )
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": _b64u(self.cred_id),
            "rawId": _b64u(self.cred_id),
            "response": {
                "attestationObject": _b64u(att_obj),
                "clientDataJSON": _b64u(self._client_data("webauthn.create", challenge_b64)),
                "transports": ["internal"],
            },
            "type": "public-key",
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def assertion_credential(self, challenge_b64: str, sign_count: int = 3) -> dict:
        rp_hash = hashlib.sha256(b"example.com").digest()
        auth_data = rp_hash + bytes([0x01 | 0x04]) + struct.pack(">I", sign_count)
        cd = self._client_data("webauthn.get", challenge_b64)
        sig = self.priv.sign(
            auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": _b64u(self.cred_id),
            "rawId": _b64u(self.cred_id),
            "response": {
                "authenticatorData": _b64u(auth_data),
                "clientDataJSON": _b64u(cd),
                "signature": _b64u(sig),
                "userHandle": _b64u(b"unused"),
            },
            "type": "public-key",
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }


def _register_passkey(client, user, device_name="测试钥匙"):
    """走完整注册两步，返回 (认证器, verify 响应 JSON)。"""
    auth = SoftAuthenticator()
    r = client.post("/api/user/security/passkeys/register/options",
                    headers={**_auth_headers(user), "Origin": ORIGIN})
    assert r.status_code == 200, r.text
    body = r.json()
    cred = auth.registration_credential(body["options"]["challenge"])
    r = client.post("/api/user/security/passkeys/register/verify",
                    headers={**_auth_headers(user), "Origin": ORIGIN},
                    json={"credential": cred,
                          "challenge_token": body["challenge_token"],
                          "device_name": device_name})
    assert r.status_code == 200, r.text
    return auth, r.json()


# ==================== TOTP ====================

def test_totp_setup_secret_never_stored_plaintext(db):
    client = _client(db)
    user = _user(db)
    r = client.post("/api/user/security/totp/setup", headers=_auth_headers(user))
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")
    row = sec.get_totp_row(db, user.id)
    assert row is not None and row.enabled is False
    assert secret not in (row.secret_sealed or "")
    assert sec.totp_enabled_for(db, user.id) is False


def test_totp_enable_wrong_code_rejected(db):
    client = _client(db)
    user = _user(db)
    client.post("/api/user/security/totp/setup", headers=_auth_headers(user))
    r = client.post("/api/user/security/totp/enable",
                    headers=_auth_headers(user), json={"code": "000000"})
    assert r.status_code == 400
    assert sec.totp_enabled_for(db, user.id) is False


def test_totp_login_gate_and_replay(db):
    client = _client(db)
    user = _user(db)
    secret = _enable_totp(client, db, user)

    # 缺码 → 401
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD})
    assert r.status_code == 401
    # 启用时用掉的那枚码原样重放 → 401（防重放）
    setup_code = pyotp.TOTP(secret).now()
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD,
                          "totp_code": setup_code})
    assert r.status_code == 401
    # 错码 → 401
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD,
                          "totp_code": "000000"})
    assert r.status_code == 401
    # 新鲜的对码 → 200 发令牌
    code = _fresh_totp_code(db, user, secret)
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD,
                          "totp_code": code})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


def test_totp_disabled_user_login_unaffected(db):
    client = _client(db)
    user = _user(db)
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_totp_disable_requires_password(db):
    client = _client(db)
    user = _user(db)
    _enable_totp(client, db, user)
    r = client.post("/api/user/security/totp/disable",
                    headers=_auth_headers(user), json={"password": "wrong-pass"})
    assert r.status_code == 403
    assert sec.totp_enabled_for(db, user.id) is True
    r = client.post("/api/user/security/totp/disable",
                    headers=_auth_headers(user), json={"password": PASSWORD})
    assert r.status_code == 200
    assert sec.totp_enabled_for(db, user.id) is False
    # 关闭后无码可登
    r = client.post("/api/user/auth/login",
                    json={"username": user.username, "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_totp_setup_blocked_when_already_enabled(db):
    client = _client(db)
    user = _user(db)
    _enable_totp(client, db, user)
    r = client.post("/api/user/security/totp/setup", headers=_auth_headers(user))
    assert r.status_code == 400


# ==================== Passkey ====================

def test_passkey_register_and_login_roundtrip(db):
    client = _client(db)
    user = _user(db)
    auth, created = _register_passkey(client, user)
    assert created["device_name"] == "测试钥匙"
    rows = sec.list_passkeys(db, user.id)
    assert len(rows) == 1 and rows[0].credential_id == _b64u(auth.cred_id)

    # login-factors 如实反映
    r = client.get("/api/user/auth/login-factors", params={"username": user.username})
    assert r.json() == {"totp_required": False, "passkey_available": True}

    # 断言登录
    r = client.post("/api/user/auth/passkey/login/options",
                    headers={"Origin": ORIGIN}, json={"username": user.username})
    assert r.status_code == 200, r.text
    body = r.json()
    cred = auth.assertion_credential(body["options"]["challenge"], sign_count=9)
    r = client.post("/api/user/auth/passkey/login/verify",
                    headers={"Origin": ORIGIN},
                    json={"username": user.username, "credential": cred,
                          "challenge_token": body["challenge_token"]})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    row = sec.list_passkeys(db, user.id)[0]
    assert row.sign_count == 9 and row.last_used_at is not None


def test_passkey_login_wrong_key_rejected(db):
    client = _client(db)
    user = _user(db)
    _register_passkey(client, user)
    stranger = SoftAuthenticator()  # 没注册过的钥匙
    # 先把陌生钥匙的 credential_id 冒充成已注册的 id，但签名密钥不同 → 验签必挂
    legit = sec.list_passkeys(db, user.id)[0]
    stranger.cred_id = sec._b64url_decode(legit.credential_id)
    r = client.post("/api/user/auth/passkey/login/options",
                    headers={"Origin": ORIGIN}, json={"username": user.username})
    body = r.json()
    cred = stranger.assertion_credential(body["options"]["challenge"])
    r = client.post("/api/user/auth/passkey/login/verify",
                    headers={"Origin": ORIGIN},
                    json={"username": user.username, "credential": cred,
                          "challenge_token": body["challenge_token"]})
    assert r.status_code == 401


def test_passkey_limit_from_config(db):
    db.add(models.SystemConfig(key="passkey_max_per_user", value="1"))
    db.commit()
    client = _client(db)
    user = _user(db)
    _register_passkey(client, user)
    r = client.post("/api/user/security/passkeys/register/options",
                    headers={**_auth_headers(user), "Origin": ORIGIN})
    assert r.status_code == 400


def test_passkey_delete(db):
    client = _client(db)
    user = _user(db)
    _auth, created = _register_passkey(client, user)
    r = client.delete(f"/api/user/security/passkeys/{created['id']}",
                      headers=_auth_headers(user))
    assert r.status_code == 200
    assert sec.list_passkeys(db, user.id) == []
    # 删后 login-factors 不再报可用
    r = client.get("/api/user/auth/login-factors", params={"username": user.username})
    assert r.json()["passkey_available"] is False


def test_challenge_token_binding():
    token = sec.issue_challenge_token("auth", 7, b"\x01\x02\x03")
    assert sec.parse_challenge_token(token, "auth", 7) == b"\x01\x02\x03"
    with pytest.raises(ValueError):
        sec.parse_challenge_token(token, "register", 7)  # 串用途
    with pytest.raises(ValueError):
        sec.parse_challenge_token(token, "auth", 8)  # 串用户
    with pytest.raises(ValueError):
        sec.parse_challenge_token(token[:-2] + "xx", "auth", 7)  # 篡改签名


def test_login_factors_unknown_user(db):
    client = _client(db)
    r = client.get("/api/user/auth/login-factors", params={"username": "nobody_here"})
    assert r.status_code == 200
    assert r.json() == {"totp_required": False, "passkey_available": False}


# ==================== 平台 API Key ====================

def test_api_key_gate_requires_strong_factor(db):
    client = _client(db)
    user = _user(db)
    r = client.post("/api/user/security/api-keys",
                    headers=_auth_headers(user), json={"name": "脚本"})
    assert r.status_code == 403
    assert sec.list_platform_api_keys(db, user.id) == []


def test_api_key_lifecycle_with_totp(db):
    client = _client(db)
    user = _user(db)
    _enable_totp(client, db, user)

    r = client.post("/api/user/security/api-keys",
                    headers=_auth_headers(user), json={"name": "我的脚本"})
    assert r.status_code == 200, r.text
    body = r.json()
    plaintext = body["plaintext"]
    assert plaintext.startswith("pak_")
    row = sec.list_platform_api_keys(db, user.id)[0]
    assert plaintext not in (row.key_hash or "")
    # 有效期默认 365 天
    assert timedelta(days=364) < (row.expires_at - row.created_at) < timedelta(days=366)

    # 列表不回明文也不回哈希
    r = client.get("/api/user/security/api-keys", headers=_auth_headers(user))
    item = r.json()["items"][0]
    assert "plaintext" not in item and "key_hash" not in item
    assert item["name"] == "我的脚本"

    # 平台只读接口调通
    r = client.get("/api/platform/me", headers={"X-API-Key": plaintext})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == user.username
    db.expire_all()
    assert sec.list_platform_api_keys(db, user.id)[0].last_used_at is not None

    # 错 Key → 401
    r = client.get("/api/platform/me", headers={"X-API-Key": "pak_wrongwrong"})
    assert r.status_code == 401

    # 撤销后 → 401
    r = client.delete(f"/api/user/security/api-keys/{row.id}",
                      headers=_auth_headers(user))
    assert r.status_code == 200
    r = client.get("/api/platform/me", headers={"X-API-Key": plaintext})
    assert r.status_code == 401


def test_api_key_gate_satisfied_by_passkey(db):
    client = _client(db)
    user = _user(db)
    _register_passkey(client, user)
    r = client.post("/api/user/security/api-keys",
                    headers=_auth_headers(user), json={"name": "pk"})
    assert r.status_code == 200, r.text


def test_api_key_expired_rejected(db):
    client = _client(db)
    user = _user(db)
    _enable_totp(client, db, user)
    r = client.post("/api/user/security/api-keys",
                    headers=_auth_headers(user), json={"name": "短命"})
    plaintext = r.json()["plaintext"]
    row = sec.list_platform_api_keys(db, user.id)[0]
    row.expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    r = client.get("/api/platform/me", headers={"X-API-Key": plaintext})
    assert r.status_code == 401


def test_api_key_validity_from_config(db):
    db.add(models.SystemConfig(key="platform_api_key_validity_days", value="30"))
    db.commit()
    client = _client(db)
    user = _user(db)
    _enable_totp(client, db, user)
    client.post("/api/user/security/api-keys",
                headers=_auth_headers(user), json={"name": "月抛"})
    row = sec.list_platform_api_keys(db, user.id)[0]
    assert timedelta(days=29) < (row.expires_at - row.created_at) < timedelta(days=31)


def test_security_overview_shape(db):
    client = _client(db)
    user = _user(db)
    r = client.get("/api/user/security/overview", headers=_auth_headers(user))
    assert r.status_code == 200
    body = r.json()
    assert body["totp"] == {"enabled": False}
    assert body["passkeys"]["max"] == 5 and body["passkeys"]["items"] == []
    assert body["api_keys"]["can_create"] is False
    _enable_totp(client, db, user)
    r = client.get("/api/user/security/overview", headers=_auth_headers(user))
    body = r.json()
    assert body["totp"]["enabled"] is True
    assert body["api_keys"]["can_create"] is True


def test_security_endpoints_require_login(db):
    client = _client(db)
    assert client.get("/api/user/security/overview").status_code == 401
    assert client.post("/api/user/security/totp/setup").status_code == 401
    assert client.get("/api/platform/me").status_code == 401
