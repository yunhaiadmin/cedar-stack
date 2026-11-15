# -*- coding: utf-8 -*-
"""流量额度拦截（执行侧）：额度用尽后拦耗流量的播放起播。

用户 2026-11-15 亲点「拦截」，取代 traffic_quota 模块 docstring 里
2026-11-13 的旧边界（只展示不拦）。自此全站口径：

- 判定只许复用 ``traffic_quota.usage_summary`` 的 ``exhausted``
  （套餐配了月额度 enabled 且有效已用 >= 额度；抵扣含余额购流量
  与时间换流量，都在 usage_summary 里算好），不许另算一套；
- 只拦「起播」：无 Range 头或 Range 从 0 开始的请求；Range 续传
  放行，已经开始的响应永不中断（出口替换只发生在响应未发出前）；
- 只拦耗流量的腿：中继腿与本机文件腿（计量口径的两条腿，见
  emby_server/traffic_meter）；302/直链字节不过服务器，永不拦；
- enabled=False（套餐没配月额度）的用户不拦；管理员/staff 同样
  过拦截（与播放门禁「管理员无豁免」同口径，判定不看 is_staff）；
- 判定出错（查库异常等）一律放行 + 记日志——拦截是账房的事，
  不许拖垮播放（与 traffic_meter 的铁律同口径）。

接入点（全清单，改播放链路前先对这里）：
- EA 取流出口 ``emby_server/api.py`` 的 ``_observe_traffic``：
  200/206 字节响应在包设计数器之前过 ``egress_traffic_blocked``，
  命中即替换成 403 JSON（detail 为人话字符串）；302 直接放行。
- EA 本机文件腿：``video_stream`` 两处 ``serve_file`` 之前过
  ``api._raise_if_traffic_exhausted``（内部调 metered_start_blocked）。
- EA 签发侧 ``playback_info``：``api._apply_traffic_gate_to_sources``
  按 ``classify_source`` 摘除已知中继类变体源；全部源都是已知
  中继类时 403 + error_code=TRAFFIC_EXHAUSTED。类型未知的源
  （无变体身份的本站 /stream 基础源，腿在取流时才定）不动，
  由取流侧出口裁决——签发侧不许凭猜拦人。

开关：SystemConfig ``traffic_enforce_enabled``，默认开（无行 =
开）；照 traffic_quota._config_int / playback_policy._config_bool
的读法走 integrations.store 热读（60 秒 TTL，改完最多 60 秒生效）。
关掉时全部放行（紧急止血口）。

性能：起播频率不高，判定结果按 user_id 做进程内 30 秒 TTL 缓存
（只缓存 usage_summary 的结论；开关每次现读 store 热读缓存）。
缓存失效只有 TTL 到期一条路：用户购流量/换流量后最迟 30 秒恢复
放行（外加计量落库的固有延迟）。判定失败不进缓存，下次重试。
"""
from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
from typing import Optional

from backend import traffic_quota
from backend.integrations import store

logger = logging.getLogger(__name__)

#: 拦截总开关（SystemConfig）：无行/空值 = 开；只有明确的
#: false/0/no/off 才关（出厂默认开，写错值不许把拦截静默关掉）。
TRAFFIC_ENFORCE_KEY = "traffic_enforce_enabled"

#: 签发侧 403 的结构化错误码（前端播放流按它认这条拦截）。
TRAFFIC_EXHAUSTED_CODE = "TRAFFIC_EXHAUSTED"

#: 被拦文案：三条出路都真实存在——概览页流量区可购买流量
#: （traffic_quota.buy_traffic_with_balance）与时间换流量
#: （exchange_time_for_traffic），直链线路在 PlaybackInfo 变体
#: 清单里（drive_direct / byos 直链 / 池副本直链）。
TRAFFIC_EXHAUSTED_MESSAGE = (
    "本月流量已用完：中继和本站文件播放先停了。"
    "可以去概览页的流量区购买流量，或用订阅时长换流量；"
    "也可以改走直链线路播放（直链不耗本站流量）。")

