#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填存量用户公开 ID（web_users.public_id）。

背景（2026-11-15 用户亲点：站内数字 ID 太简单）：用户侧改用 10 位
公开 ID（大写字母＋数字、剔除易混字符，生成唯一实现在
backend.models）。新行由建号口径显式赋值＋列默认兜底保证非空；
本脚本只处理升级前存量（public_id 为 NULL 的行）。

口径：
- 只填 public_id IS NULL 的行，已有值永不覆盖 → 幂等，重跑报 0；
- 逐行生成互不重复：脚本内维护已用集合，并经
  models.new_public_id 查库兜底防碰撞；
- 默认 dry-run 只报将回填条数；``--commit`` 才真正写库。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/backfill_user_public_ids.py --dry-run
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/backfill_user_public_ids.py --commit
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import models  # noqa: E402


def backfill_user_public_ids(db, *, dry_run: bool = True) -> dict:
    """给 public_id 为 NULL 的存量用户逐行分配公开 ID。

    返回 ``{"pending": 待填条数, "filled": 实填条数}``；
    dry_run 时只统计、不写库。
    """
    rows = (
        db.query(models.WebUser)
        .filter(models.WebUser.public_id.is_(None))
        .all()
    )
    if dry_run:
        return {"pending": len(rows), "filled": 0}
    taken = {
        pid
        for (pid,) in db.query(models.WebUser.public_id)
        .filter(models.WebUser.public_id.isnot(None))
        .all()
    }
    filled = 0
    for row in rows:
        row.public_id = models.new_public_id(db, taken)
        filled += 1
    db.commit()
    return {"pending": len(rows), "filled": filled}



def main() -> int:
    parser = argparse.ArgumentParser(description="回填 web_users.public_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报将回填条数，不写库（默认行为）")
    parser.add_argument("--commit", action="store_true",
                        help="真正写库（不给此参数一律 dry-run）")
    args = parser.parse_args()

    os.environ.setdefault("DATABASE_TYPE", "postgres")
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        stats = backfill_user_public_ids(db, dry_run=not args.commit)
        if args.commit:
            print(f"完成（commit）：已回填 {stats['filled']} 条")
        else:
            print(f"完成（dry-run）：{stats['pending']} 条待回填，未写库")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
