# -*- coding: utf-8 -*-
"""团队服务（2026-11-18 重建批 1，用户拍板口径）。

形态照友站 UHDNow（说明书 team-rebuild-spec-draft-20261118 §8 实测）：
用户自选线路/流量/时长 → 结算预览 → 钱包余额支付 → 建团
``pending_activation``（待开通播放权限），管理端开通后转 ``active``。
不复活 2026-11-16 已删除的申请制/邀请码制旧实现。

定价（用户 2026-11-18 点选「友站三分之一」，初始值、全部可配）：
- Global：锚点表（默认 10,000GB→100 分/千GB、20,000GB→98 分/千GB、
  1,000,000GB→90 分/千GB），锚点间线性插值、区间外钳到端点价；
- Premium：恒定单价（默认 400 分/千GB）；
- 起订 Global 10,000GB / Premium 5,000GB，上限 1,000,000GB，
  时长 1–12 个月线性无折扣，同时播放路数只记录不计价；
- 子账户上限＝流量GB ÷ team_sub_account_divisor（默认 100）。
金额一律美分整数（*_fen）计价，钱包扣款时换算成 Decimal 美元。

旧套餐抵扣：复用现行订阅购买的唯一公式 ``economy._switch_credit``
（传哨兵 plan 只读 id/realm_id 两字段走 switch 分支，不复制计价）；
被抵扣的旧个人订阅在履约时置到期，与现行换套餐链
（economy._fulfill_order 先把旧订阅行 end_date 置 now）同口径。

成员：团长创建的成员子账号＝独立 WebUser（parent_user_id 指团长，
与家庭子账号同机制）＋ team_members 归属行。成员禁一切经济动作
（sub_accounts.forbid_sub_account 按 parent 判定，自动生效）、
禁再建团、禁管理成员。

计量归账（§7.4 归账跟随资格来源）：``usage_attribution_user_id``
是播放计量归属的团队感知解析口——成员所属团队 active 且未到期时
流量记成员本人（团队池），其余情形回落 sub_accounts 的有效账号
解析，与现行口径逐字一致。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models, sub_accounts, traffic_quota, usernames
from backend import wallet as wallet_service
from backend.agents import money
from backend.db_retry import commit_with_retry
from backend.integrations import store
from backend.security import hash_password

logger = logging.getLogger(__name__)

GB_BYTES = 1024 ** 3
LINES = ("global", "premium")
LINE_NAMES = {"global": "Global 国际版", "premium": "Premium 高级版"}

#: Global 单价锚点表默认值（友站实测价的三分之一，美分/千GB）
DEFAULT_GLOBAL_ANCHORS: list[dict] = [
    {"gb": 10000, "per_1000gb_fen": 100},
    {"gb": 20000, "per_1000gb_fen": 98},
    {"gb": 1000000, "per_1000gb_fen": 90},
]

TEAM_CONFIG_DEFAULTS: dict[str, str] = {
    "team_purchase_enabled": "true",
    "team_global_min_traffic_gb": "10000",
    "team_premium_min_traffic_gb": "5000",
    "team_max_traffic_gb": "1000000",
    "team_premium_price_per_1000gb_fen": "400",
    "team_global_price_anchors": json.dumps(DEFAULT_GLOBAL_ANCHORS),
    "team_sub_account_divisor": "100",
}

_TRUE_WORDS = ("1", "true", "yes", "on")


class TeamError(Exception):
    """团队业务错误：``code`` 供接口层映射 HTTP 状态，消息可直接展示。"""

    def __init__(self, message: str, code: str = "team_error"):
        super().__init__(message)
        self.code = code


# ==================== 配置 ====================

def _parse_bool(raw: str, default: bool) -> bool:
    text = (raw or "").strip().lower()
    if not text:
        return default
    return text in _TRUE_WORDS


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def parse_anchors(raw) -> Optional[list[dict]]:
    """解析锚点表（JSON 串或已解析对象）；结构合法返回归一化列表，
    否则返回 None。合法＝非空 list、每项 gb 为正整数且严格递增、
    per_1000gb_fen 为非负整数。"""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or not data:
        return None
    anchors: list[dict] = []
    prev_gb = 0
    for item in data:
        if not isinstance(item, dict):
            return None
        gb = item.get("gb")
        price = item.get("per_1000gb_fen")
        if (not isinstance(gb, int) or isinstance(gb, bool) or gb <= prev_gb
                or not isinstance(price, int) or isinstance(price, bool)
                or price < 0):
            return None
        anchors.append({"gb": gb, "per_1000gb_fen": price})
        prev_gb = gb
    return anchors


def validate_anchors(raw) -> list[dict]:
    """管理端保存口的锚点校验：非法整批拒绝（TeamError）。"""
    anchors = parse_anchors(raw)
    if anchors is None:
        raise TeamError(
            "Global 单价锚点表格式不正确：应为非空数组，每项含 gb（正整数、"
            "严格递增）与 per_1000gb_fen（非负整数，美分）",
            "bad_anchors")
    return anchors


def team_config(db: Session) -> dict:
    """团队配置的唯一读取口（ttl=0 直查，改配即生效；坏值回退代码默认）。"""
    raw = {
        key: store.get_value(db, key, default, ttl=0)
        for key, default in TEAM_CONFIG_DEFAULTS.items()
    }
    anchors = parse_anchors(raw["team_global_price_anchors"])
    if anchors is None:
        logger.warning("team_global_price_anchors 配置非法，回退默认锚点表")
        anchors = [dict(a) for a in DEFAULT_GLOBAL_ANCHORS]
    return {
        "purchase_enabled": _parse_bool(
            raw["team_purchase_enabled"],
            TEAM_CONFIG_DEFAULTS["team_purchase_enabled"] == "true"),
        "global_min_gb": _parse_int(raw["team_global_min_traffic_gb"], 10000),
        "premium_min_gb": _parse_int(raw["team_premium_min_traffic_gb"], 5000),
        "max_gb": _parse_int(raw["team_max_traffic_gb"], 1000000),
        "premium_price_per_1000gb_fen": _parse_int(
            raw["team_premium_price_per_1000gb_fen"], 400),
        "global_anchors": anchors,
        "sub_account_divisor": max(
            1, _parse_int(raw["team_sub_account_divisor"], 100)),
    }


def config_payload(db: Session) -> dict:
    """配置的对外形态（用户端创建弹窗与管理端设置共用同一份）。"""
    cfg = team_config(db)
    return {
        "team_purchase_enabled": cfg["purchase_enabled"],
        "team_global_min_traffic_gb": cfg["global_min_gb"],
        "team_premium_min_traffic_gb": cfg["premium_min_gb"],
        "team_max_traffic_gb": cfg["max_gb"],
        "team_premium_price_per_1000gb_fen":
            cfg["premium_price_per_1000gb_fen"],
        "team_global_price_anchors": cfg["global_anchors"],
        "team_sub_account_divisor": cfg["sub_account_divisor"],
    }


# ==================== 计价 ====================

def _unit_price_fen(line: str, traffic_gb: int, cfg: dict) -> Decimal:
    """每千 GB 单价（美分，Decimal 保留插值精度，最终月价再取整）。"""
    if line == "premium":
        return Decimal(cfg["premium_price_per_1000gb_fen"])
    anchors = cfg["global_anchors"]
    if traffic_gb <= anchors[0]["gb"]:
        return Decimal(anchors[0]["per_1000gb_fen"])
    if traffic_gb >= anchors[-1]["gb"]:
        return Decimal(anchors[-1]["per_1000gb_fen"])
    for low, high in zip(anchors, anchors[1:]):
        if low["gb"] <= traffic_gb <= high["gb"]:
            frac = (Decimal(traffic_gb - low["gb"])
                    / Decimal(high["gb"] - low["gb"]))
            return (Decimal(low["per_1000gb_fen"])
                    + (Decimal(high["per_1000gb_fen"])
                       - Decimal(low["per_1000gb_fen"])) * frac)
    return Decimal(anchors[-1]["per_1000gb_fen"])  # 不可达，兜底


def _sentinel_plan() -> models.SubscriptionPlan:
    """抵扣公式的哨兵 plan：_switch_credit 只读 plan.id / plan.realm_id。

    id=-1 永不等于任何真实套餐 → 有生效订阅时必走 switch 分支；
    realm_id=None → 按现行口径跨服取 end_date 最新的一份。
    瞬态对象、永不入库。
    """
    return models.SubscriptionPlan(
        id=-1, name="团队套餐（抵扣计算）", price=0, duration_days=30)


def _quote_full(db: Session, user: Optional[models.WebUser], *,
                line: str, traffic_gb: int, months: int) -> dict:
    cfg = team_config(db)
    if line not in LINES:
        raise TeamError("线路不正确", "bad_line")
    if (not isinstance(traffic_gb, int) or isinstance(traffic_gb, bool)
            or not isinstance(months, int) or isinstance(months, bool)):
        raise TeamError("流量与时长必须是整数", "bad_input")
    min_gb = cfg["global_min_gb"] if line == "global" else cfg["premium_min_gb"]
    if traffic_gb < min_gb:
        raise TeamError(
            f"{LINE_NAMES[line]}起订 {min_gb:,} GB", "traffic_below_min")
    if traffic_gb > cfg["max_gb"]:
        raise TeamError(
            f"流量上限 {cfg['max_gb']:,} GB", "traffic_above_max")
    if months < 1 or months > 12:
        raise TeamError("时长须在 1–12 个月之间", "bad_months")

    unit = _unit_price_fen(line, traffic_gb, cfg)
    monthly_fen = int((unit * traffic_gb / 1000).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    list_fen = monthly_fen * months

    credit_fen = 0
    switch_type = "new"
    old_sub = None
    if user is not None:
        from backend.api import economy as economy_api
        credit, switch_type, old_sub = economy_api._switch_credit(
            db, user, _sentinel_plan())
        credit_fen = int((money(credit) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    return {
        "line": line,
        "traffic_gb": traffic_gb,
        "traffic_bytes": traffic_gb * GB_BYTES,
        "months": months,
        "unit_price_fen_per_1000gb": int(unit.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)),
        "monthly_price_fen": monthly_fen,
        "list_price_fen": list_fen,
        "switch_credit_fen": credit_fen,
        "switch_type": switch_type,
        "total_fen": max(0, list_fen - credit_fen),
        "sub_account_limit": traffic_gb // cfg["sub_account_divisor"],
        "_old_sub": old_sub,
    }


def quote(db: Session, user: Optional[models.WebUser], *,
          line: str, traffic_gb: int, months: int) -> dict:
    """结算预览（公开形态，去掉内部履约字段）。"""
    result = _quote_full(db, user, line=line, traffic_gb=traffic_gb,
                         months=months)
    result.pop("_old_sub", None)
    return result


# ==================== 团队查询 ====================

def get_owned_team(db: Session, user_id: int) -> Optional[models.Team]:
    """团长名下未解散的团队（一人至多一个，服务层保证）。"""
    return (db.query(models.Team)
            .filter(models.Team.owner_id == int(user_id),
                    models.Team.status != "dissolved")
            .order_by(models.Team.id.desc())
            .first())


def get_membership(db: Session, user_id: int) -> Optional[models.TeamMember]:
    return (db.query(models.TeamMember)
            .filter(models.TeamMember.user_id == int(user_id))
            .first())


def get_user_team(db: Session, user_id: int) -> tuple[Optional[models.Team],
                                                      Optional[str]]:
    """用户视角的团队：先看自己是不是团长，再看成员归属。"""
    team = get_owned_team(db, user_id)
    if team is not None:
        return team, "owner"
    row = get_membership(db, user_id)
    if row is not None:
        team = db.get(models.Team, row.team_id)
        if team is not None and team.status != "dissolved":
            return team, row.role
    return None, None


def active_member_count(db: Session, team_id: int) -> int:
    """占名额的成员数：成员行 × 账号仍启用（软禁用即释放名额，
    与家庭子账号 active_children_count 同口径）。"""
    return (db.query(func.count(models.TeamMember.id))
            .join(models.WebUser,
                  models.WebUser.id == models.TeamMember.user_id)
            .filter(models.TeamMember.team_id == int(team_id),
                    models.TeamMember.role == "member",
                    models.WebUser.is_active.is_(True))
            .scalar() or 0)


def team_period(team: models.Team, now: Optional[datetime] = None):
    """团队当前流量周期 [start, end)（锚点＝套餐起始日，复用
    traffic_quota.period_bounds 的钳月末算法）。"""
    anchor = team.plan_period_anchor_day
    if not anchor:
        anchor = team.plan_started_at.day if team.plan_started_at else 1
    ref = (now or datetime.now()).date()
    return traffic_quota.period_bounds(anchor, ref)


def member_usage_bytes(db: Session, team: models.Team, user_id: int,
                       now: Optional[datetime] = None) -> int:
    """成员本人在团队当前周期内的计量字节（日账 relay+local 求和）。"""
    from backend.emby_server import models as em
    start, end = team_period(team, now)
    total = (db.query(func.coalesce(
        func.sum(em.TrafficUsageDaily.relay_bytes
                 + em.TrafficUsageDaily.local_bytes), 0))
        .filter(em.TrafficUsageDaily.user_id == int(user_id),
                em.TrafficUsageDaily.day >= start,
                em.TrafficUsageDaily.day < end)
        .scalar())
    return int(total or 0)


# ==================== 载荷 ====================

def team_payload(db: Session, team: models.Team) -> dict:
    owner = db.get(models.WebUser, team.owner_id)
    return {
        "id": team.id,
        "name": team.name or "",
        "owner_id": team.owner_id,
        "owner_username": owner.username if owner else None,
        "status": team.status,
        "plan_line": team.plan_line,
        "plan_traffic_gb": team.plan_traffic_gb,
        "plan_traffic_bytes": team.plan_traffic_bytes,
        "plan_member_limit": team.plan_member_limit,
        "plan_streams": team.plan_streams,
        "plan_price_fen": team.plan_price_fen,
        "plan_started_at": team.plan_started_at.isoformat()
            if team.plan_started_at else None,
        "plan_expires_at": team.plan_expires_at.isoformat()
            if team.plan_expires_at else None,
        "member_count": active_member_count(db, team.id),
        "created_at": team.created_at.isoformat() if team.created_at else None,
    }


def member_payload(db: Session, team: models.Team, row: models.TeamMember,
                   now: Optional[datetime] = None) -> dict:
    """成员列表一行：友站表头 成员/有效期/流量/使用情况/重置日/操作 的
    后端数据面。流量＝团队总流量按子账户上限均分（配额自动计算的
    展示口径；实际用量走团队池共享，见模块 docstring）。"""
    user = db.get(models.WebUser, row.user_id)
    limit = team.plan_member_limit or 0
    quota = (team.plan_traffic_bytes or 0) // limit if limit > 0 else 0
    used = member_usage_bytes(db, team, row.user_id, now)
    start, end = team_period(team, now)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": user.username if user else None,
        "public_id": user.public_id if user else None,
        "role": row.role,
        "is_active": bool(user.is_active) if user else False,
        "joined_at": row.joined_at.isoformat() if row.joined_at else None,
        "expires_at": team.plan_expires_at.isoformat()
            if team.plan_expires_at else None,
        "traffic_quota_bytes": quota,
        "traffic_used_bytes": used,
        "traffic_used_pct": round(used * 100.0 / quota, 1) if quota else None,
        "period_start": start.isoformat(),
        "period_end": (end - timedelta(days=1)).isoformat(),
        "reset_date": end.isoformat(),
    }


def order_payload(order: models.TeamOrder) -> dict:
    return {
        "order_id": order.order_id,
        "team_id": order.team_id,
        "line": order.line,
        "traffic_gb": order.traffic_gb,
        "months": order.months,
        "streams": order.streams,
        "list_price_fen": order.list_price_fen,
        "credit_fen": order.credit_fen,
        "amount_fen": order.amount_fen,
        "status": order.status,
        "created_at": order.created_at.isoformat()
            if order.created_at else None,
    }


# ==================== 购买 ====================

def _find_order(db: Session, order_id: str,
                user_id: int) -> Optional[models.TeamOrder]:
    return (db.query(models.TeamOrder)
            .filter(models.TeamOrder.order_id == order_id,
                    models.TeamOrder.user_id == int(user_id))
            .first())


def _purchase_result(db: Session, order: models.TeamOrder) -> dict:
    team = db.get(models.Team, order.team_id) if order.team_id else None
    balance = wallet_service.get_balance(db, order.user_id)
    return {
        "order": order_payload(order),
        "team": team_payload(db, team) if team else None,
        "balance_after_fen": int((balance * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)),
    }


def purchase(db: Session, user: models.WebUser, *,
             line: str, traffic_gb: int, months: int, streams: int = 0,
             name: Optional[str] = None,
             idempotency_key: str) -> dict:
    """确认并支付：钱包扣款 → 建团/升级 → 订单落账，一个事务。

    幂等：order_id＝"TM"+idempotency_key，同键重放返回原单结果、
    不重复扣款（与钱包订阅购买同口径）。被抵扣的旧个人订阅在
    履约时置到期（现行换套餐链口径）。
    """
    if sub_accounts.is_sub_account(user):
        raise TeamError("子账号不能购买团队套餐", "forbidden_sub_account")
    cfg = team_config(db)
    if not cfg["purchase_enabled"]:
        raise TeamError("团队购买暂未开放", "team_purchase_disabled")
    if not wallet_service.wallet_enabled(db):
        raise TeamError("钱包功能暂未开放", "wallet_disabled")
    key = (idempotency_key or "").strip()
    if not key or len(key) > 60:
        raise TeamError("缺少有效的确认标识", "bad_idempotency_key")
    if (not isinstance(streams, int) or isinstance(streams, bool)
            or streams < 0):
        raise TeamError("同时播放路数不正确", "bad_streams")
    order_id = "TM" + key

    from backend.api import economy as economy_api
    economy_api.lock_user_row(db, user.id)

    existing = _find_order(db, order_id, user.id)
    if existing is not None:
        return _purchase_result(db, existing)

    priced = _quote_full(db, user, line=line, traffic_gb=traffic_gb,
                          months=months)
    payable = Decimal(priced["total_fen"]) / 100
    balance = wallet_service.get_balance(db, user.id)
    if payable > balance:
        shortfall = money(payable - balance)
        raise TeamError(
            f"钱包余额不足，还差 ${shortfall}（团队套餐需 ${money(payable)}，"
            f"当前余额 ${money(balance)}）",
            "insufficient_balance")

    if payable > 0:
        try:
            wallet_service.debit(
                db, user.id, payable, kind="team_purchase",
                ref_id=order_id, note=f"团队套餐 {LINE_NAMES[line]}",
                idempotency_key=key)
        except wallet_service.InsufficientWalletBalance:
            db.rollback()
            raise TeamError("钱包余额不足", "insufficient_balance")

    now = datetime.now()
    days = economy_api.months_grant_days(months)
    team = get_owned_team(db, user.id)
    if team is None:
        team = models.Team(owner_id=user.id, name="")
        db.add(team)
        db.flush()
        db.add(models.TeamMember(
            team_id=team.id, user_id=user.id, role="owner"))
    was_active = (team.status == "active" and team.plan_expires_at
                  and team.plan_expires_at > now)
    if was_active:
        # 续订/升级且团队在期：有效期顺延，起始与锚点不动（周期连续）
        team.plan_expires_at = team.plan_expires_at + timedelta(days=days)
    else:
        team.plan_started_at = now
        team.plan_expires_at = now + timedelta(days=days)
        team.plan_period_anchor_day = now.day
        if team.status != "active":
            team.status = "pending_activation"
    team.plan_line = line
    team.plan_traffic_gb = traffic_gb
    team.plan_traffic_bytes = priced["traffic_bytes"]
    team.plan_member_limit = priced["sub_account_limit"]
    team.plan_streams = streams
    team.plan_price_fen = priced["list_price_fen"]
    if name is not None and name.strip():
        team.name = name.strip()[:100]

    order = models.TeamOrder(
        order_id=order_id, user_id=user.id, team_id=team.id, line=line,
        traffic_gb=traffic_gb, months=months, streams=streams,
        list_price_fen=priced["list_price_fen"],
        credit_fen=priced["switch_credit_fen"],
        amount_fen=priced["total_fen"], status="paid",
        idempotency_key=key)
    db.add(order)

    old_sub = priced.get("_old_sub")
    if priced["switch_type"] == "switch" and old_sub is not None:
        # 抵扣＝把旧套餐剩余价值折现换购：旧订阅随之到期
        # （与 economy._fulfill_order 的换套餐口径一致）。
        old_sub.end_date = now

    try:
        commit_with_retry(db, label="团队购买")
    except IntegrityError:
        db.rollback()
        replay = _find_order(db, order_id, user.id)
        if replay is not None:
            return _purchase_result(db, replay)
        raise
    logger.info("团队购买成功: user=%s team=%s order=%s amount_fen=%s",
                user.id, team.id, order_id, priced["total_fen"])
    return _purchase_result(db, order)


# ==================== 成员管理 ====================

def _require_owned_team(db: Session, user: models.WebUser) -> models.Team:
    if sub_accounts.is_sub_account(user):
        raise TeamError("子账号不能管理团队成员", "forbidden_sub_account")
    team = get_owned_team(db, user.id)
    if team is None:
        raise TeamError("请先开通团队套餐", "no_team")
    return team


def create_member(db: Session, owner: models.WebUser, *,
                  username: str, password: str) -> models.WebUser:
    """团长创建成员子账号：WebUser(parent=团长)＋team_members 归属行。

    用户名/密码/撞名口径与家庭子账号（sub_accounts）同源；上限按
    团队子账户配额数成员行，不走家庭套餐的 sub_account_limit。
    """
    team = _require_owned_team(db, owner)
    uname = (username or "").strip()
    if not sub_accounts.USERNAME_RE.fullmatch(uname):
        raise TeamError("用户名需为 3–32 位字母、数字或下划线",
                        "invalid_username")
    if not password or len(password) < sub_accounts.PASSWORD_MIN_LENGTH:
        raise TeamError(
            f"密码至少 {sub_accounts.PASSWORD_MIN_LENGTH} 位", "weak_password")
    if usernames.find_users_by_username_ci(db, uname):
        raise TeamError("该用户名已被占用", "username_taken")
    if usernames.username_conflict(db, uname):
        raise TeamError("该用户名已被占用", "username_taken")
    limit = team.plan_member_limit or 0
    if active_member_count(db, team.id) >= limit:
        raise TeamError(
            f"团队成员已达上限（{limit} 个子账号）", "limit_reached")
    member = models.WebUser(
        username=uname, password_hash=hash_password(password),
        parent_user_id=owner.id, is_active=True)
    db.add(member)
    db.flush()
    db.add(models.TeamMember(
        team_id=team.id, user_id=member.id, role="member"))
    commit_with_retry(db, label="创建团队成员")
    logger.info("团队成员已创建: team=%s owner=%s member=%s",
                team.id, owner.id, member.id)
    return member


def remove_member(db: Session, owner: models.WebUser, *,
                  member_user_id: int) -> None:
    """移除成员：删归属行＋软禁用账号（账号为团队专属创建，移除后
    不得再以团长子账号身份留存，与家庭子账号删除同口径）。"""
    team = _require_owned_team(db, owner)
    row = (db.query(models.TeamMember)
           .filter(models.TeamMember.team_id == team.id,
                   models.TeamMember.user_id == int(member_user_id),
                   models.TeamMember.role == "member")
           .first())
    if row is None:
        raise TeamError("成员不存在", "member_not_found")
    try:
        sub_accounts.disable_sub_account(db, owner, int(member_user_id))
    except sub_accounts.SubAccountError as exc:
        raise TeamError(str(exc), exc.code)
    db.delete(row)
    commit_with_retry(db, label="移除团队成员")
    logger.info("团队成员已移除: team=%s member=%s", team.id, member_user_id)


def rename_team(db: Session, owner: models.WebUser, *,
                name: str) -> models.Team:
    team = _require_owned_team(db, owner)
    cleaned = (name or "").strip()
    if not cleaned:
        raise TeamError("团队名不能为空", "bad_name")
    team.name = cleaned[:100]
    commit_with_retry(db, label="团队改名")
    return team


def list_members(db: Session, team: models.Team, *, q: str = "",
                 status: str = "all", page: int = 1,
                 page_size: int = 20) -> tuple[list[dict], int]:
    """成员列表（只含 role=member 行）：按名称搜索、按账号状态筛选、
    分页。团长本人行不在成员表内展示（友站表头是「成员账号」）。"""
    query = (db.query(models.TeamMember)
             .join(models.WebUser,
                   models.WebUser.id == models.TeamMember.user_id)
             .filter(models.TeamMember.team_id == team.id,
                     models.TeamMember.role == "member"))
    if q:
        query = query.filter(
            models.WebUser.username.ilike(f"%{q.strip()}%"))
    if status == "active":
        query = query.filter(models.WebUser.is_active.is_(True))
    elif status == "disabled":
        query = query.filter(models.WebUser.is_active.is_(False))
    total = query.count()
    rows = (query.order_by(models.TeamMember.id.asc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return [member_payload(db, team, row) for row in rows], total


# ==================== 管理端 ====================

def activate_team(db: Session, team: models.Team) -> models.Team:
    """开通播放权限：pending_activation → active（状态机唯一入口）。"""
    if team.status == "active":
        raise TeamError("团队已开通播放权限", "already_active")
    if team.status != "pending_activation":
        raise TeamError("团队状态不允许开通", "bad_status")
    team.status = "active"
    if team.plan_started_at is None:
        team.plan_started_at = datetime.now()
    commit_with_retry(db, label="团队开通播放权限")
    return team


# ==================== 计量归账 ====================

def usage_attribution_user_id(db: Session, user) -> int:
    """播放计量归属解析（团队感知）：成员所属团队 active 且在期 →
    记成员本人（团队池）；其余回落有效账号解析（子账号归主账号、
    普通用户归本人），与现行口径一致。

    供 emby_server video_stream 的 set_current_user 调用，签名与
    sub_accounts.effective_user_id 对齐（user 可为对象或 id）。
    """
    uid = user.id if isinstance(user, models.WebUser) else int(user)
    row = (db.query(models.TeamMember)
           .filter(models.TeamMember.user_id == uid,
                   models.TeamMember.role == "member")
           .first())
    if row is not None:
        team = db.get(models.Team, row.team_id)
        if (team is not None and team.status == "active"
                and team.plan_expires_at is not None
                and team.plan_expires_at > datetime.now()):
            return uid
    return sub_accounts.effective_user_id(db, user)
