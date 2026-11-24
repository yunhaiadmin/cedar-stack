"""自建 Emby 服务器：流媒体服务（直连流 / Range / 中继直出）"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import logging
import os
import re
import threading
import time
from email.utils import formatdate
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

# 开箱即用播放优化：单文件并发 Range 限流（只此一处实现）
from backend.emby_server.playback_tune import RANGE_LIMITER
from urllib.parse import parse_qsl, urljoin, urlsplit

from backend.emby_server.mounts import MOUNT_UA, MountError

if TYPE_CHECKING:
    # httpx 运行时是延迟导入（各函数内按需 import，保持模块导入轻量）；
    # 但模块注解引用了 httpx.AsyncClient / httpx.Timeout，这里给类型检查器一个声明，
    # 消除未定义名称（pyflakes F821）。运行时不执行，不影响延迟导入。
    import httpx

logger = logging.getLogger(__name__)


def _drive_alt_media_file_id(url: str) -> Optional[str]:
    """从 Drive alt=media URL 提取 file_id。

    https://www.googleapis.com/drive/v3/files/<fid>?alt=media → fid；
    非此格式（115/WebDAV/其它）返回 None。
    """
    try:
        parts = urlsplit(url or "")
        if parts.netloc != "www.googleapis.com":
            return None
        segs = parts.path.strip("/").split("/")
        if len(segs) == 4 and segs[0] == "drive" and segs[1] == "v3" \
                and segs[2] == "files" and segs[3]:
            from urllib.parse import unquote
            return unquote(segs[3])
    except Exception:
        pass
    return None


def _drive_403_error_reason(body) -> tuple:
    """解析 Google Drive API 403 错误体，返回 (reason, message)；解析失败返回 ("", "")，永不抛异常。"""
    try:
        import json  # 局部导入，兼容文件内既有风格

        if not body:
            return ("", "")
        data = json.loads(body)
        if not isinstance(data, dict):
            return ("", "")
        err = data.get("error")
        if not isinstance(err, dict):
            return ("", "")

        errors = err.get("errors")
        first = {}
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first = errors[0]

        reason = first.get("reason", "")
        if not isinstance(reason, str):
            reason = str(reason) if reason is not None else ""

        message = err.get("message")
        if not message:
            message = first.get("message", "")
        if not isinstance(message, str):
            message = str(message) if message is not None else ""
        message = message[:200]

        return (reason, message)
    except Exception:
        return ("", "")


# 远程挂载（115 / WebDAV / AList）代理转发时的 UA。
# 同一环境变量只许一处默认值（横切纪律）：默认值在 mounts.MOUNT_UA 定义
# （115 优化的 PAN115_UA；115 直链对 UA 有偏好），这里直接复用，不另起一套。
REMOTE_UA = MOUNT_UA
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

CHUNK = 1024 * 256

# 中转零拷贝优化：实际读盘/读源站的块大小。
# 256KB 小块意味着每秒上百次「线程池提交 + FUSE read 系统调用 + Python 层拷贝」；
# 1MB 大块把这些开销砍到 1/4，首字节不受影响（第一块照样立即发出）。
# 注意：CHUNK 仍用于「客户端没给 Range 结束位置时的默认区间长度」计算，别混用。
READ_CHUNK = 1024 * 1024

# rclone FUSE 挂载点前缀（/mnt/mp、/mnt/paul，可用 EMBY_FUSE_PREFIXES 覆盖）。
# 这些路径的读可能因 Drive 配额/网络抖动中途失败。
FUSE_PREFIXES = tuple(
    p for p in os.getenv("EMBY_FUSE_PREFIXES", "/mnt/mp/,/mnt/paul/").split(",") if p
)

# FUSE 读遇到 OSError 时的重试次数与退避基数（秒，线性退避）。
_FUSE_READ_RETRIES = 3
_FUSE_READ_RETRY_DELAY = 0.5


def is_fuse_path(path: str) -> bool:
    """是不是 rclone FUSE 挂载上的文件（读可能中途失败，不能预先声明 Content-Length）。"""
    return bool(path) and path.startswith(FUSE_PREFIXES)


# 远程代理最多自己追几次重定向（与 httpx 的默认上限一致）
MAX_REDIRECTS = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_CREDENTIAL_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def _redirect_headers(forward: dict, from_url: str, to_url: str) -> dict:
    """跨主机重定向时丢掉凭据头

    WebDAV 的 Basic / 115 的 Cookie 是发给**源站**的；源站回一个 302 指向第三方主机时
    httpx 会把同样的头带过去——等于把凭据交给重定向目标。同主机（含换端口以外的同站）
    保留，跨主机一律剥掉。

    Drive 例外：Drive 文件保持私有，服务端用 SA 的 Bearer token 拉流；Google 下载链
    常在 Drive 域名之间跳转（drive.google.com → drive.usercontent.google.com），
    目标仍在 Drive 域名白名单内时保留 ``Authorization``（其余凭据头照剥），
    否则 token 会在跳转时丢失、私有文件拉流失败。
    """
    same_host = urlsplit(from_url).hostname == urlsplit(to_url).hostname
    if same_host:
        return forward
    to_host = (urlsplit(to_url).hostname or "").lower()
    try:
        # 延迟导入：drive_auth 只依赖标准库 + drive_changes，无循环导入风险；
        # 但 streaming 是播放热路径，异常时退回原逻辑（全剥）。
        from backend.emby_server.drive_auth import DRIVE_HOSTS
        drive_hosts: frozenset = DRIVE_HOSTS
    except Exception:  # noqa: BLE001
        drive_hosts = frozenset()
    if to_host in drive_hosts:
        # 只保留 authorization（SA Bearer token），cookie / proxy-authorization 照剥
        return {k: v for k, v in forward.items()
                if k.lower() == "authorization" or k.lower() not in _CREDENTIAL_HEADERS}
    return {k: v for k, v in forward.items() if k.lower() not in _CREDENTIAL_HEADERS}


def _next_redirect(url: str, location: str) -> tuple[str, dict[str, list[str]]]:
    """解析重定向目标并做安全校验（内网 / 回环 / 非法协议一律拒绝）。

    返回 ``(目标 URL, dns_pinning)``：pinning 是校验时刻的 DNS 快照，调用方把它
    并入 pin 版 transport（见 ``dns_pinning.py``），建连时直接连 pin 住的 IP，
    不再二次解析——堵住 DNS rebinding TOCTOU（A-P2-2）。

    DNS 解析失败时 pinning 为空 dict：保持 fail-open（见 ``playback_security``
    的事故说明），不重演「DNS 抖动误拦公开 CDN」的生产事故。
    """
    from backend.emby_server.playback_security import validate_url_pinned

    return validate_url_pinned(urljoin(url, location or ""), reject_private=True)


def _range_header(start: int, end: int, total: int) -> dict:
    return {
        "Content-Range": f"bytes {start}-{end}/{total}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }


def _validators_header(path: str) -> dict:
    """给 206 分片响应补 ETag/Last-Modified，让 CDN 能落盘缓存"""
    try:
        etag, last_modified = _file_validators(os.stat(path))
        return {"ETag": etag, "Last-Modified": last_modified}
    except OSError:
        return {}


def serve_file(path: str, request: Request, media_type: str = "video/mp4",
               cache_control: Optional[str] = None) -> StreamingResponse:
    """带 Range 支持的文件流式响应

    ``cache_control``（CDN 预留，第 2/3 层）：分片请求传 ``public, max-age…``，
    让 CDN 边缘缓存热门分片；None 时不发缓存头（行为与升级前一致）。

    FUSE 说明：rclone 挂载（/mnt/mp、/mnt/paul）的读可能因 Drive 配额/网络抖动
    中途失败。若 Range 响应预先声明 Content-Length 而实际字节不足，uvicorn 会抛
    Response content shorter than Content-Length。因此 FUSE 路径的 Range 响应
    不声明 Content-Length（走 chunked），读失败时优雅截断，播放器会重试该分片。
    本地磁盘路径保持原有行为（声明 Content-Length）。
    """
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Media file not found")
    size = os.path.getsize(path)
    fuse = is_fuse_path(path)

    extra_headers = {"Accept-Ranges": "bytes", "X-Accel-Buffering": "no"}
    if cache_control:
        extra_headers["Cache-Control"] = cache_control

    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=dict(extra_headers))

    m = RANGE_RE.match(range_header)
    if not m:
        raise HTTPException(status_code=416, detail="Invalid Range")

    raw_start, raw_end = m.group(1), m.group(2)
    if not raw_start:
        # 后缀形式 ``bytes=-500``：表示「文件最后 500 字节」。旧实现当成 start=0/end=500，
        # 返回的是文件**开头**——播放器探测 MP4 尾部 moov 时拿到错字节，部分会黑屏/ \
        # 播放失败。这里按 RFC 7233 解析成末尾区间。
        suffix = int(raw_end or 0)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Invalid Range")
        start, end = max(0, size - suffix), size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else min(start + CHUNK * 200, size - 1)
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable")

    # 单文件并发 Range 限流：同一文件最多 3 路并发读。
    # （移动端常开 5~7 个并发，7 路同时回源 Drive 会把带宽打满）。
    # 用非阻塞 try_acquire：事件循环里不能调 blocking 的 acquire（会卡住整个
    # 循环）。拿不到名额直接 503，播放器会重试，效果等同排队。
    # 租约 token（r13）：按 lease_id 精确归还，迟到释放会被忽略（ABA 漂移根治）。
    lease = RANGE_LIMITER.try_acquire(path)
    if lease is None:
        raise HTTPException(status_code=503, detail="Server busy, retry later")

    def _guarded_iter():
        # 流量计量（本机文件腿）：数迭代器真正 yield 的字节，迭代
        # 结束（含中途断开/读失败截断）后按归属用户记账一次；计数
        # 失败绝不影响出流。口径与中继腿一致：只数真发出去的字节。
        total = 0
        try:
            for chunk in iter_file():
                total += len(chunk)
                yield chunk
        finally:
            RANGE_LIMITER.release(path, lease)
            if total > 0 and _traffic_uid:
                try:
                    from backend.emby_server import traffic_meter
                    traffic_meter.note_bytes(
                        total, "local", user_id=_traffic_uid,
                        entry_host=_traffic_host)
                except Exception:  # noqa: BLE001 — 计量不拖垮播放
                    pass

    def iter_file():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            retries = 0
            while remaining > 0:
                try:
                    data = f.read(min(READ_CHUNK, remaining))
                except OSError as exc:
                    # FUSE 瞬时抖动：退避重试几次再放弃
                    retries += 1
                    if retries > _FUSE_READ_RETRIES:
                        logger.warning("FUSE 读失败放弃 %s [%d-%d]: %s", path, start, end, exc)
                        break
                    time.sleep(_FUSE_READ_RETRY_DELAY * retries)
                    continue
                if not data:
                    # rclone 放弃重试（如 Drive 403 配额耗尽）后读返回空。
                    # FUSE 下走 chunked（无 Content-Length），这里优雅截断：
                    # 播放器会把这次当成普通网络中断并重试该分片，而不会触发
                    # 服务端的 Response content shorter than Content-Length。
                    if fuse:
                        sent = (end - start + 1) - remaining
                        logger.warning(
                            "FUSE 读提前结束 %s [%d-%d]，已发送 %d/%d 字节",
                            path, start, end, sent, end - start + 1,
                        )
                    break
                retries = 0
                remaining -= len(data)
                yield data

    range_headers = _range_header(start, end, size)
    if fuse:
        # FUSE 路径的 Range 响应不声明 Content-Length，走 chunked，避免中途
        # 读失败时声明长度与实际字节对不上。Content-Range 保留（206 必需）。
        range_headers.pop("Content-Length", None)
    # 流量计量归属在此（同步调用点）先取出闭包带走：迭代器之后在
    # 线程池里消费，ContextVar 不保证跟得过去；取不到就整笔不计。
    try:
        from backend.emby_server import traffic_meter as _traffic_meter
        _traffic_uid = _traffic_meter.current_user_id()
        _traffic_host = _traffic_meter.current_entry_host()
    except Exception:  # noqa: BLE001 — 计量不拖垮播放
        _traffic_uid = None
        _traffic_host = None
    return StreamingResponse(
        _guarded_iter(),
        status_code=206,
        media_type=media_type,
        headers={**range_headers, **_validators_header(path), **extra_headers},
    )


# A-P2-2 DNS pinning 共享缓存（async 路径）：host -> [ip, ...]，TTL 300 秒。
# 只有经 ``_next_redirect`` 校验通过的重定向目标才会写入；每次建连时查。
# 总开关 EMBY_DNS_PINNING 在 transport 层实时检查，关掉即回退到正常 DNS。
from backend.emby_server.dns_pinning import PinningCache as _PinningCache
from backend.emby_server.dns_pinning import dns_pinning_enabled

_PINNING_CACHE = _PinningCache(ttl=300.0)


def _env_flag(name: str, default: bool) -> bool:
    """读布尔型环境变量；非法值记 warning 并回落默认值（fail-closed）"""
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    logger.warning("环境变量 %s=%r 不是合法布尔值，使用默认值 %s", name, raw, default)
    return default


#: 上游连接池上限不在这里：挂载池见 _mount_relay_client（env
#: RELAY_MAX_CONNECTIONS / RELAY_MAX_KEEPALIVE_CONNECTIONS），Drive
#: 池见 _drive_relay_client（env DRIVE_RELAY_MAX_CONNECTIONS /
#: DRIVE_RELAY_MAX_KEEPALIVE_CONNECTIONS），各有独立读取器与默认值。
async def _follow_redirects(
    client,
    start_url: str,
    forward: dict,
    silent: bool = False,
) -> tuple:
    """手动跟随 HTTP 重定向（resolve_drive_final_url 用）。

    每一跳都走 ``_next_redirect`` 内网校验 + ``_redirect_headers`` 跨主机剥凭据；
    中间跳的响应及时 aclose 归还连接池；终态响应**不关**，由调用方消费/关闭。

    返回 ``(terminal_resp, final_url, hops)``。

    ``silent``：False（播放口径）→ 非法重定向目标抛 HTTPException(403)、跳转超限
    抛 HTTPException(502)；True（预热口径）→ 这两种情况都返回 ``(None, current, hops)``，
    调用方静默失败。两种口径下失败时已打开的中间响应都已关闭，无泄漏。
    """
    current = start_url
    hops = 0
    resp = None
    for _ in range(MAX_REDIRECTS + 1):
        resp = await client.send(
            client.build_request("GET", current, headers=forward),
            stream=True,
        )
        location = resp.headers.get("location")
        if resp.status_code not in _REDIRECT_CODES or not location:
            break  # 非跳转码 / 3xx 却没给 Location：当作终态
        try:
            target, _hop_pinning = _next_redirect(current, location)
        except MountError as exc:
            await resp.aclose()
            resp = None
            logger.warning("拒绝代理到重定向目标 %s: %s", location, exc)
            if not silent:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            return (None, current, hops)
        except Exception as exc:
            # 畸形 Location（如 http://[::1）：原来落到外层 except → 502
            await resp.aclose()
            resp = None
            if not silent:
                logger.warning("重定向目标解析失败 %s: %s", location, exc)
                raise HTTPException(status_code=502, detail="源站不可达") from exc
            return (None, current, hops)
        await resp.aclose()
        resp = None
        # A-P2-2：把校验时刻的 DNS 快照存入共享 pin 缓存，下一跳建连直接走
        # pin 住的 IP，不再二次解析（堵 DNS rebinding TOCTOU）。
        # 开关关闭时不写：避免开关重新打开瞬间用上陈旧 pin。
        if dns_pinning_enabled():
            for _pin_host, _pin_ips in _hop_pinning.items():
                _PINNING_CACHE.store(_pin_host, _pin_ips)
        forward = _redirect_headers(forward, current, target)
        current = target
        hops += 1
    else:
        if resp is not None:
            await resp.aclose()
        if not silent:
            raise HTTPException(status_code=502, detail="源站重定向次数过多")
        return (None, current, hops)
    return (resp, current, hops)


# ---------------------------------------------------------------------------
# Drive 腿连接层（2026-11-12 起播拆账优化第 2/3 项）
# ---------------------------------------------------------------------------
# 拆账实测：relay 起播 resolve p50 707–866ms、首字节 gap p50 765ms，
# direct_302 同路径只要 8ms。代码复核定性（亲验）：成本不在
# resolve_drive_final_url（无播放调用方），在中继每请求从 Drive
# API 地址重新跟随跳转链——API 跳转一跳全额往返 + 跳转终点主机
# 轮换、连接池复用不住。本段只服务 Drive 腿（SA 中继 / BYOS /
# resolve 解析），经 _DRIVE_LEG contextvar 注入 serve_mount_relay_
# async；远程挂载腿不设该上下文，客户端与行为逐字不变（红线）。
#
# 三件套：
# 1. 最终 URL 短时缓存（CacheManager playback 域，跨 api/ea 两
#    进程共享）：同 (drive, file) 短窗口内直打上次跟随到的签名
#    最终地址，跳过 API 跳转跳、终点主机固定下来连接才复用得住。
#    签名 URL 自带授权，直打时不带 SA 凭据。窗口短（默认 120s，
#    远小于签名 URL 有效期量级）：配额归属仍在此窗口内回到铸出
#    该 URL 的凭据，与 SA 轮换的错位最多一个窗口；任何失败
#    （传输失败 / 终点 ≥400 / 额度页）立即失效、回退现解析。
# 2. Drive 专用连接池：与挂载池两个实例、参数独立（keepalive
#    30s——播放器分段请求间隔常 >5s，httpx 默认 5s 到期即冷连
#    接）；按 event loop 一份，请求路径上绝不关。
# 3. 熔断：连续传输层失败（建连/池等待/传输异常）达阈值打开，
#    冷却期内回退一次性冷客户端（用完关闭），避免池整体病态
#    （如 pin 住的 IP 失效）时每个请求都在坏池里等超时；上游
#    一旦可达（拿到任何终态响应）立即复位。

_FINAL_URL_CACHE_DOMAIN = "playback"


def _drive_final_url_cache_ttl() -> int:
    """最终 URL 缓存 TTL（秒）：env DRIVE_RELAY_FINAL_URL_CACHE_
    TTL_SEC，默认 120，钳 [0, 600]，0 = 关缓存、非法回默认。"""
    raw = os.getenv("DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC", "")
    if not str(raw).strip():
        return 120
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 DRIVE_RELAY_FINAL_URL_CACHE_TTL_SEC=%r "
                       "不是整数，使用默认值 120", raw)
        return 120
    return min(max(value, 0), 600)


def _final_url_cache_key(scope: str) -> str:
    return f"drive_final_url:{scope}"


def drive_final_url_cache_get(scope: str) -> Optional[str]:
    """读最终 URL 缓存；未启用/未命中/缓存不可用一律 None。永不抛。"""
    if not scope or _drive_final_url_cache_ttl() <= 0:
        return None
    try:
        from backend.database import CacheManager
        value = CacheManager.get(_final_url_cache_key(scope),
                                 domain=_FINAL_URL_CACHE_DOMAIN)
    except Exception:  # noqa: BLE001 — 缓存读不到按未命中
        return None
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def drive_final_url_cache_put(scope: str, url: str) -> None:
    """写最终 URL 缓存（只许成功跟随的终点）。失败自吞。永不抛。"""
    ttl = _drive_final_url_cache_ttl()
    if not scope or not url or ttl <= 0:
        return
    try:
        from backend.database import CacheManager
        CacheManager.set(_final_url_cache_key(scope), url, ttl,
                         domain=_FINAL_URL_CACHE_DOMAIN)
    except Exception:  # noqa: BLE001 — 写不进只丢这次复用机会
        pass


def drive_final_url_cache_invalidate(scope: str) -> None:
    """失效最终 URL 缓存（终点失败时回退现解析的收口）。永不抛。"""
    if not scope:
        return
    try:
        from backend.database import CacheManager
        CacheManager.delete(_final_url_cache_key(scope),
                            domain=_FINAL_URL_CACHE_DOMAIN)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 续段请求快速通道的解析结果缓存（2026-11-17 网页播放中继优化）
# ---------------------------------------------------------------------------
# 与最终 URL 缓存同一套机制（CacheManager playback 域），存的不是
# 单个 URL 而是 video_stream 首次全链通过后的「选路结果」描述符
# （JSON 串）：命中后 api.video_stream 跳过全链重算、只做便宜
# 校验（play_sign 验签 / active 绑定关系）直接出流。键含用户
# 维度——描述符里有本人令牌/本人副本的解析结果，严禁跨用户串话。
# TTL 取小者：env STREAM_RESOLVE_CACHE_TTL_SEC（默认 240、钳
# [0, 600]、0 = 关）与签发本次播放的 play_sign 剩余有效期；描述符
# 内再嵌一份绝对到期时刻，读时双保险。缓存的只是解析结果、不是
# 授权本身：门禁仍在首次请求全量执行（口径见 api._try_fastpath_
# relay），命中出流失败立即失效、回落全链。

_STREAM_RESOLVE_KINDS = ("drive_final", "drive_token", "drive_sa")

#: 描述符最小存活门槛（秒）：剩余有效期低于此值不值得存（存了
#: 也赶不上几次续段请求，反而多一份过期边缘的脏数据）。
_STREAM_RESOLVE_MIN_TTL = 10


def _stream_resolve_cache_ttl() -> int:
    """解析结果缓存 TTL（秒）：env STREAM_RESOLVE_CACHE_TTL_SEC，
    默认 240，钳 [0, 600]，0 = 关缓存、非法回默认。"""
    raw = os.getenv("STREAM_RESOLVE_CACHE_TTL_SEC", "")
    if not str(raw).strip():
        return 240
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 STREAM_RESOLVE_CACHE_TTL_SEC=%r "
                       "不是整数，使用默认值 240", raw)
        return 240
    return min(max(value, 0), 600)


def _stream_resolve_cache_key(user_id, item_id: str, variant: str) -> str:
    return f"stream_resolve:{user_id}:{item_id}:{variant}"


def stream_resolve_cache_get(user_id, item_id: str,
                             variant: str) -> Optional[dict]:
    """读解析结果描述符；未启用/未命中/内容不合规一律 None。永不抛。"""
    if user_id is None or not item_id or not variant:
        return None
    if _stream_resolve_cache_ttl() <= 0:
        return None
    try:
        from backend.database import CacheManager
        raw = CacheManager.get(
            _stream_resolve_cache_key(user_id, item_id, variant),
            domain=_FINAL_URL_CACHE_DOMAIN)
    except Exception:  # noqa: BLE001 — 缓存读不到按未命中
        return None
    if not isinstance(raw, str) or not raw.startswith("{"):
        return None
    try:
        import json
        desc = json.loads(raw)
    except Exception:  # noqa: BLE001 — 脏数据按未命中
        return None
    if not isinstance(desc, dict) \
            or desc.get("kind") not in _STREAM_RESOLVE_KINDS:
        return None
    try:
        if float(desc.get("exp") or 0) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    if desc["kind"] == "drive_final" \
            and not str(desc.get("final_url") or "").startswith(
                ("http://", "https://")):
        return None
    if desc["kind"] in ("drive_token", "drive_sa") \
            and (not desc.get("drive_id") or not desc.get("file_id")):
        return None
    if desc["kind"] == "drive_token" and not desc.get("access_token"):
        return None
    return desc


def stream_resolve_cache_put(user_id, item_id: str, variant: str,
                             descriptor: dict,
                             sign_cap_seconds: Optional[float] = None
                             ) -> bool:
    """写解析结果描述符。TTL = min(配置 TTL, 签名剩余有效期)，
    低于最小门槛不存。失败自吞。永不抛。返回 True 表示已落存
    （供调用方打 store 观测日志），未存/失败一律 False。"""
    ttl = _stream_resolve_cache_ttl()
    if user_id is None or not item_id or not variant or ttl <= 0:
        return False
    if not isinstance(descriptor, dict) \
            or descriptor.get("kind") not in _STREAM_RESOLVE_KINDS:
        return False
    if sign_cap_seconds is not None:
        ttl = min(ttl, int(sign_cap_seconds))
    if ttl < _STREAM_RESOLVE_MIN_TTL:
        return False
    try:
        import json
        payload = dict(descriptor)
        payload["exp"] = time.time() + ttl
        from backend.database import CacheManager
        CacheManager.set(
            _stream_resolve_cache_key(user_id, item_id, variant),
            json.dumps(payload), ttl, domain=_FINAL_URL_CACHE_DOMAIN)
        return True
    except Exception:  # noqa: BLE001 — 写不进只丢这次复用机会
        return False


def stream_resolve_cache_invalidate(user_id, item_id: str,
                                    variant: str) -> None:
    """失效解析结果描述符（命中出流失败时回落全链的收口）。永不抛。"""
    if user_id is None or not item_id or not variant:
        return
    try:
        from backend.database import CacheManager
        CacheManager.delete(
            _stream_resolve_cache_key(user_id, item_id, variant),
            domain=_FINAL_URL_CACHE_DOMAIN)
    except Exception:  # noqa: BLE001
        pass


#: 本次中继的最终 URL capture 盒（调用方经 capture_relay_final_url
#: 设置）：serve_mount_relay_async 跟随成功后把终点 URL 写进盒里，
#: 调用方据此为快速通道落 drive_final 描述符。无盒时中继行为逐字
#: 不变（盒只是旁路观测，不参与出流决策）。
_RELAY_FINAL_CAPTURE: "contextvars.ContextVar[Optional[dict]]" = (
    contextvars.ContextVar("aetrix_relay_final_capture", default=None))


@contextlib.contextmanager
def capture_relay_final_url(box: dict):
    """在 with 块内开启最终 URL capture：块内 serve_drive_relay_
    async 的成功跟随会把终点 URL 写进 ``box["final_url"]``。"""
    token = _RELAY_FINAL_CAPTURE.set(box)
    try:
        yield box
    finally:
        _RELAY_FINAL_CAPTURE.reset(token)


def _resolve_cache_scope(api_url: str, headers) -> str:
    """resolve 侧缓存键：API 地址 + 凭据指纹（不同凭据不许串用）。"""
    auth = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == "authorization":
            auth = str(value or "")
            break
    digest = hashlib.sha256(
        f"{api_url}\n{auth}".encode("utf-8")).hexdigest()
    return f"resolve:{digest}"


#: Drive 池熔断状态（进程内）：连续传输失败计数 + 打开到期时刻。
_DRIVE_POOL_LOCK = threading.Lock()
_DRIVE_POOL_STATE = {"failures": 0, "open_until": 0.0}


def _drive_pool_breaker_threshold() -> int:
    raw = os.getenv("DRIVE_RELAY_POOL_BREAKER_THRESHOLD", "")
    try:
        value = int(str(raw).strip()) if str(raw).strip() else 3
    except (TypeError, ValueError):
        value = 3
    return max(1, value)


def _drive_pool_breaker_cooldown() -> float:
    raw = os.getenv("DRIVE_RELAY_POOL_BREAKER_COOLDOWN_SEC", "")
    try:
        value = float(str(raw).strip()) if str(raw).strip() else 15.0
    except (TypeError, ValueError):
        value = 15.0
    return max(1.0, value)


def drive_pool_breaker_open() -> bool:
    """Drive 池熔断是否打开（冷却期内）。"""
    with _DRIVE_POOL_LOCK:
        return time.monotonic() < _DRIVE_POOL_STATE["open_until"]


def _drive_pool_note_reached() -> None:
    """上游给出终态响应（任何状态码）：连接层是通的，计数复位。"""
    with _DRIVE_POOL_LOCK:
        _DRIVE_POOL_STATE["failures"] = 0
        _DRIVE_POOL_STATE["open_until"] = 0.0


def _drive_pool_note_transport_failure() -> None:
    """一次传输层失败：计数 +1，达阈值打开熔断（冷却期）。"""
    with _DRIVE_POOL_LOCK:
        _DRIVE_POOL_STATE["failures"] += 1
        if _DRIVE_POOL_STATE["failures"] >= _drive_pool_breaker_threshold():
            _DRIVE_POOL_STATE["open_until"] = (
                time.monotonic() + _drive_pool_breaker_cooldown())
            logger.warning(
                "Drive 中继连接池熔断打开：连续传输失败 %d 次，"
                "冷却 %.0fs 内回退冷连接",
                _DRIVE_POOL_STATE["failures"],
                _drive_pool_breaker_cooldown())


def _pool_limit_value(name: str, raw: str, default: int,
                      lo: int, hi: int) -> int:
    """池上限 env 值解析：非法回默认并告警，合法钳 [lo, hi]。"""
    if not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 %s=%r 不是整数，使用默认值 %d",
                       name, raw, default)
        return default
    return min(max(value, lo), hi)


def _mount_pool_limits() -> tuple:
    """挂载中继池上限 (max_connections, max_keepalive_connections)。

    env RELAY_MAX_CONNECTIONS（默认 32，钳 [1, 512]）/
    RELAY_MAX_KEEPALIVE_CONNECTIONS（默认 16，钳 [0, 512] 且不超
    max_connections）。共享池太小会让并发拉流互相等池（PoolTimeout
    的既有 503 口径不变）。
    """
    max_conn = _pool_limit_value(
        "RELAY_MAX_CONNECTIONS",
        os.getenv("RELAY_MAX_CONNECTIONS", ""), 32, 1, 512)
    keepalive = _pool_limit_value(
        "RELAY_MAX_KEEPALIVE_CONNECTIONS",
        os.getenv("RELAY_MAX_KEEPALIVE_CONNECTIONS", ""), 16, 0, 512)
    return max_conn, min(keepalive, max_conn)


def _drive_pool_limits() -> tuple:
    """Drive 中继池上限：env DRIVE_RELAY_MAX_CONNECTIONS（默认 32）/
    DRIVE_RELAY_MAX_KEEPALIVE_CONNECTIONS（默认 16），钳制口径同
    挂载池，与挂载池独立演进。"""
    max_conn = _pool_limit_value(
        "DRIVE_RELAY_MAX_CONNECTIONS",
        os.getenv("DRIVE_RELAY_MAX_CONNECTIONS", ""), 32, 1, 512)
    keepalive = _pool_limit_value(
        "DRIVE_RELAY_MAX_KEEPALIVE_CONNECTIONS",
        os.getenv("DRIVE_RELAY_MAX_KEEPALIVE_CONNECTIONS", ""), 16, 0, 512)
    return max_conn, min(keepalive, max_conn)


#: Drive 中继客户端按 event loop 共享（与挂载池分开的独立一份）。
_DRIVE_RELAY_CLIENTS: dict = {}


def _drive_relay_client() -> "httpx.AsyncClient":
    """Drive 主腿中继的共享客户端（按 event loop 一份）。

    与挂载池分开：挂载腿的参数与行为一个字不许动（红线），Drive
    腿自己的复用参数（keepalive_expiry=30s 覆盖播放器分段间隔）
    独立演进。DNS pinning / 超时与挂载池同口径。
    """
    import asyncio

    import httpx

    from backend.emby_server.dns_pinning import PinnedAsyncHTTPTransport

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 无运行中 loop：退回按次创建，调用方自负关闭
        loop = None
    if loop is not None:
        for _dead in [l for l in _DRIVE_RELAY_CLIENTS if l.is_closed()]:
            _DRIVE_RELAY_CLIENTS.pop(_dead, None)
        client = _DRIVE_RELAY_CLIENTS.get(loop)
        if client is not None and not client.is_closed:
            return client
    _dmax, _dkeep = _drive_pool_limits()
    client = httpx.AsyncClient(
        transport=PinnedAsyncHTTPTransport(
            _PINNING_CACHE.lookup,
            limits=httpx.Limits(max_connections=_dmax,
                                max_keepalive_connections=_dkeep,
                                keepalive_expiry=30.0),
        ),
        timeout=httpx.Timeout(
            connect=_MOUNT_RELAY_TIMEOUT_CONNECT,
            read=_MOUNT_RELAY_TIMEOUT_READ,
            write=30.0,
            pool=10.0,
        ),
        follow_redirects=False,
    )
    if loop is not None:
        _DRIVE_RELAY_CLIENTS[loop] = client
    return client


def _acquire_drive_client():
    """取 Drive 腿客户端：熔断关 → (池客户端, False)；熔断开 →
    (一次性冷客户端, True)，一次性客户端由使用方负责关闭。"""
    if drive_pool_breaker_open():
        return _drive_redirect_client(), True
    return _drive_relay_client(), False


class _DriveLegScope:
    """一次 Drive 中继的连接层上下文（serve_drive_relay_async 设置）。

    serve_mount_relay_async 读到本上下文才启用 Drive 池/最终 URL
    缓存/熔断记账；挂载调用不设上下文，行为与从前逐字一致。
    cache_key 为 None 时不落最终 URL 缓存（BYOS 用户令牌腿：签名
    URL 的铸出凭据是用户本人令牌，跨请求复用会把流量钉在铸出
    时刻的授权上下文上，该腿请求量小、维持每次现跟随）。

    capture（可选）：调用方给的 dict，跟随成功后回填终点 URL
    （capture["final_url"]），供 video_stream 快速通道落
    drive_final 描述符；不给时无任何回填动作。

    hot_identity（可选）：热缓存文件身份（relay_hot_cache 用，
    ``drive:{drive_id}:{file_id}``）；serve_drive_relay_async 对
    SA 腿与令牌腿都设（身份是文件、不是凭据，字节只经已授权的
    serve 路径出）。不设时热缓存完全不介入，行为逐字不变。
    """

    __slots__ = ("cache_key", "capture", "hot_identity")

    def __init__(self, cache_key: Optional[str] = None,
                 capture: Optional[dict] = None,
                 hot_identity: Optional[str] = None):
        self.cache_key = cache_key
        self.capture = capture
        self.hot_identity = hot_identity

    def acquire(self):
        return _acquire_drive_client()


#: 当前任务的 Drive 腿上下文（无 Drive 腿在途时为 None）。
_DRIVE_LEG: "contextvars.ContextVar[Optional[_DriveLegScope]]" = (
    contextvars.ContextVar("aetrix_drive_leg", default=None))


def _drive_redirect_client() -> "httpx.AsyncClient":
    """一次性小客户端（resolve 冷路 / Drive 池熔断回退共用）。

    按次创建、用完关闭。保留 DNS pinning 校验（堵 DNS rebinding
    TOCTOU）与 follow_redirects=False（跳转由本模块自己逐跳校验）。
    """
    import httpx

    from backend.emby_server.dns_pinning import PinnedAsyncHTTPTransport

    return httpx.AsyncClient(
        transport=PinnedAsyncHTTPTransport(
            _PINNING_CACHE.lookup,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        ),
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=3.0),
        follow_redirects=False,
    )


def _try_drive_api_rate() -> None:
    """Drive 腿实发前过全局限速（drive_changes 同一令牌桶，横切只许一套）。

    全局限速收口（2026-11-22 审计 P5）：中继/解析此前全部绕行。
    本模块在事件循环里跑，只用快速失败型 try_acquire_api_token
    （refresh=False：事件循环里不做配置 DB 读，QPS 同步靠同进程
    同步路径兜底）——返回值有意忽略：拿不到令牌照常直发，限速防
    多路径叠加打爆单 SA，不是播放门禁，绝不阻塞/拒绝取流；
    限速器自身异常同样吞掉直发。计数口径：每次中继/解析请求
    记一次（重定向跳数不另计）。挂载腿（非 Drive）不调本函数。
    """
    try:
        from backend.emby_server import drive_changes as _dc_st_rl
        _dc_st_rl.try_acquire_api_token(refresh=False)
    except Exception:  # noqa: BLE001 — 限速接线永不挡播放热路径
        pass


async def resolve_drive_final_url(
    url: str,
    headers: dict | None = None,
) -> str | None:
    """解析 Drive 最终下载地址（SEAM-1：中继全删后的保守保留）。

    原始 URL → drive_api_media_url 转 API 地址 → 查最终 URL 短时
    缓存（键含凭据指纹，不同凭据不串用）→ 未命中则手动跟随跳转
    → 成功（200/206）写缓存。

    返回最终 URL；任何失败返回 None（不抛异常，调用方静默）。
    客户端走 Drive 池（熔断时一次性冷客户端、用完关）；中间响
    应及时关闭。注：播放热路径并不经本函数（中继在
    serve_mount_relay_async 内直接跟随），本函数的缓存与池和中
    继腿共用同一实现（见 _DRIVE_LEG 一段）。
    """
    followed = False
    try:
        # 1. uc 直链转 Drive API 地址
        try:
            from backend.emby_server.drive_auth import drive_api_media_url
            api_url = drive_api_media_url(url)
        except Exception:
            api_url = url
        if not api_url:
            return None

        # 2. 最终 URL 短时缓存：命中直接回（零网络往返）
        scope = _resolve_cache_scope(api_url, headers)
        cached = drive_final_url_cache_get(scope)
        if cached:
            return cached

        # 3. 手动跟随跳转（Drive 池客户端；熔断时冷客户端用完关）
        client, one_shot = _acquire_drive_client()

        forward = {k: v for k, v in (headers or {}).items()}
        forward.pop("X-Pickcode", None)
        forward.setdefault("User-Agent", REMOTE_UA)

        _try_drive_api_rate()
        try:
            resp, current, hops = await _follow_redirects(
                client, api_url, forward, silent=True)
            followed = True
            _drive_pool_note_reached()
            if resp is None:
                return None  # 非法重定向目标 / 跳转超限：静默失败
            try:
                terminal_status = resp.status_code
                if terminal_status in (403, 404, 410):
                    if terminal_status == 403 and _drive_alt_media_file_id(current):
                        try:
                            _pbody = await resp.aread()
                        except Exception:
                            _pbody = b""
                        _preason, _pmsg = _drive_403_error_reason(_pbody[:4096])
                        logger.warning("Drive 跳转解析 403 file_id=%s reason=%s msg=%.120s",
                                       _drive_alt_media_file_id(current),
                                       _preason or "-", _pmsg or "-")
                    return None
                if terminal_status in (200, 206) and current != api_url:
                    drive_final_url_cache_put(scope, current)
                return current
            finally:
                await resp.aclose()
        finally:
            if one_shot:
                await client.aclose()
    except Exception:
        # 调用方静默：任何异常都不抛。跟随本身炸了算一次传输失败
        # （已拿到终态后的收尾异常不算，followed 已置位）。
        if not followed:
            _drive_pool_note_transport_failure()
        return None


# ---------------------------------------------------------------------------
# 远程挂载专用中继（2026-10-17：中继全删 P1 回归修复，用户拍板）
# ---------------------------------------------------------------------------
# 只服务远程挂载条目（115 / rclone 等 ``mount://`` 路径解析出的
# ``kind="url"`` 目标）：这类直链依赖挂载凭据（Cookie/令牌）与特定 UA，
# 下发给客户端即死，必须由服务端带头代理转发。调用方（api.video_stream）
# 保证：Drive 条目一律先走 302，**不许**经由这里。
#
# 与已删除的 ``serve_remote_async`` 的区别（刻意不带回来）：
# - 无共享中继连接池（``RELAY_*`` 已删）：每请求一个独立小客户端，用完即关；
#   远程挂载并发量小，这点开销可接受。
# - 无塌速重连 / Singleflight / 分块共享：用户要求全删，不复活。
# - 无 Drive 特有逻辑：uc→alt=media 转换、配额负缓存、403 换 SA 重试、
#   401 失效标记——这些是 Drive 链路的，远程挂载用不上。
# - 保留：凭据头转发（``X-Pickcode`` 是内部头，不转发给上游）、Range 透传、
#   手动跟随跳转（``_follow_redirects``：逐跳内网校验 + 跨主机剥凭据 +
#   DNS pinning）、115 直链过期清缓存（403/404/410 + X-Pickcode）、
#   响应头透传、流式转发。

_MOUNT_RELAY_TIMEOUT_CONNECT = 10.0
_MOUNT_RELAY_TIMEOUT_READ = 30.0


def _mount_relay_stall_timeout() -> float:
    """客户端停读多久后放掉上游连接（秒）；``MOUNT_RELAY_STALL_TIMEOUT_SEC``。

    读超时只管「上游不给数据」，管不到「客户端不收数据」：播放器拖动/重开
    时弃用旧连接却不 close（Infuse、AVPlayer 系实测每会话残留 3-4 条），
    中继停在 yield 上等 send 排空，上游连接与共享池名额被占到客户端退出。
    缺省 60 秒（正常播放每块 1MB 的消费间隔远小于此；暂停超过阈值后恢复，
    播放器按 Range 重连续播）；0 关闭，非法值回落缺省并告警。
    """
    raw = os.getenv("MOUNT_RELAY_STALL_TIMEOUT_SEC", "")
    if not str(raw).strip():
        return 60.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("环境变量 MOUNT_RELAY_STALL_TIMEOUT_SEC=%r 不是数字，使用默认值 60", raw)
        return 60.0
    return max(0.0, value)


async def _release_upstream_when_stalled(source, resp, url: str, timeout: float):
    """包一层流：消费方（客户端）停读超过 ``timeout`` 秒就关上游响应。

    只在停在 yield 上（等客户端收）时计时；上游慢由读超时负责。关上游后
    本流结束——客户端若之后恢复读取，会收到截断的响应并按 Range 重连。
    """
    if timeout <= 0:
        async for chunk in source:
            yield chunk
        return
    loop = asyncio.get_running_loop()
    waiting_since: Optional[float] = None
    stalled = False

    async def _watch():
        nonlocal stalled
        while True:
            await asyncio.sleep(min(timeout / 4, 5.0))
            if waiting_since is not None and loop.time() - waiting_since >= timeout:
                stalled = True
                logger.info("远程挂载客户端停读 %.0fs，释放上游连接 %s",
                            timeout, url.split("?")[0])
                try:
                    await resp.aclose()
                except Exception:
                    pass
                return

    watcher = asyncio.create_task(_watch())
    try:
        async for chunk in source:
            waiting_since = loop.time()
            yield chunk
            waiting_since = None
            if stalled:
                return
    finally:
        watcher.cancel()
        await source.aclose()


async def _pt_first_byte_body(source, pt, headers_perf):
    """[play-timing] 首字节打点包装（只打点、不改流）。

    首块交付消费端时补齐分段并输出汇总行：``rcd_first_chunk``＝上游
    响应头到达（headers_perf）→首块的时长，``first_byte``＝累计点，
    此后 ``total`` 即首字节时刻。流在首块前结束/出错/被关时在
    finally 里补出汇总行，保证每次起播恰有一行 [play-timing]
    stream（无首块就不打 first_byte，不假装）。块内容/顺序/异常
    逐字透传，本包装不吞任何异常。
    """
    _seen = False
    try:
        async for chunk in source:
            if not _seen:
                _seen = True
                if headers_perf is not None:
                    pt.span("rcd_first_chunk", headers_perf)
                pt.mark("first_byte")
                pt.done()
            yield chunk
    finally:
        if not _seen:
            pt.done()


def _relay_prefetch_chunks() -> int:
    """中继预读深度（块数，块 = READ_CHUNK 1MB）：env
    STREAM_RELAY_PREFETCH_CHUNKS，默认 3，钳 [0, 8]，0 = 关预读、
    非法回默认。只对 Drive 中继腿生效（见 serve_mount_relay_async）。"""
    raw = os.getenv("STREAM_RELAY_PREFETCH_CHUNKS", "")
    if not str(raw).strip():
        return 3
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 STREAM_RELAY_PREFETCH_CHUNKS=%r "
                       "不是整数，使用默认值 3", raw)
        return 3
    return min(max(value, 0), 8)


async def _prefetch_stream_chunks(source, chunks_ahead: int):
    """有界预读包装（2026-11-17 中继取流优化，仅 Drive 中继腿用）。

    生产者任务提前从 ``source`` 读块进小队列（上限 chunks_ahead
    块，另加生产者手里在途的一块），消费端逐块取——消费端写客户端
    的间隙里上游不空转，上游抖动被队列里的存货抹平。语义与直通
    迭代逐字一致：块顺序/内容不变；源的异常在原位置（已排队块
    之后）向消费端重抛；消费端提前结束（客户端断开 / 停读看门狗
    关流）时生产者被取消、上游读取随源迭代器关闭而停止，不留
    后台偷读。无新增线程：生产者就是事件循环里的一个 task。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, int(chunks_ahead)))

    async def _produce():
        try:
            async for chunk in source:
                await queue.put((True, chunk))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — 转交消费端原位重抛
            await queue.put((False, exc))
            return
        await queue.put(None)

    task = asyncio.create_task(_produce())
    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            ok, payload = item
            if not ok:
                raise payload
            yield payload
    finally:
        task.cancel()
        _aclose = getattr(source, "aclose", None)
        if _aclose is not None:
            try:
                await _aclose()
            except Exception:  # noqa: BLE001 — 关源失败不许掩盖主流
                pass


