"""挂载直读出流（2026-10-30 立项；设计 reports/mount-serving-design-20261030.md）。

共享盘原件的主线新出法：``video_stream`` 在门禁/副本流程之后解析
原件时，若原件所在 mount 命中白名单且配了本地挂载根，直接以本地
文件（rclone FUSE 挂载点）经 ``streaming.serve_file`` 出流——绕开
公开链 usercontent 的 Quota 风控与 SA 中继的交付不稳定（试点实测，
2026-10-30：挂载冷态顺序 13.06 MB/s 稳定、seek 1.3–1.6s 零失败；
公开链整段必撞 Quota 页、Range 约半数被拒；生产中继定点读可慢至
60 秒 0 字节）。

口径（定死）：
- 本模块只做判定与路径解析，不做 IO 出流；出流复用 serve_file，
  本模块不改 streaming 任何既有函数行为。
- engages 三条件同时成立：总开关开、mount id 在白名单、mount id
  在本地根映射里。任一不成立 → 返回 None，调用方走现行出法，
  行为与未接入时逐字一致。白名单默认空 ＝ 全站零行为变化；
  配置热读失败（fail-safe）回默认，默认方向永远是「不介入」。
- engages 后本地文件不存在 → 抛 MountServingUnavailable 响亮
  失败，**绝不静默改走公开链/中继**：静默回落会污染灰度数据，
  且等于把用户偷偷送回已证实不可用的路。
- 凭据不在本层：字节由宿主机 rclone 挂载进程以其配置凭据从
  Drive 取数，应用只读本地文件；本层不持有、不拼任何网盘令牌。
- 配置三键走 SystemConfig 热读（integrations.store，全项目统一
  的带缓存读法），管理端保存即生效；回滚 = 清空白名单（或关
  总开关），热生效、不重启、不发版。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlalchemy.orm import Session

from backend.emby_server import mounts as mount_lib
from backend.emby_server.playback_security import safe_local_path

logger = logging.getLogger(__name__)

#: 总开关 SystemConfig 键；默认开（用户亲令＋部署默认开口径）。
#: 关＝挂载直读分支一行不走，完整回退现行出法。
KEY_MOUNT_SERVING_ENABLED = "mount_serving_enabled"
MOUNT_SERVING_ENABLED_DEFAULT = True

#: 白名单 SystemConfig 键；逗号分隔的 storage_mounts id 列表，
#: 默认空。**空＝行为与现行完全一致**，也是回滚位：清空即
#: 一步回滚（热生效）。
KEY_MOUNT_SERVING_MOUNT_IDS = "mount_serving_mount_ids"

#: 本地根映射 SystemConfig 键；格式 ``id=/绝对路径`` 逗号分隔，
#: 默认空。engages 需同时命中白名单与本映射：白名单是「允许」，
#: 根映射是「接好了」——只配白名单没配根＝未接入，不介入。
KEY_MOUNT_SERVING_LOCAL_ROOTS = "mount_serving_local_roots"

#: PlaybackInfo 回落段挂载探测预算 SystemConfig 键（毫秒）。
#: rclone FUSE 冷查 stat 可达 20 秒量级（2026-11-17 生产实测
#: 23 秒），计划层探测无界同步等会把整个 PlaybackInfo 堵死
#: （同日首播 19.6 秒定案）；探测必须有界，超时按未命中。
#: 默认 2000，钳 [100, 10000]（与 config_schema 的
#: MOUNT_SERVING_CONFIG_SCHEMA 同口径）。纯 SystemConfig 键，
#: 不进 env.example（#1527 先例）。
KEY_MOUNT_SERVING_PROBE_BUDGET_MS = "mount_serving_probe_budget_ms"
MOUNT_SERVING_PROBE_BUDGET_MS_DEFAULT = 2000
_PROBE_BUDGET_MS_MIN = 100
_PROBE_BUDGET_MS_MAX = 10000

#: stat 结果进程内缓存 TTL（秒）：同一文件正负结果都缓存，
#: 重复播放不重复打 FUSE。只缓存 stat 本身——engages 判定
#: （开关/白名单/根映射）每次仍热读现算，配置回滚不受缓存拖延。
_STAT_CACHE_TTL_SEC = 300
_STAT_CACHE_MAX_ENTRIES = 4096

DESCRIPTIONS = {
    KEY_MOUNT_SERVING_ENABLED: "挂载直读出流总开关（默认开；白名单为空时无行为变化）",
    KEY_MOUNT_SERVING_MOUNT_IDS: "挂载直读白名单：逗号分隔的 storage_mounts id，默认空",
    KEY_MOUNT_SERVING_LOCAL_ROOTS: "挂载直读本地根映射：id=/绝对路径，逗号分隔，默认空",
    KEY_MOUNT_SERVING_PROBE_BUDGET_MS: "PlaybackInfo 挂载直读探测预算（毫秒，默认 2000，范围 100~10000）",
}

_FALSE_WORDS = ("0", "false", "no", "off")


class MountServingUnavailable(RuntimeError):
    """挂载直读已 engages 但本地文件不可用（响亮失败，不回落）。"""


def _raw_config(db: Optional[Session], key: str) -> str:
    """SystemConfig 热读原始串：无 db / 无行 / 异常一律空串（回退）。"""
    if db is None:
        return ""
    try:
        from backend.integrations import store

        return str(store.get_value(db, key, "") or "")
    except Exception:  # noqa: BLE001 — 配置读取不许把播放打停
        return ""


def serving_enabled(db: Optional[Session]) -> bool:
    """总开关：未配行回默认开；0/false/no/off 视为关。"""
    raw = _raw_config(db, KEY_MOUNT_SERVING_ENABLED).strip().lower()
    if not raw:
        return MOUNT_SERVING_ENABLED_DEFAULT
    return raw not in _FALSE_WORDS


def parse_mount_ids(raw: str, *, strict: bool = False) -> list[int]:
    """白名单串解析：逗号分隔正整数。读路径宽容跳过坏项；
    strict（写入校验）时坏项抛 ValueError。"""
    out: list[int] = []
    for part in str(raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
            if value <= 0:
                raise ValueError
        except (TypeError, ValueError):
            if strict:
                raise ValueError(
                    f"挂载直读白名单必须是逗号分隔的正整数 id：{token!r}")
            continue
        if value not in out:
            out.append(value)
    return out


def parse_local_roots(raw: str, *, strict: bool = False) -> dict[int, str]:
    """根映射串解析：``id=/绝对路径`` 逗号分隔。

    路径不许含逗号（格式约定，挂载点命名避开即可）；根必须是
    绝对路径。读路径宽容跳过坏项；strict 时坏项/重复 id 抛
    ValueError。"""
    out: dict[int, str] = {}
    for part in str(raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        key, sep, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        try:
            mount_id = int(key)
            if mount_id <= 0 or not sep or not os.path.isabs(value):
                raise ValueError
        except (TypeError, ValueError):
            if strict:
                raise ValueError(
                    f"挂载直读本地根映射格式须为 id=/绝对路径：{token!r}")
            continue
        if mount_id in out and strict:
            raise ValueError(f"挂载直读本地根映射 id 重复：{mount_id}")
        out[mount_id] = value
    return out


def whitelist(db: Optional[Session]) -> list[int]:
    return parse_mount_ids(_raw_config(db, KEY_MOUNT_SERVING_MOUNT_IDS))


def local_roots(db: Optional[Session]) -> dict[int, str]:
    return parse_local_roots(_raw_config(db, KEY_MOUNT_SERVING_LOCAL_ROOTS))


def probe_budget_ms(db: Optional[Session]) -> int:
    """PlaybackInfo 挂载探测预算（毫秒）：SystemConfig > 默认
    2000，钳 [100, 10000]；读不到/非法回默认（fail-safe）。"""
    raw = _raw_config(db, KEY_MOUNT_SERVING_PROBE_BUDGET_MS).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return MOUNT_SERVING_PROBE_BUDGET_MS_DEFAULT
    return max(_PROBE_BUDGET_MS_MIN, min(_PROBE_BUDGET_MS_MAX, value))


# ---------------------------------------------------------------------------
# stat 缓存与有界探测（2026-11-17 首播 19.6 秒定案）
# ---------------------------------------------------------------------------
# rclone FUSE 挂载冷查 os.path.isfile 实测 23 秒（热查 0.004 秒）：
# 谁在播放关键路径上同步 stat 谁就被 FUSE 绑架。两层解法：
# ① stat 结果进程内短 TTL 缓存（正负都缓存），重复播放不重复
#    打 FUSE；只缓存 stat 本身，engages 判定每次热读现算——
#    清空白名单的回滚口径不受缓存拖延；
# ② probe_local_file_bounded：stat 丢专用线程池按预算等，
#    超时按未命中返回（维持门禁原判），迟到的结果仍写进缓存，
#    下一次同条目直接命中。原消费者是 PlaybackInfo 池回落段，
#    F1 定案（2026-11-18）后该回落段已删、本函数在计划层暂无
#    消费者——防 FUSE 冷查堵死请求的机制本体保留待用，不删。
# 取流端不走有界版：resolve_local_file 口径逐字不变（engages
# 文件缺失仍响亮失败），只是同样吃 stat 缓存。

_stat_cache: dict[str, tuple[float, bool]] = {}
_stat_cache_lock = threading.Lock()
_stat_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="mount-serving-stat")


def _stat_cache_get(path: str) -> Optional[bool]:
    now = time.monotonic()
    with _stat_cache_lock:
        entry = _stat_cache.get(path)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= now:
            _stat_cache.pop(path, None)
            return None
        return value


def _stat_cache_put(path: str, value: bool) -> None:
    with _stat_cache_lock:
        if len(_stat_cache) >= _STAT_CACHE_MAX_ENTRIES:
            _stat_cache.clear()
        _stat_cache[path] = (
            time.monotonic() + _STAT_CACHE_TTL_SEC, value)


def _clear_stat_cache() -> None:
    """清空 stat 缓存（测试与运维排障用）。"""
    with _stat_cache_lock:
        _stat_cache.clear()


def _stat_and_cache(path: str) -> bool:
    try:
        result = bool(os.path.isfile(path))
    except Exception:  # noqa: BLE001 — stat 异常按不在位（与 isfile 语义一致）
        result = False
    _stat_cache_put(path, result)
    return result


def _stat_is_file_cached(path: str) -> bool:
    hit = _stat_cache_get(path)
    if hit is not None:
        return hit
    return _stat_and_cache(path)


def _stat_is_file_bounded(path: str, budget_ms: int, *, item_id,
                          mount_id) -> Optional[bool]:
    """按预算等 stat 结果：命中缓存直接回；未命中丢线程池等
    至多 budget_ms。超时返回 None 并打 warning 留痕——线程里的
    stat 不取消，迟到结果会写进缓存，下一次同路径直接命中。"""
    hit = _stat_cache_get(path)
    if hit is not None:
        return hit
    future = _stat_executor.submit(_stat_and_cache, path)
    try:
        return future.result(timeout=max(0.01, budget_ms / 1000.0))
    except TimeoutError:
        logger.warning(
            "挂载直读文件探测超时 item=%s mount=%s budget_ms=%s "
            "path=%s（按未命中处理；迟到结果将写入缓存）",
            item_id, mount_id, budget_ms, path)
        return None


def _resolve_candidate(db: Session, item):
    """engages 判定（不查文件）：命中返回 (mount_id, rel, 本地
    绝对路径)；不 engages 返回 None；路径非法抛
    MountServingUnavailable（与 resolve_local_file 原口径一致）。"""
    parsed = mount_lib.parse_mount_path(getattr(item, "file_path", None))
    if parsed is None:
        return None
    mount_id, rel = parsed
    if not serving_enabled(db):
        return None
    if mount_id not in whitelist(db):
        return None
    root = local_roots(db).get(mount_id)
    if not root:
        return None
    mount = None
    try:
        mount = mount_lib.load_mount_fresh(db, mount_id)
    except Exception:  # noqa: BLE001 — 查不到挂载按未接入处理
        mount = None
    if mount is None or not getattr(mount, "is_enabled", False):
        return None
    try:
        path = safe_local_path(root, (rel or "/").lstrip("/"))
    except ValueError as exc:
        raise MountServingUnavailable(
            f"挂载直读路径非法（mount #{mount_id}）：{exc}") from exc
    return mount_id, rel, path


def resolve_local_file(db: Session, item) -> Optional[str]:
    """判定本条目是否走挂载直读，走则返回本地文件绝对路径。

    - 不 engages（非 mount 路径 / 开关关 / 不在白名单 / 没配根 /
      挂载不存在或停用）→ None，调用方走现行出法；
    - engages 但路径非法或文件不存在 → MountServingUnavailable
      （响亮失败，调用方转 503；绝不返回 None 装作没这回事）。

    文件在位确认走 _stat_is_file_cached（短 TTL 缓存），判定
    口径不变：缓存的负结果同样抛错，不许静默回落。
    """
    cand = _resolve_candidate(db, item)
    if cand is None:
        return None
    mount_id, rel, path = cand
    if not _stat_is_file_cached(path):
        raise MountServingUnavailable(
            f"挂载直读已启用但文件不在挂载点（mount #{mount_id}: "
            f"{rel}），未改走其他来源")
    return path


def probe_local_file_bounded(db: Session, item) -> Optional[str]:
    """有界探测（2026-11-17 定案，原 PlaybackInfo 回落段专用；
    F1 后回落段已删、暂无生产消费者，保留待用）。

    与 resolve_local_file 的差别只在文件在位确认一步：stat 按
    probe_budget_ms 预算等，超时按未命中返回 None 并打 warning
    留痕——计划层宁可维持门禁原判（preparing/failed 形态可
    重试），也不许被 FUSE 冷查堵住整个 PlaybackInfo。engages
    但文件缺失 / 路径非法同样按未命中返回 None（计划层现口径：
    探测失败按未命中）；响亮失败是取流端 resolve_local_file
    的口径，本函数不许替代它。
    """
    try:
        cand = _resolve_candidate(db, item)
    except MountServingUnavailable:
        return None
    if cand is None:
        return None
    mount_id, _rel, path = cand
    verdict = _stat_is_file_bounded(
        path, probe_budget_ms(db),
        item_id=getattr(item, "id", None), mount_id=mount_id)
    if verdict is not True:
        return None
    return path


def config_payload(db: Optional[Session] = None) -> dict:
    """管理后台读取：当前值 + 默认值（前端卡片用）。"""
    ids = whitelist(db)
    roots = local_roots(db)
    return {
        "enabled": serving_enabled(db),
        "mount_ids": ids,
        "local_roots": ",".join(
            f"{mid}={roots[mid]}" for mid in sorted(roots)),
        "probe_budget_ms": probe_budget_ms(db),
        "defaults": {
            "enabled": MOUNT_SERVING_ENABLED_DEFAULT,
            "mount_ids": [],
            "local_roots": "",
            "probe_budget_ms": MOUNT_SERVING_PROBE_BUDGET_MS_DEFAULT,
        },
    }


def write_config(db: Session, *, enabled: bool, mount_ids,
                 local_roots: str) -> dict:
    """管理后台写入：校验 → 落库（config_service 家族编排）→
    失效热缓存。非法值抛 ValueError（端点转 400）。"""
    from backend import config_service
    from backend.integrations import store

    if isinstance(mount_ids, (list, tuple)):
        ids: list[int] = []
        for value in mount_ids:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"挂载直读白名单必须是正整数 id：{value!r}")
            if ivalue <= 0:
                raise ValueError(
                    f"挂载直读白名单必须是正整数 id：{value!r}")
            if ivalue not in ids:
                ids.append(ivalue)
    else:
        ids = parse_mount_ids(str(mount_ids or ""), strict=True)
    roots = parse_local_roots(str(local_roots or ""), strict=True)
    values = {
        KEY_MOUNT_SERVING_ENABLED: "true" if enabled else "false",
        KEY_MOUNT_SERVING_MOUNT_IDS: ",".join(str(i) for i in ids),
        KEY_MOUNT_SERVING_LOCAL_ROOTS: ",".join(
            f"{mid}={roots[mid]}" for mid in sorted(roots)),
    }
    config_service.save_config(db, values, schema="mount_serving",
                               descriptions=DESCRIPTIONS)
    store.invalidate(KEY_MOUNT_SERVING_ENABLED,
                     KEY_MOUNT_SERVING_MOUNT_IDS,
                     KEY_MOUNT_SERVING_LOCAL_ROOTS)
    return config_payload(db)
