#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重置「假完成」存量：done + 无任何身份的 series/movie 翻回 pending 重刮。

背景（2026-11-23 生产实测，visible 口径 deleted_at IS NULL AND
merged_into_id IS NULL）：enrich_status='done' 且 tmdb_id / imdb_id /
external_ids 全空的 series+movie 共 757 条（series 501、movie 256），
且 enrich_attempts 全部 = 0、enrich_last_error 全空——从没被真正刮过
就被标了 done。

根因（同 PR 修复，见 enrich_worker._enrich_apply 的 _unearned_done
守卫与 tmdb._search_raw 的 401/未配置改判瞬态）：
- _enrich_apply 尾部 has_core 口径：TMDB 搜索分支未进入（工人进程
  configured=False——钥匙后填只刷新 API 进程、工人从不 refresh_keys）
  时，标题+年份齐即判 done，写 source='none' 并把 attempts 清零，
  产物与「真搜过无命中」终态同形、不可区分；
- _search_raw 把全部 key 401 / 未配置当合法空结果返回 []，搜索被
  静默吞掉同样落到 none 终态。
现有四道自愈对这批人覆盖不全：fake_done_patrol 只认 last_scraped_at
IS NULL（这批全有戳）；none 回捞要求无海报（317 条有海报被排除）且
周期 14 天；dirty_done_patrol / retry_unmatched 对 series 容器要求
自身 file_fingerprint（容器恒空）。故一次性脚本重置。

口径（与漏刮人群定义逐字一致）：
    enrich_status='done' AND item_type IN ('series','movie')
    AND deleted_at IS NULL AND merged_into_id IS NULL
    AND metadata_locked 非真
    AND tmdb_id 空 AND imdb_id 空 AND external_ids 空（'{}'/'[]' 视空）

修法：翻 pending + attempts 清零 + 清 next_retry_at/last_error +
priority=50（与 retry_unmatched 同值），并按 (name, year, type) 失效
TMDB 搜索缓存（与 retry_unmatched 同口径），让工人以当前钥匙真刮。
幂等：跑第二遍时人群已不满足 done 口径，匹配数为 0。

安全口径：默认 dry-run（只计数、不写库）；--commit 才真正写库。
--commit 时把受影响条目 id 清单写到 --ids-out（默认
/tmp/fake_done_reset_ids.json），供重置后按同一批 id 统计产出率
（补上身份 / 真搜不中 / 仍在处理）。

跟进（2026-11-23）：13:43 cohort 复核发现 68 条假完成签名复发
（45 条 source='none' 经 _suppress_flood 旁路、23 条
source='nfo'/'multisource' 经 _unearned_done 来源豁免旁路）。
定点重置只动这 68 条：加 --attempts-zero 只匹配 attempts=0 的
签名行——attempts>0 的 done+无身份行是真搜过无命中 / 资料不全超
限的 honest 终态（生产 455 条），不许一并翻回重刮烧配额。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/reset_fake_done_enrich.py
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/reset_fake_done_enrich.py --commit
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/reset_fake_done_enrich.py --attempts-zero --commit --ids-out /tmp/fake_done_followup_reset_ids.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from sqlalchemy import func, or_  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.enrich_worker import (  # noqa: E402
    ENRICH_PRIORITY_RETRY_UNMATCHED,
)


def _blank(col):
    return or_(col.is_(None), func.trim(col) == "")


def _population(db, *, attempts_zero=False):
    """漏刮人群（定义见模块 docstring）。

    attempts_zero=True 时只匹配 attempts=0 的假完成签名行（2026-11-23
    跟进定点重置用）；False 时保持原口径（首轮 757 条全量重置）。
    """
    q = (
        db.query(em.MediaItem)
        .filter(
            em.MediaItem.enrich_status == "done",
            em.MediaItem.item_type.in_(("series", "movie")),
            em.MediaItem.deleted_at.is_(None),
            em.MediaItem.merged_into_id.is_(None),
            em.MediaItem.metadata_locked.isnot(True),
            _blank(em.MediaItem.tmdb_id),
            _blank(em.MediaItem.imdb_id),
            or_(
                em.MediaItem.external_ids.is_(None),
                func.trim(em.MediaItem.external_ids).in_(("", "{}", "[]")),
            ),
        )
    )
    if attempts_zero:
        q = q.filter(em.MediaItem.enrich_attempts == 0)
    return q


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="真正写库（缺省 dry-run 只计数）")
    ap.add_argument("--attempts-zero", action="store_true",
                    help="只匹配 attempts=0 的假完成签名行（跟进定点重置）")
    ap.add_argument("--ids-out", default="/tmp/fake_done_reset_ids.json",
                    help="--commit 时受影响条目 id 清单落盘路径")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = _population(db, attempts_zero=args.attempts_zero).all()
        by_type = Counter(r.item_type for r in rows)
        by_source = Counter((r.metadata_source or "<null>") for r in rows)
        print(f"population: {len(rows)} "
              f"(series={by_type.get('series', 0)}, "
              f"movie={by_type.get('movie', 0)})")
        print(f"by metadata_source: {dict(by_source)}")
        series_ids = [r.id for r in rows if r.item_type == "series"]
        if series_ids:
            children = (
                db.query(em.MediaItem.item_type, func.count(em.MediaItem.id))
                .filter(
                    em.MediaItem.series_id.in_(series_ids),
                    em.MediaItem.item_type.in_(("season", "episode")),
                    em.MediaItem.deleted_at.is_(None),
                    em.MediaItem.merged_into_id.is_(None),
                )
                .group_by(em.MediaItem.item_type)
                .all()
            )
            print(f"children under these series: {dict(children)}")
        for r in rows[:10]:
            print(f"  sample id={r.id} {r.item_type} "
                  f"{r.name!r} year={r.production_year} "
                  f"source={r.metadata_source!r}")
        if not args.commit:
            print("DRY-RUN: no writes (add --commit to apply)")
            return 0

        ids = [r.id for r in rows]
        # 与 retry_unmatched 同口径：失效 TMDB 搜索缓存，让重刮走真搜索
        try:
            from backend.emby_server import tmdb_cache
            invalidated = 0
            for r in rows:
                try:
                    if tmdb_cache.invalidate_search(
                            r.name or "", r.production_year, r.item_type):
                        invalidated += 1
                except Exception:
                    pass
            print(f"search cache invalidated for {invalidated} rows")
        except Exception as exc:  # noqa: BLE001 — 缓存失效失败不挡重置
            print(f"search cache invalidation skipped: {exc}")

        n = (
            _population(db, attempts_zero=args.attempts_zero)
            .update(
                {
                    em.MediaItem.enrich_status: "pending",
                    em.MediaItem.enrich_attempts: 0,
                    em.MediaItem.enrich_next_retry_at: None,
                    em.MediaItem.enrich_last_error: None,
                    em.MediaItem.enrich_priority:
                        ENRICH_PRIORITY_RETRY_UNMATCHED,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        print(f"updated rows: {n}")
        with open(args.ids_out, "w", encoding="utf-8") as fh:
            json.dump(ids, fh)
        print(f"ids written to {args.ids_out}: {len(ids)}")
        remaining = _population(db, attempts_zero=args.attempts_zero).count()
        print(f"population after reset: {remaining}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
