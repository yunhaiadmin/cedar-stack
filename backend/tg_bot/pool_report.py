# -*- coding: utf-8 -*-
"""公共池群播报（用户 2026-11-10 亲点）：发到正式群，都不置顶。

两条（与每日小结 21:00、播放排行榜 21:30 错开）：

1. 每晚战报（默认 21:20）：当天（Asia/Shanghai）池子帮了几位
   不同的观看者、被看了几次、当天新加入几块盘、现在共有几块
   盘在供片。当天既无播放又无新盘就不发。
2. 每周英雄榜（默认周日 20:30）：本周（周一至周日）帮人次数
   前 3 位贡献者（称号「片库贡献者」），写清每人帮了几位朋友、
   被看了几次；并列都列，没数据不发。排序：帮的观看者数降序、
   被看次数降序、同数按 user_id 升序稳定；名次按（观看者数、
   次数）组合算，并列同名次、占齐前 3 个名次内的人都列。

口径：
- 数据源 pool_play_events + pool_member_status_events/成员行；
  自己看自己盘剔除（viewer == 成员主人，与每日小结/激励计量
  同口径）；created_at 是 UTC naive 存储帧，上海日界/周界换算
  沿 pool_reward 的 _sh_midnight_utc / period_bounds_utc；
- 「当天新加入」= 当天状态流水里以 apply/approve 落到 active
  的不同成员数（恢复 resume 不算新盘）；
- 「共有几块盘在供片」= 当前 status=active 的成员行数；
- 目标群 = bot_group_ids 第一个群（rankpush.target_group_id，
  与播放排行榜同一个群）；发送走 sender.send_message_ex，
  不置顶；
- 防重复照 playrank 纪律：last_run 标记只在推送成功或空时
  写入（bot_pool_report_last_run=日期、bot_pool_hero_last_run
  =周一日期），发送失败/未配群不写，下个 tick 重试；总开关
  关时不写标记。

开关（SystemConfig，bot 家族，读侧宽容解析）：
- bot_pool_report_enabled 默认开、bot_pool_report_time 默认 21:20；
- bot_pool_hero_enabled 默认开、bot_pool_hero_time 默认 20:30。
调度搭 tg-rankpush 的分钟巡检（与 pool_digest 同拍），不新起线程。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend import models
from backend.emby_server import models as em
from backend.integrations import store
from backend.tg_bot import rankpush, sender

logger = logging.getLogger(__name__)

REPORT_ENABLED_KEY = "bot_pool_report_enabled"
REPORT_TIME_KEY = "bot_pool_report_time"
REPORT_LAST_RUN_KEY = "bot_pool_report_last_run"
HERO_ENABLED_KEY = "bot_pool_hero_enabled"
HERO_TIME_KEY = "bot_pool_hero_time"
HERO_LAST_RUN_KEY = "bot_pool_hero_last_run"

REPORT_DEFAULT_TIME = (21, 20)
HERO_DEFAULT_TIME = (20, 30)

_JOIN_REASONS = ("apply", "approve")


def _cfg(db, key: str, default: str = "") -> str:
    try:
        return str(store.read_value(db, key, default) or "").strip()
    except Exception:
        logger.exception("pool_report 配置读取失败 key=%s", key)
        return default


def _switch(db, key: str, default: bool) -> bool:
    raw = _cfg(db, key, "true" if default else "false").lower()
    if default:
        return raw not in ("0", "false", "off", "no")
    return raw in ("1", "true", "on", "yes")


def _clock(db, key: str, default: tuple[int, int],
           default_text: str) -> tuple[int, int]:
    raw = _cfg(db, key, default_text)
    try:
        hour_s, minute_s = raw.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return default


def report_enabled(db) -> bool:
    return _switch(db, REPORT_ENABLED_KEY, True)


def hero_enabled(db) -> bool:
    return _switch(db, HERO_ENABLED_KEY, True)


def _owner_map(db) -> dict[int, int]:
    rows = db.query(em.PoolMember.id, em.PoolMember.user_id).all()
    return {int(m_id): int(u_id) for m_id, u_id in rows}


def _aggregate_events(db, start_utc, end_utc, owner_by_member) -> dict:
    """窗口内按贡献者聚合（剔除自己看自己）：
    {user_id: {"viewers": set, "plays": int}}。"""
    events = (
        db.query(
            em.PoolPlayEvent.member_id,
            em.PoolPlayEvent.viewer_user_id,
        )
        .filter(
            em.PoolPlayEvent.created_at >= start_utc,
            em.PoolPlayEvent.created_at <= end_utc,
        )
        .all()
    )
    stats: dict[int, dict] = {}
    for member_id, viewer_id in events:
        owner = owner_by_member.get(int(member_id))
        if owner is None or int(viewer_id) == owner:
            continue
        bucket = stats.setdefault(owner, {"viewers": set(), "plays": 0})
        bucket["viewers"].add(int(viewer_id))
        bucket["plays"] += 1
    return stats


def collect_daily_report(db, now_sh) -> dict:
    """当天战报数据：{viewers, plays, new_disks, active_disks}。
    窗口 = 上海当天 00:00 至发送时刻。"""
    from backend.emby_server.pool_reward import _sh_midnight_utc

    start_utc = _sh_midnight_utc(now_sh.date())
    now_utc = now_sh.astimezone(timezone.utc).replace(tzinfo=None)
    stats = _aggregate_events(db, start_utc, now_utc, _owner_map(db))
    viewers: set[int] = set()
    plays = 0
    for bucket in stats.values():
        viewers |= bucket["viewers"]
        plays += bucket["plays"]
    new_rows = (
        db.query(em.PoolMemberStatusEvent.member_id)
        .filter(
            em.PoolMemberStatusEvent.to_status == "active",
            em.PoolMemberStatusEvent.reason.in_(_JOIN_REASONS),
            em.PoolMemberStatusEvent.created_at >= start_utc,
            em.PoolMemberStatusEvent.created_at <= now_utc,
        )
        .distinct()
        .all()
    )
    active_disks = (
        db.query(em.PoolMember)
        .filter(em.PoolMember.status == "active")
        .count()
    )
    return {
        "viewers": len(viewers),
        "plays": plays,
        "new_disks": len(new_rows),
        "active_disks": int(active_disks),
    }


def render_daily_report(stat: dict) -> str:
    return (
        "📊 公共池今日战报\n"
        f"今天池子帮了 {int(stat['viewers'])} 位朋友，"
        f"片被看了 {int(stat['plays'])} 次；"
        f"新加入 {int(stat['new_disks'])} 块盘，"
        f"现在共有 {int(stat['active_disks'])} 块盘在供片。\n"
        "谢谢各位片库贡献者 🙏"
    )


def collect_heroes(db, now_sh) -> list[dict]:
    """本周英雄榜：[{user_id, username, viewers, plays}]，只含
    前 3 个名次（并列都列）。窗口 = 本周一 00:00（上海）至发送时刻。"""
    from backend.emby_server.pool_reward import (
        period_bounds_utc, period_key_for)

    week_key = period_key_for("weekly", now_sh.date())
    start_utc, _end_utc = period_bounds_utc("weekly", week_key)
    now_utc = now_sh.astimezone(timezone.utc).replace(tzinfo=None)
    stats = _aggregate_events(db, start_utc, now_utc, _owner_map(db))
    rows = [
        {"user_id": uid, "viewers": len(b["viewers"]), "plays": b["plays"]}
        for uid, b in stats.items()
        if b["plays"] > 0
    ]
    rows.sort(key=lambda r: (-r["viewers"], -r["plays"], r["user_id"]))
    heroes: list[dict] = []
    rank = 0
    prev_key = None
    for idx, row in enumerate(rows):
        key = (row["viewers"], row["plays"])
        if key != prev_key:
            rank = idx + 1
            prev_key = key
        if rank > 3:
            break
        heroes.append(row)
    if heroes:
        names = {
            int(u.id): u.username
            for u in db.query(models.WebUser)
            .filter(models.WebUser.id.in_([h["user_id"] for h in heroes]))
            .all()
        }
        for hero in heroes:
            hero["username"] = names.get(hero["user_id"]) or ""
    return heroes


def render_hero_board(heroes: list[dict]) -> str:
    lines = ["🏆 本周片库贡献者英雄榜"]
    rank = 0
    prev_key = None
    for idx, hero in enumerate(heroes):
        key = (hero["viewers"], hero["plays"])
        if key != prev_key:
            rank = idx + 1
            prev_key = key
        lines.append(
            f"{rank}. {hero.get('username') or ''}："
            f"帮了 {int(hero['viewers'])} 位朋友 · "
            f"被看了 {int(hero['plays'])} 次")
    lines.append("谢谢各位片库贡献者，是你们让没绑盘的朋友也有片看 🙏")
    return "\n".join(lines)


def _send_to_group(db, text: str) -> tuple[bool, str]:
    group_id = rankpush.target_group_id(db)
    if group_id is None:
        logger.warning("pool_report 未配置交流群（bot_group_ids），跳过")
        return False, "no_group"
    try:
        ok, err, _mid = sender.send_message_ex(
            db, group_id, text, parse_mode=None)
    except Exception:
        logger.exception("pool_report 发送异常")
        return False, "send_failed"
    if not ok:
        logger.warning("pool_report 推送失败 err=%s", err)
        return False, "send_failed"
    return True, "ok"


def push_daily_report(db, now_sh=None) -> dict:
    """生成并推送每晚战报。reason: ok / disabled / empty / no_group / send_failed。"""
    now = now_sh or rankpush._now_shanghai()
    if not report_enabled(db):
        return {"pushed": False, "reason": "disabled"}
    stat = collect_daily_report(db, now)
    if stat["plays"] <= 0 and stat["new_disks"] <= 0:
        return {"pushed": False, "reason": "empty", **stat}
    ok, reason = _send_to_group(db, render_daily_report(stat))
    return {"pushed": ok, "reason": reason, **stat}


def push_weekly_heroes(db, now_sh=None) -> dict:
    """生成并推送每周英雄榜。reason 同 push_daily_report。"""
    now = now_sh or rankpush._now_shanghai()
    if not hero_enabled(db):
        return {"pushed": False, "reason": "disabled", "heroes": []}
    heroes = collect_heroes(db, now)
    if not heroes:
        return {"pushed": False, "reason": "empty", "heroes": []}
    ok, reason = _send_to_group(db, render_hero_board(heroes))
    return {"pushed": ok, "reason": reason, "heroes": heroes}


def run_scheduled_tick(db, now_sh=None) -> list[str]:
    """调度一拍：战报到点发、英雄榜周日到点发。标记纪律照
    playrank（成功/空才写，失败下个 tick 重试）。"""
    now = now_sh or rankpush._now_shanghai()
    actions: list[str] = []
    if report_enabled(db):
        hour, minute = _clock(db, REPORT_TIME_KEY,
                              REPORT_DEFAULT_TIME, "21:20")
        today_key = now.date().isoformat()
        if ((now.hour, now.minute) >= (hour, minute)
                and _cfg(db, REPORT_LAST_RUN_KEY, "") != today_key):
            res = push_daily_report(db, now_sh=now)
            if res["reason"] in ("ok", "empty"):
                store.write_values(db, {REPORT_LAST_RUN_KEY: today_key})
                db.commit()
                actions.append(
                    "report:pushed" if res["pushed"] else "report:empty")
            else:
                actions.append(f"report:{res['reason']}")
    if hero_enabled(db) and now.weekday() == 6:
        hour, minute = _clock(db, HERO_TIME_KEY, HERO_DEFAULT_TIME, "20:30")
        monday = (now.date() - timedelta(days=now.weekday())).isoformat()
        if ((now.hour, now.minute) >= (hour, minute)
                and _cfg(db, HERO_LAST_RUN_KEY, "") != monday):
            res = push_weekly_heroes(db, now_sh=now)
            if res["reason"] in ("ok", "empty"):
                store.write_values(db, {HERO_LAST_RUN_KEY: monday})
                db.commit()
                actions.append(
                    "hero:pushed" if res["pushed"] else "hero:empty")
            else:
                actions.append(f"hero:{res['reason']}")
    return actions
