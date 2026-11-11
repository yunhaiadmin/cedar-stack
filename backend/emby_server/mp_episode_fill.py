# -*- coding: utf-8 -*-
"""缺集补集通道（B 方案，2026-11-11 用户拍板）。

为什么有这个通道
----------------
缺集核算工人（missing_episodes_worker）只负责把「缺哪几集」算清
落台账，此前补集只能靠 MP（MoviePilot）订阅——但 MP 的订阅引擎
按云海 Emby 的目录判「已有」：云海有、Aetrix 没有的集，它永远
判完成不下载（《逃亡者PLAN B》全集、《香水》缺 14 集都是这么
卡死的）。本通道绕开订阅判存：按 Aetrix 自己的缺集台账，直接
走 MP 的搜索＋手动下载接口（该接口不查任何媒体库库存，2026-
11-11 已实证可用）把缺集下回来；下好的集入库后由缺集核算工人
的既有对账自然销账，本通道绝不直接改台账的 resolved。

口径（不许漂移）
----------------
- 配置全走 SystemConfig 家族 ``mp_episode_fill``（管理端缺集
  清单页有卡）：MP 地址、token（密钥，只进请求头、绝不进日志
  与 attempt 账）、总开关（默认关，开试点由管理员手动开）、
  每日发起上限、同剧同季冷却。
- 缺集事实只认台账（emby_missing_episode_records，status=open
  的 missing_detail）；身份以 MP media 接口的权威 title/year/
  tmdb_id 复核，tmdb 对不上整部跳过记 error。
- 选种：年份与媒体年份冲突直接拒（1995 年《逃亡者》假种教训）；
  做种为 0 拒；单集体积离谱（按覆盖集数摊）拒；排序＝覆盖缺集
  数优先、做种数次之、体积小者靠前；贪心凑满缺集为止，整季包
  覆盖全缺集时只下它一个。
- attempt 账（emby_missing_episode_fill_attempts）只记事实：
  时间、种子标题、站点、download_id、结果（started/no_match/
  error）。同剧同季任一结果落账后冷却期内不重复发起；每日
  started 数达到上限硬停。
- MP 不可达/token 未配：整轮跳过只记一行日志，不报错刷屏；
  连续 3 次 MP 错误开熔断（30 分钟内轮次直接跳过）。

资源纪律（照工人家族同口径）：独立线程、独立节拍、起播繁忙
或系统高负载整轮让路、单部失败隔离不拖垮整轮、线程异常不
退出循环。
"""

import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, time as dtime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

#: worker_registry 登记名（/api/health 与管理总览按此名认人）。
WORKER_NAME = "mp_episode_fill"

#: 运行状态发布缓存键（照 missing_episodes_worker 的形状）。
STATUS_CACHE_KEY = "aetrix:mp_episode_fill:status"
_STATUS_TTL_SEC = 1800

_SHANGHAI = ZoneInfo("Asia/Shanghai")

#: 工人轮间隔（秒）：补集不追实时，半小时一轮足够；改配置
#: （开关/上限/冷却）每轮热读、不用重启。
ROUND_INTERVAL_SEC = 1800.0
#: 单轮最多处理多少部剧（每部每季最多再发起有限个下载，
#: 且有每日上限兜底）。
MAX_SERIES_PER_ROUND = 20
#: 单季单轮最多发起几个下载（贪心补集的发散上限）。
MAX_DOWNLOADS_PER_SEASON = 3
#: 连续 MP 错误多少次开熔断；熔断时长（秒）。
CIRCUIT_THRESHOLD = 3
CIRCUIT_OPEN_SEC = 1800.0
#: 体积合理区间（按覆盖集数摊到单集）：太小多半是假种/预告，
#: 太大是未压缩全集塞单集名下，一律拒。
MIN_EPISODE_BYTES = 10 * 1024 * 1024
MAX_EPISODE_BYTES = 60 * 1024 * 1024 * 1024
#: 让路判据（照 missing_episodes_worker 同口径）。
BUSY_PLAYS_5MIN = 30
LOAD_RATIO_LIMIT = 0.85


# ---------------------------------------------------------------------------
# 配置（SystemConfig 家族 mp_episode_fill；与 config_schema 同口径）
# ---------------------------------------------------------------------------