#: 判定结论缓存 TTL（秒）：进程内、按 user_id；见模块 docstring。
CACHE_TTL_SEC = 30.0

_CACHE: dict[int, tuple[bool, float]] = {}
_CACHE_LOCK = threading.Lock()

# 当前请求的 Range 头（video_stream 入口与 traffic_meter
# .set_current_user 同点写入）：字节出口 _observe_traffic 只有
# response 一个参数（签名纪律不许改），起播/续传的判定靠它把
# 请求侧的 Range 带到出口点。取不到（None）按无 Range 处理，
# 即起播——无 Range 的请求本来就是从头拉流。
_CURRENT_RANGE: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("traffic_gate_range", default=None))


def set_current_range(range_header: Optional[str]) -> None:
    _CURRENT_RANGE.set(range_header)


def current_range() -> Optional[str]:
    return _CURRENT_RANGE.get()


# ---------------------------------------------------------------------------
# 起播判定
# ---------------------------------------------------------------------------

_RANGE_START_RE = re.compile(r"bytes=(\d+)-", re.IGNORECASE)


def is_start_range(range_header: Optional[str]) -> bool:
    """本次请求是否算「起播」：无 Range 头、或 Range 从 0 开始。

    续传（起始 > 0）放行——不许把正在播的流掐断；后缀范围
    （bytes=-N，取尾部）不是从 0 起播，按续传处理；解析不了的
    怪值按起播处理（服务端对怪 Range 本就按整文件/拒绝处理，
    整文件即从 0 拉流）。
    """
    if not range_header:
        return True
    text = str(range_header).strip()
    if not text:
        return True
    match = _RANGE_START_RE.match(text)
    if match:
        return int(match.group(1)) == 0
    if text.lower().startswith("bytes=-"):
        return False
    return True


# ---------------------------------------------------------------------------
# 开关与判定
# ---------------------------------------------------------------------------

_FALSE_WORDS = ("0", "false", "no", "off")


def enforcement_enabled(db) -> bool:
    """拦截总开关：默认开，只有配置明确写关才关。

    读失败按默认开返回——真正的放行兜底在判定层（usage_summary
    异常一律放行），开关读不出来不许把播放全停了：判定层会因
    同一个库故障走异常放行，两层口径不打架。
    """
    try:
        raw = store.get_value(db, TRAFFIC_ENFORCE_KEY, "")
    except Exception:  # noqa: BLE001 — 读失败回默认开（见 docstring）
        return True
    text = str(raw or "").strip().lower()
    if not text:
        return True
    return text not in _FALSE_WORDS


def _cached_exhausted(user_id: int) -> Optional[bool]:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(user_id)
        if hit is None:
            return None
        value, expires_at = hit
        if now < expires_at:
            return value
        _CACHE.pop(user_id, None)
        return None


def _store_cached(user_id: int, value: bool) -> None:
    with _CACHE_LOCK:
        _CACHE[user_id] = (value, time.monotonic() + CACHE_TTL_SEC)


def invalidate_cache(user_id: Optional[int] = None) -> None:
    """清判定缓存（测试与运维口）：None 清全部，否则只清一人。"""
    with _CACHE_LOCK:
        if user_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(user_id, None)


def user_traffic_exhausted(db, user_id) -> bool:
    """该用户本周期流量是否已用尽（拦截结论，带 30 秒缓存）。

    判定只走 traffic_quota.usage_summary 的 exhausted；开关关、
    user_id 为空、判定异常都回 False（放行）。异常不进缓存。
    """
    if user_id is None:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    hit = _cached_exhausted(uid)
    if hit is not None:
        return hit
    try:
        if not enforcement_enabled(db):
            return False
        summary = traffic_quota.usage_summary(db, uid)
        exhausted = bool(summary.get("exhausted"))
    except Exception:  # noqa: BLE001 — 判定出错一律放行（见模块 docstring）
        logger.warning(
            "流量拦截判定失败，放行 user_id=%s", uid, exc_info=True)
        return False
    _store_cached(uid, exhausted)
    return exhausted


