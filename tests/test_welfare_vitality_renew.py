"""活力保号（2026-11-09 用户改定，取代连签保号）：资格到期只看活力。

口径定版（不许改）：
- 公益资格到期时，用户当前活力 >= 活力线 → 自动续期、不扣积分，记
  WelfareGrantLog channel="vitality_renew"；活力不够 → 不续，走既有
  保留期（welfare_grace_days）／超期取消原路。
- 活力线 welfare_vitality_renew_min（默认 3，可配）；总开关
  welfare_vitality_renew_enabled（默认开，关了不自动续）；续期天数
  沿用既有键 welfare_streak_renew_days（默认 30，语义转过来，不新造）。
- 连签不再承担保号：无任何签到记录、活力达线照样续；连签攒得再高、
  活力不达线也不续。签到积分／连签计数（展示与里程碑）照旧。
- PR #3「每月断签保护」一并回滚：漏签 1 天照旧清零（streak=1），
  签到返回不再带 streak_forgiven/forgive_remaining，web_users 的
  streak_forgive_* 两列不再被写（列本身保留不用）。
- 查看凭证（EmbyViewUnlock）仍不保号，到期不动。
- 白名单活力豁免在 vitality 侧（不扣减、ensure_can_play 放行，已有
  test_whitelist_vitality_exempt.py 覆盖）；豁免用户活力天然达线，
  续期判定不特判白名单，直读活力列即可。
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

from backend import config_schema, models, welfare_expiry
from backend.api import economy, welfare_admin
from backend.integrations import store as _store
from backend.tg_bot import features


def _db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(
        "welfare_grace_days", "welfare_streak_renew_days",
        "welfare_vitality_renew_enabled", "welfare_vitality_renew_min",
    )
    return s


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if cfg:
        cfg.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()


def _user(db, name, **kw):
    u = models.WebUser(username=name, password_hash="x", **kw)
    db.add(u)
    db.commit()
    return u


def _expiry_user(db, name, expired_days_ago, **kw):
    now = datetime.now()
    return _user(db, name, is_welfare=True,
                 welfare_expires_at=now - timedelta(days=expired_days_ago),
                 welfare_granted_at=now - timedelta(days=60), **kw)


def _renew_logs(db, user_id):
    return db.query(models.WelfareGrantLog).filter_by(
        user_id=user_id, channel="vitality_renew").all()


# ==================== 1. 活力达线 → 续 ====================

def test_vitality_at_line_renews_within_grace():
    """过期 3 天（保留期内）＋活力恰达默认线 3 → 续 30 天、零扣分、查看行不动。"""
    db = _db()
    before = datetime.now()
    u = _expiry_user(db, "at_line", 3, vitality=3, points=100)
    db.add(models.EmbyViewUnlock(user_id=u.id, realm_id=1,
                                 expires_at=before + timedelta(days=5),
                                 points_spent=50))
    db.commit()
    view_before = db.query(models.EmbyViewUnlock).filter_by(
        user_id=u.id).one().expires_at
    res = welfare_expiry.check_welfare_expiry(db)
    after = datetime.now()
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert res["renewed"] == [u.id]
    assert u.id not in res["grace"] and u.id not in res["expired"]
    assert row.is_welfare is True
    assert before + timedelta(days=30) <= row.welfare_expires_at <= after + timedelta(days=30)
    logs = _renew_logs(db, u.id)
    assert len(logs) == 1 and logs[0].days == 30
    assert db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="streak_renew").count() == 0
    assert db.query(models.PointsLog).filter_by(user_id=u.id).count() == 0
    assert row.points == 100
    view_after = db.query(models.EmbyViewUnlock).filter_by(user_id=u.id).one()
    assert view_after.expires_at == view_before
    db.close()


def test_vitality_above_line_beyond_grace_renews():
    """过期 10 天（超保留期）＋活力 14 → 仍续期，不走取消路径。"""
    db = _db()
    u = _expiry_user(db, "above_line", 10, vitality=14)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True
    assert res["renewed"] == [u.id] and res["expired"] == []
    assert db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="auto_expire").count() == 0
    db.close()


def test_no_checkin_records_still_renews_by_vitality():
    """零签到记录＋活力达线 → 照样续（连签已不承担保号的直接证据）；
    白名单豁免用户活力恒满、无签到亦属此形态，天然达线。"""
    db = _db()
    u = _expiry_user(db, "no_checkin", 3, vitality=14)
    assert db.query(models.CheckinRecord).filter_by(user_id=u.id).count() == 0
    res = welfare_expiry.check_welfare_expiry(db)
    assert res["renewed"] == [u.id]
    assert len(_renew_logs(db, u.id)) == 1
    db.close()


def test_high_streak_low_vitality_does_not_renew():
    """连签再高、活力不达线也不续：过期 3 天＋连签 40＋活力 2 → 保留期。"""
    db = _db()
    today = economy._today_start_utc_naive()
    u = _expiry_user(db, "streak_only", 3, vitality=2)
    db.add(models.CheckinRecord(user_id=u.id, checkin_date=today,
                                points_awarded=0, streak=40))
    db.commit()
    res = welfare_expiry.check_welfare_expiry(db)
    assert res["renewed"] == [] and res["grace"] == [u.id]
    assert _renew_logs(db, u.id) == []
    db.close()


# ==================== 2. 活力不达线 → 不续，走原路 ====================

def test_vitality_below_line_follows_grace():
    """过期 3 天＋活力 2（<3）→ 保留期只记录，不取消不续期、无日志。"""
    db = _db()
    u = _expiry_user(db, "below_grace", 3, vitality=2)
    exp_before = u.welfare_expires_at
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is True
    assert row.welfare_expires_at == exp_before
    assert res["grace"] == [u.id] and res["renewed"] == []
    assert db.query(models.WelfareGrantLog).filter_by(user_id=u.id).count() == 0
    db.close()


def test_vitality_zero_beyond_grace_expires():
    """过期 10 天＋活力 0 → 走取消路径（auto_expire），不续。"""
    db = _db()
    u = _expiry_user(db, "zero_expire", 10, vitality=0)
    res = welfare_expiry.check_welfare_expiry(db)
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert row.is_welfare is False
    assert res["expired"] == [u.id] and res["renewed"] == []
    assert db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="auto_expire").count() == 1
    db.close()


# ==================== 3. 开关与活力线可配 ====================

def test_switch_off_does_not_renew():
    """总开关关：活力满格也不续，过期 3 天走保留期。"""
    db = _db()
    _set_config(db, "welfare_vitality_renew_enabled", "false")
    u = _expiry_user(db, "off", 3, vitality=14)
    res = welfare_expiry.check_welfare_expiry(db)
    assert res["renewed"] == [] and res["grace"] == [u.id]
    assert _renew_logs(db, u.id) == []
    db.close()


def test_custom_vitality_line_governs():
    """活力线改 5：活力 4 不续（保留期）、活力 5 才续。"""
    db = _db()
    _set_config(db, "welfare_vitality_renew_min", "5")
    short = _expiry_user(db, "line_short", 3, vitality=4)
    enough = _expiry_user(db, "line_enough", 3, vitality=5)
    res = welfare_expiry.check_welfare_expiry(db)
    assert res["grace"] == [short.id]
    assert res["renewed"] == [enough.id]
    db.close()


def test_custom_days_from_existing_days_key():
    """续期天数沿用 welfare_streak_renew_days：改 45 → 延期与日志 days 都是 45。"""
    db = _db()
    _set_config(db, "welfare_streak_renew_days", "45")
    before = datetime.now()
    u = _expiry_user(db, "days45", 3, vitality=10)
    res = welfare_expiry.check_welfare_expiry(db)
    after = datetime.now()
    row = db.query(models.WebUser).filter_by(id=u.id).one()
    assert res["renewed"] == [u.id]
    assert before + timedelta(days=45) <= row.welfare_expires_at <= after + timedelta(days=45)
    assert _renew_logs(db, u.id)[0].days == 45
    db.close()


def test_renew_idempotent():
    """续期后重跑 tick：不再过期、不重复续期、只一条 vitality_renew 日志。"""
    db = _db()
    u = _expiry_user(db, "idem", 3, vitality=9)
    first = welfare_expiry.check_welfare_expiry(db)
    assert first["renewed"] == [u.id]
    exp_after_first = db.query(models.WebUser).filter_by(
        id=u.id).one().welfare_expires_at
    second = welfare_expiry.check_welfare_expiry(db)
    assert second["renewed"] == [] and second["grace"] == [] and second["expired"] == []
    assert db.query(models.WebUser).filter_by(
        id=u.id).one().welfare_expires_at == exp_after_first
    assert len(_renew_logs(db, u.id)) == 1
    db.close()


# ==================== 4. 配置键注册与校验 ====================

def test_keys_registered_and_removed():
    keys = welfare_admin.WELFARE_CONFIG_KEYS
    schema = config_schema.WELFARE_CONFIG_SCHEMA
    # 新口径三键
    assert keys["welfare_vitality_renew_enabled"] == "true"
    assert keys["welfare_vitality_renew_min"] == "3"
    assert schema["welfare_vitality_renew_enabled"] == ("bool",)
    assert schema["welfare_vitality_renew_min"] == ("int", 0, 1000)
    # 续期天数沿用既有键（语义转过来）
    assert keys["welfare_streak_renew_days"] == "30"
    assert schema["welfare_streak_renew_days"] == ("int", 1, 365)
    # 连签保号旧键与断签保护键摘除
    for gone in ("welfare_streak_renew_enabled", "welfare_streak_renew_min",
                 "welfare_streak_forgive_per_month"):
        assert gone not in keys
        assert gone not in schema


def test_keys_normalize():
    schema = config_schema.WELFARE_CONFIG_SCHEMA
    assert config_schema.normalize(
        "welfare_vitality_renew_enabled",
        schema["welfare_vitality_renew_enabled"], True) == "true"
    assert config_schema.normalize(
        "welfare_vitality_renew_enabled",
        schema["welfare_vitality_renew_enabled"], False) == "false"
    assert config_schema.normalize(
        "welfare_vitality_renew_min",
        schema["welfare_vitality_renew_min"], 0) == "0"
    assert config_schema.normalize(
        "welfare_vitality_renew_min",
        schema["welfare_vitality_renew_min"], 3) == "3"
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize(
            "welfare_vitality_renew_min",
            schema["welfare_vitality_renew_min"], -1)
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize(
            "welfare_vitality_renew_min",
            schema["welfare_vitality_renew_min"], 1001)


# ==================== 5. 签到回滚（PR #3 断签保护撤除） ====================

_CHECKIN_CFG = {
    "checkin_enabled": "true",
    "checkin_base_min": "3",
    "checkin_base_max": "3",
    "checkin_streak_bonus": "1",
    "checkin_streak_max_bonus": "5",
    "checkin_penalty_pct": "0",
    "checkin_crit_pct": "0",
}


@pytest.fixture()
def cdb():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate()
    for k, v in _CHECKIN_CFG.items():
        s.add(models.SystemConfig(key=k, value=v))
    s.commit()
    yield s
    s.close()


def _frame(y, m, d):
    """上海自然日 d 的 00:00（UTC naive 帧，与 CheckinRecord 写入帧一致）。"""
    return datetime(y, m, d) - timedelta(hours=8)


def test_checkin_miss_one_day_resets_streak(cdb, monkeypatch):
    """回滚后：正好漏 1 天（上次在前天）→ 照旧清零 streak=1，
    返回不带断签保护字段，streak_forgive_* 两列不被写。"""
    u = _user(cdb, "miss1", is_welfare=True, points=0, vitality=14)
    cdb.add(models.CheckinRecord(user_id=u.id, checkin_date=_frame(2026, 11, 8),
                                 points_awarded=0, streak=5))
    cdb.commit()
    monkeypatch.setattr(economy, "_today_start", lambda: _frame(2026, 11, 10))
    res = economy._do_checkin_core(cdb, u)
    assert res["streak"] == 1
    assert "streak_forgiven" not in res
    assert "forgive_remaining" not in res
    cdb.expire_all()
    row = cdb.get(models.WebUser, u.id)
    assert (getattr(row, "streak_forgive_ym", None) or "") == ""
    assert int(getattr(row, "streak_forgive_used", 0) or 0) == 0


def test_checkin_yesterday_still_continues(cdb, monkeypatch):
    """签到本身不动：昨天签过 → 连签照常 +1（计数展示/里程碑照旧）。"""
    u = _user(cdb, "cont", is_welfare=True, points=0, vitality=14)
    cdb.add(models.CheckinRecord(user_id=u.id, checkin_date=_frame(2026, 11, 9),
                                 points_awarded=0, streak=5))
    cdb.commit()
    monkeypatch.setattr(economy, "_today_start", lambda: _frame(2026, 11, 10))
    res = economy._do_checkin_core(cdb, u)
    assert res["streak"] == 6


# ==================== 6. bot 连签行文案 ====================

def _checkin(db, user_id, when, streak):
    db.add(models.CheckinRecord(user_id=user_id, checkin_date=when,
                                points_awarded=0, streak=streak))
    db.commit()


def test_bot_line_default_promises_vitality_renew():
    db = _db()
    today = economy._today_start_utc_naive()
    u = _user(db, "bot_default")
    _checkin(db, u.id, today, 5)
    line = features._streak_line(db, u.id)
    assert "连签：5 天" in line
    assert "活力" in line and "自动续公益资格" in line
    assert "满 30 天" not in line
    db.close()


def test_bot_line_switch_off_hides_promise():
    """开关关：连签行只报天数，不再宣称自动续期。"""
    db = _db()
    _set_config(db, "welfare_vitality_renew_enabled", "false")
    today = economy._today_start_utc_naive()
    u = _user(db, "bot_off")
    _checkin(db, u.id, today, 5)
    line = features._streak_line(db, u.id)
    assert line == "📋 连签：5 天"
    assert "自动续" not in line
    db.close()


def test_bot_line_custom_vitality_line_shown():
    db = _db()
    _set_config(db, "welfare_vitality_renew_min", "6")
    today = economy._today_start_utc_naive()
    u = _user(db, "bot_line6")
    _checkin(db, u.id, today, 5)
    line = features._streak_line(db, u.id)
    assert "6" in line and "活力" in line
    db.close()
