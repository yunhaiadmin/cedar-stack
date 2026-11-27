"""条目起播状态 · 只读 API（GET /api/play/items/{item_id}/status）。

给网站端「起播前状态」展示用（详情页/选播器播放区）：用户点播前
先看清这部片对他现在是什么状态，不再对着转圈猜：

- ``ready``：已就绪——副本在本人绑盘（source=own）、或条目
  本身可直读（source=direct）。
- ``preparing``：有转存单在跑——stage=queued（pending）/
  working（running）；有落盘交付行且知道总大小时带
  progress_pct，没有字节进度时给时间型预计 eta_seconds
  （近 7 天 done 单 p50 减去已等秒，下限 5 秒、上限 120 秒），
  绝不按 elapsed/p90 编百分比假进度条；elapsed > p90 时
  eta=null 且 slow=true（比平时慢）。
- ``failed``：最近一张本人转存单失败——reason 是稳定类别键
  （space/auth/source_dead/no_binding/generic），人话文案在前端。
- ``idle``：从没准备过（首播会现转存，前端不额外渲染）。

纪律：本模块纯读——不建单、不写库、不碰播放门禁与主链路逻辑。
判定口径全部复用播放主链路的现有单口实现：本人副本/制备中走
``delivery_plan`` 的探针，不在此复制第二套判定。剧集（series）
按其集行聚合（转存单/副本都是按集落的），任一集命中即按该状态
报，优先级 own > direct（direct 仅单条目）> preparing > failed。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db
from backend.api.user import get_current_user
from backend.emby_server import models as em
from backend.integrations import store

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

#: 起播 ETA 配置默认值（SystemConfig 键，见 config_schema
#: .PLAY_STATUS_ETA_CONFIG_SCHEMA）。值是 2026-11-19 生产近 7 天
#: done 单实测：本人单 940 条 p50 6 秒 / p90 26 秒。分位数缓存
#: 默认 600 秒（10 分钟）。
PLAY_STATUS_ETA_CONFIG_DEFAULTS: dict[str, int] = {
    "user_eta_p50_sec": 6,
    "user_eta_p90_sec": 26,
    "play_status_eta_cache_sec": 600,
}

#: 近 7 天 done 单分位数的进程内缓存（route → (p50, p90) 秒）。
#: 单飞刷新：第一把锁 fast-path 读，第二把锁内复核再算。
_ETA_QUANTILES_CACHE: dict = {"at": None, "data": None}
_ETA_CACHE_LOCK = threading.Lock()


def _eta_config(db: Session) -> dict[str, int]:
    """读 ETA 配置（SystemConfig，经 store 缓存读）；非法值回
    代码默认。"""
    try:
        raw = store.get_values(
            db, list(PLAY_STATUS_ETA_CONFIG_DEFAULTS),
            {k: str(v) for k, v in PLAY_STATUS_ETA_CONFIG_DEFAULTS.items()})
    except Exception:  # noqa: BLE001 — 配置读失败就用实测默认
        logger.warning("play_status eta config read failed, "
                       "using defaults", exc_info=True)
        return dict(PLAY_STATUS_ETA_CONFIG_DEFAULTS)
    out: dict[str, int] = {}
    for key, default in PLAY_STATUS_ETA_CONFIG_DEFAULTS.items():
        try:
            out[key] = int(str(raw.get(key, "")).strip())
        except (TypeError, ValueError):
            out[key] = default
    return out


def _percentile(sorted_values: list[float], pct: float) -> int:
    """nearest-rank 分位数（与设计稿 §2 口径一致）。"""
    n = len(sorted_values)
    if n == 0:
        return 0
    rank = max(1, math.ceil(pct / 100.0 * n))
    return int(sorted_values[min(n, rank) - 1])


def _compute_eta_quantiles(db: Session) -> dict[str, tuple[int, int]]:
    """近 7 天 done 单按 route（owner_scope）算耗时分位数。

    耗时 = updated_at − created_at（秒）。返回 route → (p50, p90)，
    某 route 无 done 单时该 route 缺席（调用方用配置兜底）。
    """
    since = datetime.now() - timedelta(days=7)
    rows = (db.query(em.TransferJob.owner_scope,
                     em.TransferJob.created_at,
                     em.TransferJob.updated_at)
            .filter(em.TransferJob.status == "done",
                    em.TransferJob.created_at >= since,
                    em.TransferJob.updated_at.isnot(None))
            .all())
    grouped: dict[str, list[float]] = {}
    for scope, created, updated in rows:
        if created is None or updated is None:
            continue
        secs = (updated - created).total_seconds()
        if secs < 0:
            continue
        grouped.setdefault(scope, []).append(secs)
    out: dict[str, tuple[int, int]] = {}
    for scope, values in grouped.items():
        if not values:
            continue
        values.sort()
        out[scope] = (_percentile(values, 50), _percentile(values, 90))
    return out


def _eta_quantiles(db: Session) -> dict[str, tuple[int, int]]:
    """带 TTL 的分位数读：命中缓存直接回，过期单飞重算。"""
    ttl = _eta_config(db)["play_status_eta_cache_sec"]
    cached = _ETA_QUANTILES_CACHE
    if cached["at"] is not None and time.monotonic() - cached["at"] < ttl:
        return cached["data"]
    with _ETA_CACHE_LOCK:
        if cached["at"] is not None \
                and time.monotonic() - cached["at"] < ttl:
            return cached["data"]
        try:
            data = _compute_eta_quantiles(db)
        except Exception:  # noqa: BLE001 — 聚合失败就走配置兜底
            logger.warning("play_status eta quantiles failed",
                           exc_info=True)
            data = {}
        cached["at"] = time.monotonic()
        cached["data"] = data
        return data


def _reset_eta_cache() -> None:
    """清空分位数缓存（测试用；生产不调）。"""
    with _ETA_CACHE_LOCK:
        _ETA_QUANTILES_CACHE["at"] = None
        _ETA_QUANTILES_CACHE["data"] = None


def _eta_for(route: str, elapsed: int,
             db: Session) -> tuple[int | None, bool]:
    """(route, 已等秒) → (eta_seconds, slow)。

    remaining = p50_route − elapsed，下限 5 秒、上限 120 秒；
    elapsed > p90 时 eta=null 且 slow=true。无历史分位数时用
    配置兜底值（实测 p50/p90）。
    """
    cfg = _eta_config(db)
    quantiles = _eta_quantiles(db)
    p50, p90 = quantiles.get(
        "user", (cfg["user_eta_p50_sec"], cfg["user_eta_p90_sec"]))
    if elapsed > p90:
        return None, True
    return max(5, min(120, p50 - elapsed)), False


def _job_wait_fields(db: Session, job, route: str) -> dict:
    """正在等的单 → route/job_id/elapsed_seconds/eta_seconds/slow。

    fail-open：created_at 缺失或 ETA 算不出时只给 route/job_id，
    elapsed/eta 留 null、slow 留 False，绝不让等待字段拖垮状态
    端点。
    """
    fields = {"route": route, "job_id": job.id,
              "elapsed_seconds": None, "eta_seconds": None, "slow": False}
    created = getattr(job, "created_at", None)
    if created is None:
        return fields
    try:
        elapsed = int((datetime.now() - created).total_seconds())
    except Exception:  # noqa: BLE001
        return fields
    fields["elapsed_seconds"] = max(0, elapsed)
    try:
        eta, slow = _eta_for(route, fields["elapsed_seconds"], db)
    except Exception:  # noqa: BLE001
        logger.warning("play_status eta compute failed", exc_info=True)
        return fields
    fields["eta_seconds"] = eta
    fields["slow"] = slow
    return fields


def _follow_live(db: Session, item):
    """沿 merged_into_id 跟到存活行；最终落到软删行 → None。"""
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


def _resolve_item(db: Session, item_id: int):
    """按 id 取条目；合并副本沿 merged_into_id 跟到存活行。

    取不到/最终落到软删行 → None（端点回 404）。
    """
    item = db.query(em.MediaItem).filter_by(id=item_id).first()
    return _follow_live(db, item)


def _resolve_item_ref(db: Session, ref: str):
    """按前端传来的条目引用取条目。

    前端 DTO 的 Id 恒为 guid 字符串，先按 guid 解析（与
    media_library._resolve_resume_item 同口径）；纯数字串再按
    内部 id 解析，兼容旧 int 传法。都取不到 → None（端点回 404）。
    """
    item = db.query(em.MediaItem).filter(
        em.MediaItem.guid == ref).first()
    if item is not None:
        return _follow_live(db, item)
    if ref.isdigit():
        return _resolve_item(db, int(ref))
    return None


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
    from backend.emby_server import delivery_plan

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
        # 等待可见字段（第三层）：只在 preparing/failed 时有值，
        # ready/idle 全 null/False（见 test_idle_fields_contract）。
        "route": None,
        "job_id": None,
        "elapsed_seconds": None,
        "eta_seconds": None,
        "slow": False,
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

    # 3) 条目本身可直读（挂载直读）——仅单条目口径。
    if not is_series:
        for probe in (delivery_plan._probe_mount_direct,):
            try:
                leg = probe(db, item)
            except Exception:  # noqa: BLE001
                leg = None
            if leg is not None:
                out.update(state="ready", source="direct")
                return out

    # 4) 制备中：本人有未终态转存单（单条目走探针原口拿进度；
    #    剧集按集聚合后用同一套进度口径）。
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
                own_job = _newest_user_job(db, user_id, [item.id],
                                           _INFLIGHT_STATUSES)
                if own_job is not None:
                    out.update(_job_wait_fields(db, own_job, "own"))
                else:
                    out["route"] = "own"
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
        out.update(_job_wait_fields(db, job, "own"))
        return out
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
                   reason=_REASON_BY_KIND.get(kind, "generic"),
                   route="own", job_id=failed.id)
        return out

    return out


@router.get("/items/{item_id}/status")
def play_status(
    item_id: str,
    db: Session = Depends(get_db),
    user: models.WebUser = Depends(get_current_user),
):
    item = _resolve_item_ref(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"code": 0, "msg": "ok",
            "data": compute_play_status(db, user, item)}
