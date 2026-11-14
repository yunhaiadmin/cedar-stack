"""跨服统一搜索：一个入口搜本站库 + 所有已启用上游源（第二批第 1 单）

定位（#25/#1352 聚合的延伸，只做搜索、不做聚合）：
- 复用现有口径，不新造索引：本站侧复用 ``search_api`` 的预筛
  （``search.search_variants``）与精排（``search.rank_items``）；
  上游侧用本模块自持的作品归一键
  （``aggregate_key_for``：tmdb > imdb > 标题+年份，单集追加季集号）
  与多源失败隔离形态（单源失败只隔离到该源）。
- 只读：本模块不触发聚合、
  不写库（绝不调 ``record_mapping``、不写任何表）。上游现查是
  只读的 Emby ``/Items?SearchTerm=`` 查询，不是聚合跑批；查到
  的记录只进本次响应，不落库、不建映射。
- 开关：``UNIFIED_SEARCH_ENABLED``（默认关）。关时调用方接口
  返回 404，全站行为逐字不变；本模块的 ``unified_search_enabled``
  每次调用现读 env，供测试用 ``monkeypatch.setenv`` 覆盖。
- 搜不到不谎报：空词不返回全库（返回空结果）；某源不可用时
  该源在 ``sources`` 里明确标 ``unavailable`` 并附原因码，不静默
  丢弃该源、不把半份结果说成全量。
- 上游前置条件不变：对方未放行本方出口 IP 的源，现查会被对方
  前门拦（403 无 JSON 体 → ``upstream_frontend_blocked``），搜不
  到是预期，见调用方未知项，绝不编造连通性。
- 凭据纪律：上游播放地址、对方令牌、密封凭据一律不进结果与
  错误体；错误体只用固定原因码 + 固定文案，绝不拼接上游原始体
  或异常文本（异常文本可能含带 api_key 的 URL）。

排序与去重口径（写死在本模块，调用方不另定）：
- 本地在前：本站命中按 ``rank_items`` 的顺序全在前，上游绝不
  插队到本地前面（本地优先口径）。
- 上游在后：上游命中按 ``aggregate_key`` 排序，与上游返回顺序抖动无关。
- 去重：同一 ``aggregate_key`` 只出一行；首现来源为主来源，
  其余同键来源记进 ``also_on_sources``（不静默丢源信息）。
  本地已有的作品，上游同键只追加到该本地行的 ``also_on_sources``。
- 按源过滤：``source_filter`` 为源 id 集合（接受 ``"1"`` 与
  ``"upstream:1"`` 两种写法）；过滤只影响输出行与 ``also_on_sources``，
  被滤掉的源在 ``sources`` 里标 ``not_requested``，不冒充已查。
- 上游标题匹配的已知局限：上游记录只有标题/剧名，没有别名与
  拼音派生字段，故上游匹配只做归一化后的标题/剧名子串
  （``search.normalize_compact`` + ``search_variants`` 的繁简变体），
  不做拼音与别名匹配；本站侧仍走完整精排口径。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from backend.emby_server import upstream_http
from backend.emby_server import upstream_sources as us
from backend.emby_server.search import normalize_compact, search_variants

#: 统一搜索总开关 env 键（默认关）。
UNIFIED_SEARCH_ENV = "UNIFIED_SEARCH_ENABLED"

#: 上游现查单次返回上限（Emby Items Limit）：搜索不是拉全目录，
#: 只取首屏候选，超了不翻页、不谎报全量（TotalRecordCount 照实回）。
UPSTREAM_SEARCH_LIMIT = 100


# ---------------------------------------------------------------------------
# 上游目录记录模型与作品归一键（原上游聚合的解析/归一簇；聚合整族
# 删除后，本模块是唯一存活消费方，故迁入本模块自持）
# ---------------------------------------------------------------------------

#: 单次上游请求超时（秒）：现查带数据，比探活的 10 秒放宽。
HTTP_TIMEOUT_SEC = 30.0


@dataclass(frozen=True)
class UpstreamVersion:
    """一条线路下的一个版本：仅来自同一 Emby Item 的 MediaSource。

    只存 Id + Name + RunTimeTicks，不存 Path（对方服务器本地
    路径，存了就是泄露对方布局，且本模块用不上）。
    """

    media_source_id: str
    name: str = ""
    run_time_ticks: Optional[int] = None


@dataclass(frozen=True)
class UpstreamCatalogRecord:
    """拉回的一条目录记录（Emby Items 的一项）。

    ``category_id`` 是上游侧的库/分类标识；现查只用它做来源
    标识，不做本地库映射。
    """

    source_id: str
    upstream_item_id: str
    name: str
    item_type: str  # Movie / Series / Episode / Season 等，照 Emby 原值
    category_id: str = ""
    production_year: Optional[int] = None
    tmdb_id: Optional[str] = None
    imdb_id: Optional[str] = None
    series_name: str = ""
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    versions: Tuple[UpstreamVersion, ...] = ()
    # 上游侧所属剧的 Item Id（Emby Episode 的 SeriesId 基础字段）：
    # 进库二期单集回查所属剧的身份级依据（靠它查映射/托管剧行，
    # 不靠剧名猜）。Movie / Series 记录恒为空。放末尾保位置构造兼容。
    series_upstream_id: str = ""
    # 增量拉取排序时间戳原文（Emby ``DateLastContentAdded``，缺失
    # 回落 ``DateCreated``）：增量轮按此字段倒序拉、水位线比对用
    # 它。只存原文串，解析/比较归拉取方；
    # 两字段都缺时为 None，该记录不参与水位计算（拉取方按未证明
    # 已旧处理，不据此早停）。放末尾保位置构造兼容。
    last_content_added_at: Optional[str] = None
    # 源站自带元数据（透传组，2026-11-02 多源合并填充）：苹果 CMS
    # list 负载里现成带的海报/地区/分类名/备注/评分/演员/导演/简介，
    # 由 ``applecms_sync.cms_entry_to_record`` 采集。**不参与聚合键、
    # 不参与配对**（键与证据口径不变，见 aggregate_key_for）；消费方
    # 是 CMS 库同步工人的合行填空——只填空缺、TMDB 仍是权威（行上
    # 以 metadata_source='applecms' 标记为暂定填充，TMDB 命中后可
    # 盖过）。Emby 型上游记录恒为空。放末尾保位置构造兼容。
    poster_url: str = ""
    area: str = ""
    category_name: str = ""
    remarks: str = ""
    score: Optional[float] = None
    actors: Tuple[str, ...] = ()
    directors: Tuple[str, ...] = ()
    blurb: str = ""


def _provider_id(provider_ids: object, *names: str) -> Optional[str]:
    if not isinstance(provider_ids, dict):
        return None
    for name in names:
        value = provider_ids.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _raw_timestamp(raw: dict) -> Optional[str]:
    """取增量排序时间戳原文：DateLastContentAdded 优先、DateCreated 兜底。

    DateLastContentAdded 是 Emby 的「内容最近入库时间」：新条目
    入库即赋值，剧集新增集时所属剧的该值同步刷新（本站 compat
    层 Items 实现同口径，见 api.py 的 _date_last_content_added）。
    非字符串/空值按缺失处理（返回 None）。
    """
    for key in ("DateLastContentAdded", "DateCreated"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_emby_item(
    raw: dict, *, source_id: str, category_id: str = ""
) -> UpstreamCatalogRecord:
    """把一个 Emby/Jellyfin Items 项解析为目录记录（纯函数）。

    只取身份与结构字段；凭据类键、原始字典、对方服务器路径
    一律不进记录。``ProviderIds`` 的 Tmdb/Imdb 是身份证据，
    不是凭据，保留。
    """
    if not isinstance(raw, dict):
        raise ValueError("emby item must be a dict")
    item_id = raw.get("Id")
    if not item_id:
        raise ValueError("emby item missing Id")
    media_sources = raw.get("MediaSources")
    versions: List[UpstreamVersion] = []
    if isinstance(media_sources, list):
        for media_source in media_sources:
            if not isinstance(media_source, dict):
                continue
            media_source_id = media_source.get("Id")
            if not media_source_id:
                continue
            versions.append(
                UpstreamVersion(
                    media_source_id=str(media_source_id),
                    name=str(media_source.get("Name") or ""),
                    run_time_ticks=_as_int(media_source.get("RunTimeTicks")),
                )
            )
    return UpstreamCatalogRecord(
        source_id=source_id,
        upstream_item_id=str(item_id),
        name=str(raw.get("Name") or ""),
        item_type=str(raw.get("Type") or ""),
        category_id=category_id,
        production_year=_as_int(raw.get("ProductionYear")),
        tmdb_id=_provider_id(raw.get("ProviderIds"), "Tmdb", "tmdb"),
        imdb_id=_provider_id(raw.get("ProviderIds"), "Imdb", "imdb"),
        series_name=str(raw.get("SeriesName") or ""),
        season_number=_as_int(raw.get("ParentIndexNumber")),
        episode_number=_as_int(raw.get("IndexNumber")),
        versions=tuple(versions),
        series_upstream_id=str(raw.get("SeriesId") or ""),
        last_content_added_at=_raw_timestamp(raw),
    )


def parse_emby_items_page(
    page: dict, *, source_id: str, category_id: str = ""
) -> Tuple[List[UpstreamCatalogRecord], int]:
    """解析一页 Emby Items 响应，返回（记录，TotalRecordCount）。

    Items 不是列表时按空页处理（上游返回形态异常时不炸调用方，
    对账层按“取页失败/空页”各自的纪律处理，见 :func:`reconcile`）。
    """
    if not isinstance(page, dict):
        return [], 0
    items = page.get("Items")
    records: List[UpstreamCatalogRecord] = []
    if isinstance(items, list):
        for raw in items:
            try:
                records.append(
                    parse_emby_item(raw, source_id=source_id, category_id=category_id)
                )
            except ValueError:
                # 无 Id 的脏项跳过，不让一条脏数据毁掉整页。
                continue
    return records, _as_int(page.get("TotalRecordCount")) or 0


def iter_emby_pages(
    fetch_page: Callable[[int, int], dict],
    *,
    source_id: str,
    category_id: str = "",
    page_size: int,
    on_page: Optional[Callable[[List[UpstreamCatalogRecord], int], bool]] = None,
) -> Iterable[UpstreamCatalogRecord]:
    """按 Emby StartIndex/Limit 分页拉取（传输由调用方注入）。

    调用方须保证 ``fetch_page`` 已授权、已过 SSRF 校验；本模块
    不发 HTTP。某页返回 0 条即停（防上游 TotalRecordCount 说谎
    导致死循环）；TotalRecordCount 为 0 时只拉首轮即停。

    ``on_page``（增量拉取用）：每取到一页后以（本页记录，
    TotalRecordCount）回调一次，返回 True 表示本页处理完即停
    （不再取下一页）；该页记录照常产出。缺省 None 时行为与
    历史口径逐字一致。回调异常照常上抛（与取页失败同路）。
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    start = 0
    while True:
        page = fetch_page(start, page_size)
        records, total = parse_emby_items_page(
            page, source_id=source_id, category_id=category_id
        )
        stop_after = bool(on_page(records, total)) if on_page else False
        yield from records
        if stop_after:
            break
        if not records:
            break
        start += len(records)
        if total and start >= total:
            break


