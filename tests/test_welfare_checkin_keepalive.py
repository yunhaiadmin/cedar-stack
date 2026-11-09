"""保号 A+B：签到日免日耗（vitality.daily_deduct）＋活力保号（welfare_expiry）。

A（开关门控，默认关）：开关 vitality_checkin_skip_deduct_enabled 开时，
上一上海自然日有签到记录的用户，daily_deduct 本轮不扣；从不签到的照常扣。
开关关（默认）时不跳过任何人，行为与 #1449 之前一致；开关非法值按关处理。
B：已过期用户当前活力 >= 活力线（默认 3），直接续期 30 天、不扣分、
   记 vitality_renew；查看凭证不动；续期后重跑不重复续。
   （2026-11-09 改定：B 原为连签保号 streak_renew，已被活力口径取代；
   活力续期的完整口径用例见 tests/test_welfare_vitality_renew.py，
   本文件 B 段只保留与 A 同文件的关键路径回归。）
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models, vitality, welfare_expiry
from backend.api import economy
from backend.integrations import store as _store


def _db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(
        "vitality_enabled", "vitality_daily_cost", "vitality_max",
        vitality.CONFIG_LAST_DEDUCT_DATE,
        "welfare_grace_days", "welfare_vitality_renew_enabled",
        "welfare_vitality_renew_min", "welfare_streak_renew_days",
        "vitality_checkin_skip_deduct_enabled",
    )
    return s


def _frames():
    today = economy._today_start_utc_naive()
    return today, today - timedelta(days=1)


def _user(db, name, **kw):
    u = models.WebUser(username=name, password_hash="x", **kw)
    db.add(u)
    db.commit()
    return u


def _checkin(db, user_id, when, streak):
    db.add(models.CheckinRecord(user_id=user_id, checkin_date=when,
                                points_awarded=0, streak=streak))
    db.commit()


def _vitality(db, user_id):
    return db.query(models.WebUser).filter_by(id=user_id).one().vitality


def _set_skip_switch(db, value):
    db.add(models.SystemConfig(key="vitality_checkin_skip_deduct_enabled", value=value))
    db.commit()


# ---------------- A：签到日免日耗（开关门控，默认关） ----------------

def test_deduct_skips_yesterday_checkin_only():
    """开关开：昨天签过 → 不扣；没签过 → 照扣；非公益 → 本来就不扣；扣到 0 计 suspended。"""
    db = _db()
    _set_skip_switch(db, "true")
    today, yesterday = _frames()
    skip = _user(db, "skip", is_welfare=True, vitality=5)
    _checkin(db, skip.id, yesterday, 5)
    control = _user(db, "control", is_welfare=True, vitality=5)
    zero = _user(db, "zero", is_welfare=True, vitality=1)
    non_welfare = _user(db, "nonw", is_welfare=False, vitality=5)
    _checkin(db, non_welfare.id, yesterday, 5)
    res = vitality.daily_deduct(db)
    assert _vitality(db, skip.id) == 5
    assert _vitality(db, control.id) == 4
    assert _vitality(db, zero.id) == 0
    assert _vitality(db, non_welfare.id) == 5
    assert res["deducted"] == 2
    assert res["suspended"] == 1
    assert res["skipped_checkin"] == 1
    db.close()


def test_checkin_skip_deduct_holds_vitality_checkin_adds_none():
    """开关开：活力 2 的用户昨天签过 → 扣减跳过（stays 2）；今天再签到
    只发积分、不送活力（2026-11-09 新政：签到不再送活力，爬回阈值
    只能靠观影或积分兑换），活力仍是 2、vitality_gained=0。"""
    db = _db()
    _set_skip_switch(db, "true")
    today, yesterday = _frames()
    for key, val in [("checkin_enabled", "true"), ("checkin_base_min", "3"),
                     ("checkin_base_max", "3"), ("checkin_streak_bonus", "1"),
                     ("checkin_streak_max_bonus", "5"), ("checkin_penalty_pct", "0")]:
        db.add(models.SystemConfig(key=key, value=val))
    db.commit()
    u = _user(db, "climb", is_welfare=True, vitality=2, points=0)
    _checkin(db, u.id, yesterday, 5)
    vitality.daily_deduct(db)
    assert _vitality(db, u.id) == 2
    award = economy._do_checkin_core(db, u)
    assert award["points_awarded"] > 0
    assert award["vitality_gained"] == 0
    assert _vitality(db, u.id) == 2
    db.close()


def test_deduct_ignores_stale_checkin():
    """最近签到是 3 天前（stale）→ 不跳过，照常扣。"""
    db = _db()
    today, yesterday = _frames()
    u = _user(db, "stale", is_welfare=True, vitality=5)
    _checkin(db, u.id, today - timedelta(days=3), 40)
    res = vitality.daily_deduct(db)
    assert _vitality(db, u.id) == 4
    assert res["skipped_checkin"] == 0
    db.close()


def test_deduct_switch_off_deducts_yesterday_checkin():
    """开关默认关（无配行）：昨天签过也照扣，skipped_checkin 为 0，行为与 #1449 之前一致。"""
    db = _db()
    today, yesterday = _frames()
    u = _user(db, "off", is_welfare=True, vitality=5)
    _checkin(db, u.id, yesterday, 5)
    res = vitality.daily_deduct(db)
    assert _vitality(db, u.id) == 4
    assert res["deducted"] == 1
    assert res["skipped_checkin"] == 0
    db.close()


