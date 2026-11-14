"""面板播放统计：口味印章与观看秒数（原成就模块的非成就部分）。

成就系统已于 2026-11-14 随签到废止一并删除（用户亲点）；本模块只保留与
成就无关、仍被面板收藏家页与 TG 排行共用的两件：

- playback_seconds：播放会话 → 观看秒数（末次上报进度近似），全站同口径；
- user_tastes：12 口味印章状态（面板收藏家页的类型标题数据源）。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

# 口味映射：genre 关键字 -> 口味 key（12 口味，与前端护照一致）
TASTE_MAP = [
    ("taste_anime", "国漫", ["动画", "动漫", "anime", "animation"]),
    ("taste_drama_cn", "国产剧", ["国产", "华语剧"]),
    ("taste_drama_us", "欧美剧", ["欧美", "美剧", "英剧"]),
    ("taste_drama_jpkr", "日韩剧", ["日剧", "韩剧", "日本", "韩国"]),
    ("taste_movie_cn", "华语电影", ["华语电影"]),
    ("taste_movie_foreign", "外语电影", ["外语电影"]),
    ("taste_movie_anime", "动画电影", ["动画电影"]),
    ("taste_doc", "纪录片", ["纪录片", "documentary"]),
    ("taste_variety", "综艺", ["综艺", "真人秀"]),
    ("taste_kids", "儿童", ["儿童", "少儿"]),
    ("taste_suspense", "悬疑", ["悬疑", "惊悚", "犯罪", "mystery", "thriller", "crime"]),
    ("taste_scifi", "科幻", ["科幻", "奇幻", "sci-fi", "fantasy"]),
]


def _parse_genres(genres_raw) -> list:
    if not genres_raw:
        return []
    if isinstance(genres_raw, list):
        return [str(g) for g in genres_raw]
    try:
        parsed = json.loads(genres_raw)
        if isinstance(parsed, list):
            return [str(g) for g in parsed]
    except Exception:
        pass
    return [str(genres_raw)]


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


def user_tastes(db: Session, user_id: int) -> dict:
    """12 口味印章状态：{taste_key: {'name':, 'stamped': bool, 'count': int}}

    阈值：某口味看满 5 部（去重条目）盖章。genre 缺失的条目不计入任何口味。

    数据源：emby_playback_sessions（真实播放会话，EA 兼容层写入，
    每 (user, item) 一行）。旧数据源 emby_sessions 全仓无写入路径、
    生产恒 0 行，2026-10-19 换源；换源后 genre 走 item_id 精确关联，
    不再用 item_name 模糊匹配（旧法重名条目会串味）。
    r45 的 5000 上界保留（哨兵测试钉住 SQL 必须带 LIMIT）。
    """
    from backend.emby_server import models as em  # noqa: F401  (确保表已注册)
    rows = (
        db.query(em.PlaybackSession, em.MediaItem)
        .join(em.MediaItem, em.MediaItem.id == em.PlaybackSession.item_id)
        .filter(em.PlaybackSession.user_id == user_id,
                # PA-037：联结查询的 FROM 是 Join 对象，软删钩子看不见，
                # 必须自己带 deleted_at 条件，否则已下架条目会进口味统计。
                em.MediaItem.deleted_at.is_(None))
        .order_by(em.PlaybackSession.last_update_at.desc())
        .limit(5000)
        .all()
    )

    taste_counts = {t[0]: 0 for t in TASTE_MAP}
    seen_ids = set()
    for session, item in rows:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        if playback_seconds(session, item) <= 60:  # 至少看 1 分钟才算
            continue
        genres = _parse_genres(getattr(item, "genres", None))
        genre_text = " ".join(genres).lower()
        for tkey, tname, keywords in TASTE_MAP:
            if any(kw.lower() in genre_text for kw in keywords):
                taste_counts[tkey] += 1

    result = {}
    for tkey, tname, _ in TASTE_MAP:
        cnt = taste_counts[tkey]
        result[tkey] = {"name": tname, "stamped": cnt >= 5, "count": cnt}
    return result


__all__ = ["TASTE_MAP", "playback_seconds", "user_tastes"]