#: 中继客户端按 event loop 共享（2026-10-30 起播/拖动提速）：旧口径
#: 「按次创建、用完关闭」让每次拉流/拖动都重付一遍连接建立（VPS 到
#: 谷歌实测单次 TCP+TLS 约 0.11–0.16s；生产 stream 首字节 1.6–2.2s
#: 里中继段约 0.87s 有相当部分耗在新建连接上）。共享只动连接层：
#: DNS pinning 仍在 transport 层（连接建立时校验）、跳转仍由
#: _follow_redirects 逐跳校验、凭据头仍按请求传——出法与顺序零
#: 变化。注意 httpx 0.28 关 client 会连 transport 一起关，共享
#: client 绝不许在请求路径上 aclose，只关响应把连接归还池中。
_MOUNT_RELAY_CLIENTS: dict = {}


def _mount_relay_client() -> "httpx.AsyncClient":
    """远程挂载中继的共享客户端（按 event loop 一份）。

    池上限较旧按次口径放大（8→32）：旧口径每请求独享一份池，
    共享后全进程中继共用一份，太小会让并发拉流互相等池
    （PoolTimeout 的既有 503 口径不变）。
    """
    import asyncio

    import httpx

    from backend.emby_server.dns_pinning import PinnedAsyncHTTPTransport

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 无运行中 loop：退回按次创建，调用方自负关闭
        loop = None
    if loop is not None:
        for _dead in [l for l in _MOUNT_RELAY_CLIENTS if l.is_closed()]:
            _MOUNT_RELAY_CLIENTS.pop(_dead, None)
        client = _MOUNT_RELAY_CLIENTS.get(loop)
        if client is not None and not client.is_closed:
            return client
    _mmax, _mkeep = _mount_pool_limits()
    client = httpx.AsyncClient(
        transport=PinnedAsyncHTTPTransport(
            _PINNING_CACHE.lookup,
            limits=httpx.Limits(max_connections=_mmax,
                                max_keepalive_connections=_mkeep,
                                keepalive_expiry=30.0),
        ),
        timeout=httpx.Timeout(
            connect=_MOUNT_RELAY_TIMEOUT_CONNECT,
            read=_MOUNT_RELAY_TIMEOUT_READ,
            write=30.0,
            pool=10.0,
        ),
        follow_redirects=False,
    )
    if loop is not None:
        _MOUNT_RELAY_CLIENTS[loop] = client
    return client


