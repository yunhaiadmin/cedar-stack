# -*- coding: utf-8 -*-
"""管理端团队接口（2026-11-18 重建批 1）。

挂共用 admin_router（/api/admin 前缀）：团队列表/详情、开通播放
权限（pending_activation → active 状态机）、团队配置读写（家族
"team"，走 config_service 统一编排＋审计）。端点同步 def，与
admin_wallet 同构。
"""
from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend import config_service, models
from backend import teams as teams_service
from backend.config_schema import ConfigValueError
from backend.api.admin_core import _audit, admin_router, get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db


def _team_or_404(db: Session, team_id: int) -> models.Team:
    team = db.get(models.Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    return team


@admin_router.get("/teams/config")
def get_team_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """团队配置现值（默认值已合并，锚点表为解析后的数组）。"""
    return {"config": teams_service.config_payload(db)}


@admin_router.put("/teams/config")
def put_team_config(
    payload: dict,
    request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存团队配置：只收 TEAM_CONFIG_SCHEMA 登记的键，未知键整批
    拒绝；锚点表先做结构校验（非法不落库），再走 save_config 整批
    校验落库＋审计。"""
    from backend import config_schema
    known = config_schema.TEAM_CONFIG_SCHEMA
    unknown = sorted(set(payload) - set(known))
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"未知配置项：{', '.join(unknown)}")
    if not payload:
        raise HTTPException(status_code=400, detail="没有要保存的配置项")
    values = dict(payload)
    if "team_global_price_anchors" in values:
        try:
            anchors = teams_service.validate_anchors(
                values["team_global_price_anchors"])
        except teams_service.TeamError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        values["team_global_price_anchors"] = json.dumps(anchors)

    try:
        saved = config_service.save_config(
            db, values, schema="team",
            descriptions={
                "team_purchase_enabled": "团队购买总开关（默认开）",
                "team_global_min_traffic_gb": "团队 Global 线起订流量（GB）",
                "team_premium_min_traffic_gb": "团队 Premium 线起订流量（GB）",
                "team_max_traffic_gb": "团队可购流量上限（GB）",
                "team_premium_price_per_1000gb_fen":
                    "团队 Premium 单价（美分/千GB）",
                "team_global_price_anchors":
                    "团队 Global 单价锚点表（JSON，美分/千GB）",
                "team_sub_account_divisor": "团队子账户上限除数（流量GB/除数）",
            })
    except ConfigValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "team_update_config", "system_config", None,
           {"keys": saved},
           ip=request.client.host if request.client else None)
    db.commit()
    return {"config": teams_service.config_payload(db), "saved": saved}


@admin_router.get("/teams")
def list_teams(
    status: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """团队列表：状态筛选＋按团队名/团长名搜索，分页。"""
    limit, offset = pagination_params(limit, offset)
    query = db.query(models.Team)
    if status:
        query = query.filter(models.Team.status == status)
    if q:
        query = (query.join(
            models.WebUser, models.WebUser.id == models.Team.owner_id)
            .filter(or_(models.Team.name.ilike(f"%{q.strip()}%"),
                        models.WebUser.username.ilike(f"%{q.strip()}%"))))
    total = query.count()
    rows = (query.order_by(models.Team.id.desc())
            .offset(offset).limit(limit).all())
    return {
        "items": [teams_service.team_payload(db, t) for t in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@admin_router.get("/teams/{team_id}")
def get_team_detail_admin(
    team_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """团队详情：基本面＋成员（含团长行）＋购买订单。"""
    team = _team_or_404(db, team_id)
    member_rows = (db.query(models.TeamMember)
                   .filter(models.TeamMember.team_id == team.id)
                   .order_by(models.TeamMember.id.asc()).all())
    orders = (db.query(models.TeamOrder)
              .filter(models.TeamOrder.team_id == team.id)
              .order_by(models.TeamOrder.id.desc()).all())
    return {
        "team": teams_service.team_payload(db, team),
        "members": [teams_service.member_payload(db, team, r)
                    for r in member_rows],
        "orders": [teams_service.order_payload(o) for o in orders],
    }


@admin_router.post("/teams/{team_id}/activate")
def activate_team_admin(
    team_id: int,
    request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """开通播放权限：pending_activation → active，写审计。"""
    team = _team_or_404(db, team_id)
    try:
        teams_service.activate_team(db, team)
    except teams_service.TeamError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "team_activate_playback", "team", team.id,
           {"owner_id": team.owner_id, "plan_line": team.plan_line},
           ip=request.client.host if request.client else None)
    db.commit()
    return {"team": teams_service.team_payload(db, team)}
