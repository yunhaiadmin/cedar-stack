"""弹幕 API 能力（第三方播放器，2026-11-18）：专职 Token、线路与下发。

与站内自发弹幕（backend/danmaku.py，网页播放器内发弹幕）是两个
功能：本模块管的是「给播放器填的弹幕 API 地址」——用户在概览页
生成专属 Token、选线路、复制形如
``https://<host>/api/v1/danmu/<token>/`` 的地址填进播放器，
播放器按 Danmu 插件约定（见 emby_server.compat_routes G7 注释：
Hills / Streama 系客户端，bilibili 风格 XML）在该地址后拼条目
Id 取弹幕。契约要点：

- Token：每用户恰一个（danmu_api_tokens.user_id 唯一），32 位
  十六进制，有效期一年、不自动续期，删除即失效；生成无强因子
  前置。明文落库只为服务端能为本人重拼地址——除本人地址接口
  外，任何响应、日志、审计都不出现 token 明文。
- 线路：SystemConfig 家族 danmu_api 的 danmu_api_lines（JSON
  数组 [{id,name,note,host,enabled}]），空/非法 = 代码默认两条；
  线路只换 host，路径与 token 不变。enabled 缺省视为启用
  （兼容旧配置）；enabled=false 的线路仍在列表里下发（用户端
  显示「暂停服务」不可选），但不为它拼地址（/address 回 409）。
  默认两条的 host 是施工时实测
  能路由到后端 /api 的前台域名：cdn.aetrix.love（CF 代理的
  对外客户端地址）与 www.aetrix.love（站点主域名，同走 CF）；
  emby.aetrix.love 也可达，但那是源站直连地址，不作用户面默认。
- 门槛（公开下发口服务端真校验）：token 有效 + 号主有有效
  套餐（个人有效订阅按有效账号解析，或团队播放资格）+ 有剩余
  流量（traffic_quota.usage_summary 的 exhausted 口径，未配月
  额度的套餐不算耗尽，与 traffic_gate 不拦同口径）。不达标按
  协议回空文档（播放器视为「没弹幕」）；弹幕请求不扣流量——
  本模块不写任何计量表（校验点：serve_item_danmaku 全程只读）。
- 内容来源：站内弹幕存量（danmaku_comments，按条目匹配）与
  外部弹幕源合并下发。外部源（2026-11-19 接入）是 LogVar 弹幕
  API（弹弹play 兼容协议）：开关 danmu_api_external_enabled
  默认关，上游 base 地址走 danmu_api_external_url（默认空 =
  未配置，只存后台、用户不可见）；取弹幕链路是「文件名 match
  → episodeId → 整集 comment」，结果归一后与站内弹幕按时间轴
  合并。上游超时/报错/无匹配一律回落站内结果，错误不透传；
  结果带短 TTL 进程缓存，避免每次播放都打上游。单步请求超时
  走 danmu_api_external_timeout_seconds（默认 15 秒、钳制
  3–60）：上游 match 一步实测就要 8 秒上下，固定小超时会让
  外部弹幕永远超时拿不到（2026-11-19 生产实测）。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import httpx
from sqlalchemy.orm import Session

from backend import models as core
from backend import sub_accounts, subscriptions, traffic_quota
from backend.danmaku import list_danmaku
from backend.emby_server import models as em
from backend.integrations import store

logger = logging.getLogger(__name__)

KEY_LINES = "danmu_api_lines"
KEY_EXTERNAL_ENABLED = "danmu_api_external_enabled"
KEY_EXTERNAL_URL = "danmu_api_external_url"
KEY_EXTERNAL_TIMEOUT = "danmu_api_external_timeout_seconds"

TOKEN_VALIDITY_DAYS = 365
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_LINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

#: 默认线路（配置为空/非法时的回退；host 均为实测可达后端的前台域名）
DEFAULT_LINES: list[dict] = [
    {"id": "intl", "name": "国际方向",
     "note": "CloudFlare CDN 加速的优质海外线路",
     "host": "cdn.aetrix.love", "enabled": True},
    {"id": "intl_backup", "name": "国际方向-备用",
     "note": "国际主线路不稳定时可使用",
     "host": "www.aetrix.love", "enabled": True},
]

_MAX_DELIVERY_ITEMS = 5000
_EMPTY_XML = '<?xml version="1.0" encoding="utf-8"?><i></i>'


# ------------------------------------------------------------------ Token


def _valid_row(db: Session, user_id: int) -> core.DanmuApiToken | None:
    """用户当前有效的 token 行；过期行视为不存在（到期需重新生成）。"""
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None or row.expires_at <= datetime.now():
        return None
    return row


def token_status(db: Session, user_id: int) -> dict:
    """状态口径：只有未过期的 token 才算 exists；不回明文。"""
    row = _valid_row(db, user_id)
    if row is None:
        return {"exists": False, "expires_at": None}
    return {"exists": True, "expires_at": row.expires_at.isoformat()}


def generate_token(db: Session, user_id: int) -> core.DanmuApiToken:
    """生成 token：已有有效 token 直接回现有行（不重复生成）；
    只有过期行时替换为新值（一年期从生成时刻起算）。自带 commit。"""
    row = _valid_row(db, user_id)
    if row is not None:
        return row
    now = datetime.now()
    expires = now + timedelta(days=TOKEN_VALIDITY_DAYS)
    while True:
        value = secrets.token_hex(16)
        clash = (db.query(core.DanmuApiToken)
                 .filter(core.DanmuApiToken.token == value).first())
        if clash is None:
            break
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None:
        row = core.DanmuApiToken(user_id=user_id, token=value,
                                 created_at=now, expires_at=expires)
        db.add(row)
    else:  # 过期行原地换新值：一用户一行的不变量不破
        row.token = value
        row.created_at = now
        row.expires_at = expires
    db.commit()
    db.refresh(row)
    return row


def delete_token(db: Session, user_id: int) -> bool:
    """删除即失效（硬删行）；无行返回 False。自带 commit。"""
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.user_id == user_id).first())
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def find_token_row(db: Session, token: str) -> core.DanmuApiToken | None:
    """按 token 值寻址（公开下发口）：形态非法/不存在/已过期一律 None。"""
    if not token or not _TOKEN_RE.match(token):
        return None
    row = (db.query(core.DanmuApiToken)
           .filter(core.DanmuApiToken.token == token).first())
    if row is None or row.expires_at <= datetime.now():
        return None
    return row


# ------------------------------------------------------------------ 线路


def validate_lines(raw) -> list[dict]:
    """线路表形状校验（管理端保存与读取共用）：非法抛 ValueError。

    每条必须是 {id,name,note,host}：id 为小写 slug 且全表唯一，
    host 为裸域名（不带协议/路径/端口），name 非空。可选字段
    enabled：缺省视为 True（旧配置无此字段照常启用）；出现时
    必须是布尔值。归一化输出恒带 enabled。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("线路表必须是非空数组")
    lines: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"第 {i + 1} 条线路不是对象")
        line_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        note = str(entry.get("note") or "").strip()
        host = str(entry.get("host") or "").strip().lower()
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"线路 {line_id} 的 enabled 必须是布尔值")
        if not _LINE_ID_RE.match(line_id):
            raise ValueError(f"第 {i + 1} 条线路 id 不合法：{line_id!r}")
        if line_id in seen:
            raise ValueError(f"线路 id 重复：{line_id}")
        if not name:
            raise ValueError(f"线路 {line_id} 缺少名称")
        if not _HOST_RE.match(host):
            raise ValueError(f"线路 {line_id} 的 host 不是裸域名：{host!r}")
        seen.add(line_id)
        lines.append({"id": line_id, "name": name,
                      "note": note, "host": host, "enabled": enabled})
    return lines