# ---------------------------------------------------------------------------
# 中继热缓存接线（2026-11-22 智能中继＋热缓存阶段 1；存储本体在
# relay_hot_cache，本节只做 serve 路径上的命中出流 / 写穿 / 预读）
# ---------------------------------------------------------------------------
# 只对带 hot_identity 的腿生效。身份来源两条：Drive 腿经
# _DriveLegScope（serve_drive_relay_async 设）；挂载腿经
# serve_mount_relay_async 的 hot_identity 参数（2026-11-23 二期：
# api.video_stream 对有 drive_file_id 的挂载条目传 mount:{fid}）：
# - 全命中：开流前查缓存，请求区间被分段完全覆盖 → 本地出字节、
#   不回源；
# - 未全命中：现行透传逐字不变，只在流上旁路写穿——流过的完整
#   分段投递给缓存写线程落盘，下次同区间才转全命中；
# - 跨请求预读：一次出流后，后台把请求区间之后的 N 个分段提前
#   读进缓存（有界并发、已缓存/在途去重、失败自吞）。
# 任何缓存异常一律 fail-open：回现行透传，播放不受影响。


def _try_hot_cache_response(identity, range_header, media_type, pt):
    """热缓存全命中出流：命中返回 StreamingResponse，未命中/任何
    问题返回 None（调用方走现行透传，逐字不变）。"""
    opened = None
    _pt_t0 = time.perf_counter() if pt is not None else None
    try:
        from backend.emby_server import relay_hot_cache as _hc
        if not _hc.enabled() or _hc.max_bytes() <= 0:
            return None
        meta = _hc.get_meta(identity)
        if not meta:
            _hc.note_miss()
            return None
        rng = _hc.parse_range(range_header, int(meta["total_size"]))
        if rng is None:
            return None
        start, end = rng
        opened = _hc.open_segments(identity, meta, start, end)
        if opened is None:
            _hc.note_miss()
            return None
        total = int(meta["total_size"])
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "X-Accel-Buffering": "no",
        }
        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        if meta.get("etag"):
            headers["ETag"] = meta["etag"]
        if meta.get("last_modified"):
            headers["Last-Modified"] = meta["last_modified"]
        _hc.note_hit(end - start + 1)
        logger.info("relay_hot_cache 全命中出流 identity=%s "
                    "range=%d-%d bytes=%d",
                    identity, start, end, end - start + 1)
        if pt is not None:
            # 命中路径的热缓存查询耗时在此补记（未命中路径由调用方在
            # 本函数返回 None 后补记同名字段，口径一致）。
            pt.span("cache_lookup", _pt_t0)
            pt.mark("ttfb")
            pt.done()

        async def _hit_body():
            try:
                for fobj, length in opened:
                    remaining = length
                    while remaining > 0:
                        chunk = await asyncio.to_thread(
                            fobj.read, min(remaining, READ_CHUNK))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            finally:
                for fobj, _length in opened:
                    try:
                        fobj.close()
                    except Exception:  # noqa: BLE001
                        pass

        return StreamingResponse(
            _hit_body(),
            status_code=206 if range_header else 200,
            media_type=meta.get("content_type") or media_type,
            headers=headers)
    except Exception:  # noqa: BLE001 — 缓存层任何异常都不许挡播放
        logger.warning("relay_hot_cache 命中路径异常，回现行透传",
                       exc_info=True)
        if opened:
            for fobj, _length in opened:
                try:
                    fobj.close()
                except Exception:  # noqa: BLE001
                    pass
        return None


