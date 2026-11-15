"""网页播放器设置同步（⚠️11，2026-11-15）。

播放器设置（弹幕显示、倍速、字幕字号/延迟等）原先只存浏览器
localStorage，换设备不跟随；本站 Emby 兼容口 DisplayPreferences
又是丢弃式空壳（不落库，第三方播放器口径另议、本模块不碰它）。
这里给门户自己的网页播放器补一条真落库的同步通道：

- 存储：``web_users.player_preferences`` JSON 列——与
  /api/panel/privacy（show_on_leaderboard 列）、TG 通知偏好
  （tg_notify_* 列）同一套「偏好即 WebUser 列」的 per-user 存储，
  不另起第二套 KV 表。
- 语义：整对象存取，服务端**不解释内部结构**（前端约定顶层键
  danmaku / rate / subtitle，见 user_frontend/src/api/playerPrefs.ts）；
  PUT 是整对象覆盖，不是合并——合并口径（本地优先）在前端做。
- NULL = 从未保存过，与「存了空对象 {}」严格区分：新设备第一次
  打开时前端靠 null 判断「服务端也没有」，不拿空对象覆盖本地。
- 子账号按本人存：设置是个人偏好、非经济动作，不走主账号归属
  （与弹幕发言按本人记 user_id 同口径）。
- 上限：序列化后 16KB，超限拒绝（设置对象正常只有几百字节，
  16KB 是防滥用护栏，不是业务额度）。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend import models

MAX_PREFS_BYTES = 16 * 1024


class PlayerPrefsError(ValueError):
    """设置对象不合法（非 object / 超限）：路由层转 400。"""


def validate_prefs(payload: object) -> dict:
    """校验待存对象：必须是 JSON object 且序列化后不超上限。"""
    if not isinstance(payload, dict):
        raise PlayerPrefsError("播放器设置必须是 JSON 对象")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_PREFS_BYTES:
        raise PlayerPrefsError("播放器设置过大")
    return payload


def _row(db: Session, user_id: int) -> models.WebUser | None:
    return db.query(models.WebUser).filter(
        models.WebUser.id == int(user_id)).first()


def get_player_preferences(db: Session, user_id: int) -> dict | None:
    """读该用户保存的设置；从未保存过 / 用户不存在回 None。"""
    row = _row(db, user_id)
    if row is None:
        return None
    prefs = getattr(row, "player_preferences", None)
    return prefs if isinstance(prefs, dict) else None


def set_player_preferences(
    db: Session, user_id: int, payload: object,
) -> dict:
    """整对象覆盖保存（先校验后落库），返回落库后的对象。"""
    prefs = validate_prefs(payload)
    row = _row(db, user_id)
    if row is None:
        raise PlayerPrefsError("用户不存在")
    row.player_preferences = prefs
    db.commit()
    return get_player_preferences(db, user_id) or {}
