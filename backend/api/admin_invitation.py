# -*- coding: utf-8 -*-
"""管理端·邀请总览（2026-11-14 邀请全套单新增）。

与既有管理面的分工：邀请记录列表在 admin_economy（/economy/invitations）、
渠道码管理与推广奖励策略在 admin_ops、返利比例等钱包键在钱包批 1 的
Settings 经济分组。本模块只补「钱与转化」的总览：

- GET  /api/admin/invite/overview     邀请规模/付费转化/返利总额/待入账
                                      + 异常聚集提示（单邀请人短期大量拉新）
- GET  /api/admin/invite/rebates      返利计提明细（可按状态筛）
- GET  /api/admin/invite/promoters    推广者（AFF）汇总：按邀请人聚合
                                      邀请/付费/返利（口径在 invite_promoter）
- GET  /api/admin/invite/promoters/export  推广者汇总 CSV 导出
- PUT  /api/admin/invite/promoters/{user_id}  推广者档案（专属费率/停用/备注）
- POST /api/admin/invite/rebates/settle  把待入账返利补入账（钱包就绪后用）
- GET/PUT /api/admin/invite/trial-policy 福利邀请（送试用）策略读写
- GET  /api/admin/invite/trial-codes  福利试用码只读列表（2026-11-15 改版）

异常聚集只是**提示位**：列出近 7 天拉新 ≥ 阈值的邀请人供人工核查，
不自动处置（处置口径未定，不许代码自作主张封人）。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import invite_promoter, invite_rebate, invite_trial, models
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


# ==================== 推广者（AFF）管理 ====================
# 汇总聚合口径在 backend/invite_promoter.py（与上方总览同源）；
# 档案写入走 AdminLog 审计（_audit），与本模块其他写操作同法。

@router.get("/promoters")
def invite_promoters(
    search: str | None = None,
    sort: str = "total_paid",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """推广者汇总：按邀请人聚合邀请/付费/返利，可搜索、排序、分页。"""
    total, rows = invite_promoter.promoter_summaries(
        db, search=search, sort=sort, order=order,
        page=page, page_size=page_size)
    return {"total": total, "page": max(1, page),
            "page_size": max(1, min(page_size, 200)), "promoters": rows}


@router.get("/promoters/export")
def invite_promoters_export(
    search: str | None = None,
    sort: str = "total_paid",
    order: str = "desc",
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """推广者汇总 CSV：按当前筛选/排序导出全部（不限本页）。

    列与管理端汇总表一致；金额列为美元数值，单位写在表头里。
    """
    rows = invite_promoter.summary_rows(db, search=search, sort=sort, order=order)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "推广者ID", "用户名", "邀请人数", "付费人数",
        "累计付费（美元）", "返利已入账（美元）", "返利待入账（美元）",
        "返利合计（美元）", "生效费率（%）", "费率来源", "状态", "备注",
    ])
    for r in rows:
        writer.writerow([
            r["user_id"], r["username"], r["invited_count"], r["paid_invitees"],
            r["total_paid"], r["rebate_credited"], r["rebate_pending"],
            r["rebate_total"], r["rate_percent"],
            "专属" if r["rate_source"] == "promoter" else "全局",
            "已停用" if r["is_disabled"] else "正常",
            r["note"],
        ])
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="invite-promoters.csv"'},
    )


class PromoterProfileUpdate(BaseModel):
    # 全量保存口径：三个字段即档案全貌；覆盖为 None = 清空、走全局
    rebate_percent_override: int | None = None
    is_disabled: bool = False
    note: str = ""


@router.put("/promoters/{user_id}")
def update_invite_promoter(
    user_id: int,
    payload: PromoterProfileUpdate,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存推广者档案：专属费率覆盖 / 停用 / 备注（upsert，一人一行）。"""
    user = db.query(models.WebUser).filter(models.WebUser.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if (payload.rebate_percent_override is not None
            and not 0 <= payload.rebate_percent_override <= 100):
        raise HTTPException(status_code=400, detail="专属返利比例须在 0–100 之间")
    note = (payload.note or "").strip()
    if len(note) > 1000:
        raise HTTPException(status_code=400, detail="备注过长（上限 1000 字）")

    profile = invite_promoter.get_profile(db, user_id)
    if profile is None:
        profile = models.InvitePromoterProfile(user_id=user_id)
        db.add(profile)
    profile.rebate_percent_override = payload.rebate_percent_override
    profile.is_disabled = bool(payload.is_disabled)
    profile.note = note
    profile.updated_by = current_admin.id
    db.flush()
    _audit(db, current_admin, "invite_promoter_update", "invite_promoter",
           user_id, {
               "rebate_percent_override": payload.rebate_percent_override,
               "is_disabled": bool(payload.is_disabled),
               "note": note,
           })
    db.commit()
    dto = next(
        (r for r in invite_promoter.summary_rows(db) if r["user_id"] == user_id),
        None,
    )
    return {"success": True, "promoter": dto}


class TrialPolicyUpdate(BaseModel):
    invite_trial_enabled: bool | None = None
    invite_trial_days: int | None = None
    invite_welfare_monthly_limit: int | None = None
    invite_welfare_min_paid_fen: int | None = None
    invite_trial_plan_id: int | None = None


@router.get("/trial-codes")
def invite_trial_codes(
    limit: int = 100,
    status: str | None = None,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """福利试用码只读列表（最新在前）：生成人/码/状态/使用者/时间全貌。

    状态按下发口径（惰性过期）过滤与展示：pending 且已过期的按
    expired 算。本端点只读，不提供作废/补发等处置动作（口径未定）。
    """
    q = db.query(models.InviteTrialCode)
    rows_all = q.order_by(models.InviteTrialCode.id.desc()).limit(500).all()
    user_ids = {r.creator_id for r in rows_all} | {
        r.used_by for r in rows_all if r.used_by}
    users = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_(user_ids)).all()
    } if user_ids else {}
    items = []
    for r in rows_all:
        dto = invite_trial.trial_code_dto(
            r, creator_name=users.get(r.creator_id, "未知"),
            claimer_name=users.get(r.used_by) if r.used_by else None)
        if status in (invite_trial.STATUS_PENDING, invite_trial.STATUS_USED,
                      invite_trial.STATUS_EXPIRED) and dto["status"] != status:
            continue
        items.append(dto)
        if len(items) >= min(max(limit, 1), 200):
            break
    return {"total": len(items), "codes": items}


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
