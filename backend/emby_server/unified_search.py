"""统一搜索的归一与合并层（本站库聚合搜索口的纯逻辑）。

定位：``search_api`` 的 /UnifiedSearch 端点把本站命中（走
``search_api`` 的预筛与 ``search.rank_items`` 精排）交给本模块
按作品归一键去重、成形响应体。本模块只做纯数据变换：不查库、
不发 HTTP、不写任何表。

- 作品归一键（``local_aggregate_key``）：tmdb > imdb > 标题+年份，
  单集追加季/集号——有外部身份绝不靠标题猜，同键只出首现一行。
- 开关：``UNIFIED_SEARCH_ENABLED``（默认关）。关时调用方接口
  返回 404，全站行为逐字不变；``unified_search_enabled`` 每次
  调用现读 env，供测试用 ``monkeypatch.setenv`` 覆盖。
- 搜不到不谎报：空词返回空结果，绝不 dump 全库。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set, Tuple

#: 统一搜索总开关 env 键（默认关）。
UNIFIED_SEARCH_ENV = "UNIFIED_SEARCH_ENABLED"


def _normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").strip().lower()
    text = re.sub(r"[\s\-_.,:;!?()\[\]{}'\"]+", "", text)
    return text


def _aggregate_key(*, name: str, series_name: str, item_type: str,
                   production_year: Optional[int],
                   tmdb_id: Optional[str], imdb_id: Optional[str],
                   season_number: Optional[int],
                   episode_number: Optional[int]) -> str:
    """作品归一键：外部身份优先，缺身份时退化为标题+年份。

    优先级 tmdb > imdb > 标题+年份——与 #13 远端身份“证据先行”
    同序：有外部身份绝不靠标题猜。剧集单集在系列键后追加
    季/集号，同一系列的不同集绝不归成同一作品。
    """
    if tmdb_id:
        base = f"tmdb:{tmdb_id}"
    elif imdb_id:
        base = f"imdb:{imdb_id}"
    else:
        title = _normalize_title(series_name or name)
        year = production_year or ""
        base = f"title:{title}:{year}"
    if item_type == "Episode" or episode_number is not None:
        # 注意：单集即使有 tmdb/imdb，季集号仍须参与键——同一
        # 剧集的不同集靠它区分，同一集重复出现时靠它归一。
        base = f"{base}:s{season_number}:e{episode_number}"
    return base


def unified_search_enabled() -> bool:
    """总开关是否开启（每次调用现读 env，默认关）。"""
    return (str(os.getenv(UNIFIED_SEARCH_ENV, "0") or "0")
            .strip().lower() not in {"0", "false", "no", "off", ""})


def local_aggregate_key(*, name: str, item_type: str,
                        production_year: Optional[int],
                        scan_year: Optional[int],
                        tmdb_id: Optional[str], imdb_id: Optional[str],
                        season_number: Optional[int],
                        episode_number: Optional[int]) -> str:
    """本站条目的归一键（调用方唯一入口，纯函数）。

    年份口径：production_year 是作品年份；scan_year 只是扫描期
    文件名年份，仅在 production_year 为空时回落。用 scan_year
    优先算键会让无外部身份的同一作品（标题+年份键）算出与
    production_year 口径不同的键，去重 miss。
    """
    return _aggregate_key(
        name=name, series_name="", item_type=item_type,
        production_year=(production_year if production_year is not None
                         else scan_year),
        tmdb_id=tmdb_id, imdb_id=imdb_id,
        season_number=season_number, episode_number=episode_number)


# ---------------------------------------------------------------------------
# 输入 / 输出模型（纯数据，不碰数据库）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalSearchEntry:
    """本站侧已精排的一行（调用方按 rank_items 顺序传入）。"""

    aggregate_key: str
    name: str
    item_type: str = ""
    production_year: Optional[int] = None


@dataclass(frozen=True)
class UnifiedSearchResult:
    """合并后的一行：主来源 + 同键的其余来源。"""

    aggregate_key: str
    name: str
    item_type: str
    production_year: Optional[int]
    origin: str  # 恒 "local"（响应形状保留字段）
    source: str  # 恒 "local"
    also_on_sources: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceStatus:
    """单个源的本次搜索状态：ok / unavailable / not_requested。"""

    source_id: str
    status: str
    count: int = 0
    error: Optional[str] = None


@dataclass(frozen=True)
class UnifiedSearchResponse:
    """合并结果（调用方直接序列化）。"""

    query: str
    results: Tuple[UnifiedSearchResult, ...]
    sources: Tuple[SourceStatus, ...]

    @property
    def total(self) -> int:
        return len(self.results)


# ---------------------------------------------------------------------------
# 合并：本地结果按归一键去重成形
# ---------------------------------------------------------------------------


def merge_search_results(
    local_entries: Iterable[LocalSearchEntry],
    term: str,
) -> UnifiedSearchResponse:
    """把本站已精排的搜索结果归一成响应（纯函数，不写库）。

    按调用方传入的 rank_items 顺序输出，同键只保留首现一行；
    空词返回空结果（不 dump 全库）。
    """
    query = (term or "").strip()
    if not query:
        return UnifiedSearchResponse(query=query, results=(), sources=())
    results: List[UnifiedSearchResult] = []
    seen: Set[str] = set()
    for entry in local_entries:
        if not entry.aggregate_key or entry.aggregate_key in seen:
            continue
        seen.add(entry.aggregate_key)
        results.append(UnifiedSearchResult(
            aggregate_key=entry.aggregate_key,
            name=entry.name,
            item_type=(entry.item_type or "").lower(),
            production_year=entry.production_year,
            origin="local",
            source="local",
            also_on_sources=(),
        ))
    sources = (SourceStatus(source_id="local", status="ok",
                            count=len(results)),)
    return UnifiedSearchResponse(query=query, results=tuple(results),
                                 sources=sources)


def to_response_body(response: UnifiedSearchResponse,
                     *, limit: Optional[int] = None) -> dict:
    """把合并结果序列化为接口响应体（截断只截尾，不重排）。"""
    rows = list(response.results)
    if limit is not None:
        rows = rows[:max(0, limit)]
    return {
        "query": response.query,
        "results": [
            {
                "aggregate_key": row.aggregate_key,
                "name": row.name,
                "item_type": row.item_type,
                "production_year": row.production_year,
                "origin": row.origin,
                "source": row.source,
                "also_on_sources": list(row.also_on_sources),
            }
            for row in rows
        ],
        "sources": [
            {
                "source_id": status.source_id,
                "status": status.status,
                "count": status.count,
                "error": status.error,
            }
            for status in response.sources
        ],
        "total": len(rows),
    }
