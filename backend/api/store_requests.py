"""想看商店求片 R1：登记 + 榜单 + 入库联动（路由层）。

总闸与会员闸复用商店骨架（``backend.api.store``）：
- 总闸 ``STORE_ENABLED`` 默认关，关时本组接口惰性 404（路由级依赖
  先于鉴权，未登录也不会先露 401），与商店同款。
- 我的列表限会员（``require_store_member``，与商店 B2 同款）；
  登记写口已下线（410，见 ``post_request``）。

入库联动：
- 读我的列表 / 榜单 / 管理端榜单时，对处理中（pending/approved）
  且 tmdb 对得上库内的行，当场标为 completed（不建转存任务、
  不写 emby_item_id）。这步不看私聊开关。
- 扫描对上库时，只有到货私聊开关开着才做同样的翻转并入队；
  关着时扫描不改求片行。读时翻转和扫描侧共用同一条到货入队，
  已推记录保证不重复发。
- 到货私聊走 ``tg_bot.proactive``，开关默认开（2026-11-08 起）；
  关时不发、不写已推记录。开时私聊求片人和附议人，可带「去看看」按钮。
- 入库后用户下一次打开列表/榜单即看到已入库，随后可走正常想看
  流程（POST /want 的 R0 路）。
- emby_item_id 的 guid 记录仍归既有管理端“标记已入库”入口，
  本模块不同步写它，免得两处口径打架。

不做的事（写明，免得后人以为漏了）：
- 不自动下片、不自动寻源、不接站外下载（那是 R2，另批才议）。
- 登记/附议写口已下线：求片登记合并到许愿池（MediaSeek 建单，
  月额度/总开关/白名单豁免都在那边统一判定，审计 GA-059 以
  收敛方式处置）；本模块只保留读口（我的列表/榜单）与管理端
  只读榜单，入库联动不变。
- 管理端改状态（批准/拒绝/标记已入库）走既有 MediaSeek 入口，
  本模块的管理端榜单只读。
- 想看页（/want）已合并进许愿池（/game/wishlist），本模块的
  前端读口调用方只剩个人中心的「我的求片」摘要。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import media_seek, models
from backend.api.admin_core import get_current_admin
from backend.api.store import (
    find_library_item,
    require_store_enabled,
    require_store_member,
)
from backend.api.user import get_current_user
from backend.database import get_db
from backend.emby_server.store_requests_contract import (
    STATUS_TO_R1_STATE,
    RequestIn,
    RequestListResponse,
    RequestRecord,
)

#: 榜单与列表的单页上限（防全表扫）。
BOARD_LIMIT = 50
MY_LIST_LIMIT = 100
ADMIN_LIST_LIMIT = 100
#: 同步时多读的余量：部分行同步后掉出榜单，余量补位。
SYNC_READ_EXTRA = 150

router = APIRouter(
    prefix="/api/user/store",
    tags=["用户端·想看商店求片"],
    # 总闸关时先 404，鉴权依赖不会先回答 401（与商店同款）。
    dependencies=[Depends(require_store_enabled)],
)

admin_router = APIRouter(
    prefix="/api/admin/store",
    tags=["管理端·想看商店求片"],
    dependencies=[Depends(require_store_enabled)],
)


def _r1_state(status: str) -> str:
    """MediaSeek 原始 status → R1 展示态；未知 status 原样回。"""
    return STATUS_TO_R1_STATE.get(status or "", status or "")


def _sync_row(db: Session, row: models.MovieRequest) -> bool:
    """读时入库联动：处理中的行若 tmdb 已对上库内，当场标 completed。

    只翻状态：不建转存任务、不写 emby_item_id（guid 记录归既有
    管理端“标记已入库”入口）。开关开着时顺手入队到货私聊，
    入队失败不影响翻转。返回是否翻了状态。
    """
    if row.status not in media_seek.ACTIVE_STATUSES:
        return False
    # 补缺集行（request_kind='missing_episodes'）不许走这条联动：
    # 它的剧本来就在库、tmdb 必然对得上，按「在库」翻完成等于
    # 一落单就假完成。它的完成只认缺集核算工人确认补齐。
    if (getattr(row, "request_kind", None) or "seek") == "missing_episodes":
        return False
    if not row.tmdb_id:
        return False
    if find_library_item(db, row.tmdb_id) is None:
        return False
    row.status = "completed"
    try:
        from backend.tg_bot.proactive import note_store_request_completed
        note_store_request_completed(db, row)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "求片到货提醒入队失败 request=%s", getattr(row, "id", None),
            exc_info=True)
    return True


def _library_hit(db: Session, tmdb_id: str | None):
    """tmdb → (是否在库, 库内条目 id)。"""
    if not tmdb_id:
        return False, None
    row = find_library_item(db, tmdb_id)
    if row is None:
        return False, None
    return True, row.id


def _record(row: models.MovieRequest, *, in_library: bool,
            library_item_id: int | None, voted: bool,
            message: str, weighted_vote_count: int) -> RequestRecord:
    return RequestRecord(
        request_id=row.id,
        tmdb_id=row.tmdb_id or "",
        movie_name=row.movie_name,
        vote_count=row.vote_count or 0,
        # Step 1 加权附议：原始人数仍见 vote_count；榜单排序与加权数走这里
        weighted_vote_count=weighted_vote_count,
        status=row.status,
        state=_r1_state(row.status),
        in_library=in_library,
        library_item_id=library_item_id,
        voted=voted,
        message=message,
    )


def _voted_ids(db: Session, user_id: int, request_ids: list[int]) -> set[int]:
    """当前用户在给定求片 id 里登记/附议过哪些（一次查询打标）。"""
    if not request_ids:
        return set()
    rows = (
        db.query(models.MovieRequestVote.request_id)
        .filter(
            models.MovieRequestVote.user_id == user_id,
            models.MovieRequestVote.request_id.in_(request_ids),
        )
        .all()
    )
    return {row[0] for row in rows}


@router.post("/requests", response_model=RequestRecord)
def post_request(
    body: RequestIn,
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestRecord:
    """登记写口已下线（410 Gone）：求片登记合并到许愿池。

    本口原唯一调用方（想看页 /want）已删除，想看整体并入许愿池
    （/game/wishlist）；登记/附议统一走 MediaSeek 建单与投票——
    月额度、总开关、白名单求片 VIP 豁免都在那边判定（审计
    GA-059 以收敛方式处置）。照 A-024 先例：会员闸口径不变
    （先于下线响应判定），其余一律显式 410，不假装登记成功。
    读口（我的列表/榜单）与入库联动不受影响。
    """
    require_store_member(db, user)
    raise HTTPException(
        status_code=410,
        detail="求片登记已合并到许愿池，请到许愿池里投心愿",
    )


@router.get("/requests", response_model=RequestListResponse)
def get_my_requests(
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestListResponse:
    """会员自己的求片：创建的 + 附议过的（tmdb 绑定行），含已入库态。

    读时同步：处理中的行若已入库当场翻 completed，列表即看到已入库。
    """
    require_store_member(db, user)
    voted_stmt = (
        select(models.MovieRequestVote.request_id)
        .where(models.MovieRequestVote.user_id == user.id)
    )
    rows = (
        db.query(models.MovieRequest)
        .filter(
            models.MovieRequest.tmdb_id.isnot(None),
            models.MovieRequest.tmdb_id != "",
            (
                (models.MovieRequest.user_id == user.id)
                | (models.MovieRequest.id.in_(voted_stmt))
            ),
        )
        .order_by(models.MovieRequest.created_at.desc())
        .limit(MY_LIST_LIMIT)
        .all()
    )
    changed = False
    for row in rows:
        changed = _sync_row(db, row) or changed
    if changed:
        db.commit()
    voted_set = _voted_ids(db, user.id, [row.id for row in rows])
    weighted_map = media_seek.weighted_votes_for(db, [row.id for row in rows])
    items = []
    for row in rows:
        in_library, library_item_id = _library_hit(db, row.tmdb_id)
        items.append(_record(
            row, in_library=in_library, library_item_id=library_item_id,
            voted=(row.user_id == user.id or row.id in voted_set),
            message="",
            weighted_vote_count=weighted_map.get(row.id, 0),
        ))
    return RequestListResponse(items=items)


@router.get("/requests/board", response_model=RequestListResponse)
def get_board(
    user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestListResponse:
    """会员可见求片榜：处理中的行按加权附议和排榜（Step 1；原始人数另列）。

    读时同步会把已入库的行翻出榜单（它们不再是“求片”，应去想看）。
    """
    require_store_member(db, user)
    rows = (
        db.query(models.MovieRequest)
        .filter(
            models.MovieRequest.tmdb_id.isnot(None),
            models.MovieRequest.tmdb_id != "",
            models.MovieRequest.status.in_(media_seek.ACTIVE_STATUSES),
        )
        .order_by(
            media_seek.weighted_vote_ordering().desc(),
            models.MovieRequest.created_at.desc(),
        )
        .limit(BOARD_LIMIT + SYNC_READ_EXTRA)
        .all()
    )
    changed = False
    for row in rows:
        changed = _sync_row(db, row) or changed
    if changed:
        db.commit()
    active = [row for row in rows if row.status in media_seek.ACTIVE_STATUSES]
    active = active[:BOARD_LIMIT]
    voted_set = _voted_ids(db, user.id, [row.id for row in active])
    weighted_map = media_seek.weighted_votes_for(db, [row.id for row in active])
    items = []
    for row in active:
        in_library, library_item_id = _library_hit(db, row.tmdb_id)
        items.append(_record(
            row, in_library=in_library, library_item_id=library_item_id,
            voted=(row.user_id == user.id or row.id in voted_set),
            message="",
            weighted_vote_count=weighted_map.get(row.id, 0),
        ))
    return RequestListResponse(items=items)


@admin_router.get("/requests", response_model=RequestListResponse)
def get_admin_requests(
    admin: models.WebUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> RequestListResponse:
    """管理端可见全部登记：含已入库 / 已拒绝 / 已撤回，按加权附议和排。

    只读：改状态（批准/拒绝/标记已入库）走既有 MediaSeek 入口，
    本模块不开写入口。
    """
    rows = (
        db.query(models.MovieRequest)
        .filter(
            models.MovieRequest.tmdb_id.isnot(None),
            models.MovieRequest.tmdb_id != "",
        )
        .order_by(
            media_seek.weighted_vote_ordering().desc(),
            models.MovieRequest.created_at.desc(),
        )
        .limit(ADMIN_LIST_LIMIT + SYNC_READ_EXTRA)
        .all()
    )
    changed = False
    for row in rows:
        changed = _sync_row(db, row) or changed
    if changed:
        db.commit()
    page = rows[:ADMIN_LIST_LIMIT]
    weighted_map = media_seek.weighted_votes_for(db, [row.id for row in page])
    items = []
    for row in page:
        in_library, library_item_id = _library_hit(db, row.tmdb_id)
        items.append(_record(
            row, in_library=in_library, library_item_id=library_item_id,
            voted=False, message="",
            weighted_vote_count=weighted_map.get(row.id, 0),
        ))
    return RequestListResponse(items=items)
