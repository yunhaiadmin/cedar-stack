"""中继兜底计时：客户端不上报 Playing/Progress/Stopped 时，用 EA 中继流
的实际活动（租约式活动行）重建观看段，结账走与真实会话同一套入账函数
（watch_points.award_watch_points + vitality.award_watch_minutes_reward）。

病例：2026-11-11 用户 1826 在 SenPlayer 看《后会友期》第 1 集，中继
200/206 拉流 20 余分钟但零会话上报，watch_points_daily 漏账。

口径要点（与实现同源）：
- 活动按 (user, item, device) 聚成一条租约行，只记首末时间与已闭合
  未结账段；相邻活动间隔 > 10 分钟切段。
- 结账只处理已静默收尾的段；单段 < 2 分钟不入账；与真实
  PlaybackSession 时间窗重叠的部分扣除（真实会话优先）。
- 单段封顶＝条目时长；每人每日兜底封顶 240 分钟，兜底分钟记进
  watch_points_daily.item_baselines 的保留键 "__fallback__" 可查。
- 开关 watch_fallback_credit_enabled 默认开，关时整轮跳过不消费段。
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.emby_server import models as em
from backend.emby_server import watch_activity as wa
from backend.emby_server import watch_fallback as wf
from backend.integrations import store as _store

_KEYS = [
    "watch_fallback_credit_enabled",
    "watch_points_first", "watch_points_first_minutes",
    "watch_points_tier1", "watch_points_tier1_minutes",
    "watch_points_tier2", "watch_points_tier2_minutes",
    "watch_points_daily_cap",
    "vitality_enabled", "vitality_daily_gain_limit",
    "vitality_watch_tier1_minutes", "vitality_watch_tier1_reward",
    "vitality_watch_tier2_minutes", "vitality_watch_tier2_reward",
]

_SH = ZoneInfo("Asia/Shanghai")


def _base() -> datetime:
    """今天（上海日界）上午 08:00 的 naive 时间，保证全天同日界。"""
    now = datetime.now(_SH)
    return now.replace(hour=8, minute=0, second=0, microsecond=0,
                       tzinfo=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(*_KEYS)
    yield s
    s.close()
    _store.invalidate(*_KEYS)


def _user(db, name, welfare=True):
    u = models.WebUser(username=name, password_hash="x", is_welfare=welfare,
                       vitality=0)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


_guid_seq = [0]


def _item(db, minutes=120):
    _guid_seq[0] += 1
    it = em.MediaItem(name="兜底测试片", item_type="Movie", library_id=1,
                      guid=f"fallback-test-{_guid_seq[0]}",
                      duration_ticks=int(minutes * 600_000_000))
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _touch(db, user_id, item_id, at, device="dev:t1"):
    wa.apply_touch(db, user_id, item_id, device, at)


def _daily(db, user_id):
    return (db.query(models.WatchPointsDaily)
            .filter(models.WatchPointsDaily.user_id == user_id).first())


def _fallback_minutes(db, user_id):
    import json
    row = _daily(db, user_id)
    if row is None:
        return 0.0
    return float((json.loads(row.item_baselines or "{}")
                  .get(wf.FALLBACK_BASELINE_KEY) or 0))


def _points(db, user_id):
    return int(db.query(models.WebUser.points).filter(
        models.WebUser.id == user_id).scalar() or 0)


def _vitality(db, user_id):
    return int(db.query(models.WebUser.vitality).filter(
        models.WebUser.id == user_id).scalar() or 0)


def _session(db, user_id, item_id, start, end):
    db.add(em.PlaybackSession(
        session_key=f"sk-{user_id}-{item_id}-{start.timestamp()}",
        user_id=user_id, item_id=item_id, start_time=start,
        last_update_at=end, ended_at=end))
    db.commit()


def test_segment_rebuilt_and_credited(db):
    """合成活动 25 分钟一段 → 结账入 watch_points_daily（首看档 +4），
    兜底分钟留 "__fallback__" 标记；重复结账不重复入账。"""
    u = _user(db, "seg")
    it = _item(db)
    t0 = _base()
    for off in (0, 8, 16, 25):  # 间隔 < 切段阈值 10 分钟，同属一段
        _touch(db, u.id, it.id, t0 + timedelta(minutes=off))
    stats = wf.settle_once(db, now=t0 + timedelta(minutes=40))
    assert stats["ran"] is True
    assert stats["credited_segments"] == 1
    assert abs(stats["credited_minutes"] - 25.0) < 0.01
    assert _points(db, u.id) == 4  # 当日累计 25 ≥ 首看 20 → +4
    assert abs(_fallback_minutes(db, u.id) - 25.0) < 0.01
    assert abs(float(_daily(db, u.id).total_minutes) - 25.0) < 0.01
    # 再结一轮：段已消费，不许重复入账
    stats2 = wf.settle_once(db, now=t0 + timedelta(minutes=90))
    assert stats2["credited_segments"] == 0
    assert _points(db, u.id) == 4


def test_vitality_tier_reached_via_fallback(db):
    """兜底 35 分钟一段：活力走同一 award 函数，命中 tier1（30 分钟→+1）。"""
    u = _user(db, "vit")
    it = _item(db)
    t0 = _base()
    for off in (0, 9, 18, 27, 35):
        _touch(db, u.id, it.id, t0 + timedelta(minutes=off))
    before = _vitality(db, u.id)
    wf.settle_once(db, now=t0 + timedelta(minutes=50))
    assert _vitality(db, u.id) - before == 1
    assert _points(db, u.id) == 4


def test_overlap_with_real_session_not_double_counted(db):
    """真实会话全覆盖 → 兜底零入账；部分覆盖只补未覆盖的真空部分。"""
    t0 = _base()
    u1 = _user(db, "full-cover")
    it = _item(db)
    _session(db, u1.id, it.id, t0, t0 + timedelta(minutes=30))
    for off in (0, 9, 18, 30):
        _touch(db, u1.id, it.id, t0 + timedelta(minutes=off))
    stats = wf.settle_once(db, now=t0 + timedelta(minutes=45))
    assert stats["credited_segments"] == 0
    assert _daily(db, u1.id) is None

    u2 = _user(db, "part-cover")
    _session(db, u2.id, it.id, t0, t0 + timedelta(minutes=10))
    for off in (0, 9, 18, 27, 36, 40):
        _touch(db, u2.id, it.id, t0 + timedelta(minutes=off))
    before = _vitality(db, u2.id)
    wf.settle_once(db, now=t0 + timedelta(minutes=55))
    # 40 分钟段扣掉真实会话覆盖的 10 分钟 → 补 30 分钟
    assert abs(_fallback_minutes(db, u2.id) - 30.0) < 0.01
    assert _vitality(db, u2.id) - before == 1  # 30 分钟命中活力 tier1


def test_short_segment_and_lone_touch_skipped(db):
    """孤零零一次拉流、以及不足 2 分钟的段，都不入账（防预加载刷量）。"""
    u = _user(db, "short")
    it = _item(db)
    t0 = _base()
    _touch(db, u.id, it.id, t0)  # 孤点
    _touch(db, u.id, it.id, t0 + timedelta(minutes=30), device="dev:t2")
    _touch(db, u.id, it.id, t0 + timedelta(minutes=31), device="dev:t2")
    stats = wf.settle_once(db, now=t0 + timedelta(minutes=60))
    assert stats["credited_segments"] == 0
    assert _daily(db, u.id) is None


def test_open_segment_not_settled_early(db):
    """段还没静默收尾（末次活动距今 < 切段阈值）时不结账。"""
    u = _user(db, "open")
    it = _item(db)
    t0 = _base()
    for off in (0, 20):
        _touch(db, u.id, it.id, t0 + timedelta(minutes=off))
    stats = wf.settle_once(db, now=t0 + timedelta(minutes=25))
    assert stats["credited_segments"] == 0
    assert _daily(db, u.id) is None


def test_item_duration_cap(db):
    """单段封顶＝条目自身时长：片长 10 分钟，拉流跨度 60 分钟只记 10。"""
    u = _user(db, "cap-item")
    it = _item(db, minutes=10)
    t0 = _base()
    for off in (0, 9, 18, 27, 36, 45, 54, 60):
        _touch(db, u.id, it.id, t0 + timedelta(minutes=off))
    wf.settle_once(db, now=t0 + timedelta(minutes=75))
    assert abs(_fallback_minutes(db, u.id) - 10.0) < 0.01


def test_daily_fallback_cap(db):
    """每人每日兜底封顶 240 分钟：5 条 60 分钟的段只入账 240。"""
    u = _user(db, "cap-day")
    t0 = _base()
    for i in range(5):
        it = _item(db, minutes=180)
        start = t0 + timedelta(minutes=70 * i)
        for off in (0, 9, 18, 27, 36, 45, 54, 60):
            _touch(db, u.id, it.id, start + timedelta(minutes=off))
    wf.settle_once(db, now=t0 + timedelta(minutes=70 * 5))
    assert abs(_fallback_minutes(db, u.id) - 240.0) < 0.01


def test_gap_rollover_splits_segments(db):
    """间隔超阈值切段：前段 5 分钟入账，后段孤点丢弃。"""
    u = _user(db, "gap")
    it = _item(db)
    t0 = _base()
    _touch(db, u.id, it.id, t0)
    _touch(db, u.id, it.id, t0 + timedelta(minutes=5))
    _touch(db, u.id, it.id, t0 + timedelta(minutes=40))  # 间隔 35 分钟
    wf.settle_once(db, now=t0 + timedelta(minutes=60))
    assert abs(_fallback_minutes(db, u.id) - 5.0) < 0.01


def test_switch_off_skips_round_and_keeps_segments(db):
    """开关关：整轮跳过，段不消费；重新打开后仍能结账。"""
    u = _user(db, "off")
    it = _item(db)
    t0 = _base()
    db.add(models.SystemConfig(
        key="watch_fallback_credit_enabled", value="false"))
    db.commit()
    _store.invalidate("watch_fallback_credit_enabled")
    for off in (0, 9, 18, 25):
        _touch(db, u.id, it.id, t0 + timedelta(minutes=off))
    stats = wf.settle_once(db, now=t0 + timedelta(minutes=40))
    assert stats["ran"] is False
    assert stats["reason"] == "disabled"
    assert _daily(db, u.id) is None
    # 打开后同一段仍可结账
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == "watch_fallback_credit_enabled").first()
    cfg.value = "true"
    db.commit()
    _store.invalidate("watch_fallback_credit_enabled")
    stats2 = wf.settle_once(db, now=t0 + timedelta(minutes=40))
    assert stats2["credited_segments"] == 1
    assert abs(_fallback_minutes(db, u.id) - 25.0) < 0.01
