#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零集剧集清单与成因分类（只读报告）——网页媒体库审计 PD-02。

背景（2026-11-17 生产实测，ORM 口径）：42 部 live series 没有
任何 live episode、却仍在货架上（详情页呈「还没有入库的集」
空态）。逐条查清成因后分四类：

- ``episodes_merged_elsewhere``：名下集行都在、但全部被合并到
  别的剧的集（merged_into_id 指向他剧集行）。生产 14 部；其中
  多部目标剧与原剧并不同一部（如《星球大战：摩尔 暗影之王》
  的集被合到《星球大战：幻境》），疑似宽松合并误合——须人工
  复核合并对错，**不许**顺手把空壳藏掉把误合钉死。
- ``split_shell``：名下没有任何集行（季行还在），同库同名另有
  一行装着集——季与集被拆到两行。生产 26 部；其中多对同名行
  年份/tmdb_id 互相冲突（实为不同剧，如《夏夜》2020 vs 2024、
  《活着》1994 vs 2023），不能按同名自动合并，须逐对人工核。
- ``true_empty``：名下无集、同库同名也无带集的另一行——真空
  壳（有季无集，未入库完成）。生产 2 部。
- ``episodes_soft_deleted``：名下集行全部软删的残留。生产 0 部。

本脚本只读：只分类、打印清单与计数，绝不写库。处置须人工
逐类核后再另行立单，不在本脚本范围。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api \
        python3 scripts/report_zero_episode_series.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.soft_delete import include_deleted  # noqa: E402

CAT_MERGED = "episodes_merged_elsewhere"
CAT_SPLIT = "split_shell"
CAT_EMPTY = "true_empty"
CAT_SOFT = "episodes_soft_deleted"


def _live_episode_series_ids(session) -> set:
    return {r[0] for r in session.query(em.MediaItem.series_id)
            .filter(em.MediaItem.item_type == "episode")
            .filter(em.MediaItem.series_id.isnot(None))
            .filter(em.MediaItem.merged_into_id.is_(None))
            .filter(em.MediaItem.deleted_at.is_(None))
            .distinct().all()}


def classify_zero_episode_series(session) -> list:
    """返回 [{id, name, library_id, tmdb_id, category, detail}]。

    只查 live series（未软删、未合并）里没有任何 live episode
    的行；分类口径见模块 docstring，顺序即优先级。
    """
    live_ep_sids = _live_episode_series_ids(session)
    series_rows = (session.query(em.MediaItem)
                   .filter(em.MediaItem.item_type == "series")
                   .filter(em.MediaItem.merged_into_id.is_(None))
                   .filter(em.MediaItem.deleted_at.is_(None))
                   .order_by(em.MediaItem.id).all())
    out = []
    for s in series_rows:
        if s.id in live_ep_sids:
            continue
        # 集行普查必须看见软删行（全局 ORM 钩子默认拼 deleted_at
        # IS NULL，软删残留类不放行就永远统计不到）
        with include_deleted():
            eps = (session.query(em.MediaItem)
                   .filter(em.MediaItem.series_id == s.id)
                   .filter(em.MediaItem.item_type == "episode").all())
        merged_eps = [e for e in eps
                      if e.merged_into_id is not None
                      and e.deleted_at is None]
        soft_eps = [e for e in eps if e.deleted_at is not None]
        if merged_eps:
            targets = sorted({e.merged_into_id for e in merged_eps})
            out.append({"id": s.id, "name": s.name,
                        "library_id": s.library_id,
                        "tmdb_id": s.tmdb_id or "",
                        "category": CAT_MERGED,
                        "detail": f"merged_eps={len(merged_eps)} "
                                  f"targets={targets[:5]}"})
            continue
        if eps and not [e for e in eps if e.deleted_at is None]:
            out.append({"id": s.id, "name": s.name,
                        "library_id": s.library_id,
                        "tmdb_id": s.tmdb_id or "",
                        "category": CAT_SOFT,
                        "detail": f"soft_eps={len(soft_eps)}"})
            continue
        sibling = (session.query(em.MediaItem)
                   .filter(em.MediaItem.item_type == "series")
                   .filter(em.MediaItem.library_id == s.library_id)
                   .filter(em.MediaItem.name == s.name)
                   .filter(em.MediaItem.id != s.id)
                   .filter(em.MediaItem.id.in_(live_ep_sids))
                   .filter(em.MediaItem.merged_into_id.is_(None))
                   .filter(em.MediaItem.deleted_at.is_(None))
                   .first())
        if sibling is not None:
            out.append({"id": s.id, "name": s.name,
                        "library_id": s.library_id,
                        "tmdb_id": s.tmdb_id or "",
                        "category": CAT_SPLIT,
                        "detail": f"sibling_id={sibling.id} "
                                  f"sibling_tmdb={sibling.tmdb_id or ''} "
                                  f"sibling_year={sibling.production_year or ''} "
                                  f"year={s.production_year or ''}"})
        else:
            out.append({"id": s.id, "name": s.name,
                        "library_id": s.library_id,
                        "tmdb_id": s.tmdb_id or "",
                        "category": CAT_EMPTY,
                        "detail": ""})
    return out


def main(argv=None) -> int:
    session = SessionLocal()
    try:
        rows = classify_zero_episode_series(session)
    finally:
        session.close()
    counts = Counter(r["category"] for r in rows)
    print(f"zero_episode_series total={len(rows)} "
          + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for r in rows:
        print(f"{r['id']}\tlib={r['library_id']}\t{r['category']}\t"
              f"tmdb={r['tmdb_id']}\t{r['name']}\t{r['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
