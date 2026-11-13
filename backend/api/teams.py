"""
团队 API（照 UHDNow 团队形态，2026-11-13）

端点（前缀 /api/user/teams）：
- GET  /config            团队套餐口径（起订流量/子账号上限，SystemConfig 可调）
- GET  /mine              我的团队（未加入时 team=null）＋我的套餐申请
- POST /create            创建团队（创建人即团长）
- POST /join              用邀请码加入团队
- POST /leave             退出团队（团长不能直接退，须先解散）
- POST /dissolve          解散团队（团长）
- POST /members/{id}/remove  移除成员（团长）
- POST /applications      提交团队套餐申请意向单（管理端审核开通）
- GET  /applications/mine 我的套餐申请列表

口径：
- 一人同时只能在一个团队（team_members.user_id 唯一索引兜底）。
- 套餐数值全部来自 SystemConfig 的 team 家族（起订流量、子账号上限、
  未开通套餐的成员上限），代码里只有默认值，判断不写死数值。
- 本模块不做流量计量/共享扣减（本站没有流量额度模型），也不接支付：
  申请审核通过只做套餐开通登记，付款走客服人工流程（与友站一致）。
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db
from backend.integrations.store import switch_env_value as _switch_env_value
from backend.api.user import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/teams", tags=["用户端-团队"])

CODE_ALPHABET = string.ascii_uppercase + string.digits

#: 团队配置默认值（SystemConfig 无行时回退到这里；键的校验范围在
#: config_schema.TEAM_CONFIG_SCHEMA，管理端团队审核页可改）。
TEAM_CONFIG_DEFAULTS = {
    # 套餐起订总流量：10TB（十进制字节，与贡献池容量口径一致）
    "team_plan_min_traffic_bytes": 10 * 10**12,
    # 套餐子账号（成员）上限
    "team_plan_max_members": 100,
    # 未开通套餐的团队成员上限
    "team_default_member_limit": 5,
}


# ==================== 配置读取 ====================

def _get_config(db: Session, key: str, default: str) -> str:
    # 与邀请模块同口径：env 显式设置即接管，否则读 SystemConfig
    _env_v = _switch_env_value(key)
    if _env_v is not None:
        return _env_v
    config = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    return config.value if config and config.value is not None else default


def _get_int_config(db: Session, key: str, default: int) -> int:
    try:
        return int(_get_config(db, key, str(default)))
    except (TypeError, ValueError):
        return default


def get_team_config(db: Session) -> dict:
    """团队生效配置（代码默认 ← SystemConfig 覆盖）"""
    return {
        key: _get_int_config(db, key, default)
        for key, default in TEAM_CONFIG_DEFAULTS.items()
    }


def effective_member_limit(db: Session, team: models.Team) -> int:
    """团队当前可容纳成员数：套餐已开通按套餐值，否则按未开通上限；
    两种口径都不得越过配置里的子账号硬顶。"""
    config = get_team_config(db)
    if team.plan_status == "active" and team.plan_member_limit:
        limit = int(team.plan_member_limit)
    else:
        limit = config["team_default_member_limit"]
    return max(1, min(limit, config["team_plan_max_members"]))


# ==================== 内部辅助 ====================

def _generate_invite_code(db: Session) -> str:
    """生成不冲突的团队邀请码（8 位）"""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not db.query(models.Team).filter(
            models.Team.invite_code == code
        ).first():
            return code


def _my_membership(db: Session, user_id: int) -> models.TeamMember | None:
    return db.query(models.TeamMember).filter(
        models.TeamMember.user_id == user_id).first()


def _active_team(db: Session, team_id: int) -> models.Team | None:
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if team is None or team.status != "active":
        return None
    return team


def _member_count(db: Session, team_id: int) -> int:
    return db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team_id).count()


def _username(db: Session, user_id: int) -> str:
    user = db.query(models.WebUser).filter(
        models.WebUser.id == user_id).first()
    return user.username if user else "已注销"


def _team_payload(db: Session, team: models.Team,
                  membership: models.TeamMember) -> dict:
    members = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team.id
    ).order_by(models.TeamMember.joined_at.asc(),
               models.TeamMember.id.asc()).all()
    return {
        "id": team.id,
        "name": team.name,
        "invite_code": team.invite_code,
        "owner_id": team.owner_id,
        "owner_username": _username(db, team.owner_id),
        "my_role": membership.role,
        "member_count": len(members),
        "member_limit": effective_member_limit(db, team),
        "plan_status": team.plan_status,
        "plan_line": team.plan_line,
        "plan_traffic_bytes": team.plan_traffic_bytes,
        "plan_member_limit": team.plan_member_limit,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "members": [
            {
                "user_id": m.user_id,
                "username": _username(db, m.user_id),
                "role": m.role,
                "joined_at": m.joined_at.isoformat() if m.joined_at else None,
            }
            for m in members
        ],
    }


def _application_payload(db: Session, app: models.TeamApplication) -> dict:
    team_name = app.team_name
    if app.team_id:
        team = db.query(models.Team).filter(
            models.Team.id == app.team_id).first()
        if team:
            team_name = team.name
    return {
        "id": app.id,
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


def _create_team_row(db: Session, owner: models.WebUser,
                     name: str) -> models.Team:
    """建团＋团长成员行（不提交，调用方统一提交）"""
    team = models.Team(
        name=name,
        owner_id=owner.id,
        invite_code=_generate_invite_code(db),
        status="active",
        plan_status="none",
    )
    db.add(team)
    db.flush()
    db.add(models.TeamMember(
        team_id=team.id, user_id=owner.id, role="owner"))
    db.flush()
    return team


# ==================== Endpoints ====================

@router.get("/config")
def team_config(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队套餐口径（申请表单的起订/上限提示与校验同源）"""
    return get_team_config(db)


