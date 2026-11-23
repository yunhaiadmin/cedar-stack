#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定向补拉：已绑 TMDB 编号、却从未真正拉过详情的存量条目批量入队。

背景（2026-11-24 元数据诊断，生产口径 deleted_at IS NULL AND
merged_into_id IS NULL）：顶层条目里一批「终态 none 后经松绑修复/手动
识别绑上 tmdb 编号」的剧（实测约 3,300 部）与「有身份有海报但无简介」
的条目（约 2,525 个）——编号有了，详情从未拉回，简介/类型/评分/演员
成片缺失。现有机制覆盖不到它们：

- Step-5 字段补队只在 _enrich_apply 瞬间触发，done 存量永不重入；
  且当初它们走 _searched_no_hit 终态时整段跳过 Step-5；
- enrich 抓取本身没问题：翻回 pending 后，搜索未命中会按 existing_id
  兜底拉详情（_tmdb_work / 批量 Round 3），写库走只填空 apply_details
  + 无条件 _apply_cast（本 PR 的存量详情分支补齐了文本落库）。

本脚本只做入队（人群口径与动作在 enrich_worker.enqueue_details_refill，
全仓一套）：翻 pending + priority=-10（Step-5 同口径低优先级）+
enrich_field_refill_done=True（保证只跑这一轮、跑完 Step-5 直接终态化，
不会反复烧配额）。幂等：入队即置标记，重复运行匹配数为 0。

安全口径：默认 dry-run（只计数、不写库）；--commit 才真正入队。
--commit 时把本批入队 id 清单写到 --ids-out（默认
/tmp/refill_bound_ids.json），供跑完后按同一批 id 统计产出率
（补到简介/类型/评分/演员各多少、仍缺的逐条留痕）。

跑批纪律（parent 定）：先小批试点（--limit 200）→ 抽验产出率与
TMDB 错误率 → 再逐批放大；工人低优先级队列天然排在主队列之后，
不抢新入库条目的配额。每条约 1 次搜索 + 1 次详情请求（搜索命中
时详情按命中 id 拉、未命中按 existing_id 拉，两种都恰好 1 次详情）。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/refill_bound_details.py
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/refill_bound_details.py --commit --limit 200
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/refill_bound_details.py --commit --limit 1000 --ids-out /tmp/refill_batch2_ids.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.enrich_worker import enqueue_details_refill  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="真正入队（缺省 dry-run 只计数）")
    ap.add_argument("--limit", type=int, default=500,
                    help="本批最多入队条数（默认 500）")
    ap.add_argument("--ids-out", default="/tmp/refill_bound_ids.json",
                    help="--commit 时本批入队 id 清单落盘路径")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        out = enqueue_details_refill(db, limit=args.limit,
                                     dry_run=not args.commit)
        by_source = Counter()
        if out["ids"]:
            rows = (db.query(em.MediaItem.metadata_source)
                    .filter(em.MediaItem.id.in_(out["ids"])).all())
            by_source = Counter((r[0] or "<null>") for r in rows)
        print(f"matched={out['matched']} "
              f"batch={len(out['ids'])} by_type={out['by_type']}")
        print(f"batch by metadata_source: {dict(by_source)}")
        if not args.commit:
            print("dry-run：未写库。确认人群无误后加 --commit 入队。")
            return 0
        db.commit()
        with open(args.ids_out, "w", encoding="utf-8") as fh:
            json.dump(out["ids"], fh)
        print(f"enqueued={out['enqueued']} ids -> {args.ids_out}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
