#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧集年份存量回填（一次性例程）——网页媒体库审计 PD-01。

背景（2026-11-17 生产实测，ORM 口径 deleted_at IS NULL AND
merged_into_id IS NULL）：series 9,682 部中 4,394 部（45.4%）
production_year 与 premiere_date 同时为 NULL，年份筛选
（``production_year IN (...)``）把这批剧全部排除、按年份排序
无意义、卡片年份位为空。

数据源两级，都不做搜索、不猜身份：
1. **集行年份（优先，纯本地零请求）**：同剧 live 集行
   production_year 的最小值。原审计设想用集行 premiere_date
   推，但生产实测集行 premiere_date 全库恒空（0/294,228），
   真实可用的只有 production_year；口径验证：对已有年份的
   5,270 部剧比对，MIN(集年份) 与剧年份一致 5,250 部
   （99.6%），差 1 年 5 部、差 >1 年 15 部（疑似错挂集）。
   本级只填 production_year（集行没有日期，无从填
   premiere_date）。
2. **TMDB 详情**：第 1 级覆盖不到且有 tmdb_id 的才走
   ``tmdb_client.details()``（两级缓存 + 令牌桶限速，与
   backfill_movie_genres 同口径），经全仓唯一的
   ``extract_air_dates`` 取 first_air_date，同时补
   production_year 与 premiere_date（仍只填空）。

两者皆无（无集年份、无 tmdb_id）的行不猜，计入
still_missing / no_source 清单口径。生产摸底：可由集年份补
4,356 部、残量 38 部里 18 部有 tmdb_id、20 部无任何来源。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_series_years.py            # dry-run
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_series_years.py --commit   # 真写
    ... --commit --limit 500 / --no-tmdb（跳过第 2 级）

幂等：只碰双空行、只填空字段，可反复跑。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from sqlalchemy import func  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.tmdb import extract_air_dates, tmdb_client  # noqa: E402


def _target_query(session):
    """待回填目标：live series 且 production_year 与 premiere_date 双空。"""
    return (session.query(em.MediaItem)
            .filter(em.MediaItem.item_type == "series")
            .filter(em.MediaItem.production_year.is_(None))
            .filter(em.MediaItem.premiere_date.is_(None))
            .filter(em.MediaItem.merged_into_id.is_(None))
            .filter(em.MediaItem.deleted_at.is_(None)))


def episode_year_map(session) -> dict:
    """{series_id: MIN(集 production_year)}，只看 live 集行。

    纯本地数据源、零网络请求。集行 premiere_date 生产恒空，
    不在此口径内（见模块 docstring）。
    """
    rows = (session.query(em.MediaItem.series_id,
                          func.min(em.MediaItem.production_year))
            .filter(em.MediaItem.item_type == "episode")
            .filter(em.MediaItem.series_id.isnot(None))
            .filter(em.MediaItem.production_year.isnot(None))
            .filter(em.MediaItem.merged_into_id.is_(None))
            .filter(em.MediaItem.deleted_at.is_(None))
            .group_by(em.MediaItem.series_id)
            .all())
    return {sid: int(year) for sid, year in rows if sid and year}


def backfill_series_years(session, *, dry_run: bool = True, limit: int = 0,
                          batch_size: int = 200, use_tmdb: bool = True,
                          details_fetcher=None) -> dict:
    """回填双空剧集年份，返回统计 dict。

    stats 键：total（目标数）/ episode_filled（集年份可补）/
    tmdb_filled（TMDB 可补）/ still_missing（两级皆无）/
    no_source（无集年份且无 tmdb_id）/ errors（details 异常）。
    dry_run 时只计数不写库（TMDB 级仍真调取数，与
    backfill_movie_genres 的 dry-run 口径一致）。
    details_fetcher 签名 (tmdb_id: str, kind: str) -> dict | None，
    默认 tmdb_client.details。
    """
    if details_fetcher is None:
        details_fetcher = tmdb_client.details
    stats = {"total": 0, "episode_filled": 0, "tmdb_filled": 0,
             "still_missing": 0, "no_source": 0, "errors": 0}
    total = _target_query(session).count()
    if limit:
        total = min(total, limit)
    stats["total"] = total
    if not total:
        return stats

    ep_years = episode_year_map(session)
    target_ids = [r[0] for r in _target_query(session)
                  .with_entities(em.MediaItem.id)
                  .order_by(em.MediaItem.id).limit(total).all()]
    done = 0
    pending_commit = 0
    for start in range(0, len(target_ids), batch_size):
        chunk = target_ids[start:start + batch_size]
        rows = (session.query(em.MediaItem)
                .filter(em.MediaItem.id.in_(chunk))
                .order_by(em.MediaItem.id).all())
        for item in rows:
            done += 1
            filled = False
            year = ep_years.get(item.id)
            if year:
                if not dry_run and not item.production_year:
                    item.production_year = year
                stats["episode_filled"] += 1
                filled = True
            elif use_tmdb and (item.tmdb_id or "").strip():
                try:
                    data = details_fetcher(str(item.tmdb_id), "series")
                except Exception:  # noqa: BLE001 — 单条失败不许中断整批
                    stats["errors"] += 1
                    data = None
                y, premiere = extract_air_dates(data or {})
                if y is not None or premiere is not None:
                    if not dry_run:
                        if y is not None and not item.production_year:
                            item.production_year = y
                        if premiere is not None and not item.premiere_date:
                            item.premiere_date = premiere
                    stats["tmdb_filled"] += 1
                    filled = True
            elif not (item.tmdb_id or "").strip():
                stats["no_source"] += 1
            if not filled:
                stats["still_missing"] += 1
            if not dry_run:
                pending_commit += 1
        if not dry_run and pending_commit:
            session.commit()
            pending_commit = 0
    if not dry_run and pending_commit:
        session.commit()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="剧集年份回填（PD-01）")
    ap.add_argument("--commit", action="store_true",
                    help="真正写库（默认 dry-run 只计数）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理条数")
    ap.add_argument("--no-tmdb", action="store_true",
                    help="跳过 TMDB 级（只用集年份）")
    args = ap.parse_args(argv)
    session = SessionLocal()
    try:
        stats = backfill_series_years(
            session, dry_run=not args.commit, limit=args.limit,
            use_tmdb=not args.no_tmdb)
    finally:
        session.close()
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] series_years: total={stats['total']} "
          f"episode_filled={stats['episode_filled']} "
          f"tmdb_filled={stats['tmdb_filled']} "
          f"still_missing={stats['still_missing']} "
          f"no_source={stats['no_source']} errors={stats['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
