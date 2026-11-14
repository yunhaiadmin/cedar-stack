# -*- coding: utf-8 -*-
"""一起看（Watch Together）会话与同步测试。

口径：
- 建房 / 进房按现行播放门禁逐人校验（playback_gate.sign_allowed_for_item
  单一入口）：本地库条目未绑定拒、已绑定过；CMS 库条目免绑定过。
- 房间只流转控制事件与进度：任何下发 payload 里都不许出现流地址 /
  play_sign / api_key / 令牌类字段（红线，结构断言锁死）。
- 同步：成员的 play/pause/seek 广播给全房并更新房间播放态；晚入者
  拿到的快照进度按播放态时间戳外推对齐。
- 房主解散全房收到 ended；房主离开（非解散）房主转给最早成员；
  人走空房间销毁。
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend import watch_together as wt
from backend.emby_server import models as em


def run(coro):
    return asyncio.run(coro)


class FakeWS:
    """记录 send_json 的假连接（hub 只用到 send_json）。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_):
        return [m for m in self.sent if m.get("type") == type_]

    def last_of(self, type_):
        rows = self.of_type(type_)
        return rows[-1] if rows else None


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def hub(db):
    def checker(user_id, guid):
        return wt.db_access_checker(db, user_id, guid)

    return wt.WatchTogetherHub(store=wt.RoomStore(), access_checker=checker)


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    return user


def _binding(db, user_id, status="active", provider="drive"):
    cred = em.MountCredential(
        mount_id=None, scope="user", owner_user_id=user_id,
        sealed_credential="", key_id="fernet-v1", status=status)
    db.add(cred)
    db.flush()
    binding = em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label="盘",
        credential_id=cred.id, status=status)
    db.add(binding)
    db.commit()
    return binding


def _library(db, name, upstream_source_ids=""):
    lib = em.Library(guid=uuid.uuid4().hex, name=name,
                     upstream_source_ids=upstream_source_ids)
    db.add(lib)
    db.commit()
    return lib


def _item(db, lib_id, name="片"):
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib_id, item_type="movie",
        name=name, file_path=None)
    db.add(item)
    db.commit()
    return item


def _local_item(db):
    return _item(db, _library(db, "本地库").id)


def _cms_item(db):
    return _item(db, _library(db, "cms库", upstream_source_ids="7").id)


# ---------------------------------------------------------------- 建房


def test_create_room_bound_user_ok(db, hub):
    host = _user(db, "host1")
    _binding(db, host.id)
    item = _local_item(db)
    ws = FakeWS()

    snap = run(hub.create_room(ws, host.id, item.guid))

    assert len(snap["code"]) == 6
    assert snap["item_id"] == item.guid
    assert snap["host_id"] == host.id
    assert snap["members"] == [
        {"user_id": host.id, "username": "host1", "is_host": True}]
    assert snap["state"]["playing"] is False
    assert run(hub.store.get(snap["code"])) is not None


def test_create_room_unbound_user_gate_denied(db, hub):
    guest = _user(db, "guest1")
    item = _local_item(db)
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.create_room(FakeWS(), guest.id, item.guid))
    assert excinfo.value.error == "gate_denied"


def test_create_room_former_cms_item_unbound_gate_denied(db, hub):
    # CMS 下线（2026-11-14）后旧 CMS 条目不再免门禁：未绑盘建房照样拦。
    guest = _user(db, "guest2")
    item = _cms_item(db)
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.create_room(FakeWS(), guest.id, item.guid))
    assert excinfo.value.error == "gate_denied"


def test_create_room_missing_item(db, hub):
    host = _user(db, "host2")
    _binding(db, host.id)
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.create_room(FakeWS(), host.id, "no-such-guid"))
    assert excinfo.value.error == "item_not_found"


# ---------------------------------------------------------------- 进房