def get_lines(db: Session) -> list[dict]:
    """当前生效线路：配置合法用配置，空/非法回退默认两条（读侧不抛错）。"""
    raw = store.get_value(db, KEY_LINES, "")
    if raw and str(raw).strip():
        try:
            return validate_lines(json.loads(raw))
        except (ValueError, TypeError) as exc:
            logger.warning("danmu_api_lines 配置非法，回退默认线路：%s", exc)
    return [dict(line) for line in DEFAULT_LINES]


def build_address(db: Session, user_id: int, line_id: str) -> str | None:
    """为本人拼完整弹幕地址；线路不存在、已暂停或无有效 token 时返回 None。

    暂停线路永不拼地址是核心不变量（端点层另对暂停回 409 区分
    「线路暂停」与「未生成 token」，见 api/danmu_api.py）。
    """
    line = next((ln for ln in get_lines(db) if ln["id"] == line_id), None)
    if line is None or not line["enabled"]:
        return None
    row = _valid_row(db, user_id)
    if row is None:
        return None
    return f"https://{line['host']}/api/v1/danmu/{row.token}/"


# ------------------------------------------------------------------ 门槛


def check_access(db: Session, owner_id: int) -> tuple[bool, str]:
    """使用门槛（服务端真校验）：有效套餐 + 有剩余流量。

    有效套餐：个人有效订阅（家庭子账号按主账号解析，与全站
    有效账号口径一致）或团队播放资格（teams.play_eligible_team，
    与 usage_summary/can_play 同一口）。剩余流量：只认
    usage_summary 的 exhausted（套餐配了月额度且有效已用 >=
    额度）；未配月额度（enabled=False）不算耗尽、不拦（与
    traffic_gate 对未启用额度用户的口径一致）。返回 (放行, 原因码)。
    """
    effective_id = sub_accounts.effective_user_id(db, owner_id) or owner_id
    has_plan = (subscriptions.active_subscription(db, effective_id)
                is not None)
    if not has_plan:
        from backend import teams as teams_service  # 懒引：防循环依赖

        has_plan = teams_service.play_eligible_team(db, owner_id) is not None
    if not has_plan:
        return False, "no_active_plan"
    summary = traffic_quota.usage_summary(db, owner_id)
    if summary.get("exhausted"):
        return False, "traffic_exhausted"
    return True, ""


