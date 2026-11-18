"""求片中心领域逻辑（用户端 / 管理端 / 自愈注册共用）

把「求片」这条链路的规则收在一处，避免三处各写一份：

- **额度**：每日上限走 SystemConfig ``media_seek_daily_limit``（后台可改），
  默认值只在这里定义一次（``DEFAULT_DAILY_LIMIT``），config_self_heal 引用它；
  另有月度上限（套餐权益 ``media_seek_monthly_limit`` 优先、未声明回落
  ``paid_request_monthly`` 默认10；原公益口径 ``welfare_request_monthly``
  已随福利服役族于 2026-11-14 删除），每日与月度同时生效；
- **剧集按整季申请**：``season`` 存 ``"1,2"`` 或 ``"all"``（全季），
  ``normalize_season`` 负责校验与归一化，界面文案走 ``season_label``；
- **TMDB 候选搜索**：用户端「搜你想看的片」列的是 TMDB 条目，
  并标出哪些**已经在库**（在库的直接引导播放，不占用额度）；
- **库内匹配**：管理端「标记已入库」用它确认片真的进了库（按 tmdb_id 优先、
  退回片名），匹配上才允许把求片标记成已完成。

本模块不依赖 FastAPI：路由层只做鉴权 / 参数 / 通知，规则都在这里。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend import entitlements, governance, models
from backend.integrations.store import switch_env_value as _switch_env_value

logger = logging.getLogger(__name__)

# ==================== 配置键与默认值 ====================

# 每日求片上限（防止刷单）；后台「系统设置 → 经济」里可改
CONFIG_DAILY_LIMIT = "media_seek_daily_limit"
DEFAULT_DAILY_LIMIT = 5

# 附议：每人 1 票（等级加权已随公益服删除退役，历史票的 weight_snapshot
# 不动、汇总时照旧计入）；每日附议上限走 SystemConfig（后台「系统设置 → 经济」可改）
CONFIG_DAILY_VOTE_LIMIT = "media_seek_daily_vote_limit"
DEFAULT_DAILY_VOTE_LIMIT = 20

# 用户主动撤回的求片：不再展示、也不再参与去重，但仍计入当天的提交数
WITHDRAWN_STATUS = "withdrawn"

# 求片「处理中」的状态：去重与撤回判定都用这一份口径
ACTIVE_STATUSES = ("pending", "approved")

# 请求分类（MovieRequest.request_kind）：整片求片与补缺集在
# backend/missing_episodes 里另有常量（KIND_SEEK / KIND_MISSING）；
# 这里补「追新」——用户从库内作品行发起的自由说明追新（2026-11-15
# 按作品组织），与补缺集同属「追新类」（广场计数 / 查重同口径）。
KIND_CHASE = "chase"
CHASE_KINDS = (KIND_CHASE, "missing_episodes")

# 具体说明 / 补充说明的字数上限（提交弹层计数同口径，前后端各守一遍）
MAX_NOTE_LENGTH = 2000


def kind_class(kind) -> str:
    """request_kind → 展示分类：追新类（追新/补缺集）归 'chase'，其余归 'seek'"""
    return "chase" if (kind or "seek") in CHASE_KINDS else "seek"


def clean_poster_url(raw) -> Optional[str]:
    """海报快照地址清洗：只收 http(s) 地址且不超长，其余一律丢弃为 None。

    快照来自前端候选（TMDB 图片地址），落库前过这一道，避免脏值/脚本
    地址混进展示面；展示时它只会进 <img src>，但口径上先挡一道。
    """
    text = str(raw or "").strip()
    if not text or len(text) > 500:
        return None
    if not (text.startswith("https://") or text.startswith("http://")):
        return None
    return text

# 剧集类求片必须带季（端点校验用；电影/纪录片等忽略该字段）
SERIES_TYPES = ("series", "anime")

# 全季：不指定具体季时归一化成它，记录里始终是显式值（界面不用猜空值含义）
SEASON_ALL = "all"
MAX_SEASON_NUMBER = 99
MAX_SEASON_COUNT = 30

_SEASON_SPLIT_RE = re.compile(r"[,，、/\s]+")


# ==================== 额度 ====================

def daily_limit(db: Session) -> int:
    """每日求片上限：SystemConfig 覆盖，缺省/脏值回默认值（绝不让脏配置把求片打死）"""
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == CONFIG_DAILY_LIMIT
    ).first()
    raw = str(cfg.value).strip() if cfg and cfg.value is not None else ""
    if not (raw.isascii() and raw.isdigit()):
        return DEFAULT_DAILY_LIMIT
    return max(1, int(raw))


def used_today(db: Session, user_id: int) -> int:
    """今天提交过多少条（含后来撤回的）

    撤回不退还额度——否则「提交 → 撤回 → 再提交」可以无限刷新额度，
    同时每次提交都会给全体管理员推一条站内消息，那就成了通知刷屏器。

    r34 口径收口：MovieRequest.created_at 以 UTC naive 入库，此前用服务器
    本地日界，每日额度在北京时间 08:00 才重置。函数内导入 economy 是为了
    避开循环导入（economy → api.user → media_seek）。
    """
    from backend.api.economy import _today_start_utc_naive
    today_start = _today_start_utc_naive()
    return db.query(models.MovieRequest).filter(
        models.MovieRequest.user_id == user_id,
        models.MovieRequest.created_at >= today_start,
    ).count()


def quota(db: Session, user_id: int) -> dict:
    """今日额度快照（用户端「申请时显示剩余额度」直接用它）"""
    limit = daily_limit(db)
    used = used_today(db, user_id)
    return {"used_today": used, "daily_limit": limit, "remaining": max(0, limit - used)}


# ==================== 月度额度 ====================
# 原公益口径（welfare_request_monthly，默认 3，优先于套餐权益）已随
# 福利服役族于 2026-11-14 删除：资格已全量折算成订阅，1298 名原福利
# 用户与其他订阅用户一致走套餐权益口径。

CONFIG_MONTHLY_PAID = "paid_request_monthly"
DEFAULT_MONTHLY_PAID = 10


def _monthly_limit_value(db: Session, key: str, default: int) -> int:
    """读月度上限配置：缺省/脏值回默认值（绝不让脏配置把求片打死）"""
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key
    ).first()
    raw = str(cfg.value).strip() if cfg and cfg.value is not None else ""
    if not (raw.isascii() and raw.isdigit()):
        return default
    return max(1, int(raw))


def monthly_limit(db: Session, user) -> int:
    """该用户的月度求片上限（判定顺序钉死，设计 §3.2）：

    1. 当前套餐在权益表里声明了 ``media_seek_monthly_limit``
       就用声明值（经 backend/entitlements 单口读，不许在此私自判套餐）。
    2. 没声明（无订阅/缺键/值非法）回落现行 paid 口径
       ``paid_request_monthly``（默认 10），与没有权益表时逐字一致。

    （原第 1 档公益口径已随福利服役族于 2026-11-14 删除。）
    """
    entitled = entitlements.get_user_entitlement(db, user, "media_seek_monthly_limit")
    if entitled is not None:
        return entitled
    return _monthly_limit_value(db, CONFIG_MONTHLY_PAID, DEFAULT_MONTHLY_PAID)


def used_this_month(db: Session, user_id: int) -> int:
    """本月提交过多少条（含后来撤回的，与 used_today 同一口径）

    r34 口径收口：与 used_today 同理，月界按上海自然月（UTC 入库帧对齐），
    此前服务器本地月界在每月 1 日 08:00（北京时间）才重置。
    """
    from backend.api.economy import _month_start_utc_naive
    month_start = _month_start_utc_naive()
    return db.query(models.MovieRequest).filter(
        models.MovieRequest.user_id == user_id,
        models.MovieRequest.created_at >= month_start,
    ).count()


def _is_whitelist_exempt(db: Session, user) -> bool:
    """白名单（永久）账号判定（求片月度额度豁免用）。

    口径只复用 codes 的白名单定义（codes.is_whitelist_account：核销痕迹
    ＋三处永久现算），本模块不另立名单。函数内导入 codes 避开循环导入
    （与 vitality 豁免同款写法）。判定失败时按不豁免处理
    （fail-closed）并记日志，绝不凭不确定数据放行。
    """
    if user is None:
        return False
    try:
        from backend import codes as _codes

        return _codes.is_whitelist_account(db, user)
    except Exception:
        logger.exception("白名单求片额度豁免判定失败，按不豁免处理 user=%s",
                         getattr(user, "id", None))
        return False


def monthly_quota(db: Session, user) -> dict:
    """月度额度快照：kind=用户类型，limit/used/remaining=上限/已用/剩余（剩余不为负）

    白名单（永久）账号月度额度豁免（2026-11-02 独一无二体验包·求片 VIP）：
    limit/used 仍如实快照（kind 与上限口径不变），但 remaining 不被用满
    卡死、另带 monthly_exempt=True 标明身份。提交口（api.user 的月度
    429 校验）读的就是本快照的 remaining，豁免在这一处单点生效，
    展示与校验不会两张皮。每日防刷上限不豁免，不在此列。

    家庭子账号（2026-11-15）：额度是主账号套餐的一份，全家共用——
    上限读主账号当前套餐（有效账号解析），已用量把主账号与全部
    子账号本月提交合并计（family_user_ids）；豁免身份仍看请求者
    本人（子账号不会是白名单）。求片记录本身仍记在提交者名下，
    这里只改额度口径，不动归属。
    """
    from backend import sub_accounts

    effective = sub_accounts.effective_user(db, user)
    limit = monthly_limit(db, effective)
    used = sum(used_this_month(db, uid)
               for uid in sub_accounts.family_user_ids(db, effective))
    exempt = _is_whitelist_exempt(db, user)
    return {
        "kind": "paid",
        "monthly_limit": limit,
        "monthly_used": used,
        "monthly_remaining": limit if exempt else max(0, limit - used),
        "monthly_exempt": exempt,
    }


# ==================== 剧集按整季申请 ====================

def normalize_season(raw, *, series: bool) -> str:
    """归一化「申请的季」：``"1,2"`` / ``"all"``；非剧集返回空串

    - 剧集（series / anime）必须给出季：空值当作「全季」（``all``），
      这样记录里永远是显式值，后台和用户列表都不用猜空字段的含义；
    - 合法写法：数字、逗号/顿号/斜杠分隔的多季、``all`` / ``全季`` / ``全部``；
    - 非法写法（``"第一季"``、``"abc"``、超过 99、多于 30 个季）抛 ``ValueError``，
      由路由转成 400 并说清怎么填。
    """
    text = str(raw or "").strip().lower()
    if not series:
        return ""
    if not text or text in (SEASON_ALL, "*", "全部", "全季", "all season", "all seasons"):
        return SEASON_ALL

    nums: list[int] = []
    for part in _SEASON_SPLIT_RE.split(text):
        if not part:
            continue
        if not (part.isascii() and part.isdigit()):
            raise ValueError("季要填数字，例如 1 或 1,2；不指定就留空表示全部季")
        num = int(part)
        if num > MAX_SEASON_NUMBER:
            raise ValueError(f"季号要在 0 ~ {MAX_SEASON_NUMBER} 之间（0 = 特别篇）")
        if num not in nums:
            nums.append(num)
    if not nums:
        return SEASON_ALL
    if len(nums) > MAX_SEASON_COUNT:
        raise ValueError(f"一次最多申请 {MAX_SEASON_COUNT} 个季")
    return ",".join(str(n) for n in sorted(nums))


def season_label(season) -> str:
    """界面文案：``"all"`` → 全季；``"1,3"`` → 第 1、3 季；``"0"`` → 特别篇；空 → 空串"""
    text = str(season or "").strip()
    if not text:
        return ""
    if text == SEASON_ALL:
        return "全季"
    if text == "0":
        return "特别篇"
    parts = ["特别篇" if item == "0" else item for item in text.split(",")]
    return "第 " + "、".join(parts) + " 季"


def normalize_tmdb_id(raw) -> str:
    """TMDB id：只接受纯数字（搜索候选带过来的值），其它一律丢弃为空"""
    text = str(raw or "").strip()
    if not text or not (text.isascii() and text.isdigit()) or len(text) > 12:
        return ""
    return text


# ==================== 库内匹配 ====================

# 求片类型 → 库内条目类型（判断「入库了没有」时用；未列出的类型不按类型过滤）
_TYPE_MATCH = {
    "movie": ("movie",),
    "series": ("series",),
    "anime": ("series",),
}


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符：先反斜杠，再 % 和 _（调用方配 escape='\\'）"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def library_hit(db: Session, *, tmdb_id: Optional[str] = None,
                name: str = "", item_type: Optional[str] = None,
                year: Optional[str] = None):
    """在自建媒体库里找一条匹配的条目（找到返回 MediaItem，否则 None）

    先按 ``tmdb_id`` 精确匹配（刮削过的条目都有，最可靠），没有再退回片名包含匹配
    （老条目可能还没补全 tmdb_id）。片名匹配时尽量按类型过滤，避免
    「电影《某某》」被同名的剧集顶掉。
    ``year`` 为求片年份（字符串），片名回退时用于避免同名不同年的条目误匹配；
    解析失败则忽略，不按年份过滤。
    两分支均只认未删除且未隐藏的条目（A-113）；管理端 find_library_match
    共用本口径，隐藏／已删条目不得被标记为已入库（确需强制时走 force）。
    """
    from backend.emby_server import models as em

    if tmdb_id:
        query = (
            db.query(em.MediaItem)
            .filter(em.MediaItem.tmdb_id == str(tmdb_id))
            .filter(em.MediaItem.deleted_at.is_(None))
            .filter(em.MediaItem.is_hidden == False)  # noqa: E712
        )
        allowed = _TYPE_MATCH.get(str(item_type or ""))
        if allowed:
            query = query.filter(em.MediaItem.item_type.in_(allowed))
        hit = query.order_by(em.MediaItem.date_added.desc()).first()
        if hit is not None:
            return hit

    keyword = (name or "").strip()
    if not keyword or (len(keyword) < 2 and keyword.isascii()):
        return None
    query = db.query(em.MediaItem).filter(
        em.MediaItem.name.ilike(f"%{_escape_like(keyword)}%", escape="\\")
    ).filter(
        em.MediaItem.deleted_at.is_(None),
        em.MediaItem.is_hidden == False,  # noqa: E712
    )
    allowed = _TYPE_MATCH.get(str(item_type or ""))
    if allowed:
        query = query.filter(em.MediaItem.item_type.in_(allowed))
    try:
        year_value = int(str(year).strip())
    except (TypeError, ValueError):
        year_value = None
    if year_value is not None:
        query = query.filter(or_(
            em.MediaItem.production_year.is_(None),
            em.MediaItem.production_year == year_value,
        ))
    return query.order_by(em.MediaItem.date_added.desc()).first()


def find_library_match(db: Session, request: models.MovieRequest):
    """给一条求片找库内条目（管理端「标记已入库」的判定口径）"""
    return library_hit(
        db,
        tmdb_id=getattr(request, "tmdb_id", None),
        name=getattr(request, "movie_name", "") or "",
        item_type=getattr(request, "type", None),
        year=getattr(request, "year", None),
    )


def sync_seek_library_row(db: Session, row: models.MovieRequest) -> bool:
    """入库联动（唯一口径）：处理中的 seek 行若 tmdb 已对上库内，
    当场标 completed 并顺手入队到货私聊。

    读列表路径（``api.store_requests._sync_row``）与到货盯梢工人
    （``emby_server.seek_fulfill_worker``）共用本函数，不许另写第二
    套判定。只翻状态：不建转存任务、不写 emby_item_id（guid 记录归
    管理端「标记已入库」入口）。到货私聊走 proactive 的已推去重，
    重复调用不重发；入队失败不影响翻转。返回是否翻了状态，提交由
    调用方负责。
    """
    if row.status not in ACTIVE_STATUSES:
        return False
    # 追新类行（补缺集 missing_episodes / 自由追新 chase）不许走这条
    # 联动：它们的作品本来就在库、tmdb 必然对得上，按「在库」翻完成
    # 等于一落单就假完成。补缺集的完成只认缺集核算工人确认补齐，
    # 自由追新只认管理员处理。
    if kind_class(getattr(row, "request_kind", None)) != "seek":
        return False
    if not row.tmdb_id:
        return False
    # 延迟导入：api.store_requests 会拉起 api.user（它反过来 import
    # 本模块），顶层导入必成环；与 library_hit 的延迟导入同一先例。
    from backend.api.store_requests import find_library_item

    if find_library_item(db, row.tmdb_id) is None:
        return False
    row.status = "completed"
    try:
        from backend.tg_bot.proactive import note_store_request_completed

        note_store_request_completed(db, row)
    except Exception:
        logger.warning(
            "求片到货提醒入队失败 request=%s", getattr(row, "id", None),
            exc_info=True)
    return True


# ==================== TMDB 候选搜索 ====================

def _poster_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    # 图片 CDN 地址跟刮削侧同源：后台配了镜像，这里自动跟着走
    from backend.emby_server import tmdb

    return f"{tmdb.image_base()}/w300{path}"


def _safe_float(value, default: float = 0.0) -> float:
    """脏数据容错：非数字（None / "N/A" 等）回 default，不抛 500"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    """脏数据容错：非数字回 default，不抛 500"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _year_of(hit: dict, kind: str) -> str:
    raw = hit.get("first_air_date") if kind == "series" else hit.get("release_date")
    text = str(raw or "")
    return text[:4] if len(text) >= 4 else ""


