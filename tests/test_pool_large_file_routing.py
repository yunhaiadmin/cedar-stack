# -*- coding: utf-8 -*-
"""公共池大文件分流（用户 2026-11-10 亲点新口径）。

口径：已绑盘用户播大文件（size ≥ pool_large_file_bytes，默认
15GB）时，若公共池里有就绪副本，直接播池副本（省掉几十 GB
转存到自己盘的等待与接口消耗）；小文件维持现行（转存到自己
盘播本人副本）。判定顺序钉死：

1. 已有本人就绪副本 → 播本人副本（池再大也不走池）；
2. 大文件 + 池开 + 人群过 + 池有就绪副本 → 播池副本；
3. 其余一切 → 现行流程（转存本人盘再播）。分流不许变成新
   门禁：池没副本/池关/人群不过/池播出中途失败，一律回落 3。

决策点唯一：pool_gate.large_file_pool_route（计划层经
ensure_playback_allowed_for_item 消费、播出层同函数复核），
两层不许各判一套。

种子数据用独立用户名/库名/挂载 id，模块收尾删净。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
# TRANSFER_VARIANT_ENABLED 只许 fixture 按用例设置（见
# test_pool_serve 文件头注释），不许导入期写死。

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import config_service, models
from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import pool_gate
from backend.emby_server import transfer_variant as tv
from backend.integrations import store
from backend.security import hash_password

init_db()

PW = "poolroute-pw"
BOUND = "poolroute_bound"
FREE = "poolroute_free"
CONTRIB = "poolroute_contrib"
LIB_NAME = "池大文件分流测试库"
MOUNT_ID = 9872
BIG_PATH = f"mount://{MOUNT_ID}/电影/大文件片 (2026)/大文件片.mp4"
SMALL_PATH = f"mount://{MOUNT_ID}/电影/小文件片 (2026)/小文件片.mp4"
NOSIZE_PATH = f"mount://{MOUNT_ID}/电影/无尺寸片 (2026)/无尺寸片.mp4"
ORIG_FID = "routeorigfid"
POOL_FID = "routepoolcopy"
USER_FID = "routeusercopy"
MEMBER_TOKEN = "tok-member-route"
TICKS = 10_000_000
TB = 1000 ** 4
BIG_SIZE = 20_000_000_000
SMALL_SIZE = 2_000_000_000


def _guid():
    return uuid.uuid4().hex


def _cleanup(db):
    for uname in (BOUND, FREE, CONTRIB):
        old = db.query(models.WebUser).filter(
            models.WebUser.username == uname).first()
        if old:
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
    if hasattr(em, "PoolPlayEvent"):
        db.query(em.PoolPlayEvent).delete(synchronize_session=False)
    db.query(em.TransferJob).filter(
        em.TransferJob.owner_scope.in_(("pool", "user"))).delete(
        synchronize_session=False)
    db.query(em.TransferVariant).filter(
        em.TransferVariant.owner_scope.in_(("pool", "user"))).delete(
        synchronize_session=False)
    db.query(em.PoolUsageDaily).delete(synchronize_session=False)
    db.query(em.TransferDecisionRound).filter(
        em.TransferDecisionRound.scope.in_(("pool_pick", "pool_gate"))
        ).delete(synchronize_session=False)
    db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("pool_%")).delete(
        synchronize_session=False)
    for lib in db.query(em.Library).filter(
            em.Library.name == LIB_NAME).all():
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
            db.query(em.ItemFacet).filter(
                em.ItemFacet.item_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(em.MediaItem).filter(
                em.MediaItem.id.in_(ids)).delete(
                synchronize_session=False)
        db.query(em.Library).filter(em.Library.id == lib.id).delete()
    db.query(em.StorageMount).filter(
        em.StorageMount.id == MOUNT_ID).delete(synchronize_session=False)
    db.commit()
    store.invalidate()


@pytest.fixture(scope="module")
def seed():
    db = SessionLocal()
    _cleanup(db)
    users = {}
    for uname in (BOUND, FREE, CONTRIB):
        u = models.WebUser(
            username=uname, password_hash=hash_password(PW),
            is_active=True, is_staff=True, emby_username=uname,
            emby_password=hash_password(PW))
        db.add(u)
        db.flush()
        users[uname] = u.id
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=users[CONTRIB],
        sealed_credential="", key_id="fernet-v1", status="active")
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
    cred2 = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=users[BOUND],
        sealed_credential="", key_id="fernet-v1", status="active")
    db.add(cred2)
    db.flush()
    db.add(em.UserStorageBinding(
        user_id=users[BOUND], provider="drive", account_label="自家盘",
        credential_id=cred2.id, status="active"))
    db.add(em.StorageMount(id=MOUNT_ID, name="mroute", mount_type="drive",
                           path="drive:drive1"))
    lib = em.Library(guid=_guid(), name=LIB_NAME,
                     collection_type="movies", is_enabled=True)
    db.add(lib)
    db.flush()
    movies = {}
    for key, name, path, size in (
            ("big", "大文件片", BIG_PATH, BIG_SIZE),
            ("small", "小文件片", SMALL_PATH, SMALL_SIZE),
            ("nosize", "无尺寸片", NOSIZE_PATH, None)):
        movie = em.MediaItem(
            guid=_guid(), library_id=lib.id, item_type="movie",
            name=name, drive_file_id=ORIG_FID, file_path=path,
            container="mp4", size=size,
            duration_ticks=118 * 60 * TICKS,
            probe_status="failed", probe_attempts=99)
        db.add(movie)
        db.flush()
        movies[key] = (movie.guid, movie.id)
    db.commit()
    data = {"bound_id": users[BOUND], "free_id": users[FREE],
            "contrib_id": users[CONTRIB], "member_id": member.id,
            "lib": lib.guid,
            "big": movies["big"][0], "big_id": movies["big"][1],
            "small": movies["small"][0], "small_id": movies["small"][1],
            "nosize": movies["nosize"][0],
            "nosize_id": movies["nosize"][1]}
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


@pytest.fixture(scope="module")
def client():
    from backend.main import app
    return TestClient(app)


def _auth_header():
    q = chr(34)
    return (f"MediaBrowser Client={q}PoolRouteTest{q}, Device={q}iOS{q}, "
            f"DeviceId={q}poolroute-dev-1{q}, Version={q}1.0{q}")


def _token(client, uname):
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": uname, "Pw": PW},
        headers={"X-Emby-Authorization": _auth_header()})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


@pytest.fixture(scope="module")
def bound_token(client, seed):
    return _token(client, BOUND)


@pytest.fixture(scope="module")
def free_token(client, seed):
    return _token(client, FREE)


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
    """池/本人副本播出的外部腿全假（同 test_pool_serve 口径）。"""
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
def _clean_pool_rows(seed):
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
            em.TransferJob.owner_scope.in_(("pool", "user"))).delete(
            synchronize_session=False)
        db.query(em.TransferVariant).filter(
            em.TransferVariant.owner_scope.in_(("pool", "user"))).delete(
            synchronize_session=False)
        db.query(em.PoolUsageDaily).delete(synchronize_session=False)
        if hasattr(em, "PoolPlayEvent"):
            db.query(em.PoolPlayEvent).delete(synchronize_session=False)
        db.query(em.TransferDecisionRound).filter(
            em.TransferDecisionRound.scope.in_(("pool_pick", "pool_gate"))
            ).delete(synchronize_session=False)
        db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("pool_%")).delete(
            synchronize_session=False)
        config_service.save_config(
            db, {"pool_enabled": True, "pool_enqueue_jitter_sec": 0},
            schema="pool")
        db.commit()
    finally:
        db.close()
    store.invalidate()
    pool_gate.clear_pool_quota_cache()


def _set_config(db, key, value):
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if row is None:
        db.add(models.SystemConfig(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()
    store.invalidate()


def _add_pool_variant(db, seed, item_id, fid=POOL_FID):
    row = em.TransferVariant(
        item_id=item_id, source_file_id=ORIG_FID, variant_file_id=fid,
        variant_drive_id=f"pooldrive:{seed['member_id']}",
        owner_scope="pool", owner_ref=str(seed["member_id"]),
        status="ready", size_bytes=BIG_SIZE)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _add_user_variant(db, seed, item_id, fid=USER_FID):
    row = em.TransferVariant(
        item_id=item_id, source_file_id=ORIG_FID, variant_file_id=fid,
        variant_drive_id=f"userdrive:{seed['bound_id']}",
        owner_scope="user", owner_ref=str(seed["bound_id"]),
        status="ready", size_bytes=BIG_SIZE)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _playbackinfo(client, token, guid):
    r = client.post(f"/emby/Items/{guid}/PlaybackInfo",
                    headers=_h(token), json={})
    assert r.status_code == 200, r.text
    return r.json()


def _stream(client, token, guid, query=""):
    return client.get(f"/emby/Videos/{guid}/stream.mp4{query}",
                      headers=_h(token), follow_redirects=False)


def _play_events(db):
    return db.query(em.PoolPlayEvent).all()


def _usage_play_starts(db, member_id):
    row = (db.query(em.PoolUsageDaily)
           .filter(em.PoolUsageDaily.member_id == member_id).first())
    return int(row.play_starts or 0) if row else 0


def _route(db, seed, user_key="bound_id", item_key="big_id"):
    user = db.query(models.WebUser).filter(
        models.WebUser.id == seed[user_key]).one()
    item = db.query(em.MediaItem).filter(
        em.MediaItem.id == seed[item_key]).one()
    return pool_gate.large_file_pool_route(db, user, item)


def _decision(db, seed, user_key="bound_id", item_key="big_id"):
    user = db.query(models.WebUser).filter(
        models.WebUser.id == seed[user_key]).one()
    item = db.query(em.MediaItem).filter(
        em.MediaItem.id == seed[item_key]).one()
    return pool_gate.ensure_playback_allowed_for_item(db, user, item)


# ---------------------------------------------------------------- 决策点（单元）


def test_route_large_with_pool_copy_is_pool(seed):
    """大文件 + 池有就绪副本 → 分流命中；决策入口判 pool 且带
    分流标记（消费方据此在池没判 serve 时回落本人门禁）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        assert _route(db, seed) is True
        decision = _decision(db, seed)
        assert decision.kind == "pool"
        assert decision.pool_routed is True
    finally:
        db.close()


