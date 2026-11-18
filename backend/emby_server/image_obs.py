"""EA 图片流量观测（F5 前置观测，2026-11-18）。

背景与目的：F5 评估（reports/f5-image-auth-assessment-20261118.md）
推荐 C + D-lite（堵存在性预言机 + 无凭据尺寸封顶），但生产图片
流量的构成从未测过——无凭据流量里已知客户端（Reflix/Lenna/Yamby
等）与未知爬虫各占多少、原图请求占比多少，都没有数据。用户
2026-11-18 拍板「先观测一周再上 C+D-lite」：本模块**只观测**，
不改变任何鉴权 / 放行行为，数据用来定 C 的限流与 D-lite 的
封顶数值；观测期满、参数定版后本模块与观测表一并退役。

口径：

- 挂点：纯 ASGI 中间件（与 ``audit.py`` / ``download_guard.py``
  同一写法），只匹配条目图片路径，其余请求零开销透传、不包裹
  响应体；响应状态在 ``http.response.start`` 捕获、请求结束后
  才计数，全程异常自吞——观测坏了不能影响出图。
- 维度：（服务器本地自然日、UA 家族、凭据有无、图片类型、
  尺寸档、响应状态）。UA 只归类成家族短码、**不存原文**；
  不存 IP（IP 只用于既有限流）、不存条目 GUID。
- 落存：进程内计数（锁 + dict 增量，O(1)），后台线程每
  ``FLUSH_INTERVAL_SEC`` 秒批量 upsert 到 ``emby_image_obs_daily``
  （新表走 create_all），图片请求路径不碰 DB。
- 开关：SystemConfig 键 ``emby_image_obs_enabled``，默认开；
  flush 线程每轮直查 DB 刷新开关缓存，关掉后 ``record`` 立即
  变空转、零观测开销。纯 SystemConfig 键，不进 env.example
  （#1527 先例），校验注册见 config_schema 的 image_obs 家族。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

#: 观测总开关 SystemConfig 键（默认开；关 = record 空转、flush 停写）
KEY_IMAGE_OBS_ENABLED = "emby_image_obs_enabled"

#: 计数 flush 间隔（秒）：图片是高频路径，攒批写小表，不逐请求写库
FLUSH_INTERVAL_SEC = 120

_FALSE_WORDS = frozenset({"0", "false", "no", "off"})

# 条目图片路径：/Items/{id}/Images/{type} 与带序号变体，含 /emby 前缀
# （{0,2} 兼容 EA 前置 DoubleEmbyPrefix 重写前的双前缀形态——本
# 中间件注册在其内层、看到的是重写后路径，正则两种都收无妨）。
_IMAGE_PATH_RE = re.compile(
    r"^(?:/emby){0,2}/Items/[^/]+/Images/([^/]+)(?:/\d+)?/?$")

#: 已知图片类型（路径段原样保留）；不在表里的归 Other
_KNOWN_IMAGE_TYPES = frozenset({
    "Primary", "Backdrop", "Art", "Thumb", "Logo",
    "Banner", "Disc", "Menu", "Chapter",
})

#: UA 家族归类（小写子串 → 家族短码，按序匹配、先具体后一般）。
#: reflix/lenna/yamby 是无凭据兼容放行面的三家已知客户端，单列；
#: 其余常见 Emby 客户端各归一家；浏览器 / WebView 归 browser；
#: 都不命中（含空 UA）归 other。
_UA_FAMILY_RULES: tuple[tuple[str, str], ...] = (
    ("reflix", "reflix"),
    ("lenna", "lenna"),
    ("yamby", "yamby"),
    ("infuse", "infuse"),
    ("vidhub", "vidhub"),
    ("senplayer", "senplayer"),
    ("fileball", "fileball"),
    ("hills", "hills"),
    ("emby", "emby"),
    ("mediabrowser", "emby"),
    ("mozilla", "browser"),
    ("applewebkit", "browser"),
)

#: 尺寸档分界：请求的最大维度超过它算 large（与 D-lite 的背景
#: 封顶候选 1280 对齐——观测正是为定这个数）
_LARGE_DIM_THRESHOLD = 1280

SIZE_ORIGINAL = "original"
SIZE_LARGE = "large"
SIZE_STANDARD = "standard"


def classify_ua_family(user_agent: str | None) -> str:
    """UA → 家族短码（只归类，不留原文）。"""
    ua = (user_agent or "").lower()
    if not ua:
        return "other"
    for needle, family in _UA_FAMILY_RULES:
        if needle in ua:
            return family
    return "other"


def classify_image_type(segment: str | None) -> str:
    """图片类型路径段 → 已知类型原样保留，其余归 Other。"""
    seg = (segment or "").strip()
    return seg if seg in _KNOWN_IMAGE_TYPES else "Other"


def classify_size_bucket(query: dict) -> str:
    """尺寸档：无可解析尺寸参数 = original（原图）；请求最大维度
    >1280 = large；其余 = standard。参数口径与 item_image 的
    pick_dim 一致（maxWidth/maxHeight 与别名 w/h 都看），这里只判
    「请求了多大的图」，非法值按未请求处理。"""
    dims: list[int] = []
    for name in ("maxWidth", "maxHeight", "w", "h"):
        for raw in query.get(name) or ():
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                dims.append(value)
    if not dims:
        return SIZE_ORIGINAL
    return SIZE_LARGE if max(dims) > _LARGE_DIM_THRESHOLD else SIZE_STANDARD


def request_has_credential(headers, query: dict) -> bool:
    """凭据有无（只看形态在不在，不验有效性）：与 auth
    ._token_from_request 接受的形态对齐——X-Emby-Token /
    X-MediaBrowser-Token / X-Emby-Authorization / Authorization
    任一头非空，或 ?api_key= 非空。headers 是 ASGI scope 的
    原始 [(bytes, bytes)] 列表。"""
    names = set()
    for key, value in headers or ():
        if value and value.strip():
            names.add(key.lower())
    if names & {b"x-emby-token", b"x-mediabrowser-token",
                b"x-emby-authorization", b"authorization"}:
        return True
    return any(v.strip() for v in (query.get("api_key") or ()))


# ---------------------------------------------------------------------------
# 进程内计数 + 定时 flush
# ---------------------------------------------------------------------------

# 维度键：(day, ua_family, has_credential, image_type, size_bucket, status)
_lock = threading.Lock()
_counts: dict[tuple, int] = {}
_enabled = True  # 开关进程内缓存（默认开）；由 flush 线程按 DB 刷新
_flusher_started = False


def obs_enabled() -> bool:
    return _enabled


def record(day, ua_family: str, has_credential: bool, image_type: str,
           size_bucket: str, status: int) -> None:
    """记一笔（O(1)、异常自吞）：观测绝不能影响出图。"""
    try:
        if not _enabled:
            return
        key = (day, ua_family, bool(has_credential), image_type,
               size_bucket, int(status))
        with _lock:
            _counts[key] = _counts.get(key, 0) + 1
    except Exception:  # noqa: BLE001 — 观测坏了不能影响图片响应
        pass


def record_from_scope(scope, status: int) -> None:
    """从 ASGI scope 提取维度并计数（中间件在响应结束后调用）。"""
    try:
        match = _IMAGE_PATH_RE.match(scope.get("path") or "")
        if not match:
            return
        headers = scope.get("headers") or ()
        ua = ""
        for key, value in headers:
            if key.lower() == b"user-agent":
                ua = value.decode("latin-1", "replace")
                break
        query = parse_qs(
            scope.get("query_string", b"").decode("latin-1", "replace"),
            keep_blank_values=True)
        record(
            datetime.now().date(),
            classify_ua_family(ua),
            request_has_credential(headers, query),
            classify_image_type(match.group(1)),
            classify_size_bucket(query),
            status,
        )
    except Exception:  # noqa: BLE001 — 同 record 口径：自吞
        pass


def _read_enabled_from_db() -> bool:
    """开关直查 DB（只由 flush 线程调用，不在请求路径）：无行 =
    默认开；读失败回退当前缓存值——配置层故障不能把观测开关
    状态带偏，更不能影响图片。"""
    try:
        from backend.database import SessionLocal
        from backend.integrations import store

        with SessionLocal() as db:
            raw = str(store.read_value(db, KEY_IMAGE_OBS_ENABLED, "") or "")
        raw = raw.strip().lower()
        if not raw:
            return True
        return raw not in _FALSE_WORDS
    except Exception:  # noqa: BLE001
        return _enabled


def flush_now() -> int:
    """把进程内计数批量 upsert 落表，返回落库的维度行数。

    先整体换出计数 dict 再写库；写库失败把计数并回下轮重试，
    不丢数。异常自吞（调用方是后台线程 / 测试）。
    """
    global _counts
    with _lock:
        pending, _counts = _counts, {}
    if not pending:
        return 0
    try:
        from backend import models
        from backend.database import SessionLocal

        with SessionLocal() as db:
            for (day, ua_family, has_cred, image_type, size_bucket,
                 status), n in pending.items():
                row = (
                    db.query(models.EmbyImageObsDaily)
                    .filter_by(day=day, ua_family=ua_family,
                               has_credential=has_cred,
                               image_type=image_type,
                               size_bucket=size_bucket, status=status)
                    .first()
                )
                if row is None:
                    db.add(models.EmbyImageObsDaily(
                        day=day, ua_family=ua_family,
                        has_credential=has_cred, image_type=image_type,
                        size_bucket=size_bucket, status=status, count=n))
                else:
                    row.count = int(row.count or 0) + n
            db.commit()
        return len(pending)
    except Exception:  # noqa: BLE001 — 落库失败不丢计数，下轮重试
        logger.warning("图片观测 flush 失败，计数已并回下轮重试",
                       exc_info=True)
        with _lock:
            for key, n in pending.items():
                _counts[key] = _counts.get(key, 0) + n
        return 0


def _flusher_loop() -> None:
    global _enabled
    while True:
        time.sleep(FLUSH_INTERVAL_SEC)
        try:
            _enabled = _read_enabled_from_db()
            if _enabled:
                flush_now()
        except Exception:  # noqa: BLE001 — 后台线程不许死
            logger.warning("图片观测 flush 线程一轮失败", exc_info=True)


def start_flusher() -> bool:
    """启动 flush 后台线程（幂等；EA lifespan 调用）。"""
    global _flusher_started
    with _lock:
        if _flusher_started:
            return False
        _flusher_started = True
    threading.Thread(target=_flusher_loop, name="image-obs-flush",
                     daemon=True).start()
    logger.info("图片流量观测已启动（flush 间隔 %ds，开关 SystemConfig "
                "键 %s）", FLUSH_INTERVAL_SEC, KEY_IMAGE_OBS_ENABLED)
    return True


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------


class ImageObsMiddleware:
    """EA 图片流量观测中间件（纯 ASGI，与 audit.py 同一写法）。

    只匹配条目图片路径并在响应结束后计数；不匹配的请求原样
    透传，不包裹响应体，文件 / 流式响应零影响。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        method = (scope.get("method") or "").upper()
        if method not in ("GET", "HEAD") or not _IMAGE_PATH_RE.match(
                scope.get("path") or ""):
            return await self.app(scope, receive, send)

        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        await self.app(scope, receive, send_wrapper)
        record_from_scope(scope, status)


# ---------------------------------------------------------------------------
# 测试隔离（生产代码不调用）
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    global _counts, _enabled
    with _lock:
        _counts = {}
    _enabled = True


def _set_enabled_for_tests(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def _pending_for_tests() -> dict:
    with _lock:
        return dict(_counts)


__all__ = [
    "KEY_IMAGE_OBS_ENABLED",
    "FLUSH_INTERVAL_SEC",
    "SIZE_ORIGINAL",
    "SIZE_LARGE",
    "SIZE_STANDARD",
    "ImageObsMiddleware",
    "classify_ua_family",
    "classify_image_type",
    "classify_size_bucket",
    "request_has_credential",
    "record",
    "record_from_scope",
    "flush_now",
    "start_flusher",
    "obs_enabled",
]