def search_candidates(db: Session, query: str, kind: Optional[str] = None) -> dict:
    """TMDB 候选搜索（求片用）：列条目并标出哪些已在库

    与刮削用的 ``TmdbClient.search`` 不同：这里**不做置信度裁剪**——用户搜什么就列什么，
    让他自己认片（刮削要的是唯一正确答案，求片要的是候选列表）。
    返回 ``{"configured": bool, "degraded": bool, "results": [...]}``；TMDB 未配置时 ``configured=False``，
    前端据此回退到「按片名查本地库」的旧路径，而不是给一个永远搜不出东西的框。
    degraded=True 表示 TMDB 搜索中途失败、结果可能不全。
    """
    from backend.emby_server import tmdb

    client = tmdb.tmdb_client
    keyword = (query or "").strip()
    if len(keyword) < 1:
        return {"configured": bool(client.configured), "degraded": False, "results": []}
    if not client.configured:
        return {"configured": False, "degraded": False, "results": []}

    kinds = [kind] if kind in ("movie", "series") else ["movie", "series"]
    results: list[dict] = []
    degraded = False
    for one_kind in kinds:
        try:
            hits = client.search_candidates(keyword, one_kind)
        except Exception as exc:  # noqa: BLE001 — 搜索失败只影响这次候选列表
            logger.warning("TMDB 求片搜索失败（%s / %s）: %s", keyword, one_kind, exc)
            degraded = True
            continue
        for hit in hits:
            tmdb_id = str(hit.get("id") or "").strip()
            name = str(hit.get("title") or hit.get("name") or "").strip()
            if not tmdb_id or not name:
                continue
            match = library_hit(db, tmdb_id=tmdb_id, name=name, item_type=one_kind,
                                year=_year_of(hit, one_kind))
            original = str(
                hit.get("original_title") or hit.get("original_name") or ""
            ).strip()
            results.append({
                "tmdb_id": tmdb_id,
                "kind": one_kind,               # movie / series（前端用来选「季」）
                "name": name,
                # 原名（TMDB original_title/original_name）：结果行
                # 「原名 / 年份」一行用它；与中文名相同或缺失时回空串，
                # 前端回退展示中文名，不编造。
                "original_name": original if original != name else "",
                "year": _year_of(hit, one_kind),
                "overview": str(hit.get("overview") or "")[:300],
                "poster_url": _poster_url(hit.get("poster_path")),
                "rating": round(_safe_float(hit.get("vote_average"), 0.0), 1) or None,
                "in_library": match is not None,
                "library_item_id": str(getattr(match, "guid", "") or "") or None,
            })
    # 未完成请求标注（按作品组织，2026-11-15）：结果行要按「这部作品
    # 已经有人在求/在追」分化动作（查看已有请求 vs 发起），一次批量查
    # 完本页候选的未完结行，按 tmdb 聚成 open_seek / open_chase 两旗。
    open_flags = open_request_flags(
        db, [r["tmdb_id"] for r in results])
    for r in results:
        flags = open_flags.get(r["tmdb_id"]) or {}
        r["open_seek"] = bool(flags.get("seek"))
        r["open_chase"] = bool(flags.get("chase"))
    # 已在库的沉到下面：用户要的是「还没入库、可以求」的候选
    results.sort(key=lambda r: (r["in_library"], -float(r.get("rating") or 0)))
    return {"configured": True, "degraded": degraded, "results": results[:12]}