def test_route_small_file_not_routed(seed):
    """小文件即使池里有副本也不分流（维持转存本人盘口径）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["small_id"])
        assert _route(db, seed, item_key="small_id") is False
        decision = _decision(db, seed, item_key="small_id")
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_own_ready_copy_wins_over_pool(seed):
    """已有本人就绪副本 → 池再大也不走池（判定顺序第 1 条）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _add_user_variant(db, seed, seed["big_id"])
        assert _route(db, seed) is False
        decision = _decision(db, seed)
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_no_pool_copy_not_routed(seed):
    """大文件但池里没副本 → 不分流，回现行转存本人盘流程。"""
    db = SessionLocal()
    try:
        assert _route(db, seed) is False
        decision = _decision(db, seed)
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_switch_off_not_routed(seed):
    """分流开关关 → 全回现行（与没这功能一样）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "pool_large_file_routing_enabled", "false")
        assert _route(db, seed) is False
        decision = _decision(db, seed)
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_unknown_size_treated_as_small(seed):
    """size 未知（NULL）按小文件处理：池有副本也不分流。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["nosize_id"])
        assert _route(db, seed, item_key="nosize_id") is False
        decision = _decision(db, seed, item_key="nosize_id")
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_audience_blocked_not_routed(seed):
    """人群不过（audience=subscribers 且本人无订阅）→ 不分流，
    回本人路径——分流与未绑用户共用同一套人群判定。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "pool_audience", "subscribers")
        assert _route(db, seed) is False
        decision = _decision(db, seed)
        assert decision.kind == "bound"
    finally:
        db.close()


def test_route_pool_disabled_not_routed(seed):
    """池总开关关 → 不分流（池不存在口径）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "pool_enabled", "false")
        assert _route(db, seed) is False
    finally:
        db.close()


