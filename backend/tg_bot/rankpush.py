"""观影榜海报日推（embyboss 借鉴清单第 5 项）：聚合 → 海报 → 推群。

数据源：tg_bot.queries.watch_duration_rank（emby_playback_sessions +
panel_achievements.playback_seconds，与账号面板「已观影 X 小时」同源同口径）。
推送目标：机器人现有交流群配置 bot_group_ids（与发言能量同一份
配置）的第一个群，不新造配置。

开关（SystemConfig）：
- bot_rankpush_enabled 总开关，默认开；关了手动/定时都不推。
- 按名次发能量已随积分删除摘除（2026-11-13）：原 grant_rank_rewards
  依赖的 economy._add_points 已删，发分路径整条移除；bot_rankpush_
  reward_enabled / bot_rankpush_reward_table 两个旧配置键不再被读，
  结果里的 rewards 恒为空列表（形状保留）。

调度（时间先写常量，如需可配化由 parent 另定）：
- 日榜每日 20:30（Asia/Shanghai）、周榜周日 21:00 推送；
  防重复推标记存 SystemConfig（bot_rankpush_last_daily / _last_weekly），
  发送成功或空榜才写标记，发送失败下个 tick 重试。
- 空榜（当日/本周无人观影）不推、不发空图。
"""
from __future__ import annotations

import logging
from datetime import datetime

from backend.integrations import store
from backend.tg_bot import layout, policy, queries, rank_poster, sender

logger = logging.getLogger(__name__)

ENABLED_KEY = "bot_rankpush_enabled"
LAST_DAILY_KEY = "bot_rankpush_last_daily"
LAST_WEEKLY_KEY = "bot_rankpush_last_weekly"

# 推送时间（Asia/Shanghai，先写常量）
DAILY_PUSH_HOUR, DAILY_PUSH_MINUTE = 20, 30
WEEKLY_PUSH_HOUR, WEEKLY_PUSH_MINUTE = 21, 0
WEEKLY_PUSH_WEEKDAY = 6  # 周日（datetime.weekday() 口径）

_TOP_N = 10


def _cfg(db, key: str, default: str = "") -> str:
    try:
        return str(store.read_value(db, key, default) or "").strip()
    except Exception:
        logger.exception("rankpush 配置读取失败 key=%s", key)
        return default


def rankpush_enabled(db) -> bool:
    return _cfg(db, ENABLED_KEY, "true").lower() not in ("0", "false", "off", "no")


def target_group_id(db) -> int | None:
    """推送目标＝bot_group_ids（发言能量/chatpoints 同一份群配置）的第一个群。"""
    groups = policy.parse_group_ids(_cfg(db, "bot_group_ids", ""))
    # parse_group_ids 返回集合：多群时取数值最小的一个保证确定性（通常只配一个群）
    return min(groups) if groups else None


def _window_start_sh(period: str):
    from backend.emby_server.portal import leaderboard_window

    start_sh, _start_utc = leaderboard_window(period)
    return start_sh


def period_key(period: str) -> str:
    """幂等/标记键：日榜＝榜日日期、周榜＝当周周一日期（上海日历）。"""
    return _window_start_sh(period).date().isoformat()


def _caption(period: str, rows: list[dict]) -> str:
    start_sh = _window_start_sh(period)
    if period == "week":
        title = "本周观影榜"
        when = f"{start_sh.month}月{start_sh.day}日当周"
    else:
        title = "今日观影榜"
        when = f"{start_sh.month}月{start_sh.day}日"
    lines = [layout.row("📅", "统计", when), layout.row("👥", "上榜", f"{len(rows)} 人")]
    return layout.card(layout.title(f"🏆 {title}"), lines)


def push_rank_board(db, period: str) -> dict:
    """生成并推送一期榜单。返回 {pushed, reason, rows, rewards}。

    reason: ok / disabled / empty / no_group / send_failed。
    rewards 恒为空列表：名次发分已随积分删除摘除（2026-11-13），
    键保留只为结果形状不变。
    """
    period = "week" if period == "week" else "day"
    if not rankpush_enabled(db):
        return {"pushed": False, "reason": "disabled", "rows": [], "rewards": []}
    rows = queries.watch_duration_rank(db, period, _TOP_N)
    if not rows:
        logger.info("rankpush %s 榜为空，跳过推送", period)
        return {"pushed": False, "reason": "empty", "rows": [], "rewards": []}
    group_id = target_group_id(db)
    if group_id is None:
        logger.warning("rankpush 未配置交流群（bot_group_ids），跳过推送")
        return {"pushed": False, "reason": "no_group", "rows": rows, "rewards": []}
    poster_path = None
    try:
        poster_path = rank_poster.render_rank_poster(period, rows, _window_start_sh(period))
        ok, err = sender.send_photo(
            db, group_id, _caption(period, rows), photo_path=poster_path
        )
    except Exception:
        logger.exception("rankpush 海报生成/发送异常 period=%s", period)
        ok, err = False, "exception"
    finally:
        if poster_path is not None:
            try:
                poster_path.unlink(missing_ok=True)
            except OSError:
                pass
    if not ok:
        logger.warning("rankpush 推送失败 period=%s err=%s", period, err)
        return {"pushed": False, "reason": "send_failed", "rows": rows, "rewards": []}
    logger.info(
        "rankpush 已推送 period=%s group=%s rows=%s",
        period, group_id, len(rows),
    )
    return {"pushed": True, "reason": "ok", "rows": rows, "rewards": []}


def _now_shanghai() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return datetime.now()


def run_scheduled_tick(db, now_sh: datetime | None = None) -> list[str]:
    """调度一拍：到点且本期未推过才推。返回动作列表（如 ["day:pushed"]）。

    标记只在推送成功或空榜时写入；发送失败/未配群不写，下个 tick 重试；
    总开关关时直接返回、不写标记（重开后当晚仍可补推）。
    """
    now = now_sh or _now_shanghai()
    actions: list[str] = []
    if not rankpush_enabled(db):
        return actions
    # 日榜：每日 20:30 后、当日未推
    if (now.hour, now.minute) >= (DAILY_PUSH_HOUR, DAILY_PUSH_MINUTE):
        if _cfg(db, LAST_DAILY_KEY, "") != now.date().isoformat():
            res = push_rank_board(db, "day")
            if res["reason"] in ("ok", "empty"):
                store.write_values(db, {LAST_DAILY_KEY: now.date().isoformat()})
                db.commit()
                actions.append(f"day:{res['reason']}" if not res["pushed"] else "day:pushed")
    # 周榜：周日 21:00 后、本周未推（标记键＝当周周一日期）
    if now.weekday() == WEEKLY_PUSH_WEEKDAY and (now.hour, now.minute) >= (
        WEEKLY_PUSH_HOUR,
        WEEKLY_PUSH_MINUTE,
    ):
        monday_key = period_key("week")
        if _cfg(db, LAST_WEEKLY_KEY, "") != monday_key:
            res = push_rank_board(db, "week")
            if res["reason"] in ("ok", "empty"):
                store.write_values(db, {LAST_WEEKLY_KEY: monday_key})
                db.commit()
                actions.append(f"week:{res['reason']}" if not res["pushed"] else "week:pushed")
    return actions