def test_deduct_switch_illegal_value_treated_as_off():
    """开关非法值（非 true）→ 按关处理：昨天签过也照扣。"""
    db = _db()
    _set_skip_switch(db, "yes")
    today, yesterday = _frames()
    u = _user(db, "bad", is_welfare=True, vitality=5)
    _checkin(db, u.id, yesterday, 5)
    res = vitality.daily_deduct(db)
    assert _vitality(db, u.id) == 4
    assert res["skipped_checkin"] == 0
    db.close()


# ---------------- B：活力保号 ----------------

def _expiry_user(db, name, expired_days_ago, **kw):
    now = datetime.now()
    kw.setdefault("vitality", 14)
    return _user(db, name, is_welfare=True,
                 welfare_expires_at=now - timedelta(days=expired_days_ago),
                 welfare_granted_at=now - timedelta(days=60), **kw)


def test_vitality_renew_expired_within_grace():
    """过期 3 天（保留期内）＋活力 14（达线）→ 续 30 天、零扣分、查看行不动。"""
    db = _db()
    before = datetime.now()
    u = _expiry_user(db, "renew", 3)
    db.add(models.EmbyViewUnlock(user_id=u.id, realm_id=1,
                                 expires_at=before + timedelta(days=5),
                                 points_spent=50))
    db.commit()
    view_before = db.query(models.EmbyViewUnlock).filter_by(user_id=u.id).one().expires_at
    res = welfare_expiry.check_welfare_expiry(db)
    after = datetime.now()
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True
    assert before + timedelta(days=30) <= row.welfare_expires_at <= after + timedelta(days=30)
    assert res["renewed"] == [u.id]
    assert u.id not in res["grace"] and u.id not in res["expired"]
    log = db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="vitality_renew").one()
    assert log.days == 30
    assert db.query(models.PointsLog).filter_by(user_id=u.id).count() == 0
    view_after = db.query(models.EmbyViewUnlock).filter_by(user_id=u.id).one()
    assert view_after.expires_at == view_before and view_after.points_spent == 50
    db.close()


def test_vitality_renew_beyond_grace_before_expire():
    """过期 10 天（超保留期）＋活力达线 → 仍续期，不走取消路径。"""
    db = _db()
    u = _expiry_user(db, "renew_late", 10)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True
    assert res["renewed"] == [u.id] and res["expired"] == []
    assert db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="auto_expire").count() == 0
    db.close()


def test_vitality_below_line_follows_old_path():
    """过期 3 天＋活力 2（<默认线 3）→ 原路径：保留期只记录，不取消不续期。"""
    db = _db()
    u = _expiry_user(db, "short", 3, vitality=2)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True
    assert res["grace"] == [u.id] and res["renewed"] == []
    assert db.query(models.WelfareGrantLog).filter_by(user_id=u.id).count() == 0
    db.close()


def test_high_streak_zero_vitality_does_not_renew():
    """过期 8 天＋连签 40 但活力 0 → 不续，走取消路径（连签不再保号）。"""
    db = _db()
    today, yesterday = _frames()
    u = _expiry_user(db, "streak_no_vitality", 8, vitality=0)
    _checkin(db, u.id, today, 40)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is False
    assert res["expired"] == [u.id] and res["renewed"] == []
    db.close()


def test_vitality_renew_idempotent():
    """续期后重跑 tick：不再过期、不重复续期、不多写日志。"""
    db = _db()
    u = _expiry_user(db, "idem", 3)
    first = welfare_expiry.check_welfare_expiry(db)
    assert first["renewed"] == [u.id]
    exp_after_first = db.query(models.WebUser).filter_by(id=u.id).one().welfare_expires_at
    second = welfare_expiry.check_welfare_expiry(db)
    assert second["renewed"] == [] and second["grace"] == [] and second["expired"] == []
    assert db.query(models.WebUser).filter_by(id=u.id).one().welfare_expires_at == exp_after_first
    assert db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="vitality_renew").count() == 1
    db.close()


def test_null_expiry_untouched():
    """expires_at 为 NULL（永不过期）＋活力满格 → 不受影响，无日志。"""
    db = _db()
    u = _user(db, "perm", is_welfare=True, welfare_expires_at=None, vitality=14)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True and row.welfare_expires_at is None
    assert res["renewed"] == []
    assert db.query(models.WelfareGrantLog).filter_by(user_id=u.id).count() == 0
    db.close()
