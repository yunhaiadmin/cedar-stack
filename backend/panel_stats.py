"""面板播放统计：观看秒数（原成就模块的非成就部分）。

成就系统已于 2026-11-14 随签到废止一并删除（用户亲点）；馆灵/口味印章
已于 2026-11-15 随收藏家页一并删除（用户亲点）。本模块只保留仍被
TG 排行等共用的一件：

- playback_seconds：播放会话 → 观看秒数（末次上报进度近似），全站同口径。
"""
from __future__ import annotations


def playback_seconds(session, item=None) -> int:
    """播放会话 → 观看秒数。

    数据源 PlaybackSession（emby_playback_sessions）不存累计秒数，
    用「末次上报进度」近似：min(position_ticks, duration_ticks)/1e7；
    条目无 runtime 时直接 position_ticks/1e7。口径与
    backend/api/home_summary.py 的「继续观看」累计一致（TICKS 单一定义
    在 backend/emby_server/api.py，函数内惰性导入防循环）。
    """
    from backend.emby_server.api import TICKS
    pos = getattr(session, "position_ticks", 0) or 0
    src = item if item is not None else session
    runtime = getattr(src, "duration_ticks", 0) or 0
    return int(min(pos, runtime) // TICKS if runtime else pos // TICKS)


__all__ = ["playback_seconds"]