# ---------------------------------------------------------------------------
# 聚合层：作品 + 线路 + 版本（对应 upstream_aggregate_items/variants）
# ---------------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").strip().lower()
    text = re.sub(r"[\s\-_.,:;!?()\[\]{}'\"]+", "", text)
    return text


def aggregate_key_for(record: UpstreamCatalogRecord) -> str:
    """作品归一键：外部身份优先，缺身份时退化为标题+年份。

    优先级 tmdb > imdb > 标题+年份——与 #13 远端身份“证据先行”
    同序：有外部身份绝不靠标题猜。剧集单集在系列键后追加
    季/集号，同一系列的不同集绝不归成同一作品。
    """
    if record.tmdb_id:
        base = f"tmdb:{record.tmdb_id}"
    elif record.imdb_id:
        base = f"imdb:{record.imdb_id}"
    else:
        title = _normalize_title(record.series_name or record.name)
        year = record.production_year or ""
        base = f"title:{title}:{year}"
    if record.item_type == "Episode" or record.episode_number is not None:
        # 注意：单集即使有 tmdb/imdb，季集号仍须参与键——同一
        # 剧集的不同集靠它区分，同一集重复出现时靠它归一。
        base = f"{base}:s{record.season_number}:e{record.episode_number}"
    return base


def unified_search_enabled() -> bool:
    """总开关是否开启（每次调用现读 env，默认关）。"""
    return (str(os.getenv(UNIFIED_SEARCH_ENV, "0") or "0")
            .strip().lower() not in {"0", "false", "no", "off", ""})