def open_request_flags(db: Session, tmdb_ids: list) -> dict:
    """一批 tmdb_id 各自有没有未完结的求片/追新：``{tmdb_id: {"seek": bool, "chase": bool}}``

    未完结 = ACTIVE_STATUSES（pending/approved）；追新类含补缺集行
    （kind_class 口径）。搜索标注与作品详情的拦截提示共用这一口。
    """
    ids = [str(t).strip() for t in (tmdb_ids or []) if str(t or "").strip()]
    if not ids:
        return {}
    rows = (
        db.query(models.MovieRequest.tmdb_id, models.MovieRequest.request_kind)
        .filter(
            models.MovieRequest.tmdb_id.in_(ids),
            models.MovieRequest.status.in_(ACTIVE_STATUSES),
        )
        .all()
    )
    flags: dict[str, dict] = {}
    for tmdb_id, kind in rows:
        entry = flags.setdefault(str(tmdb_id), {"seek": False, "chase": False})
        entry[kind_class(kind)] = True
    return flags


def seasons_for(tmdb_id: str) -> list[dict]:
    """剧集的季列表（用户选「按整季申请」时用）；未配置/查不到返回空表"""
    from backend.emby_server import tmdb

    client = tmdb.tmdb_client
    text = normalize_tmdb_id(tmdb_id)
    if not text or not client.configured:
        return []
    try:
        data = client.details(text, "series")
    except Exception as exc:  # noqa: BLE001 — 查不到季不影响手动填季
        logger.warning("TMDB 拉取季列表失败（%s）: %s", text, exc)
        return []
    seasons: list[dict] = []
    for row in (data or {}).get("seasons") or []:
        num = row.get("season_number")
        if num is None:
            continue
        try:
            number = int(num)
        except (TypeError, ValueError):
            continue
        seasons.append({
            "season_number": number,
            "name": str(row.get("name") or f"第 {number} 季"),
            "episode_count": _safe_int(row.get("episode_count"), 0),
            "air_date": str(row.get("air_date") or ""),
        })
    seasons.sort(key=lambda s: s["season_number"])
    return seasons


