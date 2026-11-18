# -*- coding: utf-8 -*-
"""团队功能重建·批 1 后端专项测试（2026-11-18）。

定版口径（说明书 team-rebuild-spec-draft-20261118 + 用户 2026-11-18 拍板）：
- 定价＝友站三分之一：Global 锚点 10,000GB→$1.00/千GB、
  20,000GB→$0.98/千GB、1,000,000GB→$0.90/千GB，锚点间线性插值；
  Premium 恒定 $4.00/千GB；起订 Global 10,000GB / Premium 5,000GB；
  上限 1,000,000GB；时长 1–12 个月线性；子账户上限＝流量GB/100。
- 购买＝钱包余额扣款；成功建团 pending_activation，管理端开通后 active。
- 旧套餐抵扣复用现行订阅购买公式（economy._switch_credit），被抵扣的
  旧个人订阅随购买到期（与现行换套餐链同口径）。
- 成员＝团长创建的子账号（parent 机制＋team 归属），禁经济动作/禁建团。
- 归账：active 团队成员的流量记本人（团队池），其余回落有效账号解析。

隔离：内存 SQLite（StaticPool）+ 最小 FastAPI 应用，不碰真库。
"""
import os
import types

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import teams as teams_service
from backend import wallet as wallet_service
from backend.api import admin_teams as _admin_teams  # noqa: F401 导入即注册管理端
from backend.api import teams as teams_api
from backend.api.admin_core import admin_router, get_current_admin
from backend.api.user import get_current_user
from backend.database import get_db
from backend.emby_server import models as em

GB_BYTES = 1024 ** 3


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
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _user(db, username, **kw):
    user = models.WebUser(
        username=username, password_hash="x", is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def users(db):
    return {
        "owner": _user(db, "teamowner"),
        "other": _user(db, "bystander"),
        "admin": _user(db, "teamadmin", is_staff=True, admin_role="super"),
    }


@pytest.fixture()
def client(db, users):
    app = FastAPI()
    app.include_router(teams_api.teams_router)
    app.include_router(admin_router)
    current = {"user": users["owner"], "admin": users["admin"]}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    app.dependency_overrides[get_current_admin] = lambda: current["admin"]
    return TestClient(app, raise_server_exceptions=False), current


def _set_config(db, key, value):
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(models.SystemConfig(key=key, value=value))
    db.commit()
    from backend.integrations import store
    store.invalidate(key)


def _fund(db, user, dollars):
    wallet_service.credit(
        db, user.id, Decimal(str(dollars)), kind="recharge",
        ref_id=f"fund-{user.id}")
    db.commit()


def _balance(db, user):
    return wallet_service.get_balance(db, user.id)


def _preview(client, current, user, **over):
    current["user"] = user
    payload = {"line": "global", "traffic_gb": 10000, "months": 1}
    payload.update(over)
    return client.post("/api/user/teams/preview", json=payload)


def _buy(client, current, user, **over):
    current["user"] = user
    payload = {"line": "global", "traffic_gb": 10000, "months": 1,
               "idempotency_key": "buy-1"}
    payload.update(over)
    return client.post("/api/user/teams/purchase", json=payload)


def _personal_sub(db, user, *, price="36.00", started_days_ago=15,
                  total_days=30):
    """给 user 一份生效中的个人订阅＋一笔已支付订单（抵扣计算的输入）。"""
    plan = models.SubscriptionPlan(
        name="个人版", price=Decimal(price), duration_days=total_days,
        is_active=True)
    db.add(plan)
    db.commit()
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=None,
        start_date=now - timedelta(days=started_days_ago),
        end_date=now + timedelta(days=total_days - started_days_ago),
        status="active")
    db.add(sub)
    db.commit()
    order = models.SubscriptionOrder(
        order_id=f"PO-{user.id}", user_id=user.id, plan_id=plan.id,
        subscription_id=sub.id, amount=Decimal(price), status="paid",
        payment_method="wallet")
    db.add(order)
    db.commit()
    return plan, sub


# ==================== 计价：锚点 / 插值 / 线性 ====================

