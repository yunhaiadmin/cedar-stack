"""流量额度一期：计量归账 / 周期口径 / 额度解析 / 时间换流量成交。

口径要点（与实现同源，改一处要同步改这里）：
- 计量只认真实出字节：中继腿（body_iterator 实际 yield）与本机
  文件腿（serve_file 的 Range 迭代器实际 yield）分开记；302 直链
  只记起播次数、永远不记字节（字节不过本机，估算不许冒充计量）。
- 周期锚点＝有效订阅 start_date 的「日」，逐月滚动、钳月末；
  续订顺延只动 end_date，锚点不漂移。
- 额度：套餐权益 monthly_traffic_bytes 优先 → SystemConfig
  traffic_default_monthly_bytes 回退 → 都没有 = 不启用（enabled
  =False、remaining=None，不许误显 0 额度）。
- 时间换流量（2026-11-13 23:26 定版，照 UHDNow 按比例口径）：
  扣 end_date 的天数，按**成交时该用户月额度×比例**换本周期抵扣
  额度（扣 15 天抵当月额度 50%、扣 30 天抵 100%，不是固定字节
  数）；留痕记额度基数与比例；幂等键同键只成交一次；扣完订阅
  必须仍有效，不许扣穿。
"""
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import entitlements as ent
from backend import models
from backend import traffic_quota as tq
from backend.emby_server import models as em
from backend.emby_server import traffic_meter as tm
from backend.integrations import store as _store

GB = 1024 ** 3
_CONFIG_KEYS = [tq.CONFIG_DEFAULT_QUOTA, tq.CONFIG_EXCHANGE_OPTIONS]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(*_CONFIG_KEYS)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()
    tm.reset_pending()
    yield s
    s.close()
    _store.invalidate(*_CONFIG_KEYS)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()
    tm.reset_pending()


_seq = [0]


def _user(db):
    _seq[0] += 1
    u = models.WebUser(username=f"tq{_seq[0]}", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _plan(db, entitlements=None):
    _seq[0] += 1
    p = models.SubscriptionPlan(
        name=f"plan{_seq[0]}", price=10, duration_days=90,
        entitlements=entitlements)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _sub(db, user, plan, start, end):
    s = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, start_date=start,
        end_date=end, status="active")
    db.add(s)
    db.commit()
    db.refresh(s)
    ent.invalidate(user.id)
    return s