def _hot_cache_tee_plan(identity, resp):
    """按上游响应头算写穿计划：定出流起始绝对偏移与文件总长，并把
    校验器登记进热缓存（大小/校验器变化即整身份失效旧分段）。
    算不出/未启用/登记失败回 None（本次不写穿）。永不抛。"""
    try:
        from backend.emby_server import relay_hot_cache as _hc
        if not _hc.enabled() or _hc.max_bytes() <= 0:
            return None
        # 异常 HTML 页（额度页之外的错误页）绝不写穿：把错误页当
        # 文件字节落盘会污染缓存，还会因总长不符连带失效好分段。
        if (resp.headers.get("content-type") or "") \
                .lower().startswith("text/html"):
            return None
        total = None
        start = 0
        if resp.status_code == 206:
            cr = (resp.headers.get("content-range") or "").strip()
            m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr)
            if not m:
                return None
            start = int(m.group(1))
            total = int(m.group(3))
        elif resp.status_code == 200:
            cl = resp.headers.get("content-length")
            if not cl:
                return None
            total = int(str(cl).strip())
            start = 0
        else:
            return None
        if total <= 0:
            return None
        meta = _hc.note_validators(
            identity, total, resp.headers.get("etag"),
            resp.headers.get("last-modified"),
            resp.headers.get("content-type"))
        if meta is None:
            return None
        # 代际绑定：本流字节属于登记这一刻的校验器；流期间身份被
        # 失效（文件被换）→ 提交时按代际丢弃，不许落进新一代。
        return {"identity": identity, "start": start, "total": total,
                "gen": _hc.current_generation(identity)}
    except Exception:  # noqa: BLE001 — 算不出就不缓存，播放照常
        return None


