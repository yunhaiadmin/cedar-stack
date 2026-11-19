# -*- coding: utf-8 -*-
"""管理后台 · 字幕管理（后台大改造二期全缺第 7 项，只读盘点口）。

现状核实（2026-11-19，代码逐项对实）：

- 字幕只有两个来源，且**都**落 ``emby_media_streams``（MediaStream）行，
  没有第二张字幕表：
  - 内嵌轨：ffprobe 探测写入（scanner / media_probe.replace_streams），
    ``stream_type='Subtitle'``、``is_external`` 非 True（False 或 NULL），
    codec 是容器内编码名（subrip / ass / hdmv_pgs_subtitle 等），
    language / display_title / is_default / is_forced 都来自探测。
  - 外挂文件：同目录字幕文件由 scanner、subtitle_scan_worker、
    media_probe 三处写行，``is_external=True``、``external_path`` 是
    文件路径（本机路径或 ``mount://…``），codec 是文件扩展名
    （srt / ass / …），language 是按文件名解析出的语言（解析不到
    为 ``und``），display_title / title 是文件名，第一条标默认。
- 所以本盘点口查 MediaStream 即可覆盖**已入库**的全部字幕轨；
  尚未被扫描/探测到的文件不在库里、盘点不到，页面与本文件都不
  假装能覆盖那部分。

本文件只提供读口（列表 + 轨明细），没有任何写端点：搜索字幕、
补字幕等写能力不在本单范围。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session, aliased

from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em

logger = logging.getLogger(__name__)

admin_subtitles_router = APIRouter(
    prefix="/api/admin/subtitles", tags=["管理后台·字幕管理"])

MAX_LIMIT = 200
# 只有真正挂着媒体文件的叶子条目才有字幕轨；series / season 是
# 结构节点，本身没有 MediaStream，盘点口只列 movie / episode。
PLAYABLE_TYPES = ("movie", "episode")


def _subtitle_counts(db: Session):
    """每条目的字幕轨计数子查询：总数 / 外挂数（内嵌 = 总数 - 外挂）。"""
    return (
        db.query(
            em.MediaStream.item_id.label("item_id"),
            func.count(em.MediaStream.id).label("sub_total"),
            func.sum(case(
                (em.MediaStream.is_external.is_(True), 1), else_=0
            )).label("sub_external"),
        )
        .filter(em.MediaStream.stream_type == "Subtitle")
        .group_by(em.MediaStream.item_id)
        .subquery()
    )


def _item_out(item: em.MediaItem, series_name: str,
              sub_total: int, sub_external: int) -> dict:
    return {
        "id": item.id,
        "guid": item.guid,
        "name": item.name,
        "item_type": item.item_type,
        "series_name": series_name or "",
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        "production_year": item.production_year,
        "subtitle_count": int(sub_total or 0),
        "external_count": int(sub_external or 0),
        "embedded_count": int(sub_total or 0) - int(sub_external or 0),
    }


@admin_subtitles_router.get("/items")
def list_subtitle_items(
    limit: int = 50,
    offset: int = 0,
    q: str = "",
    subtitle_filter: str = "",
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """条目字幕盘点列表：按片名（或剧名）搜，带每条字幕轨数。

    subtitle_filter：'' 全部 / 'with' 只看有字幕 / 'without' 只看无字幕。
    total 用 func.count 标量数（不用 Query.count()：全局软删除钩子
    下不到 count 的子查询包装里，见 emby_server/soft_delete.py）。
    """
    if subtitle_filter not in ("", "with", "without"):
        raise HTTPException(status_code=400, detail="subtitle_filter 只支持 with / without")
    counts = _subtitle_counts(db)
    series = aliased(em.MediaItem)
    query = (
        db.query(em.MediaItem, series.name,
                 counts.c.sub_total, counts.c.sub_external)
        .outerjoin(series, em.MediaItem.series_id == series.id)
        .outerjoin(counts, counts.c.item_id == em.MediaItem.id)
        .filter(
            em.MediaItem.item_type.in_(PLAYABLE_TYPES),
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.deleted_at.is_(None),
        )
    )
    text = q.strip()
    if text:
        query = query.filter(
            em.MediaItem.name.contains(text) | series.name.contains(text))
    if subtitle_filter == "with":
        query = query.filter(counts.c.sub_total > 0)
    elif subtitle_filter == "without":
        query = query.filter(counts.c.sub_total.is_(None))
    total = query.with_entities(func.count(em.MediaItem.id)).scalar() or 0
    rows = (
        query.order_by(em.MediaItem.date_added.desc(), em.MediaItem.id.desc())
        .limit(max(1, min(limit, MAX_LIMIT)))
        .offset(max(0, offset))
        .all()
    )
    return {
        "total": int(total),
        "items": [
            _item_out(item, series_name, sub_total, sub_external)
            for item, series_name, sub_total, sub_external in rows
        ],
    }


@admin_subtitles_router.get("/items/{item_id}")
def get_subtitle_item(
    item_id: int,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """单条目的字幕轨明细：按 stream_index 排序，内嵌/外挂分开标。

    外挂轨多给 external_path（本机路径或 mount:// 地址）；
    内嵌轨该字段为空。条目不存在、已下架、已合并副本或不是
    movie/episode 时 404。
    """
    item = (
        db.query(em.MediaItem)
        .filter(
            em.MediaItem.id == item_id,
            em.MediaItem.item_type.in_(PLAYABLE_TYPES),
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.deleted_at.is_(None),
        )
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    series_name = ""
    if item.series_id:
        series = db.query(em.MediaItem).filter(
            em.MediaItem.id == item.series_id).first()
        series_name = series.name if series else ""
    streams = (
        db.query(em.MediaStream)
        .filter(
            em.MediaStream.item_id == item.id,
            em.MediaStream.stream_type == "Subtitle",
        )
        .order_by(em.MediaStream.stream_index, em.MediaStream.id)
        .all()
    )
    tracks = []
    for stream in streams:
        external = stream.is_external is True
        tracks.append({
            "id": stream.id,
            "stream_index": stream.stream_index,
            "source": "external" if external else "embedded",
            "codec": stream.codec or "",
            "language": stream.language or "",
            "title": stream.display_title or stream.title or "",
            "is_default": bool(stream.is_default),
            "is_forced": bool(stream.is_forced),
            "external_path": stream.external_path or "" if external else "",
        })
    out = _item_out(item, series_name, len(tracks),
                    sum(1 for t in tracks if t["source"] == "external"))
    out["tracks"] = tracks
    return out