def test_quote_global_anchor_prices(db, users):
    owner = users["owner"]
    q = teams_service.quote(db, owner, line="global", traffic_gb=10000,
                            months=1)
    assert q["monthly_price_fen"] == 1000  # 10TB = $10.00/月
    assert q["list_price_fen"] == 1000
    assert q["total_fen"] == 1000
    assert q["sub_account_limit"] == 100

    q = teams_service.quote(db, owner, line="global", traffic_gb=20000,
                            months=1)
    assert q["monthly_price_fen"] == 1960  # 20TB = $19.60/月

    q = teams_service.quote(db, owner, line="global", traffic_gb=1000000,
                            months=1)
    assert q["monthly_price_fen"] == 90000  # 1PB 锚点 $0.90/千GB
    assert q["sub_account_limit"] == 10000


def test_quote_global_interpolation(db, users):
    owner = users["owner"]
    # 15,000GB：锚点间线性插值，单价 (1.00+0.98)/2 = $0.99/千GB
    q = teams_service.quote(db, owner, line="global", traffic_gb=15000,
                            months=1)
    assert q["monthly_price_fen"] == 1485
    # 500,000GB：98 - 8*(480000/980000) = 94.0816... 分/千GB → *500 取整
    q = teams_service.quote(db, owner, line="global", traffic_gb=500000,
                            months=1)
    assert q["monthly_price_fen"] == 47041


def test_quote_premium_linear_and_months(db, users):
    _set_config(db, "team_premium_enabled", "true")
    owner = users["owner"]
    q = teams_service.quote(db, owner, line="premium", traffic_gb=5000,
                            months=1)
    assert q["monthly_price_fen"] == 2000  # 5TB = $20.00/月
    assert q["sub_account_limit"] == 50
    q = teams_service.quote(db, owner, line="premium", traffic_gb=10000,
                            months=3)
    assert q["monthly_price_fen"] == 4000
    assert q["list_price_fen"] == 12000  # 时长线性、无折扣


def test_preview_validation_ranges(db, client, users):
    _set_config(db, "team_premium_enabled", "true")
    c, current = client
    owner = users["owner"]
    assert _preview(c, current, owner, traffic_gb=9999).status_code == 400
    assert _preview(c, current, owner,
                    traffic_gb=1000001).status_code == 400
    assert _preview(c, current, owner, line="premium",
                    traffic_gb=4999).status_code == 400
    assert _preview(c, current, owner, months=0).status_code == 400
    assert _preview(c, current, owner, months=13).status_code == 400
    assert _preview(c, current, owner, line="vip").status_code == 400
    resp = _preview(c, current, owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["purchase_enabled"] is True
    assert body["traffic_bytes"] == 10000 * GB_BYTES


# ==================== 旧套餐抵扣 ====================

def test_preview_switch_credit(db, client, users):
    c, current = client
    owner = users["owner"]
    _personal_sub(db, owner)  # $36、30 天、已用 15 天 → 剩余价值约 $18
    resp = _preview(c, current, owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["switch_type"] == "switch"
    assert abs(body["switch_credit_fen"] - 1800) <= 3
    # 本例抵扣（$18）超过标价（$10）：总计下限为 0，不倒贴
    assert body["total_fen"] == max(
        0, body["list_price_fen"] - body["switch_credit_fen"])


def test_purchase_applies_credit_and_expires_old_sub(db, client, users):
    c, current = client
    owner = users["owner"]
    _plan, sub = _personal_sub(db, owner)
    _fund(db, owner, 100)
    resp = _buy(c, current, owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert abs(body["order"]["credit_fen"] - 1800) <= 3
    assert body["order"]["amount_fen"] == 0  # 抵扣覆盖全价，不扣款
    assert _balance(db, owner) == Decimal("100.00")
    db.expire_all()
    old = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == sub.id).first()
    assert old.end_date <= datetime.now()  # 旧订阅随换购到期（现行链口径）


# ==================== 购买链 ====================

def test_purchase_insufficient_balance(db, client, users):
    c, current = client
    owner = users["owner"]
    resp = _buy(c, current, owner)
    assert resp.status_code == 400, resp.text
    assert "余额不足" in resp.json()["detail"]["message"]
    assert db.query(models.Team).count() == 0
    assert db.query(models.TeamOrder).count() == 0


def test_purchase_success_pending_and_replay(db, client, users):
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 50)
    resp = _buy(c, current, owner, name="星际小队")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    team = body["team"]
    assert team["status"] == "pending_activation"
    assert team["name"] == "星际小队"
    assert team["plan_member_limit"] == 100
    assert team["plan_traffic_bytes"] == 10000 * GB_BYTES
    assert body["order"]["amount_fen"] == 1000
    assert body["order"]["status"] == "paid"
    assert _balance(db, owner) == Decimal("40.00")

    # 同幂等键重放：不重复扣款、不重复建团
    resp2 = _buy(c, current, owner, name="星际小队")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["order"]["order_id"] == body["order"]["order_id"]
    assert _balance(db, owner) == Decimal("40.00")
    assert db.query(models.Team).count() == 1


def test_purchase_switch_disabled(db, client, users):
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 50)
    _set_config(db, "team_purchase_enabled", "false")
    resp = _buy(c, current, owner)
    assert resp.status_code == 403, resp.text
    assert db.query(models.Team).count() == 0


