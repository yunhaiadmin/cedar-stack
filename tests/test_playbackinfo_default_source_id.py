# -*- coding: utf-8 -*-
"""PlaybackInfo 默认源 Id 必须与详情 DTO 同值（条目 guid）。

生产实证（2026-11-20）：Reflix 点 Aetrix 源不拉流、直接跳别的服。
同一用户同一条目昨天（单源下发，Id=guid）可播，今天首播转存出本人
ready 副本后变体变两条（drive_direct + usercopy_relay），
build_media_sources 把每条源的 Id 都改成 ``{guid}:{variant}``——
PlaybackInfo 里再没有一条 Id 等于详情 DTO 里那条源的 Id（恒 guid）。
按 Id 对齐「详情源 ↔ PlaybackInfo 源」的聚合客户端（Reflix）认不到
可播源，整服跳过；不按 Id 对齐、直接吃 DirectStreamUrl 的客户端
（SenPlayer/Yamby 等）不受影响，与生产现象逐项吻合。

钉住的口径：
- 多变体下发时第一条（默认）源 Id == 条目 guid，与详情 DTO 的
  MediaSource Id 逐字一致；其余变体源保持 ``{guid}:{variant}``
  互不相同，清单内 Id 不重复。
- 第一条源的取流 URL 不变：仍带 variant 双保险（显式选路与
  MediaSourceId 后缀解析口径不动），改的只有 Id 字段本身。
- 单变体/单源下发形态逐字不变（本来就是 guid）。
harness 仿 tests/test_usercopy_relay_variant.py，全部离线。
"""
import asyncio
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.database import Base
from backend.emby_server import api, play_variants
from backend.emby_server import playback_strategy as _strategy
from backend.emby_server import probe_worker, stream_accel
from backend.emby_server import models as em
from backend.emby_server import transfer_variant as _tv

ORIG_FID = "1eXUpWksxfCCrIZUye8lo24JGXyr_2ZQO"
COPY_FID = "1TN2Wu4I5bNV3gy0mLC8ibNPGQI11-qE9"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _stub_store(monkeypatch, relay="false"):
    def fake_get(db_, key, default="", **kwargs):
        values = {
            _strategy.KEY_DRIVE_DIRECT_PLAY: "true",
            _strategy.KEY_DRIVE_RELAY: relay,
            _strategy.KEY_BYOS_DIRECT_GRAY: "true",
        }
        return values.get(key, default)

    monkeypatch.setattr(_strategy.store, "get_value", fake_get)


def _request(path="/Items/x/PlaybackInfo"):
    scope = {"type": "http", "method": "GET", "path": path,
             "raw_path": path.encode(), "query_string": b"",
             "headers": [], "scheme": "http",
             "server": ("testserver", 80),
             "client": ("testclient", 50000), "root_path": ""}
    return Request(scope)


def _pi_item(guid):
    item = em.MediaItem()
    item.guid = guid
    item.name = "无声的回响"
    item.file_path = "mount://3/MoviePilot/剧集/无声的回响/第 1 集.mkv"
    item.drive_file_id = ORIG_FID
    item.item_type = "episode"
    item.size = 3_003_081_673
    item.duration_ticks = 24_040_000_000
    item.bitrate = 9_500_000
    return item


def _copy_row():
    return SimpleNamespace(
        id=5899, owner_scope="user", owner_ref=11,
        variant_drive_id="userdrive:11",
        variant_file_id=COPY_FID, status="ready")