@router.get("/mine")
def my_team(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的团队：未加入时 team=null；applications 带上我的套餐申请（含待审）"""
    membership = _my_membership(db, current_user.id)
    team_payload = None
    if membership:
        team = _active_team(db, membership.team_id)
        if team:
            team_payload = _team_payload(db, team, membership)
    apps = db.query(models.TeamApplication).filter(
        models.TeamApplication.user_id == current_user.id
    ).order_by(models.TeamApplication.created_at.desc()).limit(20).all()
    return {
        "team": team_payload,
        "config": get_team_config(db),
        "applications": [_application_payload(db, a) for a in apps],
    }


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/create")
def create_team(
    payload: CreateTeamRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="团队名称不能为空")
    if _my_membership(db, current_user.id):
        raise HTTPException(status_code=400, detail="你已经在一个团队里了")
    team = _create_team_row(db, current_user, name)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="你已经在一个团队里了")
    db.refresh(team)
    membership = _my_membership(db, current_user.id)
    return {"team": _team_payload(db, team, membership)}


class JoinTeamRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=20)


@router.post("/join")
def join_team(
    payload: JoinTeamRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _my_membership(db, current_user.id):
        raise HTTPException(status_code=400, detail="你已经在一个团队里了")
    code = payload.invite_code.strip().upper()
    team = db.query(models.Team).filter(
        models.Team.invite_code == code,
        models.Team.status == "active",
    ).first()
    if not team:
        raise HTTPException(status_code=404, detail="邀请码无效或团队已解散")
    if _member_count(db, team.id) >= effective_member_limit(db, team):
        raise HTTPException(status_code=400, detail="团队成员已满")
    db.add(models.TeamMember(
        team_id=team.id, user_id=current_user.id, role="member"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="你已经在一个团队里了")
    membership = _my_membership(db, current_user.id)
    return {"team": _team_payload(db, team, membership)}


@router.post("/leave")
def leave_team(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = _my_membership(db, current_user.id)
    if not membership:
        raise HTTPException(status_code=400, detail="你还没有加入团队")
    if membership.role == "owner":
        raise HTTPException(
            status_code=400, detail="团长不能直接退出，请先解散团队")
    db.delete(membership)
    db.commit()
    return {"left": True}


@router.post("/dissolve")
def dissolve_team(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = _my_membership(db, current_user.id)
    if not membership or membership.role != "owner":
        raise HTTPException(status_code=400, detail="只有团长可以解散团队")
    team = _active_team(db, membership.team_id)
    if not team:
        raise HTTPException(status_code=400, detail="团队不存在或已解散")
    db.query(models.TeamMember).filter(
        models.TeamMember.team_id == team.id).delete()
    team.status = "dissolved"
    team.plan_status = "none"
    db.commit()
    return {"dissolved": True}


@router.post("/members/{user_id}/remove")
def remove_member(
    user_id: int,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = _my_membership(db, current_user.id)
    if not membership or membership.role != "owner":
        raise HTTPException(status_code=400, detail="只有团长可以移除成员")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能移除自己")
    target = db.query(models.TeamMember).filter(
        models.TeamMember.team_id == membership.team_id,
        models.TeamMember.user_id == user_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="该成员不在你的团队里")
    db.delete(target)
    db.commit()
    return {"removed": True}


class TeamApplicationRequest(BaseModel):
    team_name: str | None = Field(default=None, max_length=100)
    line: str | None = Field(default=None, max_length=100)
    traffic_bytes: int = Field(gt=0)
    member_limit: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/applications")
def submit_application(
    payload: TeamApplicationRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交团队套餐申请意向单：数值先按配置校验，管理端审核通过后开通。

    注意：本单只做申请与开通登记，不接支付（钱包未建）、不做流量计量
    （本站没有流量额度模型）——页面文案须如实标注，见 TeamView。
    """
    config = get_team_config(db)
    if payload.traffic_bytes < config["team_plan_min_traffic_bytes"]:
        raise HTTPException(
            status_code=400,
            detail=f"团队套餐起订 {config['team_plan_min_traffic_bytes']} 字节"
                   f"（约 {config['team_plan_min_traffic_bytes'] // 10**12} TB）")
    if payload.member_limit > config["team_plan_max_members"]:
        raise HTTPException(
            status_code=400,
            detail=f"子账号数不能超过 {config['team_plan_max_members']}")

    pending = db.query(models.TeamApplication).filter(
        models.TeamApplication.user_id == current_user.id,
        models.TeamApplication.status == "pending",
    ).first()
    if pending:
        raise HTTPException(status_code=400, detail="已有一份待审核的申请")

    membership = _my_membership(db, current_user.id)
    team_id = None
    team_name = (payload.team_name or "").strip() or None
    if membership:
        if membership.role != "owner":
            raise HTTPException(
                status_code=400,
                detail="你已是其他团队的成员，团队套餐须由团长申请")
        team = _active_team(db, membership.team_id)
        if not team:
            raise HTTPException(status_code=400, detail="团队不存在或已解散")
        team_id = team.id
        team_name = None
        if team.plan_status == "none":
            team.plan_status = "pending"
    app = models.TeamApplication(
        user_id=current_user.id,
        team_id=team_id,
        team_name=team_name,
        line=(payload.line or "").strip() or None,
        traffic_bytes=payload.traffic_bytes,
        member_limit=payload.member_limit,
        note=(payload.note or "").strip(),
        status="pending",
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return {"application": _application_payload(db, app)}


@router.get("/applications/mine")
def my_applications(
    limit: int = 20,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    apps = db.query(models.TeamApplication).filter(
        models.TeamApplication.user_id == current_user.id
    ).order_by(models.TeamApplication.created_at.desc()
               ).limit(min(limit, 100)).all()
    return {
        "total": len(apps),
        "applications": [_application_payload(db, a) for a in apps],
    }


# ==================== 审核通过的落点（管理端调用） ====================

def apply_approved_application(db: Session,
                               app: models.TeamApplication) -> models.Team:
    """把一份已通过的申请落到团队上（不提交，调用方统一提交）。

    - 申请挂着已有团队：把套餐字段写到该团队、plan_status='active'。
    - 申请没有团队：按申请里的团名新建团队（申请人为团长）再写套餐。
    申请人若已是其他团队的成员（提交后又被拉进别的团），无法开通，
    抛 ValueError 让管理端把原因带回页面。
    """
    if app.team_id:
        team = _active_team(db, app.team_id)
        if team is None:
            raise ValueError("申请关联的团队不存在或已解散")
    else:
        membership = _my_membership(db, app.user_id)
        if membership:
            raise ValueError("申请人已加入其他团队，无法新建团队开通套餐")
        applicant = db.query(models.WebUser).filter(
            models.WebUser.id == app.user_id).first()
        if applicant is None:
            raise ValueError("申请人账号不存在")
        name = (app.team_name or "").strip() or f"{applicant.username} 的团队"
        team = _create_team_row(db, applicant, name)
        app.team_id = team.id
    team.plan_status = "active"
    team.plan_line = app.line
    team.plan_traffic_bytes = app.traffic_bytes
    team.plan_member_limit = app.member_limit
    db.flush()
    return team
