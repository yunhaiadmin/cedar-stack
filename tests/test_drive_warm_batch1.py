# -*- coding: utf-8 -*-
"""暖地址重做批 1 · 工人骨架＋W5（存在性）＋W1（交付计划）（goal-35）。

钉死口径（用户 2026-11-08 点选「按推荐口径开工」，D1–D7 定版）：
- 新建 drive_warm 工人，旧工人保持停用；三类信号收集器原样复用
  ＋新增 item→用户展开（D1：只暖近窗播过该条目的用户）。
- 工人用各用户本人令牌提前把 W1 交付计划、W5 存在性写进现成
  缓存；点播路径判定逻辑零改动、全冷时与今天一致。
- SA 池只用于存在性校验；单轮请求预算默认 400 次硬帽；总开关
  默认关；只吃闲置产能、带配额预算与随机抖动防风控。
- 转存预建不做；W4 首字节实验不在本批。
- 身份铁律：W1 的计划按 (用户, 条目) 建，绝不把 A 用户的计划
  写给 B 用户；W5 的存在性断言与用户无关（只断言文件在），
  写进的正缓存键只含 fid、不含任何用户信息。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, CacheManager
from backend.emby_server import models as em
from backend.emby_server import delivery_plan as dp
from backend.emby_server import drive_warm_worker as dw
import backend.models  # noqa: F401 — 注册 web_users 等表

NOW = datetime(2026, 11, 8, 12, 0, 0)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "1")
    monkeypatch.setenv("DRIVE_WARM_JITTER_SEC", "0")
    monkeypatch.setenv("DRIVE_WARM_CONCURRENCY", "1")
    for key in list(os.environ):
        if key.startswith("DRIVE_WARM_") and key not in (
                "DRIVE_WARM_ENABLED", "DRIVE_WARM_JITTER_SEC",
                "DRIVE_WARM_CONCURRENCY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(dw, "_load_ratio", lambda: 0.1)
    dw._warmed_at.clear()
    dw._last_round.clear()
    CacheManager.delete(dw.STATUS_CACHE_KEY)
    yield
    dw._warmed_at.clear()
    dw._last_round.clear()
    CacheManager.delete(dw.STATUS_CACHE_KEY)


def _item(db, name, item_type="movie", *, series=None, season=None,
          ep=None, added=None, drive_file_id=None, library_id=1):
    it = em.MediaItem(
        guid=f"g-{name}-{os.urandom(4).hex()}",
        library_id=library_id, item_type=item_type, name=name,
        file_path="mount://3/电影/a.mkv",
        drive_file_id=drive_file_id or f"fid-{name}",
        series_id=series.id if series is not None else None,
        season_number=season, episode_number=ep,
        date_added=added if added is not None else NOW,
    )
    db.add(it)
    db.commit()
    return it


def _cfg(**kw):
    cfg = dw.drive_warm_config()
    cfg.update(kw)
    return cfg


def _follow(db, series, created_at, user_id=1):
    row = em.SeriesFollow(user_id=user_id, series_item_id=series.id,
                          status="active", created_at=created_at)
    db.add(row)
    db.commit()
    return row


def _watched(db, item, when, *, user_id=1, position=100):
    row = em.UserMediaData(
        user_id=user_id, item_id=item.id,
        playback_position_ticks=position, played=False,
        last_played_at=when, updated_at=when)
    db.add(row)
    db.commit()
    return row


def _session(db, item, when, *, user_id=1):
    row = em.PlaybackSession(
        session_key=f"dw-test-{item.id}-{user_id}",
        user_id=user_id, item_id=item.id,
        start_time=when, last_update_at=when)
    db.add(row)
    db.commit()
    return row


def _play(db, item, when, *, user_id=1, result="ok"):
    row = em.PlayStartEvent(
        user_id=user_id, item_id=item.id, provider="drive",
        method="drive_relay", result=result, resolve_ms=900,
        created_at=when)
    db.add(row)
    db.commit()
    return row


def _variant(db, item, *, user_id=7, status="ready", fid="copyfid",
             drive_id=None):
    row = em.TransferVariant(
        item_id=item.id, source_file_id=item.drive_file_id,
        variant_file_id=fid,
        variant_drive_id=drive_id or f"userdrive:{user_id}",
        owner_scope="user", owner_ref=str(user_id), status=status)
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def test_config_defaults_and_env_override(monkeypatch):
    cfg = dw.drive_warm_config()
    # 总开关默认关：批 1 上线后由用户点头转开（D6 灰度口径）
    assert cfg["enabled"] is True  # autouse 夹具显式开了，生产默认关见下
    assert cfg["interval_sec"] == 240.0
    assert cfg["concurrency"] == 1  # autouse 夹具钉死为 1（生产默认 2）
    assert cfg["max_targets"] == 120
    assert cfg["max_requests"] == 400
    assert cfg["users_per_item"] == 3
    assert cfg["refresh_lead_sec"] == 30.0
    assert cfg["round_budget_sec"] == 600.0
    assert cfg["busy_plays_5min"] == 30
    assert cfg["load_ratio"] == 0.85
    monkeypatch.delenv("DRIVE_WARM_ENABLED", raising=False)
    assert dw.drive_warm_config()["enabled"] is False
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "1")
    monkeypatch.setenv("DRIVE_WARM_MAX_REQUESTS_PER_ROUND", "7")
    monkeypatch.setenv("DRIVE_WARM_INTERVAL_SEC", "not-a-number")
    cfg = dw.drive_warm_config()
    assert cfg["enabled"] is True
    assert cfg["max_requests"] == 7
    assert cfg["interval_sec"] == 240.0  # 脏值回默认


# ---------------------------------------------------------------------------
# 信号展开：item → 用户（D1：只暖近窗播过该条目的用户）
# ---------------------------------------------------------------------------

def test_follow_expands_to_subscribers(db):
    series = _item(db, "追的剧", "series")
    _follow(db, series, NOW - timedelta(days=1), user_id=11)
    _follow(db, series, NOW - timedelta(days=2), user_id=22)
    cancelled = _follow(db, series, NOW - timedelta(days=1), user_id=33)
    cancelled.status = "cancelled"
    db.commit()
    fresh = _item(db, "新集", "episode", series=series, season=1, ep=9,
                  added=NOW - timedelta(hours=2))
    targets, stats = dw.build_targets(db, _cfg(), NOW)
    got = {(t["user_id"], t["item_id"], t["source"]) for t in targets}
    assert got == {(11, fresh.id, "follow"), (22, fresh.id, "follow")}
    assert stats["follow_items"] == 1


def test_follow_users_per_item_cap_takes_most_recent(db):
    series = _item(db, "热门剧", "series")
    for uid, days in ((11, 3), (22, 2), (33, 1)):
        _follow(db, series, NOW - timedelta(days=days), user_id=uid)
    _item(db, "新集", "episode", series=series, season=1, ep=1,
          added=NOW - timedelta(hours=1))
    targets, _stats = dw.build_targets(db, _cfg(users_per_item=1), NOW)
    assert len(targets) == 1
    assert targets[0]["user_id"] == 33  # 最近订阅者优先


def test_resume_expands_to_watcher(db):
    movie = _item(db, "在看的电影", "movie")
    _watched(db, movie, NOW - timedelta(days=1), user_id=33)
    targets, stats = dw.build_targets(db, _cfg(), NOW)
    assert (33, movie.id, "resume") in {
        (t["user_id"], t["item_id"], t["source"]) for t in targets}
    assert stats["resume_items"] == 1


def test_resume_next_episode_inherits_same_user(db):
    series = _item(db, "在看的剧", "series")
    eps = {}
    for season, count in ((1, 3), (2, 2)):
        for ep in range(1, count + 1):
            eps[(season, ep)] = _item(
                db, f"S{season}E{ep}", "episode", series=series,
                season=season, ep=ep)
    _watched(db, eps[(1, 3)], NOW - timedelta(days=1), user_id=33)
    targets, _stats = dw.build_targets(db, _cfg(), NOW)
    got = {(t["user_id"], t["item_id"]) for t in targets}
    assert (33, eps[(1, 3)].id) in got
    assert (33, eps[(2, 1)].id) in got  # 下一集跟随同一用户


def test_hot_expands_to_players_and_w5_only_when_anonymous(db):
    hot = _item(db, "热门新片", "movie", added=NOW - timedelta(days=1))
    _play(db, hot, NOW - timedelta(hours=1), user_id=11)
    _play(db, hot, NOW - timedelta(hours=2), user_id=22)
    _play(db, hot, NOW - timedelta(hours=3), user_id=22)
    anon = _item(db, "匿名热片", "movie", added=NOW - timedelta(days=1))
    _play(db, anon, NOW - timedelta(hours=1), user_id=None)
    _play(db, anon, NOW - timedelta(hours=2), user_id=None)
    _play(db, anon, NOW - timedelta(hours=3), user_id=None)
    targets, stats = dw.build_targets(db, _cfg(), NOW)
    got = {(t["user_id"], t["item_id"], t["source"]) for t in targets}
    assert (11, hot.id, "hot") in got
    assert (22, hot.id, "hot") in got
    assert (None, anon.id, "hot") in got  # 无用户维度：只做 W5
    assert stats["w5_only"] == 1


def test_build_targets_dedup_same_user_item(db):
    series = _item(db, "双信号剧", "series")
    _follow(db, series, NOW - timedelta(days=1), user_id=11)
    ep = _item(db, "新集", "episode", series=series, season=1, ep=1,
               added=NOW - timedelta(hours=1))
    _watched(db, ep, NOW - timedelta(hours=1), user_id=11)
    targets, _stats = dw.build_targets(db, _cfg(), NOW)
    assert [(t["user_id"], t["item_id"]) for t in targets] == [(11, ep.id)]
    assert targets[0]["source"] == "follow"  # 优先级 follow > resume


def test_build_targets_global_cap(db, monkeypatch):
    items = [_item(db, f"片{i}", "movie") for i in range(4)]
    for it in items:
        _play(db, it, NOW - timedelta(hours=1), user_id=1)
        _play(db, it, NOW - timedelta(hours=2), user_id=1)
        _play(db, it, NOW - timedelta(hours=3), user_id=1)
    targets, _stats = dw.build_targets(db, _cfg(max_targets=3), NOW)
    assert len(targets) == 3


# ---------------------------------------------------------------------------
# W1：交付计划预建（只写现成计划表，点播判定零改动）
# ---------------------------------------------------------------------------

def _stub_exists(monkeypatch, calls, result="exists"):
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)
    monkeypatch.setattr(
        dw, "_exists_check",
        lambda db_, item, fid: calls.append(fid) or result)


def test_w1_builds_plan_when_missing(db, factory, monkeypatch):
    item = _item(db, "待暖片", "movie")
    _variant(db, item, user_id=7)
    _stub_exists(monkeypatch, [])
    calls = []
    real_build = dp.build_plan
    monkeypatch.setattr(
        dp, "build_plan",
        lambda db_, uid, it: calls.append((uid, it.id)) or real_build(db_, uid, it))
    res = dw._warm_one({"user_id": 7, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w1"] == "built"
    assert calls == [(7, item.id)]  # 只给目标用户本人建计划
    plan = dp.get_fresh_plan(db, 7, item.id)
    assert plan is not None
    assert plan["legs"][0]["leg"] == "user_copy_ready"
    # 身份铁律：没给别人建计划
    assert dp.get_fresh_plan(db, 8, item.id) is None


def test_w1_fresh_skips_rebuild(db, factory, monkeypatch):
    item = _item(db, "已暖片", "movie")
    _variant(db, item, user_id=7)
    dp.build_plan(db, 7, item)
    _stub_exists(monkeypatch, [])
    calls = []
    real_build = dp.build_plan
    monkeypatch.setattr(
        dp, "build_plan",
        lambda db_, uid, it: calls.append(1) or real_build(db_, uid, it))
    res = dw._warm_one({"user_id": 7, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w1"] == "fresh"
    assert calls == []  # 新鲜计划不重建


def test_w1_skipped_when_plan_disabled(db, factory, monkeypatch):
    item = _item(db, "计划关", "movie")
    _variant(db, item, user_id=7)
    _stub_exists(monkeypatch, [])
    monkeypatch.setattr(dp, "delivery_plan_enabled", lambda db_=None: False)
    res = dw._warm_one({"user_id": 7, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w1"] == "skipped"
    assert dp.get_fresh_plan(db, 7, item.id) is None


def test_w1_skipped_for_w5_only_target(db, factory, monkeypatch):
    item = _item(db, "无用户片", "movie")
    _stub_exists(monkeypatch, [])
    res = dw._warm_one({"user_id": None, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w1"] == "skipped"


# ---------------------------------------------------------------------------
# W5：存在性保鲜（SA 池校验 → 既有发布函数写正缓存）
# ---------------------------------------------------------------------------

def test_w5_publishes_on_exists(db, factory, monkeypatch):
    item = _item(db, "存在片", "movie", drive_file_id="fid-exists")
    calls = []
    _stub_exists(monkeypatch, calls, result="exists")
    res = dw._warm_one({"user_id": None, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "published"
    assert calls == ["fid-exists"]


def test_w5_gone_and_unavailable_do_not_raise(db, factory, monkeypatch):
    gone = _item(db, "已删片", "movie", drive_file_id="fid-gone")
    calls = []
    _stub_exists(monkeypatch, calls, result="gone")
    res = dw._warm_one({"user_id": None, "item_id": gone.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "gone"
    _stub_exists(monkeypatch, calls, result="unavailable")
    res = dw._warm_one({"user_id": None, "item_id": gone.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "failed"


def test_w5_fresh_skips_check(db, factory, monkeypatch):
    item = _item(db, "新鲜片", "movie", drive_file_id="fid-fresh")
    calls = []
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: 500)
    monkeypatch.setattr(
        dw, "_exists_check",
        lambda db_, it, fid: calls.append(fid) or "exists")
    res = dw._warm_one({"user_id": None, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "fresh"
    assert calls == []  # 正缓存还新鲜，不再打 files.get


def test_w5_checks_copy_fid_too(db, factory, monkeypatch):
    item = _item(db, "有副本片", "movie", drive_file_id="fid-orig")
    _variant(db, item, user_id=7, fid="fid-copy")
    calls = []
    _stub_exists(monkeypatch, calls, result="exists")
    res = dw._warm_one({"user_id": 7, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "published"
    assert set(calls) == {"fid-orig", "fid-copy"}


def test_w5_skipped_when_no_fid(db, factory, monkeypatch):
    item = _item(db, "无锚片", "movie", drive_file_id="")
    item.drive_file_id = ""
    db.commit()
    calls = []
    _stub_exists(monkeypatch, calls, result="exists")
    res = dw._warm_one({"user_id": None, "item_id": item.id, "source": "hot"},
                       _cfg(), dw._RequestCounter(), factory,
                       time.monotonic())
    assert res["w5"] == "skipped"
    assert calls == []


# ---------------------------------------------------------------------------
# 整轮：让路 / 计数 / 预算 / 状态
# ---------------------------------------------------------------------------

def _one_target(monkeypatch, user_id, item_id):
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": user_id, "item_id": item_id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 1,
         "items": 1, "targets": 1, "w5_only": 0}))


def test_warm_once_disabled(factory, monkeypatch):
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "0")
    stats = dw.warm_once(factory)
    assert stats["ran"] is False
    assert stats["reason"] == "disabled"


def test_warm_once_busy_round_skips(factory, db, monkeypatch):
    movie = _item(db, "繁忙时段", "movie")
    for _i in range(3):
        _play(db, movie, datetime.now(), user_id=1)
    _one_target(monkeypatch, 7, movie.id)
    monkeypatch.setenv("DRIVE_WARM_BUSY_PLAYS_5MIN", "3")
    stats = dw.warm_once(factory)
    assert stats["ran"] is False
    assert stats["reason"] == "busy"


def test_warm_once_full_round_counts(factory, db, monkeypatch):
    item = _item(db, "整轮片", "movie", drive_file_id="fid-round")
    _variant(db, item, user_id=7)
    _one_target(monkeypatch, 7, item.id)
    calls = []
    _stub_exists(monkeypatch, calls, result="exists")
    stats = dw.warm_once(factory)
    assert stats["ran"] is True
    assert stats["targets"] == 1
    assert stats["warmed"] == 1
    assert stats["w1"]["built"] == 1
    assert stats["w5"]["published"] == 1
    assert stats["requests"] == 2  # 原件 fid + 本人副本 fid 各 1 次
    assert dp.get_fresh_plan(db, 7, item.id) is not None


def test_warm_once_request_budget(factory, db, monkeypatch):
    a = _item(db, "预算甲", "movie", drive_file_id="fid-a")
    b = _item(db, "预算乙", "movie", drive_file_id="fid-b")
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": None, "item_id": a.id, "source": "hot"},
         {"user_id": None, "item_id": b.id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 2,
         "items": 2, "targets": 2, "w5_only": 2}))
    _stub_exists(monkeypatch, [], result="exists")
    monkeypatch.setenv("DRIVE_WARM_MAX_REQUESTS_PER_ROUND", "1")
    stats = dw.warm_once(factory)
    assert stats["requests"] == 1
    assert stats["warmed"] == 1
    assert stats["budget"] == 1


def test_warm_once_failure_isolated(factory, db, monkeypatch):
    a = _item(db, "会失败的片", "movie", drive_file_id="fid-fail")
    b = _item(db, "会成功的片", "movie", drive_file_id="fid-ok")
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": None, "item_id": a.id, "source": "hot"},
         {"user_id": None, "item_id": b.id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 2,
         "items": 2, "targets": 2, "w5_only": 2}))
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)

    def flaky(db_, item, fid):
        if fid == "fid-fail":
            return "unavailable"
        return "exists"

    monkeypatch.setattr(dw, "_exists_check", flaky)
    stats = dw.warm_once(factory)
    assert stats["warmed"] == 1
    assert stats["failed"] == 1
    assert stats["w5"]["failed"] == 1
    assert stats["w5"]["published"] == 1


def test_status_snapshot_after_round(factory, db, monkeypatch):
    item = _item(db, "状态片", "movie", drive_file_id="fid-status")
    _one_target(monkeypatch, 7, item.id)
    _stub_exists(monkeypatch, [], result="exists")
    dw.warm_once(factory)
    snap = dw.status_snapshot()
    assert snap["last_round"]["warmed"] == 1
    assert snap["last_round"]["targets"] == 1
    assert snap["enabled"] is True


def test_start_returns_false_when_disabled(monkeypatch):
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "0")
    assert dw.start() is False
