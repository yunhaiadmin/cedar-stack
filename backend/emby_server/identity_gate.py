"""身份准入货架：判据与总开关（身份准入货架一期，案 A）。

口径（2026-10-24 用户拍板，设计稿 reports/identity-gated-shelf-design-20261024.md）：
用户端分三面——**货架面**（浏览/列表/计数/推荐/最新，系统主动枚举）只露出
已绑定身份的条目；**搜索面不收紧**、**直达面**（凭 guid/链接直达）放行。
本模块只服务货架面：搜索/直达出口不许挂这里的条件。管理端出口不挂，
天然继续看见全部条目（横切能力只许一套：全仓身份判据只许经本模块，
任何出口不许私自判 tmdb_id）。

已绑定判据：
- movie/series：自身 ``tmdb_id`` 或 ``imdb_id`` 任一 strip 后非空，
  或 ``external_ids`` 里任一在用来源键（见 EXTERNAL_ID_KEYS）非空。
  2026-11-02 站主拍板扩围（先「豆瓣认到就上架」、再「都要算进去」），
  推翻此前「只有豆瓣身份不算」：任一元数据源认到的编号都是身份；
  名单只认代码实际写入的键，名单外（含手填自由键）不算。
- season/episode：跟随所属 series 行（``series_id`` 指向行满足上面的
  判据）；自身有 tmdb/imdb 亦算（episode 口径与 AnyProviderIdEquals、
  DTO 层 ``_provider_ids`` 的「集继承剧编号」既有先例一致）。
- 其余类型（person 等）不在货架治理内，条件恒真。

落地方式：查询构造层共享 helper（设计稿 §5.1 方案甲）——每个货架出口在
自己的**基础查询**上显式 ``filter(identity_clause())``，SQL 层过滤不后置
内存剔除（守 Latest 注释立过的「先占 limit 名额」纪律）。

判据物化（BE-24，2026-11-17）：原 ``identity_clause()`` 是「自身两列 +
external_ids 七键 LIKE + series EXISTS」逐行求值，不可索引——生产实测
带门计数约 13s/条（items_list_cache 计数缓存注释），只能靠缓存摊薄。
现把判据物化为条目上的两列（见 models.MediaItem）：``identity_bound``
（自身已绑定，与 :func:`is_bound_row` 的 Python 口径逐字一致）与
``has_identity``（货架判据结果，season/episode 跟随 series）。子句只读
``has_identity`` 列、走 idx_item_identity_gate 索引；LIKE/EXISTS 形态
只留给 enrich 队列谓词（:func:`external_bound_sql`）等审计面。标记由
本模块底部的 Session flush 同步钩子维护（写路径落库前重算、series
判据翻转级联子行），Core 批量软删/复活走 :func:`resync_children_of_series`
显式重算，存量由 scripts/backfill_identity_flags.py 回填并对账。

总开关：env ``IDENTITY_SHELF_GATE``（默认 1=开），模块加载时读一次
（与既有 env 同口径，改需重启）。显式设 0 关 = 所有出口与旧行为逐字
一致，是本功能的唯一回滚手段。列表 ID 清单缓存、去重缓存与计数缓存
的键都含门状态段（见 :func:`cache_variant_segment`，BE-08），切换开关
重启后新口径键自然生效、旧键等 TTL 过期，不再需要配套 bump 数据
版本清缓存。

与治理流水线的关系（2026-11-17 BE-06 收敛，定版口径以此处为准）：
货架露出**只由本模块三开关决定**；``visibility_governance`` 是审计
流水线（未绑定条目入队＋复核任务＋只追加事件），其开关
``VISIBILITY_GOVERNANCE_ENABLED`` 不决定任何出口的露出——只开它、
不开本模块总开关，货架一条都不会少露。四开关默认值统一为开，与
生产现行口径一致（2026-11-17 生产三容器亲验全 =1），消除换机 /
重装 / 丢 env 时货架门静默失效的落差；要关门必须显式把
``IDENTITY_SHELF_GATE`` 设 0（并配套 bump 数据版本清缓存）。

二期 / 三期开关（2026-10-25 设计收尾，默认开）：
- ``IDENTITY_SHELF_PHASE2_ENABLED``：个人面与剧集面货架过滤（EA 继续观看 /
  追剧下一集 / 收藏 / 已看、门户继续观看 / 门户收藏 / 追新日历）。只在总开关
  同时开启时生效——总开关是唯一回滚手段，关总开关必须连二期一起回旧口径，
  不许二期单独残留（否则浏览已回旧口径、个人面却还藏着，口径撕裂）。
- ``IDENTITY_SHELF_PHASE3_ENABLED``：代表与版本完善（组内已绑定优先选主、
  版本列表并入同库同身份未合并副本、计数按代表组对齐）。同样只在总开关
  同时开启时生效。二期出口均为无缓存直查，开关翻转无缓存纪律；三期翻转
  影响列表页缓存（选主结果进缓存页）与去重缓存，但三层缓存键已含门
  状态段（BE-08，见 :func:`cache_variant_segment`），翻转重启后自动
  换代，不再需要 bump 数据版本。
- 案 B（直达面收紧）不在二期/三期做：用户 2026-10-24 拍板案 A（货架严 /
  搜索宽 / 直达放行），案 B 从未获专项拍板；详情 / 季集 / 播放直达面
  永远不挂这里的条件。
- 三期分组键说明：设计稿原定的「分组键扩为 tmdb→imdb→弱键三级」本批
  未动 ``dedup_key`` / ``dedup_key_for``（见 PR 说明：分页去重层
  ``_DEDUP_FIELDS`` 与扫描防重层都要同步改键才一致，属高危热路径，
  另立项再做）。本批三期只做不依赖分组键的三项：组内已绑定优先选主
  （选主不改分组，只改组内代表人选）、版本列表按分层身份直接匹配、
  计数按分层身份组 SQL 直接对齐——三者都不改 ``dedup_key`` 的分组结果，
  开关关时与旧行为逐字一致。
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from sqlalchemy import and_, event, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from backend.emby_server import models as em
from backend.emby_server.env_util import env_int

logger = logging.getLogger(__name__)

#: 身份准入货架总开关（默认开）。启动时读一次，热路径不碰 env。
IDENTITY_SHELF_GATE = env_int("IDENTITY_SHELF_GATE", 1, 0, 1)

#: 二期开关（默认开）：个人面与剧集面货架过滤。只在总开关同时开启时生效。
IDENTITY_SHELF_PHASE2_ENABLED = env_int("IDENTITY_SHELF_PHASE2_ENABLED", 1, 0, 1)

#: 三期开关（默认开）：代表与版本完善。只在总开关同时开启时生效。
IDENTITY_SHELF_PHASE3_ENABLED = env_int("IDENTITY_SHELF_PHASE3_ENABLED", 1, 0, 1)

_TOP_TYPES = ("movie", "series")
_CHILD_TYPES = ("season", "episode")


def gate_enabled() -> bool:
    """货架身份门是否开启（全仓读开关的唯一入口；测试 patch 本模块常量）。"""
    return IDENTITY_SHELF_GATE > 0


def phase2_enabled() -> bool:
    """二期个人面过滤是否生效：总开关与二期开关须同时开启。

    总开关是唯一回滚手段——关总开关必须连二期一起回旧口径，不许二期
    单独残留（测试 patch 本模块两个常量）。
    """
    return gate_enabled() and IDENTITY_SHELF_PHASE2_ENABLED > 0


def phase3_enabled() -> bool:
    """三期代表与版本完善是否生效：总开关与三期开关须同时开启（同上）。"""
    return gate_enabled() and IDENTITY_SHELF_PHASE3_ENABLED > 0


def cache_variant_segment() -> str:
    """门状态三位（总门/二期/三期）的缓存键段（BE-08）。

    列表清单、去重 dropped 集与计数的结果都随门形态变化，三层缓存键
    统一拼入本段：开关翻转（改 env＋重启）后键自然换代，旧口径结果
    等 TTL 过期，不会再出现「门已切换、缓存里还躺着旧口径清单最长
    300s」。全仓缓存键只许经本函数取门段，不许各层私拼。
    """
    return (f"g{1 if gate_enabled() else 0}"
            f"p{1 if phase2_enabled() else 0}"
            f"q{1 if phase3_enabled() else 0}")


#: external_ids 里算数的来源键（2026-11-02 站主拍板扩围）：以代码实际
#: 写入的键为准——全部经 metasources/engine.apply_to_item 落库，键来自
#: metasources/sources.py 各源 Hit 的 external_ids：tmdb / imdb（TMDB、
#: OMDb、TVmaze、TheTVDB 命中与接力产物）、douban（suggest / Frodo /
#: IMDb 桥）、bangumi、anilist、tvmaze、tvdb。TheTVDB remote_ids 的
#: sourceName 自由键（如 "IMDB"）形状不规范，不在名单内；名单外键
#: （含管理端手填）一律不算——要扩围改这一处，全站口径随之变。
EXTERNAL_ID_KEYS = ("tmdb", "imdb", "douban", "bangumi",
                    "anilist", "tvmaze", "tvdb")


def _external_ids_map(item) -> dict:
    """条目 external_ids 解析成 dict（坏 JSON / 非 dict 一律 {}）。"""
    raw = getattr(item, "external_ids", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def has_external_identity(item) -> bool:
    """external_ids 任一在用来源键非空（Python 口径，与 SQL 同判据）。"""
    ids = _external_ids_map(item)
    for key in EXTERNAL_ID_KEYS:
        value = ids.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return True
        elif isinstance(value, (int, float)):
            if value:
                return True
        elif str(value).strip():
            return True
    return False


def is_bound_row(item) -> bool:
    """Python 侧单行已绑定判定（movie/series 自身口径）。

    与 :func:`_own_bound` 的 SQL 语义逐字对应：``tmdb_id`` / ``imdb_id``
    任一 strip 后非空，或 ``external_ids`` 任一在用来源键非空（2026-11-02
    扩围）。只用于内存对象（如组内选主）；查询一律走
    :func:`identity_clause`，不许各处私自拼条件。season/episode 的
    「跟随 series」不在此函数内——内存选主只处理 movie/series 组
    （``DEDUP_TYPES``），集跟随只在 SQL 层表达。
    """
    return bool(((getattr(item, "tmdb_id", None) or "").strip())
                or ((getattr(item, "imdb_id", None) or "").strip())
                or has_external_identity(item))


def _external_bound_sql(col) -> object:
    """external_ids（JSON 文本列）任一在用来源键非空的 SQL 判据。

    PG 与 SQLite 双口径：只用 LIKE（两边同语义），不用各库方言的
    json 函数。落库形为 json.dumps 标准形（``"键": "值"``，冒号后带
    空格），手写/历史数据可能无空格——两种分隔形都认；``"键": ""``
    的空值形必须排除：正形命中且空形不命中才算该键非空。空白值
    （``" "``）各写入方都不会产生（写入前已按真值过滤实编号），SQL
    层不另处理，口径以 Python 侧 :func:`is_bound_row` 为准。
    """
    conds = []
    for key in EXTERNAL_ID_KEYS:
        present = or_(
            col.like(f'%"{key}": "%'),
            col.like(f'%"{key}":"%'),
        )
        empty = or_(
            col.like(f'%"{key}": ""%'),
            col.like(f'%"{key}":""%'),
        )
        conds.append(and_(present, ~empty))
    return or_(*conds)


def external_bound_sql(col) -> object:
    """external_ids 任一在用来源键非空的 SQL 判据（公开入口）。

    实现就是 :func:`_external_bound_sql`；enrich 队列谓词复用它，
    不许在队列模块里另写一份 LIKE 模式（横切能力只许一套）。
    """
    return _external_bound_sql(col)


def _own_bound(item) -> object:
    """行自身已绑定：tmdb_id / imdb_id 任一在 strip 后非空，或
    external_ids 任一在用来源键非空（2026-11-02 扩围）。

    ``item`` 可以是 MediaItem 实体类或 aliased 别名。存量数据可能有空串 /
    纯空白（扫描种子里就有 ``tmdb_id="   "`` 的行），必须 trim 后判空，
    只判 ``IS NOT NULL`` 会把空白串误判成已绑定。
    """
    return or_(
        and_(item.tmdb_id.isnot(None), func.trim(item.tmdb_id) != ""),
        and_(item.imdb_id.isnot(None), func.trim(item.imdb_id) != ""),
        _external_bound_sql(item.external_ids),
    )


def identity_clause():
    """挂在 MediaItem 查询上的身份准入条件（SQLAlchemy 表达式）。

    只在 :func:`gate_enabled` 为真时由调用方挂载；关时不许挂
    （关 = 旧口径逐字一致）。

    判据已物化（BE-24）：只读条目的 ``has_identity`` 列——该列由本模块
    的同步钩子/回填脚本按 :func:`is_bound_row` 的 Python 口径维护，
    语义与旧「tmdb/imdb 两列 + external_ids 七键 LIKE + series EXISTS」
    逐行求值完全一致（对齐测试 test_identity_gate_flags 钉住）：

    - movie/series：自身绑定；
    - season/episode：自身绑定 OR 所属 series 绑定且未软删；
    - 其余类型（person/studio 等）：恒真（其 has_identity 恒 True；
      列为 NULL＝未回填，按未通过处理，存量回填后不许再出现成片 NULL）。

    带门计数/列表走 idx_item_identity_gate，不再全表逐行算 LIKE/EXISTS。
    """
    governed = _TOP_TYPES + _CHILD_TYPES
    return or_(
        and_(
            em.MediaItem.item_type.in_(governed),
            em.MediaItem.has_identity.is_(True),
        ),
        # item_type 非空（表约束 nullable=False）；非四类不在治理内
        ~em.MediaItem.item_type.in_(governed),
    )


# ==================== 判据物化维护（BE-24） ====================
#
# 两列的写入面只有三处，缺一不可：
# 1. 下面的 Session before_flush/after_flush 钩子——覆盖一切 ORM 写路径
#    （扫描入库、刮削补全、求片补绑、后台编辑、脚本），落库前按内存新值
#    重算；series 的有效判据翻转时在 after_flush 级联其 season/episode。
# 2. soft_delete 的 Core 批量软删/复活（mark_deleted / resurrect /
#    restore_all）不走 ORM 事件——那三处显式调 resync_children_of_series
#    / resync_all_children 兜住，不留漂移时间窗。
# 3. 存量回填 scripts/backfill_identity_flags.py（按 compute_flag_map
#    的 Python 口径全表算、dry-run 对账后分批写、写完再对账归零）。

_WATCHED_ATTRS = ("tmdb_id", "imdb_id", "external_ids",
                  "item_type", "series_id", "deleted_at")
_FLIP_ATTRS = ("tmdb_id", "imdb_id", "external_ids", "deleted_at")
_GOVERNED_TYPES = _TOP_TYPES + _CHILD_TYPES


def _resolved_verdict(item_type, own, series_effective) -> bool:
    """单行货架判据（纯函数）：与 identity_clause 的物化口径逐字对应。"""
    if item_type in _TOP_TYPES:
        return bool(own)
    if item_type in _CHILD_TYPES:
        return bool(own) or bool(series_effective)
    return True


def _series_effective(bound, deleted_at) -> bool:
    """series 行「能替子行绑定」的判据：自身已绑定且未软删。"""
    return bool(bound) and deleted_at is None


def _fetch_series_rows(session, series_ids) -> dict:
    """批量查 series_id 指向行的原始身份字段 → {id: SimpleNamespace}。

    Core 直查：全局软删 ORM 钩子下推不到 Core 查询，已软删的行也查得到
    （调用方按 deleted_at 显式判，不靠钩子滤成「不存在」蒙混）。旧
    EXISTS 不校验目标行类型，这里同样不校验 item_type，口径逐字对齐。
    """
    ids = {int(i) for i in (series_ids or []) if i is not None}
    if not ids:
        return {}
    t = em.MediaItem.__table__
    stmt = select(
        t.c.id, t.c.identity_bound, t.c.tmdb_id, t.c.imdb_id,
        t.c.external_ids, t.c.deleted_at,
    ).where(t.c.id.in_(ids))
    return {
        r[0]: SimpleNamespace(
            identity_bound=r[1], tmdb_id=r[2], imdb_id=r[3],
            external_ids=r[4], deleted_at=r[5])
        for r in session.connection().execute(stmt).fetchall()
    }


def _row_effective(row) -> bool:
    """库里一行（_fetch_series_rows 的结果）的有效判据：identity_bound
    为 NULL（未回填）时按原始字段现算，不读 has_identity——对账/维护
    必须独立于被维护的派生列。"""
    bound = row.identity_bound
    if bound is None:
        bound = is_bound_row(row)
    return _series_effective(bound, row.deleted_at)


def _fetch_series_effective(session, series_ids) -> dict:
    """批量查 series_id 指向行的有效判据 → {id: bool}（查不到的 id 不在
    结果里，调用方按 False 处理，与旧 EXISTS 查无此行同义）。"""
    return {sid: _row_effective(row)
            for sid, row in _fetch_series_rows(session, series_ids).items()}


def compute_flag_map(session) -> dict:
    """全表应然标记 {id: (identity_bound, has_identity)}（回填/对账共用）。

    Core 直查原始字段逐行按 Python 判据算，不读标记列本身——对账必须
    独立于被对账的东西。含已软删/已合并行（标记对所有行有定义，子句
    的可见性由软删钩子另行把关）。
    """
    t = em.MediaItem.__table__
    stmt = select(
        t.c.id, t.c.item_type, t.c.series_id, t.c.tmdb_id,
        t.c.imdb_id, t.c.external_ids, t.c.deleted_at,
    )
    rows = session.connection().execute(stmt).fetchall()
    own, deleted = {}, {}
    for r in rows:
        own[r[0]] = is_bound_row(SimpleNamespace(
            tmdb_id=r[3], imdb_id=r[4], external_ids=r[5]))
        deleted[r[0]] = r[6] is not None
    out = {}
    for r in rows:
        item_id, item_type, series_id = r[0], r[1], r[2]
        o = own[item_id]
        eff = bool(
            series_id is not None
            and own.get(series_id, False)
            and not deleted.get(series_id, True)
        )
        out[item_id] = (o, _resolved_verdict(item_type, o, eff))
    return out


def resync_children_of_series(db, series_ids) -> int:
    """按 series 当前状态重算其 season/episode 的 has_identity（Core）。

    soft_delete 的批量软删/复活直改 series.deleted_at、不走 ORM 事件，
    调用方改完必须调本函数。返回 UPDATE 命中的行数。调用方负责 commit。
    """
    ids = [int(i) for i in (series_ids or []) if i]
    if not ids:
        return 0
    effective = _fetch_series_effective(db, ids)
    # 同会话备忘同步刷新：本会话后续 flush 解析这些剧时不许读到旧判据
    if isinstance(getattr(db, "info", None), dict):
        db.info.setdefault("_identity_series_memo", {}).update(effective)
    t = em.MediaItem.__table__
    conn = db.connection()
    updated = 0
    true_ids = [sid for sid, eff in effective.items() if eff]
    false_ids = [sid for sid, eff in effective.items() if not eff]
    if true_ids:
        r = conn.execute(
            t.update()
            .where(t.c.series_id.in_(true_ids),
                   t.c.item_type.in_(_CHILD_TYPES))
            .values(has_identity=True)
        )
        updated += r.rowcount or 0
    if false_ids:
        r = conn.execute(
            t.update()
            .where(t.c.series_id.in_(false_ids),
                   t.c.item_type.in_(_CHILD_TYPES))
            .values(has_identity=t.c.identity_bound)
        )
        updated += r.rowcount or 0
    return updated


def resync_all_children(db) -> int:
    """全量重算所有子行的 has_identity（restore_all 这类整表复活用）。"""
    t = em.MediaItem.__table__
    ids = [r[0] for r in db.connection().execute(
        select(t.c.id).where(t.c.item_type == "series")).fetchall()]
    total = 0
    for i in range(0, len(ids), 1000):
        total += resync_children_of_series(db, ids[i:i + 1000])
    return total


def _history_changed(state, name) -> bool:
    attr = state.attrs.get(name)
    return attr is not None and attr.history.has_changes()


def _flags_before_flush(session) -> None:
    new_items = [o for o in session.new if isinstance(o, em.MediaItem)]
    new_obj_ids = {id(o) for o in new_items}
    dirty_items = []
    for obj in session.dirty:
        if not isinstance(obj, em.MediaItem) or id(obj) in new_obj_ids:
            continue
        state = sa_inspect(obj)
        # 属性历史必须在 flush 前读：flush 后 history 被清掉，分不出
        # 改没改（与 facets._collect_facet_targets 同理）。
        if any(_history_changed(state, n) for n in _WATCHED_ATTRS):
            dirty_items.append(obj)
    targets = new_items + dirty_items
    if not targets:
        return

    # 本 flush 内 series 对象的待定状态（子行解析优先于查库：同 flush
    # 里剧先改了身份、集随后落库是刮削补绑的常见形态）。
    pending_series = {}
    for obj in targets:
        if obj.item_type == "series" and obj.id is not None:
            pending_series[obj.id] = (is_bound_row(obj), obj.deleted_at)

    # series 有效判据翻转检测（只看存量 dirty 的 series）：旧值查库
    # （before_flush 时库里还是改前值）。不许从属性 history 推旧值——
    # 实例过期后改属性时 history.deleted 为空（旧值根本没加载进来），
    # 推出来的是新值、翻转永远测不到（2026-11-17 本批实测踩中）。
    flip_candidates = [
        obj for obj in dirty_items
        if obj.item_type == "series" and obj.id is not None
        and any(_history_changed(sa_inspect(obj), n) for n in _FLIP_ATTRS)
    ]
    flips = {}
    if flip_candidates:
        old_rows = _fetch_series_rows(
            session, [obj.id for obj in flip_candidates])
        for obj in flip_candidates:
            row = old_rows.get(obj.id)
            if row is None:
                continue
            new_own, new_deleted = pending_series[obj.id]
            new_eff = _series_effective(new_own, new_deleted)
            if _row_effective(row) != new_eff:
                flips[obj.id] = new_eff

    # 子行解析所属 series 的判据：本 flush 待定值 → 本会话备忘（此前
    # 任一路径算过的 series 判据；本会话内对该剧的写都经 targets /
    # resync 刷新备忘，与 identity_map 的会话内一致性同口径）→ 本会话
    # 内存里的 series 对象（身份字段未过期才读，零查询）→ 批量查库一次。
    # 扫描入库时剧与集在同一会话里逐批落库（含跨 commit 边界），走
    # 内存/备忘解析，不给扫描链路加查询——扫描资源预算冒烟对
    # emby_items 的 SELECT 次数有硬上限，本钩子不许成为新的查询源
    # （2026-11-17 CI 实测：只走查库解析时 SELECT 12→26 顶破预算）。
    memo = session.info.setdefault("_identity_series_memo", {})
    for sid, (own, deleted) in pending_series.items():
        memo[sid] = _series_effective(own, deleted)
    known_series = {}
    if any(obj.item_type in _CHILD_TYPES for obj in targets):
        for o in list(session.identity_map.values()):
            if (isinstance(o, em.MediaItem) and o.item_type == "series"
                    and o.id is not None and o.id not in pending_series
                    and o.id not in memo):
                state = sa_inspect(o)
                if state.expired_attributes.isdisjoint(
                        ("tmdb_id", "imdb_id", "external_ids", "deleted_at")):
                    bound = o.identity_bound
                    if bound is None:
                        bound = is_bound_row(o)
                    known_series[o.id] = _series_effective(bound, o.deleted_at)
    need_db = {
        obj.series_id for obj in targets
        if obj.item_type in _CHILD_TYPES
        and obj.series_id is not None
        and obj.series_id not in pending_series
        and obj.series_id not in memo
        and obj.series_id not in known_series
    }
    db_series = _fetch_series_effective(session, need_db) if need_db else {}
    memo.update(known_series)
    memo.update(db_series)

    def series_effective_for(obj):
        sid = obj.series_id
        if sid is not None:
            if sid in pending_series:
                own, deleted = pending_series[sid]
                return _series_effective(own, deleted)
            if sid in memo:
                return memo[sid]
            return db_series.get(sid, False)
        # series_id 还没落值（同 flush 经 relationship 关联）：读内存关系
        rel = getattr(obj, "series", None)
        if rel is not None:
            return _series_effective(is_bound_row(rel),
                                     getattr(rel, "deleted_at", None))
        return False

    unresolved = []
    for obj in targets:
        own = is_bound_row(obj)
        obj.identity_bound = own
        if obj.item_type in _CHILD_TYPES:
            if obj.series_id is None and getattr(obj, "series", None) is None \
                    and obj in new_items:
                # 关联可能在 flush 过程中才由 UOW 落定，after_flush 再核
                unresolved.append(obj)
            obj.has_identity = _resolved_verdict(
                obj.item_type, own, series_effective_for(obj))
        else:
            obj.has_identity = _resolved_verdict(obj.item_type, own, False)

    if flips:
        session.info["_identity_flips"] = flips
    if unresolved:
        session.info["_identity_unresolved_children"] = unresolved


def _flags_after_flush(session) -> None:
    flips = session.info.pop("_identity_flips", None)
    unresolved = session.info.pop("_identity_unresolved_children", None)
    if not flips and not unresolved:
        return
    conn = session.connection()
    t = em.MediaItem.__table__
    if flips:
        for sid, effective in flips.items():
            if effective:
                stmt = (t.update()
                        .where(t.c.series_id == sid,
                               t.c.item_type.in_(_CHILD_TYPES))
                        .values(has_identity=True))
            else:
                stmt = (t.update()
                        .where(t.c.series_id == sid,
                               t.c.item_type.in_(_CHILD_TYPES))
                        .values(has_identity=t.c.identity_bound))
            conn.execute(stmt)
        # 本会话里已加载的子行对象标记过期，下次读回库里的新值，
        # 不让内存里的旧标记冒充已同步。
        for obj in list(session.identity_map.values()):
            if (isinstance(obj, em.MediaItem)
                    and obj.series_id in flips
                    and obj.item_type in _CHILD_TYPES):
                session.expire(obj, ["has_identity"])
    if unresolved:
        need = {o.series_id for o in unresolved if o.series_id is not None}
        db_series = _fetch_series_effective(session, need) if need else {}
        for obj in unresolved:
            if obj.id is None or obj.series_id is None:
                continue
            resolved = _resolved_verdict(
                obj.item_type, bool(obj.identity_bound),
                db_series.get(obj.series_id, False))
            if bool(obj.has_identity) != resolved:
                conn.execute(
                    t.update().where(t.c.id == obj.id)
                    .values(has_identity=resolved))
                session.expire(obj, ["has_identity"])


@event.listens_for(Session, "before_flush")
def _identity_flags_before_flush(session, flush_context, instances):
    try:
        _flags_before_flush(session)
    except Exception:  # noqa: BLE001 — 标记同步失败不许拖死写路径，日志留痕、回填脚本兜底
        logger.exception("身份标记同步（before_flush）失败，靠回填脚本兜底")


@event.listens_for(Session, "after_flush")
def _identity_flags_after_flush(session, flush_context):
    try:
        _flags_after_flush(session)
    except Exception:  # noqa: BLE001 — 同上
        logger.exception("身份标记同步（after_flush）失败，靠回填脚本兜底")
