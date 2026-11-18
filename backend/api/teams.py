# -*- coding: utf-8 -*-
"""用户端团队接口（2026-11-18 重建批 1）。

路由前缀 /api/user/teams。形态照友站：GET 详情（含创建配置）→
POST preview 结算预览 → POST purchase 确认并支付（钱包扣款）→
成员区 POST/DELETE members、PUT name。业务全在 backend/teams.py，
本层只做鉴权、入参形状与错误映射（与 api/wallet.py 同构：async
端点把同步核心下放线程池，异常统一回滚）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models, sub_accounts
from backend import teams as teams_service
from backend import wallet as wallet_service
from backend.api.user import get_current_user
from backend.database import get_db

teams_router = APIRouter(prefix="/api/user/teams", tags=["团队"])

_STATUS_BY_CODE = {
    "forbidden_sub_account": 403,
    "team_purchase_disabled": 403,
    "wallet_disabled": 403,
    "limit_reached": 403,
    "member_not_found": 404,
}


def _raise_team_error(exc: teams_service.TeamError):
    status = _STATUS_BY_CODE.get(exc.code, 400)
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc)})


class PreviewIn(BaseModel):
    line: str
    traffic_gb: int
    months: int = 1
    streams: int = 0


class PurchaseIn(PreviewIn):
    name: Optional[str] = None
    idempotency_key: str = Field(min_length=1, max_length=60)


class MemberIn(BaseModel):
    username: str
    password: str


class RenameIn(BaseModel):
    name: str


# ---------------- 同步核心（线程池内执行） ----------------

def _detail_core(db: Session, user: models.WebUser) -> dict:
    team, role = teams_service.get_user_team(db, user.id)
    return {
        "config": teams_service.config_payload(db),
        "team": teams_service.team_payload(db, team) if team else None,
        "my_role": role,
    }


def _preview_core(db: Session, user: models.WebUser,
                  payload: PreviewIn) -> dict:
    if sub_accounts.is_sub_account(user):
        raise teams_service.TeamError(
            "子账号不能购买团队套餐", "forbidden_sub_account")
    result = teams_service.quote(
        db, user, line=payload.line, traffic_gb=payload.traffic_gb,
        months=payload.months)
    cfg = teams_service.team_config(db)
    balance = wallet_service.get_balance(db, user.id)
    result["streams"] = payload.streams
    result["purchase_enabled"] = cfg["purchase_enabled"]
    result["balance_fen"] = int(balance * 100)
    result["balance_sufficient"] = (
        Decimal(result["total_fen"]) / 100 <= balance)
    return result


def _purchase_core(db: Session, user: models.WebUser,
                   payload: PurchaseIn) -> dict:
    return teams_service.purchase(
        db, user, line=payload.line, traffic_gb=payload.traffic_gb,
        months=payload.months, streams=payload.streams, name=payload.name,
        idempotency_key=payload.idempotency_key)


def _members_core(db: Session, user: models.WebUser, *, q: str,
                  status: str, page: int, page_size: int) -> dict:
    team, _role = teams_service.get_user_team(db, user.id)
    if team is None:
        raise teams_service.TeamError("请先开通团队套餐", "no_team")
    items, total = teams_service.list_members(
        db, team, q=q, status=status, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page,
            "page_size": page_size}


def _create_member_core(db: Session, user: models.WebUser,
                        payload: MemberIn) -> dict:
    member = teams_service.create_member(
        db, user, username=payload.username, password=payload.password)
    team = teams_service.get_owned_team(db, user.id)
    row = teams_service.get_membership(db, member.id)
    return teams_service.member_payload(db, team, row)


def _remove_member_core(db: Session, user: models.WebUser,
                        member_user_id: int) -> dict:
    teams_service.remove_member(db, user, member_user_id=member_user_id)
    return {"removed": True, "user_id": member_user_id}


def _rename_core(db: Session, user: models.WebUser,
                 payload: RenameIn) -> dict:
    team = teams_service.rename_team(db, user, name=payload.name)
    return {"team": teams_service.team_payload(db, team)}


# ---------------- 端点 ----------------

def _guarded(core, db: Session, *args, **kwargs):
    """同步 guarded 包装（在线程池内执行）：业务错误先回滚再上抛，
    与 api/wallet.py 的回滚口径一致；async 端点本体不碰同步 DB。"""
    try:
        return core(db, *args, **kwargs)
    except teams_service.TeamError:
        db.rollback()
        raise


@teams_router.get("")
async def get_team_detail(
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团队详情＋创建配置（无团队时 team=null，前端渲染介绍卡空态）。"""
    return await run_in_threadpool(_detail_core, db, user)


@teams_router.get("/members")
async def get_team_members(
    q: str = Query("", max_length=100),
    status: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """成员列表：按名称搜索、状态筛选（all/active/disabled）、分页。"""
    try:
        return await run_in_threadpool(
            _guarded, _members_core, db, user, q=q, status=status,
            page=page, page_size=page_size)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)


@teams_router.post("/preview")
async def post_team_preview(
    payload: PreviewIn,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """结算预览：套餐费/旧套餐抵扣/总计/子账户数（不落库）。"""
    try:
        return await run_in_threadpool(
            _guarded, _preview_core, db, user, payload)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)


@teams_router.post("/purchase")
async def post_team_purchase(
    payload: PurchaseIn,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认并支付：钱包余额扣款，成功建团（pending_activation）。"""
    try:
        return await run_in_threadpool(
            _guarded, _purchase_core, db, user, payload)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)


@teams_router.post("/members")
async def post_team_member(
    payload: MemberIn,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """团长创建成员子账号（无邀请码，成员即子账号）。"""
    try:
        return await run_in_threadpool(
            _guarded, _create_member_core, db, user, payload)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)


@teams_router.delete("/members/{member_user_id}")
async def delete_team_member(
    member_user_id: int,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """移除成员：删归属行＋软禁用账号。"""
    try:
        return await run_in_threadpool(
            _guarded, _remove_member_core, db, user, member_user_id)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)


@teams_router.put("/name")
async def put_team_name(
    payload: RenameIn,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改团队名（仅团长）。"""
    try:
        return await run_in_threadpool(
            _guarded, _rename_core, db, user, payload)
    except teams_service.TeamError as exc:
        _raise_team_error(exc)
