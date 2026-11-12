# -*- coding: utf-8 -*-
"""暖地址批 2 验收不过的修复（goal-35 命中对齐）。

验收实证（2026-11-12 小流量）：机制本身通（直调裁决入口 4.2ms
命中缓存），但真实命中起播 resolve 中位 ≈987ms、与未暖基线无
差别。本文件钉死三条根因与修复：

1. 时序对不上（user 789 / item 773506 实案，生产只读核对）：
   该 (user,item) 只有一行 ready 副本，created_at 比点播晚 2 秒
   ——副本是点播当场办成才就绪的，暖轮次里根本没有 ready 副本
   可探（W2b skipped），点播只能对新 fid 现探。修复：副本登记
   ready 的唯一收口 ``_register_ready_variant`` 顺手预探裁决
   （与既有「登记后预填存在性正缓存」同位、守护线程不阻塞）。
2. 节律对不上（系统性）：裁决/复用 TTL 300s ≈ 工人轮周期
   ≈280s，且工人新鲜判定对 ok 复用只看「还活着」（剩余 >0 即
   fresh）——第 N 轮探、N+1 轮跳、N+2 轮才重探，覆盖窗与空窗
   交替，点播近半概率落进空窗；计划 TTL 120s 更是不到半个轮
   周期。修复：三处 TTL 默认 300/300/120 → 600（≈2 个轮周期，
   仍可配）；工人新鲜阈值改为「剩余 > 提前量 + 一轮间隔」，
   ok 复用剩余不足时先清复用再调判定点，逼出真探测续满窗口。
3. 仪器缺口：emby_play_start_events 的 warm_hits/warm_in_signal
   两列无调用方传参、恒 NULL。修复：工人发布目标集 + 步骤标记
   （Redis），api 的起播打点安全口在落事件时回填两列；工人没
   在跑（目标集不存在）时两列保持 NULL（未判定口径不变）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, CacheManager
from backend.emby_server import models as em
from backend.emby_server import delivery_plan as dp
from backend.emby_server import drive_warm_worker as dw
from backend.emby_server import transfer_variant as tv
import backend.models  # noqa: F401 — 注册 web_users / system_configs 等表

NOW = datetime(2026, 11, 12, 12, 0, 0)


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
    # store 有进程级 TTL 缓存（test_delivery_plan 同款隔离）：
    # 不清会把别用例写进的 delivery_plan_ttl_sec 读串台。
    from backend.integrations import store
    store.invalidate()
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "1")
    monkeypatch.setenv("DRIVE_WARM_JITTER_SEC", "0")
    monkeypatch.setenv("DRIVE_WARM_CONCURRENCY", "1")
    for key in list(os.environ):
        if key.startswith("DRIVE_WARM_") and key not in (
                "DRIVE_WARM_ENABLED", "DRIVE_WARM_JITTER_SEC",
                "DRIVE_WARM_CONCURRENCY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TRANSFER_USERCOPY_QUOTA_PROBE_TTL_SEC",
                       raising=False)
    monkeypatch.delenv("TRANSFER_USERCOPY_QUOTA_PROBE_ENABLED",
                       raising=False)
    monkeypatch.setattr(dw, "_load_ratio", lambda: 0.1)
    dw._warmed_at.clear()
    dw._last_round.clear()
    CacheManager.delete(dw.STATUS_CACHE_KEY)
    yield
    dw._warmed_at.clear()
    dw._last_round.clear()
    CacheManager.delete(dw.STATUS_CACHE_KEY)
    store.invalidate()


def _item(db, name, *, drive_file_id=None):
    it = em.MediaItem(
        guid=f"g-{name}-{os.urandom(4).hex()}",
        library_id=1, item_type="movie", name=name,
        file_path="mount://3/电影/a.mkv",
        drive_file_id=drive_file_id or f"fid-{name}",
        date_added=NOW,
    )
    db.add(it)
    db.commit()
    return it


def _variant(db, item, *, user_id=7, fid="copyfid"):
    row = em.TransferVariant(
        item_id=item.id, source_file_id=item.drive_file_id,
        variant_file_id=fid,
        variant_drive_id=f"userdrive:{user_id}",
        owner_scope="user", owner_ref=str(user_id), status="ready")
    db.add(row)
    db.commit()
    return row


def _cfg(**kw):
    cfg = dw.drive_warm_config()
    cfg.update(kw)
    return cfg


# ---------------------------------------------------------------------------
# 根因 2（节律）：TTL 默认值必须覆盖两个轮周期（间隔 240 + 单轮执行）
# ---------------------------------------------------------------------------

def test_ttl_defaults_cover_round_period(db, factory, monkeypatch):
    # 交付计划：未配时默认 600（旧 120 不到半个轮周期，计划窗大半时间过期）
    assert dp.DELIVERY_PLAN_TTL_DEFAULT == 600
    assert dp.plan_ttl_sec(db) == 600
    # 额度裁决缓存：env 未配时默认 600（旧 300 ≈ 一个轮周期）
    assert tv._usercopy_quota_probe_ttl() == 600
    # ok 复用记录：SystemConfig 未配时默认 600
    assert tv.USERCOPY_PROBE_OK_REUSE_TTL_DEFAULT == 600
    monkeypatch.setattr("backend.database.SessionLocal", factory)
    assert tv._usercopy_probe_ok_reuse_ttl() == 600


def test_ttl_defaults_still_configurable(db, monkeypatch):
    # 可配口径不变：env / SystemConfig 给了值就按值走
    monkeypatch.setenv("TRANSFER_USERCOPY_QUOTA_PROBE_TTL_SEC", "450")
    assert tv._usercopy_quota_probe_ttl() == 450
    dp.write_config(db, enabled=True, ttl_sec=300)
    assert dp.plan_ttl_sec(db) == 300


# ---------------------------------------------------------------------------
# 根因 2（节律）：工人新鲜阈值 = 提前量 + 一轮间隔；复用剩余不足先清后探
# ---------------------------------------------------------------------------

class QuotaSeams:
    def __init__(self, monkeypatch, state):
        self.decide_calls = []
        self.clear_calls = []
        monkeypatch.setattr(dw, "_active_drive_binding",
                            lambda db_, uid: object())
        monkeypatch.setattr(dw, "_quota_probe_enabled", lambda: True)
        monkeypatch.setattr(dw, "_quota_cache_state",
                            lambda uid, fid: dict(state))
        monkeypatch.setattr(
            dw, "_quota_decide",
            lambda uid, fid: self.decide_calls.append((uid, fid)) or "ok")
        monkeypatch.setattr(
            dw, "_quota_reuse_clear",
            lambda uid, fid: self.clear_calls.append((uid, fid)),
            raising=False)


def test_w2b_reuse_within_round_margin_reprobes(db, monkeypatch):
    """复用还活着（剩余 200s）但撑不到下下轮（阈值 30+240=270）：
    旧口径记 fresh、窗口在 200s 后断档；新口径先清复用再调判定
    点，逼出一次真探测把窗口续满。"""
    seams = QuotaSeams(monkeypatch, {
        "verdict": None, "page": None,
        "verdict_ttl": None, "reuse_ttl": 200})
    code, n = dw._warm_quota(
        db, {"user_id": 7, "item_id": 1}, "fid-x", _cfg(),
        dw._RequestCounter())
    assert code == "probed"
    assert n == 2
    assert seams.clear_calls == [(7, "fid-x")]
    assert seams.decide_calls == [(7, "fid-x")]


def test_w2b_reuse_beyond_round_margin_stays_fresh(db, monkeypatch):
    """复用剩余 400s > 阈值 270：判定点到下轮仍吃得到，记 fresh。"""
    seams = QuotaSeams(monkeypatch, {
        "verdict": None, "page": None,
        "verdict_ttl": None, "reuse_ttl": 400})
    code, n = dw._warm_quota(
        db, {"user_id": 7, "item_id": 1}, "fid-x", _cfg(),
        dw._RequestCounter())
    assert code == "fresh"
    assert n == 0
    assert seams.decide_calls == []
    assert seams.clear_calls == []


def test_w1_rebuilds_when_plan_expires_within_round_margin(
        db, factory, monkeypatch):
    """计划还剩 100s（> 旧提前量 30）但撑不到下下轮：旧口径记
    fresh、100s 后计划过期，点播命中不了计划腿；新口径提前重建。"""
    item = _item(db, "将过期计划片")
    _variant(db, item, user_id=7)
    dp.build_plan(db, 7, item)
    row = db.query(em.PlaybackDeliveryPlan).first()
    soon = datetime.now() + timedelta(seconds=100)
    row.expires_at = soon
    # 工人读的是 get_fresh_plan 解析出的 plan_json（expires_at
    # 在 JSON 里），列与 JSON 必须一起改才算真造出将过期计划。
    payload = json.loads(row.plan_json)
    payload["expires_at"] = soon.isoformat()
    row.plan_json = json.dumps(payload)
    db.commit()
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)
    monkeypatch.setattr(dw, "_exists_check",
                        lambda db_, it, fid: "exists")
    calls = []
    real_build = dp.build_plan
    monkeypatch.setattr(
        dp, "build_plan",
        lambda db_, uid, it: calls.append((uid, it.id))
        or real_build(db_, uid, it))
    res = dw._warm_one(
        {"user_id": 7, "item_id": item.id, "source": "hot"},
        _cfg(), dw._RequestCounter(), factory, time.monotonic())
    assert res["w1"] == "built"
    assert calls == [(7, item.id)]


# ---------------------------------------------------------------------------
# 根因 1（时序）：副本登记 ready 时预探裁决（点播当场办成的副本首播即命中）
# ---------------------------------------------------------------------------

def _job(db, item, *, scope="user", ref="7", dest="userdrive:7"):
    job = em.TransferJob(
        item_id=item.id, trigger="playback", provider="drive",
        owner_scope=scope, owner_ref=ref, dest_drive_id=dest,
        status="running")
    db.add(job)
    db.commit()
    return job


def test_register_ready_user_copy_spawns_quota_prewarm(
        db, monkeypatch):
    item = _item(db, "当场办成片")
    job = _job(db, item)
    calls = []
    monkeypatch.setattr(
        tv, "_spawn_quota_prewarm",
        lambda uid, fid: calls.append((uid, fid)), raising=False)
    row = tv._register_ready_variant(db, job, item, "fid-new",
                                     "userdrive:7")
    assert row.status == "ready"
    # 缝收到 owner_ref 原样（字符串），转 int 在 _spawn 内部
    assert calls == [("7", "fid-new")]  # 只预探本人新副本


def test_register_ready_pool_or_115_copy_no_prewarm(db, monkeypatch):
    item = _item(db, "池与115片")
    calls = []
    monkeypatch.setattr(
        tv, "_spawn_quota_prewarm",
        lambda uid, fid: calls.append((uid, fid)), raising=False)
    pool_job = _job(db, item, scope="pool", ref="3", dest="pooldrive:3")
    tv._register_ready_variant(db, pool_job, item, "fid-pool",
                               "pooldrive:3")
    job115 = _job(db, item, dest="pan115:user-7")
    tv._register_ready_variant(db, job115, item, "fid-115",
                               "pan115:user-7")
    assert calls == []  # 池副本走 poolvar 口径、115 不是 Drive fid


class _FakeThread:
    def __init__(self, target=None, daemon=None, name=None):
        self._target = target
        self._alive = False

    def start(self):
        self._alive = True
        try:
            self._target()
        finally:
            self._alive = False

    def is_alive(self):
        # 与真 Thread 同语义：已启动且未跑完才 True。本替身是同步执行，
        # start() 返回时目标已跑完，故跑完后 is_alive() 为 False。
        return self._alive


def test_spawn_quota_prewarm_runs_decision(db, monkeypatch):
    seen = []
    monkeypatch.setattr(
        tv, "probe_user_copy_quota_decision",
        lambda uid, fid: seen.append((uid, fid)) or "ok")
    fake_threading = SimpleNamespace(
        Thread=_FakeThread, Lock=threading.Lock)
    monkeypatch.setattr(tv, "threading", fake_threading)
    tv._spawn_quota_prewarm(7, "fid-new")
    assert seen == [(7, "fid-new")]


def test_spawn_quota_prewarm_respects_probe_switch(monkeypatch):
    monkeypatch.setenv("TRANSFER_USERCOPY_QUOTA_PROBE_ENABLED", "0")
    seen = []
    monkeypatch.setattr(
        tv, "probe_user_copy_quota_decision",
        lambda uid, fid: seen.append((uid, fid)) or "ok")
    fake_threading = SimpleNamespace(
        Thread=_FakeThread, Lock=threading.Lock)
    monkeypatch.setattr(tv, "threading", fake_threading)
    tv._spawn_quota_prewarm(7, "fid-new")
    assert seen == []  # 探测关时不预探（与判定点关时恒 ok 同口径）


# ---------------------------------------------------------------------------
# 根因 3（仪器）：工人发布目标集/步骤标记 → 起播事件回填 warm 两列
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.sets = {}
        self.expiries = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        n = 0
        for key in keys:
            n += int(self.kv.pop(key, None) is not None)
            n += int(self.sets.pop(key, None) is not None)
        return n

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def sismember(self, key, member):
        return member in self.sets.get(key, set())

    def exists(self, key):
        return int(key in self.kv or key in self.sets)

    def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(dw, "_redis", lambda: fake, raising=False)
    return fake


def test_targets_publish_and_attribution(fake_redis):
    dw._publish_targets(
        [{"user_id": 7, "item_id": 42, "source": "hot"},
         {"user_id": None, "item_id": 43, "source": "hot"}],
        _cfg())
    # (用户, 条目) 对进目标集；W5-only 无用户维度不进对集
    assert dw.warm_attribution(7, 42)["warm_in_signal"] is True
    assert dw.warm_attribution(8, 42)["warm_in_signal"] is False
    assert dw.warm_attribution(7, 99)["warm_in_signal"] is False
    assert dw.warm_attribution(7, 42)["warm_hits"] == []


def test_attribution_unjudged_when_worker_not_publishing(fake_redis):
    # 目标集不存在（工人没在跑）→ 未判定：两值 None，落库保持 NULL
    out = dw.warm_attribution(7, 42)
    assert out == {"warm_hits": None, "warm_in_signal": None}


def test_attribution_unjudged_when_redis_down(monkeypatch):
    monkeypatch.setattr(dw, "_redis", lambda: None, raising=False)
    out = dw.warm_attribution(7, 42)
    assert out == {"warm_hits": None, "warm_in_signal": None}


def test_warm_one_writes_step_markers(db, factory, monkeypatch,
                                     fake_redis):
    item = _item(db, "标记片")
    _variant(db, item, user_id=7, fid="fid-copy")
    monkeypatch.setattr(dw, "_active_drive_binding",
                        lambda db_, uid: object())
    monkeypatch.setattr(dw, "_copy_public_valid",
                        lambda db_, uid, fid: True)
    monkeypatch.setattr(dw, "_quota_probe_enabled", lambda: True)
    monkeypatch.setattr(dw, "_quota_cache_state", lambda uid, fid: {
        "verdict": None, "page": None,
        "verdict_ttl": None, "reuse_ttl": None})
    monkeypatch.setattr(dw, "_quota_decide", lambda uid, fid: "ok")
    monkeypatch.setattr(dw, "_token_expires_at",
                        lambda db_, b: int(time.time()) + 7200)
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)
    monkeypatch.setattr(dw, "_exists_check",
                        lambda db_, it, fid: "exists")
    dw._publish_targets(
        [{"user_id": 7, "item_id": item.id, "source": "hot"}], _cfg())
    res = dw._warm_one(
        {"user_id": 7, "item_id": item.id, "source": "hot"},
        _cfg(), dw._RequestCounter(), factory, time.monotonic())
    assert res["w1"] == "built"
    assert res["w2b"] == "probed"
    assert res["w5"] == "published"
    out = dw.warm_attribution(7, item.id)
    assert out["warm_in_signal"] is True
    assert out["warm_hits"] == ["plan", "quota", "exists"]
    # 标记 TTL 与各自载体同寿：计划 600 / 裁决 600 / 存在性 600
    assert fake_redis.expiries[
        f"{dw._MARK_PLAN_PREFIX}7:{item.id}"] == 600
    assert fake_redis.expiries[
        f"{dw._MARK_QUOTA_PREFIX}7:{item.id}"] == 600
    assert fake_redis.expiries[
        f"{dw._MARK_EXISTS_PREFIX}{item.id}"] == 600


def test_note_play_start_safe_attaches_attribution(monkeypatch):
    from backend.emby_server import api, play_events

    captured = {}
    monkeypatch.setattr(
        play_events, "note_play_start",
        lambda **kw: captured.update(kw))
    monkeypatch.setattr(
        dw, "warm_attribution",
        lambda uid, iid: {"warm_hits": ["quota"],
                          "warm_in_signal": True})
    item = SimpleNamespace(
        id=42, file_path="mount://3/电影/a.mkv", drive_file_id="fid-o")
    api._note_play_start_safe(item, user_id=7, method="direct_302",
                              variant="drive_direct", result="ok")
    assert captured["warm_hits"] == ["quota"]
    assert captured["warm_in_signal"] is True


def test_note_play_start_safe_attribution_failure_keeps_event(
        monkeypatch):
    from backend.emby_server import api, play_events

    captured = {}
    monkeypatch.setattr(
        play_events, "note_play_start",
        lambda **kw: captured.update(kw))

    def _boom(uid, iid):
        raise RuntimeError("redis 炸了")

    monkeypatch.setattr(dw, "warm_attribution", _boom)
    item = SimpleNamespace(
        id=42, file_path="mount://3/电影/a.mkv", drive_file_id="fid-o")
    api._note_play_start_safe(item, user_id=7, method="direct_302",
                              result="ok")
    # 归因失败只丢归因：事件照常记、两列不带（落库 NULL）
    assert captured["item_id"] == 42
    assert "warm_hits" not in captured
    assert "warm_in_signal" not in captured


def test_note_play_start_safe_respects_explicit_warm_kwargs(
        monkeypatch):
    from backend.emby_server import api, play_events

    captured = {}
    monkeypatch.setattr(
        play_events, "note_play_start",
        lambda **kw: captured.update(kw))

    def _boom(uid, iid):
        raise AssertionError("显式传参时不许再算归因")

    monkeypatch.setattr(dw, "warm_attribution", _boom)
    item = SimpleNamespace(
        id=42, file_path="mount://3/电影/a.mkv", drive_file_id="fid-o")
    api._note_play_start_safe(item, user_id=7, result="ok",
                              warm_hits="plan", warm_in_signal=False)
    assert captured["warm_hits"] == "plan"
    assert captured["warm_in_signal"] is False
