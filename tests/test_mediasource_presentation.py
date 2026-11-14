# -*- coding: utf-8 -*-
"""播放源呈现口径回归（画质标签已删，Name 回条目名）。

背景：第三方播放器里别家源卡显示「Dolby＋17.4 GB＋19.02Mbps」并按
分辨率进 4K 页签，我方源卡只剩「文件」二字、无大小无码率、落进
「其他」页签。钉死的新口径：

1. MediaSource.Name 一律条目名（Emby 默认形态；画质标签已
   随删除令下线，不再按探测/文件名拼标签），不带源名/服务器名。
2. 本地条目 Size/Bitrate/MediaStreams 从真实探测数据下发；
   无探测时留空（补探测走既有按需入队）。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.database import CacheManager, SessionLocal, init_db
from backend.emby_server import models as em
from backend.security import hash_password

init_db()

TICKS = 10_000_000
PW = "MsPres#2026"
BOUND = "mspres_bound"
LIB_LOCAL = "画质呈现测试库"


def _guid():
    return uuid.uuid4().hex


def _cleanup(db):
    for lib_name in (LIB_LOCAL,):
        for lib in db.query(em.Library).filter(
                em.Library.name == lib_name).all():
            ids = [r[0] for r in db.query(em.MediaItem.id).filter(
                em.MediaItem.library_id == lib.id).all()]
            if ids:
                db.query(em.MediaStream).filter(
                    em.MediaStream.item_id.in_(ids)).delete(
                    synchronize_session=False)
                db.query(em.MediaItem).filter(
                    em.MediaItem.id.in_(ids)).delete(
                    synchronize_session=False)
            db.query(em.Library).filter(
                em.Library.id == lib.id).delete(synchronize_session=False)
    u = db.query(models.WebUser).filter(
        models.WebUser.username == BOUND).first()
    if u is not None:
        cred_ids = [r[0] for r in db.query(em.UserStorageBinding.credential_id)
                    .filter(em.UserStorageBinding.user_id == u.id).all()]
        db.query(em.UserStorageBinding).filter(
            em.UserStorageBinding.user_id == u.id).delete(
            synchronize_session=False)
        if cred_ids:
            db.query(em.MountCredential).filter(
                em.MountCredential.id.in_(cred_ids)).delete(
                synchronize_session=False)
        db.query(em.EmbyApiToken).filter(
            em.EmbyApiToken.user_id == u.id).delete(
            synchronize_session=False)
        # 站内信先清：
        # to_user_id 非空而 ORM 删用户会试图置空它；更早模块留下的
        # 孤儿站内信（收件人已被删、其 id 被本文件用户复用）会把
        # teardown 顶爆并回滚整个清理（2026-11-05 #1854 CI 实证，
        # #62 xdist 并行复现）。
        db.query(models.StationMessage).filter(
            (models.StationMessage.to_user_id == u.id)
            | (models.StationMessage.from_user_id == u.id)
        ).delete(synchronize_session=False)
        db.delete(u)
    db.commit()


@pytest.fixture(scope="module")
def seed():
    db = SessionLocal()
    _cleanup(db)
    user = models.WebUser(username=BOUND, password_hash=hash_password(PW),
                          is_active=True, is_staff=True, emby_username=BOUND,
                          emby_password=PW)
    db.add(user)
    db.flush()
    cred = em.MountCredential(mount_id=None, scope="user",
                              owner_user_id=user.id, sealed_credential="",
                              key_id="fernet-v1", status="active")
    db.add(cred)
    db.flush()
    # account_label 必须全仓唯一：共享文件库里别家种子（如
    # test_emby_views_compat）teardown 删用户不删绑定，孤儿绑定残留后
    # 新用户复用其 id 时会撞 (user_id, provider, account_label) 唯一键
    # ——多家共用「种子盘」正是本文件 CI setup 5 连 ERROR 的成因。
    db.add(em.UserStorageBinding(user_id=user.id, provider="drive",
                                 account_label="画质呈现种子盘",
                                 credential_id=cred.id, status="active"))
    lib = em.Library(guid=_guid(), name=LIB_LOCAL,
                     collection_type="movies", is_enabled=True)
    db.add(lib)
    db.flush()
    probed = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie",
        name="画质探测片",
        file_path="mount://3/电影/画质探测片 (2024)/画质探测片.mkv",
        container="mkv", size=17_400_000_000, bitrate=19_020_000,
        width=3840, height=2160, duration_ticks=2 * 3600 * TICKS,
        probe_status="done")
    db.add(probed)
    db.flush()
    db.add(em.MediaStream(item_id=probed.id, stream_index=0,
                          stream_type="Video", codec="hevc",
                          video_range="HDR10", bit_rate=19_020_000,
                          display_title="4K HEVC", is_default=True))
    db.add(em.MediaStream(item_id=probed.id, stream_index=1,
                          stream_type="Audio", codec="aac", channels=2,
                          is_default=True))
    dv_named = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie",
        name="画质文件名片",
        file_path="mount://3/电影/某片 (2024)/某片.2024.2160p.Dolby.Vision.WEB-DL.mkv",
        container="mkv", size=8_000_000_000, duration_ticks=0,
        probe_status="failed", probe_attempts=99)
    db.add(dv_named)
    plain = em.MediaItem(
        guid=_guid(), library_id=lib.id, item_type="movie",
        name="画质无标记片",
        file_path="mount://3/电影/无标记/plain.mkv",
        container="mkv", size=1_000_000_000, duration_ticks=0,
        probe_status="failed", probe_attempts=99)
    db.add(plain)
    db.commit()
    data = {"probed": probed.guid, "dv_named": dv_named.guid,
            "plain": plain.guid}
    db.close()
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


@pytest.fixture(scope="module")
def token(client, seed):
    q = chr(34)
    r = client.post(
        "/emby/Users/AuthenticateByName",
        json={"Username": BOUND, "Pw": PW},
        headers={"X-Emby-Authorization":
                 f"MediaBrowser Client={q}MsPres{q}, Device={q}iOS{q}, "
                 f"DeviceId={q}mspres-dev-1{q}, Version={q}1.0{q}"})
    assert r.status_code == 200, r.text
    return r.json()["AccessToken"]


def _playback_sources(client, token, guid):
    r = client.post(f"/emby/Items/{guid}/PlaybackInfo",
                    headers={"X-Emby-Token": token}, json={})
    assert r.status_code == 200, r.text
    return r.json()["MediaSources"]


def test_local_probed_movie_source_presentation(client, token, seed):
    src = _playback_sources(client, token, seed["probed"])[0]
    assert src["Name"] == "画质探测片"
    assert src["Size"] == 17_400_000_000
    assert src["Bitrate"] == 19_020_000
    video = [s for s in src["MediaStreams"] if s["Type"] == "Video"][0]
    assert video["Width"] == 3840 and video["Height"] == 2160
    assert video["VideoRange"] == "HDR10"


def test_local_unprobed_movie_name_is_item_name(client, token, seed):
    src = _playback_sources(client, token, seed["dv_named"])[0]
    # 画质标签已删：未探测条目的 Name 就是条目名，不从文件名拼标
    assert src["Name"] == "画质文件名片"
    # 无探测：轨道留空、码率不推导（无时长），不许编造
    assert src["MediaStreams"] == []
    assert "Bitrate" not in src


def test_local_unknown_quality_falls_back_to_item_name(client, token, seed):
    src = _playback_sources(client, token, seed["plain"])[0]
    assert src["Name"] == "画质无标记片"
