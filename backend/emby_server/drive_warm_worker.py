# -*- coding: utf-8 -*-
"""谷歌盘主线「暖地址」工人（goal-35 批 1＋批 2，2026-11-08 用户点选开工）。

为什么有这个工人
----------------
生产实证（设计稿 hidden_files/design-v1.md §0）：近 3 天成功起播
97% 走「绑盘用户播共享条目 → 转存门禁拿到本人副本 → 额度探针
判 quota → 改道本人 OAuth 令牌 API 中继」；PlaybackInfo 冷路径
p50 约 6 秒、exists 现查慢尾可达数秒。本工人把点播时现做的活
（交付计划现算、副本授权、额度探针、令牌刷新、存在性现查）按
信号提前做完，写进点播路径本来就会读的现成载体——交付计划表、
额度裁决缓存、已公开缓存与 Drive 存在性正缓存。

本批范围（批 1：工人骨架＋W5＋W1；批 2：W2＋W3）
--------------------------------------------
- W1 交付计划：按 (用户, 条目) 调 delivery_plan.build_plan 预建，
  落 playback_delivery_plans 表；新鲜计划（过期前余量 > 提前量）
  跳过。计划开关关时整步跳过。
- W2a 副本授权：对该用户最新 ready 副本调
  transfer_variant.ensure_user_copy_public 幂等确保
  anyone-reader；已公开缓存新鲜（ensure 内部同一核对）时零网络
  跳过。无生效中 Drive 绑定或无 ready 副本 → 跳过。
- W2b 额度裁决：调判定点唯一入口
  transfer_variant.probe_user_copy_quota_decision 提前探测，
  裁决落现成缓存（裁决/明细缓存＋ok 复用记录）；新鲜判定逐字
  镜像判定点的吃缓存口径——quota 裁决、ok＋明细 page=quota
  恒新鲜（到期重探是定案口径，工人不续窗）；ok 复用记录须剩
  余 > 跨轮余量（提前量＋一轮间隔）才新鲜，剩余不足先清复用
  再调判定点逼真探测续窗（批 2 验收修复：旧口径只看复用存
  活，TTL 300≈轮周期 280 时覆盖与空窗交替、点播近半落空窗
  现探）。探测开关关时整步跳过。
- 归因仪器（批 2 验收修复）：每轮发布目标集、每步真做事写
  步骤标记（见 TARGETS_CACHE_KEY 处注释），供起播事件回填
  warm_hits / warm_in_signal 两列——验收不再靠计划表 join
  推断命中。
- W3 令牌保鲜：只调 drive_oauth.get_user_drive_access_token
  （全站唯一令牌口）；令牌过期时刻在「刷新边距＋提前量」之外
  时零网络跳过；每用户每轮至多处理一次。
- W5 存在性：对条目原件 fid 与该用户最新 ready 副本 fid 各做
  一次 files.get（SA 池），200 即调 transfer_variant 既有的
  publish_drive_exists_ok 写 Redis 正缓存；404 只记 gone、不写
  墓碑；其他失败只记 failed。SA 池只用于存在性校验。
- 不做：W4（首字节实验，批 3）、转存预建（D7 不做）。

衔接口径（不许漂移）
--------------------
- 只写现成载体，不发明新缓存、不改点播路径一行判定逻辑：
  全冷时点播行为与今天逐字一致（现建计划、现探额度、现授
  权限、现刷令牌、现查存在性）。W2/W3 的 TTL 与边距全部复用
  既有键（探针 TTL env、ok 复用 TTL SystemConfig、已公开缓存
  TTL env、刷新边距常量），本工人不新造配置键。
- 信号收集器原样复用 upstream_warm_worker 的三类收集器（追剧
  新集 / 继续观看含下一集 / 新入库热门），只新增 item→用户展开
  （D1：只暖近窗播过该条目的用户；从未被任何绑定用户播过的条
  目只做无用户维度的 W5）。
- 身份铁律：W1 的计划按 (用户, 条目) 建，绝不把 A 用户的计划
  写给 B 用户；W2/W3 只用目标用户本人的绑定与本人副本（无
  生效中绑定整步跳过），令牌只写回本人绑定行；W5 的存在性
  断言与用户无关（只断言文件在），写进的正缓存键只含 fid、
  不含任何用户信息；SA 令牌只用于 W5 的 files.get，不下发、
  不写任何用户绑定行。
- 存在性实查复用 api._drive_file_exists_sync（全项目唯一的
  files.get 存在性实现），本模块只包一层缝（_exists_check，
  懒导入：import 时不拉起 api 大模块）；正缓存键与
  transfer_variant._DRIVE_EXISTS_OK_PREFIX 逐字一致。
- 资源纪律与旧工人同口径：总开关默认关、低并发、单轮双预算
  （时长 600 秒 / 出站请求 400 次硬帽）、任务间随机抖动、起播
  繁忙或系统高负载整轮让路、失败只记账不冒泡、线程异常不退出
  循环。请求计数：W1 建计划不计；W2a 真确保计 1、W2b 真探测
  计 2（两腿）、W3 真刷新计 1、W5 每次实查计 1（各步新鲜命中
  均不计）。
"""

import itertools
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from backend.emby_server import delivery_plan as dp
from backend.emby_server import upstream_warm_worker as uw
from backend.emby_server.drive_changes import (
    OAUTH_TOKEN_REFRESH_MARGIN_SEC,
)
from backend.emby_server.env_util import env_float, env_int
from backend.emby_server import models as em

logger = logging.getLogger(__name__)

#: worker_registry 登记名（/api/health 与管理总览按此名认人）。
WORKER_NAME = "drive_warm"

#: 运行状态发布缓存键（照 upstream_warm_worker 的形状）。
STATUS_CACHE_KEY = "aetrix:drive_warm:status"
_STATUS_TTL_SEC = 1800

#: Drive 存在性正缓存键前缀——须与
#: transfer_variant._DRIVE_EXISTS_OK_PREFIX /
#: api._DRIVE_EXISTS_OK_PREFIX 逐字一致（api 读、工人写）。
_DRIVE_EXISTS_OK_PREFIX = "aetrix:drive_exists:ok:"

#: 暖归因仪器（批 2 验收修复，2026-11-12）：工人每轮把目标集
#: 发布到 TARGETS_CACHE_KEY（成员 "user_id:item_id"，仅用户维度
#: 目标），每步真做事后给 (用户, 条目) 写步骤标记（TTL 与该步
#: 载体同寿）。起播打点口（api._note_play_start_safe）经
#: warm_attribution 读这两处，给 emby_play_start_events 的
#: warm_hits / warm_in_signal 两列回填真实值——批 0 落列后两列
#: 一直恒 NULL、验收只能靠计划表 join 推断，问题定位被迫绕路。
#: 目标集不存在（工人没在跑/从没发布过）时归因回未判定
#: （None/None），两列保持 NULL，与未接线时逐字一致。
TARGETS_CACHE_KEY = "aetrix:drive_warm:targets"
_MARK_PLAN_PREFIX = "aetrix:drive_warm:mark:plan:"
_MARK_QUOTA_PREFIX = "aetrix:drive_warm:mark:quota:"
_MARK_EXISTS_PREFIX = "aetrix:drive_warm:mark:exists:"


