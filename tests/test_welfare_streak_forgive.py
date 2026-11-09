"""连签断签保护（2026-11-09 用户批准）：每月 N 次「漏签 1 天」豁免。

口径定版（不许改）：
- 新键 welfare_streak_forgive_per_month（默认 1；0＝关闭、完全回到旧行为）；
- 签到时发现上次签到在前天（正好漏了整整 1 天）且本月保护没用完 →
  连签不清零：streak 照常 +1（漏掉那天不补计天数），本月已用次数 +1 落库；
- 漏 2 天及以上（间隔 ≥3 天）→ 照旧清零（streak=1）；本月保护已用完
  再漏 1 天 → 清零；
- 用量跨月自动重置：落库为 web_users 两列（streak_forgive_ym 上海自然月
  键 YYYYMM + streak_forgive_used 已用次数），月键不符即按 0 算；
- 保号判定 welfare_expiry._try_streak_renew 口径不变（streak 达
  welfare_streak_renew_min 即续），变的只是 streak 的攒法；
- streak 唯一写点是 economy._do_checkin_core（网站签到与 TG 签到共用）。
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
from backend.api import economy
from backend.api import welfare_admin
from backend.integrations import store as _store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(
        "welfare_streak_forgive_per_month", "welfare_streak_renew_min",
        "welfare_streak_renew_enabled", "welfare_streak_renew_days",
        "welfare_grace_days",
    )
    yield s
    s.close()


BASE_CFG = {
    "checkin_enabled": "true",
    "checkin_base_min": "3",
    "checkin_base_max": "3",
    "checkin_streak_bonus": "1",
    "checkin_streak_max_bonus": "5",
    "checkin_penalty_pct": "0",
    "checkin_crit_pct": "0",
}


def _seed_cfg(db, extra=None):
    cfg = dict(BASE_CFG)
    if extra:
        cfg.update(extra)
    for k, v in cfg.items():
        db.add(models.SystemConfig(key=k, value=str(v)))
    db.commit()


def _user(db, name="u1", **kw):
    kw.setdefault("is_welfare", True)
    kw.setdefault("points", 0)
    kw.setdefault("vitality", 14)
    u = models.WebUser(username=name, password_hash="x", **kw)
    db.add(u)
    db.commit()
    return u


def _frame(y, m, d):
    """上海自然日 d 的 00:00（UTC naive 帧，与 CheckinRecord 写入帧一致）。"""
    return datetime(y, m, d) - timedelta(hours=8)


def _rec(db, user, y, m, d, streak):
    db.add(models.CheckinRecord(user_id=user.id, checkin_date=_frame(y, m, d),
                                 points_awarded=0, streak=streak))
    db.commit()


def _set_today(monkeypatch, y, m, d):
    monkeypatch.setattr(economy, "_today_start", lambda: _frame(y, m, d))


def _used(db, user):
    db.expire_all()
    row = db.get(models.WebUser, user.id)
    return getattr(row, "streak_forgive_ym", None), int(
        getattr(row, "streak_forgive_used", 0) or 0)


# ==================== 1. 漏 1 天且有保护 → 续上、用量 +1 ====================

def test_miss_one_day_forgiven_streak_continues(db, monkeypatch):
    _seed_cfg(db)
    u = _user(db)
    _rec(db, u, 2026, 11, 8, streak=5)  # 11-09 漏签
    _set_today(monkeypatch, 2026, 11, 10)
    res = economy._do_checkin_core(db, u)
    assert res["streak"] == 6  # 照常 +1，漏掉的 11-09 不补计
    assert res.get("streak_forgiven") is True
    assert res.get("forgive_remaining") == 0  # 默认每月 1 次，已用完
    ym, used = _used(db, u)
    assert ym == "202611"
    assert used == 1
    today_rec = db.query(models.CheckinRecord).filter_by(
        user_id=u.id, checkin_date=_frame(2026, 11, 10)).one()
    assert today_rec.streak == 6


# ==================== 2. 同月第二次漏 1 天 → 清零 ====================

def test_second_miss_same_month_resets(db, monkeypatch):
    _seed_cfg(db)
    u = _user(db)
    _rec(db, u, 2026, 11, 8, streak=5)
    _set_today(monkeypatch, 2026, 11, 10)
    res1 = economy._do_checkin_core(db, u)
    assert res1["streak"] == 6 and res1.get("streak_forgiven") is True
    # 11-11 再漏一天，11-12 签到：本月保护已用完 → 清零
    _set_today(monkeypatch, 2026, 11, 12)
    res2 = economy._do_checkin_core(db, u)
    assert res2["streak"] == 1
    assert res2.get("streak_forgiven") is not True
    ym, used = _used(db, u)
    assert ym == "202611" and used == 1  # 用量不因为清零而回退


# ==================== 3. 漏 2 天 → 清零 ====================

def test_miss_two_days_resets(db, monkeypatch):
    _seed_cfg(db)
    u = _user(db)
    _rec(db, u, 2026, 11, 7, streak=9)  # 11-08、11-09 都漏
    _set_today(monkeypatch, 2026, 11, 10)
    res = economy._do_checkin_core(db, u)
    assert res["streak"] == 1
    assert res.get("streak_forgiven") is not True
    _, used = _used(db, u)
    assert used == 0  # 没消耗保护


# ==================== 4. 保护配置 =0 → 漏 1 天清零（旧行为回归） ====================

def test_forgive_disabled_zero_resets(db, monkeypatch):
    _seed_cfg(db, {"welfare_streak_forgive_per_month": "0"})
    u = _user(db)
    _rec(db, u, 2026, 11, 8, streak=5)
    _set_today(monkeypatch, 2026, 11, 10)
    res = economy._do_checkin_core(db, u)
    assert res["streak"] == 1
    assert res.get("streak_forgiven") is not True


# ==================== 5. 跨月 → 保护次数恢复 ====================

def test_forgive_restored_next_month(db, monkeypatch):
    _seed_cfg(db)
    u = _user(db)
    _rec(db, u, 2026, 10, 27, streak=5)
    # 10-28 漏签，10-29 签到：用掉 10 月的 1 次保护
    _set_today(monkeypatch, 2026, 10, 29)
    res1 = economy._do_checkin_core(db, u)
    assert res1["streak"] == 6 and res1.get("streak_forgiven") is True
    ym, used = _used(db, u)
    assert ym == "202610" and used == 1
    # 10-30 正常签到（不消耗保护）
    _set_today(monkeypatch, 2026, 10, 30)
    res2 = economy._do_checkin_core(db, u)
    assert res2["streak"] == 7 and res2.get("streak_forgiven") is not True
    # 10-31 漏签，11-01 签到：新月份保护恢复 → 再次豁免
    _set_today(monkeypatch, 2026, 11, 1)
    res3 = economy._do_checkin_core(db, u)
    assert res3["streak"] == 8
    assert res3.get("streak_forgiven") is True
    ym, used = _used(db, u)
    assert ym == "202611" and used == 1


# ==================== 6. 攒满阈值（中间用过 1 次保护）→ 到期仍自动续期 ====================

def test_forgiven_streak_still_auto_renews(db, monkeypatch):
    _seed_cfg(db)
    now = datetime.now()
    u = _user(db, "renew",
              welfare_expires_at=now - timedelta(days=3),
              welfare_granted_at=now - timedelta(days=60))
    _rec(db, u, 2026, 11, 6, streak=28)
    _set_today(monkeypatch, 2026, 11, 7)
    res1 = economy._do_checkin_core(db, u)
    assert res1["streak"] == 29
    # 11-08 漏签，11-09 签到：保护续上 → 30，恰达默认门槛
    _set_today(monkeypatch, 2026, 11, 9)
    res2 = economy._do_checkin_core(db, u)
    assert res2["streak"] == 30 and res2.get("streak_forgiven") is True
    # 到期 tick：alive 窗口按签到帧判（把帧钉回 11-09），应走 streak_renew
    monkeypatch.setattr(economy, "_today_start_utc_naive",
                        lambda: _frame(2026, 11, 9))
    res = welfare_expiry.check_welfare_expiry(db)
    assert res["renewed"] == [u.id]
    logs = db.query(models.WelfareGrantLog).filter_by(
        user_id=u.id, channel="streak_renew").all()
    assert len(logs) == 1


# ==================== 7. 配置键注册与校验 ====================

def test_forgive_key_registered_and_normalized():
    assert welfare_admin.WELFARE_CONFIG_KEYS[
        "welfare_streak_forgive_per_month"] == "1"
    schema = config_schema.WELFARE_CONFIG_SCHEMA[
        "welfare_streak_forgive_per_month"]
    assert schema == ("int", 0, 31)
    assert config_schema.normalize(
        "welfare_streak_forgive_per_month", schema, 0) == "0"
    assert config_schema.normalize(
        "welfare_streak_forgive_per_month", schema, 3) == "3"
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize(
            "welfare_streak_forgive_per_month", schema, -1)
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize(
            "welfare_streak_forgive_per_month", schema, 32)


# ==================== 8. 正常连签不消耗保护 ====================

def test_normal_consecutive_day_does_not_consume(db, monkeypatch):
    _seed_cfg(db)
    u = _user(db)
    _rec(db, u, 2026, 11, 9, streak=5)  # 昨天刚签
    _set_today(monkeypatch, 2026, 11, 10)
    res = economy._do_checkin_core(db, u)
    assert res["streak"] == 6
    assert res.get("streak_forgiven") is not True
    _, used = _used(db, u)
    assert used == 0
