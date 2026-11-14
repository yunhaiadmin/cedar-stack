# -*- coding: utf-8 -*-
"""管理端·邀请总览（2026-11-14 邀请全套单新增）。

与既有管理面的分工：邀请记录列表在 admin_economy（/economy/invitations）、
渠道码管理与推广奖励策略在 admin_ops、返利比例等钱包键在钱包批 1 的
Settings 经济分组。本模块只补「钱与转化」的总览：

- GET  /api/admin/invite/overview     邀请规模/付费转化/返利总额/待入账
                                      + 异常聚集提示（单邀请人短期大量拉新）
- GET  /api/admin/invite/rebates      返利计提明细（可按状态筛）
- POST /api/admin/invite/rebates/settle  把待入账返利补入账（钱包就绪后用）
- GET/PUT /api/admin/invite/trial-policy 福利邀请（送试用）策略读写

异常聚集只是**提示位**：列出近 7 天拉新 ≥ 阈值的邀请人供人工核查，
不自动处置（处置口径未定，不许代码自作主张封人）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import invite_rebate, invite_trial, models
from backend.api.admin_core import _audit, get_current_admin
from backend.database import get_db

router = APIRouter(prefix="/api/admin/invite", tags=["管理后台-邀请总览"])

#: 异常聚集提示阈值：单个邀请人近 7 天拉新达到这个数就上榜提示
BURST_WINDOW_DAYS = 7
BURST_THRESHOLD = 20


@router.get("/overview")
def invite_overview(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    relations = db.query(models.InvitationRecord).count()
    inviters = (
        db.query(func.count(func.distinct(models.InvitationRecord.inviter_id)))
        .scalar() or 0
    )
    paid_invitees = (
        db.query(func.count(func.distinct(models.InvitationRecord.invitee_id)))
        .join(models.SubscriptionOrder,
              models.SubscriptionOrder.user_id == models.InvitationRecord.invitee_id)
        .filter(models.SubscriptionOrder.status == "paid")
        .scalar() or 0
    )
    rebate_rows = (
        db.query(models.InviteRebateAccrual.status,
                 func.coalesce(func.sum(models.InviteRebateAccrual.rebate_amount), 0),
                 func.count(models.InviteRebateAccrual.id))
        .group_by(models.InviteRebateAccrual.status)
        .all()
    )
    rebate = {"credited": "0.00", "pending": "0.00", "pending_count": 0}
    for status_, total, count in rebate_rows:
        if status_ == invite_rebate.STATUS_CREDITED:
            rebate["credited"] = f"{total:.2f}"
        elif status_ == invite_rebate.STATUS_PENDING:
            rebate["pending"] = f"{total:.2f}"
            rebate["pending_count"] = int(count or 0)
    trial_grants = db.query(models.InviteTrialGrant).count()

    since = datetime.now() - timedelta(days=BURST_WINDOW_DAYS)
    burst_rows = (
        db.query(models.InvitationRecord.inviter_id,
                 func.count(models.InvitationRecord.id).label("cnt"))
        .filter(models.InvitationRecord.created_at >= since)
        .group_by(models.InvitationRecord.inviter_id)
        .having(func.count(models.InvitationRecord.id) >= BURST_THRESHOLD)
        .order_by(func.count(models.InvitationRecord.id).desc())
        .limit(20)
        .all()
    )
    burst_names = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_({r[0] for r in burst_rows})).all()
    } if burst_rows else {}

    return {
        "relations": relations,
        "inviters": int(inviters),
        "paid_invitees": int(paid_invitees),
        "conversion_rate": (round(paid_invitees / relations, 4)
                            if relations else 0),
        "rebate": rebate,
        "trial_grants": trial_grants,
        "rebate_config": invite_rebate.config_snapshot(db),
        "trial_policy": invite_trial.policy_payload(db),
        "burst_alerts": [
            {
                "inviter_id": inviter_id,
                "inviter_username": burst_names.get(inviter_id, "未知"),
                "invited_7d": int(cnt),
            }
            for inviter_id, cnt in burst_rows
        ],
        "burst_rule": f"近 {BURST_WINDOW_DAYS} 天单个邀请人拉新 ≥ {BURST_THRESHOLD} 人",
    }


@router.get("/rebates")
def invite_rebates(
    limit: int = 100,
    status: str | None = None,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    q = db.query(models.InviteRebateAccrual)
    if status in (invite_rebate.STATUS_PENDING, invite_rebate.STATUS_CREDITED):
        q = q.filter(models.InviteRebateAccrual.status == status)
    rows = (q.order_by(models.InviteRebateAccrual.id.desc())
            .limit(min(max(limit, 1), 200)).all())
    user_ids = {r.inviter_id for r in rows} | {r.invitee_id for r in rows}
    users = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_(user_ids)).all()
    } if user_ids else {}
    items = []
    for r in rows:
        dto = invite_rebate.accrual_dto(r, users.get(r.invitee_id, "未知"))
        dto["inviter_username"] = users.get(r.inviter_id, "未知")
        items.append(dto)
    return {"total": len(items), "rebates": items}


@router.post("/rebates/settle")
def invite_rebates_settle(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """把待入账（pending）返利逐笔补入钱包；钱包未就绪时笔数为 0。"""
    settled = invite_rebate.settle_pending_rebates(db)
    _audit(db, current_admin, "invite_rebate_settle", "invite_rebate", None,
           {"settled": settled})
    db.commit()
    remaining = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.status == invite_rebate.STATUS_PENDING)
        .count()
    )
    return {"settled": settled, "pending_remaining": remaining}


class TrialPolicyUpdate(BaseModel):
    invite_trial_enabled: bool | None = None
    invite_trial_days: int | None = None
    invite_welfare_monthly_limit: int | None = None
    invite_welfare_min_paid_fen: int | None = None


@router.get("/trial-policy")
def get_trial_policy(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return {"policy": invite_trial.policy_payload(db)}


@router.put("/trial-policy")
def update_trial_policy(
    payload: TrialPolicyUpdate,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    applied = invite_trial.write_policy(db, values)
    _audit(db, current_admin, "invite_trial_policy_update", "system", None, applied)
    db.commit()
    return {"success": True, "applied": applied,
            "policy": invite_trial.policy_payload(db)}
