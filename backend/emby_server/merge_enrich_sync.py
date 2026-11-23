# -*- coding: utf-8 -*-
"""合并状态与 enrich_status 的同步口径（2026-11-24 根因修复）。

根因：``enrich_worker._claim_batch`` 的认领谓词带 ``not_merged``
（``merged_into_id`` 非空的行永不认领，设计如此——合并副本的元数据
走主记录），但合并写入方给行打 ``merged_into_id`` 时不清
``enrich_status``：行当时是 pending 就永远卡 pending（生产实证
27,283 条 episode 全 merged+pending、attempts=0、重建导入后从未被
认领，靠人工 SQL 结算）。

本模块收口两个方向的同步动作，全仓合并/解合并写入点共用：

- :func:`settle_merged_row`：行被合并（merged_into_id 落值）时，
  pending/enriching 同步结算为 done——与存量 merged done 行、与
  merge_versions_worker.merge_group 的既有口径一致（副本元数据
  跟随主记录，不占待刮 backlog）。
- :func:`restore_unmerged_row`：行恢复独立（merged_into_id 清空：
  晋升、孤儿解合并、手动 unmerge、软删改指晋升、物理删除 detach）
  时，若它没有自有元数据证据，当初被结算的 done 是「借来的」——
  翻回 pending 等重刮；有自有证据的保持 done。

自有元数据证据判据（:func:`has_own_metadata`）：任一成立即算有——
``last_scraped_at`` 非空；有自有海报（``poster_path`` 或
``primary_image_url`` 非空）；``overview`` 非空；``metadata_source``
为真实来源（非空且非 ``"none"``：tmdb/nfo/douban/inherit/tmdb_img
等都意味着该行走过一次真实落库）。``metadata_locked`` 行是转人工
语义，restore 豁免（翻回 pending 也不会被认领，只会污染待刮计数，
与 fake_done_patrol 的豁免口径一致）。

两个函数都只改传入行的属性、不提交，提交由调用方按原事务节奏做。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 被合并时需要同步结算的在途状态（终态 done/failed 不动）
_SETTLE_FROM = ("pending", "enriching")


def settle_merged_row(row) -> bool:
    """行被合并后同步结算 enrich_status：pending/enriching → done。

    返回是否发生了结算。done/failed 等终态不动（failed 行被合并后
    保持 failed，与合并前语义一致；副本展示走主记录不受影响）。
    """
    if getattr(row, "enrich_status", None) in _SETTLE_FROM:
        row.enrich_status = "done"
        return True
    return False


def has_own_metadata(row) -> bool:
    """该行是否有自有元数据证据（判据见模块 docstring）。"""
    if getattr(row, "last_scraped_at", None) is not None:
        return True
    if (getattr(row, "poster_path", None)
            or getattr(row, "primary_image_url", None)):
        return True
    if getattr(row, "overview", None):
        return True
    source = getattr(row, "metadata_source", None)
    if source and source != "none":
        return True
    return False


def restore_unmerged_row(row) -> bool:
    """行恢复独立后：无自有元数据证据的 done 翻回 pending 等重刮。

    翻回时 attempts 清零、next_retry 清空（与 fake_done_patrol 的
    翻回口径一致，让行能被下一轮认领立即捡起）。返回是否翻回。
    本来就不是 done 的行（pending/enriching/failed）不动。
    """
    if getattr(row, "enrich_status", None) != "done":
        return False
    if getattr(row, "metadata_locked", None):
        return False
    if has_own_metadata(row):
        return False
    row.enrich_status = "pending"
    row.enrich_attempts = 0
    row.enrich_next_retry_at = None
    return True
