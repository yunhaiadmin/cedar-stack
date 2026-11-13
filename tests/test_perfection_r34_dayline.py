"""r34 后端完美迭代：用户权益类「日/月界线」口径统一收口（Asia/Shanghai 自然日）。

保留：F1 的 economy._today_start 基础口径、F5 求片每日额度
media_seek.used_today、F6 求片月度额度 media_seek.used_this_month /
welfare_requests._month_start。
已删（2026-11-13 货币下线）：F1 签到写入、F2 聊天积分、F3/F4 活力、
F7/F8 签到统计与趋势签到系列的用例。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import media_seek, models
from backend.api import economy


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _make_user(db, username="u1", **kw):
    u = models.WebUser(username=username, password_hash="", points=0,
                       is_active=True, **kw)
    db.add(u)
    db.commit()
    return u


def _set_cfg(db, key, value):
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


def _shanghai_today_start_utc():
    """测试侧独立算出的期望值：上海自然日 00:00 → UTC naive。"""
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return now_sh.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------- F1: 今日起点口径 ----------------

def test_today_start_is_shanghai_midnight_utc_frame():
    ts = economy._today_start()
    assert ts.tzinfo is None
    assert ts == _shanghai_today_start_utc()
    sh = ts.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    assert (sh.hour, sh.minute, sh.second, sh.microsecond) == (0, 0, 0, 0)


def _beijing_4am_today_utc():
    """北京时间今天 04:00 对应的 UTC naive 时间（旧口径下算昨天、新口径下算今天）"""
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    four = now_sh.replace(hour=4, minute=0, second=0, microsecond=0)
    return four.astimezone(timezone.utc).replace(tzinfo=None)


def test_media_seek_used_today_uses_shanghai_day(db):
    u = _make_user(db, username="u6")
    db.add(models.MovieRequest(user_id=u.id, movie_name="t",
                               created_at=_beijing_4am_today_utc()))
    db.commit()
    assert media_seek.used_today(db, u.id) == 1


def test_month_start_utc_naive_frame():
    ms = economy._month_start_utc_naive()
    assert ms.tzinfo is None
    sh = ms.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    assert (sh.day, sh.hour, sh.minute) == (1, 0, 0)


def test_media_seek_used_this_month_uses_shanghai_month(db):
    u = _make_user(db, username="u7")
    now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    first_4am = now_sh.replace(day=1, hour=4, minute=0, second=0, microsecond=0)
    created = first_4am.astimezone(timezone.utc).replace(tzinfo=None)
    db.add(models.MovieRequest(user_id=u.id, movie_name="t", created_at=created))
    db.commit()
    assert media_seek.used_this_month(db, u.id) == 1


# ---------------- F7/F8: 管理后台 ----------------

