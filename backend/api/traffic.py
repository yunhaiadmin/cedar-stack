# -*- coding: utf-8 -*-
"""用户端流量额度 API（流量额度一期）：概览流量区 + 购流量。

- GET  /api/user/traffic：当月额度/已用/抵扣/重置日 + 时间换流量
  选项。额度未启用（套餐没声明、全局默认也没配）时 enabled=false，
  前端整段不展示。
- POST /api/user/traffic/exchange：时间换流量成交（扣订阅天数换
  抵扣额度）。客户端带 idempotency_key，同一次确认的重试/双击
  只成交一次（服务层幂等，见 backend/traffic_quota.py）。

余额购买不在本期：钱包（余额）功能尚未建，前端页签置灰说明，
这里**不提供**任何余额支付端点，不许假接。
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
    days: int = Field(..., ge=1, le=365)
    idempotency_key: str = Field(..., min_length=1, max_length=64)


@traffic_router.get("/traffic")
def get_traffic(current_user: models.WebUser = Depends(get_current_user),
                db: Session = Depends(get_db)):
    summary = traffic_quota.usage_summary(db, current_user.id)
    return {
        **summary,
        "exchange_options": traffic_quota.exchange_options(db),
        # 余额购买依赖钱包（未建）：只给状态，不给端点。
        "balance_purchase_available": False,
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
            db, current_user.id, req.days, req.idempotency_key)
    except traffic_quota.TrafficError as exc:
        return JSONResponse(status_code=400,
                            content={"detail": str(exc), "code": exc.code})
    return {
        "ok": True,
        "replayed": result["replayed"],
        "log": result["log"],
        "traffic": result["summary"],
    }
