# -*- coding: utf-8 -*-
"""播放门禁改版（2026-11-22 用户亲令）：有订阅就能播，不再要求绑盘。

新口径矩阵（判定单点 playback_gate.has_play_grant → subscriptions.
view_grant，含团队资格、子账号随主账号）：
- 无订阅无绑盘 → 三口 403（ensure / ensure_for_item / 池判定入口），
  文案为订阅引导（gate.GATE_MESSAGE）；
- 有订阅无绑盘 → 可播（ensure 放行、PlaybackInfo 池判定入口放行）；
- 团队资格（团长/成员）无个人订阅 → 可播；
- 有绑盘无订阅 → 403（绑盘不再是资格）；
- staff 无订阅 → 403（管理员不豁免）；
- 订阅过期 → 403；
- 家庭子账号：主账号有订阅 → 子账号可播；
- 已下发 play_sign 消费 → 不追溯（无订阅也放行）；
- 签发（sign_allowed_for_item）：有订阅可签；无订阅不可签（绑盘
  也不行）；池分支同口径——池 ready 副本对无订阅用户不可签、
  对有订阅用户可签（pool_sign_allowed 内复核资格）。
"""

import types
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import playback_gate as gate
from backend.emby_server import pool_gate


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # view_grant 按当前服判定（缺省服 id=1，与生产同口径）
    session.add(base_models.ServerRealm(
        id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    yield session
    session.close()


def _user(db, name, **kw):
    user = base_models.WebUser(username=name, password_hash="x", **kw)
    db.add(user)
    db.commit()
    return user


def _binding(db, user_id, status="active", provider="drive"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label="盘",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _subscription(db, user_id, *, expired=False):
    plan = base_models.SubscriptionPlan(
        name=f"plan-{user_id}-{expired}", price=1, duration_days=30)
    db.add(plan)
    db.flush()
    now = datetime.now()
    if expired:
        start, end = now - timedelta(days=60), now - timedelta(days=30)
    else:
        start, end = now - timedelta(days=1), now + timedelta(days=30)
    sub = base_models.UserSubscription(
        user_id=user_id, plan_id=plan.id, realm_id=1, start_date=start,
        end_date=end, status="active")
    db.add(sub)
    db.commit()
    return sub


def _team(db, owner, *, with_member=None):
    team = base_models.Team(
        owner_id=owner.id, status="active",
        plan_expires_at=datetime.now() + timedelta(days=30))
    db.add(team)
    db.flush()
    db.add(base_models.TeamMember(
        team_id=team.id, user_id=owner.id, role="owner"))
    if with_member is not None:
        db.add(base_models.TeamMember(
            team_id=team.id, user_id=with_member.id, role="member"))
    db.commit()
    return team


def _item():
    return types.SimpleNamespace(id=1, size=0, file_path="mount://1/a.mkv")


def _blocked(db, user):
    with pytest.raises(HTTPException) as excinfo:
        gate.ensure_binding_playback_allowed(db, user)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE
    assert excinfo.value.detail == "开通订阅后即可播放"


# ---------------------------------------------------------------- ensure 本体


def test_no_subscription_no_binding_blocked(db):
    _blocked(db, _user(db, "u1"))


def test_subscription_without_binding_allowed(db):
    user = _user(db, "u1")
    _subscription(db, user.id)
    gate.ensure_binding_playback_allowed(db, user)


def test_binding_without_subscription_blocked(db):
    user = _user(db, "u1")
    _binding(db, user.id, "active")
    _blocked(db, user)


def test_subscription_and_binding_allowed(db):
    user = _user(db, "u1")
    _subscription(db, user.id)
    _binding(db, user.id, "active")
    gate.ensure_binding_playback_allowed(db, user)


def test_expired_subscription_blocked(db):
    user = _user(db, "u1")
    _subscription(db, user.id, expired=True)
    _blocked(db, user)


def test_staff_without_subscription_blocked(db):
    _blocked(db, _user(db, "boss", is_staff=True))


def test_team_owner_without_personal_subscription_allowed(db):
    owner = _user(db, "owner")
    _team(db, owner)
    gate.ensure_binding_playback_allowed(db, owner)


def test_team_member_without_personal_subscription_allowed(db):
    owner = _user(db, "owner")
    member = _user(db, "member", parent_user_id=owner.id)
    _team(db, owner, with_member=member)
    gate.ensure_binding_playback_allowed(db, member)


def test_sub_account_follows_main_subscription(db):
    main = _user(db, "main")
    _subscription(db, main.id)
    child = _user(db, "child", parent_user_id=main.id)
    gate.ensure_binding_playback_allowed(db, child)


def test_issued_play_sign_not_retroactive(db, monkeypatch):
    user = _user(db, "u1")
    monkeypatch.setattr(
        "backend.emby_server.play_sign.hotlink_enabled", lambda db: True)
    monkeypatch.setattr(
        "backend.emby_server.play_sign.verify_play_sign",
        lambda uid, item_id, exp, sign: True)
    request = types.SimpleNamespace(
        query_params={"sign": "s", "exp": "9999999999", "uid": "7"},
        path_params={"item_id": "guid-1"})
    gate.ensure_binding_playback_allowed(db, user, request)


def test_ensure_for_item_matches(db):
    plain = _user(db, "plain")
    with pytest.raises(HTTPException) as excinfo:
        gate.ensure_binding_playback_allowed_for_item(db, plain, _item())
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE
    subbed = _user(db, "subbed")
    _subscription(db, subbed.id)
    gate.ensure_binding_playback_allowed_for_item(db, subbed, _item())


# ---------------------------------------------------------------- 签发判定


def test_sign_allowed_matrix(db):
    item = _item()
    subbed = _user(db, "subbed")
    _subscription(db, subbed.id)
    bound_only = _user(db, "bound")
    _binding(db, bound_only.id, "active")
    plain = _user(db, "plain")
    assert gate.sign_allowed_for_item(db, subbed.id, item) is True
    assert gate.sign_allowed_for_item(db, bound_only.id, item) is False
    assert gate.sign_allowed_for_item(db, plain.id, item) is False
    assert gate.sign_allowed_for_item(db, None, item) is False


def test_pool_sign_requires_subscription(db, monkeypatch):
    monkeypatch.setattr(pool_gate, "pool_eligible", lambda db, u, i: "eligible")
    monkeypatch.setattr(
        pool_gate, "pool_serve_candidate", lambda db, i: object())
    item = _item()
    plain = _user(db, "plain")
    assert pool_gate.pool_sign_allowed(db, plain.id, item) is False
    assert gate.sign_allowed_for_item(db, plain.id, item) is False
    subbed = _user(db, "subbed")
    _subscription(db, subbed.id)
    assert pool_gate.pool_sign_allowed(db, subbed.id, item) is True
    assert gate.sign_allowed_for_item(db, subbed.id, item) is True


# ---------------------------------------------------------------- 池判定入口


def test_pool_entry_blocks_unsubscribed_even_pool_eligible(db, monkeypatch):
    monkeypatch.setattr(pool_gate, "pool_eligible", lambda db, u, i: "eligible")
    user = _user(db, "u1")
    with pytest.raises(HTTPException) as excinfo:
        pool_gate.ensure_playback_allowed_for_item(db, user, _item())
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE


def test_pool_entry_blocks_bound_without_subscription(db):
    user = _user(db, "u1")
    _binding(db, user.id, "active")
    with pytest.raises(HTTPException) as excinfo:
        pool_gate.ensure_playback_allowed_for_item(db, user, _item())
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE


def test_pool_entry_subscribed_unbound_goes_pool(db, monkeypatch):
    # ready 池副本在位（pool_sign_allowed 三齐）时才判 pool。
    monkeypatch.setattr(pool_gate, "pool_sign_allowed",
                        lambda db, uid, i: True)
    user = _user(db, "u1")
    _subscription(db, user.id)
    decision = pool_gate.ensure_playback_allowed_for_item(db, user, _item())
    assert decision.allowed is True
    assert decision.kind == "pool"


def test_pool_entry_subscribed_bound_stays_bound(db):
    user = _user(db, "u1")
    _subscription(db, user.id)
    _binding(db, user.id, "active")
    decision = pool_gate.ensure_playback_allowed_for_item(db, user, _item())
    assert decision.allowed is True
    assert decision.kind == "bound"
