"""管理后台 · 只读观测口（专线 B 第一阶段：补口）。

本单新建 5 个 GET 口（总线快照口 + 命令列表口已在
``backend/emby_server/playback_bus_admin.py`` 存在——
``GET /api/admin/playback-bus/snapshot`` 包 ``read_snapshot``、
``GET /api/admin/playback-bus/commands`` 包 ``list_commands``，
本单不重复建、不改现有路由行为）：

- ``GET /api/admin/storage-bindings``：全站绑定列表（按用户 / 状态查）。
- ``GET /api/admin/providers/sidecar``：sidecar 运行时状态（p115 / yun139）。
- ``GET /api/admin/yun139/pools``：139 账号池只读列表（含成员）。

全部纯只读：无 POST / PUT / DELETE；凭据类字段只出 ``has_credential``
布尔值，``credential_id`` 与密封值永不进响应（绑定口复用
``user_bindings.serialize_binding`` 的脱敏口径）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.admin_core import get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import provider_runtime as prt
from backend.emby_server import user_bindings

router = APIRouter(prefix="/api/admin", tags=["管理后台·只读观测口"])

#: sidecar 状态口覆盖的 provider（与部署形态一致：115 与 139 各一个
#: UDS 运行时，路径见 ``provider_runtime.default_socket_path``）。
KNOWN_SIDECAR_PROVIDERS = ("p115", "yun139")


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


def _sidecar_status(provider: str) -> dict:
    """单个 sidecar 的运行态：manifest + health，失败只记 error、不抛 500。"""
    client = prt.ProviderRuntimeClient(provider)
    result = {
        "provider": provider,
        "socket_path": client.socket_path,
        "available": False,
        "manifest": None,
        "health": None,
        "error": None,
    }
    try:
        result["manifest"] = client.manifest()
    except prt.ProviderRuntimeError as exc:
        result["error"] = exc.as_error_dict()
        return result
    except Exception as exc:  # noqa: BLE001 — 状态口不因侧车异常炸 500
        result["error"] = {"code": "internal", "kind": "internal",
                           "message": str(exc)[:200]}
        return result
    try:
        result["health"] = client.health()
    except prt.ProviderRuntimeError as exc:
        result["error"] = exc.as_error_dict()
        return result
    except Exception as exc:  # noqa: BLE001 — 同上
        result["error"] = {"code": "internal", "kind": "internal",
                           "message": str(exc)[:200]}
        return result
    result["available"] = True
    return result


@router.get("/providers/sidecar")
def sidecar_status(
    provider: str | None = None,
    current_admin=Depends(get_current_admin),
):
    """sidecar 运行时状态：不传 provider 查 p115 + yun139 两个；传了只查该个。

    未知 provider 回 400；侧车没起来时该项 available=False + error，
    整口仍 200（状态口不因一端没起来炸掉整页）。
    """
    if provider is not None and provider not in KNOWN_SIDECAR_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"未知 sidecar provider：{provider!r}，可选："
                   f"{', '.join(KNOWN_SIDECAR_PROVIDERS)}",
        )
    names = (provider,) if provider is not None else KNOWN_SIDECAR_PROVIDERS
    return {"providers": [_sidecar_status(name) for name in names]}


@router.get("/yun139/pools")
def list_yun139_pools(
    limit: int = 50,
    offset: int = 0,
    current_admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """139 账号池只读列表（含成员；成员只出 has_credential，credential_id 不给）。"""
    limit, offset = pagination_params(limit, offset)
    q = db.query(em.Yun139AccountPool)
    total = q.count()
    pools = (
        q.order_by(em.Yun139AccountPool.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    items = []
    for pool in pools:
        members = (
            db.query(em.Yun139AccountPoolMember)
            .filter(em.Yun139AccountPoolMember.pool_id == pool.id)
            .order_by(em.Yun139AccountPoolMember.id)
            .all()
        )
        items.append({
            "id": pool.id,
            "name": pool.name,
            "strategy": pool.strategy,
            "cooldown_seconds": pool.cooldown_seconds,
            "max_concurrent": pool.max_concurrent,
            "created_at": _iso(pool.created_at),
            "member_count": len(members),
            "members": [{
                "id": m.id,
                "label": m.label,
                "weight": m.weight,
                "fail_count": m.fail_count,
                "cooldown_until": _iso(m.cooldown_until),
                "has_credential": m.credential_id is not None,
                "created_at": _iso(m.created_at),
            } for m in members],
        })
    return {"items": items, "total": total, "limit": limit, "offset": offset}
