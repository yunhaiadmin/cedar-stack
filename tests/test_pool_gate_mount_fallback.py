# -*- coding: utf-8 -*-
"""池供给范围外条目（rclone 挂载）回落挂载直读（2026-11-12 richotis 案）。

生产定案：未绑盘用户 richotis 播 paul（mount 4，mount_type=rclone）
三部片，PlaybackInfo 200、取流 stream.mkv 全回 409。机制链：未绑
用户经 pool_gate 判 kind="pool" → 池门禁 ensure_pool_copy_gate →
无现成池副本 → create_pool_job → transfer_variant._item_provider
对 rclone 挂载返回 None → 抛 pool_provider_unsupported → verdict
failed → raise_for_transfer_gate 映射 409。挂载直读判定块排在池
门禁之后，请求永远到不了——paul 全库对未绑用户必 409。

2026-11-12 当时的修法（取流与计划层同口径）：池门禁 verdict=
failed 且 reason=pool_provider_unsupported 时，先试 mount_serving
.resolve_local_file：命中（engages 且文件在位）才关掉池模式落到
既有流程出字节；PlaybackInfo 侧按 proceed 留清单原样。

2026-11-18 F1 定案（黑盒审计＋用户播放门禁硬规矩：没绑盘只能
浏览、例外只有池副本）：上述回落对原生池观众（kind="pool" 且
pool_routed=False）撤销——池门禁 verdict 不是 serve_pool_copy
即为终局，挂载能不能探到文件与这类用户无关：PlaybackInfo 按
failed 形态覆盖清单、不下发任何可播地址，取流按门禁原判 409
不出字节。已绑用户（含大文件分流 pool_routed 回落本人门禁的
现行纪律）与池 serve 链逐字不动。

（2026-11-22 播放门禁改版注：池观众的资格前提已变为「有订阅」，
本文件种子里 FREE/CONTRIB 均带订阅；未绑/已绑只剩分流意义，
下列钉死项的分流与回落口径逐字不变。）

钉死：
1. 未绑 + rclone 挂载 + 直读可接管 → 仍 409、不出字节（F1 回归）；
2. 同上但直读未配（白名单无此挂载）→ 仍 409、不下发原件 fid；
3. 同上但直读 engages 而文件缺失 → 仍 409（不回落共享/谷歌直链）；
4. PlaybackInfo 同口径：直读可接管时也照旧标 failed 并删地址；
   直读未配时同形态；
5. drive 挂载条目池口径逐字不变：有 ready 池副本 → 池分支
   serve 302 池副本（即使该挂载也在直读白名单内）；无副本 →
   照旧建池单 preparing 409；
6. 对照绿：已绑用户播 rclone 挂载条目照常经挂载直读出字节。

种子数据用独立用户名/库名/挂载 id，模块收尾删净。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
# TRANSFER_VARIANT_ENABLED 同 test_pool_serve 口径：只经下方
# autouse fixture 按用例 setenv，导入期不许写死（会泄漏全进程）。

import shutil
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import config_service, models
from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import mount_serving as ms
from backend.emby_server import pool_gate
from backend.emby_server import transfer_variant as tv
from backend.integrations import store
from backend.security import hash_password

init_db()

PW = "poolmount-pw"
FREE = "poolmount_free"
CONTRIB = "poolmount_contrib"
LIB_NAME = "池挂载回落测试库"
RCLONE_MOUNT_ID = 9874
DRIVE_MOUNT_ID = 9875
PAUL_REL = "video/影库/龙门飞甲 (2011)/龙门飞甲 (2011) - 1080p.mkv"
PAUL_PATH = f"mount://{RCLONE_MOUNT_ID}/{PAUL_REL}"
PAUL_FID = "paulprivatefid"
DRIVE_PATH = f"mount://{DRIVE_MOUNT_ID}/电影/池驱动片 (2026)/池驱动片.mp4"
ORIG_FID = "drivesrvorigfid"
POOL_FID = "poolmountcopy"
MEMBER_TOKEN = "<MASKED_SECRET_001>"
DATA = bytes(range(256)) * 64
TB = 1000 ** 4


def _guid():
    return uuid.uuid4().hex


def _cleanup(db):
    for uname in (FREE, CONTRIB):
        old = db.query(models.WebUser).filter(
            models.WebUser.username == uname).first()
        if old:
            db.query(models.UserSubscription).filter(
                models.UserSubscription.user_id == old.id).delete(
                synchronize_session=False)
            cred_ids = [r[0] for r in db.query(
                em.UserStorageBinding.credential_id)
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
    db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("mount_serving_%")).delete(
        synchronize_session=False)
    db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("pool_%")).delete(
        synchronize_session=False)
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
            db.query(em.TransferDecisionRound).filter(
                em.TransferDecisionRound.resource_key.in_(
                    [f"item:{i}" for i in ids])).delete(
                synchronize_session=False)
            db.query(em.MediaItem).filter(em.MediaItem.id.in_(ids)).delete(
                synchronize_session=False)
        db.query(em.Library).filter(em.Library.id == lib.id).delete()
    db.query(em.StorageMount).filter(
        em.StorageMount.id.in_([RCLONE_MOUNT_ID, DRIVE_MOUNT_ID])).delete(
        synchronize_session=False)
    db.query(em.PoolUsageDaily).delete(synchronize_session=False)
    if hasattr(em, "PoolPlayEvent"):
        db.query(em.PoolPlayEvent).delete(synchronize_session=False)
    db.commit()
    store.invalidate()


@pytest.fixture(scope="module")
def seed():
    media_root = tempfile.mkdtemp(prefix="poolmount_root_")
    empty_root = tempfile.mkdtemp(prefix="poolmount_empty_")
    paul_file = os.path.join(media_root, PAUL_REL)
    os.makedirs(os.path.dirname(paul_file), exist_ok=True)
    with open(paul_file, "wb") as f:
        f.write(DATA)
    db = SessionLocal()
    _cleanup(db)
    users = {}
    for uname in (FREE, CONTRIB):
        u = models.WebUser(username=uname, password_hash=hash_password(PW),
                           is_active=True, is_staff=True)
        db.add(u)
        db.flush()
        users[uname] = u.id
    # 2026-11-22 播放门禁改版：池观众/已绑用户的播放资格一律来自
    # 订阅（绑盘不再是资格）——本文件的 FREE（未绑池观众）与
    # CONTRIB（已绑贡献者）都补订阅，池分流/回落口径本身不变。
    from datetime import datetime, timedelta

    _plan = models.SubscriptionPlan(
        name="池挂载回落测试套餐", price=1, duration_days=30)
    db.add(_plan)
    db.flush()
    _now = datetime.now()
    for _uid in users.values():
        db.add(models.UserSubscription(
            user_id=_uid, plan_id=_plan.id, realm_id=1,
            start_date=_now - timedelta(days=1),
            end_date=_now + timedelta(days=30), status="active"))
    db.flush()
    cred = em.MountCredential(mount_id=None, scope="user",
                              owner_user_id=users[CONTRIB],
                              sealed_credential="", key_id="fernet-v1",
                              status="active")
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=users[CONTRIB], provider="drive", account_label="贡献盘",
        credential_id=cred.id, status="active")
    db.add(binding)
    db.flush()
    member = em.PoolMember(
        binding_id=binding.id, user_id=users[CONTRIB], provider="drive",
        status="active")
    db.add(member)
    db.add(em.StorageMount(id=RCLONE_MOUNT_ID, name="paul-like",
                           mount_type="rclone", path="rclone:paul_emby:",
                           is_enabled=True))
    db.add(em.StorageMount(id=DRIVE_MOUNT_ID, name="gdrive-like",
                           mount_type="drive", path="drive:drive1",
                           is_enabled=True))
    lib = em.Library(guid=_guid(), name=LIB_NAME, collection_type="movies",
                     is_enabled=True)
    db.add(lib)
    db.flush()
    paul = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie", name="龙门飞甲",
        drive_file_id=PAUL_FID, file_path=PAUL_PATH, container="mkv",
        size=len(DATA), duration_ticks=118 * 60 * 10_000_000,
        probe_status="done")
    db.add(paul)
    drive_movie = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie", name="池驱动片",
        drive_file_id=ORIG_FID, file_path=DRIVE_PATH, container="mp4",
        size=2_000_000_000, duration_ticks=118 * 60 * 10_000_000,
        probe_status="done")
    db.add(drive_movie)
    db.commit()
    data = {"free_id": users[FREE], "contrib_id": users[CONTRIB],
            "member_id": member.id, "binding_id": binding.id,
            "lib": lib.guid, "paul": paul.guid, "paul_id": paul.id,
            "drive_movie": drive_movie.guid,
            "drive_movie_id": drive_movie.id,
            "media_root": media_root, "empty_root": empty_root}
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
        _cleanup(db)
    finally:
        db.close()
    shutil.rmtree(media_root, ignore_errors=True)
    shutil.rmtree(empty_root, ignore_errors=True)


@pytest.fixture(scope="module")
def client():
    from backend.main import app
    return TestClient(app)


def _auth_header():
    q = chr(34)
    return (f"MediaBrowser Client={q}PoolMountTest{q}, Device={q}iOS{q}, "
            f"DeviceId={q}poolmount-dev-1{q}, Version={q}1.0{q}")


def _token(client, uname):
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": uname, "Pw": PW},
        headers={"X-Emby-Authorization": _auth_header()})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


@pytest.fixture(scope="module")
def free_token(client, seed):
    return _token(client, FREE)


@pytest.fixture(scope="module")
def contrib_token(client, seed):
    return _token(client, CONTRIB)


def _h(token):
    return {"X-Emby-Token": token}


@pytest.fixture(autouse=True)
def _fake_member_quota(monkeypatch):
    monkeypatch.setenv("TRANSFER_VARIANT_ENABLED", "1")

    class _FakeQuotaClient:
        def __init__(self, db_arg, binding):
            pass

        def about_storage(self, drive_id):
            return {"limit": 5 * TB, "usage": 1 * TB, "free": 4 * TB}

    monkeypatch.setattr(tv, "UserDriveApiClient", _FakeQuotaClient)
    yield
    pool_gate.clear_pool_quota_cache()


@pytest.fixture(autouse=True)
def _fake_drive_serve(monkeypatch):
    """池播出的外部腿全假（沿 test_pool_serve 同款）。"""
    from backend.emby_server import api as _api
    from backend.emby_server import drive_oauth, drive_share
    monkeypatch.setattr(
        drive_oauth, "get_binding_drive_access_token",
        lambda db, binding, **kw: MEMBER_TOKEN)
    monkeypatch.setattr(
        drive_share, "ensure_shared", lambda fid, tok: "ok")
    monkeypatch.setattr(
        tv, "_usercopy_probe_http",
        lambda url: (200, "video/mp4", b"\x00" * 32))
    monkeypatch.setattr(
        tv, "_usercopy_probe_http_get",
        lambda url: (200, "video/mp4", b"\x00" * 32))

    async def _ensure_ok(db_, item_, fid, **kwargs):
        return True

    monkeypatch.setattr(_api, "_ensure_drive_file_playable", _ensure_ok)


@pytest.fixture(autouse=True)
def _clean_rows(seed):
    yield
    try:
        from backend.database import CacheManager
        with CacheManager._memory_cache_lock:
            for _k in [k for k in CacheManager._memory_cache
                       if "usercopy_public" in k
                       or "usercopy_quota" in k]:
                CacheManager._memory_cache.pop(_k, None)
    except Exception:
        pass
    db = SessionLocal()
    try:
        db.query(em.TransferJob).filter(
            em.TransferJob.item_id.in_(
                [seed["paul_id"], seed["drive_movie_id"]])).delete(
            synchronize_session=False)
        db.query(em.TransferVariant).filter(
            em.TransferVariant.item_id.in_(
                [seed["paul_id"], seed["drive_movie_id"]])).delete(
            synchronize_session=False)
        db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("mount_serving_%")).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()
    store.invalidate()
    pool_gate.clear_pool_quota_cache()


def _set_mount_serving(ids=None, roots=None):
    db = SessionLocal()
    try:
        db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("mount_serving_%")).delete(
            synchronize_session=False)
        if ids is not None:
            db.add(models.SystemConfig(
                key=ms.KEY_MOUNT_SERVING_MOUNT_IDS, value=ids))
        if roots is not None:
            db.add(models.SystemConfig(
                key=ms.KEY_MOUNT_SERVING_LOCAL_ROOTS, value=roots))
        db.commit()
    finally:
        db.close()
    store.invalidate()


def _stream(client, token, guid, query=""):
    return client.get(f"/emby/Videos/{guid}/stream.mkv{query}",
                      headers=_h(token), follow_redirects=False)


def _playbackinfo(client, token, guid):
    r = client.post(f"/emby/Items/{guid}/PlaybackInfo",
                    headers=_h(token), json={})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- 取流

def test_stream_unbound_rclone_mount_serving_engaged_still_409(
        client, free_token, seed):
    """F1 回归：未绑 + rclone 挂载 + 直读可接管 → 仍 409、不出字节。

    原生池观众的池门禁 failed 即终局，挂载直读在位也不许改写
    裁决（2026-11-12 richotis 回落对这类用户已撤销）。同时钉：
    池门禁留痕仍是 failed/pool_provider_unsupported（门禁本体没
    改）；没有池单被建出来（provider 校验在建单前抛错，池供给
    零污染）。
    """
    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    r = _stream(client, free_token, seed["paul"])
    assert r.status_code == 409, r.text
    assert r.content != DATA
    assert not r.headers.get("location")
    assert PAUL_FID not in r.text
    assert PAUL_FID not in (r.headers.get("location") or "")
    db = SessionLocal()
    try:
        jobs = (db.query(em.TransferJob)
                .filter(em.TransferJob.item_id == seed["paul_id"]).all())
        assert jobs == [], "池供给范围外条目不许落池单"
        rounds = (db.query(em.TransferDecisionRound)
                  .filter(
                      em.TransferDecisionRound.scope == "pool_gate",
                      em.TransferDecisionRound.resource_key
                      == f"item:{seed['paul_id']}",
                      em.TransferDecisionRound.reason
                      == "pool_provider_unsupported").all())
        assert rounds, "池门禁留痕应仍记 failed/pool_provider_unsupported"
    finally:
        db.close()


def test_stream_unbound_rclone_mount_not_whitelisted_stays_409(
        client, free_token, seed):
    """直读未配（白名单无此挂载）→ 维持池门禁原判 409，不回落。"""
    _set_mount_serving(ids=str(DRIVE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    r = _stream(client, free_token, seed["paul"])
    assert r.status_code == 409, r.text
    assert not r.headers.get("location")
    assert PAUL_FID not in r.text


def test_stream_unbound_rclone_mount_missing_file_stays_409(
        client, free_token, seed):
    """直读 engages 但文件不在挂载点 → 仍 409，绝不掉到尾部谷歌
    302 回落（回落会把 paul 私有 fid 的 usercontent 直链发出去）。"""
    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['empty_root']}")
    r = _stream(client, free_token, seed["paul"])
    assert r.status_code == 409, r.text
    assert not r.headers.get("location")
    assert PAUL_FID not in r.text


# ---------------------------------------------------------------- 计划层

def test_playbackinfo_unbound_rclone_mount_serving_engaged_stays_failed(
        client, free_token, seed):
    """F1 回归：PlaybackInfo 同口径——直读可接管时也维持池门禁
    原判：标 failed、删一切可播地址（与直读未配时形态一致）。"""
    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    body = _playbackinfo(client, free_token, seed["paul"])
    assert PAUL_FID not in str(body)
    src = body["MediaSources"][0]
    assert src["TransferStatus"] == "failed"
    assert "DirectPlayUrl" not in src
    assert "DirectStreamUrl" not in src


def test_playbackinfo_unbound_rclone_mount_no_serving_stays_failed(
        client, free_token, seed):
    """直读未配时 PlaybackInfo 维持原判：标 failed、删地址。"""
    _set_mount_serving(ids="", roots="")
    body = _playbackinfo(client, free_token, seed["paul"])
    src = body["MediaSources"][0]
    assert src["TransferStatus"] == "failed"
    assert "DirectPlayUrl" not in src
    assert "DirectStreamUrl" not in src


# ---------------------------------------------------------------- drive 回归

def test_stream_unbound_drive_mount_pool_serve_unchanged(
        client, free_token, seed):
    """drive 挂载条目池口径不变：有 ready 池副本 → 池分支 serve
    302 池副本——即使该挂载也在直读白名单内、本地文件也在位，
    池 serve 优先，回落判定不许截走（verdict 不是 failed）。"""
    db = SessionLocal()
    try:
        db.add(em.TransferVariant(
            item_id=seed["drive_movie_id"], source_file_id=ORIG_FID,
            variant_file_id=POOL_FID,
            variant_drive_id=f"pooldrive:{seed['member_id']}",
            owner_scope="pool", owner_ref=str(seed["member_id"]),
            status="ready", size_bytes=2_000_000_000))
        db.commit()
    finally:
        db.close()
    _set_mount_serving(ids=str(DRIVE_MOUNT_ID),
                       roots=f"{DRIVE_MOUNT_ID}={seed['media_root']}")
    r = _stream(client, free_token, seed["drive_movie"])
    assert r.status_code == 302, r.text
    loc = r.headers.get("location") or ""
    assert "usercontent" in loc and POOL_FID in loc
    assert ORIG_FID not in loc


def _set_probe_budget(raw):
    db = SessionLocal()
    try:
        db.query(models.SystemConfig).filter(
            models.SystemConfig.key
            == "mount_serving_probe_budget_ms").delete(
            synchronize_session=False)
        if raw is not None:
            db.add(models.SystemConfig(
                key="mount_serving_probe_budget_ms", value=raw))
        db.commit()
    finally:
        db.close()
    store.invalidate()


# -------------------------------------------- 有界探测（2026-11-17 定案）

def test_playbackinfo_slow_mount_stat_within_budget_stays_failed(
        client, free_token, seed, monkeypatch):
    """挂载文件 stat 冷查挂住时，PlaybackInfo 不许被堵住、清单
    维持池门禁原判 failed（#281 定案：同步等 stat 生产实测堵了
    19.6 秒且形态被改成 proceed；F1 后池分支不再拿探测结果改写
    裁决，本例继续钉「慢 stat 不堵请求、形态不变」）。"""
    import threading
    import time as _time

    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    _set_probe_budget("200")
    # stat 缓存是进程级的：同模块先跑的用例已把该路径喂热会让
    # 本例的挂死 stat 根本不发生，先清掉保证测的是冷查路径
    ms._clear_stat_cache()
    gate = threading.Event()
    real_isfile = os.path.isfile

    def picky(path):
        if str(path).endswith("龙门飞甲 (2011) - 1080p.mkv"):
            gate.wait(10)
            return True
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", picky)
    try:
        start = _time.monotonic()
        body = _playbackinfo(client, free_token, seed["paul"])
        elapsed = _time.monotonic() - start
    finally:
        gate.set()
        _set_probe_budget(None)
    assert elapsed < 5, f"PlaybackInfo 被挂载 stat 堵住：{elapsed:.1f}s"
    src = body["MediaSources"][0]
    assert src["TransferStatus"] == "failed"
    assert "DirectPlayUrl" not in src


def test_stream_slow_mount_stat_unbound_still_409(
        client, free_token, seed, monkeypatch):
    """F1 后取流端对原生池观众不再做挂载探测改写裁决：stat 慢
    （超过 PI 预算）且文件在位 → 仍按池门禁原判 409，不出字节。
    （#281 的有界探测防堵口径由 PlaybackInfo 侧用例继续钉。）"""
    import time as _time

    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    _set_probe_budget("200")
    ms._clear_stat_cache()
    real_isfile = os.path.isfile

    def slow(path):
        if str(path).endswith("龙门飞甲 (2011) - 1080p.mkv"):
            _time.sleep(1.0)
            return True
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", slow)
    try:
        r = _stream(client, free_token, seed["paul"])
    finally:
        _set_probe_budget(None)
    assert r.status_code == 409, r.text
    assert r.content != DATA


