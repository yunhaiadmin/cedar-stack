# -*- coding: utf-8 -*-
"""白名单码（code_type=3）永久生效回归测试（2026-11-01 用户实测没生效）。

既有机制：白名单码生成时 days=-1，核销 ``redeem_code`` 按 reward_type 分派。
断点（修复前）：一张白名单码只永久化 reward_type 对应的那一处——
- reward_type=subscription（默认）→ 只有订阅 +100 年，公益资格照旧到期、
  查看凭证根本不发；
- reward_type=welfare → 资格＋打包查看行永久，但订阅不发；
- reward_type=points/discount → 白名单语义完全丢失（只发能量/折扣）。

修复口径：白名单码核销 = 账号置为长期有效，该服订阅长期
（grant_membership_days days=-1 → PERMANENT_DAYS）。
（「公益资格永久」已随公益资格整族于 2026-11-14 删除；「查看行
永久」已随公益服删除下线——核销不再写 EmbyViewUnlock，存量行原样
不动、也不再参与任何判定。）
非白名单码行为逐字不变。本文件同时覆盖管理端「白名单账号」名单/回收。
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

# 注意：本文件不许在导入时重绑全局 backend.database.engine。
# 套件同进程共享最后一次导入绑定的引擎（见 test_zz_fixture_hygiene.py）；
# 本文件此前在导入时把全局引擎绑到一个未 init_db 的空临时库，
# 导致之后所有依赖全局 SessionLocal 的测试在 setup 报 no such table
# （#1668 冒烟两轮同形态失败的根因）。本文件夹具自带内存库，不需要全局引擎。
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import pytest  # noqa: E402

from backend import codes, models, subscriptions  # noqa: E402


@pytest.fixture()
def db():
    # StaticPool + check_same_thread=False：TestClient 在另一线程跑 app，
    # 同一条连接要能跨线程用（与生产 PG 的连接池语义无关，仅测试夹具）。
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.flush()
    return u


def _code(db, code_str, **kw):
    fields = {"max_uses": 1, "use_count": 0, "is_active": True,
              "code_type": codes.CODE_TYPE_WHITELIST, "days": -1,
              "reward_type": "subscription"}
    fields.update(kw)
    c = models.RegistrationCode(code=code_str, **fields)
    db.add(c)
    db.flush()
    return c


# 遗留表 emby_view_unlocks 的模型定义已删（2026-11-19）：存量行改走
# 原生 SQL 建表/写行/计数，断言口径不变（核销不写、判定不读该表）。
_LEGACY_UNLOCK_DDL = (
    "CREATE TABLE IF NOT EXISTS emby_view_unlocks ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, realm_id INTEGER NOT NULL, "
    "unlocked_at DATETIME, expires_at DATETIME, "
    "points_spent INTEGER DEFAULT 0, UNIQUE (user_id, realm_id))")


def _legacy_unlock_count(db, user_id):
    db.execute(text(_LEGACY_UNLOCK_DDL))
    return db.execute(
        text("SELECT COUNT(*) FROM emby_view_unlocks"
             " WHERE user_id = :user_id"),
        {"user_id": user_id}).scalar()


def _add_legacy_unlock(db, user_id, realm_id, expires_at):
    db.execute(text(_LEGACY_UNLOCK_DDL))
    db.execute(
        text("INSERT INTO emby_view_unlocks"
             " (user_id, realm_id, unlocked_at, expires_at, points_spent)"
             " VALUES (:user_id, :realm_id, :unlocked_at, :expires_at, 50)"),
        {"user_id": user_id, "realm_id": realm_id,
         "unlocked_at": datetime.now(), "expires_at": expires_at})


def _legacy_unlock_expires_at(db, user_id):
    db.execute(text(_LEGACY_UNLOCK_DDL))
    value = db.execute(
        text("SELECT expires_at FROM emby_view_unlocks"
             " WHERE user_id = :user_id"),
        {"user_id": user_id}).scalar()
    return datetime.fromisoformat(value) if value is not None else None


def _assert_all_permanent(db, user):
    db.refresh(user)
    # ① 查看行写口已下线：核销不得新建任何遗留查看行
    assert _legacy_unlock_count(db, user.id) == 0
    assert subscriptions.view_grant(db, user) is True
    # ② 订阅长期有效（PERMANENT_DAYS=36500 的既有「永久近似」口径）
    assert subscriptions.has_active_subscription(db, user.id) is True
    sub = (db.query(models.UserSubscription)
           .filter(models.UserSubscription.user_id == user.id,
                   models.UserSubscription.status == "active")
           .order_by(models.UserSubscription.end_date.desc()).first())
    assert sub is not None
    assert sub.end_date > datetime.now() + timedelta(days=36000)


# ---------- 1. 白名单码（subscription/welfare 型）都必须订阅长期 ----------

def test_whitelist_subscription_flavor_grants_permanent(db):
    u = _user(db, "wl_sub")
    _code(db, "VIP-SUB000000001")
    db.commit()
    result = codes.redeem_code(db, u, "VIP-SUB000000001")
    db.commit()
    assert result["success"] is True
    _assert_all_permanent(db, u)
    db.refresh(u)
    assert not hasattr(u, "is_welfare")  # 公益资格字段已删除（2026-11-14）


def test_whitelist_welfare_flavor_grants_permanent(db):
    u = _user(db, "wl_welfare")
    _code(db, "VIP-WEL000000001", reward_type="welfare")
    db.commit()
    result = codes.redeem_code(db, u, "VIP-WEL000000001")
    db.commit()
    assert result["success"] is True
    _assert_all_permanent(db, u)
    db.refresh(u)
    assert not hasattr(u, "is_welfare")  # 公益资格字段已删除（2026-11-14）


def test_whitelist_points_flavor_still_means_permanent(db):
    """能量型白名单码已随货币下线：核销直接拒绝、不烧码、不给白名单。

    旧口径（points 型码核销即永久白名单）随能量删除作废；welfare 型
    码的永久白名单语义见同文件其余测试，不受影响。
    """
    u = _user(db, "wl_points")
    _code(db, "VIP-PTS000000001", reward_type="points", points_value=50)
    db.commit()
    result = codes.redeem_code(db, u, "VIP-PTS000000001")
    db.commit()
    assert result["success"] is False
    assert "能量卡码已下线" in result["message"]
    code = db.query(models.RegistrationCode).filter_by(
        code="VIP-PTS000000001").first()
    assert code.use_count == 0  # 不烧码
    db.refresh(u)
    assert not hasattr(u, "is_welfare")


def test_whitelist_redeem_stacks_on_existing_finite_rights(db):
    """已有有限期订阅/查看行时核销白名单码 → 订阅升为长期，不报错；
    存量查看行原样不动（写口已下线，不再续开/升永久）。"""
    u = _user(db, "wl_stack")
    finite_expiry = datetime.now() + timedelta(days=5)
    _add_legacy_unlock(db, u.id, 1, finite_expiry)
    _code(db, "VIP-STK000000001", reward_type="welfare")
    db.commit()
    result = codes.redeem_code(db, u, "VIP-STK000000001")
    db.commit()
    assert result["success"] is True
    assert _legacy_unlock_expires_at(db, u.id) == finite_expiry
    db.refresh(u)
    assert subscriptions.view_grant(db, u) is True
    sub = (db.query(models.UserSubscription)
           .filter(models.UserSubscription.user_id == u.id,
                   models.UserSubscription.status == "active")
           .order_by(models.UserSubscription.end_date.desc()).first())
    assert sub is not None
    assert sub.end_date > datetime.now() + timedelta(days=36000)


# ---------- 2. 非白名单码行为不变 ----------

def test_renew_code_unchanged(db):
    u = _user(db, "plain_renew")
    _code(db, "REN-PLAIN000001", code_type=codes.CODE_TYPE_RENEW, days=30)
    db.commit()
    result = codes.redeem_code(db, u, "REN-PLAIN000001")
    db.commit()
    assert result["success"] is True
    db.refresh(u)
    assert not hasattr(u, "is_welfare")
    # 查看口径里有效订阅本身即 granted，且不得生成任何查看行
    assert subscriptions.view_grant(db, u) is True
    assert _legacy_unlock_count(db, u.id) == 0
    sub = (db.query(models.UserSubscription)
           .filter(models.UserSubscription.user_id == u.id).first())
    assert sub is not None
    assert sub.end_date < datetime.now() + timedelta(days=31)


def test_welfare_code_rejected_no_subscription(db):
    """普通资格码（code_type=注册码 + reward_type=welfare）已下线：
    兑换入口直接拒绝，不发资格、不发订阅、不建查看行。"""
    u = _user(db, "plain_welfare")
    _code(db, "REG-WEL000000001", code_type=codes.CODE_TYPE_REGISTER,
          days=30, reward_type="welfare")
    db.commit()
    result = codes.redeem_code(db, u, "REG-WEL000000001")
    db.commit()
    assert result["success"] is False
    db.refresh(u)
    assert not hasattr(u, "is_welfare")
    assert db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == u.id).count() == 0
    assert _legacy_unlock_count(db, u.id) == 0


# ---------- 3. 管理端名单 / 回收 ----------

@pytest.fixture()
def admin_client(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.admin_core import get_current_admin
    from backend.api.admin_ops import admin_ops_router
    from backend.database import get_db

    admin = models.WebUser(username="boss", password_hash="x", is_active=True)
    db.add(admin)
    db.commit()

    app = FastAPI()
    app.include_router(admin_ops_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_admin] = lambda: admin
    try:
        yield TestClient(app, raise_server_exceptions=False), admin
    finally:
        app.dependency_overrides.clear()


def test_admin_whitelist_accounts_list_and_revoke(db, admin_client):
    client, _admin = admin_client
    u = _user(db, "wl_listed")
    _code(db, "VIP-LST000000001")
    db.commit()
    assert codes.redeem_code(db, u, "VIP-LST000000001")["success"] is True
    db.commit()

    r = client.get("/api/admin/registration-codes/whitelist-accounts")
    assert r.status_code == 200
    rows = r.json()["accounts"]
    mine = [x for x in rows if x["user_id"] == u.id]
    assert len(mine) == 1
    assert mine[0]["username"] == "wl_listed"
    assert "welfare_permanent" not in mine[0]  # 字段已随资格删除摘除
    assert "view_permanent" not in mine[0]  # 字段已随查看凭证下线摘除
    assert mine[0]["subscription_longterm"] is True
    assert mine[0]["is_permanent_active"] is True

    r = client.post(
        f"/api/admin/registration-codes/whitelist-accounts/{u.id}/revoke")
    assert r.status_code == 200
    db.expire_all()
    db.refresh(u)
    assert subscriptions.view_grant(db, u) is False
    assert subscriptions.has_active_subscription(db, u.id) is False

    r = client.get("/api/admin/registration-codes/whitelist-accounts")
    mine = [x for x in r.json()["accounts"] if x["user_id"] == u.id]
    assert len(mine) == 1  # 仍留痕，但已非永久生效
    assert mine[0]["is_permanent_active"] is False


def test_admin_whitelist_accounts_requires_auth(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.admin_ops import admin_ops_router
    from backend.database import get_db

    app = FastAPI()
    app.include_router(admin_ops_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/admin/registration-codes/whitelist-accounts")
    assert r.status_code in (401, 403)
    r = client.post("/api/admin/registration-codes/whitelist-accounts/1/revoke")
    assert r.status_code in (401, 403)
