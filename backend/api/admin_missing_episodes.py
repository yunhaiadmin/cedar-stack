"""管理后台 · 缺集清单（缺集全套，2026-11-10 用户亲点）。

    GET /api/admin/missing-episodes           缺集清单（分页/排序/搜剧名/按库过滤）
    GET /api/admin/missing-episodes/summary   总览计数＋按库分布＋工人状态
    GET /api/admin/missing-episodes/fill-config   补集通道配置（token 只回是否已设置）
    PUT /api/admin/missing-episodes/fill-config   保存补集通道配置

数据源是缺集核算工人的台账（emby_missing_episode_records，
status='open' 即正在缺集），清单与总览两口只读、不写库。求补
人数与用户端同一数据源现算：该剧未完结的补缺集求片
（movie_requests，request_kind='missing_episodes'）每行记 1 位
发起人＋其 vote_count 位附议人。鉴权与管理端其他口同口径
（get_current_admin）。补集通道（2026-11-11 B 方案）配置走
config_service 家族 mp_episode_fill，与池配置同一校验体系。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import config_service, media_seek
from backend import missing_episodes as me, models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.pagination import pagination_params
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import mp_episode_fill

logger = logging.getLogger(__name__)

missing_router = APIRouter(
    prefix="/api/admin", tags=["管理后台·缺集清单"])


def _requesters_by_series(db: Session, series_rows) -> dict:
    """{series_item_id: 求补人数}：发起人＋附议人，按求片快照里
    的 series_item_id 归属（老快照没带 id 时按 tmdb 归到唯一对
    应的台账行）。"""
    tmdb_to_series = {}
    for row in series_rows:
        tmdb_to_series.setdefault(str(row.tmdb_id or ""), set()).add(
            row.series_item_id)
    counts: dict[int, int] = {}
    requests = (
        db.query(models.MovieRequest)
        .filter(
            models.MovieRequest.request_kind == me.KIND_MISSING,
            models.MovieRequest.status.in_(media_seek.ACTIVE_STATUSES),
        )
        .all())
    for req in requests:
        snap_id = me.parse_request_detail(
            req.request_detail).get("series_item_id")
        target = None
        if snap_id is not None:
            target = int(snap_id)
        else:
            candidates = tmdb_to_series.get(str(req.tmdb_id or ""), set())
            if len(candidates) == 1:
                target = next(iter(candidates))
        if target is not None:
            counts[target] = counts.get(target, 0) + 1 + int(
                req.vote_count or 0)
    return counts


@missing_router.get("/missing-episodes")
def list_missing_episodes(
    limit: int = 50,
    offset: int = 0,
    sort: str = "missing_count",
    q: str = "",
    library_id: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """缺集清单：只列正在缺集（open）的剧。

    sort：missing_count＝缺得多的在前（默认）；first_detected_at
    ＝缺得最久的在前（发现时间最早在前，先补最陈的缺口）。
    """
    limit, offset = pagination_params(limit, offset)
    query = db.query(em.MissingEpisodeRecord).filter(
        em.MissingEpisodeRecord.status == "open")
    q = (q or "").strip()
    if q:
        query = query.filter(
            em.MissingEpisodeRecord.series_name.ilike(f"%{q}%"))
    if library_id is not None:
        query = query.filter(
            em.MissingEpisodeRecord.library_id == library_id)
    if sort == "first_detected_at":
        query = query.order_by(
            em.MissingEpisodeRecord.first_detected_at.asc().nullslast(),
            em.MissingEpisodeRecord.id.asc())
    else:
        query = query.order_by(
            em.MissingEpisodeRecord.missing_count.desc(),
            em.MissingEpisodeRecord.first_detected_at.asc().nullslast(),
            em.MissingEpisodeRecord.id.asc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()

    library_names = {}
    lib_ids = {r.library_id for r in rows if r.library_id is not None}
    if lib_ids:
        library_names = {
            lib.id: lib.name
            for lib in db.query(em.Library).filter(
                em.Library.id.in_(lib_ids)).all()
        }
    requesters = _requesters_by_series(db, rows)

    items = []
    for row in rows:
        detail = me.normalize_missing_detail(row.missing_detail)
        items.append({
            "series_item_id": row.series_item_id,
            "series_name": row.series_name or "",
            "tmdb_id": row.tmdb_id or "",
            "library_id": row.library_id,
            "library_name": library_names.get(row.library_id, ""),
            "missing_detail": {
                str(sn): eps for sn, eps in detail.items()},
            "missing_count": int(row.missing_count or 0),
            "missing_text": me.missing_text(detail),
            "first_detected_at": (
                row.first_detected_at.isoformat()
                if row.first_detected_at else None),
            "last_checked_at": (
                row.last_checked_at.isoformat()
                if row.last_checked_at else None),
            "requesters": requesters.get(row.series_item_id, 0),
        })
    return {"total": total, "items": items}


@missing_router.get("/missing-episodes/summary")
def missing_episodes_summary(
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """总览：缺集剧数、总缺集数、无 TMDB 跳过数、按库分布、工人状态。"""
    open_q = db.query(em.MissingEpisodeRecord).filter(
        em.MissingEpisodeRecord.status == "open")
    open_series = open_q.count()
    total_missing = int(
        db.query(
            func.coalesce(func.sum(em.MissingEpisodeRecord.missing_count), 0))
        .filter(em.MissingEpisodeRecord.status == "open")
        .scalar() or 0)
    skipped_no_tmdb = int(
        db.query(func.count(em.MediaItem.id))
        .filter(
            em.MediaItem.item_type == "series",
            em.MediaItem.deleted_at.is_(None),
            em.MediaItem.merged_into_id.is_(None),
            (em.MediaItem.tmdb_id.is_(None)) | (em.MediaItem.tmdb_id == ""),
        )
        .scalar() or 0)

    per_library = {}
    for library_id, count in (
            db.query(
                em.MissingEpisodeRecord.library_id,
                func.count(em.MissingEpisodeRecord.id))
            .filter(em.MissingEpisodeRecord.status == "open")
            .group_by(em.MissingEpisodeRecord.library_id)
            .all()):
        per_library[library_id] = int(count)
    libraries = []
    for lib in db.query(em.Library).order_by(em.Library.id).all():
        libraries.append({
            "id": lib.id,
            "name": lib.name,
            "open_series": per_library.get(lib.id, 0),
        })

    worker = None
    try:
        from backend.emby_server import missing_episodes_worker

        worker = missing_episodes_worker.status_snapshot()
    except Exception:  # noqa: BLE001 — 工人状态读不到不拖垮总览
        logger.warning("缺集工人状态读取失败", exc_info=True)

    return {
        "open_series": open_series,
        "total_missing": total_missing,
        "skipped_no_tmdb": skipped_no_tmdb,
        "libraries": libraries,
        "worker": worker,
    }


class MpFillConfigRequest(BaseModel):
    # 补集通道配置（家族 mp_episode_fill）。token 是密钥：留空
    # 表示不修改（见 PUT 实现），出口永不回原文。
    mp_download_base: Optional[str] = None
    mp_download_token: Optional[str] = None
    mp_episode_fill_enabled: Optional[bool] = None
    mp_episode_fill_daily_cap: Optional[int] = None
    mp_episode_fill_cooldown_hours: Optional[int] = None


def _fill_config_view(db: Session) -> dict:
    """补集配置出口形状：token 换成「是否已设置」，原文绝不下发。"""
    payload = mp_episode_fill.mp_fill_config_payload(db)
    token = payload.pop("mp_download_token", "")
    payload["mp_download_token_set"] = bool(str(token or "").strip())
    return payload


@missing_router.get("/missing-episodes/fill-config")
def get_fill_config(
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """补集通道配置生效值（存量行优先，缺行回代码默认：开关关、
    每日上限 10、冷却 24 小时）。"""
    return _fill_config_view(db)


@missing_router.put("/missing-episodes/fill-config")
def update_fill_config(
    request: MpFillConfigRequest,
    raw_request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """保存补集通道配置：走 config_service 家族 mp_episode_fill
    校验（与池配置同一体系），非法整批 400、不落库；写时失效，
    工人下一轮即时生效。token 传空串/纯空白＝保持原值不动
    （管理端密码框留空即不改，防把已配好的密钥误清）。"""
    values: dict[str, Any] = {
        key: getattr(request, key) for key in request.model_fields_set
    }
    if "mp_download_token" in values and not str(
            values["mp_download_token"] or "").strip():
        values.pop("mp_download_token")
    saved: list[str] = []
    if values:
        try:
            saved = config_service.save_config(
                db, values, schema="mp_episode_fill",
                descriptions={
                    key: "缺集补集通道配置（MP 手动下载）"
                    for key in values
                })
        except ConfigValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, admin, "missing_update_fill_config", "system",
           None, {"keys": saved},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return _fill_config_view(db)
