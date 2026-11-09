# -*- coding: utf-8 -*-
"""公共池贡献者每日小结（用户 2026-11-10 亲点，情绪价值通知）。

每晚到点给「当天被看过」的贡献者发一条机器人私聊：

    🌙 公共池今日小结
    你的盘今天帮了 2 位朋友，片被看了 4 次；本月累计被看 5 次。
    谢谢你把盘分出来——有人因为你的盘，今晚有片看 🙏

口径（定版，不许漂移）：
- 对象：名下有 active 池成员行的贡献者，按用户聚合（多块盘只
  发一条）；数据源 pool_play_events，窗口按 Asia/Shanghai
  自然日：当天 00:00 至发送时刻，本月累计自当月 1 日 00:00 起
  （created_at 是 UTC naive 存储帧，日界换算沿 pool_reward 的
  _sh_midnight_utc 帧口径）；
- 自己看自己盘不算（viewer_user_id == 贡献者本人剔除，与激励
  计量同口径）；不出现观看者名字、不列片名；
- 当天被看 0 次不发（不发空消息）；
- 每人每天最多一条：走 tg_bot.proactive 的 claim 台账锚
  （TgProactiveNotice 唯一约束 kind=pool_daily_digest、
  subject_id=YYYYMMDD、user_id），调度重启/重复 tick 不重发。
  claim 行只作台账：落账后立刻在本事务内标 sent，不许停在
  pending——proactive 的合并发送只认 arrival/transfer 三类，
  停在 pending 会被它误读成转存失败行拼进别人的提醒里；
- 发送走 tg_bot.sender 私聊（与 proactive._send 同路），不进群、
  不置顶、不发站内信；
- 用户没绑 TG 静默跳过（不写台账）；「贡献回响」TG 偏好
  （notify_prefs 的 pool_digest 类，默认开）关了不发。

开关（SystemConfig，bot 家族）：
- bot_pool_digest_enabled 总开关，默认开；
- bot_pool_digest_time 发送时间（上海 HH:MM），默认 21:00，
  非法值回落 21:00（原文存储、读侧校验，照 playrank 先例）。
调度搭 tg-rankpush 的分钟巡检（scheduler 顺带一拍），不新起线程。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend import models, notify_prefs
from backend.emby_server import models as em
from backend.integrations import store
from backend.tg_bot import proactive, sender

logger = logging.getLogger(__name__)

ENABLED_KEY = "bot_pool_digest_enabled"
TIME_KEY = "bot_pool_digest_time"
KIND = "pool_daily_digest"

DEFAULT_TIME = (21, 0)


def _cfg(db, key: str, default: str = "") -> str:
    try:
        return str(store.read_value(db, key, default) or "").strip()
    except Exception:
        logger.exception("pool_digest 配置读取失败 key=%s", key)
        return default


def _switch(db, key: str, default: bool) -> bool:
    raw = _cfg(db, key, "true" if default else "false").lower()
    if default:
        return raw not in ("0", "false", "off", "no")
    return raw in ("1", "true", "on", "yes")


def digest_enabled(db) -> bool:
    return _switch(db, ENABLED_KEY, True)


def send_clock(db) -> tuple[int, int]:
    """发送钟点（上海时间）：bot_pool_digest_time 原文 HH:MM，非法回落 21:00。"""
    raw = _cfg(db, TIME_KEY, "21:00")
    try:
        hour_s, minute_s = raw.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return DEFAULT_TIME


def _now_shanghai():
    from backend.tg_bot import rankpush

    return rankpush._now_shanghai()


def collect_digest_stats(db, now_sh) -> dict:
    """按贡献者聚合当天/本月被看数据。

    返回 {user_id: {"viewers": int, "plays": int, "month_plays": int}}，
    只含当天 plays > 0 的贡献者。窗口换算：created_at 是 UTC naive
    存储帧，上海日界经 pool_reward._sh_midnight_utc 换算。
    """
    from backend.emby_server.pool_reward import _sh_midnight_utc

    today = now_sh.date()
    month_start_utc = _sh_midnight_utc(today.replace(day=1))
    today_start_utc = _sh_midnight_utc(today)
    now_utc = now_sh.astimezone(timezone.utc).replace(tzinfo=None)

    members = (
        db.query(em.PoolMember.id, em.PoolMember.user_id)
        .filter(em.PoolMember.status == "active")
        .all()
    )
    owner_by_member = {int(m_id): int(u_id) for m_id, u_id in members}
    if not owner_by_member:
        return {}
    events = (
        db.query(
            em.PoolPlayEvent.member_id,
            em.PoolPlayEvent.viewer_user_id,
            em.PoolPlayEvent.created_at,
        )
        .filter(
            em.PoolPlayEvent.member_id.in_(list(owner_by_member)),
            em.PoolPlayEvent.created_at >= month_start_utc,
            em.PoolPlayEvent.created_at <= now_utc,
        )
        .all()
    )
    stats: dict[int, dict] = {}
    for member_id, viewer_id, created_at in events:
        owner = owner_by_member.get(int(member_id))
        if owner is None or int(viewer_id) == owner:
            continue  # 自己看自己盘不算（与激励计量同口径）
        bucket = stats.setdefault(
            owner, {"viewers": set(), "plays": 0, "month_plays": 0})
        bucket["month_plays"] += 1
        if created_at >= today_start_utc:
            bucket["plays"] += 1
            bucket["viewers"].add(int(viewer_id))
    return {
        uid: {
            "viewers": len(b["viewers"]),
            "plays": b["plays"],
            "month_plays": b["month_plays"],
        }
        for uid, b in stats.items()
        if b["plays"] > 0
    }


def render_digest(stat: dict) -> str:
    return (
        "🌙 公共池今日小结\n"
        f"你的盘今天帮了 {int(stat['viewers'])} 位朋友，"
        f"片被看了 {int(stat['plays'])} 次；"
        f"本月累计被看 {int(stat['month_plays'])} 次。\n"
        "谢谢你把盘分出来——有人因为你的盘，今晚有片看 🙏"
    )


def push_daily_digests(db, now_sh=None) -> dict:
    """给当天有被看的贡献者逐个发小结。返回计数摘要。

    reason: ok / disabled。sent 是真发出的条数；skipped_pref /
    skipped_unbound / skipped_claimed 分别是偏好关、没绑 TG、
    今天已发过（claim 撞锚）的人数。
    """
    result = {"sent": 0, "skipped_pref": 0, "skipped_unbound": 0,
              "skipped_claimed": 0, "reason": "ok"}
    if not digest_enabled(db):
        result["reason"] = "disabled"
        return result
    now = now_sh or _now_shanghai()
    stats = collect_digest_stats(db, now)
    subject = int(now.strftime("%Y%m%d"))
    for user_id in sorted(stats):
        try:
            user = (
                db.query(models.WebUser)
                .filter(models.WebUser.id == user_id)
                .first()
            )
            chat_id = getattr(user, "telegram_id", None) if user else None
            if not chat_id:
                result["skipped_unbound"] += 1
                continue
            if not notify_prefs.tg_allowed(
                    db, user_id, notify_prefs.CAT_POOL_DIGEST):
                result["skipped_pref"] += 1
                continue
            if not proactive._claim(
                    db, KIND, subject, user_id, "公共池每日小结", ""):
                result["skipped_claimed"] += 1
                continue
            # 台账锚落定：本事务内直接标 sent，绝不停在 pending
            # （见模块 docstring 的误读风险）。
            (
                db.query(models.TgProactiveNotice)
                .filter(
                    models.TgProactiveNotice.kind == KIND,
                    models.TgProactiveNotice.subject_id == subject,
                    models.TgProactiveNotice.user_id == user_id,
                )
                .update(
                    {"status": "sent", "updated_at": datetime.now()},
                    synchronize_session=False,
                )
            )
            db.commit()
            ok, err = sender.send_message(
                db, int(chat_id), render_digest(stats[user_id]),
                parse_mode=None)
            if ok:
                result["sent"] += 1
            else:
                logger.warning(
                    "pool_digest 发送失败 user=%s err=%s", user_id, err)
        except Exception:
            logger.exception("pool_digest 单人发送失败 user=%s", user_id)
            try:
                db.rollback()
            except Exception:
                pass
    if result["sent"]:
        logger.info("pool_digest 已发送 %s 条（%s）",
                    result["sent"], now.date().isoformat())
    return result


def run_scheduled_tick(db, now_sh=None) -> list[str]:
    """调度一拍：到配置钟点才发；每人每天一条靠 claim 锚幂等，
    不另写全局 last_run 标记。返回动作列表（如 ["digest:sent=2"]）。"""
    now = now_sh or _now_shanghai()
    if not digest_enabled(db):
        return []
    hour, minute = send_clock(db)
    if (now.hour, now.minute) < (hour, minute):
        return []
    res = push_daily_digests(db, now_sh=now)
    if res["sent"]:
        return [f"digest:sent={res['sent']}"]
    return []