# ==================== v2：总开关 / 附议 / 附议者通知 ====================
# 全部可配置（WELFARE_CONFIG_KEYS + config_self_heal 注册），脏值回默认值。

CONFIG_ENABLED = "media_seek_enabled"
CONFIG_VOTE_ENABLED = "media_seek_vote_enabled"
CONFIG_NOTIFY_VOTERS = "media_seek_notify_voters"
# 求片减负（2026-11-10）：自动批准默认关（关时落单 pending、与现行
# 逐字一致）；到货盯梢间隔默认 5 分钟。两键登记在运营设置
# （admin_economy.ECONOMY_CONFIG_KEYS + config_schema.ECONOMY_CONFIG_SCHEMA
# + Settings.vue「兑换码 · 求片」卡），脏值回默认、绝不把链路打死。
CONFIG_AUTO_APPROVE = "media_seek_auto_approve_enabled"
CONFIG_FULFILL_INTERVAL = "media_seek_fulfill_interval_minutes"
DEFAULT_FULFILL_INTERVAL_MINUTES = 5


def _get_bool(db: Session, key: str, default: bool = True) -> bool:
    """读布尔配置：'1'/'true' 为开，'0'/'false' 为关；缺省/脏值回 default"""
    # A 类摘面板开关（2026-10-21）：env 显式设置即接管，口径见 integrations.store
    _env_v = _switch_env_value(key)
    if _env_v is not None:
        raw = str(_env_v).strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return default
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key
    ).first()
    raw = str(cfg.value).strip().lower() if cfg and cfg.value is not None else ""
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def seek_enabled(db: Session) -> bool:
    """求片总开关：关闭后用户不能提交求片（只读端点不受影响）"""
    return _get_bool(db, CONFIG_ENABLED, True)