def test_route_threshold_config_respected(seed):
    """阈值可配：把阈值抬到 30GB 后，20GB 的片不再算大文件。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "pool_large_file_bytes", "30000000000")
        assert _route(db, seed) is False
        _set_config(db, "pool_large_file_bytes", "10000000000")
        assert _route(db, seed) is True
    finally:
        db.close()


def test_route_unbound_user_decision_unchanged(seed):
    """未绑用户行为一字不改：决策仍为原生 pool（无分流标记）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        decision = _decision(db, seed, user_key="free_id")
        assert decision.kind == "pool"
        assert decision.pool_routed is False
    finally:
        db.close()


# ---------------------------------------------------------------- PlaybackInfo


def test_playbackinfo_bound_large_serves_pool_copy(
        client, bound_token, seed):
    """计划层定 route=池：已绑用户播大文件、池有副本 → 第一条
    源按池副本下发（免凭证直链），池播记账照常落行（观看者是
    已绑用户、贡献者照常计次）。"""
    db = SessionLocal()
    try:
        variant_id = _add_pool_variant(db, seed, seed["big_id"])
    finally:
        db.close()
    body = _playbackinfo(client, bound_token, seed["big"])
    text = str(body)
    assert ORIG_FID not in text
    assert MEMBER_TOKEN not in text
    src = body["MediaSources"][0]
    assert "usercontent" in (src.get("DirectPlayUrl") or "")
    assert POOL_FID in src["DirectPlayUrl"]
    assert src.get("TransferStatus") in (None, "ready")
    db = SessionLocal()
    try:
        events = _play_events(db)
        assert len(events) == 1
        assert events[0].member_id == seed["member_id"]
        assert events[0].variant_id == variant_id
        assert events[0].viewer_user_id == seed["bound_id"]
        assert _usage_play_starts(db, seed["member_id"]) == 1
        # 走了池就不许再给本人建转存单
        assert (db.query(em.TransferJob)
                .filter(em.TransferJob.item_id == seed["big_id"],
                        em.TransferJob.owner_scope == "user")
                .count() == 0)
    finally:
        db.close()


