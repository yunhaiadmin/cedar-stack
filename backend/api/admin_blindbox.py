# -*- coding: utf-8 -*-
"""管理后台 · 盲盒（双服通用新功能，2026-11-11 用户亲定）。

端点：
    GET  /api/admin/blindbox/config?realm_id=   总开关＋该服档位/奖池＋估值表
    PUT  /api/admin/blindbox/config             保存该服配置（校验+返还率红线在 backend/blindbox）
    PUT  /api/admin/blindbox/master             全站总开关
    PUT  /api/admin/blindbox/prize-values       奖品积分等值估值表
    GET  /api/admin/blindbox/draws              抽盒记录查询
    GET  /api/admin/blindbox/stats              流水/派奖/实际返还率统计
    POST /api/admin/blindbox/draws/{id}/redeliver  发放失败补发

配置走 config_service 家族 blindbox（backend/blindbox 负责结构
校验、公益专属奖限制与期望返还率 >100% 拒绝保存）。鉴权与审计
与管理端其他口同口径（get_current_admin / _audit）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import blindbox as bb
from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_blindbox_router = APIRouter(
    prefix="/api/admin", tags=["管理后台·盲盒"])


class BlindboxRealmConfigRequest(BaseModel):
    realm_id: int
    config: dict[str, Any]


class BlindboxMasterRequest(BaseModel):
    enabled: bool


class BlindboxPrizeValuesRequest(BaseModel):
    values: dict[str, Any]


def _err(exc: bb.BlindboxError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@admin_blindbox_router.get("/blindbox/config")
def get_blindbox_config(
    realm_id: int,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    cfg = bb.get_realm_config(db, realm_id)
    return {
        "success": True,
        "master_enabled": bb.master_enabled(db),
        "realm_id": realm_id,
        "config": cfg,
        "prize_values": bb.prize_values(db),
        "whitelist_stock_remaining": bb.public_tiers(db, realm_id)[
            "whitelist_stock_remaining"],
    }


@admin_blindbox_router.put("/blindbox/config")
def put_blindbox_config(
    payload: BlindboxRealmConfigRequest,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    admin_id = admin.id
    try:
        normalized = bb.save_realm_config(db, payload.realm_id, payload.config)
    except bb.BlindboxError as exc:
        db.rollback()
        raise _err(exc) from exc
    _audit(db, admin_id, "blindbox_save_config", "server_realm",
           payload.realm_id,
           {"tiers": [t["key"] for t in normalized.get("tiers") or []],
            "enabled": normalized.get("enabled")})
    db.commit()
    return {"success": True, "config": normalized}


@admin_blindbox_router.put("/blindbox/master")
def put_blindbox_master(
    payload: BlindboxMasterRequest,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    admin_id = admin.id
    bb.save_master_switch(db, payload.enabled)
    _audit(db, admin_id, "blindbox_master_switch", None, None,
           {"enabled": payload.enabled})
    db.commit()
    return {"success": True, "master_enabled": payload.enabled}


@admin_blindbox_router.put("/blindbox/prize-values")
def put_blindbox_prize_values(
    payload: BlindboxPrizeValuesRequest,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    admin_id = admin.id
    try:
        merged = bb.save_prize_values(db, payload.values)
    except bb.BlindboxError as exc:
        db.rollback()
        raise _err(exc) from exc
    _audit(db, admin_id, "blindbox_save_prize_values", None, None,
           {"values": merged})
    db.commit()
    return {"success": True, "prize_values": merged}


@admin_blindbox_router.get("/blindbox/draws")
def list_blindbox_draws(
    realm_id: Optional[int] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    limit, offset = pagination_params(limit, offset)
    q = db.query(models.BlindboxDraw)
    if realm_id is not None:
        q = q.filter(models.BlindboxDraw.realm_id == realm_id)
    if user_id is not None:
        q = q.filter(models.BlindboxDraw.user_id == user_id)
    if status:
        q = q.filter(models.BlindboxDraw.status == status)
    total = q.count()
    rows = (q.order_by(models.BlindboxDraw.id.desc())
            .offset(offset).limit(limit).all())
    items = []
    for r in rows:
        view = bb.draw_view(r)
        view["user_id"] = r.user_id
        view["delivery"] = r.delivery or {}
        view["error"] = r.error
        items.append(view)
    return {"success": True, "total": total, "items": items}


@admin_blindbox_router.get("/blindbox/stats")
def get_blindbox_stats(
    realm_id: Optional[int] = None,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    return {"success": True, **bb.draw_stats(db, realm_id)}


@admin_blindbox_router.post("/blindbox/draws/{draw_id}/redeliver")
def redeliver_blindbox_draw(
    draw_id: int,
    db: Session = Depends(get_db),
    admin: models.WebUser = Depends(get_current_admin),
):
    admin_id = admin.id
    try:
        out = bb.redeliver(db, draw_id)
    except bb.BlindboxError as exc:
        db.rollback()
        raise _err(exc) from exc
    _audit(db, admin_id, "blindbox_redeliver", "blindbox_draw", draw_id,
           {"status": out["status"]})
    db.commit()
    return {"success": True, **out}