async def _hot_cache_tee(source, plan):
    """写穿旁路：字节原样透传，同时按绝对偏移攒分段——流完整覆盖
    的分段投递落盘（从段中间进入的首段不完整、永不投递）。记账
    异常立即停记账（防偏移脱轨写错字节），透传本身不受影响。"""
    from backend.emby_server import relay_hot_cache as _hc
    seg = _hc.SEGMENT_SIZE
    total = plan["total"]
    pos = plan["start"]
    buf = bytearray()
    buf_seg = pos // seg
    buf_from = pos - buf_seg * seg
    dead = False
    async for chunk in source:
        yield chunk
        if dead or not chunk:
            continue
        try:
            view = memoryview(chunk)
            while len(view):
                idx = pos // seg
                if idx != buf_seg:
                    buf = bytearray()
                    buf_seg = idx
                    buf_from = 0
                in_seg = pos - idx * seg
                exp_len = _hc.segment_expected_len(total, idx)
                if exp_len <= 0:
                    dead = True
                    break
                take = min(exp_len - in_seg, len(view))
                buf += view[:take]
                view = view[take:]
                pos += take
                if in_seg + take == exp_len:
                    if buf_from == 0 and len(buf) == exp_len:
                        _hc.submit_segment(
                            plan["identity"], idx, bytes(buf), total,
                            gen=plan.get("gen"))
                    buf = bytearray()
        except Exception:  # noqa: BLE001 — 记账坏掉就停，字节已透传
            dead = True


#: 跨请求预读的在途任务与分段去重集（进程内）：任务上限防突发
#: 请求把预读放大成上游洪水；信号量限并发，绝不占满连接池。
_HOT_PREFETCH_TASKS: set = set()
_HOT_PREFETCH_INFLIGHT: set = set()
_HOT_PREFETCH_SEMS: dict = {}
_HOT_PREFETCH_MAX_TASKS = 16
_HOT_PREFETCH_CONCURRENCY = 2


def _hot_prefetch_sem() -> "asyncio.Semaphore":
    loop = asyncio.get_running_loop()
    sem = _HOT_PREFETCH_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_HOT_PREFETCH_CONCURRENCY)
        _HOT_PREFETCH_SEMS[loop] = sem
    return sem


