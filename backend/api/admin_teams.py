"""
管理后台 · 团队套餐申请审核

用户在团队页提交的套餐意向单在这里审核：通过即开通（落点见
backend/api/teams.py 的 apply_approved_application），驳回须带原因。
鉴权口径与 admin_pool 一致：get_current_admin 集中判定（只读角色
写请求会被 admin_roles 拦下），写操作走 _audit 留痕。

配置（起订流量/子账号上限/未开通套餐成员上限）走 config_service
家族 team 保存，与用户端读取同源。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.teams import (
    apply_approved_application,
    get_team_config,
)
from backend.config_schema import ConfigValueError
from backend.database import get_db

logger = logging.getLogger(__name__)

admin_teams_router = APIRouter(
    prefix="/api/admin/teams", tags=["管理后台·团队"])


def _application_admin_payload(db: Session,
                               app: models.TeamApplication) -> dict:
    applicant = db.query(models.WebUser).filter(
        models.WebUser.id == app.user_id).first()
    team_name = app.team_name
    if app.team_id:
        team = db.query(models.Team).filter(
            models.Team.id == app.team_id).first()
        if team:
            team_name = team.name
    return {
        "id": app.id,
        "user_id": app.user_id,
        "username": applicant.username if applicant else "已注销",
        "team_id": app.team_id,
        "team_name": team_name,
        "line": app.line,
        "traffic_bytes": app.traffic_bytes,
        "member_limit": app.member_limit,
        "note": app.note or "",
        "status": app.status,
        "review_note": app.review_note or "",
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "reviewed_at": app.reviewed_at.isoformat() if app.reviewed_at else None,
    }


@admin_teams_router.get("/applications")
def list_applications(
    status: str = "pending",
    limit: int = 100,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """申请列表：status=pending（默认）/approved/rejected/all"""
    query = db.query(models.TeamApplication)
    if status != "all":
        query = query.filter(models.TeamApplication.status == status)
    apps = query.order_by(models.TeamApplication.created_at.desc()
                          ).limit(min(limit, 200)).all()
    return {
        "total": len(apps),
        "applications": [_application_admin_payload(db, a) for a in apps],
    }


class ReviewRequest(BaseModel):
    review_note: Optional[str] = None


def _get_pending_application(db: Session,
                             application_id: int) -> models.TeamApplication:
    app = db.query(models.TeamApplication).filter(
        models.TeamApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail="该申请已处理")
    return app


@admin_teams_router.post("/applications/{application_id}/approve")
def approve_application(
    application_id: int,
    payload: ReviewRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """通过：套餐开通登记落到团队（新建或已有）；不涉及支付与流量计量。"""
    app = _get_pending_application(db, application_id)
    try:
        team = apply_approved_application(db, app)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    app.status = "approved"
    app.reviewer_id = current_admin.id
    app.review_note = (payload.review_note or "").strip()
    app.reviewed_at = datetime.now()
    _audit(db, current_admin, "team_application_approve", "team_application",
           app.id, {"team_id": team.id},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return {"application": _application_admin_payload(db, app)}


@admin_teams_router.post("/applications/{application_id}/reject")
def reject_application(
    application_id: int,
    payload: ReviewRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    app = _get_pending_application(db, application_id)
    app.status = "rejected"
    app.reviewer_id = current_admin.id
    app.review_note = (payload.review_note or "").strip()
    app.reviewed_at = datetime.now()
    # 挂在已有团队上且只是「审核中」占位时，把占位退回去
    if app.team_id:
        team = db.query(models.Team).filter(
            models.Team.id == app.team_id).first()
        if team and team.plan_status == "pending":
            team.plan_status = "none"
    _audit(db, current_admin, "team_application_reject", "team_application",
           app.id, {},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return {"application": _application_admin_payload(db, app)}


class TeamConfigRequest(BaseModel):
    # 全部可选：只改传了的键（model_fields_set 区分未传/传值）
    team_plan_min_traffic_bytes: Optional[int] = None
    team_plan_max_members: Optional[int] = None
    team_default_member_limit: Optional[int] = None


@admin_teams_router.get("/config")
def team_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return get_team_config(db)


@admin_teams_router.put("/config")
def team_update_config(
    payload: TeamConfigRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存团队配置：非法值整批 400、不落库；与用户端读取同源即时生效。"""
    values: dict[str, Any] = {
        key: getattr(payload, key) for key in payload.model_fields_set
    }
    saved: list[str] = []
    if values:
        try:
            saved = config_service.save_config(
                db, values, schema="team",
                descriptions={key: "团队配置（2026-11-13）" for key in values})
        except ConfigValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "team_update_config", "system", None,
           {"keys": saved},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return get_team_config(db)
