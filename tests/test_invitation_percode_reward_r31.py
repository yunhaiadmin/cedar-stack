# tests/test_invitation_percode_reward_r31.py
"""r31：邀请码「每码专属奖励」接通 + 撞号重试回归测试。

2026-11-13 货币下线后口径：码上的 reward_points（积分专属奖励）不再
发放——promotion.grant 遇到积分口径直接跳过；邀请人奖励只剩推广配置
的会员天数型。原两条发奖用例（码积分覆盖全局 / 0 跟随全局）已被
test_percode_points_reward_no_longer_granted 取代：钉住「积分不发、
天数照发、台账无 points 行」。
- test_mycode_collision_retry：_generate_invite_code 首次返回已存在的码 →
  IntegrityError 触发重试 → 最终拿到新码、200 不抛。
- test_update_request_schema_accepts_reward_points：更新/生成请求模型仍
  接受 reward_points 字段（存量码兼容，发放侧忽略）。
"""

from __future__ import annotations

import sys
import uuid

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import invitation as invite_mod
from backend.api.invitation import apply_invitation


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def _user(db, username: str) -> models.WebUser:
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_code(db, inviter_id: int, code_str: str, reward_points: int = 0):
    code = models.InvitationCode(
        code=code_str,
        user_id=inviter_id,
        is_active=True,
        max_uses=0,
        use_count=0,
        reward_points=reward_points,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


@pytest.fixture()
def db():
    engine = _engine()
    models.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    session = SessionLocal()
    # store 热读缓存是进程级全局的（60 秒 TTL）：不失效的话全量跑时
    # 别的用例缓存过的 promotion_* 值会串进本文件（单跑不复现）
    from backend.integrations import store

    store.invalidate()
    try:
        yield session
    finally:
        session.close()
        store.invalidate()


def _seed_promotion_days(db, days="5"):
    """现行「全局邀请人奖励」= 推广配置（会员天数）"""
    db.add(models.SystemConfig(key="promotion_reward_enabled", value="true"))
    db.add(models.SystemConfig(key="promotion_reward_type", value="days"))
    db.add(models.SystemConfig(key="promotion_reward_days", value=days))


def test_percode_points_reward_no_longer_granted(db):
    """码 reward_points=300（积分口径）整笔跳过：不发积分、也不叠加全局
    天数（override 优先语义不变，只是积分奖励已不存在）；码 reward_points=0
    时跟随全局天数照常发放。"""
    _seed_promotion_days(db)
    db.add(models.SystemConfig(key="invitation_enabled", value="true"))
    db.commit()

    tag = uuid.uuid4().hex[:8]
    inviter = _user(db, f"per-inviter-{tag}")
    invitee = _user(db, f"per-invitee-{tag}")
    code = _make_code(db, inviter.id, f"PER{tag.upper()}", reward_points=300)

    res = apply_invitation(db, invitee, code.code)
    db.commit()

    assert res["applied"] is True
    assert res["inviter_reward"] == 0
    rec = db.query(models.InvitationRecord).filter_by(invitee_id=invitee.id).one()
    assert rec.reward_points == 0
    assert db.query(models.PointsLog).filter_by(user_id=inviter.id).count() == 0
    assert db.query(models.PromotionReward).filter_by(invitee_id=invitee.id).count() == 0
    db.refresh(inviter)
    assert int(inviter.points or 0) == 0

    # 对照：无专属奖励的码 → 全局天数 5 天照常发
    invitee2 = _user(db, f"per-invitee2-{tag}")
    code2 = _make_code(db, inviter.id, f"FRE{tag.upper()}", reward_points=0)
    res2 = apply_invitation(db, invitee2, code2.code)
    db.commit()
    assert res2["inviter_reward"] == 5
    reward = db.query(models.PromotionReward).filter_by(invitee_id=invitee2.id).one()
    assert reward.reward_type == "days" and reward.reward_value == 5


def test_mycode_collision_retry(db, monkeypatch):
    """首次生成撞上已存在的码 → IntegrityError → 重试一次拿到新码，不抛 500"""
    tag = uuid.uuid4().hex[:8]
    user = _user(db, f"collide-user-{tag}")
    # 撞号目标码归另一个用户所有：my_code 查「我的码」时看不到它，
    # 但唯一索引在 commit 时会拦下同码插入 → 触发重试路径
    other = _user(db, f"collide-other-{tag}")
    existing = _make_code(db, other.id, f"COLLIDE{tag.upper()}")

    calls = {"n": 0}
    real_gen = invite_mod._generate_invite_code

    def flaky_gen(session):
        calls["n"] += 1
        if calls["n"] == 1:
            return existing.code  # 第一次故意返回已存在的码 → commit 时唯一索引拦下
        return real_gen(session)

    monkeypatch.setattr(invite_mod, "_generate_invite_code", flaky_gen)

    out = invite_mod.my_code(current_user=user, db=db)
    assert out["code"] != existing.code
    assert calls["n"] == 2
    row = db.query(models.InvitationCode).filter_by(
        user_id=user.id, code=out["code"]).one()
    assert int(row.reward_points or 0) == 0  # 自助码语义：跟随全局


def test_update_request_schema_accepts_reward_points():
    """更新请求的 pydantic 模型接受 reward_points（None=不改 / 0=跟随全局）"""
    from backend.api.admin_ops import InvitationCodeUpdateRequest, InvitationCodeGenerateRequest

    u = InvitationCodeUpdateRequest(reward_points=300)
    assert u.reward_points == 300
    g = InvitationCodeGenerateRequest(owner_user_id=1, reward_points=0)
    assert g.reward_points == 0
    d = InvitationCodeUpdateRequest()
    assert d.reward_points is None