def _room_with_host(db, hub, item=None):
    host = _user(db, "hostx")
    _binding(db, host.id)
    item = item or _local_item(db)
    host_ws = FakeWS()
    snap = run(hub.create_room(host_ws, host.id, item.guid))
    return host, host_ws, item, snap["code"]


def test_join_room_broadcasts_member_joined(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member1")
    _binding(db, member.id)
    member_ws = FakeWS()

    snap = run(hub.join_room(member_ws, member.id, code))

    assert {m["user_id"] for m in snap["members"]} == {host.id, member.id}
    joined = host_ws.last_of("wt.member_joined")
    assert joined is not None
    assert joined["member"]["user_id"] == member.id
    assert len(joined["members"]) == 2


def test_join_room_unbound_gate_denied_and_not_attached(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    guest = _user(db, "guest3")
    guest_ws = FakeWS()
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.join_room(guest_ws, guest.id, code))
    assert excinfo.value.error == "gate_denied"
    # 没进房：房里仍只有房主，也没广播 member_joined
    assert hub._unique_user_count(code) == 1
    assert host_ws.of_type("wt.member_joined") == []


def test_join_room_bad_code(db, hub):
    member = _user(db, "member2")
    _binding(db, member.id)
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.join_room(FakeWS(), member.id, "ZZZZZZ"))
    assert excinfo.value.error == "room_not_found"


def test_join_room_full(db, hub, monkeypatch):
    monkeypatch.setattr(wt, "MAX_MEMBERS", 2)
    host, host_ws, item, code = _room_with_host(db, hub)
    m1 = _user(db, "member3")
    _binding(db, m1.id)
    run(hub.join_room(FakeWS(), m1.id, code))
    m2 = _user(db, "member4")
    _binding(db, m2.id)
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.join_room(FakeWS(), m2.id, code))
    assert excinfo.value.error == "room_full"


# ---------------------------------------------------------------- 同步


