"""片库搜索词聚合计数（热搜榜）。

- 记录：Emby Items 搜索口在搜索成功后 best-effort 记一笔
  （见 emby_server.api.get_items）。只存归一化词与次数、不关联
  用户；计数失败只记日志，绝不拖垮搜索本体。
- 读取：api/media_library 的 GET /search/hot 按近 N 天聚合出榜，
  并合并管理员的置顶 / 下榜干预（SearchTermOverride，管理口在
  api/admin_search.py）。
- 归一化：去首尾空白、内部连续空白折叠为单个空格、casefold、
  超长截断。空词不记。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

_MAX_TERM_LEN = 100


def normalize_search_term(raw) -> str:
    """搜索词归一化：聚合去重的唯一口径。"""
    if not raw:
        return ""
    term = " ".join(str(raw).split()).casefold()
    return term[:_MAX_TERM_LEN]


def record_search_term(db: Session, raw, *, now: datetime | None = None) -> bool:
    """给某个搜索词的今日计数 +1。空词跳过（返回 False）。

    自带 commit；调用方仍应把整段放进 try/except——计数是旁路，
    任何失败都不许影响搜索响应。
    """
    key = normalize_search_term(raw)
    if not key:
        return False
    now = now or datetime.now()
    today = now.date()
    row = (
        db.query(em.SearchTermStat)
        .filter(em.SearchTermStat.term_key == key, em.SearchTermStat.day == today)
        .first()
    )
    if row is None:
        db.add(em.SearchTermStat(
            term_key=key,
            display_term=" ".join(str(raw).split())[:_MAX_TERM_LEN],
            day=today,
            count=1,
            last_seen_at=now,
        ))
    else:
        row.count = (row.count or 0) + 1
        row.display_term = " ".join(str(raw).split())[:_MAX_TERM_LEN]
        row.last_seen_at = now
    db.commit()
    return True


def aggregate_terms(db: Session, days: int = 7,
                    *, today: date | None = None) -> list[dict]:
    """窗口内原始聚合全量（不截断、不应用人工干预）。

    每项 {term_key, term, count, last_seen_at}，按总次数降序、
    同次数按最近出现排序——生效榜与管理端词表共用同一聚合口径。
    """
    today = today or date.today()
    since = today - timedelta(days=max(days, 1) - 1)
    rows = (
        db.query(
            em.SearchTermStat.term_key,
            func.sum(em.SearchTermStat.count).label("total"),
            func.max(em.SearchTermStat.last_seen_at).label("last_seen"),
        )
        .filter(em.SearchTermStat.day >= since)
        .group_by(em.SearchTermStat.term_key)
        .order_by(func.sum(em.SearchTermStat.count).desc(),
                  func.max(em.SearchTermStat.last_seen_at).desc())
        .all()
    )
    if not rows:
        return []
    # 展示词取每个词最近一行（窗口内）的原始写法
    keys = [r.term_key for r in rows]
    display: dict[str, str] = {}
    latest = (
        db.query(em.SearchTermStat)
        .filter(em.SearchTermStat.term_key.in_(keys),
                em.SearchTermStat.day >= since)
        .order_by(em.SearchTermStat.last_seen_at.desc())
        .all()
    )
    for row in latest:
        display.setdefault(row.term_key, row.display_term)
    return [
        {
            "term_key": r.term_key,
            "term": display.get(r.term_key, r.term_key),
            "count": int(r.total or 0),
            "last_seen_at": r.last_seen,
        }
        for r in rows
    ]


def list_overrides(db: Session) -> list:
    """全部人工干预行（pin 按 pin_order、id 升序在前，block 在后）。"""
    rows = db.query(em.SearchTermOverride).all()
    pins = sorted((r for r in rows if r.mode == "pin"),
                  key=lambda r: (r.pin_order, r.id))
    blocks = sorted((r for r in rows if r.mode == "block"),
                    key=lambda r: r.id)
    return pins + blocks


def hot_search_terms(db: Session, limit: int = 10, days: int = 7,
                     *, today: date | None = None,
                     with_flags: bool = False) -> list[dict]:
    """近 days 天（含今天）生效热搜榜：聚合榜 + 管理员置顶/下榜合并。

    合并顺序：置顶区（pin_order 升序，次数取窗口内聚合实数、无统计
    为 0）→ 其余聚合词（剔除被下榜与已置顶的）。默认返回项只有
    {term, count}（用户口形状不变）；``with_flags=True`` 时额外带
    term_key 与 pinned，供管理端预览口关联操作。
    """
    agg = aggregate_terms(db, days, today=today)
    overrides = list_overrides(db)
    blocked = {r.term_key for r in overrides if r.mode == "block"}
    pins = [r for r in overrides if r.mode == "pin"]
    agg_map = {a["term_key"]: a for a in agg}

    out: list[dict] = []

    def _emit(term_key: str, term: str, count: int, pinned: bool) -> None:
        item = {"term": term, "count": count}
        if with_flags:
            item["term_key"] = term_key
            item["pinned"] = pinned
        out.append(item)

    pinned_keys: set[str] = set()
    for row in pins:
        if row.term_key in blocked:
            continue  # 同词不会同时有两行（term_key 唯一），防御式跳过
        pinned_keys.add(row.term_key)
        hit = agg_map.get(row.term_key)
        _emit(row.term_key,
              hit["term"] if hit else row.display_term,
              hit["count"] if hit else 0,
              True)
    for a in agg:
        if a["term_key"] in blocked or a["term_key"] in pinned_keys:
            continue
        _emit(a["term_key"], a["term"], a["count"], False)
    return out[:max(limit, 1)]


def delete_term_stats(db: Session, term_key: str) -> int:
    """删除某个词的全部统计行（管理端「删除」），返回删除行数。

    不 commit：与干预行的联动删除、审计同事务，由调用方提交。
    """
    return (
        db.query(em.SearchTermStat)
        .filter(em.SearchTermStat.term_key == term_key)
        .delete(synchronize_session=False)
    )
