"""站内自发弹幕（网页播放器，2026-11-15）：配置读取与弹幕存取的唯一实现。

- 配置全走 SystemConfig（家族 danmaku，后台「弹幕设置」可改）：
  ``danmaku_enabled`` 站点开关（默认关）、``danmaku_blocked_words``
  敏感词表（逗号分隔、默认空 = 不过滤）、
  ``danmaku_min_interval_seconds`` 最小发送间隔（默认 5 秒）、
  ``danmaku_max_text_length`` 单条字数上限（默认 100）。
  代码里只有默认值，词表内容绝不写死在代码里。
- 存取只认 emby_server.models.DanmakuComment；user_id 记发言者本人
  （子账号按本人记——弹幕是发言行为，不是权益消费，不走有效账号解析）。
- 禁言（内容治理）存 emby_server.models.DanmakuMute，判定唯一实现
  是本文件的 get_active_mute / mute_is_active；管理面在
  backend/api/admin_danmaku.py 的治理端点。
- 第三方客户端的空弹幕兼容口（emby_server.compat_routes 的
  /api/danmu/*）与本站内弹幕无关，不经过这里。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from backend import models as core
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

KEY_ENABLED = "danmaku_enabled"
KEY_BLOCKED_WORDS = "danmaku_blocked_words"
KEY_MIN_INTERVAL = "danmaku_min_interval_seconds"
KEY_MAX_TEXT_LENGTH = "danmaku_max_text_length"

DEFAULT_MIN_INTERVAL_SECONDS = 5
DEFAULT_MAX_TEXT_LENGTH = 100


def _int_value(db: Session, key: str, default: int) -> int:
    raw = store.get_value(db, key, "")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def danmaku_enabled(db: Session) -> bool:
    """站点开关：无行 = 关；只认 "true"（word 风格，与 softdecode 口径一致）。"""
    return store.get_value(db, KEY_ENABLED, "false") == "true"


def parse_blocked_words(raw: str) -> list[str]:
    """敏感词表解析：中英文逗号分隔、去空白、去重保序。空表 = 不过滤。"""
    words: list[str] = []
    for part in str(raw or "").replace("，", ",").split(","):
        word = part.strip()
        if word and word not in words:
            words.append(word)
    return words


def get_settings(db: Session) -> dict:
    """当前生效的弹幕配置（默认值已代入），管理端与用户端共用这一口。"""
    return {
        "enabled": danmaku_enabled(db),
        "blocked_words": store.get_value(db, KEY_BLOCKED_WORDS, ""),
        "min_interval_seconds": _int_value(
            db, KEY_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_SECONDS),
        "max_text_length": _int_value(
            db, KEY_MAX_TEXT_LENGTH, DEFAULT_MAX_TEXT_LENGTH),
    }


def find_blocked_word(text: str, words: list[str]) -> str | None:
    """命中敏感词返回命中的词（大小写不敏感子串匹配），未命中返回 None。"""
    folded = str(text or "").casefold()
    for word in words:
        if word.casefold() in folded:
            return word
    return None


def serialize(row: em.DanmakuComment, username: str | None,
              current_user_id: int) -> dict:
    return {
        "id": row.id,
        "position_seconds": row.position_seconds,
        "text": row.text,
        "username": username or "",
        "mine": row.user_id == current_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_danmaku(db: Session, item_id: int, start: float, end: float,
                 limit: int, current_user_id: int) -> list[dict]:
    """时间窗 [start, end] 内的弹幕，按时间轴升序（同点按 id 升序）。"""
    rows = (
        db.query(em.DanmakuComment, core.WebUser.username)
        .outerjoin(core.WebUser, core.WebUser.id == em.DanmakuComment.user_id)
        .filter(
            em.DanmakuComment.item_id == item_id,
            em.DanmakuComment.position_seconds >= start,
            em.DanmakuComment.position_seconds <= end,
        )
        .order_by(em.DanmakuComment.position_seconds.asc(),
                  em.DanmakuComment.id.asc())
        .limit(limit)
        .all()
    )
    return [serialize(row, username, current_user_id) for row, username in rows]


def create_danmaku(db: Session, user_id: int, item_id: int,
                   position_seconds: float, text: str) -> em.DanmakuComment:
    """落一条弹幕（校验在端点层做完后调用），自带 commit。"""
    row = em.DanmakuComment(
        user_id=user_id,
        item_id=item_id,
        position_seconds=position_seconds,
        text=text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mute_is_active(row: em.DanmakuMute | None,
                   now: datetime | None = None) -> bool:
    """禁言是否生效中：无行 = 否；muted_until 为 NULL（永久）= 是；
    有值时严格大于 now 才算生效（等于 now = 已到期）。"""
    if row is None:
        return False
    if row.muted_until is None:
        return True
    return row.muted_until > (now or datetime.now())


def get_active_mute(db: Session, user_id: int,
                    now: datetime | None = None) -> em.DanmakuMute | None:
    """取该用户当前生效的禁言行，没被禁言（或已到期）返回 None。

    这是禁言判定的唯一实现：发送口（api/danmaku.py）真查，
    管理端列表的 active 标记也走 mute_is_active 同一口径。
    """
    row = (db.query(em.DanmakuMute)
           .filter(em.DanmakuMute.user_id == user_id).first())
    return row if mute_is_active(row, now) else None
