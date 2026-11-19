# -*- coding: utf-8 -*-
"""管理后台 · 人物条目管理（后台大改造二期部分缺，2026-11-19）。

    GET  /api/admin/people                 人物列表（搜索 / 分页）
    POST /api/admin/people/rename          改名（只改显示名）
    GET  /api/admin/people/merge-preview   合并预览（将迁移 / 将去重的关联数）
    POST /api/admin/people/merge           合并（源人物关联改指目标、源人物消灭）

现状（已核实）：人物没有实体表。``emby_people``（EmbyPerson）是按条目落的
credits 行（item_id / name / role / image / person_tmdb_id），「人物」是
聚合身份：门户规范键（portal.person_key_of）——有数字 TMDB person id 时
``tmdb:<id>``，否则 ``name:<归一化姓名>``（portal.normalize_person_name：
strip + 折叠空白 + casefold）。用户端人物页（portal.get_person_detail）
的 name / image / tmdb_id / works 全部从 credits 行聚合；EA
``/emby/Persons`` 按 name 分组。管理端此前没有人物口（EmbyAdmin 里只有
facet 类型标签「演员」）。

管理口径（按最合理管理员行为推导）：
- 分组行定义（列表计数与改名 / 合并作用域逐行对账的唯一口径）：
  tmdb 组 = person_tmdb_id 等于该 id 的全部行；name 组 = 归一化姓名相等
  且 person_tmdb_id 不是数字 id 的全部行。两组不交、并集为全部 credits
  行。与 portal._person_credit_rows 的差异：门户按姓名键反查时跨组取行
  （候选键口径，用户侧不受本口影响），管理口必须分组不交，否则计数与
  作用域对不上账。
- 改名只改显示名：本组全部 credits 行的 name 改为新名，不碰
  person_tmdb_id / role / image 等任何字段，不动刮削识别逻辑。新名与
  另一人物归一化同名时拒绝（409），改名不是合并——要并人走合并。
- 合并：源组逐行处置——条目已在目标组有行的，源行删除（目标行优先，
  仅当目标行缺 image / role 时从源行补入）；其余源行改指目标（name 改
  目标名，目标有 TMDB id 时 person_tmdb_id 一并改指）。目标无 TMDB id
  而源有时，源行保留自己的 TMDB id 不销毁绑定，合并后规范键随之为
  源的 tmdb 键。源组行全部处置完，源人物即不存在（emby_people 无
  deleted_at 列，全局软删钩子只覆盖 MediaItem，credits 行不在软删
  体系内——以关联全迁 + AdminLog 留痕代替软删）。
- 人员收藏（emby_user_person_favorites）按人员键落行：改名 / 合并后
  指向旧键的收藏同步改指新规范键（同用户已收藏新键时删旧行，避免
  唯一约束冲突），快照 name 随之更新（合并时 image / person_tmdb_id
  快照一并改指目标），否则用户收藏的人物详情会 404。
- 改名记 AdminLog（person_rename）、合并记 AdminLog（person_merge），
  details 带前后键 / 名与迁移、去重、收藏改指的对账计数。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend import models
from backend.api.admin_core import _audit, get_current_admin
from backend.api.pagination import pagination_params
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server.portal import normalize_person_name, person_key_of

logger = logging.getLogger(__name__)

admin_people_router = APIRouter(
    prefix="/api/admin/people", tags=["管理后台·人物条目管理"])


def _is_digit_id(value) -> bool:
    return bool(value) and str(value).isdigit()


def _parse_key(key: str) -> tuple[str, str]:
    """人员键 → (kind, value)；与 portal._parse_person_key 同规则。

    tmdb 键的 value 是数字 id 字符串；name 键的 value 是归一化姓名。
    """
    text = (key or "").strip()
    if text.startswith("tmdb:") and text[5:].isdigit():
        return "tmdb", text[5:]
    if text.startswith("name:"):
        norm = normalize_person_name(text[5:])
        if norm:
            return "name", norm
    raise HTTPException(status_code=400, detail="人员键格式不对")


def _rows_for_group(db: Session, kind: str, value: str) -> list:
    """分组行（模块 docstring 的唯一口径）。

    name 组先用 SQL 粗筛（去全部空格后小写相等，与 portal 同法），
    再在 Python 侧按归一化口径精确核对，并排除带数字 TMDB id 的行
    （那些行属于 tmdb 组）。
    """
    if kind == "tmdb":
        return (db.query(em.EmbyPerson)
                .filter(em.EmbyPerson.person_tmdb_id == value)
                .order_by(em.EmbyPerson.id)
                .all())
    squashed = func.replace(func.lower(func.trim(em.EmbyPerson.name)), " ", "")
    rows = (db.query(em.EmbyPerson)
            .filter(squashed == value.replace(" ", ""))
            .order_by(em.EmbyPerson.id)
            .all())
    return [r for r in rows
            if normalize_person_name(r.name) == value
            and not _is_digit_id(r.person_tmdb_id)]


def _rows_by_normalized_name(db: Session, normalized: str) -> list:
    """按归一化姓名取全部 credits 行（跨组，改名撞名检查用）。"""
    squashed = func.replace(func.lower(func.trim(em.EmbyPerson.name)), " ", "")
    rows = (db.query(em.EmbyPerson)
            .filter(squashed == normalized.replace(" ", ""))
            .order_by(em.EmbyPerson.id)
            .all())
    return [r for r in rows if normalize_person_name(r.name) == normalized]


def _aggregate(rows: list) -> dict:
    """credits 行 → 人物聚合（name / image 口径与 portal._resolve_person 一致：
    优先取有图行的 name 与 image，TMDB id 取行内第一个数字 id）。"""
    with_image = next((r for r in rows if r.image), None)
    name = (with_image or rows[0]).name
    tmdb_id = next((str(r.person_tmdb_id) for r in rows
                    if _is_digit_id(r.person_tmdb_id)), None)
    return {
        "key": person_key_of(tmdb_id, name),
        "name": name,
        "image": with_image.image if with_image else None,
        "tmdb_id": tmdb_id,
        "credits_count": len(rows),
        "works_count": len({r.item_id for r in rows}),
    }


def _candidate_keys(name: str, tmdb_id: Optional[str]) -> set:
    """收藏改指的旧键集合：规范键 + 姓名键 + tmdb 键（与 portal 候选键同口径）。"""
    keys = {person_key_of(tmdb_id, name), f"name:{normalize_person_name(name)}"}
    if tmdb_id:
        keys.add(f"tmdb:{tmdb_id}")
    return keys


_KEEP = object()


def _migrate_favorites(db: Session, old_keys: set, new_key: str, *,
                       name=_KEEP, image=_KEEP, tmdb_id=_KEEP) -> int:
    """把指向 old_keys 的人员收藏改指 new_key，返回处置行数。

    同用户已收藏新键时删旧行（uq_upersonfav_user_key 唯一约束），
    否则原地改键并按需更新快照字段（_KEEP = 该字段不动）。
    """
    favs = (db.query(em.UserPersonFavorite)
            .filter(em.UserPersonFavorite.person_key.in_(old_keys))
            .all())
    handled = 0
    for fav in favs:
        if fav.person_key == new_key:
            continue
        clash = (db.query(em.UserPersonFavorite)
                 .filter(em.UserPersonFavorite.user_id == fav.user_id,
                         em.UserPersonFavorite.person_key == new_key)
                 .first())
        if clash is not None:
            db.delete(fav)
        else:
            fav.person_key = new_key
            if name is not _KEEP:
                fav.name = name
            if image is not _KEEP:
                fav.image = image
            if tmdb_id is not _KEEP:
                fav.person_tmdb_id = tmdb_id
        handled += 1
    return handled


def _fold_group(groups: dict, key: str, *, name, image, tmdb_id,
                credits: int, works: int) -> None:
    """聚合行折进人物组（同键多行只出现在异常数据折叠时，计数相加）。"""
    group = groups.get(key)
    if group is None:
        groups[key] = {
            "key": key, "name": name, "image": image, "tmdb_id": tmdb_id,
            "credits_count": credits, "works_count": works,
        }
        return
    group["credits_count"] += credits
    group["works_count"] += works
    if not group["image"] and image:
        group["image"] = image


@admin_people_router.get("")
def list_people(q: str = "", limit: int = 50, offset: int = 0,
                current_admin: models.WebUser = Depends(get_current_admin),
                db: Session = Depends(get_db)):
    """人物列表：按规范键聚合 credits 行，works 多者靠前。

    搜索：姓名子串（ILIKE），或数字串精确匹配 TMDB id。
    聚合先在 SQL 侧按（TMDB id / 去空格小写姓名）粗分组，再在 Python
    侧折成规范键——与门户键口径一致；姓名组代表名取组内最小姓名
    （同组姓名只差空白 / 大小写，属 portal 粗筛同等口径）。
    """
    limit, offset = pagination_params(limit, offset)
    q = (q or "").strip()
    groups: dict[str, dict] = {}

    tmdb_q = (db.query(
        em.EmbyPerson.person_tmdb_id.label("tid"),
        func.count(em.EmbyPerson.id).label("credits"),
        func.count(func.distinct(em.EmbyPerson.item_id)).label("works"),
        func.min(em.EmbyPerson.name).label("name"),
        func.max(em.EmbyPerson.image).label("image"),
    ).filter(em.EmbyPerson.person_tmdb_id.isnot(None),
             em.EmbyPerson.person_tmdb_id != ""))
    if q:
        tmdb_q = tmdb_q.filter(or_(
            em.EmbyPerson.name.ilike(f"%{q}%"),
            em.EmbyPerson.person_tmdb_id == q))
    for row in tmdb_q.group_by(em.EmbyPerson.person_tmdb_id).all():
        if _is_digit_id(row.tid):
            _fold_group(groups, f"tmdb:{row.tid}", name=row.name,
                        image=row.image, tmdb_id=str(row.tid),
                        credits=row.credits, works=row.works)
        else:
            _fold_group(groups, f"name:{normalize_person_name(row.name)}",
                        name=row.name, image=row.image, tmdb_id=None,
                        credits=row.credits, works=row.works)

    squashed = func.replace(func.lower(func.trim(em.EmbyPerson.name)), " ", "")
    name_q = (db.query(
        squashed.label("sq"),
        func.count(em.EmbyPerson.id).label("credits"),
        func.count(func.distinct(em.EmbyPerson.item_id)).label("works"),
        func.min(em.EmbyPerson.name).label("name"),
        func.max(em.EmbyPerson.image).label("image"),
    ).filter(or_(em.EmbyPerson.person_tmdb_id.is_(None),
                 em.EmbyPerson.person_tmdb_id == "")))
    if q:
        name_q = name_q.filter(em.EmbyPerson.name.ilike(f"%{q}%"))
    for row in name_q.group_by(squashed).all():
        _fold_group(groups, f"name:{normalize_person_name(row.name)}",
                    name=row.name, image=row.image, tmdb_id=None,
                    credits=row.credits, works=row.works)

    items = sorted(groups.values(),
                   key=lambda g: (-g["works_count"], g["name"], g["key"]))
    return {"total": len(items), "items": items[offset:offset + limit]}


class PersonRenameRequest(BaseModel):
    key: str
    new_name: str


@admin_people_router.post("/rename")
def rename_person(req: PersonRenameRequest,
                  current_admin: models.WebUser = Depends(get_current_admin),
                  db: Session = Depends(get_db)):
    """改名：只改本组全部 credits 行的显示名，其余字段一律不动。

    新名与本组同名（归一化后）→ 幂等返回不落日志；与另一人物同名 →
    409（改名不并人，并人走合并）。指向旧键的人员收藏同步改指新键。
    """
    kind, value = _parse_key(req.key)
    rows = _rows_for_group(db, kind, value)
    if not rows:
        raise HTTPException(status_code=404, detail="人物不存在")
    new_name = " ".join((req.new_name or "").split())
    if not new_name:
        raise HTTPException(status_code=400, detail="新名不能为空")
    if len(new_name) > 200:
        raise HTTPException(status_code=400, detail="新名过长（上限 200 字）")

    agg = _aggregate(rows)
    if normalize_person_name(new_name) == normalize_person_name(agg["name"]):
        return {"success": True, "renamed": False, **agg}

    group_ids = {r.id for r in rows}
    clash = _rows_by_normalized_name(db, normalize_person_name(new_name))
    if any(r.id not in group_ids for r in clash):
        raise HTTPException(
            status_code=409,
            detail="已存在同名人物，改名不能并人；如需合并请用合并功能")

    old_keys = _candidate_keys(agg["name"], agg["tmdb_id"])
    for row in rows:
        row.name = new_name
    db.flush()
    new_key = person_key_of(agg["tmdb_id"], new_name)
    favorites_migrated = _migrate_favorites(
        db, old_keys, new_key, name=new_name)
    _audit(db, current_admin, "person_rename", "person", None, {
        "old_key": agg["key"], "new_key": new_key,
        "old_name": agg["name"], "new_name": new_name,
        "credits_count": len(rows),
        "favorites_migrated": favorites_migrated,
    })
    db.commit()
    return {
        "success": True, "renamed": True,
        "key": new_key, "name": new_name,
        "image": agg["image"], "tmdb_id": agg["tmdb_id"],
        "credits_count": len(rows), "works_count": agg["works_count"],
        "favorites_migrated": favorites_migrated,
    }


def _merge_plan(db: Session, source_key: str, target_key: str) -> dict:
    """合并预案：解析源 / 目标分组行并算出逐行处置（预览与执行共用同一口径）。"""
    s_kind, s_value = _parse_key(source_key)
    t_kind, t_value = _parse_key(target_key)
    s_rows = _rows_for_group(db, s_kind, s_value)
    if not s_rows:
        raise HTTPException(status_code=404, detail="源人物不存在")
    t_rows = _rows_for_group(db, t_kind, t_value)
    if not t_rows:
        raise HTTPException(status_code=404, detail="目标人物不存在")
    s_agg, t_agg = _aggregate(s_rows), _aggregate(t_rows)
    if s_agg["key"] == t_agg["key"]:
        raise HTTPException(status_code=400, detail="不能把人物合并到自己")

    t_items = {r.item_id for r in t_rows}
    s_items = {r.item_id for r in s_rows}
    migrate = [r for r in s_rows if r.item_id not in t_items]
    drop = [r for r in s_rows if r.item_id in t_items]
    s_keys = _candidate_keys(s_agg["name"], s_agg["tmdb_id"])
    favorites = (db.query(em.UserPersonFavorite)
                 .filter(em.UserPersonFavorite.person_key.in_(s_keys),
                         em.UserPersonFavorite.person_key != t_agg["key"])
                 .count())
    merged_tmdb = t_agg["tmdb_id"] or s_agg["tmdb_id"]
    return {
        "source": s_agg, "target": t_agg,
        "s_rows": s_rows, "t_rows": t_rows,
        "migrate": migrate, "drop": drop,
        "source_keys": s_keys,
        "favorites_to_migrate": favorites,
        "merged_key": person_key_of(merged_tmdb, t_agg["name"]),
        "merged_tmdb": merged_tmdb,
        "result_credits": len(t_rows) + len(migrate),
        "result_works": len(t_items | s_items),
    }


@admin_people_router.get("/merge-preview")
def merge_preview(source_key: str, target_key: str,
                  current_admin: models.WebUser = Depends(get_current_admin),
                  db: Session = Depends(get_db)):
    """合并预览（只读）：将迁移 / 将去重的关联数与合并后对账数，供确认弹窗展示。"""
    plan = _merge_plan(db, source_key, target_key)
    return {
        "source": plan["source"], "target": plan["target"],
        "will_migrate": len(plan["migrate"]),
        "will_drop_duplicates": len(plan["drop"]),
        "favorites_to_migrate": plan["favorites_to_migrate"],
        "result": {
            "key": plan["merged_key"],
            "credits_count": plan["result_credits"],
            "works_count": plan["result_works"],
        },
    }


class PersonMergeRequest(BaseModel):
    source_key: str
    target_key: str


@admin_people_router.post("/merge")
def merge_person(req: PersonMergeRequest,
                 current_admin: models.WebUser = Depends(get_current_admin),
                 db: Session = Depends(get_db)):
    """合并：单事务内逐行处置源组 credits 行 + 收藏改指 + AdminLog，末尾对账返回。

    对账口径：合并后关联数 = 目标原关联数 + 迁移数；作品数 = 两组条目并集。
    """
    plan = _merge_plan(db, req.source_key, req.target_key)
    s_agg, t_agg = plan["source"], plan["target"]
    t_by_item = {r.item_id: r for r in plan["t_rows"]}

    migrated = dropped = 0
    for row in plan["s_rows"]:
        target_row = t_by_item.get(row.item_id)
        if target_row is not None:
            if not target_row.image and row.image:
                target_row.image = row.image
            if not target_row.role and row.role:
                target_row.role = row.role
            db.delete(row)
            dropped += 1
        else:
            row.name = t_agg["name"]
            if t_agg["tmdb_id"]:
                row.person_tmdb_id = t_agg["tmdb_id"]
            migrated += 1
    db.flush()

    favorites_migrated = _migrate_favorites(
        db, plan["source_keys"], t_agg["key"],
        name=t_agg["name"],
        image=t_agg["image"] or plan["source"]["image"],
        tmdb_id=plan["merged_tmdb"])
    _audit(db, current_admin, "person_merge", "person", None, {
        "source_key": s_agg["key"], "target_key": t_agg["key"],
        "merged_key": plan["merged_key"],
        "source_name": s_agg["name"], "target_name": t_agg["name"],
        "migrated": migrated, "duplicates_dropped": dropped,
        "favorites_migrated": favorites_migrated,
        "target_credits_before": len(plan["t_rows"]),
        "target_credits_after": plan["result_credits"],
        "target_works_after": plan["result_works"],
    })
    db.commit()
    return {
        "success": True,
        "key": plan["merged_key"], "name": t_agg["name"],
        "image": t_agg["image"] or plan["source"]["image"],
        "tmdb_id": plan["merged_tmdb"],
        "migrated": migrated, "duplicates_dropped": dropped,
        "favorites_migrated": favorites_migrated,
        "credits_count": plan["result_credits"],
        "works_count": plan["result_works"],
    }
