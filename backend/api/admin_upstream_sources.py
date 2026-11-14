"""管理后台 · 上游线路保留配置面（详情线路开关＋专属线路）。

上游源 CRUD 与聚合触发已随上游聚合整族删除；本文件只剩两个
仍在服役的配置面，路由继续挂在 admin_emby_router 上
（/api/admin/emby/*），鉴权走 require_staff，写操作审计由
emby_server/audit.py 中间件统一记录：

- 详情页上游线路开关（/upstream-detail-line-config）：读写唯一
  实现在 emby_server/upstream_playback.detail_line_enabled。
- 白名单专属线路（/upstream-exclusive-line-config 兼容门面与
  /exclusive-lines 线路记录 CRUD）：读写唯一实现在
  upstream_playback.exclusive_lines / save_exclusive_lines；
  取流基址与入口密钥只回已配状态，原文永不回浏览器。

文件名保留 admin_upstream_sources 是注册点与管理端「上游源」页
（现只剩线路配置）的存量位置，批 6 专属线路收尾时再行处置。

例外保留：源列表只读口（GET /upstream-sources）。源的写口
（建/改/启停/删）已删，但存量源行仍是播放线路与专属线路配置
的数据基础——专属线路的选源候选与按 id 显示源名、管理端建
CMS 库的选源都靠这份列表；删它会让保留功能的管理 UI 失去
源名显示。只读、不出凭据原文（走 source_payload）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import models as base_models
from backend.database import get_db
from backend.emby_server import upstream_sources as ups
from backend.emby_server.portal import admin_emby_router, require_staff


@admin_emby_router.get("/upstream-sources")
def list_upstream_sources(
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """上游源列表（id 升序；源是管理员手工建的，数量级是「台」，不分页）。"""
    return {"items": [ups.source_payload(s) for s in ups.list_sources(db)]}


class UpstreamDetailLineConfigIn(BaseModel):
    """详情页上游线路开关写入体（U-02）。"""

    enabled: bool


@admin_emby_router.get("/upstream-detail-line-config")
def get_upstream_detail_line_config(
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """读详情页上游线路开关（SystemConfig，无行＝开，见 upstream_playback）。"""
    from backend.emby_server import upstream_playback as _up

    return {"enabled": _up.detail_line_enabled(db)}


@admin_emby_router.put("/upstream-detail-line-config")
def put_upstream_detail_line_config(
    payload: UpstreamDetailLineConfigIn,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """写详情页上游线路开关（走 config_service 统一保存通道）。

    关时详情/PlaybackInfo 来源列表不再出现上游线路、取流口 404；
    开时恢复展示。审计由 /api/admin/emby/* 中间件统一记录。
    """
    from backend import config_service
    from backend.emby_server import upstream_playback as _up

    config_service.save_config(
        db, {_up.KEY_DETAIL_LINE_ENABLED: payload.enabled},
        schema="upstream_aggregate")
    db.commit()
    return {"enabled": _up.detail_line_enabled(db)}


class UpstreamExclusiveLineConfigIn(BaseModel):
    """白名单专属线路配置写入体（2026-11-04；2026-11-05 加 pilot 字段）。

    source_ids＝专属名单全量替换（管理页列表增删后回写整表）；
    pilot_base_url 缺省（None）＝基址不动，非 None 时写入
    （空串＝清掉基址、专属线整体惰性）。
    pilot_entry_enabled / pilot_entry_secret 同口径：缺省＝不动，
    secret 非 None 时写入（空串＝清除密钥、pilot 入口永不认定）。
    """

    source_ids: list[int] = Field(default_factory=list)
    pilot_base_url: Optional[str] = None
    pilot_entry_enabled: Optional[bool] = None
    pilot_entry_secret: Optional[str] = None


def _exclusive_line_config_view(db: Session) -> dict:
    """专属线路配置读视图（默认线路的派生视图，旧口兼容门面）。

    名单回 id 列表；基址与入口密钥只回「已配/未配」——两者都含
    口令/密钥，原文不回浏览器（页面只需状态＋重设入口）；入口
    开关回当前生效值。真源是默认线路记录（见 _line_view 的新口）。
    """
    from backend.emby_server import upstream_playback as _up

    line = _up.default_line(db)
    return {
        "source_ids": sorted(line["legacy_source_ids"]) if line else [],
        "pilot_configured": bool(line and line["stream_base_url"]),
        "pilot_entry_enabled": bool(line and line["enabled"]),
        "pilot_entry_configured": bool(line and line["entry_secret"]),
    }


@admin_emby_router.get("/upstream-exclusive-line-config")
def get_upstream_exclusive_line_config(
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """读白名单专属线路配置（默认线路视图，旧口兼容门面）。"""
    return _exclusive_line_config_view(db)


@admin_emby_router.put("/upstream-exclusive-line-config")
def put_upstream_exclusive_line_config(
    payload: UpstreamExclusiveLineConfigIn,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """写白名单专属线路配置（旧口兼容门面，落到默认线路记录）。

    能力化后配置真源是线路记录集：本口把 source_ids → 默认线路
    的 legacy 名单、pilot_base_url → 取流基址、pilot_entry_* →
    启用开关/入口密钥，经 save_exclusive_lines 统一落库（保存即
    失效配置缓存，热读立刻按新值执法）。无线路时按迁移线路的
    形状新建默认线路。管理端新界面走 /exclusive-lines 系列口，
    本口只作存量兼容。审计由 /api/admin/emby/* 中间件统一记录。
    """
    from backend.emby_server import upstream_playback as _up

    lines = _up.exclusive_lines(db)
    # 必须改 lines 列表里的那个 dict 本身（default_line 是另一次
    # 解析出的副本，改它不会落到待保存的列表上）。
    line = None
    for cand in lines:
        if cand["id"] == _up.MIGRATED_LINE_ID:
            line = cand
            break
    if line is None and lines:
        line = lines[0]
    if line is None:
        line = {
            "id": _up.MIGRATED_LINE_ID, "name": _up.MIGRATED_LINE_NAME,
            "entry_url": "", "stream_base_url": "", "entry_secret": "",
            "audience": _up.AUDIENCE_WHITELIST, "scope_mode": "all",
            "scope_source_ids": [], "legacy_source_ids": [],
            "enabled": False,
        }
        lines = [line]
    line["legacy_source_ids"] = _up._coerce_id_list(payload.source_ids)
    if payload.pilot_base_url is not None:
        line["stream_base_url"] = payload.pilot_base_url
        if line["stream_base_url"]:
            origin = _up._origin_of(line["stream_base_url"])
            if origin:
                line["entry_url"] = origin
    if payload.pilot_entry_enabled is not None:
        line["enabled"] = payload.pilot_entry_enabled
    if payload.pilot_entry_secret is not None:
        line["entry_secret"] = payload.pilot_entry_secret
    _up.save_exclusive_lines(db, lines)
    db.commit()
    return _exclusive_line_config_view(db)


# ---------------------------------------------------------------------------
# 专属线路通用能力（2026-11-05）：线路记录 CRUD（管理端统一入口）
#
# 一条线路＝名称/入口地址/取流基址/入口密钥/准入人群/适用范围/
# 启用开关/存量旧地址名单。取流基址与入口密钥含口令/密钥：所有
# 读口只回「已配/未配」，原文永不回浏览器；写入走
# upstream_playback.save_exclusive_lines 统一通道（config_service
# 校验＋缓存失效），保存即热读生效。审计由 /api/admin/emby/* 中
# 间件统一记录。
# ---------------------------------------------------------------------------


def _line_view(line: dict) -> dict:
    """线路读视图：基址与密钥只回已配状态，原文不回显。"""
    return {
        "id": line["id"],
        "name": line["name"],
        "entry_url": line["entry_url"],
        "audience": line["audience"],
        "scope_mode": line["scope_mode"],
        "scope_source_ids": list(line["scope_source_ids"]),
        "legacy_source_ids": list(line["legacy_source_ids"]),
        "enabled": bool(line["enabled"]),
        "stream_configured": bool(line["stream_base_url"]),
        "secret_configured": bool(line["entry_secret"]),
    }


class ExclusiveLineIn(BaseModel):
    """新建线路写入体（基址/密钥创建时可一并给，后续可改）。"""

    name: str
    entry_url: str = ""
    stream_base_url: Optional[str] = None
    entry_secret: Optional[str] = None
    audience: str = "whitelist"
    scope_mode: str = "all"
    scope_source_ids: list[int] = Field(default_factory=list)
    legacy_source_ids: list[int] = Field(default_factory=list)
    enabled: bool = True


class ExclusiveLineUpdateIn(BaseModel):
    """编辑线路写入体：None＝不动；基址/密钥空串＝清除。"""

    name: Optional[str] = None
    entry_url: Optional[str] = None
    stream_base_url: Optional[str] = None
    entry_secret: Optional[str] = None
    audience: Optional[str] = None
    scope_mode: Optional[str] = None
    scope_source_ids: Optional[list[int]] = None
    legacy_source_ids: Optional[list[int]] = None
    enabled: Optional[bool] = None


class ExclusiveLineResetSecretIn(BaseModel):
    """重置入口密钥写入体：新值由管理员提供（须同步改反代侧注入
    值）；响应只回已配状态，原文不回显（与全线口径一致）。"""

    secret: str


def _get_line_or_404(db: Session, line_id: str):
    from backend.emby_server import upstream_playback as _up

    lines = _up.exclusive_lines(db)
    for line in lines:
        if line["id"] == line_id:
            return lines, line
    raise HTTPException(status_code=404, detail="专属线路不存在")


@admin_emby_router.get("/exclusive-lines")
def list_exclusive_lines(
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """线路列表（含迁移视图：旧四键未落库时第一条即迁移线路）。"""
    from backend.emby_server import upstream_playback as _up

    return {"lines": [_line_view(l) for l in _up.exclusive_lines(db)]}


@admin_emby_router.post("/exclusive-lines")
def create_exclusive_line(
    payload: ExclusiveLineIn,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """新建一条线路（id 服务端生成）；保存即入口认定/选线热生效。"""
    import uuid as _uuid

    from backend.emby_server import upstream_playback as _up

    lines = _up.exclusive_lines(db)
    line = {
        "id": _uuid.uuid4().hex[:12],
        "name": payload.name,
        "entry_url": payload.entry_url,
        "stream_base_url": payload.stream_base_url or "",
        "entry_secret": payload.entry_secret or "",
        "audience": payload.audience,
        "scope_mode": payload.scope_mode,
        "scope_source_ids": payload.scope_source_ids,
        "legacy_source_ids": payload.legacy_source_ids,
        "enabled": payload.enabled,
    }
    lines.append(line)
    saved = _up.save_exclusive_lines(db, lines)
    db.commit()
    created = next(l for l in saved if l["id"] == line["id"])
    return {"line": _line_view(created)}


@admin_emby_router.put("/exclusive-lines/{line_id}")
def update_exclusive_line(
    line_id: str,
    payload: ExclusiveLineUpdateIn,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """编辑线路：None 字段不动；基址/密钥传空串＝清除该项。"""
    from backend.emby_server import upstream_playback as _up

    lines, line = _get_line_or_404(db, line_id)
    for field in ("name", "entry_url", "stream_base_url", "entry_secret",
                  "audience", "scope_mode", "scope_source_ids",
                  "legacy_source_ids", "enabled"):
        value = getattr(payload, field)
        if value is not None:
            line[field] = value
    saved = _up.save_exclusive_lines(db, lines)
    db.commit()
    updated = next(l for l in saved if l["id"] == line_id)
    return {"line": _line_view(updated)}


@admin_emby_router.post("/exclusive-lines/{line_id}/reset-secret")
def reset_exclusive_line_secret(
    line_id: str,
    payload: ExclusiveLineResetSecretIn,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """重置线路入口密钥：只回已配状态，新旧值都不回显。

    重置后旧密钥命中的请求立刻按普通入口处理；反代侧注入值须
    由管理员同步改成同值，线路才重新被认定。
    """
    from backend.emby_server import upstream_playback as _up

    if not payload.secret or not payload.secret.strip():
        raise HTTPException(status_code=400, detail="密钥不能为空")
    lines, line = _get_line_or_404(db, line_id)
    line["entry_secret"] = payload.secret
    _up.save_exclusive_lines(db, lines)
    db.commit()
    return {"secret_configured": True}


@admin_emby_router.delete("/exclusive-lines/{line_id}")
def delete_exclusive_line(
    line_id: str,
    staff: base_models.WebUser = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """删除线路：删后该线入口不再被认定、用户端不再展示。"""
    from backend.emby_server import upstream_playback as _up

    lines, _line = _get_line_or_404(db, line_id)
    lines = [l for l in lines if l["id"] != line_id]
    _up.save_exclusive_lines(db, lines)
    db.commit()
    return {"deleted": True}