def normalize_source_filter(raw: Optional[Iterable[str]]) -> Optional[Set[str]]:
    """把源过滤参数归一为源 id 字符串集合。

    接受 ``"1"`` 与 ``"upstream:1"`` 两种写法；空集合 / None = 不过滤。
    """
    if raw is None:
        return None
    cleaned = set()
    for token in raw:
        token = (str(token) or "").strip()
        if token.startswith("upstream:"):
            token = token[len("upstream:"):]
        if token:
            cleaned.add(token)
    return cleaned or None


def _source_sort_key(source_id: str) -> Tuple[int, object]:
    """源 id 确定性排序键：纯数字按数值排，其余按字符串排。"""
    if source_id.isdigit():
        return (0, int(source_id))
    return (1, source_id)


def local_aggregate_key(*, name: str, item_type: str,
                        production_year: Optional[int],
                        scan_year: Optional[int],
                        tmdb_id: Optional[str], imdb_id: Optional[str],
                        season_number: Optional[int],
                        episode_number: Optional[int]) -> str:
    """本站条目的归一键（调用方唯一入口，纯函数）。

    年份口径须与上游侧一致：上游记录的 production_year 来自对方
    ProductionYear，与本站 production_year 对得上；scan_year 只是扫描期
    文件名年份，仅在 production_year 为空时回落。用 scan_year 优先算键
    会让无外部身份的同一作品（标题+年份键）在两边算出不同的键，
    去重 miss、本地行与上游行重复出现。
    """
    probe = UpstreamCatalogRecord(
        source_id="", upstream_item_id="",
        name=name, item_type=item_type,
        production_year=(production_year if production_year is not None
                         else scan_year),
        tmdb_id=tmdb_id, imdb_id=imdb_id, series_name="",
        season_number=season_number, episode_number=episode_number)
    return aggregate_key_for(probe)


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
    origin: str  # "local" 或 "upstream:<source_id>"
    source: str  # "local" 或源 id
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
# 上游标题匹配（复用搜索归一化，不新造索引）
# ---------------------------------------------------------------------------


