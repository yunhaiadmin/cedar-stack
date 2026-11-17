"""DateLastContentAdded 物化列维护（BE-26）。

``SortBy=DateLastContentAdded``（Rex 等第三方客户端的「最新更新」排序）
旧实现是对每行求值的相关标量子查询：series 取其下所有未软删单集
``date_added`` 的最大值、其余类型取自身 ``date_added``。大库按此排序
时成本随行数线性放大且难走索引。现把该值物化到
``emby_items.last_content_added_at``：

- series 行：其下未软删单集 date_added 最大值，无集时 = 自身 date_added；
- 其余类型：= 自身 date_added。

维护方式与 facets 同款：Session flush 钩子在同一事务里增量重算受影响
的 series（新集入库 / 集改属 / 集软删恢复 / 剧自身入库时间变化）；
批量 Core 写入绕过 ORM 事件的行保持 NULL，由 :func:`ensure_backfill`
在启动维护时补齐。排序侧读物化列并 ``coalesce`` 回退旧标量子查询，
NULL 行（回填未及）结果与旧实现逐字一致——物化只是把热路径从
「逐行子查询」换成「索引列」，语义零变化。

本模块导入即注册钩子（与 facets 同口径）：api.py 与 backend/main.py
都显式导入，任何进程只要能写条目就带着钩子。
"""
from __future__ import annotations

import logging

from sqlalchemy import event, func, inspect as sa_inspect, select, update
from sqlalchemy.orm import Session

from backend.emby_server import models as em

logger = logging.getLogger(__name__)

#: 存量回填单批条数（启动维护调用；有界，不许一口气全表）
BACKFILL_BATCH = 2000

_ITEMS = em.MediaItem.__table__
_CHILD = _ITEMS.alias("lca_child")


def _recompute_series_conn(conn, series_id: int) -> None:
    """在给定连接上把一个 series 行的物化值重算为当前真值（同事务）。"""
    max_child = (
        select(func.max(_CHILD.c.date_added))
        .where(_CHILD.c.series_id == _ITEMS.c.id)
        .where(_CHILD.c.deleted_at.is_(None))
        .scalar_subquery()
    )
    value = conn.execute(
        select(func.coalesce(max_child, _ITEMS.c.date_added))
        .where(_ITEMS.c.id == series_id)
    ).scalar()
    conn.execute(
        update(_ITEMS)
        .where(_ITEMS.c.id == series_id)
        .values(last_content_added_at=value)
    )


def recompute_series(db: Session, series_id: int) -> None:
    """重算一个 series 行的物化值（不提交，跟随调用方事务）。"""
    _recompute_series_conn(db.connection(), series_id)


def ensure_backfill(db: Session, batch_size: int = BACKFILL_BATCH,
                    commit: bool = True) -> int:
    """给物化列仍为 NULL 的存量行补齐（按 id 增量，内存有界）。

    非 series 行一条 Core UPDATE 整批写（值 = 自身 date_added）；series
    行逐条重算（批大小有界）。返回本次处理的行数；date_added 本身为
    NULL 的行无法定值，跳过且不计（防无进展死循环）。
    """
    total = 0
    last_first_id = None
    while True:
        rows = (
            db.query(em.MediaItem.id, em.MediaItem.item_type)
            .filter(em.MediaItem.last_content_added_at.is_(None),
                    em.MediaItem.date_added.isnot(None))
            .order_by(em.MediaItem.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            break
        if rows[0][0] == last_first_id:
            break  # 无进展（理论不可达，防死循环兜底）
        last_first_id = rows[0][0]
        plain_ids = [r[0] for r in rows if r[1] != "series"]
        if plain_ids:
            db.execute(
                update(_ITEMS)
                .where(_ITEMS.c.id.in_(plain_ids))
                .values(last_content_added_at=_ITEMS.c.date_added)
            )
        conn = db.connection()
        for r in rows:
            if r[1] == "series":
                _recompute_series_conn(conn, r[0])
        total += len(rows)
        if commit:
            db.commit()
        if len(rows) < batch_size:
            break
    if total:
        logger.info("DateLastContentAdded 物化列回填 %d 行", total)
    return total


# ==================== ORM 事件：写入路径自动维护 ====================
#
# 与 facets 同款纪律：before_flush 挑出受影响对象（flush 后属性历史
# 会被清掉），after_flush 在同一事务里用连接层语句重算。物化列是派生
# 数据，维护失败只记日志、不许拖垮业务写入（排序侧有 coalesce 回退
# 兜底，漏算的行回填会补上）。

_EPISODE_WATCHED_ATTRS = ("series_id", "date_added", "deleted_at")


def _collect_targets(session) -> tuple[list, set]:
    """返回（新 series 对象列表, 待重算的 series id 集合）。"""
    new_series: list = []
    series_ids: set = set()
    for obj in session.new:
        if not isinstance(obj, em.MediaItem):
            continue
        # 新行自身物化值先按自身 date_added 落（series 随后按集重算）
        if obj.last_content_added_at is None and obj.date_added is not None:
            obj.last_content_added_at = obj.date_added
        if obj.item_type == "series":
            new_series.append(obj)
        elif obj.item_type == "episode" and obj.series_id:
            series_ids.add(obj.series_id)
    new_obj_ids = {id(o) for o in session.new}
    for obj in session.dirty:
        if not isinstance(obj, em.MediaItem) or id(obj) in new_obj_ids:
            continue
        state = sa_inspect(obj)
        if obj.item_type == "episode":
            touched = False
            for name in _EPISODE_WATCHED_ATTRS:
                attr = state.attrs.get(name)
                if attr is not None and attr.history.has_changes():
                    touched = True
                    if name == "series_id":
                        # 改属前的旧剧也要重算（它的最大值可能回落）
                        for old in (attr.history.deleted or ()):
                            if old:
                                series_ids.add(old)
            if touched and obj.series_id:
                series_ids.add(obj.series_id)
        elif obj.item_type == "series":
            attr = state.attrs.get("date_added")
            if attr is not None and attr.history.has_changes():
                series_ids.add(obj.id)
        else:
            # 其余类型物化值 = 自身 date_added，跟随其变化
            attr = state.attrs.get("date_added")
            if attr is not None and attr.history.has_changes():
                obj.last_content_added_at = obj.date_added
    for obj in session.deleted:
        if isinstance(obj, em.MediaItem) and obj.item_type == "episode" \
                and obj.series_id:
            series_ids.add(obj.series_id)
    return new_series, series_ids


@event.listens_for(Session, "before_flush")
def _lca_before_flush(session, flush_context, instances):  # noqa: ARG001
    new_series, series_ids = _collect_targets(session)
    if new_series or series_ids:
        session.info["_lca_new_series"] = new_series
        session.info["_lca_series_ids"] = series_ids


@event.listens_for(Session, "after_flush")
def _lca_after_flush(session, flush_context):  # noqa: ARG001
    new_series = session.info.pop("_lca_new_series", None)
    series_ids = session.info.pop("_lca_series_ids", None)
    targets = set(series_ids or ())
    for obj in new_series or ():
        if obj.id is not None:
            targets.add(obj.id)
    if not targets:
        return
    try:
        conn = session.connection()
        for sid in targets:
            _recompute_series_conn(conn, sid)
    except Exception:  # noqa: BLE001 — 派生数据写失败不该拖垮业务写入
        logger.exception("维护 DateLastContentAdded 物化列失败（回填会补齐）")
