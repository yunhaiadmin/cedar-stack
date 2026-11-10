"""getUniqueGift 的进程内缓存层（Bot API 10.4 包5，薄层）。

sender.get_unique_gift 是纯传输 wrapper（横切唯一一套）；本模块只
在其上加进程内 TTL 缓存，把缓存口径与传输层分开：

- 命中（查到礼物）：缓存 3600 秒——unique gift 按名查询的结果是
  准静态元数据（图案/背景/稀有度类字段），小时级缓存足够。
- 未命中（名称不存在等失败）：负缓存 300 秒防刷，避免同一个
  错名反复打 Telegram API。
- 不落库、不进 SystemConfig；进程级缓存，多进程各自一份
  （准静态数据可接受）。

现行机器人无任何礼物功能，本层只备未来礼物类玩法调用，
本期无调用方、无页面。
"""
from __future__ import annotations

import threading
import time

from backend.tg_bot import sender

_POSITIVE_TTL_SECONDS = 3600.0
_NEGATIVE_TTL_SECONDS = 300.0

# name -> (expires_at_monotonic, ok, err, gift)
_cache: dict[str, tuple[float, bool, str | None, dict | None]] = {}
_lock = threading.Lock()

# 时钟接缝（测试可替换）：默认单调时钟
_now = time.monotonic


def get_unique_gift(db, name: str) -> tuple[bool, str | None, dict | None]:
    """带进程内 TTL 缓存的按名查 unique gift。

    返回口径与 sender.get_unique_gift 完全一致：
    (是否成功, 错误信息, UniqueGift 原样 dict)。
    """
    now = _now()
    with _lock:
        hit = _cache.get(name)
        if hit is not None and hit[0] > now:
            return hit[1], hit[2], hit[3]
    ok, err, gift = sender.get_unique_gift(db, name)
    ttl = _POSITIVE_TTL_SECONDS if ok else _NEGATIVE_TTL_SECONDS
    with _lock:
        _cache[name] = (_now() + ttl, ok, err, gift)
    return ok, err, gift