def test_control_event_broadcast_and_state(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member5")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.join_room(member_ws, member.id, code))

    run(hub.handle_event(member_ws, member.id,
                         {"event": "seek", "position": 321.5,
                          "playing": True}))
    evt = host_ws.last_of("wt.event")
    assert evt is not None
    assert evt["event"] == "seek"
    assert evt["position"] == 321.5
    assert evt["playing"] is True
    assert evt["from_user_id"] == member.id
    # 发送方不回声
    assert member_ws.of_type("wt.event") == []

    run(hub.handle_event(host_ws, host.id,
                         {"event": "pause", "position": 330.0}))
    evt2 = member_ws.last_of("wt.event")
    assert evt2["event"] == "pause"
    assert evt2["playing"] is False
    room = run(hub.store.get(code))
    assert room["state"]["playing"] is False
    assert room["state"]["position"] == 330.0


def test_late_joiner_aligns_to_extrapolated_position(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    # 房主 10 秒前在 100s 处按下播放（倍速 1）
    import time as _time
    room = run(hub.store.get(code))
    room["state"] = {"playing": True, "position": 100.0, "rate": 1.0,
                     "ts": _time.time() - 10}
    run(hub.store.set(code, room))

    member = _user(db, "member6")
    _binding(db, member.id)
    snap = run(hub.join_room(FakeWS(), member.id, code))
    assert snap["state"]["playing"] is True
    assert 109.0 <= snap["state"]["position"] <= 111.0


def test_host_state_anchor_broadcasts_sync(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member7")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.join_room(member_ws, member.id, code))

    run(hub.handle_state(host_ws, host.id,
                         {"position": 500.0, "playing": True, "rate": 1.5}))
    sync = member_ws.last_of("wt.sync")
    assert sync is not None
    assert sync["playing"] is True
    assert sync["rate"] == 1.5
    assert sync["position"] >= 500.0

    # 非房主的锚点不采纳、不广播
    member_ws.sent.clear()
    run(hub.handle_state(member_ws, member.id,
                         {"position": 1.0, "playing": False, "rate": 1}))
    assert member_ws.of_type("wt.sync") == []
    assert host_ws.of_type("wt.sync") == []


# ---------------------------------------------------------------- 解散/离开


def test_dissolve_by_host_ends_room(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member8")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.join_room(member_ws, member.id, code))

    run(hub.dissolve_room(host_ws, host.id))
    assert member_ws.last_of("wt.ended")["reason"] == "dissolved"
    assert run(hub.store.get(code)) is None
    assert hub._conns.get(code) is None


def test_dissolve_by_member_rejected(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member9")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.join_room(member_ws, member.id, code))
    with pytest.raises(wt.WatchError) as excinfo:
        run(hub.dissolve_room(member_ws, member.id))
    assert excinfo.value.error == "not_host"
    assert run(hub.store.get(code)) is not None


def test_host_leave_transfers_host(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member10")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.join_room(member_ws, member.id, code))

    run(hub.handle_disconnect(host_ws, host.id))
    changed = member_ws.last_of("wt.host_changed")
    assert changed is not None
    assert changed["host_id"] == member.id
    room = run(hub.store.get(code))
    assert room["host_id"] == member.id


def test_last_leave_destroys_room(db, hub):
    host, host_ws, item, code = _room_with_host(db, hub)
    run(hub.handle_disconnect(host_ws, host.id))
    assert run(hub.store.get(code)) is None


# ---------------------------------------------------------------- 协议层


def test_handle_message_create_join_event_flow(db, hub):
    host = _user(db, "hosty")
    _binding(db, host.id)
    item = _local_item(db)
    host_ws = FakeWS()
    run(hub.handle_message(host_ws, host.id,
                           {"action": "wt.create", "item_id": item.guid}))
    created = host_ws.last_of("wt.created")
    assert created is not None
    code = created["code"]

    member = _user(db, "member11")
    _binding(db, member.id)
    member_ws = FakeWS()
    run(hub.handle_message(member_ws, member.id,
                           {"action": "wt.join", "code": code.lower()}))
    assert member_ws.last_of("wt.joined") is not None

    run(hub.handle_message(member_ws, member.id,
                           {"action": "wt.event", "event": "play",
                            "position": 42.0}))
    evt = host_ws.last_of("wt.event")
    assert evt["event"] == "play" and evt["position"] == 42.0


def test_handle_message_gate_denied_returns_error_frame(db, hub):
    guest = _user(db, "guesty")
    item = _local_item(db)
    ws = FakeWS()
    run(hub.handle_message(ws, guest.id,
                           {"action": "wt.create", "item_id": item.guid}))
    err = ws.last_of("wt.error")
    assert err is not None
    assert err["error"] == "gate_denied"


# ---------------------------------------------------------------- 红线


_FORBIDDEN_KEYS = {
    "url", "stream_url", "direct_stream_url", "play_sign", "api_key",
    "token", "access_token", "password", "src",
}


def _assert_no_forbidden(payload, path="root"):
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key.lower() not in _FORBIDDEN_KEYS, f"{path}.{key}"
            _assert_no_forbidden(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for i, value in enumerate(payload):
            _assert_no_forbidden(value, f"{path}[{i}]")


def test_no_stream_secrets_in_any_payload(db, hub):
    """红线：房间全程 payload 只许控制事件/进度/名单，不许带流地址与令牌。"""
    host, host_ws, item, code = _room_with_host(db, hub)
    member = _user(db, "member12")
    _binding(db, member.id)
    member_ws = FakeWS()
    snap = run(hub.join_room(member_ws, member.id, code))
    run(hub.handle_event(member_ws, member.id,
                         {"event": "play", "position": 10.0}))
    run(hub.handle_state(host_ws, host.id,
                         {"position": 11.0, "playing": True, "rate": 1}))
    _assert_no_forbidden(snap)
    for ws in (host_ws, member_ws):
        for payload in ws.sent:
            _assert_no_forbidden(payload)
