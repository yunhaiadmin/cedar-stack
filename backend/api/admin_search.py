# -*- coding: utf-8 -*-
"""管理后台 · 热搜管理（后台大改造二期缺口「搜索排序/热搜管理」）。

    GET    /api/admin/search/ranking    生效榜预览（与用户端同口径）
    GET    /api/admin/search/terms      窗口内原始聚合词表（含干预状态）
    GET    /api/admin/search/overrides  置顶 / 下榜干预列表
    POST   /api/admin/search/overrides  新增或改一个词的干预（置顶/下榜）
    POST   /api/admin/search/overrides/reorder  置顶区整体重排
    DELETE /api/admin/search/overrides  取消一个词的干预（query: term）
    DELETE /api/admin/search/terms      删除一个词的全部统计（query: term）

核实结论（2026-11-19，逐个 grep 过）：

- 热搜有真实数据：``emby_search_term_stats`` 按（归一化词 × 自然日）
  聚合计数，Emby Items 搜索口在带 SearchTerm 的搜索成功后 best-effort
  写入（见 emby_server.search_stats）；用户端
  ``GET /api/user/media-library/search/hot`` 近 7 天 SUM 出榜，
  门户搜索页走同一搜索口，计数同源。本页是这份数据的管理口。
- 搜索排序**没有配置键**：排序是 emby_server.search 的相关度分层
  （标题精确 > 前缀 > 词边界 > 包含 > 别名 > 拼音 > 元数据 > 模糊，
  TIER_* 常量写死），年份 / 评分不参与排序，config_schema 里没有
  任何 search 排序键。故本项不给排序口径切换——没有真实存在的
  排序实现可切，不造选项。

干预语义（落 SearchTermOverride，读口合并，保存即对用户端生效）：

- 置顶（pin）：按 pin_order 排在生效榜最前，无统计的词次数为 0；
- 下榜（block）：统计照记、永不上榜（适合违规词 / 误拼词）；
- 删除（DELETE /terms）：清掉该词的全部统计行，词立即下榜，之后
  再被搜索会重新累计；该词的置顶行随删（不然删完还置顶在榜上），
  下榜行保留（删数据不等于解除屏蔽）。

全部写操作记 AdminLog（_audit）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import search_stats

logger = logging.getLogger(__name__)

admin_search_router = APIRouter(
    prefix="/api/admin/search", tags=["管理后台·热搜管理"])

_MODES = ("pin", "block")
_MAX_DAYS = 90


def _check_days(days: int) -> int:
    if not (1 <= days <= _MAX_DAYS):
        raise HTTPException(
            status_code=400, detail=f"days 只支持 1~{_MAX_DAYS}")
    return days


def _override_out(row: em.SearchTermOverride) -> dict:
    return {
        "id": row.id,
        "term_key": row.term_key,
        "term": row.display_term,
        "mode": row.mode,
        "pin_order": row.pin_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _find_override(db: Session, term_key: str) -> Optional[em.SearchTermOverride]:
    return (db.query(em.SearchTermOverride)
            .filter(em.SearchTermOverride.term_key == term_key).first())


def _next_pin_order(db: Session) -> int:
    """新置顶排在置顶区末尾：现有 pin 的最大 pin_order + 1（空区为 0）。"""
    orders = [r.pin_order for r in (
        db.query(em.SearchTermOverride)
        .filter(em.SearchTermOverride.mode == "pin").all())]
    return (max(orders) + 1) if orders else 0


@admin_search_router.get("/ranking")
def get_effective_ranking(
    days: int = Query(7, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """生效榜预览：与用户端 GET /search/hot 同一合并函数、同一口径，
    只是多带 term_key / pinned 供页面关联操作。"""
    _check_days(days)
    return {
        "days": days,
        "items": search_stats.hot_search_terms(
            db, limit=limit, days=days, with_flags=True),
    }


@admin_search_router.get("/terms")
def list_search_terms(
    days: int = Query(7, ge=1),
    limit: int = Query(100, ge=1, le=200),
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """窗口内原始聚合词表（不剔除下榜词），每行带当前干预状态。"""
    _check_days(days)
    agg = search_stats.aggregate_terms(db, days=days)
    overrides = {r.term_key: r for r in search_stats.list_overrides(db)}
    items = []
    for a in agg[:limit]:
        row = overrides.get(a["term_key"])
        items.append({
            "term_key": a["term_key"],
            "term": a["term"],
            "count": a["count"],
            "last_seen_at": (a["last_seen_at"].isoformat()
                             if a["last_seen_at"] else None),
            "override": row.mode if row else None,
            "pin_order": row.pin_order if row else None,
        })
    return {"days": days, "total": len(agg), "items": items}


@admin_search_router.get("/overrides")
def list_search_overrides(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """干预列表：置顶（按 pin_order）在前、下榜在后。"""
    return {"items": [_override_out(r) for r in search_stats.list_overrides(db)]}


class OverrideSetRequest(BaseModel):
    term: str = Field(..., description="搜索词原文（服务端按统计同一口径归一化）")
    mode: str = Field(..., description="pin 置顶 / block 下榜")


class OverrideReorderRequest(BaseModel):
    term_keys: list[str] = Field(
        ..., description="当前全部置顶词的归一化键，按目标顺序排列")


@admin_search_router.post("/overrides")
def set_search_override(
    req: OverrideSetRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """新增或修改一个词的干预。同一词重复提交即改模式（幂等口径）：
    转置顶且原先不是置顶时排到置顶区末尾，已置顶的改他项不换序。"""
    if req.mode not in _MODES:
        raise HTTPException(status_code=400, detail="mode 只支持 pin / block")
    term_key = search_stats.normalize_search_term(req.term)
    if not term_key:
        raise HTTPException(status_code=400, detail="搜索词不能为空")
    display = " ".join(str(req.term).split())[:100]

    row = _find_override(db, term_key)
    if row is None:
        row = em.SearchTermOverride(
            term_key=term_key,
            display_term=display,
            mode=req.mode,
            pin_order=_next_pin_order(db) if req.mode == "pin" else 0,
        )
        db.add(row)
    else:
        if req.mode == "pin" and row.mode != "pin":
            row.pin_order = _next_pin_order(db)
        if req.mode == "block":
            row.pin_order = 0
        row.mode = req.mode
        row.display_term = display
    _audit(db, current_admin, "search_hot_override_set", "search_term", None,
           details={"term_key": term_key, "mode": req.mode},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": _override_out(row)}


@admin_search_router.post("/overrides/reorder")
def reorder_search_overrides(
    req: OverrideReorderRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """置顶区整体重排：提交的键集合必须恰好等于当前全部置顶词，
    防止两人同时编辑时用过期顺序覆盖（集合不符 400，先刷新再排）。"""
    pins = (db.query(em.SearchTermOverride)
            .filter(em.SearchTermOverride.mode == "pin").all())
    by_key = {r.term_key: r for r in pins}
    if set(req.term_keys) != set(by_key) or len(req.term_keys) != len(by_key):
        raise HTTPException(
            status_code=400, detail="置顶词集合已变化，请刷新后重新排序")
    for idx, key in enumerate(req.term_keys):
        by_key[key].pin_order = idx
    _audit(db, current_admin, "search_hot_override_reorder", "search_term",
           None, details={"term_keys": list(req.term_keys)},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return {"success": True,
            "items": [_override_out(r) for r in search_stats.list_overrides(db)]}


@admin_search_router.delete("/overrides")
def remove_search_override(
    term: str = Query(..., description="搜索词原文或归一化键"),
    raw_request: Request = None,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """取消一个词的干预（置顶 / 下榜都走这里）：删干预行，词恢复
    按统计自然上榜。不存在 404。"""
    term_key = search_stats.normalize_search_term(term)
    if not term_key:
        raise HTTPException(status_code=400, detail="搜索词不能为空")
    row = _find_override(db, term_key)
    if row is None:
        raise HTTPException(status_code=404, detail="该词没有干预记录")
    mode = row.mode
    row_id = row.id
    db.delete(row)
    _audit(db, current_admin, "search_hot_override_remove", "search_term",
           row_id, details={"term_key": term_key, "mode": mode},
           ip=raw_request.client.host if raw_request and raw_request.client else None)
    db.commit()
    return {"success": True, "term_key": term_key, "mode": mode}


@admin_search_router.delete("/terms")
def delete_search_term_stats(
    term: str = Query(..., description="搜索词原文或归一化键"),
    raw_request: Request = None,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """删除一个词的全部统计行：词立即下榜，之后再被搜索会重新累计。
    该词的置顶行随删（不然删完仍置顶在榜上）；下榜行保留（删数据
    不等于解除屏蔽）。统计与干预都不存在时 404。"""
    term_key = search_stats.normalize_search_term(term)
    if not term_key:
        raise HTTPException(status_code=400, detail="搜索词不能为空")
    row = _find_override(db, term_key)
    removed_pin = bool(row and row.mode == "pin")
    deleted = search_stats.delete_term_stats(db, term_key)
    if row is not None and removed_pin:
        db.delete(row)
    if deleted == 0 and row is None:
        raise HTTPException(status_code=404, detail="该词没有搜索统计")
    _audit(db, current_admin, "search_hot_stats_delete", "search_term", None,
           details={"term_key": term_key, "deleted_rows": deleted,
                    "removed_pin": removed_pin},
           ip=raw_request.client.host if raw_request and raw_request.client else None)
    db.commit()
    return {"success": True, "term_key": term_key,
            "deleted_rows": deleted, "removed_pin": removed_pin}
