#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填被季文件夹名污染的条目名（2026-11-12 Season 污染）。

污染形态（生产实测，visible 口径 deleted_at IS NULL AND merged_into_id IS NULL，
共 1,339 条）：
- series 行 5 条：「Season 01」「Season 1960」「Season S01」
- episode 行 1,334 条：「Season 01 S01」998、「Season 02 S02」110、
  「Season 03 S03」93、「Season 1950 S19」68、「Season 04 S04」17、
  「Season 05 S05」13 ……

根因：扫描器 ``parse_media_filename`` 的标题回退链把季文件夹名当标题，
``_series_name_of`` 只跳一层季目录、年季（Season 1950）不认成季目录。
写入口已随本 PR 修掉；本脚本只修存量。

修法（只碰名字仍匹配污染形态的行，幂等——跑第二遍时名字已干净，匹配数为 0）：
- series 行：有 tmdb_id → TMDB ``details`` 取剧名；无 tmdb → 从子集
  file_path 向上越过季文件夹取剧集文件夹名（复用 scanner 的跳季逻辑）；
  推导不出 → 进人工清单（不猜名）。
- episode 行：有 series.tmdb_id 且能确定 S/E → TMDB ``find_episode`` 取
  集标题（占位标题不算）；取不到 → 文件名里干净的集标题（如
  "Little Quacker"）；再取不到 → 「剧名 SxxExx」（无集号时「剧名 Sxx」，
  与扫描器现行「片名 Sxx」口径一致）。
- S/E 补填（只补 NULL、不覆盖已有值；只认无歧义形态）：紧凑型 S0101 →
  S01E01、S02EP01 → S02E01；年季 S1950E01 有歧义（季 1950 vs 季 19
  集 50），不碰 season/episode_number。

安全口径：默认 dry-run（只计数、不写库、不 commit）；``--commit`` 才
真正写库。TMDB 调用只读（details / season），dry-run 也会打（要计数
能修回多少），一轮约几百次请求、走客户端自带缓存与限流。

用法（容器内）：
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/backfill_season_names.py
    docker exec -i -e PYTHONPATH=/app -w /app aetrix-api python3 scripts/backfill_season_names.py --commit
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_TYPE", "postgres")

from backend.database import SessionLocal  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server.scanner import (  # noqa: E402
    ScanFile,
    _is_season_like_name,
    _series_name_of,
    _tidy_name,
)
from backend.emby_server.tmdb import (  # noqa: E402
    _is_placeholder_episode_title,
    tmdb_client,
)

# episode 名污染形态：「Season 01 S01」「Season 1950 S19」——季标记 + 季代码
_EP_POLLUTED_RE = re.compile(r"(?i)^(season\s+\S+|s\d{1,4})\s+s\d{1,2}$")
# 无歧义 S/E：紧凑型 S0101 与 S02EP01（S1950E01 有歧义，不进这里）
_SE_COMPACT_RE = re.compile(r"(?i)\b[Ss](\d{2})(\d{2})\b")
_SE_EP_RE = re.compile(r"(?i)\b[Ss](\d{1,2})[Ee][Pp](\d{1,3})\b")
# S/E 标记之后取集标题用
_SE_MARKER_RE = re.compile(r"(?i)[Ss]\d{1,4}\s*[Ee][Pp]?\s*\d{1,3}")
# 质量标签：集标题候选里只剩这些就不算标题
_TAG_RE = re.compile(
    r"(?i)^(\d{3,4}p|4k|8k|web-?dl|blu-?ray|hdr\d*|dolby|dts|aac|hevc|x26[45]"
    r"|10bit|remux|proper|repack)$")


def is_polluted_series_name(name: str) -> bool:
    return _is_season_like_name(name or "")


