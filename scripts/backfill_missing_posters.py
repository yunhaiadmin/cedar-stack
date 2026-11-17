#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无海报作品存量回填（一次性例程）——网页媒体库审计 PD-02。

背景（2026-11-17 生产实测，ORM 口径）：260 部 movie＋227 部
series 的 poster_path 与 primary_image_url 同时为空（共 487
部，占作品 2.2%），卡片只能落占位。其中 324 部有 tmdb_id、
163 部无 tmdb_id；486 部 enrich_status 已 done（补全跑过仍
无图：要么 TMDB 本就无海报，要么当时没走 details 取图）。

数据源两级，都不做搜索、不猜身份：
1. **donor（优先，纯本地零请求）**：同 tmdb_id、同类型、已有
   海报的 live 行——跨库同片同源，图片地址直接抄（与
   backfill_movie_genres 的 donor 口径一致；生产仅 5 部命中）。
2. **TMDB 详情**：donor 覆盖不到且有 tmdb_id 的走现行图片
   管线 ``tmdb_client.details()`` → ``image_specs()`` 取
   Primary（w500）→ ``_set_image`` 落字段（远程地址始终保留、
   本地已缓存时顺带落本地路径，与刮削期同口径）。

无 tmdb_id 的 163 部不在本脚本能力内（不许按片名猜身份），
计入 no_source 清单口径，交刮削侧识别补全处理。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_missing_posters.py           # dry-run
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/backfill_missing_posters.py --commit  # 真写

幂等：只碰双空行、只填空字段，可反复跑。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from sqlalchemy import func, or_  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.tmdb import _set_image, image_specs, tmdb_client  # noqa: E402


def _blank(col):
    return or_(col.is_(None), func.trim(col) == "")


def _target_query(session):
    """待回填目标：live movie/series 且海报双空。"""
    return (session.query(em.MediaItem)
            .filter(em.MediaItem.item_type.in_(("movie", "series")))
            .filter(_blank(em.MediaItem.poster_path))
            .filter(_blank(em.MediaItem.primary_image_url))
            .filter(em.MediaItem.merged_into_id.is_(None))
            .filter(em.MediaItem.deleted_at.is_(None)))


def donor_posters(session) -> dict:
    """donor 映射：{(tmdb_id, item_type): (primary_image_url, poster_path)}。

    纯本地数据源、零网络请求。同键多行取 id 最小的一行
    （确定性口径）；合并/软删/无身份/双空行不作 donor。
    """
    rows = (session.query(em.MediaItem.tmdb_id, em.MediaItem.item_type,
                          em.MediaItem.primary_image_url,
                          em.MediaItem.poster_path)
            .filter(em.MediaItem.item_type.in_(("movie", "series")))
            .filter(em.MediaItem.tmdb_id.isnot(None))
            .filter(~_blank(em.MediaItem.tmdb_id))
            .filter(or_(~_blank(em.MediaItem.primary_image_url),
                        ~_blank(em.MediaItem.poster_path)))
            .filter(em.MediaItem.merged_into_id.is_(None))
            .filter(em.MediaItem.deleted_at.is_(None))
            .order_by(em.MediaItem.id)
            .all())
    out: dict = {}
    for tmdb_id, kind, url, path in rows:
        key = (str(tmdb_id), kind)
        if key not in out:
            out[key] = (url, path)
    return out


def backfill_missing_posters(session, *, dry_run: bool = True, limit: int = 0,
                             batch_size: int = 200, details_fetcher=None,
                             image_setter=None) -> dict:
    """回填无海报作品，返回统计 dict。

    stats 键：total / donor_filled / tmdb_filled /
    still_missing（TMDB 也无海报）/ no_source（无 tmdb_id）/
    errors。dry_run 只计数不写库（TMDB 级仍真调取数）。
    details_fetcher 签名 (tmdb_id, kind) -> dict | None；
    image_setter 签名 (item, kind, url) -> None，默认
    tmdb._set_image（与刮削期落图同口径）。
    """
    if details_fetcher is None:
        details_fetcher = tmdb_client.details
    if image_setter is None:
        image_setter = _set_image
    stats = {"total": 0, "donor_filled": 0, "tmdb_filled": 0,
             "still_missing": 0, "no_source": 0, "errors": 0}
    total = _target_query(session).count()
    if limit:
        total = min(total, limit)
    stats["total"] = total
    if not total:
        return stats

    donors = donor_posters(session)
    target_ids = [r[0] for r in _target_query(session)
                  .with_entities(em.MediaItem.id)
                  .order_by(em.MediaItem.id).limit(total).all()]
    pending_commit = 0
    for start in range(0, len(target_ids), batch_size):
        chunk = target_ids[start:start + batch_size]
        rows = (session.query(em.MediaItem)
                .filter(em.MediaItem.id.in_(chunk))
                .order_by(em.MediaItem.id).all())
        for item in rows:
            filled = False
            tmdb_key = (str(item.tmdb_id), item.item_type) \
                if (item.tmdb_id or "").strip() else None
            donor = donors.get(tmdb_key) if tmdb_key else None
            if donor:
                if not dry_run:
                    url, path = donor
                    if url and not (item.primary_image_url or "").strip():
                        item.primary_image_url = url
                    if path and not (item.poster_path or "").strip():
                        item.poster_path = path
                stats["donor_filled"] += 1
                filled = True
            elif tmdb_key:
                try:
                    data = details_fetcher(str(item.tmdb_id), item.item_type)
                except Exception:  # noqa: BLE001 — 单条失败不许中断整批
                    stats["errors"] += 1
                    data = None
                primary = [u for k, u in image_specs(data or {})
                           if k == "Primary"]
                if primary:
                    if not dry_run:
                        image_setter(item, "Primary", primary[0])
                    stats["tmdb_filled"] += 1
                    filled = True
            else:
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
    ap = argparse.ArgumentParser(description="无海报作品回填（PD-02）")
    ap.add_argument("--commit", action="store_true",
                    help="真正写库（默认 dry-run 只计数）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理条数")
    args = ap.parse_args(argv)
    session = SessionLocal()
    try:
        stats = backfill_missing_posters(session, dry_run=not args.commit,
                                         limit=args.limit)
    finally:
        session.close()
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] missing_posters: total={stats['total']} "
          f"donor_filled={stats['donor_filled']} "
          f"tmdb_filled={stats['tmdb_filled']} "
          f"still_missing={stats['still_missing']} "
          f"no_source={stats['no_source']} errors={stats['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
