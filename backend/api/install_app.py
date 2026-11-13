"""
安装应用页（用户中心「帮助」组，2026-11-13 用户亲点新建）。

页面三块：服务器地址卡（对外客户端地址只有一个口径，见 SERVER_ADDRESS）、
6 平台官方客户端下载、按设备分步教程（教程文案在前端，本文件只管数据）。

下载链接是系统配置项（家族 install_app，config_schema 登记）：管理端可配，
页面只读配置渲染，前后端都不写死任何下载地址。本文件是唯一的平台表来源：

- 没有代码级默认链接：某平台未配链接 = 用户端 payload 不下发该平台、
  页面整行不显示，不许编链接凑数、不许渲染假按钮；
- 管理端把某键清空保存 = 删行取消配置（与全站配置空值语义一致）。

服务器地址红线：对外只公布 CDN 地址 cdn.aetrix.love（橙云藏源）；
源站地址与任何源 IP 不许出现在这里或前端页面里。
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config_service, models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.user import get_current_user
from backend.config_schema import ConfigValueError
from backend.database import get_db
from backend.integrations import store

logger = logging.getLogger(__name__)

#: 对外公布的客户端服务器地址（唯一口径：CDN 地址，藏源）。
SERVER_ADDRESS = "https://cdn.aetrix.love"

#: 平台表：key（API 用）/ label（平台名）/ devices（设备说明）/
#: store（获取渠道）/ config_key（配置键）。顺序即页面展示顺序。
PLATFORMS: list[dict] = [
    {
        "key": "ios",
        "label": "iOS",
        "devices": "iPhone / iPad",
        "store": "App Store",
        "config_key": "install_app_ios_url",
    },
    {
        "key": "android",
        "label": "Android",
        "devices": "手机 / 平板",
        "store": "Google Play",
        "config_key": "install_app_android_url",
    },
    {
        "key": "android_tv",
        "label": "Android TV",
        "devices": "电视 / 电视盒子",
        "store": "Google Play",
        "config_key": "install_app_android_tv_url",
    },
    {
        "key": "apple_tv",
        "label": "Apple TV",
        "devices": "Apple TV 盒子",
        "store": "App Store",
        "config_key": "install_app_apple_tv_url",
    },
    {
        "key": "windows",
        "label": "Windows",
        "devices": "Windows 电脑",
        "store": "Emby 官网",
        "config_key": "install_app_windows_url",
    },
    {
        "key": "macos",
        "label": "macOS",
        "devices": "Mac 电脑",
        "store": "Emby 官网",
        "config_key": "install_app_macos_url",
    },
]

_BY_KEY = {p["key"]: p for p in PLATFORMS}

_DESCRIPTIONS = {
    p["config_key"]: f"安装应用页 · {p['label']} 客户端下载链接"
    for p in PLATFORMS
}


def _effective_urls(db: Session) -> dict[str, str]:
    """各平台当前生效链接：只认配置行，无行 = 未配（空串）。"""
    keys = [p["config_key"] for p in PLATFORMS]
    defaults = {p["config_key"]: "" for p in PLATFORMS}
    values = store.get_values(db, keys, defaults)
    return {p["key"]: (values.get(p["config_key"]) or "") for p in PLATFORMS}


def user_payload(db: Session) -> dict:
    """用户端页面数据：服务器地址 + 已配链接的平台（未配整行不下发）。"""
    urls = _effective_urls(db)
    return {
        "server_address": SERVER_ADDRESS,
        "platforms": [
            {
                "key": p["key"],
                "label": p["label"],
                "devices": p["devices"],
                "store": p["store"],
                "url": urls[p["key"]],
            }
            for p in PLATFORMS
            if urls[p["key"]]
        ],
    }


def admin_payload(db: Session) -> dict:
    """管理端编辑数据：全平台列表 + 生效值 + 是否已配（供界面提示）。"""
    urls = _effective_urls(db)
    stored = {
        row.key
        for row in db.query(models.SystemConfig)
        .filter(models.SystemConfig.key.in_([p["config_key"] for p in PLATFORMS]))
        .all()
    }
    return {
        "platforms": [
            {
                "key": p["key"],
                "label": p["label"],
                "devices": p["devices"],
                "url": urls[p["key"]],
                "configured": p["config_key"] in stored,
            }
            for p in PLATFORMS
        ]
    }


def _validate_url(key: str, value: str) -> str:
    """链接形状校验（端点层）：只收 http(s) 链接，空串 = 取消配置。"""
    v = value.strip()
    if not v:
        return ""
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"{key} 的下载链接必须是 http(s) 链接",
        )
    return v


# ==================== 用户端 ====================

user_install_app_router = APIRouter(prefix="/api/user", tags=["安装应用"])


@user_install_app_router.get("/install-app")
def install_app_for_user(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """安装应用页数据：服务器地址 + 6 平台下载链接（当前生效值）。"""
    return user_payload(db)


# ==================== 管理端 ====================

admin_install_app_router = APIRouter(
    prefix="/api/admin/install-app", tags=["管理后台·安装应用"])


class InstallAppConfigRequest(BaseModel):
    # 平台 key → 链接；只传要改的键，空串 = 清空取消配置
    urls: dict[str, Optional[str]] = {}


@admin_install_app_router.get("/config")
def install_app_get_config(
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """安装应用下载链接读取：全平台生效值 + 是否已配。"""
    return admin_payload(db)


@admin_install_app_router.put("/config")
def install_app_update_config(
    request: InstallAppConfigRequest,
    raw_request: Request,
    current_admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """安装应用下载链接保存：未知平台/坏链接 400；写时失效，用户端即时生效。"""
    values: dict[str, str] = {}
    for key, raw in (request.urls or {}).items():
        platform = _BY_KEY.get(key)
        if platform is None:
            raise HTTPException(status_code=400, detail=f"未知平台：{key}")
        values[platform["config_key"]] = _validate_url(key, raw or "")
    saved: list[str] = []
    if values:
        try:
            saved = config_service.save_config(
                db, values, schema="install_app", descriptions=_DESCRIPTIONS)
        except ConfigValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    payload = admin_payload(db)
    _audit(db, current_admin, "install_app_update_config", "system", None,
           {"keys": saved},
           ip=raw_request.client.host if raw_request.client else None)
    db.commit()
    return payload