def test_sub_account_cannot_preview_or_purchase(db, client, users):
    c, current = client
    owner = users["owner"]
    child = _user(db, "familychild", parent_user_id=owner.id)
    _fund(db, owner, 50)
    assert _preview(c, current, child).status_code == 403
    assert _buy(c, current, child).status_code == 403


def test_team_detail_before_and_after(db, client, users):
    c, current = client
    owner = users["owner"]
    current["user"] = owner
    resp = c.get("/api/user/teams")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team"] is None
    assert body["config"]["team_purchase_enabled"] is True
    assert body["config"]["team_global_min_traffic_gb"] == 10000

    _fund(db, owner, 50)
    _buy(c, current, owner)
    resp = c.get("/api/user/teams")
    body = resp.json()
    assert body["team"]["status"] == "pending_activation"
    assert body["my_role"] == "owner"
    assert body["team"]["member_count"] == 0


# ==================== 成员子账号 ====================

def _small_team(db, client, users):
    """Premium 起订改 100GB → 子账户上限 1，方便测上限。"""
    c, current = client
    owner = users["owner"]
    _set_config(db, "team_premium_enabled", "true")
    _set_config(db, "team_premium_min_traffic_gb", "100")
    _fund(db, owner, 10)
    resp = _buy(c, current, owner, line="premium", traffic_gb=100,
                idempotency_key="small-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["plan_member_limit"] == 1
    return resp.json()["team"]


def test_member_create_limit_and_forbidden(db, client, users):
    c, current = client
    owner = users["owner"]
    _small_team(db, client, users)

    current["user"] = owner
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberone", "password": "secret1"})
    assert resp.status_code == 200, resp.text
    member_id = resp.json()["user_id"]
    member = db.query(models.WebUser).filter(
        models.WebUser.id == member_id).first()
    assert member.parent_user_id == owner.id  # 子账号机制
    row = db.query(models.TeamMember).filter(
        models.TeamMember.user_id == member_id).first()
    assert row is not None and row.role == "member"

    # 上限＝流量/100＝1：第二个成员被拒
    resp = c.post("/api/user/teams/members",
                  json={"username": "membertwo", "password": "secret1"})
    assert resp.status_code == 403, resp.text

    # 成员本人：禁购买、禁建团、禁管成员、禁改名
    current["user"] = member
    assert _buy(c, current, member,
                idempotency_key="m-1").status_code == 403
    assert _preview(c, current, member).status_code == 403
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberthree", "password": "secret1"})
    assert resp.status_code == 403
    resp = c.put("/api/user/teams/name", json={"name": "x"})
    assert resp.status_code == 403


def test_member_list_fields_search_and_usage(db, client, users):
    c, current = client
    owner = users["owner"]
    team = _small_team(db, client, users)
    current["user"] = owner
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberone", "password": "secret1"})
    member_id = resp.json()["user_id"]

    today = datetime.now().date()
    db.add(em.TrafficUsageDaily(
        user_id=member_id, day=today, relay_bytes=12345, local_bytes=100,
        direct_plays=0))
    db.commit()

    resp = c.get("/api/user/teams/members")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["username"] == "memberone"
    assert item["traffic_quota_bytes"] == 100 * GB_BYTES // 1
    assert item["traffic_used_bytes"] == 12445
    assert item["expires_at"] is not None
    assert item["reset_date"] is not None

    resp = c.get("/api/user/teams/members", params={"q": "zzz"})
    assert resp.json()["total"] == 0
    resp = c.get("/api/user/teams/members", params={"q": "member"})
    assert resp.json()["total"] == 1
    resp = c.get("/api/user/teams/members", params={"status": "disabled"})
    assert resp.json()["total"] == 0


def test_member_delete_disables_and_frees_slot(db, client, users):
    c, current = client
    owner = users["owner"]
    _small_team(db, client, users)
    current["user"] = owner
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberone", "password": "secret1"})
    member_id = resp.json()["user_id"]

    resp = c.delete(f"/api/user/teams/members/{member_id}")
    assert resp.status_code == 200, resp.text
    db.expire_all()
    member = db.query(models.WebUser).filter(
        models.WebUser.id == member_id).first()
    assert member.is_active is False  # 软禁用（与家庭子账号删除同口径）
    assert db.query(models.TeamMember).filter(
        models.TeamMember.user_id == member_id).count() == 0

    # 名额释放后可再建
    resp = c.post("/api/user/teams/members",
                  json={"username": "membernew", "password": "secret1"})
    assert resp.status_code == 200, resp.text


def test_rename_team(db, client, users):
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 50)
    _buy(c, current, owner)
    current["user"] = owner
    resp = c.put("/api/user/teams/name", json={"name": "  新队名  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["name"] == "新队名"
    resp = c.put("/api/user/teams/name", json={"name": "   "})
    assert resp.status_code == 400


# ==================== 管理端：开通状态机 + 配置 ====================

def test_admin_activate_state_machine(db, client, users):
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 50)
    team = _buy(c, current, owner).json()["team"]

    resp = c.post(f"/api/admin/teams/{team['id']}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["status"] == "active"
    # 已开通再点 → 400
    resp = c.post(f"/api/admin/teams/{team['id']}/activate")
    assert resp.status_code == 400
    resp = c.post("/api/admin/teams/99999/activate")
    assert resp.status_code == 404

    resp = c.get("/api/admin/teams")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    resp = c.get(f"/api/admin/teams/{team['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["owner_username"] == "teamowner"


def test_admin_config_roundtrip(db, client, users):
    c, current = client
    owner = users["owner"]
    resp = c.get("/api/admin/teams/config")
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["team_premium_price_per_1000gb_fen"] == 400
    assert cfg["team_global_price_anchors"][0]["per_1000gb_fen"] == 100
    # Premium 开售开关默认关（暂停服务，2026-11-19 用户亲点）
    assert cfg["team_premium_enabled"] is False

    resp = c.put("/api/admin/teams/config",
                 json={"team_premium_price_per_1000gb_fen": 500,
                       "team_premium_enabled": True})
    assert resp.status_code == 200, resp.text
    q = teams_service.quote(db, owner, line="premium", traffic_gb=5000,
                            months=1)
    assert q["monthly_price_fen"] == 2500

    # 锚点表非法 → 整批拒绝、不落库
    resp = c.put("/api/admin/teams/config",
                 json={"team_global_price_anchors": "not-a-table"})
    assert resp.status_code == 400, resp.text
    resp = c.put("/api/admin/teams/config",
                 json={"team_global_price_anchors": [
                     {"gb": 10000, "per_1000gb_fen": 120}]})
    assert resp.status_code == 200, resp.text
    q = teams_service.quote(db, owner, line="global", traffic_gb=10000,
                            months=1)
    assert q["monthly_price_fen"] == 1200

    # 未知键拒绝
    resp = c.put("/api/admin/teams/config", json={"nope": 1})
    assert resp.status_code == 400


# ==================== 计量归账口径 ====================

def test_usage_attribution_states(db, client, users):
    c, current = client
    owner = users["owner"]
    plain = users["other"]
    assert teams_service.usage_attribution_user_id(db, plain) == plain.id

    child = _user(db, "familychild2", parent_user_id=owner.id)
    assert teams_service.usage_attribution_user_id(db, child) == owner.id

    team = _small_team(db, client, users)
    current["user"] = owner
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberx", "password": "secret1"})
    member_id = resp.json()["user_id"]
    member = db.query(models.WebUser).filter(
        models.WebUser.id == member_id).first()
    # 团队待开通：成员流量仍按子账号口径归团长
    assert teams_service.usage_attribution_user_id(db, member) == owner.id

    resp = c.post(f"/api/admin/teams/{team['id']}/activate")
    assert resp.status_code == 200, resp.text
    # 团队 active：成员流量记本人（团队池归账）
    assert teams_service.usage_attribution_user_id(db, member) == member_id

    # 团队到期：回落子账号口径
    row = db.query(models.Team).filter(models.Team.id == team["id"]).first()
    row.plan_expires_at = datetime.now() - timedelta(days=1)
    db.commit()
    assert teams_service.usage_attribution_user_id(db, member) == owner.id


def test_usage_attribution_duck_typed_user(db, client, users):
    """回归（#322 CI 114 红）：video_stream 的 set_current_user 传
    SimpleNamespace 桩用户（流式测试的 _user() 口径）时，归一口须按
    .id 取值；旧写法 int(桩对象) 抛 TypeError 是本单根因。"""
    plain = users["other"]
    stub = types.SimpleNamespace(id=plain.id, is_staff=False)
    assert teams_service.usage_attribution_user_id(db, stub) == plain.id
    # id 值直传与 WebUser 对象两形态不变
    assert teams_service.usage_attribution_user_id(db, plain.id) == plain.id
    assert teams_service.usage_attribution_user_id(db, plain) == plain.id
    # 无 id 的桩对象：返回 None，不碰库、不抛错（与 effective_user_id 同口径）
    assert teams_service.usage_attribution_user_id(db, object()) is None

    # 桩用户是 active 团队成员时同样记本人（团队池）
    team = _small_team(db, client, users)
    c, current = client
    current["user"] = users["owner"]
    resp = c.post("/api/user/teams/members",
                  json={"username": "memberns", "password": "secret1"})
    member_id = resp.json()["user_id"]
    resp = c.post(f"/api/admin/teams/{team['id']}/activate")
    assert resp.status_code == 200, resp.text
    stub_member = types.SimpleNamespace(id=member_id, is_staff=False)
    assert (teams_service.usage_attribution_user_id(db, stub_member)
            == member_id)


# ==================== 管理端：团队列表筛选/搜索/分页 + 详情 + 配置校验
# （2026-11-18 重建批 3：管理页只对接批 1 的购买制后端） ====================


def test_admin_list_filter_search_pagination(db, client, users):
    c, current = client
    owner, other = users["owner"], users["other"]
    _fund(db, owner, 50)
    _fund(db, other, 50)
    t1 = _buy(c, current, owner).json()["team"]
    t2 = _buy(c, current, other, idempotency_key="buy-2").json()["team"]

    resp = c.get("/api/admin/teams")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 2

    resp = c.get("/api/admin/teams", params={"status": "pending_activation"})
    assert resp.json()["total"] == 2
    resp = c.get("/api/admin/teams", params={"status": "active"})
    assert resp.json()["total"] == 0

    resp = c.get("/api/admin/teams", params={"q": "teamowner"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == t1["id"]
    resp = c.get("/api/admin/teams", params={"q": "bystander"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == t2["id"]
    resp = c.get("/api/admin/teams", params={"q": "zzz-no-such-team"})
    assert resp.json()["total"] == 0

    resp = c.get("/api/admin/teams", params={"limit": 1, "offset": 1})
    assert resp.json()["total"] == 2
    assert len(resp.json()["items"]) == 1

    resp = c.post(f"/api/admin/teams/{t1['id']}/activate")
    assert resp.status_code == 200, resp.text
    resp = c.get("/api/admin/teams", params={"status": "active"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == t1["id"]
    resp = c.get("/api/admin/teams", params={"status": "pending_activation"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == t2["id"]


def test_admin_detail_members_orders(db, client, users):
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 50)
    team = _buy(c, current, owner).json()["team"]

    current["user"] = owner
    resp = c.post("/api/user/teams/members",
                  json={"username": "detailmember", "password": "secret1"})
    assert resp.status_code == 200, resp.text

    resp = c.get(f"/api/admin/teams/{team['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team"]["owner_username"] == "teamowner"
    assert body["team"]["status"] == "pending_activation"
    usernames = [m["username"] for m in body["members"]]
    assert "teamowner" in usernames
    assert "detailmember" in usernames
    assert len(body["orders"]) == 1
    assert body["orders"][0]["status"] == "paid"
    assert body["orders"][0]["traffic_gb"] == 10000

    resp = c.get("/api/admin/teams/99999")
    assert resp.status_code == 404


def test_admin_config_rejects_bad_values(db, client, users):
    c, current = client
    resp = c.put("/api/admin/teams/config",
                 json={"not_a_team_key": 1})
    assert resp.status_code == 400
    resp = c.put("/api/admin/teams/config",
                 json={"team_global_price_anchors": "not-json"})
    assert resp.status_code == 400
    resp = c.put("/api/admin/teams/config",
                 json={"team_global_price_anchors": "[{\"gb\": 1}]"})
    assert resp.status_code == 400

    resp = c.put("/api/admin/teams/config",
                 json={"team_sub_account_divisor": 50,
                       "team_global_price_anchors":
                           '[{"gb": 10000, "per_1000gb_fen": 100}]'})
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["team_sub_account_divisor"] == 50
    assert cfg["team_global_price_anchors"][0]["per_1000gb_fen"] == 100
    resp = c.get("/api/admin/teams/config")
    assert resp.json()["config"]["team_sub_account_divisor"] == 50


# ==================== Premium 暂停服务（2026-11-19） ====================

def test_premium_paused_by_default_blocks_new_sales(db, client, users):
    """默认暂停：Premium 预览/购买被拒（403＋premium_line_paused），
    Global 不受影响，详情 config 带 team_premium_enabled=False。"""
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 100)

    resp = _preview(c, current, owner, line="premium", traffic_gb=5000)
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "premium_line_paused"
    assert detail["message"] == "Premium 线路暂停服务"

    resp = _buy(c, current, owner, line="premium", traffic_gb=5000,
                idempotency_key="paused-1")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "premium_line_paused"
    assert _balance(db, owner) == Decimal("100")

    # Global 照常预览，用户端详情里能看到暂停标记
    resp = _preview(c, current, owner)
    assert resp.status_code == 200, resp.text
    current["user"] = owner
    resp = c.get("/api/user/teams")
    assert resp.json()["config"]["team_premium_enabled"] is False


def test_premium_paused_existing_team_renewal_still_works(
        db, client, users):
    """存量 Premium 团队：暂停后团长预览/续费仍放行（同口续费），
    无 Premium 团队的人仍被拒。"""
    c, current = client
    owner = users["owner"]
    other = users["other"]
    _set_config(db, "team_premium_enabled", "true")
    _fund(db, owner, 200)
    resp = _buy(c, current, owner, line="premium", traffic_gb=5000,
                idempotency_key="renew-1")
    assert resp.status_code == 200, resp.text
    assert _balance(db, owner) == Decimal("180")

    _set_config(db, "team_premium_enabled", "false")
    resp = _preview(c, current, owner, line="premium", traffic_gb=5000)
    assert resp.status_code == 200, resp.text
    resp = _buy(c, current, owner, line="premium", traffic_gb=5000,
                idempotency_key="renew-2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["plan_line"] == "premium"
    assert _balance(db, owner) == Decimal("160")

    _fund(db, other, 200)
    resp = _preview(c, current, other, line="premium", traffic_gb=5000)
    assert resp.status_code == 403, resp.text


def test_premium_reenabled_restores_sales(db, client, users):
    """重新开售后 Premium 恢复可买。"""
    c, current = client
    owner = users["owner"]
    _fund(db, owner, 100)
    resp = _preview(c, current, owner, line="premium", traffic_gb=5000)
    assert resp.status_code == 403, resp.text
    _set_config(db, "team_premium_enabled", "true")
    resp = _preview(c, current, owner, line="premium", traffic_gb=5000)
    assert resp.status_code == 200, resp.text
    resp = _buy(c, current, owner, line="premium", traffic_gb=5000,
                idempotency_key="reenabled-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["plan_line"] == "premium"
