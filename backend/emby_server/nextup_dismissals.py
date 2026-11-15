"""「隐藏下一集」关闭记录的读写口（审计 ❌1）。

语义（推导方案）：点下一集卡的垃圾桶＝「这部剧的下一集别再在首页
推给我」，按（用户 × 剧）关闭、可恢复、只影响本人。关闭记录里存
关闭时那一集的 guid；查询侧只有当被关那集仍是当前下一集时才排除
该剧——用户真看了新集、下一集变了，关闭自动失效，卡片回来。

本模块只管记录本身的存取；「是否仍生效」的判定由调用方拿当前
下一集 guid 与 dismissed_episode_guid 对比完成。
"""

from __future__ import annotations

from datetime import datetime

from backend.emby_server import models as em


def dismiss(db, user_id: int, series_id: int, episode_guid: str):
    """记下关闭（幂等）：同（用户 × 剧）已有行时更新为最新那集。"""
    row = (
        db.query(em.UserNextUpDismissal)
        .filter(
            em.UserNextUpDismissal.user_id == user_id,
            em.UserNextUpDismissal.series_id == series_id,
        )
        .first()
    )
    if row is None:
        row = em.UserNextUpDismissal(
            user_id=user_id, series_id=series_id,
            dismissed_episode_guid=episode_guid,
        )
        db.add(row)
    else:
        row.dismissed_episode_guid = episode_guid
        row.created_at = datetime.now()
    db.commit()
    return row


def undismiss(db, user_id: int, series_id: int) -> bool:
    """取消关闭（幂等）：有行删行、没行也算成功，返回是否删掉了一行。"""
    row = (
        db.query(em.UserNextUpDismissal)
        .filter(
            em.UserNextUpDismissal.user_id == user_id,
            em.UserNextUpDismissal.series_id == series_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def dismissal_map(db, user_id: int, series_ids) -> dict:
    """{series_id: dismissed_episode_guid}：只取该用户的关闭记录。"""
    ids = list({sid for sid in series_ids if sid is not None})
    if not ids:
        return {}
    rows = (
        db.query(em.UserNextUpDismissal)
        .filter(
            em.UserNextUpDismissal.user_id == user_id,
            em.UserNextUpDismissal.series_id.in_(ids),
        )
        .all()
    )
    return {r.series_id: r.dismissed_episode_guid for r in rows}
