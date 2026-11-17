# -*- coding: utf-8 -*-
"""管理端 · 官网菜单抽屉配置（运营参数）。

官网落地页移动端菜单抽屉的全部内容（导航项 / 社区渠道卡片 /
分组标题 / 底部按钮）存 SystemConfig 单键 site_drawer_config
（家族 site_drawer，整份 JSON 结构校验），本模块只做一件事：
把管理员编辑的整份结构经 config_service 校验落库、读回。
保存后用户端经公开端点 /api/site/drawer 实时读取（直查口径，
无缓存延迟）。读写形状与校验细节见
backend/integrations/site_drawer.py 与 config_schema 的
coerce_site_drawer_config。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import site_drawer

admin_site_drawer_router = APIRouter(
    prefix="/api/admin/site-drawer", tags=["管理-官网菜单抽屉"])


class DrawerNavItem(BaseModel):
    icon: str = "home"
    label: str = ""
    url: str = ""
    external: bool = False
    enabled: bool = True


class DrawerChannel(BaseModel):
    icon: str = "message"
    title: str = ""
    subtitle: str = ""
    url: str = ""
    enabled: bool = True


class DrawerCta(BaseModel):
    label: str = "进入后台"
    url: str = ""
    enabled: bool = True


class SiteDrawerConfigRequest(BaseModel):
    nav_items: list[DrawerNavItem] = Field(default_factory=list)
    channels_title: str = "社区渠道"
    channels: list[DrawerChannel] = Field(default_factory=list)
    cta: DrawerCta = Field(default_factory=DrawerCta)


def _response(db: Session) -> dict:
    return {
        "config": site_drawer.read_config(db),
        "icon_options": site_drawer.ICON_OPTIONS,
    }


@admin_site_drawer_router.get("/config")
def get_site_drawer_config(
    db: Session = Depends(get_db),
    _admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """当前整份抽屉配置（未存过时返回默认值，含旧渠道回落结果，
    管理员看到的就是访客将看到的）。"""
    return _response(db)


@admin_site_drawer_router.put("/config")
def update_site_drawer_config(
    payload: SiteDrawerConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: models.WebUser = Depends(get_current_admin),
) -> dict:
    """整份保存：结构非法（空标题/未知图标/危险链接/超量）→ 400
    且不落库；合法则落库并回读实际生效的结构。"""
    try:
        config_service.save_config(
            db,
            {site_drawer.KEY: payload.model_dump()},
            schema="site_drawer",
        )
    except ConfigValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(
        db, current_admin, "site_drawer_config_update", "system_config",
        details={
            "nav_items": len(payload.nav_items),
            "channels": len(payload.channels),
            "cta_enabled": payload.cta.enabled,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return _response(db)
