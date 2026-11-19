# -*- coding: utf-8 -*-
"""管理后台 · 追新日历（后台大改造二期缺口项，2026-11-19）。

    GET /api/admin/chase-calendar                 按日分组的新增入库条目
    GET /api/admin/chase-calendar/items/{item_id} 条目详情（只读）

现状核实（2026-11-19，逐项查过代码，不许凭印象改口径）：

- 用户端「追新日历」（user_frontend/src/views/panel/ChaseCalendarView.vue）
  的数据源是 ``GET /api/user/emby/calendar``
  （backend/emby_server/portal.py 的 ``get_chase_calendar``）：按
  ``emby_items.date_added``（入库时间）分组的新增条目，**不是播出
  日历**——它按 Asia/Shanghai 自然日分格、只能往过去翻。
- 「剧集下集播出日期」在本仓**没有落库数据源**：``emby_items`` 只有
  ``premiere_date``（剧/电影的首播/上映日，TMDB first_air_date /
  release_date，只补缺项），单集 air_date 只在 TMDB 实时响应里被
  缺集核算（missing_episodes_worker）与刮削消费，从不落库；库里也
  不存在未播出集的条目行（条目行由文件扫描产生）。所以「未来 30 天
  按播出日排期」无数据可依，本页不另起 TMDB 实时推算（与任务口径
  一致：不许另起一套日期推算），落点是与用户端**同一来源**的管理
  视图：同一 ``date_added`` 口径、同一上海自然日分格、同一类型集合。
- 追剧人数有精确数据源：``emby_series_follows``
  （emby_server.models.SeriesFollow，status=active 计数）。
- MP 订阅**没有本地落库**：订阅只存在于 MoviePilot 侧，本仓只有
  实时 API 客户端（backend/moviepilot.py），条目/追剧行上没有任何
  MP 关联键。详情里因此只明示无关联记录，不做实时猜测匹配。
- 排期可写字段：不存在。``date_added`` 是入库事实、``premiere_date``
  是刮削补缺的首播日，都不是可运营编辑的排期字段——本页全只读，
  不硬造字段。

与用户端口径的差异只有一处，且是刻意的：**可见范围**。用户端挂
``_library_scope`` + 身份门（用户看不到的库/未绑定条目不上日历）；
管理端要看的恰恰是「什么东西进了库」，故全库可见、不挂身份门。
日期帧换算（UTC-naive 入库 ↔ 上海自然日）与 portal 逐字同口径：
过滤界 start-8h / end+1d-8h，分格 (date_added + 8h).date()；
解析与上限直接复用 portal 的 ``_calendar_day`` / ``CALENDAR_MAX_DAYS``
/ ``CALENDAR_ITEM_TYPES``，不复制第二套实现。

只读端点：不写 AdminLog（与 #385/#387 等二期只读口同口径）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server.portal import (
    CALENDAR_ITEM_TYPES,
    CALENDAR_MAX_DAYS,
    _calendar_day,
)

logger = logging.getLogger(__name__)

admin_chase_calendar_router = APIRouter(
    prefix="/api/admin/chase-calendar", tags=["管理后台·追新日历"])

#: 管理端单日最多展开多少条详情（用户端格子是 24；管理端要能看全天
#: 清单，给到 200，超出只给真实计数并在 truncated 里明示）。
ADMIN_CALENDAR_MAX_ITEMS_PER_DAY = 200

#: 默认窗口：含今天在内的近 30 天（上海自然日）。
ADMIN_CALENDAR_DEFAULT_DAYS = 30


def _today_shanghai() -> datetime:
    """今天（Asia/Shanghai）的 00:00 naive；与 portal._calendar_month_bounds
    同一取时方式（缺 tzdata 时回退服务器本地时间）。"""
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(
            tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    except Exception:  # noqa: BLE001 — 缺 tzdata 回退旧行为（与 portal 一致）
        return datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0)


def _date_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.date().isoformat() if dt else None


def _follow_counts(db: Session, series_ids: set[int]) -> dict[int, int]:
    """一批剧的 active 追剧人数（一次 GROUP BY，不逐条查）。"""
    if not series_ids:
        return {}
    rows = (db.query(em.SeriesFollow.series_item_id, func.count(em.SeriesFollow.id))
            .filter(em.SeriesFollow.series_item_id.in_(series_ids),
                    em.SeriesFollow.status == "active")
            .group_by(em.SeriesFollow.series_item_id)
            .all())
    return {sid: int(cnt) for sid, cnt in rows}


def _item_out(item: em.MediaItem, *, lib_names: dict[int, str],
              series_names: dict[int, str],
              followers: dict[int, int]) -> dict:
    series_id = item.id if item.item_type == "series" else item.series_id
    return {
        "item_id": item.id,
        "name": item.name,
        "item_type": item.item_type,
        "series_id": series_id,
        "series_name": (item.name if item.item_type == "series"
                        else series_names.get(item.series_id or 0)),
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "library_id": item.library_id,
        "library_name": lib_names.get(item.library_id),
        "date_added": item.date_added.isoformat() if item.date_added else None,
        "premiere_date": _date_str(item.premiere_date),
        "production_year": item.production_year,
        # 电影没有追剧概念，给 null 与剧集的 0 人区分开
        "followers": (followers.get(series_id or 0, 0)
                      if series_id else None),
    }


@admin_chase_calendar_router.get("")
def get_admin_chase_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    item_type: Optional[str] = None,
    library_id: Optional[int] = None,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """按日分组的新增入库条目（口径见模块头注）。

    ``start`` / ``end`` 都是含当天的上海日历日（YYYY-MM-DD）；都不给
    时默认近 30 天（含今天）。区间上限与用户端同一把尺
    （CALENDAR_MAX_DAYS=93 天），超了截到上限而不是报错——与用户端
    ``get_chase_calendar`` 的处理一致。
    """
    start_dt = _calendar_day(start)
    end_dt = _calendar_day(end)
    if start_dt is None and end_dt is None:
        end_dt = _today_shanghai()
        start_dt = end_dt - timedelta(days=ADMIN_CALENDAR_DEFAULT_DAYS - 1)
    elif start_dt is None:
        start_dt = end_dt
    elif end_dt is None:
        end_dt = start_dt
    if end_dt < start_dt:
        end_dt = start_dt
    if (end_dt - start_dt).days >= CALENDAR_MAX_DAYS:
        end_dt = start_dt + timedelta(days=CALENDAR_MAX_DAYS - 1)

    types = [t for t in (item_type or "").split(",")
             if t.strip() in CALENDAR_ITEM_TYPES] or list(CALENDAR_ITEM_TYPES)

    # 闭区间 → SQL 左闭右开；入参是上海日历日、date_added 是 UTC-naive
    # 入库，过滤界换算成 UTC 帧（与 portal 逐字同口径）。
    start_utc = start_dt - timedelta(hours=8)
    end_exclusive_utc = end_dt + timedelta(days=1) - timedelta(hours=8)

    # types 角标口径与用户端一致：先按全类型取行做分类型计数，
    # 类型筛选只作用于明细行，不让角标跟着筛选归零。
    query = (db.query(em.MediaItem)
             .filter(em.MediaItem.is_hidden == False,  # noqa: E712
                     em.MediaItem.date_added >= start_utc,
                     em.MediaItem.date_added < end_exclusive_utc,
                     em.MediaItem.item_type.in_(CALENDAR_ITEM_TYPES)))
    if library_id is not None:
        query = query.filter(em.MediaItem.library_id == library_id)
    rows = query.order_by(em.MediaItem.date_added.desc(),
                          em.MediaItem.id.desc()).all()

    type_counts: dict[str, int] = {}
    for it in rows:
        type_counts[it.item_type] = type_counts.get(it.item_type, 0) + 1
    rows = [it for it in rows if it.item_type in types]

    # 分格（上海自然日）+ 单日截断：count 是当天真实总数，
    # items 截断时 truncated=True，不拿 len(items) 冒充总数。
    day_items: dict[str, list[em.MediaItem]] = {}
    day_counts: dict[str, int] = {}
    for it in rows:
        day = ((it.date_added + timedelta(hours=8)).date()
               if it.date_added else start_dt.date()).isoformat()
        day_counts[day] = day_counts.get(day, 0) + 1
        bucket = day_items.setdefault(day, [])
        if len(bucket) < ADMIN_CALENDAR_MAX_ITEMS_PER_DAY:
            bucket.append(it)

    shown = [it for items in day_items.values() for it in items]
    lib_names = {row[0]: row[1] for row in
                 db.query(em.Library.id, em.Library.name).all()}
    series_ids = {(it.id if it.item_type == "series" else it.series_id)
                  for it in shown}
    series_ids.discard(None)
    series_names: dict[int, str] = {}
    if series_ids:
        series_names = {row[0]: row[1] for row in (
            db.query(em.MediaItem.id, em.MediaItem.name)
            .filter(em.MediaItem.id.in_(series_ids)).all())}
    followers = _follow_counts(db, series_ids)

    days = [
        {
            "date": day,
            "count": day_counts[day],
            "truncated": day_counts[day] > len(day_items[day]),
            "items": [
                _item_out(it, lib_names=lib_names,
                          series_names=series_names, followers=followers)
                for it in day_items[day]
            ],
        }
        for day in sorted(day_items, reverse=True)
    ]
    return {
        "start": start_dt.date().isoformat(),
        "end": end_dt.date().isoformat(),
        "types": type_counts,
        "total": sum(day_counts.values()),
        "days": days,
    }


@admin_chase_calendar_router.get("/items/{item_id}")
def get_admin_chase_calendar_item(
    item_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """条目详情（只读）：来源、所属剧的追剧订阅数、MP 关联明示。

    没有可写排期字段（见模块头注），本口不提供任何编辑能力。
    """
    item = db.query(em.MediaItem).filter(em.MediaItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")

    lib = db.query(em.Library).filter(em.Library.id == item.library_id).first()

    series: Optional[em.MediaItem] = None
    if item.item_type == "series":
        series = item
    elif item.series_id:
        series = db.query(em.MediaItem).filter(
            em.MediaItem.id == item.series_id).first()

    follow_block = None
    if series is not None:
        counts = dict(
            db.query(em.SeriesFollow.status, func.count(em.SeriesFollow.id))
            .filter(em.SeriesFollow.series_item_id == series.id)
            .group_by(em.SeriesFollow.status).all())
        recent = (db.query(em.SeriesFollow)
                  .filter(em.SeriesFollow.series_item_id == series.id)
                  .order_by(em.SeriesFollow.created_at.desc(),
                            em.SeriesFollow.id.desc())
                  .limit(10).all())
        usernames = {}
        if recent:
            usernames = {u.id: u.username for u in (
                db.query(models.WebUser)
                .filter(models.WebUser.id.in_({f.user_id for f in recent}))
                .all())}
        episode_stats = (db.query(func.count(em.MediaItem.id),
                                  func.max(em.MediaItem.date_added))
                         .filter(em.MediaItem.item_type == "episode",
                                 em.MediaItem.series_id == series.id)
                         .first())
        follow_block = {
            "series_id": series.id,
            "series_name": series.name,
            "followers_active": int(counts.get("active", 0)),
            "followers_total": int(sum(counts.values())),
            "recent_followers": [
                {
                    "user_id": f.user_id,
                    "username": usernames.get(f.user_id, ""),
                    "status": f.status,
                    "created_at": (f.created_at.isoformat()
                                   if f.created_at else None),
                }
                for f in recent
            ],
            "episode_count": int(episode_stats[0] or 0),
            "latest_episode_added": (episode_stats[1].isoformat()
                                     if episode_stats and episode_stats[1]
                                     else None),
        }

    return {
        "item_id": item.id,
        "name": item.name,
        "original_title": item.original_title,
        "item_type": item.item_type,
        "library_id": item.library_id,
        "library_name": lib.name if lib is not None else None,
        "production_year": item.production_year,
        "premiere_date": _date_str(item.premiere_date),
        "date_added": item.date_added.isoformat() if item.date_added else None,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "tmdb_id": item.tmdb_id,
        "imdb_id": item.imdb_id,
        # 来源：文件路径与来源形态（管理员判断这条是从哪入库的）
        "file_path": item.file_path,
        "media_source": item.media_source,
        "container": item.container,
        "size": int(item.size or 0),
        "is_hidden": bool(item.is_hidden),
        "series": ({"series_id": series.id, "series_name": series.name}
                   if series is not None and item.item_type == "episode"
                   else None),
        "follow": follow_block,
        # MP 订阅只存在于 MoviePilot 侧、未落库，条目上没有关联键：
        # 明示无关联记录，不做实时猜测匹配（见模块头注）。
        "mp_subscription": None,
        "mp_note": "MP 订阅未落库（只在 MoviePilot 侧），本系统没有条目级关联记录",
    }
