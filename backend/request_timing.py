"""API 请求耗时打点（C-08 可观测性，2026-11-28）。

背景：nginx 访问日志原先没有 $request_time、后端也没有任何请求
耗时记录，API 路由级延迟分位（P50/P95/P99）测不到，后续优化没有
验收标尺。nginx 侧已加 aetrix_timed 日志格式（docker/nginx.conf）；
本模块是后端侧的一半：

- 纯 ASGI 中间件（与 audit.py / image_obs.py 同一写法）：不包裹
  响应体，流式播放不受影响；只在响应 start 时取状态、请求结束
  时记一笔，全程异常自吞——打点坏了不能影响业务。
- 聚合维度：（方法、归一路径、状态码）。归一路径把 guid/纯数字/
  长 hex 段替换为 {id}——ASGI 层拿不到路由模板（路由在下游才
  定），用路径归一压低基数；种类上限 512，超出归 __other__，
  聚合结构恒有界。
- 每键记 count / total_ms / max_ms 与直方图桶（上界毫秒
  10/50/100/250/500/1000/2500/5000，末桶为溢出），snapshot()
  给出可算近似分位的完整数据，供 health/observability 后续消费。
- 慢请求（≥ SLOW_LOG_MS）另打一条 warning 日志（归一路径，不带
  原始 id），方便从日志直接定位慢端点。
- 紧急关停：env REQUEST_TIMING_DISABLED=1 时中间件零开销透传。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

#: 直方图桶上界（毫秒）；最后一个桶为 >5000ms 溢出
BUCKET_BOUNDS_MS = (10, 50, 100, 250, 500, 1000, 2500, 5000)
#: 聚合键总数硬上限（键 = 方法×归一路径×状态码），超出归 __other__
MAX_KEYS = 8192
#: 慢请求日志阈值（毫秒）
SLOW_LOG_MS = 1000

_DISABLED = os.environ.get("REQUEST_TIMING_DISABLED", "") == "1"

_ID_SEGMENT_RE = re.compile(
    r"^(?:\d+|[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{24,})$"
)

_LOCK = threading.Lock()
_STATS: dict[tuple, dict] = {}
_STARTED_AT = time.time()


def normalize_path(path: str) -> str:
    """路径归一：id 形态的段替换为 {id}，压低聚合基数。"""
    parts = []
    for seg in (path or "/").split("/"):
        if not seg:
            continue
        parts.append("{id}" if _ID_SEGMENT_RE.match(seg) else seg)
    return "/" + "/".join(parts)


def _bucket_index(ms: float) -> int:
    for i, bound in enumerate(BUCKET_BOUNDS_MS):
        if ms <= bound:
            return i
    return len(BUCKET_BOUNDS_MS)


def _record(method: str, path: str, status: int, ms: float) -> None:
    key = (method, path, int(status))
    with _LOCK:
        entry = _STATS.get(key)
        if entry is None:
            if len(_STATS) >= MAX_KEYS:
                key = (method, "__other__", int(status))
                entry = _STATS.get(key)
            if entry is None:
                entry = _STATS[key] = {
                    "count": 0, "total_ms": 0.0, "max_ms": 0.0,
                    "buckets": [0] * (len(BUCKET_BOUNDS_MS) + 1),
                }
        entry["count"] += 1
        entry["total_ms"] += ms
        if ms > entry["max_ms"]:
            entry["max_ms"] = ms
        entry["buckets"][_bucket_index(ms)] += 1


def snapshot() -> dict:
    """当前聚合快照（进程启动以来累计）：{键: {count,total_ms,max_ms,buckets}}。

    键形如 "GET /Items/{id}/Images/Primary 200"。分位可由 buckets
    近似算（见 approx_percentile_ms）。"""
    with _LOCK:
        items = list(_STATS.items())
    out = {}
    for (method, path, status), e in items:
        out[f"{method} {path} {status}"] = {
            "count": e["count"],
            "total_ms": round(e["total_ms"], 1),
            "max_ms": round(e["max_ms"], 1),
            "buckets": list(e["buckets"]),
        }
    return {"started_at": _STARTED_AT, "bucket_bounds_ms": list(BUCKET_BOUNDS_MS),
            "routes": out}


def approx_percentile_ms(entry: dict, q: float) -> float:
    """由直方图桶近似算分位（返回命中桶的上界，溢出桶返回 max_ms）。"""
    count = int(entry.get("count") or 0)
    if count <= 0:
        return 0.0
    target = count * q
    seen = 0
    buckets = entry.get("buckets") or []
    for i, n in enumerate(buckets):
        seen += n
        if seen >= target:
            if i < len(BUCKET_BOUNDS_MS):
                return float(BUCKET_BOUNDS_MS[i])
            return float(entry.get("max_ms") or BUCKET_BOUNDS_MS[-1])
    return float(entry.get("max_ms") or 0.0)


def _reset_for_tests() -> None:
    with _LOCK:
        _STATS.clear()


class RequestTimingMiddleware:
    """全路径请求计时：透传一切，只在结束后记一笔聚合。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _DISABLED or scope.get("type") != "http":
            return await self.app(scope, receive, send)
        method = (scope.get("method") or "").upper()
        path = normalize_path(scope.get("path", ""))
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            ms = (time.perf_counter() - started) * 1000.0
            try:
                _record(method, path, status, ms)
                if ms >= SLOW_LOG_MS:
                    logger.warning("慢请求 %s %s -> %s %.0fms",
                                   method, path, status, ms)
            except Exception:  # noqa: BLE001 — 打点异常绝不影响业务
                pass