async def _hot_prefetch_run(identity, range_header, fetcher) -> None:
    """预读主体：把请求区间之后 N 个未缓存分段取回落盘。

    fetcher(seg_start, seg_end) -> bytes | None 由调用方按腿构造。
    已缓存/在途的分段跳过（去重）；任何失败自吞，绝不外抛。
    """
    try:
        from backend.emby_server import relay_hot_cache as _hc
        meta = _hc.get_meta(identity)
        if not meta:
            return
        gen = _hc.current_generation(identity)
        total = int(meta["total_size"])
        rng = _hc.parse_range(range_header, total)
        if rng is None:
            return
        seg = _hc.SEGMENT_SIZE
        last_seg = (total - 1) // seg
        cur_last = rng[1] // seg
        n = _hc.prefetch_segments()
        sem = _hot_prefetch_sem()
        for idx in range(cur_last + 1,
                         min(last_seg, cur_last + n) + 1):
            key = (identity, idx)
            if key in _HOT_PREFETCH_INFLIGHT:
                continue
            if _hc.segment_present(identity, meta, idx):
                continue
            _HOT_PREFETCH_INFLIGHT.add(key)
            try:
                s = idx * seg
                e = min(s + seg, total) - 1
                async with sem:
                    data = await fetcher(s, e)
                if data and len(data) == e - s + 1:
                    if _hc.submit_segment(identity, idx, data, total,
                                          gen=gen):
                        _hc.note_prefetch_fetched()
            except Exception:  # noqa: BLE001 — 预读失败自吞
                pass
            finally:
                _HOT_PREFETCH_INFLIGHT.discard(key)
    except Exception:  # noqa: BLE001 — 预读绝不外抛
        pass


def _schedule_hot_prefetch(identity, range_header, fetcher) -> None:
    """把预读挂成后台任务（不阻塞出流）。无缓存元数据（本次没走
    写穿/命中）时不挂——没有总长锚点，预读无从算起。永不抛。"""
    try:
        from backend.emby_server import relay_hot_cache as _hc
        if not _hc.enabled() or _hc.prefetch_segments() <= 0:
            return
        if not _hc.get_meta(identity):
            return
        if len(_HOT_PREFETCH_TASKS) >= _HOT_PREFETCH_MAX_TASKS:
            return
        task = asyncio.get_running_loop().create_task(
            _hot_prefetch_run(identity, range_header, fetcher))
        _HOT_PREFETCH_TASKS.add(task)
        task.add_done_callback(_HOT_PREFETCH_TASKS.discard)
    except Exception:  # noqa: BLE001 — 调度失败就算了
        pass


async def _fetch_drive_segment_bytes(drive_id, file_id, access_token,
                                     seg_start, seg_end):
    """预读用：取 Drive 文件一个字节区间（走 Drive 池/熔断口径，
    跳转逐跳校验同主腿）。任何失败回 None。"""
    from backend.emby_server.drive_auth import (
        drive_api_media_url,
        drive_usercontent_download_url,
    )
    token = access_token
    if token is None:
        from backend.emby_server.drive_changes import get_token_for_drive
        token = await asyncio.to_thread(get_token_for_drive, drive_id, False)
    if not token:
        return None
    url = drive_api_media_url(drive_usercontent_download_url(file_id))
    client, one_shot = _acquire_drive_client()
    resp = None
    _try_drive_api_rate()
    try:
        resp, _cur, _hops = await _follow_redirects(
            client, url,
            {"Authorization": f"Bearer {token}",
             "Range": f"bytes={seg_start}-{seg_end}"},
            silent=True)
        if resp is None or resp.status_code != 206:
            return None
        chunks = []
        async for c in resp.aiter_bytes(READ_CHUNK):
            chunks.append(c)
        return b"".join(chunks)
    except Exception:  # noqa: BLE001 — 预读取段失败回 None
        return None
    finally:
        if resp is not None:
            try:
                await resp.aclose()
            except Exception:  # noqa: BLE001
                pass
        if one_shot:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


def _schedule_drive_hot_prefetch(drive_id, file_id, access_token,
                                 request) -> None:
    """Drive 腿出流后的跨请求预读调度：fetcher 按腿构造（SA 腿现取
    轮换凭据、令牌腿用本次令牌）。永不抛。"""
    try:
        range_header = None
        headers = getattr(request, "headers", None)
        if headers is not None:
            range_header = headers.get("range")
        identity = f"drive:{drive_id}:{file_id}"

        async def _fetcher(seg_start, seg_end):
            return await _fetch_drive_segment_bytes(
                drive_id, file_id, access_token, seg_start, seg_end)

        _schedule_hot_prefetch(identity, range_header, _fetcher)
    except Exception:  # noqa: BLE001 — 调度失败就算了
        pass


async def _fetch_mount_segment_bytes(url, headers, seg_start, seg_end):
    """预读用：取挂载上游一个字节区间（共享挂载客户端，跳转逐跳
    校验同主腿；凭据头用主腿同一份 forward、Range 换成本段区间）。
    共享客户端只关响应、绝不关 client。任何失败回 None。"""
    client = _mount_relay_client()
    forward = {k: v for k, v in (headers or {}).items()}
    forward.pop("X-Pickcode", None)
    forward["Range"] = f"bytes={seg_start}-{seg_end}"
    resp = None
    try:
        resp, _cur, _hops = await _follow_redirects(
            client, url, forward, silent=True)
        if resp is None or resp.status_code != 206:
            return None
        chunks = []
        async for c in resp.aiter_bytes(READ_CHUNK):
            chunks.append(c)
        return b"".join(chunks)
    except Exception:  # noqa: BLE001 — 预读取段失败回 None
        return None
    finally:
        if resp is not None:
            try:
                await resp.aclose()
            except Exception:  # noqa: BLE001
                pass


def _schedule_mount_hot_prefetch(identity, url, headers,
                                 range_header) -> None:
    """挂载腿出流后的跨请求预读调度：fetcher 按挂载上游构造（同一
    URL＋同一份挂载凭据头，只换 Range）。永不抛。"""
    try:
        async def _fetcher(seg_start, seg_end):
            return await _fetch_mount_segment_bytes(
                url, headers, seg_start, seg_end)

        _schedule_hot_prefetch(identity, range_header, _fetcher)
    except Exception:  # noqa: BLE001 — 调度失败就算了
        pass


