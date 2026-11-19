# -*- coding: utf-8 -*-
"""管理端 · 官网落地页文案编辑（运营参数）。

官网落地页（未登录首页）的首屏文案、合作方名单、设备区、展示区
与卖点三卡文本存 SystemConfig 单键 landing_content（家族
landing，整份 JSON 结构校验），本模块只做一件事：把管理员编辑
的整份结构经 config_service 校验落库、读回。保存后用户端经
公开端点 /api/site/landing 实时读取（直查口径，无缓存延迟）。
读写形状与校验细节见 backend/integrations/landing_content.py
与 config_schema 的 coerce_landing_content。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import landing_content

admin_landing_router = APIRouter(
    prefix="/api/admin/landing", tags=["管理-官网内容"])


class LandingSpec(BaseModel):
    value: str = ""
    sub: str = ""


class LandingHero(BaseModel):
    title_lines: list[str] = Field(default_factory=list)
    subs: list[str] = Field(default_factory=list)
    scroll_hint: str = ""
    specs: list[LandingSpec] = Field(default_factory=list)


class LandingPartners(BaseModel):
    eyebrow: str = ""
    names: list[str] = Field(default_factory=list)


class LandingDevices(BaseModel):
    eyebrow: str = ""
    title_lines: list[str] = Field(default_factory=list)
    sub: str = ""
    marquee_clients: list[str] = Field(default_factory=list)
    marquee_specs: list[str] = Field(default_factory=list)


class LandingShowcaseBlock(BaseModel):
    eyebrow: str = ""
    title: str = ""
    sub: str = ""


class LandingShowcase(BaseModel):
    cinema: LandingShowcaseBlock = Field(default_factory=LandingShowcaseBlock)
    audio: LandingShowcaseBlock = Field(default_factory=LandingShowcaseBlock)


class LandingPoint(BaseModel):
    title: str = ""
    text: str = ""


class LandingCard(BaseModel):
    eyebrow: str = ""
    title_lines: list[str] = Field(default_factory=list)
    desc: str = ""
    chips: list[str] = Field(default_factory=list)
    points: list[LandingPoint] = Field(default_factory=list)


class LandingEssentials(BaseModel):
    eyebrow: str = ""
    title: str = ""
    sub: str = ""
    cards: list[LandingCard] = Field(default_factory=list)


class LandingContentRequest(BaseModel):
    hero: LandingHero = Field(default_factory=LandingHero)
    partners: LandingPartners = Field(default_factory=LandingPartners)
    devices: LandingDevices = Field(default_factory=LandingDevices)
    showcase: LandingShowcase = Field(default_factory=LandingShowcase)
    essentials: LandingEssentials = Field(default_factory=LandingEssentials)


def _response(db: Session) -> dict:
    return {
        "config": landing_content.read_config(db),
        "has_custom": landing_content.has_custom(db),
    }


@admin_landing_router.get("/content")
def get_landing_content(
    db: Session = Depends(get_db),
    _admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """当前整份落地页文案（未存过时返回默认 = 现行写死文案，
    管理员看到的就是访客将看到的）。"""
    return _response(db)


@admin_landing_router.put("/content")
def update_landing_content(
    payload: LandingContentRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """整份保存：结构非法（标题/副文案为空、卖点卡不是 3 张、
    超长/超量）→ 400 且不落库；合法则落库并回读实际生效的结构。"""
    try:
        config_service.save_config(
            db,
            {landing_content.KEY: payload.model_dump()},
            schema="landing",
        )
    except ConfigValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(
        db, current_admin, "landing_content_update", "system_config",
        details={
            "subs": len(payload.hero.subs),
            "partners": len(payload.partners.names),
            "cards": len(payload.essentials.cards),
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return _response(db)


@admin_landing_router.delete("/content")
def reset_landing_content(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """恢复默认文案：删除自定义行，官网立即回落现行写死文案
    （与 #391 隐私政策「清空 = 删行回落」同一口径）。"""
    try:
        config_service.save_config(
            db,
            {landing_content.KEY: ""},
            schema="landing",
        )
    except ConfigValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(
        db, current_admin, "landing_content_reset", "system_config",
        details={},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return _response(db)
