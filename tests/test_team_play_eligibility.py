# -*- coding: utf-8 -*-
"""团队批 4：播放资格接团队权益（补 PR #322 缺口）专项测试。

缺口：#322 购买履约把被抵扣的旧个人订阅置到期，但 can_play 没接
团队资格——团长购团后本人与成员播放会断。本批口径：

- 播放资格（subscriptions.can_play 唯一真源）：active 且未过期的
  团队，团长与成员均可播（资格来源＝团队套餐）；pending_activation
  （未开通播放权限）/dissolved/过期一律不可播；非成员不受影响。
- 流量资格（traffic_quota.usage_summary 唯一判定口）：团队有效时
  配额＝团队套餐 plan_traffic_bytes、已用＝团队池（团长＋全部成员
  计量日账）在团队周期内的合计（与 #322 归账钩子口径一致）。
- 家庭子账号上限：active_children_count 不计 team_members 归属
  账号（团队名额由 plan_member_limit 自管，两套产品不混算）。

隔离：内存 SQLite（StaticPool），不碰真库。
"""
import os
import types

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import entitlements, models, sub_accounts, subscriptions
from backend import teams as teams_service
from backend import traffic_gate, traffic_quota
from backend import wallet as wallet_service
from backend.emby_server import models as em

GB = 1024 ** 3
PW = "secret1"


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
    entitlements._PLAN_SNAPSHOT_CACHE.clear()
    entitlements._USER_PLAN_CACHE.clear()
    traffic_gate.invalidate_cache()
    yield
    entitlements._PLAN_SNAPSHOT_CACHE.clear()
    entitlements._USER_PLAN_CACHE.clear()
    traffic_gate.invalidate_cache()


# ---------------- 造数帮手 ----------------

def _user(db, username, **kw):
    user = models.WebUser(
        username=username, password_hash="x", is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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


def _personal_sub(db, user, *, price="36.00", started_days_ago=15,
                  total_days=30, realm_id=None):
    """生效中的个人订阅＋已支付订单（团队购买抵扣的输入，同 #322）。"""
    plan = models.SubscriptionPlan(
        name="个人版", price=Decimal(price), duration_days=total_days,
        is_active=True)
    db.add(plan)
    db.commit()
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm_id,
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
    entitlements.invalidate(user.id)
    return plan, sub


def _buy_team(db, owner, *, traffic_gb=1000, key="buy-1", activate=False):
    """服务层直购团队（Global，起订改到 100GB；1000GB→子账户上限 10）。"""
    _set_config(db, "team_global_min_traffic_gb", "100")
    _fund(db, owner, 50)
    result = teams_service.purchase(
        db, owner, line="global", traffic_gb=traffic_gb, months=1,
        idempotency_key=key)
    team = teams_service.get_owned_team(db, owner.id)
    assert team is not None
    assert result["team"]["id"] == team.id
    if activate:
        teams_service.activate_team(db, team)
        db.refresh(team)
    return team


def _usage(db, user_id, *, relay=0, local=0):
    """给某人今天的日账累加字节（日账 (user_id, day) 唯一，累加式）。"""
    today = datetime.now().date()
    row = (db.query(em.TrafficUsageDaily)
           .filter(em.TrafficUsageDaily.user_id == user_id,
                   em.TrafficUsageDaily.day == today)
           .first())
    if row is None:
        db.add(em.TrafficUsageDaily(
            user_id=user_id, day=today, relay_bytes=relay,
            local_bytes=local, direct_plays=0))
    else:
        row.relay_bytes = int(row.relay_bytes or 0) + relay
        row.local_bytes = int(row.local_bytes or 0) + local
    db.commit()


# ---------------- 播放资格：团长 ----------------

def test_owner_plays_via_team_after_personal_sub_terminated(db):
    """核心缺口：购团把旧个人订阅置到期后，团长凭团队套餐可播。"""
    owner = _user(db, "towner")
    _plan, sub = _personal_sub(db, owner)
    team = _buy_team(db, owner, activate=True)
    db.refresh(sub)
    # #322 既有口径：被抵扣的旧个人订阅在履约时置到期
    assert sub.end_date <= datetime.now()
    assert subscriptions.has_active_subscription(db, owner.id) is False
    assert team.status == "active"
    assert subscriptions.can_play(db, owner) is True
    subscriptions.ensure_playback_allowed(db, owner)  # 不抛即放行


def test_pending_activation_blocks_owner_and_member(db):
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=False)
    assert team.status == "pending_activation"
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    assert subscriptions.can_play(db, owner) is False
    assert subscriptions.can_play(db, member) is False
    with pytest.raises(HTTPException) as exc:
        subscriptions.ensure_playback_allowed(db, member)
    assert exc.value.status_code == 403