def metered_start_blocked(db, user_id, range_header) -> bool:
    """耗流量腿的起播是否该拦：起播 + 已用尽才拦。"""
    if not is_start_range(range_header):
        return False
    return user_traffic_exhausted(db, user_id)


# ---------------------------------------------------------------------------
# 字节出口专用（_observe_traffic 无 db 在手，自开短会话判定）
# ---------------------------------------------------------------------------

def _open_session():
    """自开一个短会话（与 traffic_meter 落库线程同一口径）。"""
    from backend.database import SessionLocal

    return SessionLocal()


def egress_traffic_blocked(user_id, range_header) -> bool:
    """中继腿出口判定：起播 + 已用尽 → True（调用方替换成 403）。

    自开会话、用完即关；任何异常放行（False）。缓存命中时连
    会话都不开——先查缓存再决定要不要碰库。
    """
    if user_id is None or not is_start_range(range_header):
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    hit = _cached_exhausted(uid)
    if hit is not None:
        return hit
    db = None
    try:
        db = _open_session()
        return user_traffic_exhausted(db, uid)
    except Exception:  # noqa: BLE001 — 出口判定出错一律放行
        logger.warning(
            "流量拦截出口判定失败，放行 user_id=%s", uid, exc_info=True)
        return False
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 签发侧：MediaSource 分类（PlaybackInfo 用）
# ---------------------------------------------------------------------------

def _variant_name_of(source: dict) -> Optional[str]:
    """从 MediaSource 身份里取出变体名（取不到返回 None）。

    两个权威来源：``Id`` 的 ``{guid}:{variant}`` 后缀
    （play_variants.build_media_sources 的构造口径）与 URL 上的
    ``variant=`` 参数（variantize_url 的打标口径，池改道给基础
    源打的也是它）。
    """
    from backend.emby_server import play_variants

    sid = str(source.get("Id") or "")
    if ":" in sid:
        name = sid.rsplit(":", 1)[1].strip()
        if play_variants.variant_by_name(name) is not None:
            return name
    for key in ("DirectStreamUrl", "DirectPlayUrl"):
        url = str(source.get(key) or "")
        if "variant=" in url:
            tail = url.split("variant=", 1)[1]
            name = tail.split("&", 1)[0].strip()
            if play_variants.variant_by_name(name) is not None:
                return name
    return None


def variant_is_metered(variant_name) -> bool:
    """变体名 → 是否耗本站流量（中继/本机文件腿）。

    元数据只认 play_variants 一处：is_remote（客户端自拉字节）
    为 False 的就是计量腿；未知变体名按不耗流量处理（签发侧
    不凭猜拦人，取流侧出口还会按真实腿裁决）。
    """
    if not variant_name:
        return False
    from backend.emby_server import play_variants

    variant = play_variants.variant_by_name(str(variant_name))
    return bool(variant is not None and not variant.is_remote)


def classify_source(source: dict) -> str:
    """签发侧源分类：``direct`` / ``metered`` / ``unknown``。

    - 变体身份明确的按 play_variants 元数据：is_remote（客户端
      自拉字节）= direct；其余（中继/本机文件）= metered；
    - 无变体身份但带 RequiredHttpHeaders 的是 alt=media 直链
      （_apply_alt_media_direct 已把地址换成谷歌直链）= direct；
    - 其余（无变体身份的本站 /stream 基础源）= unknown：腿要到
      取流时才定（可能 302、可能中继），签发侧不凭猜拦，
      留给取流侧出口裁决。
    """
    if not isinstance(source, dict):
        return "unknown"
    name = _variant_name_of(source)
    if name is not None:
        return "metered" if variant_is_metered(name) else "direct"
    if source.get("IsRemote") is True:
        return "direct"
    if source.get("RequiredHttpHeaders"):
        return "direct"
    return "unknown"


def drop_metered_sources(sources: list) -> list:
    """摘除已知中继类源，保留 direct 与 unknown（逐字不动原 dict）。"""
    return [s for s in sources if classify_source(s) != "metered"]
