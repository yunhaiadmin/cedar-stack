"""
新人指南页 · 服务器地址下发（2026-11-27 用户亲点）。

指南页（/game/guide）里的服务器地址原先写死在前端页面里——前端包
人人能下载，页面层 v-if 隐藏（#540）挡不住翻代码。用户亲点改严：
地址改由本端点下发，没资格连数据都拿不到。

口径：
- 须登录（get_current_user），未登录 401；
- 资格判定复用播放门禁单点 ``emby_server.playback_gate.has_play_grant``
  （有效订阅或团队播放资格，家庭子账号随主账号解析，无 staff 豁免），
  与真实播放同一把尺，不许另判一套；
- 无资格：只回 ``{"visible": false}``，不带地址字段，响应里绝不
  出现地址字符串；
- 有资格：回 ``{"visible": true, "server_address": <地址>}``。

地址取值（本端点不写死对外地址，顺序）：
1. ``EMBY_PUBLIC_URL``——emby_server.public_url.configured_public_url，
   全站交给客户端的绝对地址单一来源（生产已配为 CDN 地址）；
2. 系统配置键 ``client_server_address``（config_schema 家族 guide，
   管理端可配）；
3. 兜底 DEFAULT_SERVER_ADDRESS（与 install_app.SERVER_ADDRESS 同一
   CDN 口径，仅作配置全缺时的最后默认值）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend import models
from backend.api.user import get_current_user
from backend.database import get_db
from backend.emby_server.public_url import configured_public_url
from backend.integrations import store

logger = logging.getLogger(__name__)

#: 系统配置键：对外服务器地址（EMBY_PUBLIC_URL 未配时生效）。
CONFIG_KEY_SERVER_ADDRESS = "client_server_address"

#: 配置全缺时的兜底地址（CDN 口径，与 install_app.SERVER_ADDRESS 一致）。
DEFAULT_SERVER_ADDRESS = "https://cdn.aetrix.love"


def server_address(db: Session) -> str:
    """当前生效的对外服务器地址（取值顺序见模块头）。"""
    public = configured_public_url()
    if public:
        return public
    value = (store.get_value(
        db, CONFIG_KEY_SERVER_ADDRESS, DEFAULT_SERVER_ADDRESS)
        or "").strip().rstrip("/")
    return value or DEFAULT_SERVER_ADDRESS


def guide_server_address_payload(db: Session, user: models.WebUser) -> dict:
    """下发判定：有播放资格才带地址，无资格响应里绝不出现地址。"""
    # 懒引：playback_gate 在 emby_server 侧，避免顶层互相牵连
    from backend.emby_server.playback_gate import has_play_grant

    if not has_play_grant(db, user):
        return {"visible": False}
    return {"visible": True, "server_address": server_address(db)}


user_guide_router = APIRouter(prefix="/api/user", tags=["新人指南"])


@user_guide_router.get("/guide/server-address")
def guide_server_address(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新人指南页服务器地址：登录 + 播放资格才下发地址本体。"""
    return guide_server_address_payload(db, current_user)
