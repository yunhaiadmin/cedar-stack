"""邀请码位长对齐（2026-11-18）：新生成码 6 位（对齐友站码面），
存量 8 位码按字符串等值查、继续有效（不迁移、不拦截）。

锚点：
- _generate_invite_code 只产 6 位、字符集不变（A-Z0-9）；
- my-code 自动生成与换码（regenerate）走同一生成器，新码都是 6 位；
- apply_invitation 对存量 8 位码照常建立关系（查码不按位长过滤）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-invite-code-length-key-32chars!")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import invitation as invitation_api
from backend.integrations import store


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    store.invalidate()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        store.invalidate()


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_generated_invite_code_is_six_chars(db):
    for _ in range(20):
        code = invitation_api._generate_invite_code(db)
        assert len(code) == 6
        assert set(code) <= set(invitation_api.CODE_ALPHABET)


def test_my_code_and_regenerate_produce_six_chars(db):
    user = _user(db, "six-user")
    first = invitation_api.my_code(user, db)
    assert len(first["code"]) == 6
    out = invitation_api.regenerate_my_code(user, db)
    assert len(out["code"]) == 6
    assert out["code"] != first["code"]


def test_legacy_eight_char_code_still_applies(db):
    inviter = _user(db, "legacy-inviter")
    legacy = models.InvitationCode(
        code="LEGACY88", user_id=inviter.id,
        is_active=True, max_uses=0, use_count=0, reward_points=0,
    )
    db.add(legacy)
    db.commit()

    newcomer = _user(db, "legacy-newcomer")
    result = invitation_api.apply_invitation(db, newcomer, "LEGACY88")
    assert result["applied"] is True
    db.commit()

    record = db.query(models.InvitationRecord).filter_by(invitee_id=newcomer.id).one()
    assert record.inviter_id == inviter.id
    refreshed = db.query(models.InvitationCode).filter_by(code="LEGACY88").one()
    assert refreshed.use_count == 1