def _call_pi(monkeypatch, db, item, *, copy_row):
    user = SimpleNamespace(id=11, is_staff=False)
    monkeypatch.setattr(api, "_require_visible_item",
                        lambda db_, u_, iid: item)
    monkeypatch.setattr(api, "ensure_playback_allowed",
                        lambda db_, u_: None)
    monkeypatch.setattr(
        "backend.emby_server.playback_gate.ensure_binding_playback_allowed",
        lambda db_, u_, r=None: None)
    monkeypatch.setattr(probe_worker, "maybe_enqueue",
                        lambda db_, it: None)
    monkeypatch.setattr(
        stream_accel, "rewrite_playback_urls_unified",
        lambda db_, s, base: (s, None))
    monkeypatch.setattr(api, "_drive_mount_id_for_item",
                        lambda db_, it: "drive-x")
    monkeypatch.setattr(play_variants, "_ENABLED", True)
    _stub_store(monkeypatch)
    monkeypatch.setattr(
        _tv, "resolve_play_file_id",
        lambda db_, it, u: (COPY_FID, copy_row) if copy_row
        else (ORIG_FID, None))
    return asyncio.run(api.playback_info(
        item.guid, _request(), user_id="", user=user, db=db))


# ---------------------------------------------------------------------------
# PlaybackInfo 层：生产同形态（本人 ready 副本 → 双源）
# ---------------------------------------------------------------------------

def test_pi_two_sources_default_id_is_guid(db, monkeypatch):
    item = _pi_item("guid-reflix-1")
    resp = _call_pi(monkeypatch, db, item, copy_row=_copy_row())
    sources = resp["MediaSources"]
    assert len(sources) == 2
    # 默认源 Id 与详情 DTO 同值（guid），按 Id 认源的客户端能对上。
    assert sources[0]["Id"] == item.guid
    # 其余变体源保持后缀身份、互不相同。
    assert sources[1]["Id"] == f"{item.guid}:usercopy_relay"
    assert len({s["Id"] for s in sources}) == len(sources)
    # 取流 URL 逐字不变：variant 双保险仍在，显式选路口径不动。
    assert "variant=drive_direct" in sources[0]["DirectStreamUrl"]
    assert "variant=usercopy_relay" in sources[1]["DirectStreamUrl"]


def test_pi_default_id_matches_detail_dto(db, monkeypatch):
    item = _pi_item("guid-reflix-2")
    resp = _call_pi(monkeypatch, db, item, copy_row=_copy_row())
    detail_src = api._media_source(item, "http://testserver", "", db,
                                   user=SimpleNamespace(id=11))
    assert resp["MediaSources"][0]["Id"] == detail_src["Id"] == item.guid


def test_pi_no_copy_single_source_id_still_guid(db, monkeypatch):
    item = _pi_item("guid-reflix-3")
    resp = _call_pi(monkeypatch, db, item, copy_row=None)
    assert [s["Id"] for s in resp["MediaSources"]] == [item.guid]


# ---------------------------------------------------------------------------
# 构造层：build_media_sources 纯函数口径
# ---------------------------------------------------------------------------

def _variant(name, label):
    return play_variants.Variant(name=name, label=label, protocol="Http",
                                 is_remote=False, needs_relay=True)


def test_build_media_sources_first_id_is_guid():
    base = {"Id": "g9", "Name": "片名", "MediaStreams": []}
    out = play_variants.build_media_sources(
        base, [_variant("drive_direct", "Drive 直链"),
               _variant("drive_relay", "Drive 中继"),
               _variant("usercopy_relay", "我的副本中继")],
        guid="g9", stream_url="http://x/stream?MediaSourceId=g9",
        direct_stream_ok=True)
    assert [s["Id"] for s in out] == [
        "g9", "g9:drive_relay", "g9:usercopy_relay"]
    # 身份改的是 Id，不是 URL：每条仍带各自 variant 标记。
    assert "variant=drive_direct" in out[0]["DirectStreamUrl"]
    assert "variant=usercopy_relay" in out[2]["DirectStreamUrl"]


def test_build_media_sources_single_variant_id_is_guid():
    base = {"Id": "g9", "Name": "片名", "MediaStreams": []}
    out = play_variants.build_media_sources(
        base, [_variant("drive_relay", "中继")],
        guid="g9", stream_url="http://x/stream?MediaSourceId=g9",
        direct_stream_ok=True)
    assert [s["Id"] for s in out] == ["g9"]