def matches_upstream_term(record: UpstreamCatalogRecord, term: str) -> bool:
    """上游记录是否命中查询词：归一化标题/剧名子串。

    用 ``search_variants`` 的繁简变体逐个比对紧凑串；空词恒为
    False（空词不 dump 全库）。拼音与别名因上游记录无此字段而
    不参与匹配（见模块头已知局限）。
    """
    term = (term or "").strip()
    if not term:
        return False
    haystacks = [
        normalize_compact(record.name),
        normalize_compact(record.series_name or ""),
    ]
    haystacks = [text for text in haystacks if text]
    if not haystacks:
        return False
    for variant in search_variants(term):
        needle = normalize_compact(variant)
        if not needle:
            continue
        if any(needle in hay for hay in haystacks):
            return True
    return False


# ---------------------------------------------------------------------------
# 合并：本地在前、上游在后、同键去重、单源失败隔离
# ---------------------------------------------------------------------------


def merge_search_results(
    local_entries: Iterable[LocalSearchEntry],
    per_source: Dict[str, object],
    term: str,
    *,
    source_filter: Optional[Set[str]] = None,
) -> UnifiedSearchResponse:
    """合并本站与多源上游搜索结果（纯函数，不写库、不发 HTTP）。

    ``per_source`` 的值约定：
    成功时为该源的目录记录列表（``UpstreamCatalogRecord``），
    失败时为 ``Exception`` 实例或错误字符串；未知形态按失败计，
    不抛异常。空词返回空结果（不 dump 全库）。
    """
    query = (term or "").strip()
    wanted = normalize_source_filter(source_filter)
    if not query:
        statuses = tuple(
            SourceStatus(
                source_id=source_id,
                status=("not_requested"
                        if wanted is not None and source_id not in wanted
                        else "unavailable"),
                error=("not_requested" if wanted is not None
                        and source_id not in wanted else "empty_query"),
            )
            for source_id in sorted(per_source, key=_source_sort_key)
        )
        return UnifiedSearchResponse(query=query, results=(), sources=statuses)

    def _requested(source_id: str) -> bool:
        return wanted is None or source_id in wanted

    results: List[UnifiedSearchResult] = []
    by_key: Dict[str, UnifiedSearchResult] = {}
    # 本地在前：按调用方传入的 rank_items 顺序，本地行不重排。
    for entry in local_entries:
        if entry.aggregate_key in by_key:
            continue
        row = UnifiedSearchResult(
            aggregate_key=entry.aggregate_key,
            name=entry.name,
            item_type=(entry.item_type or "").lower(),
            production_year=entry.production_year,
            origin="local",
            source="local",
            also_on_sources=(),
        )
        by_key[entry.aggregate_key] = row
        results.append(row)

    # 上游：先按源内去重、按查询词过滤，再跨源归一。
    upstream_by_key: Dict[str, dict] = {}
    statuses: List[SourceStatus] = []
    for source_id in sorted(per_source, key=_source_sort_key):
        value = per_source[source_id]
        if not _requested(source_id):
            statuses.append(SourceStatus(source_id=source_id,
                                         status="not_requested"))
            continue
        if isinstance(value, Exception):
            # 异常原文绝不进 error 体：它可能含带 api_key 的上游 URL
            # （模块头凭据纪律）；统一记固定原因码，不拼接异常文本。
            statuses.append(SourceStatus(source_id=source_id,
                                         status="unavailable",
                                         error="upstream_unavailable"))
            continue
        if isinstance(value, str):
            statuses.append(SourceStatus(source_id=source_id,
                                         status="unavailable",
                                         error=value))
            continue
        if not (isinstance(value, list) and all(
                isinstance(record, UpstreamCatalogRecord)
                for record in value)):
            statuses.append(SourceStatus(
                source_id=source_id, status="unavailable",
                error="unexpected per-source value type"))
            continue
        seen_in_source: Set[str] = set()
        matched = 0
        for record in value:
            if not matches_upstream_term(record, query):
                continue
            key = aggregate_key_for(record)
            if key in seen_in_source:
                continue
            seen_in_source.add(key)
            matched += 1
            slot = upstream_by_key.get(key)
            if slot is None:
                upstream_by_key[key] = {
                    "name": record.series_name or record.name,
                    "item_type": (record.item_type or "").lower(),
                    "production_year": record.production_year,
                    "sources": [source_id],
                }
            else:
                slot["sources"].append(source_id)
        statuses.append(SourceStatus(source_id=source_id, status="ok",
                                     count=matched))

    # 上游在后：按 aggregate_key 排序。
    for key in sorted(upstream_by_key):
        slot = upstream_by_key[key]
        sources = slot["sources"]
        primary = sources[0]
        existing = by_key.get(key)
        if existing is not None:
            # 本地已有：上游来源只追加到 also_on_sources，不另起一行。
            merged_also = tuple(
                dict.fromkeys(list(existing.also_on_sources) + sources))
            merged_row = UnifiedSearchResult(
                aggregate_key=existing.aggregate_key,
                name=existing.name,
                item_type=existing.item_type,
                production_year=existing.production_year,
                origin=existing.origin,
                source=existing.source,
                also_on_sources=merged_also,
            )
            results[results.index(existing)] = merged_row
            by_key[key] = merged_row
            continue
        row = UnifiedSearchResult(
            aggregate_key=key,
            name=slot["name"],
            item_type=slot["item_type"],
            production_year=slot["production_year"],
            origin=f"upstream:{primary}",
            source=primary,
            also_on_sources=tuple(sources[1:]),
        )
        by_key[key] = row
        results.append(row)

    return UnifiedSearchResponse(query=query, results=tuple(results),
                                 sources=tuple(statuses))


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