# ---------------------------------------------------------------------------
# 配置（全部 env 热读：改配置不用改代码，脏值回默认——与旧工人口径一致）
# ---------------------------------------------------------------------------

def _env_on(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def drive_warm_config() -> dict:
    """Drive 暖地址工人有效配置（每轮热读一次）。

    键名与 env.example 一一对应；总开关默认关（D6 灰度口径：
    批 1 上线后由用户点头转开并开始计时观察），其余默认值照旧
    工人保守口径。信号窗口与上限照旧工人同名键改前缀。
    """
    return {
        # 总开关（关 = 工人不启动、在跑的整轮让路退出）
        "enabled": _env_on("DRIVE_WARM_ENABLED", False),
        # 轮间隔（秒）
        "interval_sec": env_float(
            "DRIVE_WARM_INTERVAL_SEC", 240.0, 30.0, 86400.0),
        # 轮内并发（条目标并行暖数）
        "concurrency": env_int("DRIVE_WARM_CONCURRENCY", 2, 1, 8),
        # 单轮目标上限（(用户, 条目) 对数，三类信号合计、优先级序截断）
        "max_targets": env_int(
            "DRIVE_WARM_MAX_TARGETS_PER_ROUND", 120, 1, 2000),
        # 单轮总时长预算（秒）：到点不再开新任务
        "round_budget_sec": env_float(
            "DRIVE_WARM_ROUND_BUDGET_SEC", 600.0, 10.0, 7200.0),
        # 单轮出站请求预算（W5 实查计数，W1 建计划不计；默认 400 硬帽）
        "max_requests": env_int(
            "DRIVE_WARM_MAX_REQUESTS_PER_ROUND", 400, 1, 100000),
        # 每条目最多展开的用户数（最近活动倒序，默认 3）
        "users_per_item": env_int(
            "DRIVE_WARM_USERS_PER_ITEM", 3, 1, 50),
        # 复验提前量（秒）：计划过期前 / 正缓存剩余 TTL 小于该值即复暖
        "refresh_lead_sec": env_float(
            "DRIVE_WARM_REFRESH_LEAD_SEC", 30.0, 0.0, 3600.0),
        # 任务间随机抖动上限（秒）：0~该值均匀随机，反风控
        "jitter_sec": env_float(
            "DRIVE_WARM_JITTER_SEC", 3.0, 0.0, 60.0),
        # 让路判据一：近 5 分钟起播数 >= 该值 → 整轮跳过
        "busy_plays_5min": env_int(
            "DRIVE_WARM_BUSY_PLAYS_5MIN", 30, 1, 100000),
        # 让路判据二：1 分钟负载 / CPU 数 > 该值 → 跳过/停轮
        "load_ratio": env_float(
            "DRIVE_WARM_LOAD_RATIO", 0.85, 0.1, 10.0),
        # 信号一：新集回望窗（小时）与每剧集数上限
        "follow_lookback_hours": env_float(
            "DRIVE_WARM_FOLLOW_LOOKBACK_HOURS", 72.0, 1.0, 2160.0),
        "follow_per_series": env_int(
            "DRIVE_WARM_FOLLOW_PER_SERIES", 3, 1, 50),
        # 信号二：继续观看回望（天）与条目上限
        "resume_days": env_float(
            "DRIVE_WARM_RESUME_DAYS", 7.0, 0.5, 90.0),
        "resume_limit": env_int(
            "DRIVE_WARM_RESUME_LIMIT", 60, 1, 1000),
        # 信号三：热度统计窗（小时）/ 新入库界（天）/ 起播次数门槛
        "hot_window_hours": env_float(
            "DRIVE_WARM_HOT_WINDOW_HOURS", 48.0, 1.0, 720.0),
        "hot_new_days": env_float(
            "DRIVE_WARM_HOT_NEW_DAYS", 7.0, 0.5, 365.0),
        "hot_min_plays": env_int(
            "DRIVE_WARM_HOT_MIN_PLAYS", 3, 1, 1000),
    }


# ---------------------------------------------------------------------------
# 信号展开：item → 用户（D1：只暖近窗播过该条目的用户）
# ---------------------------------------------------------------------------

def _follow_users(db, series_id, cfg) -> list:
    """追剧信号的用户展开：该剧的 active 订阅用户（最近订阅在前）。"""
    if not series_id:
        return []
    rows = (
        db.query(em.SeriesFollow.user_id)
        .filter(em.SeriesFollow.series_item_id == series_id,
                em.SeriesFollow.status == "active")
        .order_by(em.SeriesFollow.created_at.desc().nullslast(),
                  em.SeriesFollow.id.desc())
        .limit(cfg["users_per_item"])
        .all())
    return [row[0] for row in rows]


def _resume_users(db, item, cfg, now=None) -> list:
    """继续观看信号的用户展开：近窗内看过该条目的用户（最近在前）。

    直接活动为空时（典型是「下一集」：还没人看过它）回落到同剧
    近窗活动用户——续看某剧的人，下一集暖给他（与收集器
    collect_resume_item_ids「下一集跟随同一用户」同口径）。
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=cfg["resume_days"])
    recency = {}

    def _note(user_id, ts):
        if user_id is None or ts is None:
            return
        if ts > recency.get(user_id, datetime.min):
            recency[user_id] = ts

    for user_id, last_played, updated in (
            db.query(em.UserMediaData.user_id,
                     em.UserMediaData.last_played_at,
                     em.UserMediaData.updated_at)
            .filter(em.UserMediaData.item_id == item.id,
                    (em.UserMediaData.last_played_at >= cutoff)
                    | (em.UserMediaData.updated_at >= cutoff))
            .all()):
        _note(user_id, last_played or updated)
    for user_id, last_update in (
            db.query(em.PlaybackSession.user_id,
                     em.PlaybackSession.last_update_at)
            .filter(em.PlaybackSession.item_id == item.id,
                    em.PlaybackSession.last_update_at >= cutoff)
            .all()):
        _note(user_id, last_update)
    if not recency and getattr(item, "series_id", None):
        # 下一集回落：同剧任一集的近窗活动用户
        for user_id, last_played, updated in (
                db.query(em.UserMediaData.user_id,
                         em.UserMediaData.last_played_at,
                         em.UserMediaData.updated_at)
                .join(em.MediaItem,
                      em.MediaItem.id == em.UserMediaData.item_id)
                .filter(em.MediaItem.series_id == item.series_id,
                        em.MediaItem.merged_into_id.is_(None),
                        (em.UserMediaData.last_played_at >= cutoff)
                        | (em.UserMediaData.updated_at >= cutoff))
                .all()):
            _note(user_id, last_played or updated)
        for user_id, last_update in (
                db.query(em.PlaybackSession.user_id,
                         em.PlaybackSession.last_update_at)
                .join(em.MediaItem,
                      em.MediaItem.id == em.PlaybackSession.item_id)
                .filter(em.MediaItem.series_id == item.series_id,
                        em.MediaItem.merged_into_id.is_(None),
                        em.PlaybackSession.last_update_at >= cutoff)
                .all()):
            _note(user_id, last_update)
    ordered = sorted(recency.items(), key=lambda kv: kv[1], reverse=True)
    return [user_id for user_id, _ts in ordered[:cfg["users_per_item"]]]


def _hot_users(db, item_id, cfg, now=None) -> list:
    """新入库热门信号的用户展开：近窗内成功播过该条目的用户。

    只认 user_id 非空的行——匿名/解析事件不产生用户维度，该条
    目退回无用户维度的 W5-only 目标（调用方处理）。
    """
    now = now or datetime.now()
    window_start = now - timedelta(hours=cfg["hot_window_hours"])
    latest = func.max(em.PlayStartEvent.created_at)
    rows = (
        db.query(em.PlayStartEvent.user_id, latest)
        .filter(em.PlayStartEvent.item_id == item_id,
                em.PlayStartEvent.result == "ok",
                em.PlayStartEvent.user_id.isnot(None),
                em.PlayStartEvent.created_at >= window_start)
        .group_by(em.PlayStartEvent.user_id)
        .order_by(latest.desc())
        .limit(cfg["users_per_item"])
        .all())
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# 目标构建（信号 → (用户, 条目) 目标；优先级 follow > resume > hot）
# ---------------------------------------------------------------------------

def build_targets(db, cfg, now=None):
    """把三类信号收敛成暖目标列表 ``[{user_id, item_id, source}]``。

    收集器原样复用 upstream_warm_worker（条目去重、每剧集数帽、
    下一集展开等口径逐字一致）；本函数只新增 item→用户展开。
    条目在多个信号里出现时只取优先级最高的信号（与旧工人同口径）；
    同 (用户, 条目) 只留一个目标；无用户维度的条目留一个
    ``user_id=None`` 的 W5-only 目标；总量到 max_targets 即停。
    返回 (targets, stats)。
    """
    now = now or datetime.now()
    follow_ids = uw.collect_follow_episode_ids(db, cfg, now)
    resume_ids = uw.collect_resume_item_ids(db, cfg, now)
    hot_ids = uw.collect_hot_item_ids(db, cfg, now)
    stats = {
        "follow_items": len(follow_ids),
        "resume_items": len(resume_ids),
        "hot_items": len(hot_ids),
        "items": 0,
        "targets": 0,
        "w5_only": 0,
    }
    targets, seen_items, seen_pairs = [], set(), set()
    for item_id, source in itertools.chain(
            ((i, "follow") for i in follow_ids),
            ((i, "resume") for i in resume_ids),
            ((i, "hot") for i in hot_ids)):
        if len(targets) >= cfg["max_targets"]:
            break
        if item_id in seen_items:
            continue
        seen_items.add(item_id)
        item = (db.query(em.MediaItem)
                .filter(em.MediaItem.id == item_id).first())
        if item is None:
            continue
        try:
            if source == "follow":
                users = _follow_users(db, item.series_id, cfg)
            elif source == "resume":
                users = _resume_users(db, item, cfg, now)
            else:
                users = _hot_users(db, item_id, cfg, now)
        except Exception:  # noqa: BLE001 — 单条目展开失败不拖垮整轮
            logger.warning("暖地址目标展开失败 item=%s source=%s",
                           item_id, source, exc_info=True)
            continue
        if not users:
            if len(targets) >= cfg["max_targets"]:
                break
            targets.append({"user_id": None, "item_id": int(item_id),
                            "source": source})
            stats["w5_only"] += 1
            continue
        for user_id in users:
            if len(targets) >= cfg["max_targets"]:
                break
            key = (int(user_id), int(item_id))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            targets.append({"user_id": int(user_id),
                            "item_id": int(item_id), "source": source})
    stats["items"] = len(seen_items)
    stats["targets"] = len(targets)
    return targets, stats


# ---------------------------------------------------------------------------
# 衔接缝（与既有机制的唯一接触面；测试在本模块上 monkeypatch 这些
# 函数，等同仓库既有「patch 类/模块属性」的口径）
# ---------------------------------------------------------------------------

def _exists_ok_remaining(file_id):
    """Redis 正缓存剩余 TTL（秒）：键不存在/Redis 不可用回 None。

    供 W5 判断「还新鲜不用再查」：剩余 > 提前量即新鲜。只读，
    永不抛。
    """
    fid = (file_id or "").strip()
    if not fid:
        return None
    try:
        from backend.database import get_redis

        rc = get_redis()
        if rc is None:
            return None
        ttl = rc.ttl(_DRIVE_EXISTS_OK_PREFIX + fid)
        if ttl is None or int(ttl) <= 0:
            return None
        return int(ttl)
    except Exception:  # noqa: BLE001 — Redis 故障按「不新鲜」处理
        return None


def _exists_check(db, item, file_id):
    """存在性实查（SA 池）：复用 api 的 _drive_file_exists_sync。

    返回 "exists"（200，已由该函数写正缓存）/ "gone"（404，文件
    已删/不可见）/ "unavailable"（无挂载 drive_id、401/403/429/
    5xx/超时等——下次轮次再试，不写任何缓存）。懒导入：本模块
    import 时不拉起 api 大模块。永不抛（异常一律转 unavailable）。
    """
    try:
        from backend.emby_server import api as _api

        drive_id = _api._drive_mount_id_for_item(db, item)
        if not drive_id:
            return "unavailable"
        try:
            _api._drive_file_exists_sync(drive_id, file_id)
        except _api._DriveFileGone:
            return "gone"
        except _api._DriveFileUnavailable:
            return "unavailable"
        return "exists"
    except Exception:  # noqa: BLE001 — 实查本身炸了按不可用记账
        logger.debug("暖地址存在性实查异常 fid=%s", file_id, exc_info=True)
        return "unavailable"


def _newest_ready_user_copy_fid(db, user_id, item_id):
    """该用户在该条目上最新 ready 副本的 fid（无则 None）。

    过滤口径与 delivery_plan._probe_user_copy_ready 逐字一致：
    item + owner_scope=user + owner_ref 本人 + status=ready，
    quark:/pan115: 前缀的变体行不算（不是 Drive fid）。
    """
    for cand in (db.query(em.TransferVariant)
                 .filter(em.TransferVariant.item_id == item_id,
                         em.TransferVariant.owner_scope == "user",
                         em.TransferVariant.owner_ref == str(user_id),
                         em.TransferVariant.status == "ready")
                 .order_by(em.TransferVariant.id.desc())
                 .limit(20).all()):
        if (cand.variant_drive_id or "").startswith(("quark:", "pan115:")):
            continue
        fid = (cand.variant_file_id or "").strip()
        if fid:
            return fid
    return None


def _active_drive_binding(db, user_id):
    """该用户生效中的 Drive 绑定行（transfer_variant 既有口径）。

    W2/W3 的前置：无绑定 → 整步跳过（点播门禁本就不许无绑定
    播放，工人不给没凭据的用户烧预算）。懒导入、永不抛（读
    失败按无绑定处理，点播侧自会按现行口径处理该用户）。
    """
    try:
        from backend.emby_server import transfer_variant as tv

        return tv._active_binding(db, user_id, "drive")
    except Exception:  # noqa: BLE001 — 读不到按无绑定跳过
        return None


def _copy_public_valid(db, user_id, fid) -> bool:
    """副本已公开缓存是否新鲜（ensure_user_copy_public 内部同一核对）。

    True = anyone-reader 已授且回库核对通过，ensure 再调也只是
    命中缓存零网络——工人据此记 fresh 不重复调。永不抛。
    """
    try:
        from backend.emby_server import transfer_variant as tv

        return bool(tv._copy_public_cache_valid(db, user_id, fid))
    except Exception:  # noqa: BLE001 — 核对失败按不新鲜（ensure 自会兜底）
        return False


def _ensure_copy_public(db, user_id, fid) -> bool:
    """W2a 执行缝：调 ensure_user_copy_public 幂等确保副本授权。

    只用该用户本人绑定令牌（该函数内部口径），成功（含已授过）
    回 True；无绑定/令牌失效/授权失败回 False。永不抛。
    """
    try:
        from backend.emby_server import transfer_variant as tv

        return bool(tv.ensure_user_copy_public(db, user_id, fid))
    except Exception:  # noqa: BLE001 — ensure 本身永不抛，这里只兜底
        return False


def _quota_probe_enabled() -> bool:
    """额度探测总开关（transfer_variant 既有 env 口径）。永不抛。"""
    try:
        from backend.emby_server import transfer_variant as tv

        return bool(tv.usercopy_quota_probe_enabled())
    except Exception:  # noqa: BLE001 — 读不到按关（点播侧也不会探）
        return False


def _quota_cache_state(user_id, fid) -> dict:
    """额度裁决缓存现状（只读）：{verdict, page, verdict_ttl, reuse_ttl}。

    verdict/page 走 transfer_variant 的现成读口（CacheManager，
    Redis 故障时内存回退）；两个 TTL 走原始 Redis 键读剩余秒数
    （裁决键带域形 rb:d:playback: 与旧无域形都试，与 CacheManager
    的写/读布局逐字一致）。Redis 不可用时 TTL 一律 None——新鲜
    判定退化为「证明不了新鲜」，工人会多调一次判定点，而判定点
    读缓存本身不现探，代价只是本地读、无网络。永不抛。
    """
    state = {"verdict": None, "page": None,
             "verdict_ttl": None, "reuse_ttl": None}
    try:
        from backend.database import CacheManager, get_redis
        from backend.emby_server import transfer_variant as tv

        verdict = CacheManager.get(
            tv._usercopy_quota_cache_key(user_id, fid),
            domain="playback")
        if verdict in ("ok", "quota", "interstitial"):
            state["verdict"] = verdict
            detail = tv.usercopy_quota_probe_detail(user_id, fid)
            state["page"] = detail.get("page")
        rc = get_redis()
        if rc is not None:
            key = tv._usercopy_quota_cache_key(user_id, fid)
            for cand in (f"rb:d:playback:{key}", f"rb:{key}"):
                ttl = rc.ttl(cand)
                if ttl is not None and int(ttl) > 0:
                    state["verdict_ttl"] = int(ttl)
                    break
            rttl = rc.ttl(tv._usercopy_probe_ok_reuse_key(user_id, fid))
            if rttl is not None and int(rttl) > 0:
                state["reuse_ttl"] = int(rttl)
    except Exception:  # noqa: BLE001 — 读不到按全冷（判定点自会处理）
        pass
    return state


def _quota_decide(user_id, fid) -> str:
    """W2b 执行缝：调判定点唯一入口 probe_user_copy_quota_decision。

    判定点对缓存的读法（已知 quota 吃缓存、ok 落复用记录等）
    全在该入口内收口，工人不直调 probe 本体、不复制判定逻辑。
    返回裁决字符串（"ok"/"quota"/"interstitial"/"error"）。
    该入口本身永不抛，这里只兜底转 "error"。
    """
    try:
        from backend.emby_server import transfer_variant as tv

        return tv.probe_user_copy_quota_decision(user_id, fid)
    except Exception:  # noqa: BLE001
        return "error"


def _quota_reuse_clear(user_id, fid) -> None:
    """清掉 ok 复用记录（transfer_variant 既有清口）。

    W2b 续窗用：复用剩余不足跨轮余量时先清再调判定点——判定点
    见复用命中会直接返回 ok、不会真探测，清掉后它才走「失效
    裁决缓存 → 新鲜两腿 → 回写复用」的完整路径把窗口续满。
    永不抛。
    """
    try:
        from backend.emby_server import transfer_variant as tv

        tv._probe_ok_reuse_clear(user_id, fid)
    except Exception:  # noqa: BLE001 — 清不掉按原样（下轮再试）
        pass


def _quota_probe_ttl() -> int:
    """裁决缓存 TTL（transfer_variant 既有 env 口径，默认 600）。"""
    try:
        from backend.emby_server import transfer_variant as tv

        return int(tv._usercopy_quota_probe_ttl())
    except Exception:  # noqa: BLE001 — 读不到按默认
        return 600


def _exists_ok_ttl() -> int:
    """存在性正缓存 TTL（与 publish_drive_exists_ok 同口径）。"""
    try:
        ttl = int(os.getenv("DRIVE_EXISTS_CACHE_TTL_SEC", "") or "")
    except (TypeError, ValueError):
        ttl = 600
    return ttl if ttl > 0 else 600


def _redis():
    """原始 Redis 客户端（不可用回 None）。永不抛。"""
    try:
        from backend.database import get_redis

        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def _publish_targets(targets, cfg) -> None:
    """把本轮目标集发布给起播归因（仅 (用户, 条目) 对）。

    整集替换（先删后加）：上一轮的目标不再是本轮目标时，归因
    立刻按新集判。键 TTL 取两倍最坏轮周期（间隔 + 单轮预算，
    再与状态发布同下限 600），工人停跑后集随 TTL 自然消失、
    归因回未判定。永不抛：发布失败只丢本轮归因。
    """
    rc = _redis()
    if rc is None:
        return
    try:
        members = [f"{t['user_id']}:{t['item_id']}" for t in targets
                   if t.get("user_id") is not None]
        rc.delete(TARGETS_CACHE_KEY)
        if members:
            rc.sadd(TARGETS_CACHE_KEY, *members)
        ttl = int(max(600.0, 2.0 * (cfg["interval_sec"]
                                    + cfg["round_budget_sec"])))
        rc.expire(TARGETS_CACHE_KEY, ttl)
    except Exception:  # noqa: BLE001 — 归因是观测，不许拖垮整轮
        pass


def _mark_step(key, ttl_sec) -> None:
    """写一个步骤标记（值恒 "1"，TTL 由调用方按载体寿命给）。"""
    rc = _redis()
    if rc is None or ttl_sec is None or int(ttl_sec) <= 0:
        return
    try:
        rc.set(key, "1", ex=int(ttl_sec))
    except Exception:  # noqa: BLE001 — 标记失败只丢这步归因
        pass


def _refresh_quota_mark(user_id, item_id, copy_fid, *, probed=False) -> None:
    """quota 归因标记（W2b probed / fresh 两态共用）。

    旧口径只标 probed：稳态轮次里 W2b 多为 fresh（判定点吃现成
    裁决缓存/复用记录、零现探），标记却不续写，等 probed 时写
    的标记过期后 warm_hits 系统性漏报 quota，暖命中组的前后对
    比口径失真。现口径：只要判定点此刻不会现探（复用记录或裁
    决缓存还活着），标记就该在，TTL 取载体剩余（复用优先、其
    次裁决；剩余读不到但裁决在，按裁决 TTL 记）。本轮刚真探过
    （probed）时裁决/复用必是刚按裁决 TTL 写下的，状态读不到
    剩余也按裁决 TTL 兜底——与旧口径的 probed 标记逐字一致。
    fresh 且证明不了还活着就不标。永不抛。
    """
    try:
        if user_id is None or not copy_fid:
            return
        state = _quota_cache_state(user_id, copy_fid)
        ttl = state.get("reuse_ttl") or state.get("verdict_ttl")
        if ttl is None and (probed or state.get("verdict") in (
                "ok", "quota", "interstitial")):
            ttl = _quota_probe_ttl()
        if ttl:
            _mark_step(f"{_MARK_QUOTA_PREFIX}{user_id}:{item_id}", ttl)
    except Exception:  # noqa: BLE001 — 标记失败只丢这步归因
        pass


def warm_attribution(user_id, item_id) -> dict:
    """起播暖归因（api 打点口唯一读口）：{warm_hits, warm_in_signal}。

    warm_hits：本次起播能对上的预热步（plan/quota/exists 的
    子集，按此序）——标记在、且 TTL 与对应载体同寿，标记活着
    即该步的暖成果在起播时仍有效；warm_in_signal：(用户, 条
    目) 是否在工人最近一轮目标集内。目标集不存在（工人没在
    跑）或 Redis 不可用 → 两值 None（未判定，落库保持 NULL）。
    永不抛。
    """
    out = {"warm_hits": None, "warm_in_signal": None}
    if item_id is None:
        return out
    rc = _redis()
    if rc is None:
        return out
    try:
        if not rc.exists(TARGETS_CACHE_KEY):
            return out
        hits = []
        if user_id is not None:
            if rc.get(f"{_MARK_PLAN_PREFIX}{user_id}:{item_id}"):
                hits.append("plan")
            if rc.get(f"{_MARK_QUOTA_PREFIX}{user_id}:{item_id}"):
                hits.append("quota")
        if rc.get(f"{_MARK_EXISTS_PREFIX}{item_id}"):
            hits.append("exists")
        out["warm_hits"] = hits
        if user_id is not None:
            out["warm_in_signal"] = bool(rc.sismember(
                TARGETS_CACHE_KEY, f"{user_id}:{item_id}"))
        return out
    except Exception:  # noqa: BLE001 — 归因读失败按未判定
        return {"warm_hits": None, "warm_in_signal": None}


def _token_expires_at(db, binding):
    """绑定行当前 access_token 的过期时刻（epoch 秒），读不到回 None。

    走 drive_oauth 的凭据解封口（与刷新判定同一读法）；凭据解
    不开（密钥轮换等）回 None，W3 按失败记账、不硬刷（点播时
    该用户自会撞上现行失效口径）。永不抛。
    """
    try:
        from backend.emby_server import drive_oauth as do

        _cred, token = do._drive_credential_of(db, binding)
        expires = int(token.get("expires_at") or 0)
        return expires if expires > 0 else None
    except Exception:  # noqa: BLE001 — 读不到按未知，见 docstring
        return None


def _token_ensure(db, user_id) -> bool:
    """W3 执行缝：调全站唯一令牌口 get_user_drive_access_token。

    未临近过期直接返回现令牌（零网络）、临近才刷新并写回本人
    绑定行——刷新与否由该口内部边距判定，工人不复制。成功回
    True；DriveOAuthError（无绑定/失效/限流）回 False。永不抛。
    """
    try:
        from backend.emby_server import drive_oauth as do

        return bool(do.get_user_drive_access_token(db, user_id))
    except Exception:  # noqa: BLE001 — 令牌口失败只记账
        return False


def _load_ratio() -> float:
    """1 分钟负载 / CPU 数（取不到按 0：不许因测不准饿死工人）。"""
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 0.0
    return load1 / float(os.cpu_count() or 1)


def _recent_play_starts(db, minutes, now=None) -> int:
    """近 N 分钟全站起播数（让路判据；读失败按 0，不许饿死工人）。"""
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=minutes)
    try:
        return int(
            db.query(func.count(em.PlayStartEvent.id))
            .filter(em.PlayStartEvent.created_at >= cutoff)
            .scalar() or 0)
    except Exception:  # noqa: BLE001 — 判据读不到不拦保鲜，见 docstring
        return 0


class _RequestCounter:
    """轮内出站请求计数（线程安全）：预算判据的唯一数据源。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.count = 0

    def add(self, n=1):
        with self._lock:
            self.count += n
        return self.count


# ---------------------------------------------------------------------------
# 暖执行：W1 交付计划 / W2 授权＋裁决 / W3 令牌 / W5 存在性
# ---------------------------------------------------------------------------

def _warm_plan(db, target, cfg) -> str:
    """W1：为 (用户, 条目) 预建交付计划。

    返回 built（新建/重建）/ fresh（计划还新鲜，过期前余量 >
    提前量）/ failed（条目没了或建计划失败）/ skipped（无用户
    维度，或交付计划开关关）。只调 delivery_plan 的既有入口，
    不碰点播判定逻辑；建计划本身是 DB/本地探针，不计出站请求。
    """
    user_id = target.get("user_id")
    item_id = target["item_id"]
    if user_id is None:
        return "skipped"
    if not dp.delivery_plan_enabled(db):
        return "skipped"
    now = datetime.now()
    plan = dp.get_fresh_plan(db, user_id, item_id)
    if plan is not None:
        try:
            expires = datetime.fromisoformat(str(plan.get("expires_at")))
        except (TypeError, ValueError):
            expires = None
        # 跨轮余量（批 2 验收修复）：剩余须撑过「提前量 + 一轮
        # 间隔」才算新鲜——只剩提前量时下一轮还没跑计划就过期，
        # 点播落进空窗命中不了计划腿。提前重建是纯 DB 活、不
        # 计出站请求，代价远小于空窗。
        if expires is not None and \
                (expires - now).total_seconds() > \
                cfg["refresh_lead_sec"] + cfg["interval_sec"]:
            return "fresh"
    item = (db.query(em.MediaItem)
            .filter(em.MediaItem.id == item_id).one_or_none())
    if item is None:
        return "failed"
    built = dp.build_plan(db, user_id, item)
    return "built" if built is not None else "failed"


def _warm_exists(db, item, target, cfg, counter):
    """W5：存在性保鲜，返回 (结果码, 本步实查请求数)。

    查的 fid：条目原件 fid（item.drive_file_id）＋该用户最新
    ready 副本 fid（有才查）。每 fid 先看 Redis 正缓存剩余 TTL，
    剩余 > 提前量即新鲜跳过（不计请求）；否则 SA 池实查 1 次。
    结果码：published（至少一 fid 实查 200 已写正缓存）/
    fresh（全部新鲜）/ gone（实查 404、且无一 fid 写成）/
    failed（实查不可用、且无一 fid 写成）/ skipped（无 fid 可查）
    / budget（轮中预算耗尽，未查完）。
    """
    user_id = target.get("user_id")
    fids = []
    orig = (getattr(item, "drive_file_id", "") or "").strip()
    if orig:
        fids.append(orig)
    if user_id is not None:
        copy_fid = _newest_ready_user_copy_fid(db, user_id, item.id)
        if copy_fid and copy_fid not in fids:
            fids.append(copy_fid)
    if not fids:
        return "skipped", 0
    lead = cfg["refresh_lead_sec"]
    published = gone = failed = fresh = 0
    made = 0
    for fid in fids:
        remaining = _exists_ok_remaining(fid)
        if remaining is not None and remaining > lead:
            fresh += 1
            continue
        if counter.count >= cfg["max_requests"]:
            return "budget", made
        counter.add(1)
        made += 1
        outcome = _exists_check(db, item, fid)
        if outcome == "exists":
            published += 1
        elif outcome == "gone":
            gone += 1
        else:
            failed += 1
    if published > 0:
        return "published", made
    if failed > 0:
        return "failed", made
    if gone > 0:
        return "gone", made
    if fresh == len(fids):
        return "fresh", made
    return "failed", made


def _warm_copy_public(db, target, copy_fid, cfg, counter):
    """W2a：副本 anyone-reader 授权幂等确保，返回 (结果码, 请求数)。

    结果码：ensured（本轮真调 ensure 且成功）/ fresh（已公开
    缓存新鲜，零网络）/ failed（ensure 失败）/ skipped（无用户
    维度、无 ready 副本、或无生效中绑定）/ budget（预算耗尽）。
    副本 fid 由调用方按「最新 ready 副本」口径算好传入（与 W5
    同一 fid）。真确保计 1 次请求。
    """
    user_id = target.get("user_id")
    if user_id is None or not copy_fid:
        return "skipped", 0
    if _active_drive_binding(db, user_id) is None:
        return "skipped", 0
    if _copy_public_valid(db, user_id, copy_fid):
        return "fresh", 0
    if counter.count >= cfg["max_requests"]:
        return "budget", 0
    counter.add(1)
    ok = _ensure_copy_public(db, user_id, copy_fid)
    return ("ensured", 1) if ok else ("failed", 1)


def _warm_quota(db, target, copy_fid, cfg, counter):
    """W2b：额度探针裁决预探，返回 (结果码, 请求数)。

    结果码：probed（本轮真探测，裁决已落缓存/复用记录）/
    fresh（判定点会吃现成缓存、不会现探）/ failed（探测返回
    error）/ skipped（无用户维度、无 ready 副本、无生效中绑定
    或探测开关关）/ budget（预算耗尽）。

    新鲜判定逐字镜像 probe_user_copy_quota_decision 的吃缓存
    口径，三种「判定点不现探」的情形：①裁决缓存为 quota；
    ②裁决为 ok 且明细 page=quota（GET 腿额度页信号）；③ok 复用
    记录还在。①②是 quota 方向：吃缓存到期重探是 2026-10-31
    定案口径，判定点自己不会续 TTL，工人调判定点也只得缓存
    裁决、无从续窗——恒记 fresh、不调判定点。③是 ok 方向（批
    2 验收修复）：复用剩余须 > 跨轮余量（提前量 + 一轮间隔）
    才记 fresh；剩余不足时先清复用再调判定点——判定点见复用
    命中只返回 ok 不真探，清掉后才走完整新鲜探测把复用窗口
    续满。旧口径只看「复用还活着」：第 N 轮探（TTL 300≈轮周
    期 280）、N+1 轮记 fresh、N+2 轮才重探，中间约一个轮周期
    是空窗，点播落进去就现探（验收实证）。其余情形调判定点
    入口真探测，计 2 次请求（两腿形态）。
    """
    user_id = target.get("user_id")
    if user_id is None or not copy_fid:
        return "skipped", 0
    if _active_drive_binding(db, user_id) is None:
        return "skipped", 0
    if not _quota_probe_enabled():
        return "skipped", 0
    state = _quota_cache_state(user_id, copy_fid)
    margin = cfg["refresh_lead_sec"] + cfg["interval_sec"]
    eaten = state["verdict"] == "quota" or (
        state["verdict"] == "ok" and state["page"] == "quota")
    if eaten:
        return "fresh", 0
    reuse_ttl = state["reuse_ttl"]
    if reuse_ttl is not None and reuse_ttl > margin:
        return "fresh", 0
    if counter.count >= cfg["max_requests"]:
        return "budget", 0
    if reuse_ttl is not None and reuse_ttl > 0:
        # 复用还活着但撑不到下下轮：先清，逼判定点真探测续窗。
        _quota_reuse_clear(user_id, copy_fid)
    counter.add(2)  # 先计后探：两腿形态，与 W5 的硬帽口径一致
    verdict = _quota_decide(user_id, copy_fid)
    if verdict == "error":
        return "failed", 2
    return "probed", 2


class _RoundContext:
    """一轮内的跨目标状态（当前只有 W3 的用户去重）。

    W3 按用户处理：同一用户在一轮里有多个目标时只查/刷一次，
    后续目标记 fresh。线程安全（轮内并发执行共用一个实例）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._token_users = set()

    def claim_token_user(self, user_id) -> bool:
        """本轮首次处理该用户回 True，重复回 False。"""
        with self._lock:
            if user_id in self._token_users:
                return False
            self._token_users.add(user_id)
            return True


def _warm_token(db, target, cfg, counter, round_ctx):
    """W3：本人令牌保鲜，返回 (结果码, 请求数)。

    结果码：refreshed（本轮调令牌口且成功——临近过期时该口内
    部真刷新并写回，未临近时是零网络直读）/ fresh（令牌过期
    时刻在刷新边距＋提前量之外，或本轮已处理过该用户）/
    failed（凭据读不到或令牌口失败）/ skipped（无用户维度或
    无生效中绑定）/ budget（预算耗尽）。

    新鲜判据：expires_at > now + 刷新边距（300 秒既有常量）
    + 提前量——保证点播落进边距窗之前工人已先刷过。真调令牌
    口计 1 次请求。
    """
    user_id = target.get("user_id")
    if user_id is None:
        return "skipped", 0
    binding = _active_drive_binding(db, user_id)
    if binding is None:
        return "skipped", 0
    if round_ctx is not None and not round_ctx.claim_token_user(user_id):
        return "fresh", 0
    expires = _token_expires_at(db, binding)
    if expires is None:
        return "failed", 0
    horizon = time.time() + OAUTH_TOKEN_REFRESH_MARGIN_SEC \
        + cfg["refresh_lead_sec"]
    if expires > horizon:
        return "fresh", 0
    if counter.count >= cfg["max_requests"]:
        return "budget", 0
    counter.add(1)
    ok = _token_ensure(db, user_id)
    return ("refreshed", 1) if ok else ("failed", 1)


#: 进程内最近暖过时刻表：(user_id, item_id) → epoch 秒。只作
#: 观测（最近一轮统计的补充），不决定新鲜度——新鲜度以计划
#: 过期时间与正缓存 TTL 为准。
_warmed_at: dict = {}
_warmed_lock = threading.Lock()

#: 最近一轮统计（runtime_status / 管理总览的数据源）。
_last_round: dict = {}
_last_lock = threading.Lock()

_warm_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_start_lock = threading.Lock()


def _warm_one(target, cfg, counter, session_factory, round_start,
              round_ctx=None) -> dict:
    """暖一个 (用户, 条目) 目标，返回各步结果码与请求数。

    步骤顺序照设计稿 §5.2：W1 计划 → W2a 授权 → W2b 裁决 →
    W3 令牌 → W5 存在性。outcome：warmed（至少一步真做了事）/
    fresh（全步新鲜或无需做，且无失败）/ failed（有步骤失败且
    无一步做成）/ budget（预算耗尽）/ load（负载越线停轮）。
    永不抛异常：任何失败都收敛成 failed 记账——暖是后台锦上
    添花，不许把异常漏进工人循环、更不许漏进起播路径（起播
    路径根本不经过这里）。round_ctx 为一轮内的跨目标状态
    （W3 用户去重），直调时可省略（按单目标新上下文处理）。
    """
    _skip = {"w1": "skipped", "w2a": "skipped", "w2b": "skipped",
             "w3": "skipped", "w5": "skipped"}
    if time.monotonic() - round_start > cfg["round_budget_sec"]:
        return {"outcome": "budget", "requests": 0, **_skip}
    if counter.count >= cfg["max_requests"]:
        return {"outcome": "budget", "requests": 0, **_skip}
    if _load_ratio() > cfg["load_ratio"]:
        return {"outcome": "load", "requests": 0, **_skip}
    if round_ctx is None:
        round_ctx = _RoundContext()
    before = counter.count
    plan_ttl = None
    try:
        jitter = cfg["jitter_sec"]
        if jitter > 0:
            time.sleep(random.uniform(0, jitter))
        db = session_factory()
        try:
            item = (db.query(em.MediaItem)
                    .filter(em.MediaItem.id == target["item_id"])
                    .one_or_none())
            if item is None:
                return {"outcome": "failed",
                        "requests": counter.count - before,
                        **{k: "failed" for k in _skip}}
            w1 = _warm_plan(db, target, cfg)
            if w1 == "built":
                try:
                    plan_ttl = dp.plan_ttl_sec(db)
                except Exception:  # noqa: BLE001 — 标记 TTL 读不到就不写
                    plan_ttl = None
            user_id = target.get("user_id")
            copy_fid = None
            if user_id is not None:
                copy_fid = _newest_ready_user_copy_fid(
                    db, user_id, item.id)
            w2a, _n = _warm_copy_public(db, target, copy_fid, cfg,
                                        counter)
            w2b, _n = _warm_quota(db, target, copy_fid, cfg, counter)
            w3, _n = _warm_token(db, target, cfg, counter, round_ctx)
            w5, _made = _warm_exists(db, item, target, cfg, counter)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — 失败只记账，见 docstring
        logger.info("暖地址失败 user=%s item=%s: %s",
                    target.get("user_id"), target["item_id"], exc)
        return {"outcome": "failed",
                "requests": counter.count - before,
                **{k: "failed" for k in _skip}}
    # 暖归因标记（批 2 验收修复）：真做过事的步给 (用户, 条目)
    # 留标记，TTL 与该步载体同寿——起播时标记活着即暖成果仍有
    # 效，api 打点口据此回填 warm_hits。写标记在 DB 会话关闭后
    # 做（只剩 Redis 写），失败自吞（_mark_step 内部）。
    _uid = target.get("user_id")
    _iid = target["item_id"]
    if w1 == "built" and _uid is not None and plan_ttl:
        _mark_step(f"{_MARK_PLAN_PREFIX}{_uid}:{_iid}", plan_ttl)
    if w2b in ("probed", "fresh") and _uid is not None:
        _refresh_quota_mark(_uid, _iid, copy_fid,
                            probed=(w2b == "probed"))
    if w5 == "published":
        _mark_step(f"{_MARK_EXISTS_PREFIX}{_iid}", _exists_ok_ttl())
    if "budget" in (w2a, w2b, w3, w5):
        outcome = "budget"
    elif w1 == "built" or w2a == "ensured" or w2b == "probed" \
            or w3 == "refreshed" or w5 == "published":
        outcome = "warmed"
        with _warmed_lock:
            _warmed_at[(target.get("user_id"), target["item_id"])] = \
                time.time()
    elif w1 == "failed" or w2a == "failed" or w2b == "failed" \
            or w3 == "failed" or w5 in ("failed", "gone"):
        outcome = "failed"
    else:
        outcome = "fresh"
    return {"outcome": outcome, "w1": w1, "w2a": w2a, "w2b": w2b,
            "w3": w3, "w5": w5,
            "requests": counter.count - before}


def _default_session_factory():
    from backend.database import SessionLocal

    return SessionLocal


def _finish_round(stats, started_monotonic):
    """收尾：补齐时间字段、存最近轮、发布状态、清理时刻表。"""
    stats["elapsed_sec"] = round(time.monotonic() - started_monotonic, 2)
    stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
    with _last_lock:
        _last_round.clear()
        _last_round.update(stats)
    horizon = time.time() - 3600.0
    with _warmed_lock:
        for key in [k for k, ts in _warmed_at.items() if ts < horizon]:
            _warmed_at.pop(key, None)
    _publish_status()


def warm_once(session_factory=None) -> dict:
    """跑一轮暖地址，返回本轮统计（任何情况下都返回，不抛）。

    让路顺序：总开关 → 系统负载 → 起播繁忙 → 构建目标 → 低并发
    执行（轮中双预算与负载复查在 _warm_one 里逐条守）。请求计
    数口径见模块头注（W1 不计、W2a 1、W2b 2、W3 1、W5 每次 1）。
    """
    cfg = drive_warm_config()
    started = time.monotonic()
    stats = {
        "ran": False, "reason": None,
        "signals": {"follow": 0, "resume": 0, "hot": 0},
        "items": 0, "targets": 0, "w5_only": 0,
        "warmed": 0, "fresh": 0, "failed": 0, "budget": 0,
        "load_skipped": 0,
        "w1": {"built": 0, "fresh": 0, "failed": 0, "skipped": 0},
        "w2a": {"ensured": 0, "fresh": 0, "failed": 0, "skipped": 0},
        "w2b": {"probed": 0, "fresh": 0, "failed": 0, "skipped": 0},
        "w3": {"refreshed": 0, "fresh": 0, "failed": 0, "skipped": 0},
        "w5": {"published": 0, "fresh": 0, "gone": 0, "failed": 0,
               "skipped": 0, "budget": 0},
        "requests": 0, "elapsed_sec": 0.0,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
    }
    try:
        if not cfg["enabled"]:
            stats["reason"] = "disabled"
            return stats
        if _load_ratio() > cfg["load_ratio"]:
            stats["reason"] = "load"
            return stats
        factory = session_factory or _default_session_factory()
        db = factory()
        try:
            if _recent_play_starts(db, 5) >= cfg["busy_plays_5min"]:
                stats["reason"] = "busy"
                return stats
            targets, sig_stats = build_targets(db, cfg)
        finally:
            db.close()
        stats["signals"] = {
            "follow": sig_stats.get("follow_items", 0),
            "resume": sig_stats.get("resume_items", 0),
            "hot": sig_stats.get("hot_items", 0),
        }
        stats["items"] = sig_stats.get("items", 0)
        stats["targets"] = len(targets)
        stats["w5_only"] = sig_stats.get("w5_only", 0)
        _publish_targets(targets, cfg)
        counter = _RequestCounter()
        round_ctx = _RoundContext()
        outcomes = {"warmed": 0, "fresh": 0, "failed": 0,
                    "budget": 0, "load": 0}
        if targets:
            with ThreadPoolExecutor(
                    max_workers=cfg["concurrency"],
                    thread_name_prefix="drive-warm") as pool:
                futures = [
                    pool.submit(_warm_one, target, cfg, counter,
                                factory, started, round_ctx)
                    for target in targets]
                for future in futures:
                    try:
                        res = future.result()
                    except Exception:  # noqa: BLE001 — _warm_one 本应不抛
                        outcomes["failed"] += 1
                        for key in ("w1", "w2a", "w2b", "w3", "w5"):
                            stats[key]["failed"] += 1
                        continue
                    outcomes[res["outcome"]] += 1
                    for key in ("w1", "w2a", "w2b", "w3", "w5"):
                        code = res.get(key, "skipped")
                        if code in stats[key]:
                            stats[key][code] += 1
        stats["warmed"] = outcomes["warmed"]
        stats["fresh"] = outcomes["fresh"]
        stats["failed"] = outcomes["failed"]
        stats["budget"] = outcomes["budget"]
        stats["load_skipped"] = outcomes["load"]
        stats["requests"] = counter.count
        stats["ran"] = True
        return stats
    finally:
        _finish_round(stats, started)


# ---------------------------------------------------------------------------
# 状态上报（照 upstream_warm_worker 形状：进程内最近轮 + 缓存发布跨进程读）
# ---------------------------------------------------------------------------

def runtime_status() -> dict:
    with _last_lock:
        last = dict(_last_round) if _last_round else None
    thread = _warm_thread
    return {
        "running": bool(thread is not None and thread.is_alive()),
        "enabled": drive_warm_config()["enabled"],
        "last_round": last,
        "reported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _publish_status() -> None:
    try:
        from backend.database import CacheManager

        CacheManager.set(
            STATUS_CACHE_KEY,
            json.dumps(runtime_status(), ensure_ascii=False, default=str),
            ttl=_STATUS_TTL_SEC)
    except Exception:  # noqa: BLE001 — 状态发布失败不影响暖本体
        pass


def status_snapshot() -> dict:
    """管理总览读口：本进程在跑给本地状态，否则读工人发布的状态。"""
    thread = _warm_thread
    if thread is not None and thread.is_alive():
        snap = runtime_status()
        snap["source"] = "local"
        return snap
    try:
        from backend.database import CacheManager

        raw = CacheManager.get(STATUS_CACHE_KEY)
        if raw:
            snap = json.loads(raw)
            snap["source"] = "worker"
            return snap
    except Exception:  # noqa: BLE001 — 读不到就回本地空状态
        pass
    snap = runtime_status()
    snap["source"] = "none"
    return snap


# ---------------------------------------------------------------------------
# 工人线程（照 upstream_warm_worker 的 start/stop/registry 形状）
# ---------------------------------------------------------------------------

def _warm_loop():
    logger.info("Drive 暖地址 worker 启动（间隔 %.0f 秒）",
                drive_warm_config()["interval_sec"])
    while not _stop_event.is_set():
        try:
            stats = warm_once()
            if stats.get("ran"):
                logger.info(
                    "Drive 暖地址一轮：信号 %s，目标 %d，暖好 %d，"
                    "已新鲜 %d，失败 %d，请求 %d，耗时 %.1fs",
                    stats["signals"], stats["targets"], stats["warmed"],
                    stats["fresh"], stats["failed"], stats["requests"],
                    stats["elapsed_sec"])
            elif stats.get("reason") not in (None, "disabled"):
                logger.info("Drive 暖地址本轮让路：%s", stats["reason"])
        except Exception as exc:  # noqa: BLE001 — 循环体异常不许退出
            logger.warning("Drive 暖地址一轮异常: %s", exc, exc_info=True)
        finally:
            try:
                from backend.emby_server import worker_registry as _wr

                _wr.heartbeat(WORKER_NAME)
            except Exception:  # noqa: BLE001
                pass
        _stop_event.wait(drive_warm_config()["interval_sec"])


def start() -> bool:
    """启动暖地址后台线程（幂等；总开关关时不启动）。"""
    global _warm_thread
    if not drive_warm_config()["enabled"]:
        logger.info("Drive 暖地址已禁用（DRIVE_WARM_ENABLED=0）")
        return False
    with _start_lock:
        if _warm_thread is not None and _warm_thread.is_alive():
            return True
        _stop_event.clear()
        _warm_thread = threading.Thread(
            target=_warm_loop, name="drive-warm", daemon=True)
        _warm_thread.start()
        from backend.emby_server import worker_registry as _wr

        _wr.register(WORKER_NAME, _warm_thread)
        return True


def stop():
    _stop_event.set()
