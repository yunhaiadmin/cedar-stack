# -*- coding: utf-8 -*-
"""用户端钱包 API（钱包批 1：只入账，不扣款）。

- GET  /api/user/wallet：当前余额 + 钱包开关 + 最近流水（第一页）。
- GET  /api/user/wallet/ledger：自己的流水分页（可按 kind 筛选）。
- POST /api/user/wallet/redeem：余额兑换码入账——只认
  reward_type=balance 的卡码，核销主体复用 codes.redeem_code
  （claim_code 原子占位 + 蜜罐 + 指名 + 每人每码一次），非余额码
  在占位前分流拒绝、不烧码。

批 1 不提供任何扣款/充值端点：订阅购买、购流量、在线充值分别是
批 2 / 批 2 / 批 3，不许在这里假接。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import codes, models
from backend import wallet as wallet_service
from backend.api.user import get_current_user
from backend.database import get_db
from backend.db_retry import commit_with_retry
from backend.ratelimit import check_rate_limit

wallet_router = APIRouter(prefix="/api/user", tags=["用户端·钱包"])


@wallet_router.get("/wallet")
def get_wallet(current_user: models.WebUser = Depends(get_current_user),
               db: Session = Depends(get_db)):
    balance = wallet_service.get_balance(db, current_user.id)
    rows, total = wallet_service.ledger_query(
        db, user_id=current_user.id, page=1, page_size=10)
    return {
        "balance": f"{balance:.2f}",
        "wallet_enabled": wallet_service.wallet_enabled(db),
        "ledger": [wallet_service.ledger_dict(r) for r in rows],
        "ledger_total": total,
    }


@wallet_router.get("/wallet/ledger")
def get_wallet_ledger(kind: str = "", page: int = 1, page_size: int = 20,
                      current_user: models.WebUser = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rows, total = wallet_service.ledger_query(
        db, user_id=current_user.id, kind=kind or None,
        page=page, page_size=page_size)
    return {
        "items": [wallet_service.ledger_dict(r) for r in rows],
        "total": total,
        "page": max(int(page or 1), 1),
        "page_size": min(max(int(page_size or 20), 1), 100),
    }


class WalletRedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


@wallet_router.post("/wallet/redeem")
def redeem_wallet_code(req: WalletRedeemRequest,
                       current_user: models.WebUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """余额码兑换：只成交 reward_type=balance 的码，其余不烧码分流。"""
    allowed, _ = check_rate_limit(f"wallet_redeem:{current_user.id}", 10, 60)
    if not allowed:
        invalid = codes.classify_invalid_code(db, current_user, req.code)
        if invalid is not None:
            return JSONResponse(
                status_code=400,
                content={"detail": invalid["message"],
                         "error_code": invalid["error_code"]})
        return JSONResponse(status_code=429,
                            content={"detail": "操作过于频繁，请稍后再试"})

    if not current_user.is_active:
        return JSONResponse(status_code=403,
                            content={"detail": "账号已被禁用，请联系管理员"})

    if not wallet_service.wallet_enabled(db):
        return JSONResponse(status_code=403,
                            content={"detail": "钱包功能暂未开启"})

    # 分流：找得到且不是蜜罐的码，reward_type 不是 balance 一律不核销
    # （不占位、不烧码），提示去对应入口；蜜罐与不存在交给 redeem_code
    # 的既有口径处理（蜜罐必须先触发陷阱，不能被分流短路）。
    found = codes.find_reg_code(db, req.code.strip())
    if found is not None and not codes.is_honeypot(found):
        reward_type = getattr(found, "reward_type", "subscription") \
            or "subscription"
        if reward_type != codes.REWARD_BALANCE:
            return JSONResponse(
                status_code=400,
                content={"detail": "这张卡码不是余额码，请到会员页兑换",
                         "error_code": "not_balance_code"})

    result = codes.redeem_code(db, current_user, req.code)
    if not result.get("success"):
        db.rollback()
        content = {"detail": result.get("message") or "卡码无效"}
        if result.get("error_code"):
            content["error_code"] = result["error_code"]
        return JSONResponse(status_code=400, content=content)
    # 占位与钱包入账由这里统一提交（与会员核销同一口径）
    commit_with_retry(db, label="钱包兑换码入账")
    return {
        "ok": True,
        "message": result.get("message"),
        "balance_value": result.get("balance_value"),
        "balance": result.get("balance_after"),
    }
