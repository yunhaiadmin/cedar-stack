# -*- coding: utf-8 -*-
"""暖地址重做批 2 · W2（副本授权＋额度裁决）＋W3（令牌保鲜）（goal-35）。

钉死口径（设计稿 design-v1.md §4/§5.2，D1–D7 已按推荐口径定版）：
- W2a 副本 anyone-reader 授权：工人对信号目标的本人 ready 副本
  幂等确保一次（调 transfer_variant.ensure_user_copy_public 既有
  入口）；已公开缓存新鲜时零网络跳过。
- W2b 额度探针裁决：工人调判定点唯一入口
  probe_user_copy_quota_decision 提前探测，裁决落现成缓存
  （裁决缓存＋ok 复用记录）；点播时判定点命中缓存不再现探。
  新鲜判定逐字镜像判定点的吃缓存口径：quota 裁决 / ok＋明细
  page=quota 恒新鲜（到期重探定案）；ok 复用记录须剩余 >
  跨轮余量（提前量＋一轮间隔）才新鲜，不足先清复用逼真探测
  续窗（2026-11-12 批 2 验收修复，见 test_drive_warm_hit_align）。
- W3 本人令牌保鲜：只调 drive_oauth 唯一令牌口
  get_user_drive_access_token；令牌离过期还远（刷新边距＋提前量
  之外）零网络跳过；每用户每轮至多处理一次。
- 身份铁律：W2/W3 只用目标用户本人绑定与本人副本；无生效中
  Drive 绑定 → 整步跳过（点播门禁本就不许无绑定播放）。
- 点播路径判定逻辑零改动：工人只写现成载体，全冷时点播与
  今天逐字一致（现探、现授、现刷）；总开关关时工人零动作。
- 资源纪律沿批 1：单轮请求预算硬帽（W2a 确保 1 次、W2b 新鲜
  探测 2 次、W3 刷新 1 次计数）、随机抖动、让路不变。
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
from backend.emby_server import drive_warm_worker as dw
from backend.emby_server import transfer_variant as tv
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


def _item(db, name, item_type="movie", *, series=None, drive_file_id=None):
    it = em.MediaItem(
        guid=f"g-{name}-{os.urandom(4).hex()}",
        library_id=1, item_type=item_type, name=name,
        file_path="mount://3/电影/a.mkv",
        drive_file_id=drive_file_id or f"fid-{name}",
        series_id=series.id if series is not None else None,
        date_added=NOW,
    )
    db.add(it)
    db.commit()
    return it


def _variant(db, item, *, user_id=7, status="ready", fid="copyfid"):
    row = em.TransferVariant(
        item_id=item.id, source_file_id=item.drive_file_id,
        variant_file_id=fid,
        variant_drive_id=f"userdrive:{user_id}",
        owner_scope="user", owner_ref=str(user_id), status=status)
    db.add(row)
    db.commit()
    return row


def _cfg(**kw):
    cfg = dw.drive_warm_config()
    cfg.update(kw)
    return cfg


class Seams:
    """批 2 全部衔接缝的打桩台：默认「无绑定、无副本外动作」。"""

    def __init__(self, monkeypatch):
        self.ensure_calls = []
        self.decide_calls = []
        self.token_ensure_calls = []
        # W5 缝（批 1）：正缓存永不新鲜、实查恒 exists
        monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)
        monkeypatch.setattr(dw, "_exists_check",
                            lambda db_, item, fid: "exists")
        # W2/W3 缝：默认无生效中绑定（W2/W3 整步跳过）
        self.binding = None
        monkeypatch.setattr(dw, "_active_drive_binding",
                            lambda db_, uid: self.binding)
        self.public_valid = False
        monkeypatch.setattr(dw, "_copy_public_valid",
                            lambda db_, uid, fid: self.public_valid)
        self.ensure_result = True
        monkeypatch.setattr(
            dw, "_ensure_copy_public",
            lambda db_, uid, fid: self.ensure_calls.append((uid, fid))
            or self.ensure_result)
        self.probe_enabled = True
        monkeypatch.setattr(dw, "_quota_probe_enabled",
                            lambda: self.probe_enabled)
        self.quota_state = {"verdict": None, "page": None,
                            "verdict_ttl": None, "reuse_ttl": None}
        monkeypatch.setattr(dw, "_quota_cache_state",
                            lambda uid, fid: dict(self.quota_state))
        self.decide_result = "quota"
        monkeypatch.setattr(
            dw, "_quota_decide",
            lambda uid, fid: self.decide_calls.append((uid, fid))
            or self.decide_result)
        self.clear_calls = []
        monkeypatch.setattr(
            dw, "_quota_reuse_clear",
            lambda uid, fid: self.clear_calls.append((uid, fid)),
            raising=False)
        self.token_expires_at = int(time.time()) + 7200
        monkeypatch.setattr(dw, "_token_expires_at",
                            lambda db_, binding: self.token_expires_at)
        self.token_ensure_result = True
        monkeypatch.setattr(
            dw, "_token_ensure",
            lambda db_, uid: self.token_ensure_calls.append(uid)
            or self.token_ensure_result)

    def with_binding(self):
        self.binding = object()
        return self


@pytest.fixture()
def seams(monkeypatch):
    return Seams(monkeypatch)


def _warm(factory, user_id, item_id):
    return dw._warm_one(
        {"user_id": user_id, "item_id": item_id, "source": "hot"},
        _cfg(), dw._RequestCounter(), factory, time.monotonic())


def _wait_http_calls(http_calls, count=2, timeout=5.0):
    """等后台 GET 腿落定再断言：GET 与 Range 并行提交，追加顺序
    不定；轮询等条数到位，超时后照常用 sorted 断言（会红、不
    是静默放过）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(http_calls) >= count:
            return
        time.sleep(0.005)