def test_playbackinfo_bound_large_no_pool_copy_falls_back(
        client, bound_token, seed, monkeypatch):
    """大文件 + 池无副本 → 回落现行：给本人建转存单、清单回
    准备中形态；不许因为想走池就播不了、更不许下发共享原件。"""
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    db = SessionLocal()
    try:
        _set_config(db, "play_inline_transfer_enabled", "false")
    finally:
        db.close()
    try:
        body = _playbackinfo(client, bound_token, seed["big"])
        assert POOL_FID not in str(body)
        assert ORIG_FID not in str(body)
        src = body["MediaSources"][0]
        assert src["TransferStatus"] == "preparing"
        db = SessionLocal()
        try:
            job = (db.query(em.TransferJob)
                   .filter(em.TransferJob.item_id == seed["big_id"],
                           em.TransferJob.owner_scope == "user")
                   .one())
            assert job.owner_ref == str(seed["bound_id"])
            assert _play_events(db) == []
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(models.SystemConfig).filter(
                models.SystemConfig.key
                == "play_inline_transfer_enabled").delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
        store.invalidate()


def test_playbackinfo_bound_small_pool_copy_not_used(
        client, bound_token, seed, monkeypatch):
    """小文件 + 池有副本 → 仍走本人路径（转存准备中），池副本
    不下发、池播不记账。"""
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["small_id"])
        _set_config(db, "play_inline_transfer_enabled", "false")
    finally:
        db.close()
    try:
        body = _playbackinfo(client, bound_token, seed["small"])
        assert POOL_FID not in str(body)
        src = body["MediaSources"][0]
        assert src["TransferStatus"] == "preparing"
        db = SessionLocal()
        try:
            assert _play_events(db) == []
            assert (db.query(em.TransferJob)
                    .filter(em.TransferJob.item_id == seed["small_id"],
                            em.TransferJob.owner_scope == "user")
                    .count() == 1)
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(models.SystemConfig).filter(
                models.SystemConfig.key
                == "play_inline_transfer_enabled").delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
        store.invalidate()


