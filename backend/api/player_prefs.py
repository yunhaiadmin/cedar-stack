"""网页播放器设置同步 · 用户端接口（⚠️11，2026-11-15）。

- GET /api/user/player-preferences
  回当前用户保存的播放器设置：{"preferences": {...}}；
  从未保存过回 {"preferences": null}。
- PUT /api/user/player-preferences
  整对象覆盖保存：请求体必须是 JSON object、序列化后 ≤16KB，
  非 object / 坏 JSON / 超限一律 400 且不落库。

须登录；子账号按本人存（个人偏好、非经济动作，不走主账号
归属）。核心存取在 backend/player_prefs.py（唯一实现）。
Emby 兼容口 DisplayPreferences 空壳不在本单范围、不动。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import player_prefs as prefs_core
from backend.api.user import get_current_user
from backend.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/player-preferences", tags=["player-prefs"])


@router.get("")
def fetch_player_preferences(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"preferences": prefs_core.get_player_preferences(db, user.id)}


@router.put("")
async def save_player_preferences(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = await request.body()
    if len(raw) > prefs_core.MAX_PREFS_BYTES:
        raise HTTPException(status_code=400, detail="播放器设置过大")
    try:
        payload = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="播放器设置不是合法 JSON") from None
    try:
        saved = prefs_core.set_player_preferences(db, user.id, payload)
    except prefs_core.PlayerPrefsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"preferences": saved}
