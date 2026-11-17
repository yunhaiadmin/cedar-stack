#!/usr/bin/env python3
"""一次性回填：emby_items 的身份门物化标记（identity_bound / has_identity）。

背景：BE-24（2026-11-17）把身份门判据从「tmdb/imdb 两列 +
external_ids 七键 LIKE + series EXISTS」逐行求值改为物化两列 +
索引（见 backend/emby_server/identity_gate.py 文件头）。新写入由
identity_gate 的 Session flush 同步钩子维护；存量行两列为 NULL，
identity_clause 按未通过处理，需要跑一遍本脚本补齐。

口径：全表应然值由 identity_gate.compute_flag_map 按 Python 判据
（is_bound_row）逐行算——与运行时子句、同步钩子同一口径，不另写
一套 SQL 判据。含已软删/已合并行（标记对所有行有定义）。

流程（纪律：先 dry-run 对账、再执行、执行后复对账）：
1. ``--dry-run``：只算应然值与现状的差异（mismatch），打印将回填量，
   不落库；
2. 正式跑：按 id 分批 UPDATE 差异行、每批 commit；中断后重跑自动
   续上（已一致的行不再命中差异，自然幂等）；
3. 跑完自动复对账：mismatch 必须归零，否则退出码非 0 并打印残余量。

用法（容器里，aetrix-api 与 aetrix-worker 都能连 DB）：

    docker exec aetrix-api python scripts/backfill_identity_flags.py --dry-run
    docker exec aetrix-api python scripts/backfill_identity_flags.py
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import identity_gate  # noqa: E402
from backend.emby_server import models as em  # noqa: E402


def backfill_identity_flags(session, *, dry_run: bool = False,
                            batch_size: int = 500) -> dict:
    """对账并（非 dry-run 时）回填身份标记，返回统计字典。

    返回键：total（全表行数）、mismatch（执行前与应然值不一致的行数，
    含两列为 NULL 的未回填行）、updated（实际写库行数；dry-run 恒 0）、
    remaining（执行后复对账的残余不一致行数；dry-run 时等于 mismatch）。
    """
    expected = identity_gate.compute_flag_map(session)
    t = em.MediaItem.__table__
    current = {
        r[0]: (r[1], r[2])
        for r in session.connection().execute(
            t.select().with_only_columns(t.c.id, t.c.identity_bound,
                                         t.c.has_identity)
        ).fetchall()
    }
    diff = {}
    for item_id, want in expected.items():
        have = current.get(item_id)
        have = (None, None) if have is None else (have[0], have[1])
        # 库里 BOOLEAN 读回可能是 0/1/bool，统一按真值比
        if (None if have[0] is None else bool(have[0]),
                None if have[1] is None else bool(have[1])) != want:
            diff[item_id] = want
    result = {"total": len(expected), "mismatch": len(diff),
              "updated": 0, "remaining": len(diff)}
    if dry_run or not diff:
        return result

    ids = sorted(diff)
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        session.connection().execute(
            t.update()
            .where(t.c.id.in_(chunk))
            .values(
                identity_bound=case(
                    *[(t.c.id == j, diff[j][0]) for j in chunk],
                    else_=t.c.identity_bound),
                has_identity=case(
                    *[(t.c.id == j, diff[j][1]) for j in chunk],
                    else_=t.c.has_identity),
            )
        )
        session.commit()
        result["updated"] += len(chunk)

    # 复对账：重算应然值（原始字段未动，应然值不变）与落库后现状比
    current2 = {
        r[0]: (r[1], r[2])
        for r in session.connection().execute(
            t.select().with_only_columns(t.c.id, t.c.identity_bound,
                                         t.c.has_identity)
        ).fetchall()
    }
    remaining = 0
    for item_id, want in expected.items():
        have = current2.get(item_id, (None, None))
        if (None if have[0] is None else bool(have[0]),
                None if have[1] is None else bool(have[1])) != want:
            remaining += 1
    result["remaining"] = remaining
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="只对账打印将回填量，不落库")
    ap.add_argument("--batch", type=int, default=500,
                    help="每批 UPDATE 的行数（默认 500）")
    args = ap.parse_args()

    session = SessionLocal()
    try:
        stats = backfill_identity_flags(
            session, dry_run=args.dry_run, batch_size=args.batch)
    finally:
        session.close()
    mode = "dry-run 对账" if args.dry_run else "回填"
    print(f"[{mode}] 全表 {stats['total']} 行，不一致 {stats['mismatch']} 行，"
          f"实际写入 {stats['updated']} 行，残余不一致 {stats['remaining']} 行")
    if not args.dry_run and stats["remaining"]:
        print("⚠️ 复对账未归零，请勿认为回填完成", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