def _config(db, key, value):
    row = (db.query(models.SystemConfig)
           .filter_by(key=key).one_or_none())
    if row is None:
        db.add(models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()
    _store.invalidate(key)


# ---------------------------------------------------------------------------
# 计量：归账 / 累计 / 无归属不计 / 直链只记次
# ---------------------------------------------------------------------------

def test_meter_flush_accumulates_by_user_day_source(db):
    d = date(2026, 11, 20)
    tm.note_bytes(1000, "relay", user_id=7, day=d)
    tm.note_bytes(500, "local", user_id=7, day=d)
    tm.note_bytes(250, "relay", user_id=8, day=d)
    tm.note_direct_play(user_id=7, day=d)
    assert tm.flush_now(db) == 2
    rows = db.query(em.TrafficUsageDaily).order_by(
        em.TrafficUsageDaily.user_id).all()
    assert [(r.user_id, r.relay_bytes, r.local_bytes, r.direct_plays)
            for r in rows] == [(7, 1000, 500, 1), (8, 250, 0, 0)]
    # 第二批继续累计到同一行，不新建行
    tm.note_bytes(100, "relay", user_id=7, day=d)
    assert tm.flush_now(db) == 1
    row = (db.query(em.TrafficUsageDaily)
           .filter_by(user_id=7, day=d).one())
    assert row.relay_bytes == 1100
    assert db.query(em.TrafficUsageDaily).count() == 2


def test_meter_skips_without_user_and_bad_source(db):
    tm.set_current_user(None)
    tm.note_bytes(100, "relay")            # 无归属：不计
    tm.note_bytes(100, "carrier-pigeon", user_id=7)  # 非法来源：不计
    tm.note_bytes(0, "relay", user_id=7)   # 0 字节：不计
    tm.note_direct_play()                  # 无归属：不计
    assert tm.flush_now(db) == 0
    assert db.query(em.TrafficUsageDaily).count() == 0
    assert tm.stats()["skipped_no_user"] >= 2


def test_meter_contextvar_attribution(db):
    tm.set_current_user(42)
    try:
        tm.note_bytes(64, "local", day=date(2026, 11, 20))
    finally:
        tm.set_current_user(None)
    assert tm.flush_now(db) == 1
    row = db.query(em.TrafficUsageDaily).one()
    assert (row.user_id, row.local_bytes) == (42, 64)


# ---------------------------------------------------------------------------
# 周期口径
# ---------------------------------------------------------------------------

def test_period_bounds_anchor_and_month_end_clamp():
    assert tq.period_bounds(13, date(2026, 11, 20)) == (
        date(2026, 11, 13), date(2026, 12, 13))
    # 锚点日还没到 → 周期从上个月锚点开始
    assert tq.period_bounds(13, date(2026, 11, 5)) == (
        date(2026, 10, 13), date(2026, 11, 13))
    # 31 日锚点：2 月钳到 28 日
    assert tq.period_bounds(31, date(2026, 2, 10)) == (
        date(2026, 1, 31), date(2026, 2, 28))
    assert tq.period_bounds(31, date(2026, 3, 1)) == (
        date(2026, 2, 28), date(2026, 3, 31))
    # 跨年
    assert tq.period_bounds(13, date(2027, 1, 5)) == (
        date(2026, 12, 13), date(2027, 1, 13))


# ---------------------------------------------------------------------------
# 额度解析与汇总
# ---------------------------------------------------------------------------

def _std_user(db, entitlements={"monthly_traffic_bytes": 500 * GB}):
    u = _user(db)
    p = _plan(db, entitlements)
    s = _sub(db, u, p, datetime(2026, 10, 13, 10, 0),
             datetime(2027, 1, 13, 10, 0))
    return u, p, s


def test_summary_sums_current_period_only(db):
    u, _p, _s = _std_user(db)
    now = datetime(2026, 11, 20, 12, 0)
    db.add_all([
        em.TrafficUsageDaily(user_id=u.id, day=date(2026, 11, 13),
                             relay_bytes=100 * GB, local_bytes=0,
                             direct_plays=0),
        em.TrafficUsageDaily(user_id=u.id, day=date(2026, 11, 19),
                             relay_bytes=0, local_bytes=50 * GB,
                             direct_plays=3),
        # 上一周期的大额用量不许串进本周期
        em.TrafficUsageDaily(user_id=u.id, day=date(2026, 10, 20),
                             relay_bytes=999 * GB, local_bytes=0,
                             direct_plays=0),
    ])
    db.commit()
    s = tq.usage_summary(db, u.id, now)
    assert s["enabled"] is True
    assert s["quota_bytes"] == 500 * GB
    assert s["gross_used_bytes"] == 150 * GB
    assert s["used_bytes"] == 150 * GB
    assert s["remaining_bytes"] == 350 * GB
    assert s["direct_plays"] == 3
    assert s["period_start"] == "2026-11-13"
    assert s["reset_date"] == "2026-12-13"
    assert s["exhausted"] is False


def test_summary_not_enabled_without_quota_declaration(db):
    u = _user(db)
    p = _plan(db, None)  # 套餐不声明、全局也没配
    _sub(db, u, p, datetime(2026, 10, 13), datetime(2027, 1, 13))
    s = tq.usage_summary(db, u.id, datetime(2026, 11, 20))
    assert s["enabled"] is False
    assert s["quota_bytes"] is None
    assert s["remaining_bytes"] is None  # 不许误显 0 额度


def test_config_default_applies_and_plan_overrides(db):
    _config(db, tq.CONFIG_DEFAULT_QUOTA, str(200 * GB))
    u = _user(db)
    p = _plan(db, None)
    _sub(db, u, p, datetime(2026, 10, 13), datetime(2027, 1, 13))
    assert tq.quota_bytes_for(db, u.id) == 200 * GB
    # 套餐声明优先于全局默认
    u2, _p2, _s2 = _std_user(db)
    assert tq.quota_bytes_for(db, u2.id) == 500 * GB


def test_entitlement_key_registered_and_validated():
    assert ent.validate_entitlements(
        {"monthly_traffic_bytes": 500 * GB}) == {
            "monthly_traffic_bytes": 500 * GB}
    with pytest.raises(ValueError):
        ent.validate_entitlements({"monthly_traffic_bytes": -5})
    # 读路径：超安全栏的值按缺键剔除，不当权益
    assert ent.parse_entitlements(
        {"monthly_traffic_bytes": 10 ** 20}) == {}


# ---------------------------------------------------------------------------
# 时间换流量
# ---------------------------------------------------------------------------

def test_exchange_success_deducts_days_and_credits(db):
    u, _p, sub = _std_user(db)
    now = datetime(2026, 11, 20, 12, 0)
    db.add(em.TrafficUsageDaily(
        user_id=u.id, day=date(2026, 11, 19), relay_bytes=150 * GB,
        local_bytes=0, direct_plays=0))
    db.commit()
    result = tq.exchange_time_for_traffic(db, u.id, 15, "key-1", now)
    assert result["replayed"] is False
    assert result["log"]["days_deducted"] == 15
    # 按比例口径：15 天抵当月额度（500GB）的 50% = 250GB
    assert result["log"]["bytes_credited"] == 250 * GB
    assert result["log"]["quota_base_bytes"] == 500 * GB
    assert result["log"]["quota_pct"] == 50
    assert result["log"]["sub_end_before"] == "2027-01-13T10:00:00"
    assert result["log"]["sub_end_after"] == "2026-12-29T10:00:00"
    db.refresh(sub)
    assert sub.end_date == datetime(2026, 12, 29, 10, 0)
    # 汇总：抵扣 250GB ≥ 毛已用 150GB → 有效已用 0，剩余回满 500GB
    s = result["summary"]
    assert s["credit_bytes"] == 250 * GB
    assert s["credit_applied_bytes"] == 150 * GB
    assert s["used_bytes"] == 0
    assert s["remaining_bytes"] == 500 * GB


def test_exchange_idempotent_replay_never_double_deducts(db):
    u, _p, sub = _std_user(db)
    now = datetime(2026, 11, 20, 12, 0)
    tq.exchange_time_for_traffic(db, u.id, 15, "dup-key", now)
    again = tq.exchange_time_for_traffic(db, u.id, 15, "dup-key", now)
    assert again["replayed"] is True
    db.refresh(sub)
    assert sub.end_date == datetime(2026, 12, 29, 10, 0)  # 只扣一次
    assert db.query(em.TrafficExchangeLog).count() == 1
    # 换一个键才是新的一笔
    second = tq.exchange_time_for_traffic(db, u.id, 30, "other-key", now)
    assert second["log"]["quota_pct"] == 100
    assert second["log"]["bytes_credited"] == 500 * GB  # 30 天抵 100%
    db.refresh(sub)
    assert sub.end_date == datetime(2026, 11, 29, 10, 0)
    assert db.query(em.TrafficExchangeLog).count() == 2


def test_exchange_rejects_insufficient_days(db):
    u = _user(db)
    p = _plan(db, {"monthly_traffic_bytes": 500 * GB})
    _sub(db, u, p, datetime(2026, 11, 15, 10, 0),
         datetime(2026, 11, 30, 10, 0))  # 只剩 10 天
    with pytest.raises(tq.TrafficError) as ei:
        tq.exchange_time_for_traffic(
            db, u.id, 15, "k-ins", datetime(2026, 11, 20, 12, 0))
    assert ei.value.code == "insufficient_days"
    assert db.query(em.TrafficExchangeLog).count() == 0


def test_exchange_rejects_without_quota_and_bad_option(db):
    u = _user(db)
    p = _plan(db, None)  # 无额度声明、无全局默认
    _sub(db, u, p, datetime(2026, 10, 13), datetime(2027, 1, 13))
    now = datetime(2026, 11, 20, 12, 0)
    with pytest.raises(tq.TrafficError) as ei:
        tq.exchange_time_for_traffic(db, u.id, 15, "k-noq", now)
    assert ei.value.code == "quota_not_enabled"
    # 有额度但选项不存在
    u2, _p2, _s2 = _std_user(db)
    with pytest.raises(tq.TrafficError) as ei2:
        tq.exchange_time_for_traffic(db, u2.id, 7, "k-bad", now)
    assert ei2.value.code == "bad_option"


def test_exchange_options_config_override(db):
    _config(db, tq.CONFIG_EXCHANGE_OPTIONS,
            '[{"days": 10, "quota_pct": 25}]')
    assert tq.exchange_options(db) == [{"days": 10, "quota_pct": 25}]
    _config(db, tq.CONFIG_EXCHANGE_OPTIONS, "not-json")
    assert tq.exchange_options(db) == tq.DEFAULT_EXCHANGE_OPTIONS
    # 旧固定字节形态（{"days":..,"bytes":..}）在新口径下不算合法项：
    # 无合法项 → 整体回退默认（不许拿旧固定值继续成交）
    _config(db, tq.CONFIG_EXCHANGE_OPTIONS,
            '[{"days": 10, "bytes": 53687091200}]')
    assert tq.exchange_options(db) == tq.DEFAULT_EXCHANGE_OPTIONS


def test_exchange_full_quota_option(db):
    """30 天档抵当月额度 100%：额度多大抵多少，不是固定值。"""
    u, _p, _sub = _std_user(db)  # 月额度 500GB
    now = datetime(2026, 11, 20, 12, 0)
    result = tq.exchange_time_for_traffic(db, u.id, 30, "k-full", now)
    assert result["log"]["bytes_credited"] == 500 * GB
    assert result["log"]["quota_base_bytes"] == 500 * GB
    assert result["log"]["quota_pct"] == 100


def test_exchange_base_follows_config_fallback_quota(db):
    """套餐未声明时，比例基数 = 全局默认额度（回退口径不变）。"""
    _config(db, tq.CONFIG_DEFAULT_QUOTA, str(200 * GB))
    u = _user(db)
    p = _plan(db, None)
    _sub(db, u, p, datetime(2026, 10, 13), datetime(2027, 1, 13))
    now = datetime(2026, 11, 20, 12, 0)
    result = tq.exchange_time_for_traffic(db, u.id, 15, "k-fb", now)
    assert result["log"]["quota_base_bytes"] == 200 * GB
    assert result["log"]["bytes_credited"] == 100 * GB  # 200GB 的 50%


def test_exchange_credit_floors_to_whole_bytes(db):
    """比例折算向下取整到整字节，不许四舍五入多给。"""
    u, _p, _s = _std_user(db, {"monthly_traffic_bytes": 333})
    now = datetime(2026, 11, 20, 12, 0)
    result = tq.exchange_time_for_traffic(db, u.id, 15, "k-floor", now)
    assert result["log"]["bytes_credited"] == 166  # 333 × 50% = 166.5 → 166


def test_exchange_period_anchor_month_end(db):
    """月末锚点：周期与留痕快照都钳月末，抵扣只记进本周期。"""
    u = _user(db)
    p = _plan(db, {"monthly_traffic_bytes": 500 * GB})
    _sub(db, u, p, datetime(2026, 1, 31, 10, 0),
         datetime(2027, 1, 31, 10, 0))  # 锚点日 = 31
    now = datetime(2026, 2, 10, 12, 0)  # 周期 [01-31, 02-28)
    result = tq.exchange_time_for_traffic(db, u.id, 15, "k-anchor", now)
    assert result["log"]["period_start"] == "2026-01-31"
    assert result["log"]["period_end"] == "2026-02-28"
    assert result["summary"]["period_start"] == "2026-01-31"
    assert result["summary"]["credit_bytes"] == 250 * GB


def test_exchange_options_view_folds_user_quota(db):
    """选项视图：比例是配置口径，bytes 按该用户当下额度现折算。"""
    u, _p, _s = _std_user(db)  # 月额度 500GB
    view = tq.exchange_options_view(db, u.id)
    assert view == [
        {"days": 15, "quota_pct": 50, "bytes": 250 * GB},
        {"days": 30, "quota_pct": 100, "bytes": 500 * GB},
    ]
    # 未启用用户：比例照给、折算字节为 0（前端整段本就不渲染）
    u2 = _user(db)
    p2 = _plan(db, None)
    _sub(db, u2, p2, datetime(2026, 10, 13), datetime(2027, 1, 13))
    view2 = tq.exchange_options_view(db, u2.id)
    assert [o["bytes"] for o in view2] == [0, 0]
    assert [o["quota_pct"] for o in view2] == [50, 100]


# ---------------------------------------------------------------------------
# 本机文件腿：serve_file 的 Range 迭代按真实出字节归账
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_serve_file_range_counts_local_bytes(db, tmp_path):
    import asyncio

    from backend.emby_server import streaming

    f = tmp_path / "clip.bin"
    f.write_bytes(b"x" * 1000)
    tm.set_current_user(9)
    try:
        resp = streaming.serve_file(
            str(f), _FakeRequest({"range": "bytes=0-99"}))

        async def _collect():
            return [chunk async for chunk in resp.body_iterator]

        body = b"".join(asyncio.run(_collect()))
    finally:
        tm.set_current_user(None)
    assert len(body) == 100
    assert tm.flush_now(db) == 1
    row = db.query(em.TrafficUsageDaily).one()
    assert (row.user_id, row.local_bytes, row.relay_bytes) == (9, 100, 0)
