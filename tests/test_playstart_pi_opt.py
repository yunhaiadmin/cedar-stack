# -*- coding: utf-8 -*-
"""起播拆账优化（2026-11-12 拆账清单第 1、4、5 项）回归钉。

第 1 项：PlaybackInfo 内等转存就绪改短等分级——PI 最多短等
``playbackinfo_copy_wait_sec``（默认 3 秒），等不到回准备中形态，
不再把整个 PlaybackInfo 堵到 15–20 秒上限；执行不取消、工人兜底。
第 4 项：用户副本配额探测两腿并行（Range 定案口径不变，GET 腿
结果只在 Range 放行时采用）＋新鲜裁决秒级短缓存（默认 5 秒，
只缓存 ok / interstitial，error 与 quota 不走短缓存，池路径不用）。
第 5 项：PlaybackInfo 基础体缓存键去掉设备 profile / 码率碎片
（缓存体只由条目 + 用户决定），同用户同条目跨客户端共享命中；
门禁裁决仍每请求新鲜算，缓存不把准备中冻成可播。

全部离线：探测打桩、缓存走内存、PI 用 TestClient + 独立种子。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import asyncio
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models as base_models
from backend.emby_server import transfer_variant as tv
from backend.integrations import store


# ---------------------------------------------------------------------------
# 第 1 项：短等上限配置口径
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/copywait.db",
        connect_args={"check_same_thread": False, "timeout": 30})
    base_models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_store_cache():
    store.invalidate()
    yield
    store.invalidate()


def _set_config(db, key, value):
    row = (db.query(base_models.SystemConfig)
           .filter(base_models.SystemConfig.key == key).first())
    if row is None:
        db.add(base_models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()
    store.invalidate()


def test_copy_wait_sec_default_and_clamp(cfg_db):
    assert tv.playbackinfo_copy_wait_sec(cfg_db) == 3
    _set_config(cfg_db, tv.KEY_PLAYBACKINFO_COPY_WAIT_SEC, "7")
    assert tv.playbackinfo_copy_wait_sec(cfg_db) == 7
    _set_config(cfg_db, tv.KEY_PLAYBACKINFO_COPY_WAIT_SEC, "99")
    assert tv.playbackinfo_copy_wait_sec(cfg_db) == 10
    # 非正数在 _system_config_int 口径里＝未配置 → 回默认
    # （与 play_inline 三键逐字同口径，不另立一套）
    _set_config(cfg_db, tv.KEY_PLAYBACKINFO_COPY_WAIT_SEC, "0")
    assert tv.playbackinfo_copy_wait_sec(cfg_db) == 3
    _set_config(cfg_db, tv.KEY_PLAYBACKINFO_COPY_WAIT_SEC, "junk")
    assert tv.playbackinfo_copy_wait_sec(cfg_db) == 3


# ---------------------------------------------------------------------------
# 第 1 项：maybe_run_play_inline 的请求侧短等（执行不取消）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _inline_counter_clean():
    with tv._inline_inflight_lock:
        tv._inline_inflight = 0
    yield
    with tv._inline_inflight_lock:
        tv._inline_inflight = 0


def test_inline_short_wait_returns_preparing(monkeypatch):
    monkeypatch.setattr(
        tv, "_play_inline_config",
        lambda: {"enabled": True, "budget_sec": 20, "concurrency": 4})
    release = threading.Event()

    def slow_inline(job_id, session_factory=None, drive_api_factory=None):
        release.wait(3)
        return "done"

    monkeypatch.setattr(tv, "claim_and_execute_inline", slow_inline)
    gate = {"verdict": "preparing", "job_id": 123, "reason": "job_created"}

    async def regate():
        return {"verdict": "proceed"}

    async def _main():
        # 耗时在协程内量：asyncio.run 收尾会等被 shield 的执行
        # 线程收尾，那是测试框架的账、不是函数本体的等待口径
        # （生产事件循环常驻，无此收尾）。
        t0 = time.monotonic()
        r = await tv.maybe_run_play_inline(
            gate, regate, wait_budget_sec=0.2)
        return r, time.monotonic() - t0

    try:
        result, elapsed = asyncio.run(_main())
    finally:
        release.set()
    # 预算口径是 20 秒，短等 0.2 秒就该回准备中，不许堵满预算
    assert result["verdict"] == "preparing"
    assert elapsed < 2.0


def test_inline_short_wait_fast_job_proceeds(monkeypatch):
    monkeypatch.setattr(
        tv, "_play_inline_config",
        lambda: {"enabled": True, "budget_sec": 20, "concurrency": 4})
    monkeypatch.setattr(
        tv, "claim_and_execute_inline",
        lambda job_id, session_factory=None, drive_api_factory=None: "done")
    gate = {"verdict": "preparing", "job_id": 124, "reason": "job_created"}

    async def regate():
        return {"verdict": "proceed", "reason": "has_ready_variant"}

    result = asyncio.run(tv.maybe_run_play_inline(
        gate, regate, wait_budget_sec=5))
    assert result["verdict"] == "proceed"


def test_wait_stream_short_budget_returns_preparing():
    calls = {"n": 0}

    async def regate():
        calls["n"] += 1
        return {"verdict": "preparing", "reason": "job_in_flight"}

    gate = {"verdict": "preparing", "reason": "job_in_flight"}
    start = time.monotonic()
    result = asyncio.run(tv.wait_for_transfer_ready(
        gate, regate, max_sec=0.3, poll_sec=0.05))
    elapsed = time.monotonic() - start
    assert result["verdict"] == "preparing"
    assert calls["n"] >= 2
    assert elapsed < 1.5


# ---------------------------------------------------------------------------
# 第 4 项：配额探测两腿并行 + 秒级短缓存
# ---------------------------------------------------------------------------

FID = "BURSTcopyFID2222222222222222222"
QUOTA_HTML = (b"<!DOCTYPE html><html><head><title>Google Drive - Quota "
              b"exceeded</title>")


@pytest.fixture()
def fake_cm(monkeypatch):
    store_dict = {}

    class _CM:
        @staticmethod
        def get(key, *, domain=None):
            return store_dict.get((domain, key))

        @staticmethod
        def set(key, value, ttl=300, *, domain=None):
            store_dict[(domain, key)] = value
            return True

        @staticmethod
        def delete(key, *, domain=None):
            store_dict.pop((domain, key), None)
            return True

    monkeypatch.setattr("backend.database.CacheManager", _CM)
    monkeypatch.setattr(tv, "publish_drive_exists_ok",
                        lambda fid, **k: True)
    monkeypatch.setenv("TRANSFER_USERCOPY_QUOTA_PROBE_ENABLED", "1")
    return store_dict


def test_probe_legs_run_in_parallel(fake_cm, monkeypatch):
    def slow_range(url):
        time.sleep(0.5)
        return 206, "video/mp4", b"\x00\x00\x00 ftypisom"

    def slow_get(url):
        time.sleep(0.5)
        return 200, "video/mp4", b"\x00\x00\x00 ftypisom"

    monkeypatch.setattr(tv, "_usercopy_probe_http", slow_range)
    monkeypatch.setattr(tv, "_usercopy_probe_http_get", slow_get)
    start = time.monotonic()
    verdict = tv.probe_user_copy_quota(7, FID)
    elapsed = time.monotonic() - start
    assert verdict == "ok"
    # 串行两腿要 ≥1.0s；并行只需一腿的时间量级
    assert elapsed < 0.9


def test_probe_parallel_range_quota_still_quota(fake_cm, monkeypatch):
    monkeypatch.setattr(
        tv, "_usercopy_probe_http",
        lambda url: (200, "text/html; charset=utf-8", QUOTA_HTML))
    monkeypatch.setattr(
        tv, "_usercopy_probe_http_get",
        lambda url: (200, "video/mp4", b"\x00\x00\x00 ftypisom"))
    assert tv.probe_user_copy_quota(7, FID) == "quota"


def test_probe_parallel_get_leg_quota_signal(fake_cm, monkeypatch):
    monkeypatch.setattr(
        tv, "_usercopy_probe_http",
        lambda url: (206, "video/mp4", b"\x00\x00\x00 ftypisom"))
    monkeypatch.setattr(
        tv, "_usercopy_probe_http_get",
        lambda url: (200, "text/html", QUOTA_HTML))
    assert tv.probe_user_copy_quota(7, FID) == "ok"
    detail = tv.usercopy_quota_probe_detail(7, FID)
    assert detail.get("page") == "quota"


def _decision_setup(monkeypatch, range_impl):
    """decision 测试脚手架：隔离 ok 复用层，只留短缓存变量。"""
    calls = {"range": 0, "get": 0}

    def range_counting(url):
        calls["range"] += 1
        return range_impl(url)

    def get_counting(url):
        calls["get"] += 1
        return 200, "video/mp4", b"\x00\x00\x00 ftypisom"

    monkeypatch.setattr(tv, "_usercopy_probe_http", range_counting)
    monkeypatch.setattr(tv, "_usercopy_probe_http_get", get_counting)
    monkeypatch.setattr(tv, "_probe_ok_reuse_hit", lambda u, f: False)
    monkeypatch.setattr(tv, "_probe_ok_reuse_store", lambda u, f: None)
    return calls


def test_decision_burst_cache_suppresses_reprobe(fake_cm, monkeypatch):
    calls = _decision_setup(
        monkeypatch,
        lambda url: (206, "video/mp4", b"\x00\x00\x00 ftypisom"))
    assert tv.probe_user_copy_quota_decision(7, FID) == "ok"
    assert calls["range"] == 1
    # 秒级短缓存内第二次判定：不许再失效重探（旧口径每判定两腿）
    assert tv.probe_user_copy_quota_decision(7, FID) == "ok"
    assert calls["range"] == 1


def test_decision_burst_cache_disabled_by_zero_ttl(fake_cm, monkeypatch):
    monkeypatch.setenv("TRANSFER_USERCOPY_PROBE_BURST_TTL_SEC", "0")
    calls = _decision_setup(
        monkeypatch,
        lambda url: (206, "video/mp4", b"\x00\x00\x00 ftypisom"))
    assert tv.probe_user_copy_quota_decision(7, FID) == "ok"
    assert tv.probe_user_copy_quota_decision(7, FID) == "ok"
    assert calls["range"] == 2


def test_decision_burst_never_caches_error(fake_cm, monkeypatch):
    def boom(url):
        raise RuntimeError("timeout")

    calls = _decision_setup(monkeypatch, boom)
    assert tv.probe_user_copy_quota_decision(7, FID) == "error"
    assert tv.probe_user_copy_quota_decision(7, FID) == "error"
    assert calls["range"] == 2


def test_decision_burst_skips_pool_path(fake_cm, monkeypatch):
    calls = _decision_setup(
        monkeypatch,
        lambda url: (206, "video/mp4", b"\x00\x00\x00 ftypisom"))
    assert tv.probe_user_copy_quota_decision("poolvar:9", FID) == "ok"
    assert tv.probe_user_copy_quota_decision("poolvar:9", FID) == "ok"
    # 池路径（熔断恢复须当场探）不走任何复用/短缓存：逐字旧口径
    assert calls["range"] == 2


# ---------------------------------------------------------------------------
# 第 1 / 5 项：PlaybackInfo 接口级（TestClient + 独立种子）
# ---------------------------------------------------------------------------

from backend import config_service, models  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.security import hash_password  # noqa: E402

init_db()

PW = "pspiopt-pw"
BOUND = "pspiopt_bound"
FREE = "pspiopt_free"
CONTRIB = "pspiopt_contrib"
LIB_NAME = "起播优化测试库"
TB = 1000 ** 4
TICKS = 10_000_000


def _guid():
    return uuid.uuid4().hex


def _cleanup_pi(db):
    for uname in (BOUND, FREE, CONTRIB):
        old = db.query(models.WebUser).filter(
            models.WebUser.username == uname).first()
        if old:
            cred_ids = [r[0] for r in db.query(em.UserStorageBinding.credential_id)
                        .filter(em.UserStorageBinding.user_id == old.id).all()]
            db.query(em.PoolMember).filter(
                em.PoolMember.user_id == old.id).delete(
                synchronize_session=False)
            db.query(em.UserStorageBinding).filter(
                em.UserStorageBinding.user_id == old.id).delete(
                synchronize_session=False)
            if cred_ids:
                db.query(em.MountCredential).filter(
                    em.MountCredential.id.in_(cred_ids)).delete(
                    synchronize_session=False)
            db.query(em.EmbyApiToken).filter(
                em.EmbyApiToken.user_id == old.id).delete(
                synchronize_session=False)
            db.query(models.WebUser).filter(
                models.WebUser.id == old.id).delete()
    db.query(em.PoolUsageDaily).delete(synchronize_session=False)
    for lib in db.query(em.Library).filter(em.Library.name == LIB_NAME).all():
        ids = [r[0] for r in db.query(em.MediaItem.id)
               .filter(em.MediaItem.library_id == lib.id).all()]
        if ids:
            db.query(em.TransferVariant).filter(
                em.TransferVariant.item_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(em.TransferJob).filter(
                em.TransferJob.item_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(em.MediaStream).filter(
                em.MediaStream.item_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(em.MediaItem).filter(em.MediaItem.id.in_(ids)).delete(
                synchronize_session=False)
        db.query(em.Library).filter(em.Library.id == lib.id).delete()
    db.query(em.StorageMount).filter(em.StorageMount.id == 3).delete(
        synchronize_session=False)
    db.commit()
    store.invalidate()


@pytest.fixture(scope="module")
def pi_seed():
    db = SessionLocal()
    _cleanup_pi(db)
    users = {}
    for uname in (BOUND, FREE, CONTRIB):
        u = models.WebUser(username=uname, password_hash=hash_password(PW),
                           is_active=True, is_staff=True, emby_username=uname,
                           emby_password=hash_password(PW))
        db.add(u)
        db.flush()
        users[uname] = u.id

    def _binding(uid):
        cred = em.MountCredential(mount_id=None, scope="user",
                                  owner_user_id=uid, sealed_credential="",
                                  key_id="fernet-v1", status="active")
        db.add(cred)
        db.flush()
        b = em.UserStorageBinding(
            user_id=uid, provider="drive", account_label="测试盘",
            credential_id=cred.id, status="active")
        db.add(b)
        db.flush()
        return b

    contrib_binding = _binding(users[CONTRIB])
    member = em.PoolMember(
        binding_id=contrib_binding.id, user_id=users[CONTRIB],
        provider="drive", status="active")
    db.add(member)
    _binding(users[BOUND])
    db.add(em.StorageMount(id=3, name="m3", mount_type="drive",
                           path="drive:drive1"))
    lib = em.Library(guid=_guid(), name=LIB_NAME, collection_type="movies",
                     is_enabled=True)
    db.add(lib)
    db.flush()
    movie1 = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie", name="缓存测试片",
        drive_file_id="psorig1", file_path="mount://3/电影/缓存测试片.mp4",
        container="mp4", size=2_000_000_000,
        duration_ticks=118 * 60 * TICKS, probe_status="failed",
        probe_attempts=99)
    movie2 = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie", name="池等待片",
        drive_file_id="psorig2", file_path="mount://3/电影/池等待片.mp4",
        container="mp4", size=2_000_000_000,
        duration_ticks=118 * 60 * TICKS, probe_status="failed",
        probe_attempts=99)
    db.add_all([movie1, movie2])
    db.flush()
    # 已绑用户的本人 ready 副本（缓存测试片走 proceed，不建单）
    db.add(em.TransferVariant(
        item_id=movie1.id, source_file_id="psorig1",
        variant_file_id="pscopy1", variant_drive_id=f"userdrive:{users[BOUND]}",
        owner_scope="user", owner_ref=str(users[BOUND]), status="ready",
        size_bytes=2_000_000_000))
    db.commit()
    data = {"bound_id": users[BOUND], "free_id": users[FREE],
            "movie1": movie1.guid, "movie2": movie2.guid,
            "movie2_id": movie2.id}
    db.close()
    db = SessionLocal()
    config_service.save_config(
        db, {"pool_enabled": True, "pool_enqueue_jitter_sec": 0},
        schema="pool")
    db.commit()
    db.close()
    store.invalidate()
    yield data
    db = SessionLocal()
    try:
        _cleanup_pi(db)
    finally:
        db.close()


@pytest.fixture(scope="module")
def pi_client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


def _auth_header():
    q = chr(34)
    return (f"MediaBrowser Client={q}PsPiOpt{q}, Device={q}iOS{q}, "
            f"DeviceId={q}pspiopt-dev-1{q}, Version={q}1.0{q}")


def _token(client, uname):
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": uname, "Pw": PW},
        headers={"X-Emby-Authorization": _auth_header()})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


@pytest.fixture()
def _pi_env(monkeypatch):
    # 只给接口级用例用：这里打桩的 decision/detail 正是单元用例的
    # 被测对象，autouse 会把桩打到它们头上（本文件初版实犯）。
    monkeypatch.setenv("TRANSFER_VARIANT_ENABLED", "1")

    class _FakeQuotaClient:
        def __init__(self, db_arg, binding):
            pass

        def about_storage(self, drive_id):
            return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}

    monkeypatch.setattr(tv, "UserDriveApiClient", _FakeQuotaClient)
    # alt 直链尾段全部打桩到安全侧：本文件只钉缓存与等待，不碰谷歌
    from backend.emby_server import api as _api
    from backend.emby_server import first_play as _fp
    monkeypatch.setattr(tv, "probe_user_copy_quota_decision",
                        lambda u, f: "ok")
    monkeypatch.setattr(tv, "usercopy_quota_probe_cached",
                        lambda u, f: True)
    monkeypatch.setattr(tv, "usercopy_quota_probe_detail",
                        lambda u, f: {})
    monkeypatch.setattr(_fp, "load_warm", lambda *a, **k: None)
    monkeypatch.setattr(_fp, "consume_inflight_warm", lambda *a, **k: None)
    monkeypatch.setattr(_fp, "_resolve_for_playback",
                        lambda *a, **k: {"ok": False, "last_status": None})
    # 计划构建后台线程与本文件的被测对象（等待/缓存）无关：它在
    # 沙箱里会拿假凭据长时间占住 sqlite 写锁，把门禁重查的决策
    # 留痕 commit 拖成秒级锁等待（实测单请求 34 秒全耗在锁上），
    # 故关掉 spawn 只留门禁与等流本真。
    monkeypatch.setattr(
        "backend.emby_server.delivery_plan.spawn_ensure_plan",
        lambda *a, **k: None)
    yield


def _playbackinfo(client, token, guid, payload=None):
    r = client.post(f"/emby/Items/{guid}/PlaybackInfo",
                    headers={"X-Emby-Token": token}, json=payload or {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.usefixtures("_pi_env")
def test_playbackinfo_cache_key_ignores_profile_and_bitrate(
        pi_client, pi_seed, monkeypatch):
    from backend.emby_server import api as _api
    orig_ms = _api._media_source
    calls = {"n": 0}

    def counting(item, base, api_key, db, user=None):
        calls["n"] += 1
        return orig_ms(item, base, api_key, db, user=user)

    monkeypatch.setattr(_api, "_media_source", counting)
    token = _token(pi_client, BOUND)
    body1 = _playbackinfo(pi_client, token, pi_seed["movie1"], {})
    body2 = _playbackinfo(
        pi_client, token, pi_seed["movie1"],
        {"MaxStreamingBitrate": 8_000_000,
         "DeviceProfile": {"Name": "OtherClient",
                           "MaxStreamingBitrate": 8_000_000}})
    assert body1["MediaSources"] and body2["MediaSources"]
    # 缓存体只由条目 + 用户决定：换 profile / 码率不许碎片化重算
    assert calls["n"] == 1


@pytest.mark.usefixtures("_pi_env")
def test_playbackinfo_pool_wait_is_short(pi_client, pi_seed):
    token = _token(pi_client, FREE)
    start = time.monotonic()
    body = _playbackinfo(pi_client, token, pi_seed["movie2"])
    elapsed = time.monotonic() - start
    src = body["MediaSources"][0]
    assert src["TransferStatus"] == "preparing"
    # 旧口径等满 15 秒窗；短等分级后应在短等上限（默认 3 秒）量级返回
    assert elapsed < 8.0
    db = SessionLocal()
    try:
        job = (db.query(em.TransferJob)
               .filter(em.TransferJob.item_id == pi_seed["movie2_id"],
                       em.TransferJob.owner_scope == "pool").first())
        assert job is not None and job.status == "pending"
    finally:
        db.close()