async def serve_mount_relay_async(
    url: str,
    request: Request,
    headers: Optional[dict] = None,
    media_type: str = "video/mp4",
    pt=None,
    sniff_quota_page: bool = False,
    hot_identity: Optional[str] = None,
):
    """远程挂载专用中继：服务端带挂载凭据取流、流式转发给播放器。

    - ``headers``：挂载解析出的鉴权头（UA/Referer/Cookie），只在本机使用、
      不下发给客户端；``X-Pickcode`` 是 115 缓存失效用的内部头，不转发。
    - Range 透传：客户端的 ``Range`` 原样发给上游，上游的 200/206 与
      ``Content-Range``/``Accept-Ranges`` 等头原样透回。
    - ``pt``（可选）：播放链路计时器，只加日志不改逻辑。
    - ``hot_identity``（可选）：热缓存文件身份（挂载腿二期接线，
      2026-11-23）：api.video_stream 对有 drive_file_id 的挂载条目
      传 ``mount:{drive_file_id}``——file_id 是谷歌侧不可变身份、
      与内容绑定，写穿时按上游响应登记总长＋Last-Modified 校验器
      把关（变化即整身份失效）。给了才启用热缓存命中出流/写穿/
      跨请求预读（机制与 Drive 腿共用 relay_hot_cache 一套）；
      不给时热缓存完全不介入、本函数行为逐字不变。Drive 腿的
      SA 池/最终 URL 缓存/熔断只认 _DRIVE_LEG 上下文，本参数
      不启用它们。
    - ``sniff_quota_page``（转存变体应急自动触发用）：只 Drive 中继传
      True；115 挂载中继不受影响。开启时，上游回 200 text/html 即预读
      首块（无论是否带 Range；A-089：带 Range 撞上 200 额度页时旧口径
      把 HTML 原样当视频流下发、播放器解码失败且服务端无归因）——命中
      Quota 标记则关响应关客户端、抛 transfer_variant.QuotaPageError
      （调用方记信号 + 入队，本次播放回落现行链）；未命中则首块原样
      补回流中，字节一分不少。带 Range 的 200 非 HTML（上游忽略 Range
      回全文件）同样经 prelude 原样续流；206 本就不满足 status==200，
      不受嗅探影响。
    """
    import httpx

    forward = {k: v for k, v in (headers or {}).items()}
    # X-Pickcode 是内部头（115 直链缓存失效用），不转发给上游
    forward.pop("X-Pickcode", None)
    forward.setdefault("User-Agent", REMOTE_UA)
    range_header = request.headers.get("range")
    if range_header:
        forward["Range"] = range_header

    # Drive 腿上下文（只有 serve_drive_relay_async 会设）：启用
    # Drive 专用池 + 最终 URL 缓存 + 熔断记账。挂载调用无上下文，
    # 客户端与行为逐字不变。
    _leg = _DRIVE_LEG.get()
    # 热缓存身份：显式参数（挂载腿二期，api.video_stream 对有
    # drive_file_id 的挂载条目传 mount:{fid}）优先，其次 Drive 腿
    # 上下文。两者皆无 → 热缓存完全不介入，行为逐字不变。
    _hot_identity = hot_identity or (
        getattr(_leg, "hot_identity", None) if _leg is not None else None)
    # 挂载腿的跨请求预读只在身份来自显式参数时在本函数内调度
    # （fetcher 按挂载上游构造）；Drive 腿的预读由
    # serve_drive_relay_async 按 Drive fetcher 调度，不重复挂。
    _mount_prefetch = bool(hot_identity) and _leg is None
    # 热缓存全命中：请求区间被缓存分段完全覆盖时本地出字节、
    # 不回源；未命中回 None，走下面现行透传逐字不变。
    if _hot_identity:
        _cache_t0 = time.perf_counter() if pt is not None else None
        _hit_resp = _try_hot_cache_response(
            _hot_identity, range_header, media_type, pt)
        if _hit_resp is None and pt is not None:
            pt.span("cache_lookup", _cache_t0)
        if _hit_resp is not None:
            if _mount_prefetch:
                _schedule_mount_hot_prefetch(
                    _hot_identity, url, forward, range_header)
            return _hit_resp
    client = _mount_relay_client()
    _one_shot = False
    _start_url = url
    _used_cached_final = False
    _final_cache_key = None
    if _leg is not None:
        client, _one_shot = _leg.acquire()
        _final_cache_key = _leg.cache_key
        if _final_cache_key:
            _cached_final = drive_final_url_cache_get(_final_cache_key)
            if _cached_final:
                _start_url = _cached_final
                _used_cached_final = True
                # 签名最终 URL 自带授权：不把 SA 凭据发去终点主机
                forward.pop("Authorization", None)
    resp = None
    if _leg is not None:
        # 只有 Drive 腿进 Drive 限速口径；挂载腿（_leg 为 None）逐字不碰
        _try_drive_api_rate()
    _rcd_t0 = time.perf_counter() if pt is not None else None
    try:
        try:
            # 跳转自己逐跳校验后跟随（内网目标拒绝、跨主机剥凭据），终态
            # 响应不关，由下面的流消费/关闭；silent=False 口径：非法重定向
            # 目标抛 403、跳转超限抛 502。
            resp, _current, _hops = await _follow_redirects(
                client, _start_url, forward, silent=False)
        except HTTPException:
            raise
        except httpx.PoolTimeout as exc:
            if _leg is not None:
                _drive_pool_note_transport_failure()
                if _used_cached_final:
                    drive_final_url_cache_invalidate(_final_cache_key)
            raise HTTPException(
                status_code=503, detail="远程挂载代理繁忙，请稍后重试",
                headers={"Retry-After": "2"}) from exc
        except Exception as exc:  # noqa: BLE001 — 源站不可达：给干净的 502
            if _leg is not None:
                _drive_pool_note_transport_failure()
                if _used_cached_final:
                    drive_final_url_cache_invalidate(_final_cache_key)
            logger.warning("远程挂载代理失败 %s: %s", url.split("?")[0], exc)
            raise HTTPException(status_code=502, detail="源站不可达") from exc
        if _leg is not None:
            _drive_pool_note_reached()
            if _leg.capture is not None and resp.status_code < 400:
                # 快速通道 capture：只记成功终点（失败状态在本函数
                # 后段抛错，调用方不会在失败时读盒）。
                _leg.capture["final_url"] = _current
            if _final_cache_key and not _used_cached_final \
                    and _current != url and resp.status_code < 400:
                drive_final_url_cache_put(_final_cache_key, _current)
        # 上游响应头到达时刻（rcd_first_chunk 分段的起点）：无论有无
        # 计时器都取一次（perf_counter 约 20ns，不值当分支）。
        _headers_perf = time.perf_counter()
        if pt is not None:
            # 向 rcd 发请求→收到终态响应头的整段（含逐跳跳转/建连/
            # rcd 内部向谷歌开流）：冷起播约 2 秒 TTFB 的主体在此。
            pt.span("rcd_ttfb", _rcd_t0)
            pt.mark("ttfb")
        # 115 直链过期检测：403/404/410 时清 pickcode 缓存，客户端重试时
        # 挂载解析会拿到新直链（沿用删除前的口径）。
        if resp.status_code in (403, 404, 410):
            _pickcode = (headers or {}).get("X-Pickcode")
            if _pickcode:
                try:
                    from backend.emby_server.mounts import _invalidate_115_url
                    _invalidate_115_url(_pickcode)
                except Exception as exc:
                    # r46补遗：清缓存失败不能静默——否则死直链继续被沿用，
                    # 后续播放反复 403/502 却无日志可查。
                    logger.warning("115 直链缓存失效失败 pickcode=%s: %s",
                                   _pickcode, exc)
                # 批一 #1 连带：解析结果缓存里还留着同一条死链，只清换链
                # 缓存不够——不连带清，死链会被解析缓存续命最多一个 TTL。
                try:
                    from backend.emby_server.mounts import (
                        invalidate_resolve_cache_by_pickcode)
                    invalidate_resolve_cache_by_pickcode(_pickcode)
                except Exception as exc:
                    logger.warning("115 解析缓存连带失效失败 pickcode=%s: %s",
                                   _pickcode, exc)
        if resp.status_code >= 400:
            status = resp.status_code
            if _used_cached_final:
                # 缓存的最终地址已死：失效，调用方重试走现解析
                drive_final_url_cache_invalidate(_final_cache_key)
            await resp.aclose()
            resp = None
            logger.warning("远程挂载源站返回 %s: %s", status, url.split("?")[0])
            raise HTTPException(
                status_code=502 if status >= 500 else status,
                detail=f"源站返回 {status}")

        # 转存变体 Quota 嗅探（仅 Drive 中继传 True；115 挂载中继不受影响）。
        # 上游回 200 时预读首块（带 Range 同样嗅探，A-089）：命中 Quota 标记
        # → 关响应关客户端、抛 QuotaPageError（video_stream 接住记信号 +
        # 入队，本次播放回落现行链）；未命中 → 首块记为 prelude，
        # iter_mount 先吐首块再续流，字节一分不少、透传头不动。
        # 嗅探本身绝不能拖垮播放：预读失败时当作未嗅探，照常流式转发。
        # 注意：QuotaPageError 在本块内关完响应才抛——外层 except 只认
        # HTTPException，QuotaPageError 会原样穿到调用方，不会漏关连接。
        _prelude = None
        if sniff_quota_page and resp.status_code == 200:
            from backend.emby_server import transfer_variant as _tv
            try:
                _aiter = resp.aiter_bytes(READ_CHUNK)
                try:
                    _first = await _aiter.__anext__()
                except StopAsyncIteration:
                    _first = b""
                if _tv.is_quota_page_bytes(
                        resp.status_code, resp.headers.get("content-type"),
                        _first):
                    await resp.aclose()
                    resp = None
                    if _used_cached_final:
                        # 额度页也算最终地址失效：失效后重试走现解析
                        drive_final_url_cache_invalidate(_final_cache_key)
                    if _one_shot:
                        # QuotaPageError 不走外层 HTTPException 清理，
                        # 一次性客户端在这里就地关
                        try:
                            await client.aclose()
                        except Exception:
                            pass
                    raise _tv.QuotaPageError("上游响应为 Quota 额度页")
                _prelude = (_aiter, _first)
            except _tv.QuotaPageError:
                raise
            except Exception as exc:
                logger.warning("Quota 嗅探预读失败，照常转发：%s",
                               type(exc).__name__)
                _prelude = None

        passthrough = {}
        for name in ("content-range", "accept-ranges", "content-length",
                     "last-modified", "etag"):
            value = resp.headers.get(name)
            if value:
                passthrough[name.title()] = value
        # 与本地路径同口径：远程源支持 Range，上游没给也要补
        passthrough.setdefault("Accept-Ranges", "bytes")
        passthrough["X-Accel-Buffering"] = "no"
        # 边缘缓存调试头（通用能力）：EDGE_CACHE_MODE=query_ignore 时声明
        try:
            from backend.emby_server import edge_cache
            _edge_dbg = edge_cache.debug_header_value()
            if _edge_dbg:
                passthrough[edge_cache.DEBUG_HEADER] = _edge_dbg
        except Exception:
            pass
        content_type = resp.headers.get("content-type") or media_type

        # 热缓存写穿计划（带 hot_identity 时：Drive 腿经上下文/
        # 挂载腿经参数）：按上游响应头定起始偏移/总长并登记校验器
        # （变化即失效旧分段）；算不出为 None、本次不写穿。Quota
        # 嗅探在本段之前，额度页抛错时根本到不了这里，不会被
        # 写进缓存。
        _tee_plan = _hot_cache_tee_plan(_hot_identity, resp) \
            if _hot_identity else None

        # 服务端预读（2026-11-17 中继取流优化）：只对 Drive 中继腿
        # （_leg 在位）生效——生产者先读后续块进有界队列，抹平上游
        # 抖动；挂载腿（_leg 为 None）维持下面的直通迭代逐字不变。
        _prefetch_ahead = _relay_prefetch_chunks() if _leg is not None else 0

        async def _raw_chunks():
            if _prelude is not None:
                # 嗅探已预读首块：先吐首块，再续跑同一迭代器——
                # 非 Quota 的 HTML 原样续流，字节一个不少。
                _aiter, _first = _prelude
                if _first:
                    yield _first
                async for chunk in _aiter:
                    yield chunk
            else:
                async for chunk in resp.aiter_bytes(READ_CHUNK):
                    yield chunk

        async def iter_mount():
            _source = _raw_chunks()
            if _tee_plan is not None:
                _source = _hot_cache_tee(_source, _tee_plan)
            if _prefetch_ahead > 0:
                _source = _prefetch_stream_chunks(_source, _prefetch_ahead)
            try:
                async for chunk in _source:
                    yield chunk
            except httpx.TransportError as exc:
                # 源站中途断开：结束这条流、归还连接；响应头已发出，
                # 播放器会对剩余区间重新发 Range。
                logger.warning("远程挂载流中断 %s: %s",
                               url.split("?")[0], type(exc).__name__)
            finally:
                # 先关源迭代（预读时连带停掉生产者任务），再关响应。
                try:
                    await _source.aclose()
                except Exception:
                    pass
                # 只关响应：连接归还共享客户端的池（client 按 loop
                # 共享，关掉会连 transport 一起关、后续请求全废）。
                try:
                    await resp.aclose()
                except Exception:
                    pass
                if _one_shot:
                    # 一次性冷客户端（Drive 池熔断回退）：流结束即关，
                    # 不许留着复用——那正是熔断要隔离的病态连接层。
                    try:
                        await client.aclose()
                    except Exception:
                        pass

        _body = _release_upstream_when_stalled(
            iter_mount(), resp, url, _mount_relay_stall_timeout())
        if pt is not None:
            # [play-timing] 汇总行推迟到首字节交付时输出（见
            # _pt_first_byte_body）：total 即首字节时刻、行内含
            # rcd_first_chunk 分段。只动日志输出时机，不动流本身。
            _body = _pt_first_byte_body(_body, pt, _headers_perf)
        if _mount_prefetch:
            # 挂载腿跨请求预读：本次出流后把请求区间之后的 N 个
            # 分段提前读进热缓存，播放器逐段请求时逐段命中（调度
            # 永不抛、不影响本次响应）。Drive 腿不在此调度（其
            # 预读在 serve_drive_relay_async 按 Drive fetcher 挂）。
            _schedule_mount_hot_prefetch(
                _hot_identity, url, forward, range_header)
        return StreamingResponse(
            _body,
            status_code=resp.status_code,
            media_type=content_type, headers=passthrough,
        )
    except HTTPException:
        if resp is not None:
            try:
                await resp.aclose()
            except Exception:
                pass
        if _one_shot:
            try:
                await client.aclose()
            except Exception:
                pass
        raise


#: Drive 中继 SA failover 的触发状态码：令牌死（401）/ 权限与配额类
#: （403）/ 限流（429）——都是「换一个 SA 就可能好」的失败。404 是文件
#: 不存在、换号也没用，原样透传（#1192 定版）；其余状态码维持挂载
#: 中继既有口径（不触发换号）。
_RELAY_SA_FAILOVER_STATUSES = frozenset({401, 403, 429})

#: 中继 SA failover 总尝试次数缺省（调用方未传 max_attempts 时用；
#: 与 playback_strategy 的 SystemConfig 缺省同为 3，见该模块键注释）。
_RELAY_SA_FAILOVER_DEFAULT_ATTEMPTS = 3

#: failover 循环内排除已试 SA 的重抽上限（A-009）：轮换随机步长可能抽回
#: 同一个 SA；抽中已试 SA 时再抽一次，最多该次数，耗尽则接受最后一次
#: 抽到的令牌（退回旧语义，避免小池场景下直接 503）。
_RELAY_SA_EXCLUDE_REDRAWS = 3


def _note_relay_sa_cooldown(url: str, token: str, status: int) -> None:
    """把本次取流失败的 SA 凭据按值失效（冷却记账，只记账、绝不抛错）。

    复用核查（横切只许一套，不另起第二套冷却）：
    - drive_changes._api_get 的指数退避是请求级、无 per-SA 状态；
      _mark_dead_drive 是 per-盘 404 退避——对象都不是 SA，不适用。
    - 本仓唯一的「凭据失败→失效」实现是 drive_auth 的按值双层失效
      （401 毒化修复）：401 走其指定入口 note_drive_unauthorized，
      403/429 走 invalidate_drive_bearer_token，本函数只分发不新造。
    失效后，该 SA 在双层缓存里的坏令牌被清掉，下次轮到它时重新换取；
    本次重试则靠 get_token_for_drive 的轮换直接换下一个 SA。
    配额类失败（403/429）另把该 SA 登记进 drive_auth 的 per-SA 配额冷却表
    （note_sa_cooldown，全站唯一登记表）：冷却期内选号跳过它，不再往已爆
    配额的号上撞。邮箱须在失效前反查（A-088 同口径：失效清掉两层缓存
    条目后反查恒得 None）。
    记账本身失败只记日志——冷却记账不能拖垮 failover。
    """
    try:
        from backend.emby_server import drive_auth

        email = drive_auth.sa_email_for_token(token)
        if status == 401:
            drive_auth.note_drive_unauthorized(url, f"Bearer {token}")
        else:
            drive_auth.invalidate_drive_bearer_token(token)
        if status in (403, 429):
            drive_auth.note_sa_cooldown(email, status=status)
    except Exception as exc:  # noqa: BLE001 - 记账失败只记日志
        logger.warning("Drive 中继 SA 冷却记账失败 status=%s: %s",
                       status, exc)


