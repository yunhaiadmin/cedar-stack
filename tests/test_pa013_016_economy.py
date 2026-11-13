"""完美审计 R8（B8 经济）PA-013 回归测试。

- PA-013：不活跃扫描须把签到与播放计为活跃信号，不能只认网站登录。

（PA-014/015/016 是签到发活力/签到状态口径，已随签到与活力货币于
2026-11-13 下线删除，本文件只保留 PA-013。）
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models, welfare_expiry
from backend.emby_server import models as em_models
from backend.integrations import store as _store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate("welfare_inactive_days")
    # 活力配置走进程内 TTL 热缓存（按 key 全局共享、不区分数据库）：
    # 全量跑时前序测试文件在本库外写过的值会串进来，故每个用例前失效，
    # 与 test_welfare_admin_config.py 的 fixture 同口径。
    _store.invalidate("welfare_inactive_days")
    yield s
    s.close()


def _user(db, name, **kw):
    u = models.WebUser(username=name, password_hash="x", is_welfare=True, **kw)
    db.add(u)
    db.commit()
    return u


def _seed_checkin_cfg(db, extra=None):
    cfg = {
        "checkin_enabled": "true",
        "checkin_base_min": "3",
        "checkin_base_max": "3",
        "checkin_streak_bonus": "1",
        "checkin_streak_max_bonus": "5",
        "checkin_penalty_pct": "0",
        "checkin_crit_pct": "0",
        "checkin_milestone_points": "0",
        "checkin_full_attendance_points": "0",
    }
    if extra:
        cfg.update(extra)
    for k, v in cfg.items():
        db.add(models.SystemConfig(key=k, value=str(v)))
    db.commit()


# ---------------- PA-013 ----------------

def test_pa013_checkin_yesterday_keeps_welfare(db):
    """40 天未网站登录、昨天刚签到 → 不取消公益身份。"""
    now = datetime.now()
    u = _user(db, "checkin_active", last_login_at=now - timedelta(days=40),
              welfare_expires_at=now + timedelta(days=365),
              welfare_granted_at=now - timedelta(days=40))
    db.add(models.CheckinRecord(user_id=u.id, checkin_date=now - timedelta(days=1),
                                points_awarded=0, streak=40))
    db.commit()
    res = welfare_expiry.check_inactive_users(db)
    assert res["disabled"] == []
    assert db.query(models.WebUser).filter_by(id=u.id).one().is_welfare is True


def test_pa013_recent_playback_keeps_welfare(db):
    """40 天未网站登录、5 天前有播放会话 → 不取消公益身份。"""
    now = datetime.now()
    u = _user(db, "playback_active", last_login_at=now - timedelta(days=40),
              welfare_expires_at=now + timedelta(days=365),
              welfare_granted_at=now - timedelta(days=40))
    db.add(em_models.PlaybackSession(
        user_id=u.id, session_key="pa013-sess",
        start_time=now - timedelta(days=40),
        last_update_at=now - timedelta(days=5)))
    db.commit()
    res = welfare_expiry.check_inactive_users(db)
    assert res["disabled"] == []
    assert db.query(models.WebUser).filter_by(id=u.id).one().is_welfare is True


def test_pa013_truly_inactive_still_disabled(db):
    """40 天无任何信号 → 仍取消公益身份（既有行为不回归）。"""
    now = datetime.now()
    u = _user(db, "truly_inactive", last_login_at=now - timedelta(days=40),
              welfare_expires_at=now + timedelta(days=365),
              welfare_granted_at=now - timedelta(days=40))
    res = welfare_expiry.check_inactive_users(db)
    assert res["disabled"] == [u.id]
    assert db.query(models.WebUser).filter_by(id=u.id).one().is_welfare is False
