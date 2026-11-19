"""门户认证端到端测试：注册 → 登录 → me → refresh → 改密 → 受保护端点"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_TYPE"] = "sqlite"
os.environ["REDIS_ENABLED"] = "false"
os.environ["EMBY_PUBLIC_URL"] = "http://media.example.com:8000"
os.environ["SECRET_KEY"] = "e2e-test-secret-key-not-for-production"

DB = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"

from fastapi.testclient import TestClient  # noqa: E402

from backend.database import engine, init_db  # noqa: E402

init_db()

from backend.main import app  # noqa: E402

client = TestClient(app)

AH = {"Content-Type": "application/json"}

# ==================== 1. 注册 ====================
r = client.post("/api/user/auth/register", json={
    "username": "newuser",
    "password": "secret123",
    "email": "newuser@example.com",
})
assert r.status_code == 201, (r.status_code, r.text)
data = r.json()
assert data["access_token"], "应返回 access_token"
assert data["refresh_token"], "应返回 refresh_token"
assert data["token_type"] == "bearer"
assert data["user"]["username"] == "newuser"
assert data["user"]["id"] > 0
print("OK register -> token + user:", data["user"])

# 账号统一：注册出参不再带接线名字段
assert "emby_username" not in data["user"], data["user"]
print("OK register response has no emby_username field")

# ==================== 2. 重复注册拒绝 ====================
r = client.post("/api/user/auth/register", json={"username": "newuser", "password": "secret123"})
assert r.status_code == 409, (r.status_code, r.text)
assert "已被注册" in r.json()["detail"]
print("OK duplicate register rejected (409)")

# 非法用户名
r = client.post("/api/user/auth/register", json={"username": "ab", "password": "secret123"})
assert r.status_code == 422 or r.status_code == 400, (r.status_code, r.text)
print("OK invalid username rejected")

# ==================== 3. JWT 结构校验 ====================
from backend.security import decode_token  # noqa: E402

payload = decode_token(data["access_token"], expected_type="access")
assert payload and payload["sub"] == str(data["user"]["id"])
assert decode_token(data["refresh_token"], expected_type="refresh") is not None
assert decode_token(data["access_token"], expected_type="refresh") is None, "access 不能当 refresh 用"
assert decode_token(data["refresh_token"], expected_type="access") is None, "refresh 不能当 access 用"
print("OK JWT structure (type isolation)")

# ==================== 4. 登录 ====================
r = client.post("/api/user/auth/login", json={"username": "newuser", "password": "secret123"})
assert r.status_code == 200, (r.status_code, r.text)
login_data = r.json()
assert login_data["access_token"] and login_data["user"]["username"] == "newuser"
assert "emby_username" not in login_data["user"], "登录出参不应带接线名字段"
access = login_data["access_token"]
print("OK login -> token")

import sqlite3  # noqa: E402

db = sqlite3.connect(DB)
row = db.execute(
    "SELECT emby_username, emby_password FROM web_users WHERE username='newuser'"
).fetchone()
db.close()
assert row == (None, None), f"登录不应写入接线凭据, got {row}"
print("OK login leaves emby credentials unset")

# 错误密码
r = client.post("/api/user/auth/login", json={"username": "newuser", "password": "wrongpass"})
assert r.status_code == 401, (r.status_code, r.text)
assert "用户名或密码错误" in r.json()["detail"]
print("OK wrong password rejected (401)")

# 不存在的用户
r = client.post("/api/user/auth/login", json={"username": "ghost", "password": "whatever"})
assert r.status_code == 401
print("OK unknown user rejected (401)")

# ==================== 5. /me（JWT 鉴权）====================
r = client.get("/api/user/auth/me", headers={"Authorization": f"Bearer {access}"})
assert r.status_code == 200, (r.status_code, r.text)
assert r.json()["username"] == "newuser"
print("OK /me with JWT")

# 无 token
r = client.get("/api/user/auth/me")
assert r.status_code == 401
print("OK /me without token rejected (401)")

# 篡改 token
r = client.get("/api/user/auth/me", headers={"Authorization": "Bearer forged.token.here"})
assert r.status_code == 401
print("OK /me with forged token rejected (401)")

# refresh token 不能访问受保护端点
refresh_token = login_data["refresh_token"]
r = client.get("/api/user/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
assert r.status_code == 401, (r.status_code, r.text)
print("OK refresh token cannot access protected endpoint")

# ==================== 6. refresh 轮换 ====================
r = client.post("/api/user/auth/refresh", json={"refresh_token": refresh_token})
assert r.status_code == 200, (r.status_code, r.text)
new_access = r.json()["access_token"]
assert new_access
new_refresh = r.json()["refresh_token"]
assert new_refresh and new_refresh != refresh_token, "refresh 应轮换"
print("OK refresh -> new tokens")

# 新 access 可用
r = client.get("/api/user/auth/me", headers={"Authorization": f"Bearer {new_access}"})
assert r.status_code == 200
print("OK new access token works")

# 无效 refresh
r = client.post("/api/user/auth/refresh", json={"refresh_token": "garbage"})
assert r.status_code == 401
print("OK invalid refresh rejected (401)")

# ==================== 7. 改密（G-53：有恢复邮箱须带邮箱验证码） ====================
# 本进程内把邮件渠道换成假渠道截码（与单测同口径），走完整带码流程。
import re as _re  # noqa: E402

from backend.integrations import mail as _integrations_mail  # noqa: E402


class _SmokeChannel:
    def __init__(self):
        self.sent = []

    def _deliver(self, to_email, title, content):
        self.sent.append((to_email, title, content))
        return True, None


_smoke_channel = _SmokeChannel()
_integrations_mail.build_channel = lambda db: _smoke_channel

r = client.post("/api/user/auth/change-password/send-code",
                headers={"Authorization": f"Bearer {new_access}"})
assert r.status_code == 200, (r.status_code, r.text)
assert r.json()["verification"] == "email_code", r.json()
assert _smoke_channel.sent and _smoke_channel.sent[-1][0] == "newuser@example.com"
_code = _re.search(r"(?<!\d)(\d{6})(?!\d)", _smoke_channel.sent[-1][2]).group(1)

# 不带码改密应被拒
r = client.post("/api/user/auth/change-password",
                headers={"Authorization": f"Bearer {new_access}"},
                json={"old_password": "secret123", "new_password": "newpass456"})
assert r.status_code == 400, (r.status_code, r.text)
print("OK change-password requires email code")

r = client.post("/api/user/auth/change-password",
                headers={"Authorization": f"Bearer {new_access}"},
                json={"old_password": "secret123", "new_password": "newpass456",
                      "email_code": _code})
assert r.status_code == 200, (r.status_code, r.text)
print("OK change-password")

# 旧密码登录失败
r = client.post("/api/user/auth/login", json={"username": "newuser", "password": "secret123"})
assert r.status_code == 401
# 改密前的 token 留作"旧 token 应作废"的断言
stale_access = new_access
stale_refresh = new_refresh
# 新密码登录成功
r = client.post("/api/user/auth/login", json={"username": "newuser", "password": "newpass456"})
assert r.status_code == 200
new_access = r.json()["access_token"]
assert new_access and new_access != stale_access, "改密后应签发新 token"
assert "emby_username" not in r.json()["user"], "登录出参不应带接线名字段"
print("OK relogin with new password")

# 审查第八批 P2/P3：改密后旧 access / 旧 refresh 必须作废（后续步骤改用新 token）
r = client.get("/api/user/auth/me", headers={"Authorization": f"Bearer {stale_access}"})
assert r.status_code == 401, f"改密后旧 access 应被拒绝, got {r.status_code}"
print("OK stale access token rejected after password change")
r = client.post("/api/user/auth/refresh", json={"refresh_token": stale_refresh})
assert r.status_code == 401, f"改密后旧 refresh 应被拒绝, got {r.status_code}"
print("OK stale refresh token rejected after password change")

# 账号统一：门户改密不再写接线凭据两列（客户端登录直接验门户密码）
db = sqlite3.connect(DB)
row = db.execute(
    "SELECT emby_username, emby_password FROM web_users WHERE username='newuser'"
).fetchone()
db.close()
assert row == (None, None), f"改密不应再写接线凭据, got {row}"
print("OK password change leaves emby credential columns untouched")

# 账号卡：emby_username 字段下发网站用户名（本用户无查看权限，
# 按门禁口径置空）；不返回明文密码。带权限时的下发值由
# tests/test_account_unify_client_login.py 覆盖。
r = client.get("/api/user/emby/server", headers={"Authorization": f"Bearer {new_access}"})
assert r.status_code == 200
assert not r.json().get("emby_password"), "账号卡不应返回明文密码"
assert r.json().get("has_password") is True
assert r.json().get("emby_username") == "", r.json()
print("OK account card hides plaintext password and gates username")

# ==================== 8. 旧数字 token 默认已禁用（防 ID 枚举冒充）====================
user_id = data["user"]["id"]
r = client.get("/api/user/emby/server", headers={"Authorization": f"Bearer {user_id}"})
assert r.status_code == 401, f"数字 token 应被拒绝, got {r.status_code}"
print("OK legacy numeric token rejected by default")

# JWT 也可访问门户 Emby 端点
r = client.get("/api/user/emby/server", headers={"Authorization": f"Bearer {new_access}"})
assert r.status_code == 200
print("OK JWT works on /api/user/emby/server")

# ==================== 9. 接线账号端点已随账号统一删除 ====================
r = client.post("/api/user/emby/password",
                headers={"Authorization": f"Bearer {new_access}"},
                json={"username": "newuser_play", "password": "playpass1"})
assert r.status_code == 404, (r.status_code, r.text)
print("OK legacy set-emby-password endpoint is gone (404)")

# ==================== 10. Emby 协议认证（网站名+网站密码）====================
r = client.post("/emby/Users/AuthenticateByName",
                json={"Username": "newuser", "Pw": "newpass456"},
                headers={"X-Emby-Authorization": 'MediaBrowser Client="T", Device="T", DeviceId="t1", Version="1"'})
assert r.status_code == 200, (r.status_code, r.text)
print("OK Emby AuthenticateByName with portal credentials")

# 错误密码必须被拒绝（此前空存储时任意密码可通过）
r = client.post("/emby/Users/AuthenticateByName",
                json={"Username": "newuser", "Pw": "wrong-password"},
                headers={"X-Emby-Authorization": 'MediaBrowser Client="T", Device="T", DeviceId="t2", Version="1"'})
assert r.status_code == 401, f"错误密码应被拒绝, got {r.status_code}"
print("OK Emby AuthenticateByName rejects wrong password")

# ==================== 11. logout ====================
r = client.post("/api/user/auth/logout", headers={"Authorization": f"Bearer {new_access}"})
assert r.status_code == 200
print("OK logout")

print("\n✅ 门户认证端到端测试全部通过")
os.remove(DB)
