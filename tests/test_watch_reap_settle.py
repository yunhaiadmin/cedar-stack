"""观看奖励回收结算：不发 Stopped 的会话被过期回收时按档补发，
且与 Stopped 结算互斥（同一会话只发一次）。

病例：2026-11-12 用户 789（LangHuan 客户端）连看 13 集、每集位置
约 46 分钟，会话全被回收器收尾，活力 0 发放——奖励原本只在
compat_routes._record_stopped 结算，回收路径只标 ended_at 不结算。

2026-11-13 货币删除收口：活力与观影能量两腿已下线，结算现只发
观影 XP（member_level.award_watch_xp）。钉死三条（XP 口径）：
1. 无 Stopped、会话被回收仍按档发 XP（30 分钟 +1 / 60 分钟 +2，
   watched 基线与 Stopped 同口径：max(umd 最远点, 会话最后位置)）；
2. 先结算的一方落 rewards_settled_at 标记，后到的迟到 Stopped /
   重复 Stopped 不再重复发；同条目同日只计 1 次，复活重播也不双发；
3. 可刷日封顶 10（观影＋签到合计），两场 65 分钟共发 4 XP，不截断。
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.emby_server import compat_routes, maintenance
from backend.emby_server import models as em

MIN = 600_000_000  # 1 分钟的 ticks


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(db, name, welfare=True):
    u = models.WebUser(username=name, password_hash="x",
                       is_welfare=welfare)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


_guid_seq = [0]


def _item(db, minutes=120):
    _guid_seq[0] += 1
    it = em.MediaItem(name="回收结算测试片", item_type="Movie", library_id=1,
                      guid=f"reap-settle-{_guid_seq[0]}",
                      duration_ticks=int(minutes * MIN))
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _stale_session(db, user_id, item_id, pos_minutes, key, idle_minutes=30):
    """一条没有 Stopped、心跳已停的会话（等回收）。"""
    now = datetime.now()
    s = em.PlaybackSession(
        session_key=key, user_id=user_id, item_id=item_id,
        client_name="LangHuan", play_method="DirectStream",
        start_time=now - timedelta(minutes=idle_minutes + pos_minutes),
        last_update_at=now - timedelta(minutes=idle_minutes),
        position_ticks=int(pos_minutes * MIN), ended_at=None)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _xp(db, user_id):
    return int(db.query(models.WebUser.member_xp).filter(
        models.WebUser.id == user_id).scalar() or 0)


def _watch_xp_rows(db, user_id):
    return (db.query(models.MemberXpLog)
            .filter(models.MemberXpLog.user_id == user_id,
                    models.MemberXpLog.source == "watch")
            .count())


def _req():
    return SimpleNamespace(headers={},
                           client=SimpleNamespace(host="127.0.0.1"))


def _stopped(db, user, item, key, pos_minutes):
    """直接走 _record_stopped（可见性校验与本单无关，替身成条目本身）。"""
    body = {"ItemId": item.guid, "PlaySessionId": key,
            "PositionTicks": int(pos_minutes * MIN), "IsPaused": False,
            "PlayMethod": "DirectStream"}
    orig = compat_routes._require_visible_item
    compat_routes._require_visible_item = lambda db_, user_, _id: item
    try:
        compat_routes._record_stopped(_req(), user, db, body)
    finally:
        compat_routes._require_visible_item = orig


# ---------- 1. 回收按档发 XP ----------

def test_reap_settles_tier1_without_stopped(db):
    u = _user(db, "reap-t1")
    it = _item(db)
    s = _stale_session(db, u.id, it.id, 46, "reap-t1-key")
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 1
    db.refresh(s)
    assert s.ended_at is not None
    assert s.rewards_settled_at is not None
    assert _xp(db, u.id) == 1
    assert _watch_xp_rows(db, u.id) == 1


def test_reap_settles_tier2_without_stopped(db):
    u = _user(db, "reap-t2")
    it = _item(db)
    _stale_session(db, u.id, it.id, 65, "reap-t2-key")
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 1
    assert _xp(db, u.id) == 2


def test_reap_watched_basis_uses_umd_max(db):
    """watched 基线与 Stopped 同口径：会话最后位置只有 20 分钟，
    但 umd 本轮最远点 46 分钟 → 按 46 分钟发一档。"""
    u = _user(db, "reap-umd")
    it = _item(db)
    _stale_session(db, u.id, it.id, 20, "reap-umd-key")
    db.add(em.UserMediaData(user_id=u.id, item_id=it.id,
                            playback_position_ticks=int(20 * MIN),
                            max_position_ticks=int(46 * MIN)))
    db.commit()
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 1
    assert _xp(db, u.id) == 1


def test_reap_below_tier_no_award_but_session_closed(db):
    """不足一档不发奖，但回收收尾照常（ended_at 落、结算标记落，
    结算失败/无奖绝不能把会话留在未结束状态）。"""
    u = _user(db, "reap-low")
    it = _item(db)
    s = _stale_session(db, u.id, it.id, 10, "reap-low-key")
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 1
    db.refresh(s)
    assert s.ended_at is not None
    assert s.rewards_settled_at is not None
    assert _xp(db, u.id) == 0
    assert _watch_xp_rows(db, u.id) == 0


# ---------- 2. 幂等：先结算者胜，后到者跳过 ----------

def test_late_stopped_after_reap_no_double(db):
    """回收先结算（+1 XP），迟到的 Stopped 再来也不能重复发。"""
    u = _user(db, "late-stop")
    it = _item(db)
    _stale_session(db, u.id, it.id, 46, "late-stop-key")
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 1
    assert _xp(db, u.id) == 1
    _stopped(db, u, it, "late-stop-key", 46)
    assert _xp(db, u.id) == 1
    assert _watch_xp_rows(db, u.id) == 1


def test_duplicate_stopped_no_double(db):
    """正常 Stopped 结算后，同一会话的重复 Stopped 不再重复发。"""
    u = _user(db, "dup-stop")
    it = _item(db)
    _stopped(db, u, it, "dup-stop-key", 46)
    assert _xp(db, u.id) == 1
    _stopped(db, u, it, "dup-stop-key", 46)
    assert _xp(db, u.id) == 1
    assert _watch_xp_rows(db, u.id) == 1


def test_revival_same_item_no_double_xp(db):
    """同一会话键复活（新一轮播放）后，结算标记可重新落、会话可再收尾，
    但 XP 按条目每日只计 1 次：同条目同日复活重播不双发（活力时代
    的「再 +1」口径已随货币下线）。"""
    u = _user(db, "revive")
    it = _item(db)
    _stopped(db, u, it, "revive-key", 46)
    assert _xp(db, u.id) == 1
    # 复活：Progress 上报（ended=False）命中已结束行 → 清 ended 与结算标记
    body = {"ItemId": it.guid, "PlaySessionId": "revive-key",
            "PositionTicks": int(46 * MIN), "IsPaused": False,
            "PlayMethod": "DirectStream"}
    orig = compat_routes._require_visible_item
    compat_routes._require_visible_item = lambda db_, user_, _id: it
    try:
        compat_routes._record_progress(_req(), u, db, body)
        compat_routes._record_stopped(_req(), u, db, body)
    finally:
        compat_routes._require_visible_item = orig
    assert _xp(db, u.id) == 1
    assert _watch_xp_rows(db, u.id) == 1


# ---------- 3. 两场同日各结各的（日封顶 10，不截断） ----------

def test_reap_two_sessions_both_settle(db):
    """两场 65 分钟的回收会话同日结算，各发 2 XP、共 4 XP（日封顶 10 未撞线）。"""
    u = _user(db, "reap-cap")
    it1 = _item(db)
    it2 = _item(db)
    _stale_session(db, u.id, it1.id, 65, "reap-cap-1")
    _stale_session(db, u.id, it2.id, 65, "reap-cap-2")
    assert maintenance.reap_stale_playback_sessions(db, stale_minutes=10) == 2
    assert _xp(db, u.id) == 4
    assert _watch_xp_rows(db, u.id) == 2