# ------------------------------------------------------------------ 下发


def external_enabled(db: Session) -> bool:
    """外部弹幕源适配开关：无行 = 关；只认 "true"（与 danmaku 口径一致）。"""
    return store.get_value(db, KEY_EXTERNAL_ENABLED, "false") == "true"


def validate_external_url(raw) -> str:
    """外部源地址形状校验（管理端保存侧）：空 = 未配置（合法）；
    非空必须是 http(s) 绝对地址（可带 token 路径），归一去尾斜杠。"""
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("外部弹幕源地址必须是 http(s) 绝对地址")
    if parsed.username or parsed.password:
        raise ValueError("外部弹幕源地址不许带账号密码")
    return value


def external_url(db: Session) -> str:
    """当前生效的外部源 base 地址；未配置/非法 = ""（读侧不抛错）。

    地址形如 https://<host>/<token 路径>——路径本身即上游凭据，
    只存后台配置，不进任何用户端响应与日志。
    """
    raw = str(store.get_value(db, KEY_EXTERNAL_URL, "") or "").strip()
    if not raw:
        return ""
    try:
        return validate_external_url(raw)
    except ValueError:
        logger.warning("danmu_api_external_url 配置非法，外部源停用")
        return ""


#: 外部源单步请求超时（秒）：默认 15——上游 match 一步实测 8.7 秒、
#: comment 一步 3.3 秒（2026-11-19 生产实测），旧固定总超时 4 秒
#: 让 match 永远 ReadTimeout、外部弹幕一条都拿不到。可配
#: （danmu_api_external_timeout_seconds），读侧钳制 3–60：下限
#: 防慢上游必死，上限防单个播放请求无限等；保存侧 schema 同
#: 范围拒绝是第一层，钳制兜底直写库的脏值。
EXTERNAL_TIMEOUT_DEFAULT_SECONDS = 15.0
EXTERNAL_TIMEOUT_MIN_SECONDS = 3.0
EXTERNAL_TIMEOUT_MAX_SECONDS = 60.0
#: 连接超时固定 3 秒（且不超过总超时）：上游慢在响应生成、不在
#: 建连；连接迟迟建不上通常是网络不通，不陪总超时一起等。
_EXTERNAL_CONNECT_TIMEOUT = 3.0


