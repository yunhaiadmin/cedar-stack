# -*- coding: utf-8 -*-
"""管理后台 API · 钱包（钱包批 1）。

端点挂在共用 admin_router 上（前缀 /api/admin），分组路径
``/economy/wallet/*``：

- GET  /economy/wallet/balance?user_id=：查某用户余额 + 最近流水
- POST /economy/wallet/adjust：手工调账（金额带符号、原因必填，
  记 operator_id，写流水行；扣减走条件 UPDATE，余额不足整笔拒绝）
- GET  /economy/wallet/ledger：全站流水（按用户 / kind / 时间筛选）
- GET/POST /economy/wallet/settings：钱包 8 个配置键读写
  （键已登记进 ECONOMY_CONFIG_SCHEMA + ECONOMY_CONFIG_KEYS，
  落库走 config_service 统一通道，与经济设置页同一套校验）

这里的端点一律同步 ``def``（与 admin_economy 同口径：同步 SQLAlchemy
写进 async 端点会占住事件循环）。
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models
from backend import wallet as wallet_service
from backend.api.admin_core import _audit, admin_router, get_current_admin
from backend.database import get_db


def _user_or_404(db: Session, user_id: int) -> models.WebUser:
    user = db.query(models.WebUser).filter(
        models.WebUser.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@admin_router.get("/economy/wallet/balance")
def wallet_admin_balance(
    user_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """查某用户钱包余额 + 最近 20 条流水。"""
    user = _user_or_404(db, user_id)
    rows, total = wallet_service.ledger_query(
        db, user_id=user_id, page=1, page_size=20)
    return {
        "user_id": user.id,
        "username": user.username,
        "balance": f"{wallet_service.get_balance(db, user_id):.2f}",
        "ledger": [wallet_service.ledger_dict(r) for r in rows],
        "ledger_total": total,
    }


class WalletAdjustRequest(BaseModel):
    user_id: int
    # 金额（元，带符号）：正数增、负数减；用字符串收，避免 JSON float
    # 在钱的路径上丢精度，服务端按 Decimal 解析并归一到分。
    amount: str = Field(..., min_length=1, max_length=32)
    reason: str = Field(..., min_length=1, max_length=255)


@admin_router.post("/economy/wallet/adjust")
def wallet_admin_adjust(
    request: WalletAdjustRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """管理员手工调账：金额/原因必填，记 operator_id，落审计。"""
    _user_or_404(db, request.user_id)
    try:
        amount = Decimal(str(request.amount).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="金额格式不正确")
    try:
        entry, _replayed = wallet_service.admin_adjust(
            db, request.user_id, amount,
            note=request.reason, operator_id=current_admin.id)
    except wallet_service.WalletError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(db, current_admin, "wallet_adjust", "web_user", request.user_id,
           {"amount": f"{amount:.2f}", "reason": request.reason.strip(),
            "balance_after": f"{entry.balance_after:.2f}"})
    db.commit()
    return {
        "ok": True,
        "user_id": request.user_id,
        "amount": f"{amount:.2f}",
        "balance": f"{entry.balance_after:.2f}",
        "ledger_id": entry.id,
    }


@admin_router.get("/economy/wallet/ledger")
def wallet_admin_ledger(
    user_id: Optional[int] = None,
    kind: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    page_size: int = 20,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """全站钱包流水查询（时间筛选为 ISO 字符串，可空）。"""
    q = db.query(models.WalletLedger)
    if user_id is not None:
        q = q.filter(models.WalletLedger.user_id == user_id)
    if kind:
        q = q.filter(models.WalletLedger.kind == kind)
    for raw, is_start in ((start, True), (end, False)):
        if raw:
            try:
                ts = datetime.fromisoformat(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="时间格式不正确")
            if is_start:
                q = q.filter(models.WalletLedger.created_at >= ts)
            else:
                q = q.filter(models.WalletLedger.created_at <= ts)
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    total = q.count()
    rows = (q.order_by(models.WalletLedger.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    usernames = {}
    ids = {r.user_id for r in rows} | {r.operator_id for r in rows
                                       if r.operator_id}
    if ids:
        usernames = {u.id: u.username for u in db.query(models.WebUser)
                     .filter(models.WebUser.id.in_(ids)).all()}
    items = []
    for r in rows:
        item = wallet_service.ledger_dict(r)
        item["username"] = usernames.get(r.user_id)
        item["operator_name"] = usernames.get(r.operator_id) \
            if r.operator_id else None
        items.append(item)
    return {"items": items, "total": total, "page": page,
            "page_size": page_size}


# ---------- 钱包配置（11 键，默认值见 wallet.WALLET_CONFIG_DEFAULTS） ----------

@admin_router.get("/economy/wallet/settings")
def wallet_admin_get_settings(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    config = wallet_service.wallet_config(db)
    return {"settings": {
        key: ("true" if value else "false") if isinstance(value, bool)
        else str(value)
        for key, value in config.items()
    }}


class WalletSettingsRequest(BaseModel):
    settings: dict


@admin_router.post("/economy/wallet/settings")
def wallet_admin_update_settings(
    request: WalletSettingsRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """保存钱包配置：只认 11 个钱包键，未知键 400（不静默吞，防写错键名）。"""
    from backend import config_service

    allowed = set(wallet_service.WALLET_CONFIG_DEFAULTS)
    values = {}
    for key, value in request.settings.items():
        if key not in allowed:
            raise HTTPException(status_code=400, detail=f"未知钱包配置键：{key}")
        values[key] = value
    if values:
        # 校验失败抛 ConfigValueError，由 main.py 的统一 handler 转 400
        config_service.save_config(db, values, schema="economy")
    db.commit()
    _audit(db, current_admin, "wallet_update_settings", "system", None,
           {k: str(v) for k, v in values.items()})
    db.commit()
    return {"ok": True, "saved": sorted(values)}


# ---------- 返利撤回欠账（钱包时代，钱包批 2） ----------

@admin_router.get("/economy/wallet/rebate-reversals")
def wallet_rebate_reversals(
    status: str = "",
    limit: int = 50,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """钱包时代返利撤回欠账：订单退款时邀请人余额不足、返利没扣回来
    的计提行（status=credited 且 reversal_owed=True），以及已扣回
    的（status=reversed）。与积分时代的 RebateReversalDebt 分开记账、
    不混账；金额是人民币元（Decimal），不在 Integer 的旧表里存分。
    """
    from backend import invite_rebate as _invite_rebate

    limit = max(1, min(int(limit or 50), 200))
    q = db.query(models.InviteRebateAccrual)
    if status == "owed":
        q = q.filter(models.InviteRebateAccrual.reversal_owed == True)  # noqa: E712
    elif status == "reversed":
        q = q.filter(models.InviteRebateAccrual.status ==
                     _invite_rebate.STATUS_REVERSED)
    else:
        q = q.filter(
            (models.InviteRebateAccrual.reversal_owed == True)  # noqa: E712
            | (models.InviteRebateAccrual.status ==
               _invite_rebate.STATUS_REVERSED))
    rows = q.order_by(models.InviteRebateAccrual.id.desc()).limit(limit).all()
    user_ids = ({r.inviter_id for r in rows} | {r.invitee_id for r in rows})
    users = {u.id: u.username for u in db.query(models.WebUser).filter(
        models.WebUser.id.in_(user_ids)).all()} if user_ids else {}
    return {"records": [
        {
            "id": r.id,
            "order_id": r.source_ref,
            "inviter": users.get(r.inviter_id, f"用户 {r.inviter_id}"),
            "invitee": users.get(r.invitee_id, f"用户 {r.invitee_id}"),
            "rebate_amount": f"{r.rebate_amount:.2f}",
            "status": r.status,
            "reversal_owed": bool(r.reversal_owed),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "credited_at": r.credited_at.isoformat() if r.credited_at else None,
            "reversed_at": r.reversed_at.isoformat() if r.reversed_at else None,
        }
        for r in rows
    ]}


class RebateReversalRetryRequest(BaseModel):
    # 邀请人余额不足时是否允许扣成负数（与退款对话框的 allow_negative 同口径）
    allow_negative: bool = False


@admin_router.post("/economy/wallet/rebate-reversals/{accrual_id}/retry")
def wallet_retry_rebate_reversal(
    accrual_id: int,
    request: RebateReversalRetryRequest,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """重试一条钱包时代返利撤回欠账：再扣一次邀请人钱包返利。

    行锁认领（防并发双重试；崩溃回滚后仍是 owed，不留中间态）。
    成功则计提行转 reversed、清 owed 标记；余额仍不足则保持 owed
    并如实返回原因（不是报错）。
    """
    from datetime import datetime as _dt

    from backend import invite_rebate as _invite_rebate

    accrual = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.id == accrual_id,
                models.InviteRebateAccrual.reversal_owed == True)  # noqa: E712
        .with_for_update()
        .first()
    )
    if accrual is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="欠账不存在或已处理")
    try:
        _entry, _replayed = wallet_service.debit(
            db, accrual.inviter_id, accrual.rebate_amount,
            kind="rebate_reversal", ref_id=f"rebate:{accrual.source_ref}",
            note=f"欠账重试撤回返利（订单 {accrual.source_ref}）",
            idempotency_key=f"rebate-reversal:{accrual.source_ref}",
            allow_negative=request.allow_negative)
    except wallet_service.WalletError as exc:
        db.rollback()
        return {"success": False, "accrual_id": accrual_id,
                "status": "owed", "reason": str(exc)}
    accrual.status = _invite_rebate.STATUS_REVERSED
    accrual.reversal_owed = False
    accrual.reversed_at = _dt.now()
    _audit(db, current_admin, "wallet_retry_rebate_reversal",
           "invite_rebate_accrual", accrual_id,
           {"status": "reversed",
            "amount": f"{accrual.rebate_amount:.2f}",
            "allow_negative": request.allow_negative})
    db.commit()
    return {"success": True, "accrual_id": accrual_id,
            "status": "reversed",
            "reversed_amount": f"{accrual.rebate_amount:.2f}"}


@admin_router.post("/economy/wallet/rebate-reversals/{accrual_id}/waive")
def wallet_waive_rebate_reversal(
    accrual_id: int,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """豁免一条钱包时代返利撤回欠账（owed → 清标记，钱留在邀请人钱包）。"""
    accrual = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.id == accrual_id,
                models.InviteRebateAccrual.reversal_owed == True)  # noqa: E712
        .with_for_update()
        .first()
    )
    if accrual is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="欠账不存在或已处理")
    accrual.reversal_owed = False
    _audit(db, current_admin, "wallet_waive_rebate_reversal",
           "invite_rebate_accrual", accrual_id,
           {"status": "waived",
            "amount": f"{accrual.rebate_amount:.2f}"})
    db.commit()
    return {"success": True, "accrual_id": accrual_id, "status": "waived"}