def vote_enabled(db: Session) -> bool:
    """附议开关：关闭后附议端点 403"""
    return _get_bool(db, CONFIG_VOTE_ENABLED, True)


def notify_voters_enabled(db: Session) -> bool:
    """入库通知附议者开关：标记 completed 时是否也通知附议过的用户"""
    return _get_bool(db, CONFIG_NOTIFY_VOTERS, True)


def auto_approve_enabled(db: Session) -> bool:
    """自动批准开关：默认关。开启后新建单落单即 approved（状态就是
    人工批准的 approved，不另造第三种状态）；关闭时行为与现行一致。"""
    return _get_bool(db, CONFIG_AUTO_APPROVE, False)


def fulfill_interval_minutes(db: Session) -> int:
    """到货盯梢工人的轮间隔（分钟）：SystemConfig 覆盖，缺省/脏值回
    默认 5，钳制 1~1440（与 config_schema 登记范围同口径）。"""
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == CONFIG_FULFILL_INTERVAL
    ).first()
    raw = str(cfg.value).strip() if cfg and cfg.value is not None else ""
    if not (raw.isascii() and raw.isdigit()):
        return DEFAULT_FULFILL_INTERVAL_MINUTES
    return min(1440, max(1, int(raw)))


# ==================== 附议 ====================

def daily_vote_limit(db: Session) -> int:
    """每日附议上限：SystemConfig 覆盖，缺省/脏值回默认值（绝不让脏配置把附议打死）"""
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == CONFIG_DAILY_VOTE_LIMIT
    ).first()
    raw = str(cfg.value).strip() if cfg and cfg.value is not None else ""
    if not (raw.isascii() and raw.isdigit()):
        return DEFAULT_DAILY_VOTE_LIMIT
    return max(1, int(raw))


def vote_weight_for_user(db: Session, user, req) -> int:
    """投票人本次附议的权重：全站恒为 1（等级加权已随公益服删除退役）。

    签名保留给既有调用方（``_cast_vote_locked`` 等）；历史票的
    ``weight_snapshot`` 不动，``weighted_votes_for`` 汇总时照旧计入。
    """
    return 1


def weighted_votes_for(db: Session, request_ids: list) -> dict:
    """一批求片的加权附议和（一次 GROUP BY 查询；老票 weight_snapshot 为空按 1 计）"""
    if not request_ids:
        return {}
    rows = (
        db.query(
            models.MovieRequestVote.request_id,
            func.coalesce(func.sum(func.coalesce(models.MovieRequestVote.weight_snapshot, 1)), 0),
        )
        .filter(models.MovieRequestVote.request_id.in_(request_ids))
        .group_by(models.MovieRequestVote.request_id)
        .all()
    )
    return {int(rid): int(total or 0) for rid, total in rows}


def weighted_vote_ordering():
    """榜单加权排序表达式：附议行权重快照之和（老票空值按 1 计），供 order_by 用"""
    return (
        select(
            func.coalesce(
                func.sum(func.coalesce(models.MovieRequestVote.weight_snapshot, 1)), 0
            )
        )
        .where(models.MovieRequestVote.request_id == models.MovieRequest.id)
        .scalar_subquery()
    )


def daily_votes_used(db: Session, user_id: int) -> int:
    """今天已附议次数（按当日现存附议行计；取消是硬删除，删行即释放名额——
    与求片撤回的软删除不同，附议没有“撤回不退还”可计的残留行。
    防刷仍成立：榜单热度只反映现存票，投→撤不留膨胀热度）。
    日界复用 economy 口径（懒导入防循环）。"""
    from backend.api.economy import _today_start_utc_naive
    today_start = _today_start_utc_naive()
    return db.query(models.MovieRequestVote).filter(
        models.MovieRequestVote.user_id == user_id,
        models.MovieRequestVote.created_at >= today_start,
    ).count()


def _cast_vote_locked(db: Session, user, req) -> None:
    """在已持行锁的求片行上补一票（权重快照与 toggle 的加票分支同一套）。

    调用方负责前置校验（pending / 非本人 / 每日上限）与 commit；
    被停权用户只保留基础 1 票，不按等级权重。
    """
    weight = 1 if governance.is_governance_frozen(db, user.id) else vote_weight_for_user(db, user, req)
    db.add(models.MovieRequestVote(
        request_id=req.id, user_id=user.id, weight_snapshot=weight,
    ))
    req.vote_count = (req.vote_count or 0) + 1


def ensure_vote(db: Session, user, request_id: int) -> dict:
    """幂等附议：已附议原样返回，未附议补一票。返回 {"voted", "added",
    "limited", "vote_count", "weighted_vote_count"}。

    与 toggle 的区别只在语义：toggle 是开关（点两次＝取消），ensure 是
    「确保投过」（点两次仍是投过）——许愿池选片投愿的查重转换用它。
    其余规则与 toggle 的加票分支同一套：附议开关关闭 / 求片不存在 /
    非 pending / 给自己附议 → ValueError；每日上限已满不抛错（返回
    voted=False + limited=True）——查重转换不该因附议名额用完就把整个
    投愿打成失败，上层据此只指路不附议。
    """
    if not vote_enabled(db):
        raise ValueError("附议功能已关闭")
    req = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == request_id
    ).with_for_update().first()
    if req is None:
        raise ValueError("求片不存在")
    if req.status != "pending":
        raise ValueError("该求片已处理，不能附议")
    if req.user_id == user.id:
        raise ValueError("不能给自己的求片附议")

    existing = db.query(models.MovieRequestVote).filter(
        models.MovieRequestVote.request_id == request_id,
        models.MovieRequestVote.user_id == user.id,
    ).first()

    added = False
    limited = False
    if existing:
        voted = True
    elif daily_votes_used(db, user.id) >= daily_vote_limit(db):
        voted = False
        limited = True
    else:
        _cast_vote_locked(db, user, req)
        db.commit()
        voted = True
        added = True
    weighted = weighted_votes_for(db, [request_id]).get(request_id, 0)
    return {
        "voted": voted,
        "added": added,
        "limited": limited,
        "vote_count": int(req.vote_count or 0),
        "weighted_vote_count": int(weighted),
    }


