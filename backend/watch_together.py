# -*- coding: utf-8 -*-
"""一起看（Watch Together）：网页播放器的多人同步会话。

红线（本模块的设计约束，不许绕）：
- 房间里只流转「控制事件＋进度＋条目 guid＋成员名单」：播放 / 暂停 /
  seek、当前进度、倍速、谁在房里。**绝不承载任何流地址、play_sign、
  api_key 或令牌**——每个成员在自己的播放页走各自账号的现行签名
  链路（PlaybackInfo）取流，互不可见。
- 建房 / 进房时服务端按现行播放门禁逐人校验，走单一入口
  ``playback_gate.sign_allowed_for_item``（绑定盘 / 公共池 / CMS 免
  绑定同一口径），不过门禁不许建房、不许进房；房间本身不下发任何
  播放凭证，校验只是「这个人自己能不能播这部片」。

通道：复用 ``backend/websocket.py`` 的 ``/ws/{user_id}`` 连接与 JWT
鉴权（ConnectionManager），不新开第二套 WS / 鉴权。房间连接成员表
是进程内的（WebSocket 对象本来就只在本进程）；房间记录（条目、
房主、播放态）优先存 Redis（与 ``backend.database`` 同一
REDIS_URL 的异步客户端），Redis 未启用 / 不可达 / 中途故障时降级
进程内带 TTL 的字典并打日志——降级期间房间只在本进程可见，与
现有通知 WS 的连接管理口径一致（serve.py workers=1）。

协议（在既有 WS 连接上以 action 区分）：
- 客户端 → 服务端：``wt.create`` / ``wt.join`` / ``wt.leave`` /
  ``wt.dissolve`` / ``wt.event``（play|pause|seek）/ ``wt.state``
  （房主周期锚点，只有房主的会被采纳）。
- 服务端 → 客户端：``wt.created`` / ``wt.joined`` /
  ``wt.member_joined`` / ``wt.member_left`` / ``wt.host_changed`` /
  ``wt.event`` / ``wt.sync`` / ``wt.ended`` / ``wt.error``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 房间记录存活上限（秒）：有活动（事件/锚点）时续期；房主与成员全
# 离开时立即销毁，不等 TTL。
ROOM_TTL_SECONDS = 6 * 3600
# 单房人数上限（含房主）：一起看是小圈子同步，不是直播间。
MAX_MEMBERS = 8
# 邀请码字母表：去掉易混字符（0/O/1/l/I）。
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6

GATE_DENIED_MESSAGE = (
    "还没过播放门禁：先绑定自己的网盘，或等公共池副本就绪后再来一起看。")
ITEM_NOT_FOUND_MESSAGE = "这部片不存在，或不在可播范围内。"
ROOM_NOT_FOUND_MESSAGE = "房间不存在或已结束，核对邀请链接再试。"
ROOM_FULL_MESSAGE = "这个房间人满了，下次早点来。"
NOT_IN_ROOM_MESSAGE = "你还不在这个房间里。"
ALREADY_IN_ROOM_MESSAGE = "你已经在一个一起看房间里了，先退出再试。"


class WatchError(Exception):
    """一起看业务错误：error 是给前端分流的短码，message 是大白话。"""

    def __init__(self, error: str, message: str):
        super().__init__(message)
        self.error = error
        self.message = message


def current_position(state: dict, now: Optional[float] = None) -> float:
    """按房间播放态外推当前进度（秒）：播放中按倍速随时间走，暂停不动。"""
    now = time.time() if now is None else now
    pos = float(state.get("position") or 0)
    if state.get("playing"):
        elapsed = max(0.0, now - float(state.get("ts") or now))
        pos += elapsed * float(state.get("rate") or 1)
    return max(0.0, pos)


# ==================== 房间记录存储 ====================


class RoomStore:
    """房间记录存储：Redis 优先，进程内 TTL 字典降级（见模块头）。"""

    def __init__(self):
        self._redis = None
        self._redis_decided = False
        self._mem: dict[str, tuple[dict, float]] = {}

    async def _ensure_redis(self) -> None:
        if self._redis_decided:
            return
        self._redis_decided = True
        try:
            from backend.database import REDIS_ENABLED, REDIS_URL
        except Exception:  # noqa: BLE001 — 配置读不到就当未启用
            return
        if not REDIS_ENABLED:
            return
        try:
            from redis import asyncio as aioredis

            client = aioredis.from_url(
                REDIS_URL, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2)
            await client.ping()
            self._redis = client
            logger.info("一起看房间存储：使用 Redis")
        except Exception as exc:  # noqa: BLE001 — 连不上就降级，不许拖死功能
            logger.warning("一起看房间存储连不上 Redis，降级进程内存储：%s", exc)

    def _drop_redis(self, exc: Exception) -> None:
        logger.warning("一起看房间存储 Redis 故障，降级进程内存储：%s", exc)
        self._redis = None

    async def get(self, code: str) -> Optional[dict]:
        await self._ensure_redis()
        if self._redis is not None:
            try:
                raw = await self._redis.get(f"wt:room:{code}")
                return json.loads(raw) if raw else None
            except Exception as exc:  # noqa: BLE001
                self._drop_redis(exc)
        entry = self._mem.get(code)
        if entry is None:
            return None
        room, expire_at = entry
        if expire_at <= time.time():
            self._mem.pop(code, None)
            return None
        return room

    async def set(self, code: str, room: dict,
                  ttl: int = ROOM_TTL_SECONDS) -> None:
        await self._ensure_redis()
        if self._redis is not None:
            try:
                await self._redis.set(
                    f"wt:room:{code}", json.dumps(room), ex=ttl)
                return
            except Exception as exc:  # noqa: BLE001
                self._drop_redis(exc)
        self._mem[code] = (room, time.time() + ttl)

    async def delete(self, code: str) -> None:
        await self._ensure_redis()
        if self._redis is not None:
            try:
                await self._redis.delete(f"wt:room:{code}")
            except Exception as exc:  # noqa: BLE001
                self._drop_redis(exc)
        self._mem.pop(code, None)


# ==================== 门禁校验 ====================


def db_access_checker(db, user_id: int, item_guid: str):
    """解析用户 / 条目并按现行播放门禁判定（单一入口，不下发任何凭证）。

    返回 (username, item_found, allowed)：item_found=False 表示条目
    不存在；allowed 走 ``playback_gate.sign_allowed_for_item``——已
    绑定恒可、免门禁条目（CMS 库）可、公共池有 ready 副本可，其余
    不可。与取流时现场裁决同一口径，不另写一份判定。
    """
    from backend import models
    from backend.emby_server import models as em
    from backend.emby_server import playback_gate

    user = db.query(models.WebUser).filter(
        models.WebUser.id == user_id).first()
    if not user:
        return None, False, False
    item = db.query(em.MediaItem).filter(
        em.MediaItem.guid == item_guid).first()
    if item is None:
        return user.username, False, False
    allowed = bool(playback_gate.sign_allowed_for_item(db, user_id, item))
    return user.username, True, allowed


def _default_access_checker(user_id: int, item_guid: str):
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        return db_access_checker(db, user_id, item_guid)
    finally:
        db.close()


# ==================== 会话中枢 ====================


class WatchTogetherHub:
    """房间成员 / 连接管理与事件广播（连接表进程内，记录走 RoomStore）。"""

    def __init__(self, store: Optional[RoomStore] = None,
                 access_checker=None):
        self.store = store or RoomStore()
        self._check_access = access_checker or _default_access_checker
        # code -> {websocket: {"user_id": int, "username": str}}
        self._conns: dict[str, dict[Any, dict]] = {}
        # websocket -> code
        self._ws_room: dict[Any, str] = {}
        self._lock = asyncio.Lock()

    # ---------- 内部工具 ----------

    def _members_payload(self, code: str, room: dict) -> list[dict]:
        seen: dict[int, dict] = {}
        for info in self._conns.get(code, {}).values():
            uid = info["user_id"]
            if uid not in seen:
                seen[uid] = {
                    "user_id": uid,
                    "username": info["username"],
                    "is_host": uid == room.get("host_id"),
                }
        return list(seen.values())

    def _snapshot(self, code: str, room: dict) -> dict:
        state = dict(room.get("state") or {})
        return {
            "code": code,
            "item_id": room.get("item_id"),
            "host_id": room.get("host_id"),
            "members": self._members_payload(code, room),
            "state": {
                "playing": bool(state.get("playing")),
                "position": current_position(state),
                "rate": float(state.get("rate") or 1),
                "ts": time.time(),
            },
        }

    async def _send(self, ws, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001 — 单个连接发送失败不许拖垮广播
            logger.debug("一起看消息发送失败，连接可能已断", exc_info=True)

    async def _broadcast(self, code: str, payload: dict,
                         exclude=None) -> None:
        for ws in list(self._conns.get(code, {})):
            if ws is exclude:
                continue
            await self._send(ws, payload)

    def _attach(self, code: str, ws, user_id: int, username: str) -> None:
        self._conns.setdefault(code, {})[ws] = {
            "user_id": user_id, "username": username}
        self._ws_room[ws] = code

    def _detach(self, ws) -> Optional[str]:
        code = self._ws_room.pop(ws, None)
        if code is None:
            return None
        conns = self._conns.get(code)
        if conns is not None:
            conns.pop(ws, None)
            if not conns:
                del self._conns[code]
        return code

    def _user_still_in(self, code: str, user_id: int) -> bool:
        return any(info["user_id"] == user_id
                   for info in self._conns.get(code, {}).values())

    def _unique_user_count(self, code: str) -> int:
        return len({info["user_id"]
                    for info in self._conns.get(code, {}).values()})

    def _new_code(self) -> str:
        return "".join(secrets.choice(_CODE_ALPHABET)
                       for _ in range(_CODE_LENGTH))

    # ---------- 建房 / 进房 ----------

    async def create_room(self, ws, user_id: int, item_guid: str) -> dict:
        if ws in self._ws_room:
            raise WatchError("already_in_room", ALREADY_IN_ROOM_MESSAGE)
        username, found, allowed = self._check_access(user_id, item_guid)
        if not found:
            raise WatchError("item_not_found", ITEM_NOT_FOUND_MESSAGE)
        if not allowed:
            raise WatchError("gate_denied", GATE_DENIED_MESSAGE)
        async with self._lock:
            code = self._new_code()
            for _ in range(5):
                if await self.store.get(code) is None:
                    break
                code = self._new_code()
            room = {
                "code": code,
                "item_id": item_guid,
                "host_id": user_id,
                "created_at": time.time(),
                "state": {"playing": False, "position": 0.0,
                          "rate": 1.0, "ts": time.time()},
            }
            await self.store.set(code, room)
            self._attach(code, ws, user_id, username or "")
            return self._snapshot(code, room)

    async def join_room(self, ws, user_id: int, code: str) -> dict:
        code = (code or "").strip().upper()
        room = await self.store.get(code)
        if room is None:
            raise WatchError("room_not_found", ROOM_NOT_FOUND_MESSAGE)
        current = self._ws_room.get(ws)
        if current == code:
            return self._snapshot(code, room)  # 同房重进：幂等回快照
        if current is not None:
            await self._leave_room(ws, user_id)  # 点了新邀请：先离旧房
            room = await self.store.get(code)
            if room is None:
                raise WatchError("room_not_found", ROOM_NOT_FOUND_MESSAGE)
        username, found, allowed = self._check_access(
            user_id, str(room.get("item_id") or ""))
        if not found:
            raise WatchError("item_not_found", ITEM_NOT_FOUND_MESSAGE)
        if not allowed:
            raise WatchError("gate_denied", GATE_DENIED_MESSAGE)
        async with self._lock:
            if (not self._user_still_in(code, user_id)
                    and self._unique_user_count(code) >= MAX_MEMBERS):
                raise WatchError("room_full", ROOM_FULL_MESSAGE)
            self._attach(code, ws, user_id, username or "")
            snapshot = self._snapshot(code, room)
            member = next(m for m in snapshot["members"]
                          if m["user_id"] == user_id)
            await self._broadcast(code, {
                "type": "wt.member_joined",
                "member": member,
                "members": snapshot["members"],
            }, exclude=ws)
            return snapshot

    # ---------- 离房 / 解散 ----------

    async def _leave_room(self, ws, user_id: int) -> None:
        code = self._detach(ws)
        if code is None:
            return
        room = await self.store.get(code)
        if room is None:
            return
        if not self._conns.get(code):
            await self.store.delete(code)  # 人走空了，房间即灭
            return
        payloads: list[dict] = []
        if room.get("host_id") == user_id and not self._user_still_in(
                code, user_id):
            # 房主离开且房里还有人：房主转给最早进房的成员，不散场
            members = self._members_payload(code, room)
            new_host = members[0]["user_id"] if members else None
            if new_host is not None:
                room["host_id"] = new_host
                await self.store.set(code, room)
                members = self._members_payload(code, room)
                payloads.append({
                    "type": "wt.host_changed",
                    "host_id": new_host,
                    "members": members,
                })
        members = self._members_payload(code, room)
        payloads.append({
            "type": "wt.member_left",
            "user_id": user_id,
            "members": members,
        })
        for payload in payloads:
            await self._broadcast(code, payload)

    async def dissolve_room(self, ws, user_id: int) -> None:
        code = self._ws_room.get(ws)
        if code is None:
            raise WatchError("not_in_room", NOT_IN_ROOM_MESSAGE)
        room = await self.store.get(code)
        if room is None:
            self._detach(ws)
            raise WatchError("room_not_found", ROOM_NOT_FOUND_MESSAGE)
        if room.get("host_id") != user_id:
            raise WatchError("not_host", "只有房主能结束一起看。")
        await self._broadcast(code, {"type": "wt.ended", "reason": "dissolved"})
        for member_ws in list(self._conns.get(code, {})):
            self._detach(member_ws)
        await self.store.delete(code)

    # ---------- 控制事件 / 房主锚点 ----------

    async def handle_event(self, ws, user_id: int, data: dict) -> None:
        code = self._ws_room.get(ws)
        if code is None:
            raise WatchError("not_in_room", NOT_IN_ROOM_MESSAGE)
        room = await self.store.get(code)
        if room is None:
            self._detach(ws)
            raise WatchError("room_not_found", ROOM_NOT_FOUND_MESSAGE)
        event = str(data.get("event") or "")
        if event not in ("play", "pause", "seek"):
            raise WatchError("bad_event", "不支持的同步事件。")
        try:
            position = max(0.0, float(data.get("position") or 0))
        except (TypeError, ValueError):
            position = 0.0
        state = dict(room.get("state") or {})
        if event == "play":
            state["playing"] = True
        elif event == "pause":
            state["playing"] = False
        else:  # seek：保持发送方当时的播放态（没带就沿用房间现态）
            state["playing"] = bool(data.get("playing", state.get("playing")))
        state["position"] = position
        state["ts"] = time.time()
        room["state"] = state
        await self.store.set(code, room)
        info = self._conns.get(code, {}).get(ws, {})
        await self._broadcast(code, {
            "type": "wt.event",
            "event": event,
            "position": position,
            "playing": bool(state["playing"]),
            "rate": float(state.get("rate") or 1),
            "from_user_id": user_id,
            "from_username": info.get("username", ""),
            "ts": state["ts"],
        }, exclude=ws)

    async def handle_state(self, ws, user_id: int, data: dict) -> None:
        """房主周期锚点：刷新房间播放态并给成员发对齐信号（非房主忽略）。"""
        code = self._ws_room.get(ws)
        if code is None:
            return
        room = await self.store.get(code)
        if room is None or room.get("host_id") != user_id:
            return
        try:
            position = max(0.0, float(data.get("position") or 0))
            rate = float(data.get("rate") or 1)
        except (TypeError, ValueError):
            return
        state = {
            "playing": bool(data.get("playing")),
            "position": position,
            "rate": rate if rate > 0 else 1.0,
            "ts": time.time(),
        }
        room["state"] = state
        await self.store.set(code, room)
        await self._broadcast(code, {
            "type": "wt.sync",
            "position": current_position(state),
            "playing": state["playing"],
            "rate": state["rate"],
            "ts": state["ts"],
        }, exclude=ws)

    # ---------- WS 入口 ----------

    async def handle_message(self, ws, user_id: int, data: dict) -> None:
        """处理一条 wt.* 消息：业务错误回 wt.error，绝不向上抛断连接。"""
        action = str(data.get("action") or "")
        try:
            if action == "wt.create":
                snapshot = await self.create_room(
                    ws, user_id, str(data.get("item_id") or ""))
                await self._send(ws, {"type": "wt.created", **snapshot})
            elif action == "wt.join":
                snapshot = await self.join_room(
                    ws, user_id, str(data.get("code") or ""))
                await self._send(ws, {"type": "wt.joined", **snapshot})
            elif action == "wt.leave":
                await self._leave_room(ws, user_id)
            elif action == "wt.dissolve":
                await self.dissolve_room(ws, user_id)
            elif action == "wt.event":
                await self.handle_event(ws, user_id, data)
            elif action == "wt.state":
                await self.handle_state(ws, user_id, data)
        except WatchError as exc:
            await self._send(ws, {
                "type": "wt.error", "error": exc.error,
                "message": exc.message})
        except Exception:  # noqa: BLE001 — 一起看故障不许拖垮通知长连
            logger.exception("一起看消息处理失败 action=%s", action)
            await self._send(ws, {
                "type": "wt.error", "error": "internal",
                "message": "一起看出了点问题，稍后再试。"})

    async def handle_disconnect(self, ws, user_id: int) -> None:
        """连接断开即离房（房主转让 / 房间回收在 _leave_room 里）。"""
        try:
            await self._leave_room(ws, user_id)
        except Exception:  # noqa: BLE001 — 断开清理失败不能反噬主流程
            logger.debug("一起看断开清理失败", exc_info=True)


# 全局单例：websocket.py 的消息循环委托到这里
watch_together_hub = WatchTogetherHub()

__all__ = [
    "MAX_MEMBERS",
    "ROOM_TTL_SECONDS",
    "RoomStore",
    "WatchError",
    "WatchTogetherHub",
    "current_position",
    "db_access_checker",
    "watch_together_hub",
]