def test_stream_bound_user_rclone_mount_still_served(
        client, contrib_token, seed):
    """对照绿（F1）：已绑用户（CONTRIB 名下有 active drive 绑定）
    播 rclone 挂载条目不经池分支裁决，挂载直读照常出真字节——
    本单只收原生池观众，已绑链路逐字不动。"""
    _set_mount_serving(ids=str(RCLONE_MOUNT_ID),
                       roots=f"{RCLONE_MOUNT_ID}={seed['media_root']}")
    r = _stream(client, contrib_token, seed["paul"])
    assert r.status_code == 200, r.text
    assert r.content == DATA


def test_stream_unbound_drive_mount_preparing_unchanged(
        client, free_token, seed, monkeypatch):
    """drive 挂载条目无副本 → 照旧建池单、preparing 409（provider
    在池范围内，回落判定不许碰；池单必须真的建出来）。"""
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    r = _stream(client, free_token, seed["drive_movie"])
    assert r.status_code == 409, r.text
    assert "转存准备中" in r.text
    db = SessionLocal()
    try:
        job = (db.query(em.TransferJob)
               .filter(em.TransferJob.item_id == seed["drive_movie_id"],
                       em.TransferJob.owner_scope == "pool").one())
        assert job.status == "pending"
        assert job.dest_drive_id == f"pooldrive:{seed['member_id']}"
    finally:
        db.close()
