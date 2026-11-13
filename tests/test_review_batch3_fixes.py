"""审查修复回归（第三批）：redeem 双花、红包重放幂等。"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.database import SessionLocal, init_db
from backend import models

init_db()


def _make_user(username: str, points: int) -> models.WebUser:
    db = SessionLocal()
    old = db.query(models.WebUser).filter(models.WebUser.username == username).first()
    if old:
        db.query(models.WebUser).filter(models.WebUser.id == old.id).delete()
        db.commit()
    u = models.WebUser(username=username, password_hash="x", is_active=True,
                       points=points)
    db.add(u)
    db.commit()
    db.refresh(u)
    uid = u.id
    db.close()
    db2 = SessionLocal()
    user = db2.query(models.WebUser).filter(models.WebUser.id == uid).first()
    return user, db2


def test_redeem_welfare_atomic_spend():
    """redeem_welfare 用原子扣减：余额不足时抛 ValueError 且余额不变。"""
    from backend import points
    user, db = _make_user("review_redeem_u", 50)
    try:
        try:
            points.redeem_welfare(db, user, 7)  # 默认 100 分，不够
            raise AssertionError("应抛 ValueError")
        except ValueError as e:
            assert "能量不足" in str(e)
        db.refresh(user)
        assert int(user.points) == 50, "扣款失败时余额不应变化"
    finally:
        db.close()