# ---------------------------------------------------------------------------
# W2a：副本 anyone-reader 授权幂等确保
# ---------------------------------------------------------------------------

def test_w2a_ensures_public_when_cache_cold(db, factory, seams):
    item = _item(db, "待授片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    res = _warm(factory, 7, item.id)
    assert res["w2a"] == "ensured"
    assert seams.ensure_calls == [(7, "fid-copy")]  # 只授本人副本


def test_w2a_fresh_when_public_cache_valid(db, factory, seams):
    item = _item(db, "已授片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.public_valid = True
    res = _warm(factory, 7, item.id)
    assert res["w2a"] == "fresh"
    assert seams.ensure_calls == []  # 已公开缓存新鲜：零网络


def test_w2a_failed_when_ensure_fails(db, factory, seams):
    item = _item(db, "授败片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.ensure_result = False
    res = _warm(factory, 7, item.id)
    assert res["w2a"] == "failed"


def test_w2a_skipped_without_binding(db, factory, seams):
    item = _item(db, "无绑定片")
    _variant(db, item, user_id=7, fid="fid-copy")
    res = _warm(factory, 7, item.id)
    assert res["w2a"] == "skipped"
    assert seams.ensure_calls == []


def test_w2a_skipped_without_ready_copy(db, factory, seams):
    item = _item(db, "无副本片")
    seams.with_binding()
    res = _warm(factory, 7, item.id)
    assert res["w2a"] == "skipped"
    assert seams.ensure_calls == []


def test_w2_steps_skipped_for_w5_only_target(db, factory, seams):
    item = _item(db, "无用户片")
    res = _warm(factory, None, item.id)
    assert res["w2a"] == "skipped"
    assert res["w2b"] == "skipped"
    assert res["w3"] == "skipped"
    assert seams.ensure_calls == []
    assert seams.decide_calls == []
    assert seams.token_ensure_calls == []


# ---------------------------------------------------------------------------
# W2b：额度探针裁决预探（判定点唯一入口；新鲜口径镜像判定点）
# ---------------------------------------------------------------------------

def test_w2b_probes_when_cache_cold(db, factory, seams):
    item = _item(db, "待探片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.public_valid = True  # 隔离 W2a，只看 W2b
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "probed"
    assert seams.decide_calls == [(7, "fid-copy")]


def test_w2b_fresh_when_quota_cached_beyond_lead(db, factory, seams):
    item = _item(db, "已判额片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.quota_state = {"verdict": "quota", "page": "quota",
                         "verdict_ttl": 200, "reuse_ttl": None}
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "fresh"
    assert seams.decide_calls == []  # quota 吃缓存：判定点不会现探


def test_w2b_fresh_when_ok_with_quota_page_signal(db, factory, seams):
    item = _item(db, "整拉额片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.quota_state = {"verdict": "ok", "page": "quota",
                         "verdict_ttl": 200, "reuse_ttl": None}
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "fresh"
    assert seams.decide_calls == []  # GET 腿额度页信号同样吃缓存


def test_w2b_reuse_within_round_margin_reprobes(db, factory, seams):
    # 批 2 验收修复后的节律：复用剩余 200 ≤ 跨轮余量
    # （refresh_lead 30 + interval 240 = 270）→ 先清复用再调判定
    # 点逼真探测续窗（旧口径只看「还活着」记 fresh，复用在下一
    # 轮前过期、覆盖出空窗，点播落进去现探——验收实证）。
    item = _item(db, "复用片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.quota_state = {"verdict": None, "page": None,
                         "verdict_ttl": None, "reuse_ttl": 200}
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "probed"
    assert seams.clear_calls == [(7, "fid-copy")]
    assert seams.decide_calls == [(7, "fid-copy")]


def test_w2b_fresh_when_reuse_covers_next_round(db, factory, seams):
    item = _item(db, "复用充裕片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.quota_state = {"verdict": None, "page": None,
                         "verdict_ttl": None, "reuse_ttl": 400}
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "fresh"
    assert seams.decide_calls == []  # 复用撑得到下下轮：判定点直接吃
    assert seams.clear_calls == []


def test_w2b_redecides_when_ttl_within_lead(db, factory, seams):
    item = _item(db, "将过期片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.quota_state = {"verdict": "quota", "page": "quota",
                         "verdict_ttl": 10, "reuse_ttl": None}
    res = _warm(factory, 7, item.id)
    # TTL 已进提前量：判定点仍吃缓存（不现探），本轮记新鲜、
    # 不耗请求；到期后的点播或下一轮自然重探（现行口径）。
    assert res["w2b"] == "fresh"
    assert seams.decide_calls == []


def test_w2b_skipped_when_probe_disabled(db, factory, seams):
    item = _item(db, "探测关片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.probe_enabled = False
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "skipped"
    assert seams.decide_calls == []


def test_w2b_failed_when_probe_errors(db, factory, seams):
    item = _item(db, "探败片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.decide_result = "error"
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "failed"


def test_w2b_warm_hit_means_playback_skips_probe_http(
        db, factory, monkeypatch):
    """端到端（先红后绿核心）：工人预探后，点播判定点不再现探。

    不打桩工人的 W2 缝（走 transfer_variant 真实现），只打桩
    探针的两条 HTTP 腿：第一腿回额度页 → 裁决 quota 落缓存。
    2026-11-12 并行化后两腿同时提交，计数按集合比（顺序不定）。
    批 1 代码没有 W2：工人跑完后判定点仍要现探（HTTP 计数
    增加）→ 红；批 2 暖完后判定点吃缓存 → 计数不动 → 绿。
    """
    item = _item(db, "端到端片")
    _variant(db, item, user_id=7, fid="fid-e2e")
    # 工人侧衔接：绑定在、已公开缓存冷（W2a 会真跑但无绑定令牌
    # 时 ensure 内部回 False，不影响 W2b）；W5 缝照批 1 打桩。
    monkeypatch.setattr(dw, "_exists_ok_remaining", lambda fid: None)
    monkeypatch.setattr(dw, "_exists_check",
                        lambda db_, it, fid: "exists")
    monkeypatch.setattr(dw, "_active_drive_binding",
                        lambda db_, uid: object())
    http_calls = []

    def fake_range(url):
        http_calls.append("range")
        return 200, "text/html", b"<html>download quota exceeded</html>"

    def fake_get(url):
        http_calls.append("get")
        return 200, "text/html", b"<html>download quota exceeded</html>"

    monkeypatch.setattr(tv, "_usercopy_probe_http", fake_range)
    monkeypatch.setattr(tv, "_usercopy_probe_http_get", fake_get)
    res = _warm(factory, 7, item.id)
    assert res["w2b"] == "probed"
    _wait_http_calls(http_calls, 2)
    assert sorted(http_calls) == ["get", "range"]  # 预探两腿并行，Range 判 quota 即落缓存
    # 点播判定点（PlaybackInfo / video_stream 共用入口）：
    verdict = tv.probe_user_copy_quota_decision(7, "fid-e2e")
    assert verdict == "quota"
    assert sorted(http_calls) == ["get", "range"]  # 命中工人预探的裁决，零现探


def test_playback_decision_cold_still_probes(db, monkeypatch):
    """全冷回落：没人暖过时判定点照旧现探（点播行为与今天一致）。"""
    http_calls = []

    def fake_range(url):
        http_calls.append("range")
        return 200, "text/html", b"<html>download quota exceeded</html>"

    monkeypatch.setattr(tv, "_usercopy_probe_http", fake_range)
    monkeypatch.setattr(tv, "_usercopy_probe_http_get",
                        lambda url: (200, "video/mp4", b""))
    verdict = tv.probe_user_copy_quota_decision(7, "fid-cold")
    assert verdict == "quota"
    assert http_calls == ["range"]  # GET 桩不记数，只看 Range 腿现探一次


# ---------------------------------------------------------------------------
# W3：本人令牌保鲜（唯一令牌口；边距＋提前量外零网络）
# ---------------------------------------------------------------------------

def test_w3_refreshes_token_near_expiry(db, factory, seams):
    item = _item(db, "令牌将过期片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    # 离过期 100 秒 < 刷新边距 300 ＋ 提前量 30 → 本轮提前刷新
    seams.token_expires_at = int(time.time()) + 100
    res = _warm(factory, 7, item.id)
    assert res["w3"] == "refreshed"
    assert seams.token_ensure_calls == [7]  # 只刷本人令牌


def test_w3_fresh_when_token_healthy(db, factory, seams):
    item = _item(db, "令牌健康片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.token_expires_at = int(time.time()) + 7200
    res = _warm(factory, 7, item.id)
    assert res["w3"] == "fresh"
    assert seams.token_ensure_calls == []  # 还远：不调令牌口、零网络


def test_w3_failed_when_ensure_fails(db, factory, seams):
    item = _item(db, "刷败片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    seams.token_expires_at = int(time.time()) + 100
    seams.token_ensure_result = False
    res = _warm(factory, 7, item.id)
    assert res["w3"] == "failed"


def test_w3_once_per_user_per_round(factory, db, seams, monkeypatch):
    a = _item(db, "同用户甲")
    b = _item(db, "同用户乙")
    _variant(db, a, user_id=7, fid="fid-a")
    _variant(db, b, user_id=7, fid="fid-b")
    seams.with_binding()
    seams.token_expires_at = int(time.time()) + 100
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": 7, "item_id": a.id, "source": "hot"},
         {"user_id": 7, "item_id": b.id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 2,
         "items": 2, "targets": 2, "w5_only": 0}))
    stats = dw.warm_once(factory)
    assert stats["w3"]["refreshed"] == 1
    assert seams.token_ensure_calls == [7]  # 同用户整轮只刷一次


# ---------------------------------------------------------------------------
# 整轮：计数 / 预算 / 开关
# ---------------------------------------------------------------------------

def test_warm_once_batch2_counts(factory, db, seams, monkeypatch):
    item = _item(db, "整轮批2片", drive_file_id="fid-orig2")
    _variant(db, item, user_id=7, fid="fid-copy2")
    seams.with_binding()
    seams.token_expires_at = int(time.time()) + 100
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": 7, "item_id": item.id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 1,
         "items": 1, "targets": 1, "w5_only": 0}))
    stats = dw.warm_once(factory)
    assert stats["ran"] is True
    assert stats["warmed"] == 1
    assert stats["w2a"]["ensured"] == 1
    assert stats["w2b"]["probed"] == 1
    assert stats["w3"]["refreshed"] == 1
    # 请求账：W2a 确保 1 + W2b 探测 2 + W3 刷新 1 + W5 两 fid 各 1
    assert stats["requests"] == 6


def test_batch2_respects_request_budget(factory, db, seams, monkeypatch):
    item = _item(db, "预算片", drive_file_id="fid-orig3")
    _variant(db, item, user_id=7, fid="fid-copy3")
    seams.with_binding()
    seams.token_expires_at = int(time.time()) + 100
    monkeypatch.setattr(dw, "build_targets", lambda db_, cfg, now=None: (
        [{"user_id": 7, "item_id": item.id, "source": "hot"}],
        {"follow_items": 0, "resume_items": 0, "hot_items": 1,
         "items": 1, "targets": 1, "w5_only": 0}))
    monkeypatch.setenv("DRIVE_WARM_MAX_REQUESTS_PER_ROUND", "1")
    stats = dw.warm_once(factory)
    assert stats["requests"] == 1  # W2a 用掉唯一预算
    assert stats["budget"] == 1
    assert seams.decide_calls == []  # 预算耗尽：不再预探
    assert seams.token_ensure_calls == []  # 也不再刷令牌


def test_disabled_round_touches_nothing(factory, db, seams, monkeypatch):
    item = _item(db, "关时片")
    _variant(db, item, user_id=7, fid="fid-copy")
    seams.with_binding()
    monkeypatch.setenv("DRIVE_WARM_ENABLED", "0")
    stats = dw.warm_once(factory)
    assert stats["ran"] is False
    assert stats["reason"] == "disabled"
    assert seams.ensure_calls == []
    assert seams.decide_calls == []
    assert seams.token_ensure_calls == []