def toggle_vote(db: Session, user, request_id: int) -> dict:
    """附议 / 取消附议一条求片。返回 {"voted": bool, "vote_count": int,
    "weighted_vote_count": int}

    规则（附议）：
    - 附议开关关闭 → ValueError（路由转 403）
    - 求片不存在 → ValueError
    - 只能对 pending 的求片附议
    - 不能给自己的求片附议
    - 已附议 → 取消；未附议 → 附议（vote_count 行锁更新，防并发）
    - 新附议恒为 1 票（weight_snapshot 记 1）；历史票的权重快照不动，
      取消删行即自动扣减该票权重；取消附议不受停权影响
    - 新附议受每日上限管（默认 20 次，按当日现存附议行计；取消删行即释放
      名额）；取消永远放行
    """
    if not vote_enabled(db):
        raise ValueError("附议功能已关闭")
    # 行锁读求片行：校验与 vote_count 更新在同一行锁下，并发附议不丢数
    req = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == request_id
    ).with_for_update().first()
    if req is None:
        raise ValueError("求片不存在")
    if req.status != "pending":
        raise ValueError("该求片已处理，不能附议")
    if req.user_id == user.id:
        raise ValueError("不能给自己的求片附议")

    existing = db.query(models.MovieRequestVote).filter(
        models.MovieRequestVote.request_id == request_id,
        models.MovieRequestVote.user_id == user.id,
    ).first()

    if existing:
        db.delete(existing)
        req.vote_count = max(0, (req.vote_count or 0) - 1)
        voted = False
    else:
        if daily_votes_used(db, user.id) >= daily_vote_limit(db):
            raise ValueError("今日附议已达上限")
        _cast_vote_locked(db, user, req)
        voted = True
    db.commit()
    weighted = weighted_votes_for(db, [request_id]).get(request_id, 0)
    return {
        "voted": voted,
        "vote_count": int(req.vote_count or 0),
        "weighted_vote_count": int(weighted),
    }


def voter_ids(db: Session, request_id: int, exclude_user_id=None) -> list:
    """一条求片的所有附议者 user_id（可排除求片者本人）"""
    q = db.query(models.MovieRequestVote.user_id).filter(
        models.MovieRequestVote.request_id == request_id
    )
    if exclude_user_id is not None:
        q = q.filter(models.MovieRequestVote.user_id != exclude_user_id)
    return [row[0] for row in q.all()]


def voted_ids(db: Session, user_id: int, request_ids: list) -> set:
    """当前用户在给定求片 id 列表里附议过哪些（用户端列表打标用，一次查询）"""
    if not request_ids:
        return set()
    rows = db.query(models.MovieRequestVote.request_id).filter(
        models.MovieRequestVote.user_id == user_id,
        models.MovieRequestVote.request_id.in_(request_ids),
    ).all()
    return {row[0] for row in rows}


# ==================== 请求广场（按媒体聚合） ====================

#: 广场状态过滤口径（用户端统一状态名 → movie_requests.status 集合）。
#: 撤回（withdrawn）是用户自己的取消，不进广场聚合（下方查询恒排除）。
PLAZA_STATUS_MAP = {
    "pending": ("pending",),
    "doing": ("approved",),
    "done": ("completed",),
    "rejected": ("rejected",),
}
#: 广场一次最多返回的媒体组数与每组附带的可附议明细行数（防数据量无界增长）
PLAZA_GROUP_LIMIT = 60
PLAZA_DETAIL_LIMIT = 10
#: 聚合前最多读取的求片行数（按创建时间倒序，超出部分是老话题，不进广场）
_PLAZA_ROW_LIMIT = 2000

_TITLE_KEY_STRIP = re.compile(r"[《》\s·•:：,，.。!！?？;；'\"“”‘’()（）\-—]")


def _plaza_title_key(name: Optional[str]) -> str:
    """原名兜底行（无 tmdb_id）的媒体身份键：与前端 normTitle 同字符集的归一化片名。"""
    return _TITLE_KEY_STRIP.sub("", (name or "").lower())


def _plaza_group_key(row) -> str:
    """媒体身份：有 tmdb_id 按 tmdb_id（管理端入库匹配同一口径），
    没有退回「归一化片名|年份|类型」（原名兜底行只能这样认亲）。"""
    tmdb_id = (getattr(row, "tmdb_id", None) or "").strip()
    if tmdb_id:
        return f"tmdb:{tmdb_id}"
    return "title:{}|{}|{}".format(
        _plaza_title_key(row.movie_name), row.year or "", row.type or "")


def _plaza_poster_url(item) -> Optional[str]:
    """库内条目海报 URL：与 emby_server.library_covers._item_poster_url 同口径
    （本地缓存图在盘 → 走 Images 路由；否则远程图直给；都没有 → None）。"""
    import os

    from backend.emby_server import image_store

    poster_path = (getattr(item, "poster_path", None) or "").strip()
    remote = (getattr(item, "primary_image_url", None) or "").strip()
    if poster_path and image_store.is_cached_path(poster_path) and os.path.isfile(poster_path):
        return f"/emby/Items/{item.guid}/Images/Primary"
    return remote or None


def _plaza_missing_text(row) -> str:
    """补缺集行的缺集文案：与 api/user.py 的 _request_missing_text 同口径。"""
    from backend import missing_episodes as _me

    if (getattr(row, "request_kind", None) or "seek") != _me.KIND_MISSING:
        return ""
    return _me.missing_text(_me.request_detail_missing(row.request_detail))


