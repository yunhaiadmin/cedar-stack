"""管理后台 · 只读观测口（专线 B 第一阶段：补口）。

本单新建以下 GET 口：

- ``GET /api/admin/storage-bindings``：全站绑定列表（按用户 / 状态查）。

全部纯只读：无 POST / PUT / DELETE；凭据类字段只出 ``has_credential``
布尔值，``credential_id`` 与密封值永不进响应（绑定口复用
``user_bindings.serialize_binding`` 的脱敏口径）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.admin_core import get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import user_bindings

router = APIRouter(prefix="/api/admin", tags=["管理后台·只读观测口"])


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/storage-bindings")
def list_storage_bindings(
    user_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """全站绑定列表（管理端）：按用户 / 状态过滤，id 倒序。

    凭据口径：每行复用 ``serialize_binding``（不出凭据原文与密封值），
    仅额外加 ``user_id``（管理端需知归属，credential_id 仍不给）。
    """
    limit, offset = pagination_params(limit, offset)
    q = db.query(em.UserStorageBinding)
    if user_id is not None:
        q = q.filter(em.UserStorageBinding.user_id == user_id)
    if status is not None:
        q = q.filter(em.UserStorageBinding.status == status)
    total = q.count()
    rows = (
        q.order_by(em.UserStorageBinding.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    items = []
    for row in rows:
        payload = user_bindings.serialize_binding(row)
        payload["user_id"] = row.user_id
        items.append(payload)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