def external_timeout_seconds(db: Session) -> float:
    """当前生效的外部源单步总超时（秒）：缺省/非法回默认，钳制 3–60。"""
    raw = store.get_value(db, KEY_EXTERNAL_TIMEOUT, "")
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return EXTERNAL_TIMEOUT_DEFAULT_SECONDS
    return min(max(seconds, EXTERNAL_TIMEOUT_MIN_SECONDS),
               EXTERNAL_TIMEOUT_MAX_SECONDS)


def _upstream_timeout(db: Session) -> httpx.Timeout:
    """由配置秒数拼 httpx 超时：读/写/池用总超时，连接单独 3 秒封顶。"""
    total = external_timeout_seconds(db)
    return httpx.Timeout(
        total, connect=min(_EXTERNAL_CONNECT_TIMEOUT, total))


# 外部源短 TTL 进程内缓存：键 = 条目 id，值 = (过期时刻, 归一弹幕)。
# 命中 10 分钟、无匹配 5 分钟、上游失败 1 分钟——避免每次播放都
# 打上游，也避免上游故障时每个请求都等一遍超时。
_EXT_TTL_OK = 600.0
_EXT_TTL_NOMATCH = 300.0
_EXT_TTL_FAIL = 60.0
_EXT_CACHE_MAX = 512
_ext_cache: dict[int, tuple[float, list[dict]]] = {}
_ext_cache_lock = threading.Lock()

_MAX_EXTERNAL_ITEMS = 30000


def _ext_cache_get(item_id: int) -> list[dict] | None:
    now = time.monotonic()
    with _ext_cache_lock:
        hit = _ext_cache.get(item_id)
        if hit is None:
            return None
        expires, rows = hit
        if expires <= now:
            _ext_cache.pop(item_id, None)
            return None
        return rows


def _ext_cache_put(item_id: int, rows: list[dict], ttl: float) -> None:
    with _ext_cache_lock:
        if len(_ext_cache) >= _EXT_CACHE_MAX:
            _ext_cache.clear()
        _ext_cache[item_id] = (time.monotonic() + ttl, rows)