def plaza_groups(db: Session, user_id: int, *, status: Optional[str] = None,
                 kind: Optional[str] = None, q: Optional[str] = None) -> list[dict]:
    """请求广场：把全站求片按媒体身份聚合（一部片一张卡）。

    口径（与「广场只放求片数据、工单不进广场」的既定规则一致）：
    - 数据源只有 movie_requests；反馈工单没有媒体身份、也不公开，恒不进广场，
      故每组的 feedback_count 恒为 0——这是数据口径的结果，不是估算；
    - 计数三分：seek＝整片求片行，chase＝追新类行（自由追新 chase＋
      补缺集 missing_episodes，kind_class 口径），total＝组内全部行；
    - status / kind / q 都先作用在行上再聚合（筛选后计数自洽）；
      status 取 PLAZA_STATUS_MAP 的键，kind 取 seek / chase，未知值忽略（按全部）；
    - 「库内已有」：组内有 completed 行 / 任一行带 emby_item_id / tmdb 命中库内
      可见条目（library_hit 的可见性口径：未删除、未隐藏、类型相符）任一成立；
    - 明细只带 pending 行（可附议的那些），按加权附议和排序。
    """
    query = db.query(models.MovieRequest).filter(
        models.MovieRequest.status != WITHDRAWN_STATUS)
    wanted = PLAZA_STATUS_MAP.get(status or "")
    if wanted:
        query = query.filter(models.MovieRequest.status.in_(wanted))
    if kind == "seek":
        query = query.filter(
            or_(models.MovieRequest.request_kind.is_(None),
                models.MovieRequest.request_kind == "seek"))
    elif kind == "chase":
        query = query.filter(models.MovieRequest.request_kind.in_(CHASE_KINDS))
    keyword = (q or "").strip()
    if keyword:
        query = query.filter(
            models.MovieRequest.movie_name.ilike(
                f"%{_escape_like(keyword)}%", escape="\\"))
    rows = (query.order_by(models.MovieRequest.created_at.desc())
            .limit(_PLAZA_ROW_LIMIT).all())

    groups: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = _plaza_group_key(row)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "key": key,
                "tmdb_id": (getattr(row, "tmdb_id", None) or "").strip() or None,
                "movie_name": row.movie_name,
                "year": row.year,
                "type": row.type,
                "in_library": False,
                "emby_item_id": None,
                "poster_url": None,
                "processing_count": 0,
                "seek_count": 0,
                "chase_count": 0,
                "feedback_count": 0,
                "total_count": 0,
                "vote_total": 0,
                "latest_at": row.created_at,
                "_pending": [],
                "_poster_snapshot": None,
            }
            order.append(key)
        g["total_count"] += 1
        if kind_class(getattr(row, "request_kind", None)) == "chase":
            g["chase_count"] += 1
        else:
            g["seek_count"] += 1
        # 海报快照回退：组内最新一条带快照的行（行已按创建时间倒序）
        if g["_poster_snapshot"] is None:
            snap = clean_poster_url(getattr(row, "poster_url", None))
            if snap:
                g["_poster_snapshot"] = snap
        if row.status in ACTIVE_STATUSES:
            g["processing_count"] += 1
        if row.status == "completed" or getattr(row, "emby_item_id", None):
            g["in_library"] = True
        if g["emby_item_id"] is None and getattr(row, "emby_item_id", None):
            g["emby_item_id"] = row.emby_item_id
        g["vote_total"] += row.vote_count or 0
        if row.created_at and (g["latest_at"] is None or row.created_at > g["latest_at"]):
            g["latest_at"] = row.created_at
        if row.status == "pending":
            g["_pending"].append(row)

    # tmdb 命中库内条目（批量一次查完）：补 in_library / 海报 / 条目 id
    tmdb_ids = [g["tmdb_id"] for g in groups.values() if g["tmdb_id"]]
    if tmdb_ids:
        from backend.emby_server import models as em

        items = (
            db.query(em.MediaItem)
            .filter(em.MediaItem.tmdb_id.in_(tmdb_ids))
            .filter(em.MediaItem.deleted_at.is_(None))
            .filter(em.MediaItem.is_hidden == False)  # noqa: E712
            .order_by(em.MediaItem.date_added.desc())
            .all()
        )
        by_tmdb: dict[str, list] = {}
        for item in items:
            by_tmdb.setdefault(str(item.tmdb_id), []).append(item)
        for g in groups.values():
            if not g["tmdb_id"]:
                continue
            candidates = by_tmdb.get(g["tmdb_id"]) or []
            allowed = _TYPE_MATCH.get(str(g["type"] or ""))
            hit = None
            for item in candidates:
                if not allowed or item.item_type in allowed:
                    hit = item
                    break
            if hit is None:
                continue
            g["in_library"] = True
            if g["emby_item_id"] is None:
                g["emby_item_id"] = hit.guid
            g["poster_url"] = _plaza_poster_url(hit)

    pending_ids = [r.id for g in groups.values() for r in g["_pending"]]
    weighted_map = weighted_votes_for(db, pending_ids)
    voted = voted_ids(db, user_id, pending_ids)

    result: list[dict] = []
    for key in order:
        g = groups[key]
        # 库内海报优先；没有库内海报（库外作品）时用提交快照，
        # 与友站广场「库外作品也带 TMDB 海报」的展示口径一致。
        if g["poster_url"] is None:
            g["poster_url"] = g["_poster_snapshot"]
        del g["_poster_snapshot"]
        pending = sorted(
            g.pop("_pending"),
            key=lambda r: weighted_map.get(r.id, 0), reverse=True,
        )[:PLAZA_DETAIL_LIMIT]
        g["requests"] = [
            {
                "id": r.id,
                "movie_name": r.movie_name,
                "year": r.year,
                "type": r.type,
                "season_label": season_label(r.season),
                "title_only": bool(getattr(r, "title_only", False)),
                "request_kind": getattr(r, "request_kind", None) or "seek",
                "missing_text": _plaza_missing_text(r),
                "vote_count": r.vote_count or 0,
                "weighted_vote_count": weighted_map.get(r.id, 0),
                "voted": r.id in voted,
                "mine": r.user_id == user_id,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in pending
        ]
        g["latest_at"] = g["latest_at"].isoformat() if g["latest_at"] else ""
        result.append(g)
    result.sort(key=lambda g: (g["vote_total"], g["latest_at"]), reverse=True)
    return result[:PLAZA_GROUP_LIMIT]


# ==================== 作品请求详情（按作品汇集，2026-11-15） ====================

#: 作品详情里 movie_requests.status → 用户端统一状态名（与广场口径一致，
#: 撤回行不进详情——它在任何公开面都不出现）。
WORK_STATE_MAP = {
    "pending": "pending",
    "approved": "doing",
    "completed": "done",
    "rejected": "rejected",
}


def work_detail(db: Session, user, *, tmdb_id=None, name=None, year=None,
                media_type=None) -> dict:
    """作品请求详情：按作品汇集全部公开请求（求片反馈「作品页」的数据口）。

    身份口径与广场聚合一致：有 tmdb_id 按 tmdb_id 认亲；没有（原名
    兜底行）退回广场同一套分组键（归一化片名|年份|类型）。反馈工单
    没有媒体身份、不进本口（feedback 计数恒 0，与广场同一数据口径）。

    返回 ``{"work", "counts", "my_open", "requests"}``：
    - work：作品头（片名/年份/类型/海报/库内状态/库内条目 id）；
      海报先认库内条目、再认提交快照，都没有为 None；
    - counts：seek / chase / feedback / total 三分计数（撤回不计）；
    - my_open：当前用户未完结请求的 id 指针（{seek, chase}），
      详情页「同类型未完成拦截」直接读它，不用前端自己翻列表猜；
    - requests：全部非撤回行（创建时间倒序），带补充说明与我的附议态。
    """
    tmdb_text = normalize_tmdb_id(tmdb_id)
    user_id = getattr(user, "id", None)

    if tmdb_text:
        rows = (
            db.query(models.MovieRequest)
            .filter(
                models.MovieRequest.tmdb_id == tmdb_text,
                models.MovieRequest.status != WITHDRAWN_STATUS,
            )
            .order_by(models.MovieRequest.created_at.desc(),
                      models.MovieRequest.id.desc())
            .all()
        )
    else:
        wanted_key = "title:{}|{}|{}".format(
            _plaza_title_key(name), year or "", media_type or "")
        candidates = (
            db.query(models.MovieRequest)
            .filter(
                models.MovieRequest.tmdb_id.is_(None),
                models.MovieRequest.status != WITHDRAWN_STATUS,
                models.MovieRequest.movie_name.ilike(
                    f"%{_escape_like((name or '').strip())}%", escape="\\"),
            )
            .order_by(models.MovieRequest.created_at.desc(),
                      models.MovieRequest.id.desc())
            .limit(_PLAZA_ROW_LIMIT)
            .all()
        ) if (name or "").strip() else []
        rows = [r for r in candidates if _plaza_group_key(r) == wanted_key]

    head = rows[0] if rows else None
    work_name = (head.movie_name if head else (name or "")) or ""
    work_year = (head.year if head and head.year else year) or None
    work_type = (head.type if head and head.type else media_type) or None

    hit = library_hit(
        db,
        tmdb_id=tmdb_text or None,
        name=work_name,
        item_type=work_type,
        year=str(work_year) if work_year else None,
    ) if (tmdb_text or work_name) else None

    poster_url = _plaza_poster_url(hit) if hit is not None else None
    if poster_url is None:
        for r in rows:  # 行已按时间倒序：最新一条的快照
            poster_url = clean_poster_url(getattr(r, "poster_url", None))
            if poster_url:
                break

    # 补充说明批量取（一次查询，按时间正序挂回各行）
    supplements_by_request: dict[int, list] = {}
    if rows:
        sup_rows = (
            db.query(models.MovieRequestSupplement)
            .filter(models.MovieRequestSupplement.request_id.in_(
                [r.id for r in rows]))
            .order_by(models.MovieRequestSupplement.created_at,
                      models.MovieRequestSupplement.id)
            .all()
        )
        for s in sup_rows:
            supplements_by_request.setdefault(s.request_id, []).append({
                "id": s.id,
                "body": s.body,
                "mine": s.user_id == user_id,
                "created_at": s.created_at.isoformat() if s.created_at else "",
            })

    voted = voted_ids(db, user_id, [r.id for r in rows]) if user_id else set()

    counts = {"seek": 0, "chase": 0, "feedback": 0, "total": 0}
    my_open: dict[str, Optional[int]] = {"seek": None, "chase": None}
    payload_rows = []
    for r in rows:
        cls = kind_class(getattr(r, "request_kind", None))
        counts[cls] += 1
        counts["total"] += 1
        if (r.user_id == user_id and r.status in ACTIVE_STATUSES
                and my_open[cls] is None):
            my_open[cls] = r.id
        payload_rows.append({
            "id": r.id,
            "kind": cls,
            "request_kind": getattr(r, "request_kind", None) or "seek",
            "status": r.status,
            "state": WORK_STATE_MAP.get(r.status, "pending"),
            "movie_name": r.movie_name,
            "note": r.note,
            "season_label": season_label(r.season),
            "missing_text": _plaza_missing_text(r),
            "admin_note": r.admin_note,
            "vote_count": r.vote_count or 0,
            "voted": r.id in voted,
            "mine": r.user_id == user_id,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "supplements": supplements_by_request.get(r.id, []),
        })

    return {
        "work": {
            "tmdb_id": tmdb_text or (head.tmdb_id if head else None) or None,
            "name": work_name,
            "year": work_year,
            "type": work_type,
            "poster_url": poster_url,
            "in_library": hit is not None,
            "library_item_id": str(getattr(hit, "guid", "") or "") or None,
        },
        "counts": counts,
        "my_open": my_open,
        "requests": payload_rows,
    }


__all__ = [
    "ACTIVE_STATUSES",
    "CHASE_KINDS",
    "CONFIG_DAILY_LIMIT",
    "CONFIG_ENABLED",
    "CONFIG_NOTIFY_VOTERS",
    "CONFIG_VOTE_ENABLED",
    "DEFAULT_DAILY_LIMIT",
    "KIND_CHASE",
    "MAX_NOTE_LENGTH",
    "MAX_SEASON_COUNT",
    "MAX_SEASON_NUMBER",
    "SERIES_TYPES",
    "SEASON_ALL",
    "WITHDRAWN_STATUS",
    "clean_poster_url",
    "daily_limit",
    "find_library_match",
    "kind_class",
    "library_hit",
    "normalize_season",
    "normalize_tmdb_id",
    "notify_voters_enabled",
    "open_request_flags",
    "plaza_groups",
    "quota",
    "search_candidates",
    "season_label",
    "seasons_for",
    "seek_enabled",
    "work_detail",
    "CONFIG_DAILY_VOTE_LIMIT",
    "DEFAULT_DAILY_VOTE_LIMIT",
    "daily_vote_limit",
    "daily_votes_used",
    "ensure_vote",
    "toggle_vote",
    "used_today",
    "vote_enabled",
    "vote_weight_for_user",
    "voted_ids",
    "voter_ids",
    "weighted_vote_ordering",
    "weighted_votes_for",
]