MP_FILL_CONFIG_DEFAULTS: dict = {
    "mp_download_base": "http://167.17.76.115:4500",
    "mp_download_token": "",
    "mp_episode_fill_enabled": False,
    "mp_episode_fill_daily_cap": 10,
    "mp_episode_fill_cooldown_hours": 24,
}


def _raw_config(db) -> dict:
    from backend.integrations import store

    defaults = {
        key: ("true" if value is True else "false" if value is False
              else str(value))
        for key, value in MP_FILL_CONFIG_DEFAULTS.items()
    }
    try:
        return store.get_values(db, list(MP_FILL_CONFIG_DEFAULTS), defaults)
    except Exception:  # noqa: BLE001 配置层异常不许把工人打成 500
        logger.warning("MP 补集配置读取失败，回落代码默认", exc_info=True)
        return defaults


def mp_fill_config_payload(db) -> dict:
    """补集通道配置生效值（类型化）：管理端 GET 与工人共用这一
    份解析，不许两处各解析一遍。注意返回值含 token 原文，只许
    工人内部用；管理端出口必须先脱敏（见 admin_missing_episodes）。"""
    raw = _raw_config(db)
    out: dict = {}
    for key, default in MP_FILL_CONFIG_DEFAULTS.items():
        value = raw.get(key, "")
        if isinstance(default, bool):
            out[key] = str(value).strip().lower() == "true"
        elif isinstance(default, int):
            try:
                out[key] = int(str(value).strip())
            except (TypeError, ValueError):
                out[key] = default
        else:
            out[key] = str(value) if value not in (None, "") else default
    return out


# ---------------------------------------------------------------------------
# MP 客户端（手动通道：media 核身份 / 标题搜种 / 发起下载）
# ---------------------------------------------------------------------------

class MpError(Exception):
    """MP 调用失败（网络、HTTP 非 2xx、信封 success=false）。"""