def _upstream_json(op: str, method: str, url: str,
                   timeout: httpx.Timeout,
                   json_body: dict | None = None) -> dict | None:
    """打上游一发：任何失败（超时/连接/HTTP 非 200/坏 JSON）回 None。

    日志只记操作名与异常类型：上游地址含 token 路径，不落日志。
    """
    try:
        # trust_env=False：服务端取上游不看部署环境代理变量（no_proxy
        # 里带 [::1] 这类写法会让 httpx 解析代理表时直接抛 InvalidURL，
        # 2026-11-19 本机实测）；直连口径与站内其它出网调用一致。
        with httpx.Client(timeout=timeout,
                          trust_env=False) as client:
            resp = client.request(method, url, json=json_body)
        if resp.status_code != 200:
            logger.warning("danmu external %s: http %s",
                           op, resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:  # 上游不可控：一切异常都回落，不许透传
        logger.warning("danmu external %s failed: %s",
                       op, type(exc).__name__)
        return None
    return data if isinstance(data, dict) else None


def _match_episode(base: str, item: em.MediaItem, filename: str,
                   timeout: httpx.Timeout
                   ) -> tuple[tuple[int, float] | None, bool]:
    """按文件名 POST /api/v2/match 匹配：回 ((episodeId, shift 秒), False)。

    未命中回 (None, False)；传输层失败回 (None, True)——两者缓存
    TTL 不同（失败短缓存，防上游故障期反复等超时）。
    """
    body = {
        "fileName": filename,
        "fileHash": "",
        "fileSize": int(getattr(item, "size", 0) or 0),
        "videoDuration": int(
            (getattr(item, "duration_ticks", 0) or 0) // 10_000_000),
        "matchMode": "fileNameOnly",
    }
    data = _upstream_json("match", "POST", f"{base}/api/v2/match",
                          timeout, json_body=body)
    if data is None:
        return None, True
    if not data.get("success") or not data.get("isMatched"):
        return None, False
    matches = data.get("matches") or []
    if not matches or not isinstance(matches[0], dict):
        return None, False
    try:
        episode_id = int(matches[0]["episodeId"])
        shift = float(matches[0].get("shift") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None, False
    return (episode_id, shift), False


def _fetch_comments(base: str, episode_id: int, shift: float,
                    timeout: httpx.Timeout) -> list[dict] | None:
    """GET /api/v2/comment/<episodeId> 取整集弹幕并归一；失败回 None。

    上游每条 {p: "时间,模式,颜色,[来源]", m: 文本, t: 时间}，映射
    到下发形态 {position_seconds, text, mode, color}：时间加 shift
    偏移并钳到 >= 0；模式（1 滚动/4 底部/5 顶部）与十进制颜色
    原样带入，解析不出回退滚动/白色；cid、来源标签、点赞数在
    bilibili XML 里无对应字段，丢弃。
    """
    data = _upstream_json("comment", "GET",
                          f"{base}/api/v2/comment/{episode_id}", timeout)
    if data is None:
        return None
    rows: list[dict] = []
    for entry in data.get("comments") or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("m") or "").strip()
        if not text:
            continue
        parts = str(entry.get("p") or "").split(",")
        try:
            position = float(parts[0])
        except (IndexError, TypeError, ValueError):
            try:
                position = float(entry.get("t"))
            except (TypeError, ValueError):
                continue
        mode, color = 1, 16777215
        if len(parts) >= 3:
            try:
                mode = int(parts[1])
            except ValueError:
                pass
            try:
                color = int(parts[2])
            except ValueError:
                pass
            if not 0 <= color <= 16777215:
                color = 16777215
        rows.append({
            "position_seconds": max(0.0, position + shift),
            "text": text,
            "mode": mode,
            "color": color,
        })
        if len(rows) >= _MAX_EXTERNAL_ITEMS:
            break
    rows.sort(key=lambda r: r["position_seconds"])
    return rows


def _candidate_filenames(db: Session, item: em.MediaItem) -> list[str]:
    """给上游 match 的候选文件名：真实文件基名优先，其次元数据拼名。

    上游按文件名解析标题/季/集：真实基名信息最全；拼名口径——
    单集用「剧名 SxxExx」、电影用「片名 年份」/「片名」。
    """
    names: list[str] = []

    def _push(name: str) -> None:
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)

    file_path = str(getattr(item, "file_path", "") or "")
    if file_path:
        _push(file_path.replace("\\", "/").rsplit("/", 1)[-1])
    item_type = getattr(item, "item_type", "")
    if item_type == "episode":
        title = ""
        series_id = getattr(item, "series_id", None)
        if series_id:
            series = (db.query(em.MediaItem)
                      .filter(em.MediaItem.id == series_id).first())
            if series is not None:
                title = series.name or ""
        title = title or (item.name or "")
        season = getattr(item, "season_number", None)
        episode = getattr(item, "episode_number", None)
        if title and season is not None and episode is not None:
            _push(f"{title} S{int(season):02d}E{int(episode):02d}.mp4")
        elif title:
            _push(f"{title}.mp4")
    elif item_type == "movie":
        if item.name and getattr(item, "production_year", None):
            _push(f"{item.name} {int(item.production_year)}.mp4")
        if item.name:
            _push(f"{item.name}.mp4")
    return names


def fetch_external_danmaku(db: Session, item: em.MediaItem) -> list[dict]:
    """外部弹幕源取弹幕：归一形态 [{position_seconds, text, mode, color}]。

    开关关 / 未配地址 / 条目不是可播类型（movie/episode）→ 空，
    不打上游。命中流程：候选文件名逐个 match → 首个命中的
    episodeId 取整集 comment。匹配不到、上游超时/报错一律回空
    （下发侧回落站内结果）。门槛校验在 api 层 _serve 已先行，
    不过关根本走不到这里——本函数不重复判门槛。
    """
    if not external_enabled(db):
        return []
    base = external_url(db)
    if not base:
        return []
    if getattr(item, "item_type", "") not in ("movie", "episode"):
        return []
    cached = _ext_cache_get(item.id)
    if cached is not None:
        return cached
    timeout = _upstream_timeout(db)
    matched: tuple[int, float] | None = None
    failed = False
    for filename in _candidate_filenames(db, item):
        matched, failed = _match_episode(base, item, filename, timeout)
        if matched is not None:
            break
    if matched is None:
        _ext_cache_put(item.id, [],
                       _EXT_TTL_FAIL if failed else _EXT_TTL_NOMATCH)
        return []
    episode_id, shift = matched
    rows = _fetch_comments(base, episode_id, shift, timeout)
    if rows is None:
        _ext_cache_put(item.id, [], _EXT_TTL_FAIL)
        return []
    _ext_cache_put(item.id, rows, _EXT_TTL_OK)
    return rows


def _resolve_item(db: Session, item_ref: str) -> em.MediaItem | None:
    """条目寻址：Emby Id（guid）优先；纯数字时兼认内部 id（客户端差异兜底）。"""
    item = (db.query(em.MediaItem)
            .filter(em.MediaItem.guid == item_ref).first())
    if item is None and item_ref.isdigit():
        item = (db.query(em.MediaItem)
                .filter(em.MediaItem.id == int(item_ref)).first())
    return item


def render_item_danmaku_xml(db: Session, item: em.MediaItem,
                            owner_id: int) -> str:
    """按条目拼 bilibili 风格弹幕 XML：站内弹幕 + 外部源按时间轴合并。

    <d> 的 p 属性沿用 Danmu 插件约定：时间(秒),模式,字号(25),
    颜色(十进制),发送时间戳,弹幕池,发送者,行 id。站内弹幕恒为
    滚动/白色；外部源条目带自己的模式与颜色（上游归一时已处理）。
    """
    rows = list_danmaku(db, item.id, 0.0, float(7 * 24 * 3600),
                        _MAX_DELIVERY_ITEMS, current_user_id=owner_id)
    merged = [(float(r["position_seconds"]), str(r["text"]), 1, 16777215)
              for r in rows]
    for ext in fetch_external_danmaku(db, item):
        try:
            color = ext.get("color")
            merged.append((
                float(ext["position_seconds"]), str(ext["text"]),
                int(ext.get("mode") or 1),
                int(color) if color is not None else 16777215))
        except (KeyError, TypeError, ValueError):
            continue
    merged.sort(key=lambda pair: pair[0])
    if not merged:
        return _EMPTY_XML
    parts = ['<?xml version="1.0" encoding="utf-8"?><i>']
    for idx, (position, text, mode, color) in enumerate(merged):
        pos = f"{position:.3f}".rstrip("0").rstrip(".")
        parts.append(
            f'<d p="{pos},{mode},25,{color},0,0,danmu,{idx}">'
            f"{xml_escape(text)}</d>")
    parts.append("</i>")
    return "".join(parts)


def empty_xml() -> str:
    return _EMPTY_XML