async def serve_drive_relay_async(
    request: Request,
    drive_id: str,
    file_id: str,
    media_type: str = "video/mp4",
    pt=None,
    access_token: Optional[str] = None,
    rotate_on_quota: bool = True,
    max_attempts: Optional[int] = None,
):
    """Drive 中继入口：设置 Drive 腿连接层上下文后转内层实现。

    上下文（_DRIVE_LEG）让 serve_mount_relay_async 对本次中继启用
    Drive 专用池 / 熔断 / 最终 URL 缓存；SA 腿（access_token 为
    None）按 (drive_id, file_id) 落最终 URL 缓存键，BYOS 用户令
    牌腿不落缓存（见 _DriveLegScope）。failover 的多次尝试共享
    同一上下文：缓存被某次尝试判死失效后，后续尝试自动回现解析。
    语义与口径全在 _serve_drive_relay_async_inner 的 docstring。
    """
    _leg = _DriveLegScope(
        cache_key=None if access_token is not None
        else f"relay:{drive_id}:{file_id}",
        capture=_RELAY_FINAL_CAPTURE.get(),
        hot_identity=f"drive:{drive_id}:{file_id}")
    _leg_token = _DRIVE_LEG.set(_leg)
    try:
        _resp = await _serve_drive_relay_async_inner(
            request, drive_id, file_id, media_type=media_type, pt=pt,
            access_token=access_token, rotate_on_quota=rotate_on_quota,
            max_attempts=max_attempts)
    finally:
        _DRIVE_LEG.reset(_leg_token)
    # 跨请求预读：本次出流（全命中或写穿透传）后，把请求区间之后
    # 的 N 个分段提前读进热缓存；调度永不抛、不影响本次响应。
    _schedule_drive_hot_prefetch(drive_id, file_id, access_token, request)
    return _resp


async def _serve_drive_relay_async_inner(
    request: Request,
    drive_id: str,
    file_id: str,
    media_type: str = "video/mp4",
    pt=None,
    access_token: Optional[str] = None,
    rotate_on_quota: bool = True,
    max_attempts: Optional[int] = None,
):
    """Drive 条目 SA 中继：服务端用盘凭据从 Drive API 取流、流式转发。

    播放器只跟本站说话：file_id 永不出服务器，也不经「任何人可读」的
    公开下载池（公开池是全网共用额度，爆池时整库播不了，2026-10-23
    实发）；alt=media + 盘凭据走 SA/OAuth 各自的额度，实测公开池已死的
    文件用 SA 凭据仍 206 出字节。

    - 取流 / Range 透传 / 流式转发复用 serve_mount_relay_async
      （横切只许一套）；这里只管 Drive 特有的凭据与地址：API 地址由
      drive_auth 的现成构造函数拼，凭据走 get_token_for_drive
      （全项目唯一入口：共享盘 SA 轮换 / 个人盘 OAuth 不回退）。
    - SA failover（2026-10-29 用户亲令「中转加上换号能力」）：上游
      取流回 401/403/429（令牌死 / 当前 SA 下载额度被打满 / 限流）→
      先按值失效该 SA 凭据（drive_auth 既有机制，见
      _note_relay_sa_cooldown），再轮换凭据重试；总尝试次数由
      ``max_attempts`` 界定（调用方传 SystemConfig 键
      drive_relay_sa_failover_max_attempts 的值，未传缺省 3，含首次），
      次数耗尽回最后一次的原始错误。每次换号打一条
      drive_relay_sa_failover 日志（sa= 字段与 drive_token_mint 同
      形态）。404 与其余状态码不换号、按挂载中继口径抛错（404
      透传、5xx 在下层已转 502）。
    - 请求撞上 Quota 额度页（上游回 200 HTML 额度页，无论是否带
      Range，A-089）→ 默认与 403 同口径：轮换凭据重试（与状态码
      failover 共用尝试预算）；预算耗尽才原样抛 QuotaPageError。
      额度页分支同样按值失效该 SA 凭据再重试（A-009）；已试 SA 在
      失效前记录（A-088：失效清缓存，事后反查记不上）；轮换抽中已试
      SA 时有界重抽（上限 3 次），
      耗尽则接受最后一次抽到的令牌。``rotate_on_quota=False``
      （绑定用户正在啃共享原件）时第一次额度页就抛，不换下一个 SA。
      调用方记信号；绑定用户不改播共享原件。BYOS 用户令牌腿不
      嗅探、不重试，原样不动。
    - failover 覆盖边界（按取流实情）：serve_mount_relay_async 在
      上游响应头阶段就把 ≥400 抛成 HTTPException、整段请求的 Quota
      页也在首块嗅探时抛出——都发生在首字节送达播放器之前（客户端
      Range 原样透传、同一 Range 段换号重取）。流已开始后的中途
      断流不在覆盖内（下层结束该流，播放器对剩余区间重新发 Range，
      那是新请求、新一轮 failover）。
    - 凭据获取失败（个人盘 OAuth 抛错、无 SA 可用）→ 503。
    - access_token 给了（BYOS 用户令牌中继）：直接用它、只试一次，
      上游 403 原样抛、绝不换凭据重试——换凭据就是跨用户复用，
      BYOS 铁律不许。drive_id 此时只作日志标识，不用于取凭据。
    """
    from backend.emby_server.drive_auth import (
        drive_api_media_url,
        drive_usercontent_download_url,
        sa_log_id as _sa_log_id,
    )
    from backend.emby_server.drive_changes import get_token_for_drive
    from backend.emby_server.drive_changes import (
        sa_email_for_token as _dc_sa_email_for_token)
    from backend.emby_server import transfer_variant as _tv

    url = drive_api_media_url(drive_usercontent_download_url(file_id))
    if access_token is not None:
        # BYOS 用户令牌腿：单次直试，任何 HTTPException 原样抛。
        return await serve_mount_relay_async(
            url, request,
            headers={"Authorization": f"Bearer {access_token}"},
            media_type=media_type, pt=pt)
    total = (_RELAY_SA_FAILOVER_DEFAULT_ATTEMPTS if max_attempts is None
             else max(1, int(max_attempts)))
    tried_sa_emails: set[str] = set()

    def _remember_tried(tok):
        email = _dc_sa_email_for_token(tok)
        if email:
            tried_sa_emails.add(email)
        return email
    for attempt in range(total):
        last = attempt == total - 1
        try:
            token = await asyncio.to_thread(
                get_token_for_drive, drive_id, attempt > 0)
            # A-009：轮换随机步长可能抽回已试 SA——抽中已试则再抽
            # （有界），避免 3 次预算打在同一个 SA 上；邮箱反查不到
            # （个人盘 OAuth 腿等）时不排除、保持旧语义。
            for _ in range(_RELAY_SA_EXCLUDE_REDRAWS):
                email = _dc_sa_email_for_token(token)
                if not email or email not in tried_sa_emails:
                    break
                nxt = await asyncio.to_thread(
                    get_token_for_drive, drive_id, True)
                if not nxt:
                    break
                token = nxt
        except Exception as exc:  # noqa: BLE001 - 个人盘 OAuth 失败等
            logger.warning("Drive 中继取凭据失败 drive=%s: %s", drive_id, exc)
            raise HTTPException(
                status_code=503, detail="Drive 凭据不可用") from exc
        if not token:
            raise HTTPException(status_code=503, detail="Drive 凭据不可用")
        try:
            return await serve_mount_relay_async(
                url, request,
                headers={"Authorization": f"Bearer {token}"},
                media_type=media_type, pt=pt, sniff_quota_page=True)
        except _tv.QuotaPageError:
            if last or not rotate_on_quota:
                raise
            # A-009：额度页分支此前只记日志、不失效——同 SA 重抽会命中
            # 令牌缓存、必然复现同一张额度页；按值失效再重试（额度页
            # 本身没有状态码，429 走 invalidate 路径）。
            # A-088：先记录已试 SA 再冷却——冷却会同时清掉内外两层
            # 缓存条目，事后反查恒得 None、已试集记不上；日志 sa=
            # 直接用这次记下的邮箱，不再事后反查。
            sa_email = _remember_tried(token)
            _note_relay_sa_cooldown(url, token, 429)
            logger.warning(
                "drive_relay_sa_failover sa=%s failover_attempt=%d/%d "
                "status=quota_page file_id=%s",
                _sa_log_id(sa_email), attempt + 1, total, file_id)
        except HTTPException as exc:
            if exc.status_code not in _RELAY_SA_FAILOVER_STATUSES or last:
                raise
            # A-088：先记录已试 SA 再冷却（同额度页分支；401 经
            # note_drive_unauthorized 同样走 invalidate，顺序要求相同）。
            sa_email = _remember_tried(token)
            _note_relay_sa_cooldown(url, token, exc.status_code)
            logger.warning(
                "drive_relay_sa_failover sa=%s failover_attempt=%d/%d "
                "status=%d file_id=%s",
                _sa_log_id(sa_email), attempt + 1, total,
                exc.status_code, file_id)
    raise HTTPException(status_code=502, detail="Drive 中继失败")  # pragma: no cover


def _file_validators(stat: os.stat_result) -> tuple[str, str]:
    """按「mtime + 大小」生成 ETag 与 Last-Modified（内容一变校验器就变）"""
    return f'"{int(stat.st_mtime)}-{stat.st_size}"', formatdate(stat.st_mtime, usegmt=True)


def serve_image(path: Optional[str]) -> FileResponse:
    """图片响应：带 ETag / Last-Modified 与 24h 客户端强制缓存

    浏览媒体库时同一张海报会被反复请求（滚动、返回、切页），旧实现每次整文件重发。
    现在响应带 ``ETag`` / ``Last-Modified`` 与 ``Cache-Control: private, max-age=86400``，
    浏览器与客户端在有效期内直接命中本地缓存，不再为同一张图重复付出磁盘 I/O 与带宽。
    PA-036 起图片端点已鉴权：private 是为了不让共享缓存（反代 / CDN）把
    受限库的图复用给其他人，客户端本地缓存不受影响。
    """
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image not found")
    ext = os.path.splitext(path)[1].lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    try:
        stat = os.stat(path)
    except OSError:  # 判断与取属性之间文件被删 / 挂载掉线：干净 404，不要 500
        raise HTTPException(status_code=404, detail="Image not found") from None
    etag, last_modified = _file_validators(stat)
    headers = {
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": "private, max-age=86400",
        "Accept-Ranges": "bytes",
    }
    return FileResponse(path, media_type=media_type, headers=headers)