def test_member_plays_via_team_when_active(db):
    owner = _user(db, "towner")
    _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    assert member.parent_user_id == owner.id
    assert subscriptions.can_play(db, member) is True


def test_expired_team_blocks_play(db):
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    assert subscriptions.can_play(db, member) is True
    team.plan_expires_at = datetime.now() - timedelta(days=1)
    db.commit()
    assert subscriptions.can_play(db, owner) is False
    assert subscriptions.can_play(db, member) is False


def test_dissolved_team_blocks_play(db):
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    team.status = "dissolved"
    db.commit()
    assert subscriptions.can_play(db, owner) is False
    assert subscriptions.can_play(db, member) is False


# ---------------- 播放资格：回归 ----------------

def test_non_member_without_subscription_cannot_play(db):
    bystander = _user(db, "bystander")
    assert subscriptions.can_play(db, bystander) is False


def test_personal_subscription_user_still_plays(db):
    user = _user(db, "solo")
    _personal_sub(db, user)
    assert subscriptions.can_play(db, user) is True


def test_family_sub_account_still_plays_via_parent(db):
    parent = _user(db, "parent")
    plan = models.SubscriptionPlan(
        name="家庭版", price=Decimal("36.00"), duration_days=30,
        is_active=True, entitlements={"sub_account_limit": 2})
    db.add(plan)
    db.commit()
    now = datetime.now()
    db.add(models.UserSubscription(
        user_id=parent.id, plan_id=plan.id, realm_id=None,
        start_date=now, end_date=now + timedelta(days=30),
        status="active"))
    db.commit()
    entitlements.invalidate(parent.id)
    child = sub_accounts.create_sub_account(db, parent, "kid1", PW)
    assert subscriptions.can_play(db, child) is True


# ---------------- 家庭上限排除团队成员 ----------------

def test_team_members_do_not_consume_family_limit(db):
    owner = _user(db, "towner")
    _buy_team(db, owner, activate=True)  # 子账户上限 10
    members = [
        teams_service.create_member(
            db, owner, username=f"tmember{i}", password=PW)
        for i in range(3)
    ]
    assert len(members) == 3
    # 团长再持家庭版（sub_account_limit=2）：团队成员不占家庭名额
    plan = models.SubscriptionPlan(
        name="家庭版", price=Decimal("36.00"), duration_days=30,
        is_active=True, entitlements={"sub_account_limit": 2})
    db.add(plan)
    db.commit()
    now = datetime.now()
    db.add(models.UserSubscription(
        user_id=owner.id, plan_id=plan.id, realm_id=None,
        start_date=now, end_date=now + timedelta(days=30),
        status="active"))
    db.commit()
    entitlements.invalidate(owner.id)

    assert sub_accounts.active_children_count(db, owner.id) == 0
    sub_accounts.create_sub_account(db, owner, "kid1", PW)
    sub_accounts.create_sub_account(db, owner, "kid2", PW)
    assert sub_accounts.active_children_count(db, owner.id) == 2
    with pytest.raises(sub_accounts.SubAccountError) as exc:
        sub_accounts.create_sub_account(db, owner, "kid3", PW)
    assert exc.value.code == "limit_reached"

    payload = sub_accounts.management_payload(db, owner)
    assert payload["used"] == 2
    assert payload["limit"] == 2
    item_names = {item["username"] for item in payload["items"]}
    assert item_names == {"kid1", "kid2"}


# ---------------- 流量资格：团队池 ----------------

