# -*- coding: utf-8 -*-
"""用户端流量额度 API（流量额度一期）：概览流量区 + 购流量。

- GET  /api/user/traffic：当月额度/已用/抵扣/重置日 + 时间换流量
  包表（配置下发：默认半月包 15 天抵 50%、整月包 30 天抵 100%）。
  额度未启用（套餐没声明、全局默认也没配）时 enabled=false，
  前端整段不展示。
- POST /api/user/traffic/exchange：时间换流量成交（按包扣订阅
  时长换抵扣额度，请求带 package key）。客户端带
  idempotency_key，同一次确认的重试/双击只成交一次（服务层
  幂等，见 backend/traffic_quota.py）。
- POST /api/user/traffic/buy-balance（钱包批 2）：余额购流量
  （按配置单价扣钱包余额换本周期抵扣额度，任意 ≥100 的整数
  GB）。同样带 idempotency_key 幂等。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models, traffic_quota
from backend.api.user import get_current_user
from backend.database import get_db
from backend.ratelimit import check_rate_limit

logger = logging.getLogger(__name__)

traffic_router = APIRouter(prefix="/api/user", tags=["用户端·流量"])


class TimeExchangeRequest(BaseModel):
    # 时间换流量按包计（2026-11-15）：package 是规则下发的包 key
    # （默认 half/full），合法性与天数/比例由服务层按配置校验。
    package: str = Field(..., min_length=1, max_length=32)
    idempotency_key: str = Field(..., min_length=1, max_length=64)


@traffic_router.get("/traffic")
def get_traffic(current_user: models.WebUser = Depends(get_current_user),
                db: Session = Depends(get_db)):
    from backend import wallet as wallet_service

    summary = traffic_quota.usage_summary(db, current_user.id)
    wallet_on = wallet_service.wallet_enabled(db)
    return {
        **summary,
        # 时间换流量规则（包表，配置下发）+ 该用户额度与已抵扣
        "exchange_rule": traffic_quota.exchange_rule_view(
            db, current_user.id),
        # 余额购买（钱包批 2）：钱包开着即放开页签；单价与余额一并下发，
        # 前端按档位现算价格、余额不足时明示差额。
        "balance_purchase_available": wallet_on,
        "balance_price_per_100gb_fen": (
            int(wallet_service.wallet_config(db)
                .get("traffic_balance_price_per_100gb") or 0)
            if wallet_on else 0),
        "wallet_balance": (
            f"{wallet_service.get_balance(db, current_user.id):.2f}"
            if wallet_on else "0.00"),
    }


@traffic_router.post("/traffic/exchange")
def post_traffic_exchange(
        req: TimeExchangeRequest,
        current_user: models.WebUser = Depends(get_current_user),
        db: Session = Depends(get_db)):
    allowed, _ = check_rate_limit(
        f"traffic_exchange:{current_user.id}", 10, 60)
    if not allowed:
        return JSONResponse(status_code=429,
                            content={"detail": "操作太频繁，请稍后再试"})
    try:
        result = traffic_quota.exchange_time_for_traffic(
            db, current_user.id, req.package, req.idempotency_key)
    except traffic_quota.TrafficError as exc:
        return JSONResponse(status_code=400,
                            content={"detail": str(exc), "code": exc.code})
    return {
        "ok": True,
        "replayed": result["replayed"],
        "log": result["log"],
        "traffic": result["summary"],
    }


class BalancePurchaseRequest(BaseModel):
    # 任意 ≥100 的整数 GB（服务层再校验下限）；上限留宽防手误爆量
    gb: int = Field(..., ge=100, le=100000)
    idempotency_key: str = Field(..., min_length=1, max_length=64)


@traffic_router.post("/traffic/buy-balance")
def post_traffic_buy_balance(
        req: BalancePurchaseRequest,
        current_user: models.WebUser = Depends(get_current_user),
        db: Session = Depends(get_db)):
    """余额购流量：按配置单价扣钱包余额，换本周期抵扣额度。

    任意 ≥100 的整数 GB（服务层再校验下限与比例计价）；客户端
    每次确认生成一个新幂等键，重试/双击只成交一次。钱包余额不足
    时服务层条件 UPDATE 拒绝，整笔回滚。
    """
    allowed, _ = check_rate_limit(
        f"traffic_buy_balance:{current_user.id}", 10, 60)
    if not allowed:
        return JSONResponse(status_code=429,
                            content={"detail": "操作太频繁，请稍后再试"})
    try:
        result = traffic_quota.buy_traffic_with_balance(
            db, current_user.id, req.gb, req.idempotency_key)
    except traffic_quota.TrafficError as exc:
        return JSONResponse(status_code=400,
                            content={"detail": str(exc), "code": exc.code})
    return {
        "ok": True,
        "replayed": result["replayed"],
        "log": result["log"],
        "traffic": result["summary"],
        "paid_amount": result["paid_amount"],
        "balance_after": result["balance_after"],
        "gb": result["gb"],
    }
