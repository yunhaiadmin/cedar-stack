# -*- coding: utf-8 -*-
"""推广者档案（AFF 后台管理，2026-11-20）：专属费率 / 停用 / 备注 + 汇总聚合。

档案表 ``invite_promoter_profiles`` 按 user_id 一人一行，只承载三件
事：返利比例覆盖、停用标记、管理员备注。没有档案行 ＝ 一切走全局
口径（``invite_rebate`` 的配置键），存量用户行为不变。

汇总聚合的口径与邀请总览（admin_invitation.invite_overview）同源：

- 邀请人数：``invitation_records`` 按 inviter 计数；
- 付费人数：与总览同一条 join——被邀请人名下有 status=paid 的订阅
  订单即算（按被邀请人去重）；
- 累计付费 / 已入账 / 待入账：锚定计提台账 ``invite_rebate_accruals``，
  状态集与 ``invite_rebate._invitee_prior_paid`` / ``_accrued_total``
  一致（pending+credited 计入，reversed 不计——退款撤回后等于这笔
  付费没发生过）。注意这意味着未产生计提行的付费（例如全局比例
  被配成 0 的时期）不计入累计付费，与分档累计的既有口径一致。

金额一律美元 ``Numeric(12,2)``，出参沿用总览格式（两位小数字符串）。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import invite_rebate, models
from backend.agents import money

#: 汇总可排序字段（前端排序下拉与导出共用）
SORT_FIELDS = ("total_paid", "rebate_total", "invited_count", "paid_invitees")

_ACTIVE_STATUSES = (invite_rebate.STATUS_PENDING, invite_rebate.STATUS_CREDITED)


def get_profile(db: Session, user_id: int) -> models.InvitePromoterProfile | None:
    return (
        db.query(models.InvitePromoterProfile)
        .filter(models.InvitePromoterProfile.user_id == user_id)
        .first()
    )


def is_disabled(db: Session, user_id: int) -> bool:
    profile = get_profile(db, user_id)
    return bool(profile is not None and profile.is_disabled)


def percent_override(db: Session, user_id: int) -> int | None:
    """专属返利比例覆盖（%）：无档案/未设返回 None；越界值钳到 0..100。"""
    profile = get_profile(db, user_id)
    if profile is None or profile.rebate_percent_override is None:
        return None
    return max(0, min(100, int(profile.rebate_percent_override)))


# ==================== 汇总聚合 ====================

def _aggregate_maps(db: Session) -> dict[int, dict]:
    """按 inviter 归集四组数：邀请数 / 付费人数 / 计提台账三笔钱。"""
    stats: dict[int, dict] = {}

    def slot(inviter_id: int) -> dict:
        return stats.setdefault(inviter_id, {
            "invited_count": 0, "paid_invitees": 0,
            "total_paid": Decimal("0"), "rebate_credited": Decimal("0"),
            "rebate_pending": Decimal("0"),
        })

    for inviter_id, cnt in (
        db.query(models.InvitationRecord.inviter_id,
                 func.count(models.InvitationRecord.id))
        .group_by(models.InvitationRecord.inviter_id).all()
    ):
        slot(inviter_id)["invited_count"] = int(cnt or 0)

    # 付费人数：与总览 invite_overview 同一条 join，按被邀请人去重
    for inviter_id, cnt in (
        db.query(models.InvitationRecord.inviter_id,
                 func.count(func.distinct(models.InvitationRecord.invitee_id)))
        .join(models.SubscriptionOrder,
              models.SubscriptionOrder.user_id == models.InvitationRecord.invitee_id)
        .filter(models.SubscriptionOrder.status == "paid")
        .group_by(models.InvitationRecord.inviter_id).all()
    ):
        slot(inviter_id)["paid_invitees"] = int(cnt or 0)

    for inviter_id, status, paid_sum, rebate_sum in (
        db.query(models.InviteRebateAccrual.inviter_id,
                 models.InviteRebateAccrual.status,
                 func.coalesce(func.sum(models.InviteRebateAccrual.paid_amount), 0),
                 func.coalesce(func.sum(models.InviteRebateAccrual.rebate_amount), 0))
        .filter(models.InviteRebateAccrual.status.in_(_ACTIVE_STATUSES))
        .group_by(models.InviteRebateAccrual.inviter_id,
                  models.InviteRebateAccrual.status).all()
    ):
        entry = slot(inviter_id)
        entry["total_paid"] += money(paid_sum or 0)
        if status == invite_rebate.STATUS_CREDITED:
            entry["rebate_credited"] += money(rebate_sum or 0)
        elif status == invite_rebate.STATUS_PENDING:
            entry["rebate_pending"] += money(rebate_sum or 0)
    return stats


def summary_rows(db: Session, *, search: str | None = None,
                 sort: str = "total_paid", order: str = "desc") -> list[dict]:
    """全部推广者汇总行（已按 search 过滤、已排序，未分页）。

    推广者集合 = 有邀请关系的 inviter ∪ 有档案行的用户（管理员可以
    先给一个还没拉过人的合作方建档设费率，他也应出现在列表里）。
    列表接口与 CSV 导出共用本函数，保证两边口径与顺序完全一致。
    """
    stats = _aggregate_maps(db)
    profiles = {
        p.user_id: p for p in db.query(models.InvitePromoterProfile).all()
    }
    user_ids = set(stats) | set(profiles)
    if not user_ids:
        return []
    users = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_(user_ids)).all()
    }
    global_percent = invite_rebate.rebate_percent(db)
    global_mode = invite_rebate.rebate_mode(db)

    rows: list[dict] = []
    needle = (search or "").strip().lower()
    for user_id in user_ids:
        username = users.get(user_id, "未知")
        if needle and needle not in username.lower():
            continue
        entry = stats.get(user_id) or {
            "invited_count": 0, "paid_invitees": 0,
            "total_paid": Decimal("0"), "rebate_credited": Decimal("0"),
            "rebate_pending": Decimal("0"),
        }
        profile = profiles.get(user_id)
        override = (
            None if profile is None or profile.rebate_percent_override is None
            else max(0, min(100, int(profile.rebate_percent_override)))
        )
        credited = money(entry["rebate_credited"])
        pending = money(entry["rebate_pending"])
        total_paid = money(entry["total_paid"])
        rows.append({
            "user_id": user_id,
            "username": username,
            "invited_count": int(entry["invited_count"]),
            "paid_invitees": int(entry["paid_invitees"]),
            "total_paid": f"{total_paid:.2f}",
            "rebate_credited": f"{credited:.2f}",
            "rebate_pending": f"{pending:.2f}",
            "rebate_total": f"{money(credited + pending):.2f}",
            # 当前生效费率：专属覆盖优先；否则全局基础比例（分档模式下
            # 这是第一档比例，展示口径与总览 rebate_config 一致）
            "rate_percent": override if override is not None else global_percent,
            "rate_source": "promoter" if override is not None else "global",
            "rate_mode": global_mode,
            "rebate_percent_override": override,
            "is_disabled": bool(profile is not None and profile.is_disabled),
            "note": (profile.note if profile is not None else "") or "",
            "updated_at": (profile.updated_at.isoformat()
                           if profile is not None and profile.updated_at else None),
            "_sort_paid": total_paid,
            "_sort_rebate": money(credited + pending),
        })

    sort_key = sort if sort in SORT_FIELDS else "total_paid"

    def keyfn(row: dict):
        if sort_key == "total_paid":
            return row["_sort_paid"]
        if sort_key == "rebate_total":
            return row["_sort_rebate"]
        return row[sort_key]

    # 次级排序固定 user_id 升序，翻页/导出顺序稳定
    rows.sort(key=lambda r: r["user_id"])
    rows.sort(key=keyfn, reverse=(order != "asc"))
    for row in rows:
        row.pop("_sort_paid", None)
        row.pop("_sort_rebate", None)
    return rows


def promoter_summaries(db: Session, *, search: str | None = None,
                       sort: str = "total_paid", order: str = "desc",
                       page: int | None = None,
                       page_size: int | None = None) -> tuple[int, list[dict]]:
    """推广者汇总（总数, 本页行）：page/page_size 都给才分页。"""
    rows = summary_rows(db, search=search, sort=sort, order=order)
    total = len(rows)
    if page is not None and page_size is not None:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        start = (page - 1) * page_size
        rows = rows[start:start + page_size]
    return total, rows


__all__ = [
    "SORT_FIELDS", "get_profile", "is_disabled", "percent_override",
    "promoter_summaries", "summary_rows",
]