def test_playbackinfo_bound_large_own_copy_wins(
        client, bound_token, seed):
    """大文件 + 池有副本 + 本人已有副本 → 播本人副本：响应不带
    池副本、池播不记账。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _add_user_variant(db, seed, seed["big_id"])
    finally:
        db.close()
    body = _playbackinfo(client, bound_token, seed["big"])
    text = str(body)
    assert POOL_FID not in text
    assert ORIG_FID not in text
    src = body["MediaSources"][0]
    assert USER_FID in (src.get("DirectPlayUrl") or "")
    db = SessionLocal()
    try:
        assert _play_events(db) == []
    finally:
        db.close()


# ---------------------------------------------------------------- video_stream


def test_stream_bound_large_serves_pool_302(
        client, bound_token, seed):
    """播出层服从计划层 route：已绑用户无变体直打大文件流 →
    按池副本 302，记账落行（观看者=已绑用户）。"""
    db = SessionLocal()
    try:
        variant_id = _add_pool_variant(db, seed, seed["big_id"])
    finally:
        db.close()
    r = _stream(client, bound_token, seed["big"])
    assert r.status_code == 302, r.text
    loc = r.headers.get("location") or ""
    assert "usercontent" in loc and POOL_FID in loc
    assert ORIG_FID not in loc
    db = SessionLocal()
    try:
        events = _play_events(db)
        assert len(events) == 1
        assert events[0].variant_id == variant_id
        assert events[0].viewer_user_id == seed["bound_id"]
        assert _usage_play_starts(db, seed["member_id"]) == 1
    finally:
        db.close()


def test_stream_bound_large_pool_dead_copy_falls_back(
        client, bound_token, seed, monkeypatch):
    """池播出中途失败（副本被确认已死）→ 回落本人路径：给本人
    建转存单并按本人门禁回准备中明示，不许把池的 409 直接甩给
    已绑用户、更不许下发共享原件。"""
    from backend.emby_server import api as _api
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "play_inline_transfer_enabled", "false")
    finally:
        db.close()

    async def _ensure_gone(db_, item_, fid, **kwargs):
        raise HTTPException(status_code=404, detail="gone")

    monkeypatch.setattr(_api, "_ensure_drive_file_playable",
                        _ensure_gone)
    monkeypatch.setattr(
        pool_gate, "reconcile_dead_pool_copy",
        lambda db_, variant: {"action": "stale"})
    try:
        r = _stream(client, bound_token, seed["big"])
        assert r.status_code in (409, 503), r.text
        assert "公共池副本已失效" not in r.text
        assert ORIG_FID not in r.text
        db = SessionLocal()
        try:
            # 本人路径已接管：转存单建给本人
            assert (db.query(em.TransferJob)
                    .filter(em.TransferJob.item_id == seed["big_id"],
                            em.TransferJob.owner_scope == "user")
                    .count() == 1)
            # 池播未成功下发 → 不记账
            assert _play_events(db) == []
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(models.SystemConfig).filter(
                models.SystemConfig.key
                == "play_inline_transfer_enabled").delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
        store.invalidate()


def test_stream_routed_via_decision_pool_gate_fail_falls_back(
        client, bound_token, seed, monkeypatch):
    """第二条进池路径（回归钉死）：本地二值门禁抛错、裁决改由
    新判定入口给出（kind=pool 且 pool_routed=True）时，
    _pool_stream_mode 一开始就是 True、预判块被守卫跳过——
    池门禁判非 serve（failed）也必须回落本人流程：建本人
    转存单、按本人门禁回准备中明示，不许把池的报错原样甩给
    分流用户（分流不许变成新门禁）。"""
    from backend.emby_server import playback_gate as _gate_mod
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
        _set_config(db, "play_inline_transfer_enabled", "false")
    finally:
        db.close()

    def _binary_gate_raises(db_, user_, request_=None, *a, **kw):
        raise HTTPException(status_code=403, detail="二值门禁桩")

    # 二值门禁抛错 → _vs_decision 来自 ensure_playback_allowed
    # _for_item（本单 _decision 单元例已钉死其为 pool+routed）。
    monkeypatch.setattr(
        _gate_mod, "ensure_binding_playback_allowed",
        _binary_gate_raises)
    # 池门禁判非 serve：failed → raise_for_transfer_gate 抛 409。
    monkeypatch.setattr(
        pool_gate, "ensure_pool_copy_gate",
        lambda db_, item_, user_, *a, **kw: {
            "verdict": "failed", "detail": "池门禁失败标记"})
    try:
        r = _stream(client, bound_token, seed["big"])
        assert r.status_code in (409, 503), r.text
        # 池门禁的报错不许原样甩给分流用户
        assert "池门禁失败标记" not in r.text
        assert ORIG_FID not in r.text
        db = SessionLocal()
        try:
            # 本人路径已接管：转存单建给本人、池播不记账
            assert (db.query(em.TransferJob)
                    .filter(em.TransferJob.item_id == seed["big_id"],
                            em.TransferJob.owner_scope == "user")
                    .count() == 1)
            assert _play_events(db) == []
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(models.SystemConfig).filter(
                models.SystemConfig.key
                == "play_inline_transfer_enabled").delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
        store.invalidate()


def test_stream_native_pool_gate_fail_reraises(
        client, free_token, seed, monkeypatch):
    """原生池观众（未绑、pool_routed 恒 False）行为逐字不变：
    池门禁判非 serve 的报错原样上抛，不落本人分支（未绑用户
    本来就没有本人分支可落）。"""
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["big_id"])
    finally:
        db.close()
    monkeypatch.setattr(
        pool_gate, "ensure_pool_copy_gate",
        lambda db_, item_, user_, *a, **kw: {
            "verdict": "failed", "detail": "池门禁失败标记"})
    r = _stream(client, free_token, seed["big"])
    assert r.status_code == 409, r.text
    assert "池门禁失败标记" in r.text
    db = SessionLocal()
    try:
        assert (db.query(em.TransferJob)
                .filter(em.TransferJob.item_id == seed["big_id"],
                        em.TransferJob.owner_scope == "user")
                .count() == 0)
        assert _play_events(db) == []
    finally:
        db.close()


def test_stream_bound_small_never_pool(client, bound_token, seed,
                                       monkeypatch):
    """播出层同口径：小文件即使池有副本，已绑用户直打流也不走
    池（回本人门禁：无本人副本 → 准备中明示）。"""
    monkeypatch.setenv("TRANSFER_WAIT_STREAM_ENABLED", "0")
    db = SessionLocal()
    try:
        _add_pool_variant(db, seed, seed["small_id"])
        _set_config(db, "play_inline_transfer_enabled", "false")
    finally:
        db.close()
    try:
        r = _stream(client, bound_token, seed["small"])
        assert r.status_code in (409, 503), r.text
        assert POOL_FID not in r.text
        assert ORIG_FID not in r.text
        db = SessionLocal()
        try:
            assert _play_events(db) == []
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(models.SystemConfig).filter(
                models.SystemConfig.key
                == "play_inline_transfer_enabled").delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
        store.invalidate()
