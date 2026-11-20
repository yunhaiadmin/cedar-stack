#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只存远程地址的存量图片回填（可重跑例程）——海报落盘通道。

背景：刮削期预热默认关（``EMBY_IMAGE_PREWARM``），新条目先只有
``primary_image_url`` / ``backdrop_image_url`` 远程地址；janitor 的
补下载巡检（``image_backlog_patrol``）带播放信号门（BE-18），没有
播放/追剧信号的条目永远轮不到，图只能等第一次有人打开时按需自愈。
本脚本是运维侧的批量通道：把「有远程地址、无本地文件」的 live
条目逐批下载落盘，**不带播放信号门**，与巡检共用同一套下载引擎
（``image_store.prewarm`` + ``localize`` 回读、内容寻址落盘）。

口径：
- 目标：未软删、未合并行，海报（primary_image_url 有、poster_path
  空）或背景（backdrop_image_url 有、backdrop_path 空）缺本地图；
- 幂等：已落盘的行天然不在候选里，可反复跑，跑完再跑报 0；
- 默认 dry-run 只计数、零下载；``--commit`` 才真正下载写库；
- ``--limit`` / ``--batch-size`` 控制单次规模；按 id 升序分批，
  每批提交一次，中途中断已提交批次不回滚。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_image_localize.py            # dry-run
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_image_localize.py --commit   # 真下载写库
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from sqlalchemy import and_, or_  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import image_store  # noqa: E402
from backend.emby_server import models as em  # noqa: E402


def _remote_only_condition():
    """候选口径（与 enrich_worker.image_backlog_patrol 同形，减播放信号门）。"""
    poster_missing = and_(
        em.MediaItem.primary_image_url.isnot(None),
        em.MediaItem.primary_image_url != "",
        or_(em.MediaItem.poster_path.is_(None),
            em.MediaItem.poster_path == ""))
    backdrop_missing = and_(
        em.MediaItem.backdrop_image_url.isnot(None),
        em.MediaItem.backdrop_image_url != "",
        or_(em.MediaItem.backdrop_path.is_(None),
            em.MediaItem.backdrop_path == ""))
    return and_(em.MediaItem.deleted_at.is_(None),
                em.MediaItem.merged_into_id.is_(None),
                or_(poster_missing, backdrop_missing))


def backfill_image_localize(session, *, dry_run: bool = True, limit: int = 0,
                            batch_size: int = 100, prewarm=None,
                            localize=None) -> dict:
    """把只存远程地址的条目图片逐批落盘，返回统计 dict。

    stats 键：total（候选条数）/ filled（至少落下一张图的条数）/
    still_remote（本轮没下到的条数）。dry_run 只统计候选、零下载。
    prewarm / localize 可注入替身（测试用），默认走 image_store。
    """
    if prewarm is None:
        prewarm = image_store.prewarm
    if localize is None:
        localize = image_store.localize
    stats = {"total": 0, "filled": 0, "still_remote": 0}
    target_ids = [r[0] for r in (session.query(em.MediaItem.id)
                                 .filter(_remote_only_condition())
                                 .order_by(em.MediaItem.id)
                                 .limit(limit if limit else None)
                                 .all())]
    stats["total"] = len(target_ids)
    if dry_run or not target_ids:
        return stats
    batch_size = max(1, batch_size)
    for start in range(0, len(target_ids), batch_size):
        chunk = target_ids[start:start + batch_size]
        rows = (session.query(em.MediaItem)
                .filter(em.MediaItem.id.in_(chunk))
                .order_by(em.MediaItem.id).all())
        urls = []
        for row in rows:
            if row.primary_image_url and not row.poster_path:
                urls.append(row.primary_image_url)
            if row.backdrop_image_url and not row.backdrop_path:
                urls.append(row.backdrop_image_url)
        if urls:
            prewarm(urls)
        for row in rows:
            got = False
            if row.primary_image_url and not row.poster_path:
                local = localize(row.primary_image_url,
                                 allow_download=False)
                if local:
                    row.poster_path = local
                    got = True
            if row.backdrop_image_url and not row.backdrop_path:
                local = localize(row.backdrop_image_url,
                                 allow_download=False)
                if local:
                    row.backdrop_path = local
                    got = True
            if got:
                stats["filled"] += 1
            else:
                stats["still_remote"] += 1
        session.commit()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="只存远程地址的存量图片回填")
    ap.add_argument("--commit", action="store_true",
                    help="真正下载写库（默认 dry-run 只计数）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理条数")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="每批条数（每批提交一次）")
    args = ap.parse_args(argv)
    session = SessionLocal()
    try:
        stats = backfill_image_localize(
            session, dry_run=not args.commit, limit=args.limit,
            batch_size=args.batch_size)
    finally:
        session.close()
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] image_localize: total={stats['total']} "
          f"filled={stats['filled']} still_remote={stats['still_remote']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
