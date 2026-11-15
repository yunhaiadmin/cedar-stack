"""片库搜索词聚合计数（热搜榜）。

- 记录：Emby Items 搜索口在搜索成功后 best-effort 记一笔
  （见 emby_server.api.get_items）。只存归一化词与次数、不关联
  用户；计数失败只记日志，绝不拖垮搜索本体。
- 读取：api/media_library 的 GET /search/hot 按近 N 天聚合出榜。
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


def hot_search_terms(db: Session, limit: int = 10, days: int = 7,
                     *, today: date | None = None) -> list[dict]:
    """近 days 天（含今天）Top 搜索词：[{"term": 展示词, "count": 总次数}]。

    按窗口内总次数降序、同次数按最近出现排序；无数据回空表。
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
        .limit(max(limit, 1))
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
        {"term": display.get(r.term_key, r.term_key), "count": int(r.total or 0)}
        for r in rows
    ]