def test_team_traffic_quota_is_team_pool(db):
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=True)  # 1000GB 团队套餐
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    quota = 1000 * GB
    assert team.plan_traffic_bytes == quota

    # 池共享：成员 600GB + 团长 399GB，合计未满 → 两人都不拦
    _usage(db, member.id, relay=600 * GB)
    _usage(db, owner.id, relay=399 * GB)
    summary = traffic_quota.usage_summary(db, member.id)
    assert summary["enabled"] is True
    assert summary["quota_bytes"] == quota
    assert summary["used_bytes"] == 999 * GB
    assert summary["exhausted"] is False
    assert traffic_gate.user_traffic_exhausted(db, member.id) is False

    # 成员再用 2GB → 池合计 1001GB 越额 → 团长与成员同拦（池口径）
    _usage(db, member.id, local=2 * GB)
    traffic_gate.invalidate_cache()
    summary = traffic_quota.usage_summary(db, member.id)
    assert summary["used_bytes"] == 1001 * GB
    assert summary["exhausted"] is True
    assert traffic_quota.usage_summary(db, owner.id)["exhausted"] is True
    assert traffic_gate.user_traffic_exhausted(db, member.id) is True
    assert traffic_gate.user_traffic_exhausted(db, owner.id) is True


# ---------------- 查看资格（view_grant）：团队 ----------------

def test_team_owner_view_grant_via_team(db):
    """购团把旧个人订阅置到期后，团长凭团队套餐仍可查看 Emby 账号/线路。"""
    owner = _user(db, "towner")
    _personal_sub(db, owner)
    _buy_team(db, owner, activate=True)
    # 资格来源只能是团队：个人订阅已被抵扣置到期
    assert subscriptions.has_active_subscription(db, owner.id) is False
    granted, unlock = subscriptions.view_grant(db, owner)
    assert granted is True
    assert unlock is None


def test_team_member_view_grant_via_team(db):
    owner = _user(db, "towner")
    _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    granted, unlock = subscriptions.view_grant(db, member)
    assert granted is True
    assert unlock is None


def test_pending_team_no_view_grant(db):
    owner = _user(db, "towner")
    _buy_team(db, owner, activate=False)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    assert subscriptions.view_grant(db, owner) == (False, None)
    assert subscriptions.view_grant(db, member) == (False, None)


def test_expired_team_no_view_grant(db):
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    assert subscriptions.view_grant(db, member)[0] is True
    team.plan_expires_at = datetime.now() - timedelta(days=1)
    db.commit()
    assert subscriptions.view_grant(db, owner) == (False, None)
    assert subscriptions.view_grant(db, member) == (False, None)


def test_no_team_user_view_grant_unchanged(db):
    """无团队用户行为不变：无订阅不放行、有个人订阅放行。"""
    bystander = _user(db, "bystander")
    assert subscriptions.view_grant(db, bystander) == (False, None)
    solo = _user(db, "solo")
    # view_grant 走 _scoped 口径（面板当前服=1），存量用例同口径绑服造数
    _personal_sub(db, solo, realm_id=1)
    assert subscriptions.view_grant(db, solo) == (True, None)


def test_pending_team_grants_no_traffic_quota(db):
    owner = _user(db, "towner")
    _buy_team(db, owner, activate=False)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    _usage(db, member.id, relay=500 * GB)
    summary = traffic_quota.usage_summary(db, member.id)
    # 未开通：不走团队池判定（回落个人口径，成员无个人额度）
    assert summary["quota_bytes"] != 1000 * GB
    assert summary["exhausted"] is False


def test_play_eligible_team_duck_typed_user(db):
    """回归（#322 CI 114 红同款）：usage_summary 的存量调用方传对象
    时，play_eligible_team 须按 .id 取值；裸 object() 取不到 id
    返回 None，不抛错。"""
    owner = _user(db, "towner")
    team = _buy_team(db, owner, activate=True)
    member = teams_service.create_member(
        db, owner, username="tmember1", password=PW)
    stub_owner = types.SimpleNamespace(id=owner.id, is_staff=False)
    stub_member = types.SimpleNamespace(id=member.id, is_staff=False)
    assert teams_service.play_eligible_team(db, stub_owner) is not None
    assert teams_service.play_eligible_team(db, stub_owner).id == team.id
    assert teams_service.play_eligible_team(db, stub_member) is not None
    assert teams_service.play_eligible_team(db, stub_member).id == team.id
    assert teams_service.play_eligible_team(db, owner.id) is not None
    assert teams_service.play_eligible_team(db, object()) is None
    bystander = _user(db, "bystander")
    assert teams_service.play_eligible_team(
        db, types.SimpleNamespace(id=bystander.id)) is None
