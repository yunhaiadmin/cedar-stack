"""播放门禁（2026-11-22 改版：有订阅就能播，不再要求绑盘）。

本文件钉改版后的 ensure 本体口径（判定单点见
test_playback_gate_subscription.py 的完整矩阵，此处保留绑盘时代
用例的回归对照）：
- 无订阅一律 403 + 订阅引导文案：无绑盘 / 仅 invalid / 仅 expired /
  仅已下线 provider（115/夸克）存量行 / 只有 active Drive 绑盘，
  结论一致——绑盘不再是播放资格，文案也不再分下线专用句；
- 有订阅放行：与名下有没有绑盘无关；
- 浏览不受影响：门禁判定只读资格，不碰条目/浏览状态；
- 已下发链接不追溯：带有效 play_sign 的请求放行，签名无效/防盗链
  关时仍按门禁判定。
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


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(base_models.ServerRealm(
        id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    yield session
    session.close()


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    return user


def _binding(db, user_id, status, provider="drive", label="盘"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status,
    )
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label=label,
        credential_id=cred.id, status=status,
    )
    db.add(binding)
    db.commit()
    return binding


def _subscription(db, user_id):
    plan = base_models.SubscriptionPlan(
        name=f"plan-{user_id}", price=1, duration_days=30)
    db.add(plan)
    db.flush()
    now = datetime.now()
    sub = base_models.UserSubscription(
        user_id=user_id, plan_id=plan.id, realm_id=1,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), status="active")
    db.add(sub)
    db.commit()
    return sub


def _assert_blocked(db, user_id):
    user = db.query(base_models.WebUser).filter(
        base_models.WebUser.id == user_id).first()
    with pytest.raises(HTTPException) as excinfo:
        gate.ensure_binding_playback_allowed(db, user)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == gate.GATE_MESSAGE


def test_gate_rejects_user_without_bindings(db):
    user = _user(db, "u1")
    _assert_blocked(db, user.id)


def test_gate_rejects_invalid_only(db):
    user = _user(db, "u1")
    _binding(db, user.id, "invalid")
    _assert_blocked(db, user.id)


def test_gate_rejects_expired_only(db):
    user = _user(db, "u1")
    _binding(db, user.id, "expired")
    _assert_blocked(db, user.id)


def test_gate_blocks_active_drive_without_subscription(db):
    # 改版核心：active Drive 绑盘不再是播放资格，无订阅照样 403。
    user = _user(db, "u1")
    _binding(db, user.id, "active", provider="drive")
    _assert_blocked(db, user.id)


def test_gate_blocks_active_pan115_only(db):
    # 已下线 provider 存量行：改版后与从未绑定同口径——403 且只出
    # 订阅引导文案，不再出下线专用句（门禁已不看绑定）。
    user = _user(db, "u1")
    _binding(db, user.id, "active", provider="pan115")
    _assert_blocked(db, user.id)


def test_gate_blocks_active_quark_only(db):
    user = _user(db, "u1")
    _binding(db, user.id, "active", provider="quark")
    _assert_blocked(db, user.id)


def test_gate_allows_subscribed_without_any_binding(db):
    user = _user(db, "u1")
    _subscription(db, user.id)
    gate.ensure_binding_playback_allowed(db, user)


def test_gate_allows_subscribed_with_bindings(db):
    user = _user(db, "u1")
    _subscription(db, user.id)
    _binding(db, user.id, "active", provider="pan115", label="旧115")
    _binding(db, user.id, "active", provider="drive", label="谷歌盘")
    gate.ensure_binding_playback_allowed(db, user)


def test_gate_ignores_other_users_bindings(db):
    owner = _user(db, "owner")
    stranger = _user(db, "stranger")
    _binding(db, owner.id, "active")
    _assert_blocked(db, stranger.id)


def _signed_request():
    return types.SimpleNamespace(
        query_params={"sign": "s", "exp": "9999999999", "uid": "7"},
        path_params={"item_id": "guid-1"},
    )


def test_gate_exempts_issued_play_sign(db, monkeypatch):
    user = _user(db, "u1")
    monkeypatch.setattr(
        "backend.emby_server.play_sign.hotlink_enabled", lambda db: True)
    monkeypatch.setattr(
        "backend.emby_server.play_sign.verify_play_sign",
        lambda uid, item_id, exp, sign: True)
    gate.ensure_binding_playback_allowed(db, user, _signed_request())


def test_gate_rejects_invalid_play_sign(db, monkeypatch):
    user = _user(db, "u1")
    monkeypatch.setattr(
        "backend.emby_server.play_sign.hotlink_enabled", lambda db: True)
    monkeypatch.setattr(
        "backend.emby_server.play_sign.verify_play_sign",
        lambda uid, item_id, exp, sign: False)
    with pytest.raises(HTTPException) as excinfo:
        gate.ensure_binding_playback_allowed(db, user, _signed_request())
    assert excinfo.value.status_code == 403


def test_gate_rejects_when_hotlink_disabled(db, monkeypatch):
    user = _user(db, "u1")
    monkeypatch.setattr(
        "backend.emby_server.play_sign.hotlink_enabled", lambda db: False)
    monkeypatch.setattr(
        "backend.emby_server.play_sign.verify_play_sign",
        lambda uid, item_id, exp, sign: True)
    with pytest.raises(HTTPException) as excinfo:
        gate.ensure_binding_playback_allowed(db, user, _signed_request())
    assert excinfo.value.status_code == 403


def test_gate_ignores_item_state(db):
    """浏览不受影响：门禁只看资格，条目状态不改变判定结果。"""
    lib = em.Library(guid="lib1", name="lib1")
    db.add(lib)
    db.commit()
    with_path = em.MediaItem(
        guid="g1", library_id=lib.id, item_type="movie", name="有路径",
        file_path="mount://1/f.mkv")
    without_path = em.MediaItem(
        guid="g2", library_id=lib.id, item_type="movie", name="无路径",
        file_path="")
    db.add_all([with_path, without_path])
    db.commit()
    subbed = _user(db, "subbed")
    _subscription(db, subbed.id)
    plain = _user(db, "plain")
    # 同一门禁函数在两种条目状态下结论不变：只看资格，不看条目。
    gate.ensure_binding_playback_allowed(db, subbed)
    gate.ensure_binding_playback_allowed(db, subbed)
    _assert_blocked(db, plain.id)
    _assert_blocked(db, plain.id)
