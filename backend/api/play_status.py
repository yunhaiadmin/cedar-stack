"""条目起播状态 · 只读 API（GET /api/play/items/{item_id}/status）。

给网站端「起播前状态」展示用（详情页/选播器播放区）：用户点播前
先看清这部片对他现在是什么状态，不再对着转圈猜：

- ``ready``：已就绪——副本在本人绑盘（source=own）、公共池里有
  可下发副本（source=pool）、或条目本身可直读（source=direct）。
- ``preparing``：有转存单在跑——stage=queued（pending）/
  working（running）；有落盘交付行且知道总大小时带
  progress_pct，没有可靠进度就不带（不编数字，更不给预计时间）。
- ``failed``：最近一张本人转存单失败——reason 是稳定类别键
  （space/auth/source_dead/no_binding/generic），人话文案在前端。
- ``idle``：从没准备过（首播会现转存，前端不额外渲染）。

纪律：本模块纯读——不建单、不写库、不碰播放门禁与主链路逻辑。
判定口径全部复用播放主链路的现有单口实现：本人副本/制备中走
``delivery_plan`` 的探针、池走 ``pool_gate`` 的资格与可下发口径、
绑定与大文件分流走 ``pool_gate.large_file_pool_route``，不在此
复制第二套判定。剧集（series）按其集行聚合（转存单/副本都是
按集落的），任一集命中即按该状态报，优先级 own > pool > direct
（direct 仅单条目）> preparing > failed。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db
from backend.api.user import get_current_user
from backend.emby_server import models as em

logger = logging.getLogger("aetrix.play_status")

router = APIRouter(prefix="/api/play", tags=["起播状态"])

#: error_kind → 前端人话类别键（只映射已坐实的稳定类别，其余
#: 一律 generic；不下发 error_detail 原文——里面可能带内部细节）。
_REASON_BY_KIND = {
    "insufficient_space": "space",
    "auth_error": "auth",
    "auth": "auth",
    "dead": "source_dead",
    "drive_copy_dead": "source_dead",
    "item_gone": "source_dead",
    "orphan": "source_dead",
    "no_source_fid": "source_dead",
    "no_drive_file": "source_dead",
    "no_binding": "no_binding",
    "no_drive": "no_binding",
}

_INFLIGHT_STATUSES = ("pending", "running")


def _resolve_item(db: Session, item_id: int):
    """按 id 取条目；合并副本沿 merged_into_id 跟到存活行。

    取不到/最终落到软删行 → None（端点回 404）。
    """
    item = db.query(em.MediaItem).filter_by(id=item_id).first()
    hops = 0
    while item is not None and getattr(item, "merged_into_id", None) \
            and hops < 5:
        nxt = db.query(em.MediaItem).filter_by(
            id=item.merged_into_id).first()
        if nxt is None:
            break
        item = nxt
        hops += 1
    if item is None or getattr(item, "deleted_at", None) is not None:
        return None
    return item


def _episode_ids(db: Session, series_id: int) -> list[int]:
    rows = (db.query(em.MediaItem.id)
            .filter(em.MediaItem.series_id == series_id,
                    em.MediaItem.item_type == "episode",
                    em.MediaItem.deleted_at.is_(None),
                    em.MediaItem.merged_into_id.is_(None))
            .all())
    return [r[0] for r in rows]


def _newest_user_job(db: Session, user_id: int, item_ids: list[int],
                     statuses):
    """本人某组条目上最新的一张转存单（指定状态集内）。

    字段口径与 delivery_plan._probe_user_copy_preparing 一致
    （owner_scope=user + owner_ref=本人 id 字符串）。
    """
    if not item_ids:
        return None
    return (db.query(em.TransferJob)
            .filter(em.TransferJob.item_id.in_(item_ids),
                    em.TransferJob.owner_scope == "user",
                    em.TransferJob.owner_ref == str(user_id),
                    em.TransferJob.status.in_(statuses))
            .order_by(em.TransferJob.id.desc())
            .first())


def _job_progress_pct(db: Session, job) -> int | None:
    """转存单的落盘进度（与 delivery_plan 制备中探针同口径：
    有交付行且总大小已知才给百分比，否则 None——不编造）。"""
    delivery = (db.query(em.TransferDelivery)
                .filter_by(job_id=job.id).one_or_none())
    if delivery is None or not int(delivery.size or 0) > 0:
        return None
    committed = int(delivery.committed_offset or 0)
    return max(0, min(100, committed * 100 // int(delivery.size)))


def compute_play_status(db: Session, user, item) -> dict:
    """(用户, 条目) → 起播状态 dict。纯读；各探针独立 fail-open。"""
    from backend.emby_server import delivery_plan, pool_gate

    user_id = int(user.id)
    is_series = getattr(item, "item_type", "") == "series"
    item_ids = [item.id]
    if is_series:
        item_ids += _episode_ids(db, item.id)

    out = {
        "item_id": item.id,
        "state": "idle",
        "source": None,
        "stage": None,
        "progress_pct": None,
        "reason": None,
    }

    # 1) 本人 ready 副本（单条目走 delivery_plan 探针原口；剧集按集
    #    聚合，查询条件与探针逐字一致：user + ready + 剔除已下线
    #    provider 的副本行）。
    try:
        if is_series:
            rows = (db.query(em.TransferVariant)
                    .filter(em.TransferVariant.item_id.in_(item_ids),
                            em.TransferVariant.owner_scope == "user",
                            em.TransferVariant.owner_ref == str(user_id),
                            em.TransferVariant.status == "ready")
                    .order_by(em.TransferVariant.id.desc())
                    .limit(20).all())
            own_ready = any(
                not (r.variant_drive_id or "").startswith(
                    ("quark:", "pan115:"))
                for r in rows)
        else:
            own_ready = delivery_plan._probe_user_copy_ready(
                db, user_id, item) is not None
    except Exception:  # noqa: BLE001 — 探针失败只是这条腿缺席
        logger.warning("play_status own-ready probe failed item=%s",
                       item.id, exc_info=True)
        own_ready = False
    if own_ready:
        out.update(state="ready", source="own")
        return out

    # 2) 池 ready 副本：资格（池开关+人群+门禁）成立且有可下发
    #    副本。已绑盘用户只有大文件分流命中时才走池（与播放裁决
    #    同口径 large_file_pool_route），未绑用户池就是他的播路。
    try:
        pool_ready = False
        if pool_gate.pool_eligible(db, user, item) == "eligible":
            from backend.emby_server import playback_gate
            if playback_gate.has_active_binding(db, user_id):
                pool_ready = bool(pool_gate.large_file_pool_route(
                    db, user, item))
            else:
                pool_ready = any(
                    pool_gate._servable_pool_copy_count(db, iid) > 0
                    for iid in item_ids)
    except Exception:  # noqa: BLE001
        logger.warning("play_status pool probe failed item=%s",
                       item.id, exc_info=True)
        pool_ready = False
    if pool_ready:
        out.update(state="ready", source="pool")
        return out

    # 3) 条目本身可直读（挂载直读 / 上游映射）——仅单条目口径。
    if not is_series:
        for probe in (delivery_plan._probe_mount_direct,
                      delivery_plan._probe_upstream):
            try:
                leg = probe(db, item)
            except Exception:  # noqa: BLE001
                leg = None
            if leg is not None:
                out.update(state="ready", source="direct")
                return out

    # 4) 制备中：本人有未终态转存单（单条目走探针原口拿进度；
    #    剧集按集聚合后用同一套进度口径）。未绑用户再看池在途单。
    job = None
    try:
        if is_series:
            job = _newest_user_job(db, user_id, item_ids,
                                   _INFLIGHT_STATUSES)
        else:
            leg = delivery_plan._probe_user_copy_preparing(
                db, user_id, item)
            if leg is not None:
                out.update(
                    state="preparing",
                    stage=("queued" if leg.get("job_status") == "pending"
                           else "working"),
                    progress_pct=leg.get("progress_pct"))
                return out
    except Exception:  # noqa: BLE001
        logger.warning("play_status preparing probe failed item=%s",
                       item.id, exc_info=True)
        job = None
    if job is not None:
        out.update(
            state="preparing",
            stage="queued" if job.status == "pending" else "working",
            progress_pct=_job_progress_pct(db, job))
        return out
    try:
        from backend.emby_server import playback_gate
        if not playback_gate.has_active_binding(db, user_id) \
                and pool_gate.pool_eligible(db, user, item) == "eligible" \
                and any(pool_gate._pool_inflight_for_item(db, iid) > 0
                        for iid in item_ids):
            out.update(state="preparing", stage="working")
            return out
    except Exception:  # noqa: BLE001
        logger.warning("play_status pool-inflight probe failed item=%s",
                       item.id, exc_info=True)

    # 5) 最近一张本人失败单 → failed + 类别键。
    try:
        failed = _newest_user_job(db, user_id, item_ids, ("failed",))
    except Exception:  # noqa: BLE001
        logger.warning("play_status failed probe failed item=%s",
                       item.id, exc_info=True)
        failed = None
    if failed is not None:
        kind = (getattr(failed, "error_kind", "") or "").strip()
        out.update(state="failed",
                   reason=_REASON_BY_KIND.get(kind, "generic"))
        return out

    return out


@router.get("/items/{item_id}/status")
def play_status(
    item_id: int,
    db: Session = Depends(get_db),
    user: models.WebUser = Depends(get_current_user),
):
    item = _resolve_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"code": 0, "msg": "ok",
            "data": compute_play_status(db, user, item)}