# ---------------------------------------------------------------------------
# 上游现查的传输与凭据（原上游聚合跑批的同名函数簇；跑批删除后
# 本模块是唯一存活消费方，故迁入本模块自持）
# ---------------------------------------------------------------------------

class UpstreamSearchError(Exception):
    """上游现查的运行错误：带 HTTP 状态码与短码，调用方映射为固定原因码。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _credential_error() -> UpstreamSearchError:
    return UpstreamSearchError(
        409, "upstream_credential_invalid",
        "上游凭据失效，请管理员重录该源凭据")


def _unavailable_error() -> UpstreamSearchError:
    return UpstreamSearchError(
        502, "upstream_unavailable", "上游服务当前不可用，请稍后再试")


def _frontend_blocked_error() -> UpstreamSearchError:
    return UpstreamSearchError(
        502, "upstream_frontend_blocked",
        "上游前门（nginx）拦截，未到 Emby：请对方放行本方出口 IP，"
        "重录凭据无效")


def _forbidden_error(body) -> UpstreamSearchError:
    """403 分流：Emby JSON 报错体 → 凭据失效；无 JSON 体（nginx HTML
    被传输层吞成 None）→ 前门拦截 502。重录凭据修不好后者，绝不误判。"""
    if isinstance(body, dict):
        return _credential_error()
    return _frontend_blocked_error()



def _default_http_post(url: str, payload: dict, headers: dict,
                       timeout: float):
    """认证 POST：返回 (status|None, body|None)，超时/断连给 (None, None)。"""
    data = json.dumps(payload).encode("utf-8")
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "Aetrix/1.0")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with upstream_http.safe_urlopen(req, timeout=timeout) as resp:
            try:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                body = None
            return resp.status, body
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            body = None
        return exc.code, body
    except Exception:
        return None, None


def _default_http_get(url: str, headers: dict, timeout: float):
    """目录 GET：返回 (status|None, body|None)，超时/断连给 (None, None)。"""
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "Aetrix/1.0")
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with upstream_http.safe_urlopen(req, timeout=timeout) as resp:
            try:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                body = None
            return resp.status, body
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            body = None
        return exc.code, body
    except Exception:
        return None, None


def _source_headers(source) -> dict:
    """发往上游的请求头：源上配的 UA / Referer / 附加头。

    与 upstream_playback._source_headers 同口径；凭据绝不走这里。
    """
    headers: dict = {"Accept": "application/json"}
    # 前门会拦 Python-urllib 默认 UA（实测 403，聚合误判为凭据失效）：
    # 源没配 UA 时默认用 Aetrix UA，不许裸用 urllib 默认 UA 出站。
    headers["User-Agent"] = getattr(source, "user_agent", None) or "Aetrix/1.0"
    if getattr(source, "referer", None):
        headers["Referer"] = source.referer
    try:
        extra = json.loads(source.extra_headers_json or "{}")
    except (TypeError, ValueError):
        extra = {}
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value
    return headers


def _api_key_for_source(source, *, http_post: Optional[Callable] = None):
    """取上游 Items 请求用的 api_key（匿名返回 None）。

    - 未配凭据 → None（匿名访问）；
    - api_key 形态 → 密封值本身就是 key；
    - password 形态 → 向上游认证口换 AccessToken，换不到按凭据
      失效 / 上游不可用映射，绝不降级成匿名；403 按体分流
      （JSON 体 → 凭据失效，无 JSON 体 → 前门拦截 502）。
    密封解不开（SealedSecretError）→ 凭据失效，调用方须请管理员重录。
    """
    try:
        cred = us.load_credential(source)
    except us.SealedSecretError as exc:
        raise _credential_error() from exc
    if cred is None:
        return None
    if cred["auth_method"] == "api_key":
        return cred["secret"]
    if cred["auth_method"] == "password":
        post = _default_http_post if http_post is None else http_post
        base = (source.base_url or "").rstrip("/")
        headers = dict(_source_headers(source))
        headers["Content-Type"] = "application/json"
        headers["X-Emby-Authorization"] = (
            'MediaBrowser Client="Aetrix", Device="upstream-aggregate", '
            'DeviceId="upstream-aggregate", Version="1.0"'
        )
        status, body = post(
            f"{base}/Users/AuthenticateByName",
            {"Username": cred["username"], "Pw": cred["secret"]},
            headers, HTTP_TIMEOUT_SEC)
        if status == 200 and isinstance(body, dict) and body.get("AccessToken"):
            return str(body["AccessToken"])
        if status == 401:
            raise _credential_error()
        if status == 403:
            raise _forbidden_error(body)
        raise _unavailable_error()
    raise _unavailable_error()


def _resolve_api_key(source, *, http_post=None):
    """取 Items 请求用的 api_key；None = 匿名。只解析一次供逐库复用。"""
    return _api_key_for_source(source, http_post=http_post)



# ---------------------------------------------------------------------------
# 上游现查（只读 Emby /Items?SearchTerm=，不是聚合跑批）
# ---------------------------------------------------------------------------


def search_upstream_source(source, term: str, *,
                           http_get=None, http_post=None):
    """对单个已启用 Emby/Jellyfin 型源做只读现查。

    返回 ``(records, error)``：成功时 ``error`` 为 None；失败时
    ``records`` 为空、``error`` 为固定原因码 + 固定文案（绝不含
    上游原始体与异常文本）。applecms 型源调用方不许走到这里
    （调用方直接记 unavailable，见 unified_search_api）。

    传输与凭据用本模块自持实现（``_resolve_api_key`` /
    ``_default_http_get`` / ``_source_headers``）：凭据只在容器内
    解密使用，绝不进返回值。本函数不写库、不触发聚合。
    """
    term = (term or "").strip()
    if not term:
        return [], "empty_query"
    get = _default_http_get if http_get is None else http_get
    try:
        api_key = _resolve_api_key(source, http_post=http_post)
    except UpstreamSearchError as exc:
        return [], f"{exc.code}: {exc.message}"
    except Exception:  # noqa: BLE001 — 异常文本可能含带 key 的 URL，绝不外泄
        return [], "upstream_unavailable: 上游搜索请求失败"
    base = (source.base_url or "").rstrip("/")
    headers = _source_headers(source)
    params = {
        "SearchTerm": term,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Episode",
        "Fields": "ProviderIds",
        "StartIndex": "0",
        "Limit": str(UPSTREAM_SEARCH_LIMIT),
    }
    if api_key:
        params["api_key"] = api_key
    url = f"{base}/Items?{urllib.parse.urlencode(params)}"
    try:
        status, body = get(url, headers, HTTP_TIMEOUT_SEC)
    except Exception:  # noqa: BLE001 — 同上，不外泄异常文本
        return [], "upstream_unavailable: 上游搜索请求失败"
    if status == 200 and isinstance(body, dict):
        records, _total = parse_emby_items_page(
            body, source_id=str(source.id))
        matched = [record for record in records
                   if matches_upstream_term(record, term)]
        return matched, None
    if status == 401:
        return [], "upstream_credential_invalid: 上游凭据失效，请管理员重录该源凭据"
    if status == 403:
        if isinstance(body, dict):
            return [], ("upstream_credential_invalid: "
                        "上游凭据失效，请管理员重录该源凭据")
        return [], ("upstream_frontend_blocked: "
                    "上游前门（nginx）拦截，未到 Emby："
                    "请对方放行本方出口 IP，重录凭据无效")
    if status == 429:
        return [], "upstream_quota_exhausted: 上游额度已耗尽，请稍后再试或换个源"
    return [], "upstream_unavailable: 上游服务当前不可用，请稍后再试"
