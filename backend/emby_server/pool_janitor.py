# -*- coding: utf-8 -*-
"""公共贡献池巡检 janitor（goal-33 PR-5 保护收尾）。

定位：池的周期性后台收尾工，挂在 worker.py 既有后台线程框架
里，不起新进程。判定本体
都在 pool_gate（熔断/选盘/建单/状态收口），本模块只做周期
调度与批量执行，不另立判定口径。每轮按固定顺序跑七步：

1. 熔断触发扫：active 成员当日 quota_hits 达阈值 → 熔断
   （pool_gate.maybe_trip_member_breaker；copy/serve 记数点已
   当场查过，这里是兜底扫，防记数点之外的累积无人收口）；
2. 熔断半开：冷却到期的 suspended(breaker) 成员跑试探探测
   （pool_gate.breaker_recover_probe），通了恢复、再命中翻倍
   冷却回熔断；
3. 绑定失效兜底：成员仍 active/pending 但绑定行已非 active
   → 走 PR-4 的 on_binding_failed 同口径落 suspended；
   binding_invalid 暂停成员名下的 ready 副本一律标 stale
   停发（补充节 S4：失效盘副本立即下线）；
4. 令牌探活：每轮轮换取 20 个 active 成员取一次令牌（刷新失败在
   drive_oauth 内自落绑定状态），落态后成员没跟上的用
   on_binding_failed 补齐（Drive 砍减第 6 项：轮换抽样，不再每轮
   只探 id 最小的 20 人）；
5. 死副本对账：ready 池副本用成员令牌验活（成员盘文件 SA
   看不见，故 transfer_variant.patrol_once 对池行整行跳过、
   归本步）——条目没了标 orphan、源 fid 对不上标 stale、
   文件 404/403 记 strike（满 2 次标 stale，与 patrol 同
   口径 STALE_STRIKES）；每行 6 小时内只验一次、每轮最多 40 行
   （Drive 砍减第 6 项抽样）；成员当场点播侧的死副本对账
   （GA-032）是热路径自愈，本步是冷路径全量收尾；
6. 容量淘汰（design §6.5，仅池开时）：成员盘用量超上限或
   剩余低于保底 → 按 orphan → stale → 闲置冷副本（热度升
   序）的顺序淘汰，删文件后标 released、删失败标 orphan
   等下轮；淘汰到上限 95% 滞后带即停，防反复横跳。闲置主
   动清理是本步的独立分支（pool_idle_evict_proactive_
   enabled 开时，仅池开时）：不等容量触发，闲置超
   pool_copy_idle_evict_days 天的 ready 副本直接走同一删
   除路径清掉（同每成员每轮上限、同保护口径），盘不紧张
   时冷副本也不会无限躺。退出/清退成员的残留文件不在本
   步（见第 7 步）；
7. 退出残留收尾：exited/ejected 成员名下 status=released
   但文件可能残留的行（PR-4 同步删文件上限 50 的超出部分、
   以及当时令牌已死没删成的），绑定还活着时逐个核对删
   除；删完/确认已不存在/绑定已死无法再删的，落
   retention_reason 标记收口，不再重复处理（文件在贡献者
   自己盘里，删不掉不阻塞任何事，design §7 风险 3）。

热门预热补位（refill，补充节 S4 + 等待优化第二层）在淘汰
之后单独一步（仅池开且转存队列闲置时）：候选四路信号按
追剧订阅 > 求片待办 > 热播丢副本 > 点播排行补缺排序（见
_refill_candidates；求片指到剧行时只取首集 + 最新集，不
整季扫；点播排行一路 2026-11-21 追加：按 emby_play_start_
events 近 7 天排行给从没进过池的热门条目补缺，走 janitor
键 pool_refill_playrank_* 控制），走
create_pool_job(trigger="refill") 备到目标份数——预热项
（追剧/求片）目标 pool_refill_prefetch_target_copies 份
（默认 1）、丢副本热播项目标 pool_item_max_copies 份；
每轮上限 pool_refill_max_per_round（默认 10）。补位单照
常吃选盘打分与单盘日限额，优先级低于观众当场点播的进池
单（闲置门 + 每轮上限双保险）；选盘时跳过失败记忆里的
成员×源盘组合（pool_gate 失败记忆），单条目建单失败只记
blocked、不拖垮整步。

主链路资源纪律（后台红线）：本 janitor 不抢播放/API 的
CPU 与 DB 连接——单线程顺序执行、每步有批量上限（对账 40
行/轮、淘汰 20 份/成员/轮、残留 30 行/轮、补位 3 单/轮、
令牌探活 20 人/轮）、轮间隔 JANITOR_INTERVAL_SEC（30
分钟）、Drive 调用只经成员令牌的 about/get/delete 小请求，
不读文件字节。每步独立 try/except + 独立提交，一步失败只
记 stats、不拖垮整轮。每轮收尾一条结构化日志。

配置：行为阈值全走既有 pool 配置键（熔断两键、容量/保底/
闲置淘汰天数、副本上限），闲置主动清理开关走 pool_janitor
家族键；轮间隔与批量上限是运维节律、
非业务口径，作模块常量（同 PR-4 _EXIT_DELETE_LIMIT 先例）。
池总开关关时：第 6/8 步（淘汰/补位）跳过（池不存在就不
生产新动作），其余卫生步照跑（无成员/无数据时天然空转，
线上零变化）。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from backend.emby_server import models as em
from backend.emby_server import pool_gate
from backend.emby_server import transfer_variant as _tv

logger = logging.getLogger(__name__)

#: 轮间隔（秒）：低频收尾活，30 分钟一轮足够（熔断有记数点
#: 当场触发兜底，本轮间隔只影响兜底扫与半开试探的时效）。
JANITOR_INTERVAL_SEC = 1800
#: 每轮死副本对账行数上限。
RECONCILE_BATCH = 40
#: 每轮每成员淘汰份数上限。
EVICT_MAX_PER_MEMBER = 20
#: 每轮退出残留收尾行数上限。
CLEANUP_BATCH = 30
#: 每轮令牌探活成员数上限。
TOKEN_CHECK_BATCH = 20
#: 巡检每轮 get_file 总量上限（Drive 砍减第 6 项）：死副本对账与
#: 退出残留收尾共用同一预算，30 分钟一轮最多 40 次 files.get；
#: 对账吃满后残留收尾顺延到下一轮。
JANITOR_GET_FILE_BUDGET = 40
#: 死副本对账每行最短复验间隔（秒）：6 小时——同一份副本 6 小时内
#: 只验一次，刚验过的不重复打 get_file（Drive 砍减第 6 项）。
RECONCILE_CHECK_INTERVAL_SEC = 6 * 3600
#: 补位热度窗（天）：近窗内有起播才算热条目（补充节 S4）。
#: 每轮补位上限已改为 janitor 配置键 pool_refill_max_per_round
#: （等待优化第二层，默认 10），不再是模块常量。
REFILL_HOT_WINDOW_DAYS = 7
#: 残留收尾完成标记（写 retention_reason，防每轮重复核对）：
#: cleaned = 文件已删或已确认不存在；closed = 绑定已死、无
#: 法再删，按 design §7 风险 3 收口（行已 released 即完结）。
CLEANED_MARKER = "pool_janitor_cleaned"
CLOSED_MARKER = "pool_janitor_closed"


# ------------------------------------------------------------ 通用小件


def _member_client(db, member, client_factory=None):
    """成员盘客户端：绑定 active 才建得出来，否则回 None。

    client_factory(db, binding) 可注入（测试替身）；默认
    UserDriveApiClient（成员自己的令牌，绝不回落 SA）。
    """
    binding = pool_gate._member_binding(db, member)
    if binding is None or binding.status != "active":
        return None, binding
    try:
        if client_factory is not None:
            return client_factory(db, binding), binding
        return _tv.UserDriveApiClient(db, binding), binding
    except Exception:  # noqa: BLE001 — 建不出来按无令牌处理
        logger.warning("池巡检客户端构建失败 member=%s",
                       getattr(member, "id", None), exc_info=True)
        return None, binding


def _actively_playing_variant_ids(db, now) -> set:
    """正在播的池副本 id 集合（淘汰保护）：窗口内有起播事件
    的变体行。口径沿 transfer_cleanup 的正在播窗口（600 秒，
    形态复用：那边按用户会话判，本池按副本起播事件判——池
    副本的属主是成员、观众是别人，会话表对不上副本行）。"""
    from backend.emby_server.transfer_cleanup import (
        ACTIVE_PLAYING_WINDOW_SEC)

    since = now - timedelta(seconds=ACTIVE_PLAYING_WINDOW_SEC)
    rows = (db.query(em.PoolPlayEvent.variant_id)
            .filter(em.PoolPlayEvent.created_at >= since)
            .distinct().all())
    return {int(r[0]) for r in rows if r[0] is not None}


# ------------------------------------------------------------ 1/2 熔断扫与半开


def _sweep_breaker_trips(db, now) -> dict:
    stats = {"tripped": 0}
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.status == "active")
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        if pool_gate.maybe_trip_member_breaker(db, member, now=now):
            stats["tripped"] += 1
    db.commit()
    return stats


def _sweep_breaker_recovery(db, now, *, probe_fn=None) -> dict:
    stats = {"recovered": 0, "retripped": 0}
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.status == "suspended",
                       em.PoolMember.suspended_reason == "breaker",
                       em.PoolMember.breaker_until.isnot(None),
                       em.PoolMember.breaker_until <= now)
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        outcome = pool_gate.breaker_recover_probe(
            db, member, probe_fn=probe_fn, now=now)
        if outcome == "recovered":
            stats["recovered"] += 1
        elif outcome == "retripped":
            stats["retripped"] += 1
    db.commit()
    return stats


# ------------------------------------------------------------ 3/4 绑定兜底与令牌探活


def _sweep_binding_backstop(db, now) -> dict:
    stats = {"suspended": 0, "copies_staled": 0}
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.status.in_(("active", "pending")),
                       em.PoolMember.binding_id.isnot(None))
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        binding = pool_gate._member_binding(db, member)
        if binding is not None and binding.status != "active":
            pool_gate.on_binding_failed(db, binding)
            stats["suspended"] += 1
    db.commit()
    # 失效盘副本立即下线（S4）：binding_invalid 暂停成员名下
    # 还挂着 ready 的行一律标 stale（serve 口径本就跳过暂停
    # 成员，这里把行状态也对齐，防成员恢复后死副本复活）。
    dead = (db.query(em.PoolMember)
            .filter(em.PoolMember.status == "suspended",
                    em.PoolMember.suspended_reason == "binding_invalid")
            .all())
    for member in dead:
        rows = (db.query(em.TransferVariant)
                .filter(em.TransferVariant.owner_scope == "pool",
                        em.TransferVariant.owner_ref == str(member.id),
                        em.TransferVariant.status == "ready")
                .all())
        for row in rows:
            row.status = "stale"
            stats["copies_staled"] += 1
    db.commit()
    return stats


def _sweep_token_probe(db, now) -> dict:
    stats = {"checked": 0, "suspended": 0}
    member_ids = [r[0] for r in
                  db.query(em.PoolMember.id)
                  .filter(em.PoolMember.status == "active",
                          em.PoolMember.binding_id.isnot(None))
                  .order_by(em.PoolMember.id.asc()).all()]
    if not member_ids:
        return stats
    # 轮换窗口（Drive 砍减第 6 项抽样）：按 30 分钟轮次取不同的连续
    # 20 人，使全部成员轮流被探——旧实现每轮固定取 id 最小的 20 人，
    # 成员超过 20 后尾部成员永不被探。轮次由 now 的 30 分钟桶决定，
    # 无进程内状态，测试可按 now 复现。
    round_index = int(now.timestamp() // JANITOR_INTERVAL_SEC)
    start = (round_index * TOKEN_CHECK_BATCH) % len(member_ids)
    picked = [member_ids[(start + i) % len(member_ids)]
              for i in range(min(TOKEN_CHECK_BATCH, len(member_ids)))]
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.id.in_(picked))
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        binding = pool_gate._member_binding(db, member)
        if binding is None or binding.status != "active":
            continue
        stats["checked"] += 1
        # 取令牌本身即探活：刷新失败在 drive_oauth 内落绑定
        # 状态（_apply_refresh_failure → _mark_failed → 池连带
        # on_binding_failed）；本口吞异常回 ""，故探完回读
        # 绑定与成员状态对账，没跟上的用同一钩子补齐。
        pool_gate.pool_member_access_token(db, member.id)
        db.expire_all()
        binding = pool_gate._member_binding(db, member)
        fresh = (db.query(em.PoolMember)
                 .filter(em.PoolMember.id == member.id).first())
        if (binding is not None and binding.status != "active"
                and fresh is not None and fresh.status == "active"):
            pool_gate.on_binding_failed(db, binding)
            stats["suspended"] += 1
    db.commit()
    return stats


# ------------------------------------------------------------ 5 死副本对账


def _sweep_reconcile_copies(db, now, *, client_factory=None,
                            get_file_budget=None) -> dict:
    """死副本对账：每行副本 6 小时内只验一次（Drive 砍减第 6 项抽样）。

    只取 last_checked_at 为空或早于 now-6h 的 ready 行，按最久未验
    优先、每轮最多 RECONCILE_BATCH 行；get_file 次数受整轮预算
    （get_file_budget，{"remaining": N}）约束，耗尽即停、余行下轮再验。
    """
    stats = {"checked": 0, "stale": 0, "orphaned": 0, "errors": 0}
    due_since = now - timedelta(seconds=RECONCILE_CHECK_INTERVAL_SEC)
    rows = (db.query(em.TransferVariant)
            .filter(em.TransferVariant.owner_scope == "pool",
                    em.TransferVariant.status == "ready",
                    (em.TransferVariant.last_checked_at.is_(None)
                     | (em.TransferVariant.last_checked_at < due_since)))
            .order_by(em.TransferVariant.last_checked_at.asc()
                      .nulls_first(),
                      em.TransferVariant.id.asc())
            .limit(RECONCILE_BATCH).all())
    clients: dict = {}
    for row in rows:
        try:
            member_id = int((row.owner_ref or "").strip())
        except (TypeError, ValueError):
            continue
        member = (db.query(em.PoolMember)
                  .filter(em.PoolMember.id == member_id).first())
        if member is None or member.status != "active":
            # 非 active 成员的副本下线归第 3 步/生命周期口，
            # 本步只验活在池成员的副本。
            continue
        item = (db.query(em.MediaItem)
                .filter(em.MediaItem.id == row.item_id,
                        em.MediaItem.deleted_at.is_(None))
                .first())
        if item is None:
            row.status = "orphan"
            stats["orphaned"] += 1
            continue
        item_fid = (getattr(item, "drive_file_id", "") or "").strip()
        if item_fid and row.source_file_id and \
                item_fid != row.source_file_id:
            # 源条目换了 fid：这份副本是旧片，立即下线（§6.4）。
            row.status = "stale"
            stats["stale"] += 1
            continue
        if member.id not in clients:
            clients[member.id] = _member_client(
                db, member, client_factory=client_factory)
        client, _binding = clients[member.id]
        if client is None:
            continue
        fid = (row.variant_file_id or "").strip()
        if not fid:
            continue
        if get_file_budget is not None and get_file_budget["remaining"] <= 0:
            break
        try:
            if get_file_budget is not None:
                get_file_budget["remaining"] -= 1
            client.get_file(f"myDrive:{member.user_id}", fid)
        except (_tv._NotFound, _tv._Forbidden):
            row.stale_count = int(row.stale_count or 0) + 1
            row.last_checked_at = now
            stats["checked"] += 1
            if row.stale_count >= _tv.STALE_STRIKES:
                row.status = "stale"
                stats["stale"] += 1
        except Exception:  # noqa: BLE001 — 429/5xx/网络：不记 strike
            stats["errors"] += 1
            logger.warning("池副本对账 get_file 异常 variant=%s"
                           "（不记 strike）", row.id, exc_info=True)
            continue
        else:
            row.stale_count = 0
            row.last_checked_at = now
            stats["checked"] += 1
    db.commit()
    return stats


# ------------------------------------------------------------ 6 容量淘汰


def _evict_candidates(db, member, cfg, now, playing_ids) -> list:
    """淘汰候选（已排序）：orphan → stale → 闲置冷 ready。

    闲置口径（design §6.5）：last_played_at 为空取 created_at，
    超过 pool_copy_idle_evict_days 天的 ready 行才入选（0 =
    不按闲置淘汰）；同档内按热度升序（近 7 天起播数少者在
    前、再按 last_played_at 旧者在前）。retention_until 未到
    与正在播的行恒跳过。
    """
    rows = (db.query(em.TransferVariant)
            .filter(em.TransferVariant.owner_scope == "pool",
                    em.TransferVariant.owner_ref == str(member.id),
                    em.TransferVariant.status.in_(
                        ("ready", "stale", "orphan")))
            .all())

    def _blocked(row) -> bool:
        if row.id in playing_ids:
            return True
        until = getattr(row, "retention_until", None)
        return until is not None and until > now

    orphans = sorted(
        (r for r in rows if r.status == "orphan" and not _blocked(r)),
        key=lambda r: r.id)
    stales = sorted(
        (r for r in rows if r.status == "stale" and not _blocked(r)),
        key=lambda r: r.id)
    idle_days = int(cfg["pool_copy_idle_evict_days"])
    readies = []
    if idle_days > 0:
        from sqlalchemy import func

        since7 = now - timedelta(days=7)
        ready_ids = [r.id for r in rows if r.status == "ready"]
        play_counts = dict(
            db.query(em.PoolPlayEvent.variant_id,
                     func.count(em.PoolPlayEvent.id))
            .filter(em.PoolPlayEvent.created_at >= since7,
                    em.PoolPlayEvent.variant_id.in_(ready_ids))
            .group_by(em.PoolPlayEvent.variant_id).all()) \
            if ready_ids else {}
        for row in rows:
            if row.status != "ready" or _blocked(row):
                continue
            basis = row.last_played_at or row.created_at
            if basis is None or (now - basis).days < idle_days:
                continue
            readies.append((
                int(play_counts.get(row.id, 0)),
                row.last_played_at or datetime.min,
                row.id, row))
        readies.sort(key=lambda t: (t[0], t[1], t[2]))
    return orphans + stales + [t[3] for t in readies]


def _execute_evictions(db, member, client, candidates, *,
                       should_stop=None, usage=0,
                       reason_for) -> dict:
    """淘汰删除执行（容量触发与闲置主动清理共用的唯一删除路径）。

    按候选顺序逐个删文件：成功标 released、失败标 orphan 等
    下轮；每成员每轮上限 EVICT_MAX_PER_MEMBER；should_stop
    (stats, usage) 在每份删除前判定、真则提前收手（容量分支
    的滞后带停口）；收尾留一条 pool_evict 决策痕并提交。
    reason_for(stats, usage) 在收尾时按最终计数拼 reason。
    """
    stats = {"evicted": 0, "delete_failed": 0, "freed_bytes": 0}
    snapshot = []
    marker = f"myDrive:{member.user_id}"
    for row in candidates:
        if stats["evicted"] >= EVICT_MAX_PER_MEMBER:
            break
        if should_stop is not None and should_stop(stats, usage):
            break
        fid = (row.variant_file_id or "").strip()
        snapshot.append({"variant_id": int(row.id),
                         "item_id": int(row.item_id),
                         "status": row.status,
                         "size_bytes": int(row.size_bytes or 0)})
        if fid:
            try:
                client.delete_file_ignore_missing(marker, fid)
            except Exception:  # noqa: BLE001 — 删失败标 orphan 等下轮
                logger.warning("池淘汰删文件失败 member=%s variant=%s",
                               member.id, row.id, exc_info=True)
                row.status = "orphan"
                stats["delete_failed"] += 1
                continue
        row.status = "released"
        usage -= int(row.size_bytes or 0)
        stats["freed_bytes"] += int(row.size_bytes or 0)
        stats["evicted"] += 1
    if snapshot:
        from backend.emby_server import transfer_decisions as _decisions

        _decisions.note_decision(
            scope="pool_evict", resource_key=f"member:{member.id}",
            decision=f"evicted:{stats['evicted']}",
            reason=reason_for(stats, usage),
            candidates=snapshot)
    db.commit()
    return stats


def _evict_member_once(db, member, cfg, now, *, client_factory=None,
                       quota_fn=None) -> dict:
    """单盘容量淘汰一轮：触发判定 + 按候选顺序删到滞后带为止。"""
    stats = {"evicted": 0, "delete_failed": 0, "freed_bytes": 0,
             "triggered": False}
    cap = int(member.capacity_cap_bytes
              or cfg["pool_member_capacity_bytes"])
    reserve = int(cfg["pool_member_reserve_free_bytes"])
    rows = (db.query(em.TransferVariant)
            .filter(em.TransferVariant.owner_scope == "pool",
                    em.TransferVariant.owner_ref == str(member.id),
                    em.TransferVariant.status.in_(
                        ("ready", "stale", "orphan")))
            .all())
    usage = sum(int(r.size_bytes or 0) for r in rows)
    quota = pool_gate._member_quota(db, member, quota_fn=quota_fn)
    free = None
    if quota is not None:
        free = _tv._to_int(quota.get("free"))
    over_cap = cap > 0 and usage > cap
    low_free = free is not None and free < reserve
    if not over_cap and not low_free:
        return stats
    stats["triggered"] = True
    target_usage = int(cap * 0.95) if cap > 0 else 0
    client, _binding = _member_client(
        db, member, client_factory=client_factory)
    if client is None:
        # 令牌建不出来：删不了文件，本轮只记账不假装已淘。
        stats["no_client"] = True
        return stats
    playing_ids = _actively_playing_variant_ids(db, now)
    candidates = _evict_candidates(db, member, cfg, now, playing_ids)

    def _at_band(inner, cur_usage):
        cap_ok = (not over_cap) or cur_usage <= target_usage
        free_ok = (not low_free) or (
            free is not None
            and free + inner["freed_bytes"] >= reserve)
        return cap_ok and free_ok

    done = _execute_evictions(
        db, member, client, candidates, should_stop=_at_band,
        usage=usage,
        reason_for=lambda _s, final_usage: (
            f"usage={final_usage} cap={cap} free={free} "
            f"reserve={reserve}"))
    stats.update(done)
    return stats


def _sweep_evictions(db, cfg, now, *, client_factory=None,
                     quota_fn=None) -> dict:
    stats = {"members": 0, "evicted": 0, "delete_failed": 0,
             "freed_bytes": 0}
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.status == "active")
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        one = _evict_member_once(
            db, member, cfg, now, client_factory=client_factory,
            quota_fn=quota_fn)
        if one.get("triggered"):
            stats["members"] += 1
        stats["evicted"] += one["evicted"]
        stats["delete_failed"] += one["delete_failed"]
        stats["freed_bytes"] += one["freed_bytes"]
    return stats


# ------------------------------------------ 6b 闲置主动清理


def _idle_proactive_enabled(db) -> bool:
    """闲置主动清理开关（pool_janitor 家族键，默认开）：读取
    异常按关处理（fail-closed，不许因读不到配置就多删文件）。"""
    try:
        return bool(pool_gate.janitor_config_payload(
            db)["pool_idle_evict_proactive_enabled"])
    except Exception:  # noqa: BLE001
        logger.warning("闲置主动清理开关读取失败，按关处理",
                       exc_info=True)
        return False


def _evict_idle_member_once(db, member, cfg, now, *,
                            client_factory=None) -> dict:
    """单盘闲置主动清理一轮（不依赖容量触发）：只取闲置超标的
    ready 副本（候选口径与容量淘汰共用 _evict_candidates——
    正在播、retention_until 未到的恒跳过），删除/标态/决策
    留痕与容量淘汰共用 _execute_evictions。"""
    stats = {"evicted": 0, "delete_failed": 0, "freed_bytes": 0,
             "triggered": False}
    idle_days = int(cfg["pool_copy_idle_evict_days"])
    if idle_days <= 0:
        # 闲置天数 0 = 闲置淘汰关：与容量分支同口径不淘。
        return stats
    playing_ids = _actively_playing_variant_ids(db, now)
    candidates = [
        row for row in _evict_candidates(db, member, cfg, now,
                                         playing_ids)
        if row.status == "ready"]
    if not candidates:
        return stats
    client, _binding = _member_client(
        db, member, client_factory=client_factory)
    if client is None:
        # 令牌建不出来：删不了文件，本轮只记账不假装已清。
        stats["no_client"] = True
        return stats
    stats["triggered"] = True
    done = _execute_evictions(
        db, member, client, candidates,
        reason_for=lambda _s, _u: f"proactive_idle days={idle_days}")
    stats.update(done)
    return stats


def _sweep_idle_proactive_evictions(db, cfg, now, *,
                                    client_factory=None) -> dict:
    """闲置主动清理一轮（第 6 步的独立分支）：逐个 active 成员
    清闲置超标的 ready 副本，不看容量上限与剩余空间。"""
    stats = {"members": 0, "evicted": 0, "delete_failed": 0,
             "freed_bytes": 0}
    members = (db.query(em.PoolMember)
               .filter(em.PoolMember.status == "active")
               .order_by(em.PoolMember.id.asc()).all())
    for member in members:
        one = _evict_idle_member_once(
            db, member, cfg, now, client_factory=client_factory)
        if one.get("triggered"):
            stats["members"] += 1
        stats["evicted"] += one["evicted"]
        stats["delete_failed"] += one["delete_failed"]
        stats["freed_bytes"] += one["freed_bytes"]
    return stats


# ------------------------------------------------------------ 7 退出残留收尾


def _sweep_leftover_cleanup(db, now, *, client_factory=None,
                           get_file_budget=None) -> dict:
    """退出残留收尾：逐行核对已 released 行的文件是否还在。

    get_file 次数受整轮预算（get_file_budget，{"remaining": N}）约束，
    耗尽即停、余行下轮再核（行未落标记，不会丢）。"""
    stats = {"cleaned": 0, "closed": 0, "errors": 0}
    rows = (db.query(em.TransferVariant)
            .filter(em.TransferVariant.owner_scope == "pool",
                    em.TransferVariant.status == "released",
                    em.TransferVariant.retention_reason.is_(None))
            .order_by(em.TransferVariant.id.asc())
            .limit(CLEANUP_BATCH).all())
    clients: dict = {}
    for row in rows:
        try:
            member_id = int((row.owner_ref or "").strip())
        except (TypeError, ValueError):
            row.retention_reason = CLOSED_MARKER
            stats["closed"] += 1
            continue
        member = (db.query(em.PoolMember)
                  .filter(em.PoolMember.id == member_id).first())
        if member is None or member.status not in (
                pool_gate.POOL_MEMBER_TERMINAL_STATUSES):
            # 还在池的成员名下 released 行不归本步（淘汰已当场
            # 删文件、生命周期下线另有口径），不碰。
            continue
        if member.id not in clients:
            clients[member.id] = _member_client(
                db, member, client_factory=client_factory)
        client, binding = clients[member.id]
        fid = (row.variant_file_id or "").strip()
        if client is None or not fid:
            # 绑定已死/无文件 id：删不掉也不必再删——文件在
            # 贡献者自己盘里，行已 released 即完结（§7 风险 3）。
            row.retention_reason = CLOSED_MARKER
            stats["closed"] += 1
            continue
        if get_file_budget is not None and get_file_budget["remaining"] <= 0:
            break
        try:
            if get_file_budget is not None:
                get_file_budget["remaining"] -= 1
            client.get_file(f"myDrive:{member.user_id}", fid)
        except (_tv._NotFound, _tv._Forbidden):
            row.retention_reason = CLEANED_MARKER
            stats["cleaned"] += 1
            continue
        except Exception:  # noqa: BLE001 — 暂时性错误，下轮再核
            stats["errors"] += 1
            continue
        try:
            client.delete_file_ignore_missing(
                f"myDrive:{member.user_id}", fid)
        except Exception:  # noqa: BLE001 — 删失败下轮再试，不标完成
            logger.warning("池残留删文件失败 variant=%s",
                           row.id, exc_info=True)
            stats["errors"] += 1
            continue
        row.retention_reason = CLEANED_MARKER
        stats["cleaned"] += 1
    db.commit()
    return stats


# ------------------------------------------------------------ 8 热条目补位


def _lost_pool_copy(db, item_id) -> bool:
    """该条目是否「因下线丢过副本」：有不可下发的池行（stale/
    released/orphan），或有 ready 行挂在不可下发的成员名下
    （暂停/熔断/失效），才算丢过；从没满过份数的热片不归
    丢副本补位（§6.4 下线条目不自动重进的边界，S4 例外只限
    成员失效跌破这一种）。"""
    holders = pool_gate._servable_pool_holder_ids(db, item_id)
    all_rows = (db.query(em.TransferVariant)
                .filter(em.TransferVariant.item_id == item_id,
                        em.TransferVariant.owner_scope == "pool")
                .all())
    for row in all_rows:
        if row.status in ("stale", "released", "orphan"):
            return True
        if row.status == "ready":
            try:
                owner_id = int((row.owner_ref or "").strip())
            except (TypeError, ValueError):
                owner_id = None
            if owner_id not in holders:
                return True
    return False


def _live_item(db, item_id):
    """活条目（未软删、未合并）或 None。"""
    if item_id is None:
        return None
    return (db.query(em.MediaItem)
            .filter(em.MediaItem.id == item_id,
                    em.MediaItem.deleted_at.is_(None),
                    em.MediaItem.merged_into_id.is_(None))
            .first())


def _series_first_latest_episodes(db, series_id):
    """某剧的首集 + 最新集（live 集）：求片指到剧行时只预热
    这两集（生产实测待办剧集级缺口 6,417 集，整季预热不可
    行）；首集是入坑点、最新集是追更点。无 live 集回 []。"""
    eps = (db.query(em.MediaItem)
           .filter(em.MediaItem.item_type == "episode",
                   em.MediaItem.series_id == series_id,
                   em.MediaItem.deleted_at.is_(None),
                   em.MediaItem.merged_into_id.is_(None))
           .all())
    if not eps:
        return []
    first = min(
        eps,
        key=lambda e: (getattr(e, "season_number", None) or 0,
                       getattr(e, "episode_number", None) or 0,
                       int(e.id)))
    dated = [e for e in eps if getattr(e, "date_added", None)]
    latest = (max(dated, key=lambda e: (e.date_added, int(e.id)))
              if dated else first)
    return [first] if first.id == latest.id else [first, latest]


def _seek_request_items(db, req) -> list:
    """一条求片待办行 → 待预热的库内条目（movie/episode 行）。

    - missing_episodes：先按 request_detail 的缺集明细
      （季 → 集号）精确定位到单集行；
    - emby_item_id：电影/单集直接取行；剧行展开为首集 +
      最新集（见 _series_first_latest_episodes）；
    - 对不上库内活行回 []（片还没入库，等入库后再说）。"""
    from backend import missing_episodes as _me

    out = []
    if (req.request_kind or "") == "missing_episodes":
        detail = _me.parse_request_detail(req.request_detail)
        series_id = detail.get("series_item_id")
        missing = _me.normalize_missing_detail(detail.get("missing"))
        for season_no, ep_nos in sorted(missing.items()):
            for ep_no in ep_nos:
                try:
                    season_no = int(season_no)
                    ep_no = int(ep_no)
                except (TypeError, ValueError):
                    continue
                ep = (db.query(em.MediaItem)
                      .filter(em.MediaItem.item_type == "episode",
                              em.MediaItem.series_id == series_id,
                              em.MediaItem.season_number == season_no,
                              em.MediaItem.episode_number == ep_no,
                              em.MediaItem.deleted_at.is_(None),
                              em.MediaItem.merged_into_id.is_(None))
                      .first())
                if ep is not None:
                    out.append(ep)
        if out:
            return out
    try:
        item_id = int((req.emby_item_id or "").strip())
    except (TypeError, ValueError):
        return out
    item = _live_item(db, item_id)
    if item is None:
        return out
    if item.item_type == "series":
        return _series_first_latest_episodes(db, item.id)
    return [item]


def _follow_candidates(db, target) -> list:
    """追剧信号：各 active 订阅下订阅后到货的新集（最新集在前）。
    生产实测有效订阅仅 1 个，量小但意图最强（用户明确要追的
    剧，新集一到就可能点），排第一优先级。"""
    from backend.emby_server import series_follow as _sf

    out = []
    follows = (db.query(em.SeriesFollow)
               .filter(em.SeriesFollow.status == "active")
               .order_by(em.SeriesFollow.id.asc()).all())
    for follow in follows:
        try:
            eps, _unknown = _sf._episodes_after_follow(db, follow)
        except Exception:  # noqa: BLE001 — 单剧失败跳过不杀整步
            logger.warning("预热追剧取集失败 follow=%s",
                           getattr(follow, "id", None), exc_info=True)
            continue
        eps = sorted(
            eps,
            key=lambda e: (getattr(e, "date_added", None)
                           or datetime.min, int(e.id)),
            reverse=True)
        for ep in eps:
            if getattr(ep, "deleted_at", None) is not None:
                continue
            out.append({"signal": "follow", "item": ep,
                        "target": target,
                        "detail": f"follow={follow.id}"})
    return out


def _seek_candidates(db, target) -> list:
    """求片信号：pending/approved 待办行映射到的库内条目，
    附议多 → 早提交的在前。生产实测 177 条待办映射 193 个
    库内条目、100% 无池副本，是缺口最大的信号；但这是「想
    要」而非「在播」，只做窄切口（剧行首集 + 最新集）。"""
    from backend import models as base_models

    out = []
    reqs = (db.query(base_models.MovieRequest)
            .filter(base_models.MovieRequest.status.in_(
                ("pending", "approved")),
                base_models.MovieRequest.request_kind.in_(
                    ("seek", "missing_episodes")))
            .order_by(base_models.MovieRequest.vote_count.desc(),
                      base_models.MovieRequest.created_at.asc())
            .all())
    for req in reqs:
        try:
            items = _seek_request_items(db, req)
        except Exception:  # noqa: BLE001 — 单行失败跳过不杀整步
            logger.warning("预热求片映射失败 request=%s",
                           getattr(req, "id", None), exc_info=True)
            continue
        for item in items:
            out.append({"signal": "seek", "item": item,
                        "target": target,
                        "detail": f"request={req.id}"})
    return out


def _hot_candidates(db, cfg, now, max_copies) -> list:
    """热播信号（补充节 S4 原口径）：近 7 天有池起播、且因下线
    跌破份数的条目，按「24h 起播多 → 7 天起播多 → 体积小」
    排序；目标份数是 pool_item_max_copies（与预热项的 1 份
    目标不同：丢过副本的热片要补满冗余）。"""
    from sqlalchemy import case, func

    since7 = now - timedelta(days=REFILL_HOT_WINDOW_DAYS)
    since24 = now - timedelta(days=1)
    hot = (db.query(
        em.PoolPlayEvent.item_id,
        func.count(em.PoolPlayEvent.id).label("plays_7d"),
        func.sum(case(
            (em.PoolPlayEvent.created_at >= since24, 1), else_=0)
        ).label("plays_24h"))
        .filter(em.PoolPlayEvent.created_at >= since7)
        .group_by(em.PoolPlayEvent.item_id)
        .all())
    enriched = []
    for item_id, plays_7d, plays_24h in hot:
        item = _live_item(db, item_id)
        if item is None:
            continue
        enriched.append((
            -int(plays_24h or 0), -int(plays_7d or 0),
            int(getattr(item, "size", None) or 0), item,
            int(plays_7d or 0), int(plays_24h or 0)))
    enriched.sort(key=lambda t: (t[0], t[1], t[2]))
    out = []
    for _n24, _n7, _size, item, plays_7d, plays_24h in enriched:
        if not _lost_pool_copy(db, item.id):
            continue
        out.append({"signal": "hot", "item": item,
                    "target": max_copies,
                    "detail": (f"plays_24h={plays_24h} "
                               f"plays_7d={plays_7d}")})
    return out


def _playrank_candidates(db, cfg, now, target) -> list:
    """点播排行信号（2026-11-21 池优化）：近 7 天起播排行
    （emby_play_start_events 全口径，含走用户盘/挂载的起播）
    前 N 的条目，按点播数降序。既有 hot 信号只认池起播且
    「丢过副本」的条目——从没进过池的热门片（观众一直在吃
    现转存长链）不在其内，本信号补的就是这块缺口：让热门
    片在池里提前有现成副本，点播链缩到「池里有 → 直接播」。
    取数上限走 janitor 键 pool_refill_playrank_top_n，开关
    pool_refill_playrank_enabled（默认开）。目标份数与追剧/
    求片同为预热目标（默认 1 份）。"""
    from sqlalchemy import func

    jcfg = pool_gate.janitor_config_payload(db)
    if not jcfg["pool_refill_playrank_enabled"]:
        return []
    top_n = int(jcfg["pool_refill_playrank_top_n"])
    if top_n <= 0:
        return []
    since = now - timedelta(days=REFILL_HOT_WINDOW_DAYS)
    rows = (db.query(
        em.PlayStartEvent.item_id,
        func.count(em.PlayStartEvent.id).label("plays"))
        .filter(em.PlayStartEvent.created_at >= since,
                em.PlayStartEvent.item_id.isnot(None))
        .group_by(em.PlayStartEvent.item_id)
        .order_by(func.count(em.PlayStartEvent.id).desc(),
                  em.PlayStartEvent.item_id.asc())
        .limit(top_n).all())
    out = []
    for item_id, plays in rows:
        item = _live_item(db, item_id)
        if item is None:
            continue
        out.append({"signal": "playrank", "item": item,
                    "target": target,
                    "detail": f"plays_7d={int(plays or 0)}"})
    return out


def _refill_candidates(db, cfg, now) -> list:
    """本轮补位候选（已排好序、已按条目去重，取最先的信号）：
    追剧 > 求片 > 热播（丢副本补位）> 点播排行（从没进过池
    的热门补缺）。同一条目多信号命中时只保留最高优先级
    那一次。"""
    jcfg = pool_gate.janitor_config_payload(db)
    prefetch_target = max(1, int(
        jcfg["pool_refill_prefetch_target_copies"]))
    max_copies = max(1, int(cfg["pool_item_max_copies"]))
    ordered = []
    seen = set()
    for cand in (_follow_candidates(db, prefetch_target)
                 + _seek_candidates(db, prefetch_target)
                 + _hot_candidates(db, cfg, now, max_copies)
                 + _playrank_candidates(db, cfg, now,
                                         prefetch_target)):
        item_id = int(cand["item"].id)
        if item_id in seen:
            continue
        seen.add(item_id)
        ordered.append(cand)
    return ordered


def _sweep_refill(db, cfg, now, *, quota_fn=None) -> dict:
    stats = {"created": 0, "blocked": 0, "skipped_busy": 0,
             "skipped_bad_combo": 0,
             "by_signal": {"follow": 0, "seek": 0, "hot": 0,
                           "playrank": 0}}
    queue = _tv.queue_stats(db, now=now)
    if int(queue.get("due_count") or 0) > 0 or \
            int(queue.get("running") or 0) > 0:
        # 只吃闲置产能：转存队列有人在等/在跑时补位整步跳过。
        stats["skipped_busy"] = 1
        return stats
    jcfg = pool_gate.janitor_config_payload(db)
    max_per_round = max(0, int(jcfg["pool_refill_max_per_round"]))
    for cand in _refill_candidates(db, cfg, now):
        if stats["created"] >= max_per_round:
            break
        item = cand["item"]
        target = cand["target"]
        servable = (pool_gate._servable_pool_copy_count(db, item.id)
                    + pool_gate._pool_inflight_for_item(db, item.id))
        if servable >= target:
            continue
        holders = pool_gate._servable_pool_holder_ids(db, item.id)
        try:
            job = pool_gate.create_pool_job(
                db, item, trigger="refill", quota_fn=quota_fn,
                exclude_member_ids=holders)
        except _tv.TransferError as exc:
            # 全员都被失败记忆挡住才记 skipped_bad_combo（选盘
            # 已无可用成员）；其余建单拒绝只记 blocked。
            if (exc.kind == "no_member"
                    and pool_gate.bad_combo_member_ids_for_item(
                        db, item)):
                stats["skipped_bad_combo"] += 1
            stats["blocked"] += 1
            continue
        except Exception:  # noqa: BLE001 — 单条目失败不杀整步
            logger.warning("补位建单失败 item=%s",
                           getattr(item, "id", None), exc_info=True)
            stats["blocked"] += 1
            continue
        from backend.emby_server import transfer_decisions as _decisions

        _decisions.note_decision(
            scope="pool_pick", resource_key=f"item:{item.id}",
            decision=f"member:{job.owner_ref}",
            reason=(f"refill signal={cand['signal']} "
                    f"{cand['detail']} servable={servable}"
                    f"<target={target}"),
            candidates=[])
        stats["created"] += 1
        stats["by_signal"][cand["signal"]] += 1
    return stats


# ------------------------------------------------------------ 轮次收口与守护线程


def run_pool_janitor_once(db, now=None, *, client_factory=None,
                          quota_fn=None, probe_fn=None) -> dict:
    """跑一整轮巡检（七步 + 补位），返回各步统计 dict。

    永不抛异常：每步独立 try/except + 出错回滚该步事务，
    一步坏不拖垮整轮（守护线程与测试共用本入口）。
    """
    now = now or datetime.now()
    cfg = pool_gate.pool_config_payload(db)
    enabled = bool(cfg["pool_enabled"])
    stats: dict = {"pool_enabled": enabled}
    # 整轮 get_file 预算（Drive 砍减第 6 项）：死副本对账与退出残留
    # 收尾共用，30 分钟一轮最多 JANITOR_GET_FILE_BUDGET 次。
    get_file_budget = {"remaining": JANITOR_GET_FILE_BUDGET}

    def _step(name, fn):
        try:
            stats[name] = fn()
        except Exception:  # noqa: BLE001 — 单步失败不拖垮整轮
            logger.warning("池巡检步骤失败 step=%s", name, exc_info=True)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            stats[name] = {"error": True}

    _step("breaker_trips", lambda: _sweep_breaker_trips(db, now))
    _step("breaker_recovery", lambda: _sweep_breaker_recovery(
        db, now, probe_fn=probe_fn))
    _step("binding_backstop", lambda: _sweep_binding_backstop(db, now))
    _step("token_probe", lambda: _sweep_token_probe(db, now))
    _step("reconcile", lambda: _sweep_reconcile_copies(
        db, now, client_factory=client_factory,
        get_file_budget=get_file_budget))
    if enabled:
        _step("evict", lambda: _sweep_evictions(
            db, cfg, now, client_factory=client_factory,
            quota_fn=quota_fn))
        if _idle_proactive_enabled(db):
            _step("idle_proactive", lambda:
                  _sweep_idle_proactive_evictions(
                      db, cfg, now, client_factory=client_factory))
    _step("leftover_cleanup", lambda: _sweep_leftover_cleanup(
        db, now, client_factory=client_factory,
        get_file_budget=get_file_budget))
    if enabled:
        _step("refill", lambda: _sweep_refill(
            db, cfg, now, quota_fn=quota_fn))
    logger.info("pool_janitor round %s", stats)
    return stats


_janitor_thread: threading.Thread | None = None
_stop_event = threading.Event()
_start_lock = threading.Lock()


def _janitor_loop():
    # 起步先等一小段，避开 worker 启动高峰；之后每
    # JANITOR_INTERVAL_SEC 一轮，开关/成员状态都在轮内现读，
    # 后台改配置不用重启。
    if _stop_event.wait(60):
        return
    while not _stop_event.is_set():
        try:
            from backend.database import SessionLocal

            db = SessionLocal()
            try:
                run_pool_janitor_once(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 — 线程永不因一轮异常死掉
            logger.warning("池巡检轮次异常", exc_info=True)
        _stop_event.wait(JANITOR_INTERVAL_SEC)


def start() -> bool:
    """启动池巡检后台线程（常驻；无成员/池关时轮内天然空转）。"""
    global _janitor_thread
    with _start_lock:
        if _janitor_thread and _janitor_thread.is_alive():
            return True
        _stop_event.clear()
        _janitor_thread = threading.Thread(
            target=_janitor_loop, name="pool-janitor", daemon=True)
        _janitor_thread.start()
        try:
            from backend.emby_server import worker_registry as _wr

            _wr.register("pool_janitor", _janitor_thread)
        except Exception:  # noqa: BLE001 — 注册失败不影响线程本身
            pass
        return True


def stop():
    """停池巡检后台线程（测试/关机用）。"""
    _stop_event.set()