class MpClient:
    """MoviePilot 手动下载通道客户端。

    只用三个已实证的端点，全部不查媒体库库存：
    - GET  /api/v1/media/{tmdbid}?type_name=电视剧
      &media_source=themoviedb → 信封 data 是权威媒体信息；
    - GET  /api/v1/search/title?keyword=... → data 是结果数组，
      每项顶层带 torrent_info；
    - POST /api/v1/download/ {media_in, torrent_in} →
      data.download_id。
    token 只进 X-API-Key 请求头，绝不写日志/异常文本。
    """

    def __init__(self, base_url: str, token: str, http=None,
                 timeout: float = 30.0):
        self._base = (base_url or "").rstrip("/")
        self._token = token or ""
        self._http = http if http is not None else httpx.Client(
            timeout=timeout)

    def _request(self, method: str, path: str, **kwargs):
        if not self._base:
            raise MpError("MP 地址未配置")
        try:
            resp = self._http.request(
                method, f"{self._base}{path}",
                headers={"X-API-Key": self._token}, **kwargs)
        except httpx.HTTPError as exc:
            raise MpError(f"MP 请求失败：{type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise MpError(f"MP HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise MpError("MP 响应非 JSON") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            message = ""
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")[:120]
            raise MpError(f"MP 返回失败：{message}" if message
                          else "MP 返回失败")
        return payload.get("data")

    def get_media(self, tmdb_id) -> Optional[dict]:
        """按 TMDB 号取权威媒体信息；MP 侧没有返回 None。"""
        data = self._request(
            "GET", f"/api/v1/media/{int(tmdb_id)}",
            params={"type_name": "电视剧",
                    "media_source": "themoviedb"})
        return data if isinstance(data, dict) else None

    def search_title(self, keyword: str) -> list:
        """按标题搜种；返回结果数组（每项带 torrent_info）。"""
        data = self._request(
            "GET", "/api/v1/search/title", params={"keyword": keyword})
        return data if isinstance(data, list) else []

    def start_download(self, media_in: dict, torrent_in: dict) -> str:
        """发起手动下载，返回 download_id（取不到回空串）。"""
        data = self._request(
            "POST", "/api/v1/download/",
            json={"media_in": media_in, "torrent_in": torrent_in})
        if isinstance(data, dict):
            return str(data.get("download_id") or "")
        return ""


# ---------------------------------------------------------------------------
# 选种口径（纯函数，可单测）
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(
    r"S(\d{1,2})((?:E\d{1,3}(?:\s*[-~]\s*E?\d{1,3})?)+)", re.IGNORECASE)
_EPISODE_RE = re.compile(
    r"E(\d{1,3})(?:\s*[-~]\s*E?(\d{1,3}))?", re.IGNORECASE)
_SEASON_MARK_RE = re.compile(r"S0*(\d{1,2})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_PACK_MARKERS = ("全集", "完集", "整季", "全季", "complete")


def parse_episode_coverage(title: str, season: int):
    """从种子标题解析它覆盖指定季的哪些集。

    返回集号集合；整季包（只有季标/全集标记、无逐集号）返回
    字符串 ``"pack"``；解析不出或不属于该季返回 None。
    """
    if not title:
        return None
    episodes: set = set()
    for seg in _SEGMENT_RE.finditer(title):
        if int(seg.group(1)) != int(season):
            continue
        for ep in _EPISODE_RE.finditer(seg.group(2)):
            start = int(ep.group(1))
            end = int(ep.group(2)) if ep.group(2) else start
            if end < start or end - start > 500:
                continue
            episodes.update(range(start, end + 1))
    if episodes:
        return episodes
    lowered = title.lower()
    has_pack_marker = any(m in title or m in lowered
                          for m in _PACK_MARKERS)
    if not has_pack_marker:
        return None
    season_marks = {int(m.group(1))
                    for m in _SEASON_MARK_RE.finditer(title)}
    if season_marks:
        return "pack" if int(season) in season_marks else None
    # 无季标的全集包只认第 1 季（多季剧的裸「全集」多半是整剧
    # 包，给非 1 季认领会下错季）。
    return "pack" if int(season) == 1 else None


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _year_conflict(title: str, media: dict) -> bool:
    """标题里的年份与媒体年份对不上 → 拒（假种实证口径）。"""
    media_year = str((media or {}).get("year") or "").strip()[:4]
    if not media_year.isdigit():
        return False
    years = set(_YEAR_RE.findall(title or ""))
    return bool(years) and media_year not in years


def select_torrents(results, media: dict, season: int,
                    missing_eps) -> list:
    """按口径从搜索结果里挑要发起下载的 torrent_info 列表。

    排序：覆盖缺集数多者优先 → 做种多者优先 → 体积小者优先；
    贪心累积覆盖，直到缺集凑满或达到单季发起上限。
    """
    missing = {int(e) for e in (missing_eps or [])}
    if not missing:
        return []
    candidates = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        info = item.get("torrent_info")
        if not isinstance(info, dict):
            continue
        title = str(info.get("title") or "")
        coverage = parse_episode_coverage(title, season)
        if coverage is None:
            continue
        if _year_conflict(title, media):
            continue
        seeders = _as_int(info.get("seeders"))
        if seeders <= 0:
            continue
        covered = set(missing) if coverage == "pack" \
            else set(coverage) & missing
        if not covered:
            continue
        size = _as_int(info.get("size"))
        if size > 0:
            per_episode = size / len(covered)
            if per_episode < MIN_EPISODE_BYTES \
                    or per_episode > MAX_EPISODE_BYTES:
                continue
        candidates.append((info, covered, seeders, size))
    candidates.sort(key=lambda c: (-len(c[1]), -c[2], c[3] or 0))
    picked: list = []
    got: set = set()
    for info, covered, _seeders, _size in candidates:
        if covered <= got:
            continue
        picked.append(info)
        got |= covered
        if got >= missing or len(picked) >= MAX_DOWNLOADS_PER_SEASON:
            break
    return picked


# ---------------------------------------------------------------------------
# 工人一轮
# ---------------------------------------------------------------------------

def _load_ratio() -> float:
    """1 分钟负载 / CPU 数（取不到按 0：不许因测不准饿死工人）。"""
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 0.0
    return load1 / float(os.cpu_count() or 1)


def _recent_play_starts(db, minutes, now=None) -> int:
    """近 N 分钟全站起播数（让路判据；读失败按 0）。"""
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=minutes)
    try:
        return int(
            db.query(func.count(em.PlayStartEvent.id))
            .filter(em.PlayStartEvent.created_at >= cutoff)
            .scalar() or 0)
    except Exception:  # noqa: BLE001 — 判据读不到不拦补集
        return 0


def _default_session_factory():
    from backend.database import SessionLocal

    return SessionLocal


def _default_client_factory(cfg):
    return MpClient(cfg["mp_download_base"], cfg["mp_download_token"])


_circuit_until = 0.0
_circuit_lock = threading.Lock()


def _reset_circuit() -> None:
    global _circuit_until
    with _circuit_lock:
        _circuit_until = 0.0


def _circuit_open() -> bool:
    with _circuit_lock:
        return time.monotonic() < _circuit_until


def _trip_circuit() -> None:
    global _circuit_until
    with _circuit_lock:
        _circuit_until = time.monotonic() + CIRCUIT_OPEN_SEC


def _started_today(db, now) -> int:
    day_start = datetime.combine(
        now.astimezone(_SHANGHAI).date(), dtime.min)
    return int(
        db.query(func.count(em.MissingEpisodeFillAttempt.id))
        .filter(
            em.MissingEpisodeFillAttempt.status == "started",
            em.MissingEpisodeFillAttempt.created_at >= day_start)
        .scalar() or 0)


def _in_cooldown(db, series_item_id, season, now, cooldown_hours) -> bool:
    horizon = now - timedelta(hours=max(1, int(cooldown_hours)))
    latest = (
        db.query(func.max(em.MissingEpisodeFillAttempt.created_at))
        .filter(
            em.MissingEpisodeFillAttempt.series_item_id == series_item_id,
            em.MissingEpisodeFillAttempt.season_number == int(season))
        .scalar())
    return latest is not None and latest > horizon


def _record_attempt(db, record, season, missing_eps, status, *,
                    torrent=None, download_id="", detail="") -> None:
    torrent = torrent or {}
    db.add(em.MissingEpisodeFillAttempt(
        series_item_id=record.series_item_id,
        tmdb_id=str(record.tmdb_id or ""),
        season_number=int(season),
        missing_eps=json.dumps([int(e) for e in missing_eps]),
        torrent_title=str(torrent.get("title") or "")[:500],
        site_name=str(torrent.get("site_name") or "")[:100],
        download_id=str(download_id or "")[:80],
        status=status,
        detail=str(detail or "")[:300],
    ))
    db.commit()


def fill_once(session_factory=None, cfg=None, client_factory=None,
              now=None) -> dict:
    """跑一轮补集，返回本轮统计（任何情况下都返回，不抛）。

    让路顺序：总开关 → token 未配 → 熔断 → 系统负载 → 起播
    繁忙 → 逐部逐季处理（每日上限/冷却在季粒度守恒）。一部剧
    或一季失败只落 error 账、不拖垮整轮；MP 连续失败达阈值
    开熔断并提前收轮。
    """
    now = now or datetime.now()
    started = time.monotonic()
    stats = {
        "ran": False, "reason": None,
        "checked": 0, "seasons": 0, "started": 0, "no_match": 0,
        "errors": 0, "cooldown_skips": 0, "cap_reached": False,
        "stopped": None,
        "elapsed_sec": 0.0,
        "started_at": now.isoformat(timespec="seconds"),
        "finished_at": None,
    }
    try:
        factory = session_factory or _default_session_factory()
        db = factory()
        try:
            cfg = cfg or mp_fill_config_payload(db)
            if not cfg["mp_episode_fill_enabled"]:
                stats["reason"] = "disabled"
                return stats
            if not str(cfg.get("mp_download_token") or "").strip():
                stats["reason"] = "not_configured"
                logger.info("MP 补集通道未配 token，本轮跳过")
                return stats
            if _circuit_open():
                stats["reason"] = "circuit_open"
                return stats
            if _load_ratio() > LOAD_RATIO_LIMIT:
                stats["reason"] = "load"
                return stats
            if _recent_play_starts(db, 5) >= BUSY_PLAYS_5MIN:
                stats["reason"] = "busy"
                return stats
            stats["ran"] = True
            client = (client_factory or _default_client_factory)(cfg)
            cap = max(0, int(cfg["mp_episode_fill_daily_cap"]))
            remaining = cap - _started_today(db, now)
            if remaining <= 0:
                stats["cap_reached"] = True
                return stats
            records = (
                db.query(em.MissingEpisodeRecord)
                .filter(
                    em.MissingEpisodeRecord.status == "open",
                    em.MissingEpisodeRecord.missing_count > 0)
                .order_by(
                    em.MissingEpisodeRecord.missing_count.asc(),
                    em.MissingEpisodeRecord.first_detected_at.desc(),
                    em.MissingEpisodeRecord.id.asc())
                .limit(MAX_SERIES_PER_ROUND)
                .all())
            consecutive_errors = 0
            for record in records:
                if stats["cap_reached"] or stats["stopped"]:
                    break
                stats["checked"] += 1
                from backend import missing_episodes as me

                detail = me.normalize_missing_detail(
                    record.missing_detail)
                if not detail:
                    continue
                try:
                    media = client.get_media(record.tmdb_id)
                except MpError as exc:
                    stats["errors"] += 1
                    consecutive_errors += 1
                    first_season = sorted(detail)[0]
                    _record_attempt(
                        db, record, first_season,
                        detail[first_season], "error",
                        detail=f"media 接口失败：{exc}"[:300])
                    if consecutive_errors >= CIRCUIT_THRESHOLD:
                        _trip_circuit()
                        stats["stopped"] = "circuit"
                    continue
                if media is None:
                    stats["errors"] += 1
                    consecutive_errors += 1
                    first_season = sorted(detail)[0]
                    _record_attempt(
                        db, record, first_season,
                        detail[first_season], "error",
                        detail="media 接口无此剧")
                    if consecutive_errors >= CIRCUIT_THRESHOLD:
                        _trip_circuit()
                        stats["stopped"] = "circuit"
                    continue
                media_tmdb = str(media.get("tmdb_id") or "")
                if media_tmdb and media_tmdb != str(record.tmdb_id or ""):
                    stats["errors"] += 1
                    first_season = sorted(detail)[0]
                    _record_attempt(
                        db, record, first_season,
                        detail[first_season], "error",
                        detail=f"media 身份不符：MP 返回 tmdb={media_tmdb}")
                    continue
                consecutive_errors = 0
                keywords = []
                for raw_name in (media.get("title"),
                                 media.get("original_title")):
                    name = str(raw_name or "").strip()
                    if name and name not in keywords:
                        keywords.append(name)
                if not keywords:
                    keywords = [record.series_name or ""]
                for season in sorted(detail):
                    if stats["cap_reached"] or stats["stopped"]:
                        break
                    missing_eps = detail[season]
                    if _in_cooldown(
                            db, record.series_item_id, season, now,
                            cfg["mp_episode_fill_cooldown_hours"]):
                        stats["cooldown_skips"] += 1
                        continue
                    stats["seasons"] += 1
                    results: list = []
                    search_error = None
                    for keyword in keywords:
                        try:
                            results = client.search_title(keyword)
                        except MpError as exc:
                            search_error = exc
                            break
                        if results:
                            break
                    if search_error is not None:
                        stats["errors"] += 1
                        consecutive_errors += 1
                        _record_attempt(
                            db, record, season, missing_eps, "error",
                            detail=f"搜种失败：{search_error}"[:300])
                        if consecutive_errors >= CIRCUIT_THRESHOLD:
                            _trip_circuit()
                            stats["stopped"] = "circuit"
                        continue
                    picked = select_torrents(
                        results, media, season, missing_eps)
                    if not picked:
                        stats["no_match"] += 1
                        _record_attempt(
                            db, record, season, missing_eps, "no_match",
                            detail="无合格种子")
                        continue
                    for torrent in picked:
                        if remaining <= 0:
                            stats["cap_reached"] = True
                            break
                        try:
                            download_id = client.start_download(
                                media, torrent)
                        except MpError as exc:
                            stats["errors"] += 1
                            consecutive_errors += 1
                            _record_attempt(
                                db, record, season, missing_eps,
                                "error", torrent=torrent,
                                detail=f"发起下载失败：{exc}"[:300])
                            if consecutive_errors >= CIRCUIT_THRESHOLD:
                                _trip_circuit()
                                stats["stopped"] = "circuit"
                            break
                        consecutive_errors = 0
                        remaining -= 1
                        stats["started"] += 1
                        _record_attempt(
                            db, record, season, missing_eps, "started",
                            torrent=torrent, download_id=download_id)
            return stats
        finally:
            db.close()
    finally:
        _finish_round(stats, started)


# ---------------------------------------------------------------------------
# 状态上报（照 missing_episodes_worker 形状）
# ---------------------------------------------------------------------------

_last_round: dict = {}
_last_lock = threading.Lock()


def runtime_status() -> dict:
    with _last_lock:
        last = dict(_last_round) if _last_round else None
    thread = _fill_thread
    return {
        "running": bool(thread is not None and thread.is_alive()),
        "enabled": None,  # 开关在 DB 配置里，读状态时由 snapshot 补
        "last_round": last,
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _finish_round(stats, started_monotonic):
    stats["elapsed_sec"] = round(time.monotonic() - started_monotonic, 2)
    stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
    with _last_lock:
        _last_round.clear()
        _last_round.update(stats)
    _publish_status()


def _publish_status() -> None:
    try:
        from backend.database import CacheManager

        CacheManager.set(
            STATUS_CACHE_KEY,
            json.dumps(runtime_status(), ensure_ascii=False, default=str),
            ttl=_STATUS_TTL_SEC)
    except Exception:  # noqa: BLE001 — 状态发布失败不影响补集本体
        pass


def status_snapshot(db=None) -> dict:
    """管理总览读口：本进程在跑给本地状态，否则读工人发布的状态。"""
    snap = None
    thread = _fill_thread
    if thread is not None and thread.is_alive():
        snap = runtime_status()
        snap["source"] = "local"
    else:
        try:
            from backend.database import CacheManager

            raw = CacheManager.get(STATUS_CACHE_KEY)
            if raw:
                snap = json.loads(raw)
                snap["source"] = "worker"
        except Exception:  # noqa: BLE001 — 读不到就回本地空状态
            snap = None
    if snap is None:
        snap = runtime_status()
        snap["source"] = "none"
    if db is not None:
        try:
            snap["enabled"] = mp_fill_config_payload(
                db)["mp_episode_fill_enabled"]
        except Exception:  # noqa: BLE001
            pass
    return snap


# ---------------------------------------------------------------------------
# 工人线程（照工人家族 start/stop/registry 形状）
# ---------------------------------------------------------------------------

_fill_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_start_lock = threading.Lock()


def _fill_loop():
    logger.info("MP 缺集补集 worker 启动（间隔 %.0f 秒）",
                ROUND_INTERVAL_SEC)
    # 错峰：启动后先随机等一小段，避免与其它工人同秒齐发。
    _stop_event.wait(random.uniform(5.0, 30.0))
    while not _stop_event.is_set():
        started = time.monotonic()
        try:
            stats = fill_once()
            if stats.get("ran") and (
                    stats.get("started") or stats.get("no_match")
                    or stats.get("errors")):
                logger.info(
                    "MP 补集一轮：处理 %d 部，发起 %d，无种 %d，"
                    "失败 %d，冷却跳过 %d，耗时 %.1fs",
                    stats["checked"], stats["started"],
                    stats["no_match"], stats["errors"],
                    stats["cooldown_skips"], stats["elapsed_sec"])
            elif stats.get("reason") not in (
                    None, "disabled", "not_configured"):
                logger.info("MP 补集本轮让路：%s", stats["reason"])
        except Exception as exc:  # noqa: BLE001 — 循环体异常不许退出
            logger.warning("MP 补集一轮异常: %s", exc, exc_info=True)
        finally:
            try:
                from backend.emby_server import worker_registry as _wr

                _wr.heartbeat(WORKER_NAME)
            except Exception:  # noqa: BLE001
                pass
        elapsed = time.monotonic() - started
        _stop_event.wait(max(60.0, ROUND_INTERVAL_SEC - elapsed))


def start() -> bool:
    """启动补集后台线程（幂等）。

    线程常驻、轮内现读配置：总开关默认关，关时每轮空转返回，
    线上零行为；管理员在管理端打开开关后下一轮自动生效，不用
    重启（照 seek_fulfill 工人形状）。
    """
    global _fill_thread
    with _start_lock:
        if _fill_thread is not None and _fill_thread.is_alive():
            return True
        _stop_event.clear()
        _fill_thread = threading.Thread(
            target=_fill_loop, name="mp-episode-fill", daemon=True)
        _fill_thread.start()
        from backend.emby_server import worker_registry as _wr

        _wr.register(WORKER_NAME, _fill_thread)
        return True


def stop():
    _stop_event.set()
