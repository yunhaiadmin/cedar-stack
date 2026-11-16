# -*- coding: utf-8 -*-
"""家庭子账号专项测试（2026-11-15，feat/sub-accounts）。

定版口径（backend/sub_accounts.py 模块 docstring 是唯一出处）：
子账号是独立 WebUser（parent_user_id 指主账号）；订阅/流量/求片
额度一律按主账号算（有效账号解析）；播放门禁随主账号（绑定走
playback_gate 单点、订阅走 subscriptions.can_play）；子账号禁止
一切经济动作（接口 403）；删除=软禁用；可建上限读主账号当前
套餐 entitlements 的 sub_account_limit（未声明按 0）；主账号降
到无子账号权益的档时子账号同步不可播。

隔离：内存 SQLite（StaticPool）+ 最小 FastAPI 应用，不碰真库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import entitlements, models, ratelimit, sub_accounts, subscriptions
from backend import traffic_quota
from backend.api import economy as economy_api
from backend.api import emby_portal, traffic as traffic_api
from backend.api import sub_accounts as sub_accounts_api
from backend.api import wallet as wallet_api
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import playback_gate
from backend.security import create_access_token, hash_password

PASSWORD = "SubPass123"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main",
                                   is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_caches():
    ratelimit._limiter._hits.clear()
    entitlements._PLAN_SNAPSHOT_CACHE.clear()
    entitlements._USER_PLAN_CACHE.clear()
    yield
    ratelimit._limiter._hits.clear()
    entitlements._PLAN_SNAPSHOT_CACHE.clear()
    entitlements._USER_PLAN_CACHE.clear()


# ---------------- 造数帮手 ----------------

def _user(db, username, password=None, **kw):
    user = models.WebUser(
        username=username,
        password_hash=hash_password(password) if password else "x",
        is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _plan(db, name, *, sub_limit=None, traffic_bytes=None, seek_limit=None):
    declared = {}
    if sub_limit is not None:
        declared["sub_account_limit"] = sub_limit
    if traffic_bytes is not None:
        declared["monthly_traffic_bytes"] = traffic_bytes
    if seek_limit is not None:
        declared["media_seek_monthly_limit"] = seek_limit
    plan = models.SubscriptionPlan(
        name=name, price=100, duration_days=30, is_active=True,
        entitlements=declared or None)
    db.add(plan)
    db.commit()
    return plan


def _subscribe(db, user, plan, days=30):
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=None,
        start_date=now, end_date=now + timedelta(days=days),
        status="active")
    db.add(sub)
    db.commit()
    entitlements.invalidate(user.id)
    return sub


def _family(db, sub_limit=2, name="parent", plan_name="家庭版", **plan_kw):
    """主账号 + 含子账号权益的套餐 + 生效订阅。"""
    parent = _user(db, name, PASSWORD)
    plan = _plan(db, plan_name, sub_limit=sub_limit, **plan_kw)
    _subscribe(db, parent, plan)
    return parent, plan


def _binding(db, user_id, status="active"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider="drive", account_label="盘",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _client(db):
    app = FastAPI()
    app.include_router(emby_portal.auth_router)
    app.include_router(sub_accounts_api.sub_accounts_router)
    app.include_router(traffic_api.traffic_router)
    app.include_router(wallet_api.wallet_router)
    app.include_router(economy_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _auth(user):
    token = create_access_token(
        user.id, {"username": user.username},
        token_version=user.token_version or 0)
    return {"Authorization": f"Bearer {token}"}


# ---------------- 创建：上限与前置校验 ----------------

def test_create_rejected_when_limit_zero(db):
    """非家庭套餐（sub_account_limit 未声明→0）：接口层直接拒。"""
    parent = _user(db, "solo", PASSWORD)
    plan = _plan(db, "国际版")  # 不声明 sub_account_limit
    _subscribe(db, parent, plan)
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    assert exc.value.code == "limit_reached"


def test_create_rejected_without_active_subscription(db):
    parent = _user(db, "nosub", PASSWORD)
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    assert exc.value.code == "no_active_subscription"


def test_create_up_to_limit_then_rejected(db):
    parent, _plan_row = _family(db, sub_limit=2)
    first = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    second = sub_accounts.create_sub_account(db, parent, "kid2", PASSWORD)
    assert first.parent_user_id == parent.id
    assert second.parent_user_id == parent.id
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "kid3", PASSWORD)
    assert exc.value.code == "limit_reached"


def test_sub_account_cannot_create(db):
    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, child, "grandkid", PASSWORD)
    assert exc.value.code == "is_sub_account"


def test_create_username_rules_and_conflict(db):
    parent, _ = _family(db)
    _user(db, "taken_name", PASSWORD)
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "taken_name", PASSWORD)
    assert exc.value.code == "username_taken"
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "ab", PASSWORD)
    assert exc.value.code == "bad_username"
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "kid1", "short")
    assert exc.value.code == "bad_password"


def test_create_api_status_codes(db):
    client = _client(db)
    parent, _ = _family(db)
    resp = client.post("/api/user/sub-accounts", headers=_auth(parent),
                       json={"username": "kid1", "password": PASSWORD})
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    assert body["item"]["username"] == "kid1"
    assert body["item"]["is_active"] is True
    # 撞名 → 409
    resp = client.post("/api/user/sub-accounts", headers=_auth(parent),
                       json={"username": "kid1", "password": PASSWORD})
    assert resp.status_code == 409
    # 列表带上限信息
    resp = client.get("/api/user/sub-accounts", headers=_auth(parent))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["is_sub_account"] is False
    assert payload["limit"] == 2
    assert payload["used"] == 1
    assert [c["username"] for c in payload["items"]] == ["kid1"]


# ---------------- 登录与删除 ----------------

def test_sub_account_login_and_identity(db):
    client = _client(db)
    parent, _ = _family(db)
    sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    resp = client.post("/api/user/auth/login",
                       json={"username": "kid1", "password": PASSWORD})
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["is_sub_account"] is True
    assert user["parent_username"] == "parent"
    # 子账号视角的订阅列表 = 主账号订阅 + 来源标记
    token = resp.json()["access_token"]
    resp = client.get("/api/user/sub-accounts",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["is_sub_account"] is True
    assert resp.json()["parent_username"] == "parent"


def test_delete_disables_login_and_frees_slot(db):
    client = _client(db)
    parent, _ = _family(db, sub_limit=1)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    resp = client.delete(f"/api/user/sub-accounts/{child.id}",
                         headers=_auth(parent))
    assert resp.status_code == 200
    # 记录保留（软禁用），登录被拒
    row = db.query(models.WebUser).filter(
        models.WebUser.id == child.id).first()
    assert row is not None and row.is_active is False
    resp = client.post("/api/user/auth/login",
                       json={"username": "kid1", "password": PASSWORD})
    assert resp.status_code in (401, 403)
    # 名额释放：可再建一个补上
    replacement = sub_accounts.create_sub_account(
        db, parent, "kid2", PASSWORD)
    assert replacement.parent_user_id == parent.id


def test_delete_others_child_is_404(db):
    client = _client(db)
    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    stranger, _ = _family(db, name="stranger", plan_name="家庭版B")
    resp = client.delete(f"/api/user/sub-accounts/{child.id}",
                         headers=_auth(stranger))
    assert resp.status_code == 404


# ---------------- 播放门禁随主账号 ----------------

def test_binding_gate_follows_parent(db):
    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    # 主账号没绑盘：子账号同样被拦
    assert playback_gate.has_active_binding(db, child.id) is False
    with pytest.raises(Exception):
        playback_gate.ensure_binding_playback_allowed(db, child)
    # 主账号绑上 active drive 盘：子账号随之放行（自己不绑盘）
    binding = _binding(db, parent.id, "active")
    assert playback_gate.has_active_binding(db, child.id) is True
    playback_gate.ensure_binding_playback_allowed(db, child)
    # 主账号绑定失效：子账号同步被拦
    binding.status = "invalid"
    db.commit()
    assert playback_gate.has_active_binding(db, child.id) is False


def test_can_play_follows_parent_subscription(db):
    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    assert subscriptions.can_play(db, child) is True
    # 主账号订阅过期 → 子账号同步不可播
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == parent.id).first()
    sub.end_date = datetime.now() - timedelta(days=1)
    db.commit()
    entitlements.invalidate(parent.id)
    assert subscriptions.can_play(db, child) is False


def test_downgrade_to_no_sub_entitlement_blocks_play(db):
    """降档：limit 变小（仍 >0）存量可播；降到无子账号权益档不可播。"""
    parent, family_plan = _family(db, sub_limit=2)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    child2 = sub_accounts.create_sub_account(db, parent, "kid2", PASSWORD)
    assert subscriptions.can_play(db, child) is True
    # 降到 limit=1：已建两个不强删、仍可播，但不可再新建
    family_plan.entitlements = {"sub_account_limit": 1}
    db.commit()
    entitlements.invalidate()
    assert subscriptions.can_play(db, child) is True
    assert subscriptions.can_play(db, child2) is True
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, parent, "kid3", PASSWORD)
    assert exc.value.code == "limit_reached"
    # 换到无子账号权益的套餐（订阅仍生效）：子账号同步不可播
    solo_plan = _plan(db, "国际版")
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == parent.id).first()
    sub.plan_id = solo_plan.id
    db.commit()
    entitlements.invalidate(parent.id)
    assert subscriptions.has_active_subscription(db, parent.id) is True
    assert subscriptions.can_play(db, parent) is True
    assert subscriptions.can_play(db, child) is False


# ---------------- 流量与求片额度按主账号算 ----------------

def test_traffic_quota_and_usage_follow_parent(db):
    parent, _ = _family(db, traffic_bytes=10_000)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    # 计量归账点解析：子账号的字节记到主账号名下
    assert sub_accounts.effective_user_id(db, child) == parent.id
    assert sub_accounts.effective_user_id(db, parent) == parent.id
    # 主账号名下的日账，子账号视角同数（配额与已用都读主账号）
    db.add(em.TrafficUsageDaily(
        user_id=parent.id, day=date.today(),
        relay_bytes=3_000, local_bytes=500, direct_plays=0))
    db.commit()
    child_view = traffic_quota.usage_summary(db, child)
    parent_view = traffic_quota.usage_summary(db, parent)
    assert child_view["quota_bytes"] == 10_000
    assert child_view["used_bytes"] == 3_500
    assert child_view["used_bytes"] == parent_view["used_bytes"]
    assert child_view["exhausted"] is False
    # 额度耗尽按主账号算：子账号同样 exhausted
    row = db.query(em.TrafficUsageDaily).first()
    row.relay_bytes = 10_000
    db.commit()
    assert traffic_quota.usage_summary(db, child)["exhausted"] is True


def test_seek_quota_shared_within_family(db):
    from backend import media_seek

    parent, _ = _family(db, seek_limit=5)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    for user, title in ((parent, "片一"), (child, "片二")):
        db.add(models.MovieRequest(
            user_id=user.id, movie_name=title, status="pending",
            created_at=datetime.now()))
    db.commit()
    quota = media_seek.monthly_quota(db, child)
    # 上限读主账号套餐；已用量全家合并（主账号 1 + 子账号 1）
    assert quota["monthly_limit"] == 5
    assert quota["monthly_used"] == 2
    assert media_seek.monthly_quota(db, parent)["monthly_used"] == 2


def test_subscription_display_shared_from_family(db):
    from backend.api import user as user_api

    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    rows = user_api.get_my_subscriptions(db=db, current_user=child)
    assert len(rows) == 1
    assert rows[0]["shared_from_family"] is True
    rows = user_api.get_my_subscriptions(db=db, current_user=parent)
    assert rows[0]["shared_from_family"] is False


# ---------------- 子账号禁止经济动作（接口 403） ----------------

def test_sub_account_economy_endpoints_forbidden(db):
    client = _client(db)
    parent, _ = _family(db, traffic_bytes=10_000)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    headers = _auth(child)
    # 钱包买订阅
    resp = client.post("/api/user/wallet/pay-subscription",
                       headers=headers,
                       json={"plan_id": 1, "idempotency_key": "k0"})
    assert resp.status_code == 403
    # 余额码兑换
    resp = client.post("/api/user/wallet/redeem",
                       headers=headers, json={"code": "XXXX"})
    assert resp.status_code == 403
    # 网关充值下单
    resp = client.post("/api/user/economy/payment/order",
                       headers=headers,
                       json={"kind": "subscription", "item_id": 1})
    assert resp.status_code == 403
    # 购流量（余额购买）
    resp = client.post("/api/user/traffic/buy-balance",
                       headers=headers,
                       json={"gb": 100, "idempotency_key": "k1"})
    assert resp.status_code == 403
    # 时间换流量（#215 起按包计：package=half，旧 gb 字段已删）
    resp = client.post("/api/user/traffic/exchange",
                       headers=headers,
                       json={"package": "half", "idempotency_key": "k2"})
    assert resp.status_code == 403
    # 再建子账号（子账号视角调创建接口）
    resp = client.post("/api/user/sub-accounts",
                       headers=headers,
                       json={"username": "grandkid", "password": PASSWORD})
    assert resp.status_code == 403


def test_admin_user_list_shows_parent(db):
    from backend.api import admin as admin_api

    parent, _ = _family(db)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PASSWORD)
    result = admin_api.list_users(db=db, limit=50, offset=0)
    by_id = {item["id"]: item for item in result["users"]}
    assert by_id[child.id]["parent_user_id"] == parent.id
    assert by_id[child.id]["parent_username"] == "parent"
    assert by_id[parent.id]["parent_user_id"] is None
    assert by_id[parent.id]["parent_username"] is None
