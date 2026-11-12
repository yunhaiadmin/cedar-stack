# -*- coding: utf-8 -*-
"""播放会话观看奖励结算（Stopped 与过期回收共用一处口径）。

结算点原本只有 compat_routes._record_stopped：客户端不发 Stopped
（异常退出、部分第三方客户端如 LangHuan）时，会话只能被 maintenance
的过期回收收尾，奖励永远发不出去（2026-11-12 实证：连看 13 集、
每集约 46 分钟，活力 0 发放）。回收路径现在也走本模块同一套结算，
并以 PlaybackSession.rewards_settled_at 做幂等标记：谁先结算谁落
标记，后到的一方（迟到的 Stopped / 重复 Stopped）跳过，防重复发奖。

三笔账与 _record_stopped 的历史口径逐字一致（同函数、同输入）：
- 活力 vitality.award_watch_minutes_reward（单次分钟数分档）；
- 观影 XP member_level.award_watch_xp（同档；自身按条目每日一次幂等）；
- 观影能量 watch_points.award_watch_points（当日累计基线口径，
  重复传同一分钟数天然不重复计）。
三笔各自尽力而为：一笔失败只记日志，绝不影响另外两笔与调用方主流程。
"""

import logging
from datetime import datetime

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

TICKS_PER_MINUTE = 600_000_000


def effective_max_position_ticks(umd: em.UserMediaData) -> int:
    """这行「历史最远看到哪」的有效值（进度双记，FMBY 学习批一 #4）。

    ``max_position_ticks`` 为 NULL = 这行还没被双记写过（升级前老数据），
    回退 ``playback_position_ticks``——老数据在第一次新上报前，最远点就等于
    当时的断点。不做批量回填：靠真实进度上报在 _apply_play_progress
    里以旧 position 为基线自然长出来，心跳不写库的口径不因此被打破。

    （本函数原在 compat_routes，迁来此处作为结算口径的唯一实现；
    compat_routes 仍从本模块导入同名函数，旧引用不变。）
    """
    if umd.max_position_ticks is not None:
        return int(umd.max_position_ticks)
    return int(umd.playback_position_ticks or 0)


def settle_session_rewards(db, user_id: int, item_id: int,
                           watched_ticks, finished: bool) -> None:
    """把一轮观看的三笔奖励各结算一次（各自尽力而为、互不影响）。"""
    minutes = (watched_ticks or 0) / TICKS_PER_MINUTE
    # 观影时长分档奖励：发放走自己的锁 + 提交，失败只记日志。
    try:
        from backend import vitality as _vitality

        _vitality.award_watch_minutes_reward(db, user_id, minutes)
    except Exception:  # noqa: BLE001 — 奖励失败不挡主流程
        logger.debug("观影时长奖励发放失败", exc_info=True)
    # 公益原生经验：观影 XP（与活力同结算点同分档）。
    try:
        from backend import member_level as _ml

        _ml.award_watch_xp(db, user_id, minutes, item_id)
    except Exception:  # noqa: BLE001 — 经验发放失败不挡主流程
        logger.debug("观影 XP 发放失败", exc_info=True)
    # 观影能量：按当日累计分钟分档，幂等与封顶在 watch_points 内。
    try:
        from backend import watch_points as _wp

        _wp.award_watch_points(db, user_id, minutes, item_id, finished)
    except Exception:  # noqa: BLE001 — 奖励失败不挡主流程
        logger.debug("观影能量发放失败", exc_info=True)


def session_watched_ticks(db, session) -> tuple:
    """回收结算的 watched 口径：与 _record_stopped 同基线。

    _record_stopped 用 max(umd 本轮最远点, Stopped 携带位置)；回收时
    没有 Stopped 报文，位置取会话行自己记的最后位置（Progress 写库
    口径），umd 最远点仍参与取大。finished 同 90% 口径（按会话最后
    位置对条目时长判）。返回 (watched_ticks, finished)。
    """
    pos = int(session.position_ticks or 0)
    watched = pos
    umd = (
        db.query(em.UserMediaData)
        .filter(
            em.UserMediaData.user_id == session.user_id,
            em.UserMediaData.item_id == session.item_id,
        )
        .first()
    )
    if umd is not None:
        watched = max(watched, effective_max_position_ticks(umd))
    item = (
        db.query(em.MediaItem)
        .filter(em.MediaItem.id == session.item_id)
        .first()
    )
    runtime = int(item.duration_ticks or 0) if item is not None else 0
    finished = bool(runtime and pos >= runtime * 0.9)
    return watched, finished


def settle_session_row(db, session) -> bool:
    """给一行会话结算观看奖励并落幂等标记（回收器调用）。

    已落标记（rewards_settled_at 非空）直接跳过、返回 False；结算了
    返回 True，标记随调用方的提交一并落库。
    """
    if getattr(session, "rewards_settled_at", None) is not None:
        return False
    watched, finished = session_watched_ticks(db, session)
    settle_session_rewards(db, session.user_id, session.item_id,
                           watched, finished)
    session.rewards_settled_at = datetime.now()
    return True