def is_polluted_episode_name(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and (_is_season_like_name(n) or bool(_EP_POLLUTED_RE.match(n)))


def series_name_from_child_path(file_path: str) -> str:
    """从子集 file_path 向上越过季文件夹取剧集文件夹名（复用扫描器逻辑）。

    拿不到返回 ""（不猜）。"""
    if not file_path:
        return ""
    if file_path.startswith("mount://"):
        parts = file_path.split("/", 3)
        if len(parts) < 4:
            return ""
        rel = parts[3]
        dir_rel = rel.rsplit("/", 1)[0] if "/" in rel else "/"
        sf = ScanFile(stored_path=file_path, name="", local_dir=None,
                      dir_rel=dir_rel, mount_id=None)
    else:
        sf = ScanFile(stored_path=file_path, name="",
                      local_dir=os.path.dirname(file_path), mount_id=None)
    name = _series_name_of(sf, {"name": "", "year": None})
    return "" if name == "未知剧集" else name


def episode_title_from_filename(file_path: str) -> str:
    """文件名 S/E 标记之后干净的集标题（如 "Little Quacker"），没有返回 ""。"""
    stem = os.path.basename(file_path or "")
    stem = os.path.splitext(stem)[0]
    m = _SE_MARKER_RE.search(stem)
    if not m:
        return ""
    tail = stem[m.end():]
    # 剥掉分隔符与尾巴的质量标签段（如 ".1080p.WEB-DL"）
    tail = re.sub(r"^[\s\-_.\[\]()]+", "", tail)
    tail = re.split(r"(?i)[\s\-_.\[\]()](?:\d{3,4}p|4k|web-?dl|blu-?ray|hdr)", tail)[0]
    title = _tidy_name(tail)
    if not title or len(title) < 2:
        return ""
    if _TAG_RE.match(title.replace(" ", "")):
        return ""
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", title):
        return ""
    return title


def derive_se_numbers(file_path: str) -> tuple:
    """从文件名无歧义推导 (season, episode)；推导不出返回 (None, None)。"""
    stem = os.path.basename(file_path or "")
    m = _SE_EP_RE.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _SE_COMPACT_RE.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="真正写库；不加则只 dry-run 计数")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        base_q = (db.query(em.MediaItem)
                  .filter(em.MediaItem.deleted_at.is_(None),
                          em.MediaItem.merged_into_id.is_(None)))
        polluted_series = [r for r in base_q.filter(
            em.MediaItem.item_type == "series").all()
            if is_polluted_series_name(r.name or "")]
        # episode 表几十万行：先用 SQL 把候选缩到名字以 Season 开头
        # 或裸季号（S01）的行，再用 Python 精确匹配污染形态
        from sqlalchemy import or_ as _or
        polluted_eps = [r for r in base_q.filter(
            em.MediaItem.item_type == "episode",
            _or(em.MediaItem.name.ilike("season%"),
                em.MediaItem.name.op("~")(r"^S\d{1,4}$"))).all()
            if is_polluted_episode_name(r.name or "")]
        print(f"polluted series rows: {len(polluted_series)}")
        print(f"polluted episode rows: {len(polluted_eps)}")

        # ---- series：先修，episode 的「剧名 SxxExx」兜底要用修后的剧名 ----
        series_new_name: dict[int, str] = {}
        series_manual: list[tuple[int, str, str]] = []
        series_tmdb_ok = series_dir_ok = 0
        for row in polluted_series:
            new_name = ""
            source = ""
            if row.tmdb_id:
                try:
                    data = tmdb_client.details(str(row.tmdb_id), "series")
                except Exception as exc:  # noqa: BLE001 — TMDB 瞬态失败记 manual
                    data = None
                    print(f"  series {row.id} tmdb error: {exc!r}")
                if data and data.get("name"):
                    new_name, source = data["name"], "tmdb"
            if not new_name:
                child = (db.query(em.MediaItem)
                         .filter(em.MediaItem.series_id == row.id,
                                 em.MediaItem.deleted_at.is_(None),
                                 em.MediaItem.merged_into_id.is_(None),
                                 em.MediaItem.file_path.isnot(None))
                         .order_by(em.MediaItem.id).first())
                if child is not None:
                    new_name = series_name_from_child_path(child.file_path or "")
                    if new_name:
                        source = "dir"
            if new_name and not _is_season_like_name(new_name):
                series_new_name[row.id] = new_name
                if source == "tmdb":
                    series_tmdb_ok += 1
                else:
                    series_dir_ok += 1
                if args.commit:
                    row.name = new_name
            else:
                series_manual.append((row.id, row.name or "",
                                      row.file_path or ""))
        print(f"series fixable: tmdb={series_tmdb_ok} dir={series_dir_ok} "
              f"manual={len(series_manual)}")
        for row_id, name, path in series_manual:
            print(f"  MANUAL series id={row_id} name={name!r} path={path!r}")

        # ---- episode ----
        ep_tmdb_ok = ep_filetitle_ok = ep_fallback_ok = 0
        ep_manual: list[tuple[int, str, str]] = []
        ep_se_fill = 0
        # 批量取 series 行（tmdb + 修后名）
        series_rows = {r.id: r for r in
                       db.query(em.MediaItem)
                       .filter(em.MediaItem.item_type == "series").all()}
        for row in polluted_eps:
            series = series_rows.get(row.series_id or -1)
            series_name = series_new_name.get(row.series_id or -1) or (
                series.name if series is not None else "")
            season_no, ep_no = row.season_number, row.episode_number
            if (season_no is None or ep_no is None) and row.file_path:
                d_season, d_ep = derive_se_numbers(row.file_path)
                if season_no is None and d_season is not None:
                    season_no = d_season
                if ep_no is None and d_ep is not None:
                    ep_no = d_ep
            new_name, source = "", ""
            # 1) TMDB 集标题
            if (series is not None and series.tmdb_id
                    and season_no is not None and ep_no is not None):
                try:
                    ep_data = tmdb_client.find_episode(
                        str(series.tmdb_id), int(season_no), int(ep_no))
                except Exception as exc:  # noqa: BLE001
                    ep_data = None
                    print(f"  episode {row.id} tmdb error: {exc!r}")
                title = (ep_data or {}).get("name") or ""
                if title and not _is_placeholder_episode_title(title):
                    new_name, source = title, "tmdb"
            # 2) 文件名干净集标题
            if not new_name and row.file_path:
                title = episode_title_from_filename(row.file_path)
                if title:
                    new_name, source = title, "filetitle"
            # 3) 「剧名 SxxExx」/「剧名 Sxx」兜底
            if not new_name and series_name and not _is_season_like_name(series_name):
                if season_no is not None and ep_no is not None:
                    new_name = f"{series_name} S{season_no:02d}E{ep_no:02d}"
                elif season_no is not None:
                    new_name = f"{series_name} S{season_no:02d}"
                source = "fallback"
            if new_name and not is_polluted_episode_name(new_name):
                if source == "tmdb":
                    ep_tmdb_ok += 1
                elif source == "filetitle":
                    ep_filetitle_ok += 1
                else:
                    ep_fallback_ok += 1
                if args.commit:
                    row.name = new_name
                    if row.season_number is None and season_no is not None:
                        row.season_number = season_no
                        ep_se_fill += 1
                    if row.episode_number is None and ep_no is not None:
                        row.episode_number = ep_no
                        ep_se_fill += 1
            else:
                ep_manual.append((row.id, row.name or "", row.file_path or ""))
        print(f"episode fixable: tmdb={ep_tmdb_ok} filetitle={ep_filetitle_ok} "
              f"fallback={ep_fallback_ok} manual={len(ep_manual)}")
        for row_id, name, path in ep_manual:
            print(f"  MANUAL episode id={row_id} name={name!r} path={path!r}")

        if args.commit:
            db.commit()
            print(f"COMMITTED: series={len(series_new_name)} "
                  f"episodes={ep_tmdb_ok + ep_filetitle_ok + ep_fallback_ok} "
                  f"se_fields_filled={ep_se_fill}")
        else:
            print("DRY-RUN: no writes (add --commit to apply)")
        print(f"SUMMARY polluted_series={len(polluted_series)} "
              f"series_fixed={len(series_new_name)} "
              f"series_manual={len(series_manual)} "
              f"polluted_episodes={len(polluted_eps)} "
              f"episodes_fixed={ep_tmdb_ok + ep_filetitle_ok + ep_fallback_ok} "
              f"episodes_manual={len(ep_manual)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
