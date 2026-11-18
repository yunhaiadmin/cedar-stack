# -*- coding: utf-8 -*-
"""家庭子账号：有效账号解析的唯一实现 + 子账号生命周期服务。

用户 2026-11-15 亲令：订阅照友站走，家庭版含 2 个子账号、流量共享。
友站只公开了卖点，机制细节按下述推导口径执行（本模块是唯一出处，
各消费点不许自己再判一遍「是不是子账号」）：

- 子账号是独立 WebUser（``parent_user_id`` 指向主账号）：自己的
  用户名/密码/登录、自己的观看记录与资料。只能由主账号在用户
  中心创建，不走开放注册；子账号不能再建子账号（不许套娃）。
- 子账号没有自己的订阅与钱包：订阅有效性、月流量配额与用量
  归账、求片月额度等权益一律按主账号算。实现上就是本模块的
  「有效账号解析」——``effective_user`` / ``effective_user_id``
  把子账号解析成主账号，各消费点解析后再走既有单口（订阅走
  ``subscriptions``、权益走 ``entitlements``、绑定走
  ``playback_gate``、流量走 ``traffic_quota``），不新造第二套判定。
- 播放门禁随主账号：绑定门禁与订阅校验都看主账号——主账号有
  active 绑盘/池资格且订阅有效，子账号即放行；子账号自己不绑盘。
- 主账号订阅过期/降档，子账号同步不可播：这是「权益读主账号
  当前套餐」的机械结果，不另设规则。降档到 limit 变小，已建子
  账号不强制删除，但不可再新建（limit 按当前套餐现算）；降到
  无子账号权益的档（当前套餐 sub_account_limit=0）时，子账号
  同步不可播——播放资格里同样含一条「主账号当前套餐声明了
  子账号权益」的判定（落在 subscriptions.can_play 对子账号的
  追加校验），与创建上限读同一个键、同一个单口。
- 子账号禁止一切经济动作：购买订阅、购流量、时间换流量、充值
  下单、兑换（卡码/余额码/会员码）、代理进货——用户侧接口一律
  403（``forbid_sub_account``），前端入口同步隐藏。
- 删除 = 软禁用：主账号删除子账号只把 ``is_active`` 置 False 并
  递增 ``token_version``（已发 token 立即作废），记录保留备查；
  被禁用的子账号不占可建名额（limit 数的是在用子账号），主账号
  可再建新的补上。
- 可建上限 = 主账号当前套餐 entitlements 的 ``sub_account_limit``
  （经 ``entitlements`` 单口读，未声明按 0；国际版 0、家庭版 2）。
  创建还要求主账号当前订阅有效——订阅过期不许再往家庭里加人。
- 团队成员子账号（backend/teams，parent 同为团长、另带
  team_members 归属行）不是家庭子账号：不占 ``sub_account_limit``
  名额、不进家庭管理列表，名额与资格全归团队侧（2026-11-18
  团队批 4）。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend import entitlements, models, subscriptions
from backend.db_retry import commit_with_retry
from backend.security import hash_password
from backend.usernames import find_users_by_username_ci, username_conflict

logger = logging.getLogger(__name__)

#: 子账号用户名规则：与开放注册口 USERNAME_RE 逐字一致
#: （backend/api/emby_portal.py），不许两套命名规则。
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")

#: 子账号密码下限：与开放注册口 RegisterRequest 的 min_length 一致。
PASSWORD_MIN_LENGTH = 6

#: 有效账号解析跟随 parent 链的深度上限：正常数据只有一层（子账号
#: 不能再建子账号），上限只防脏数据成环时无限循环。
_MAX_PARENT_HOPS = 5


class SubAccountError(Exception):
    """子账号服务层错误：``code`` 是稳定类别键，API 层据此映射状态码。"""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 有效账号解析（全站唯一实现）
# ---------------------------------------------------------------------------

def is_sub_account(user) -> bool:
    """该用户是不是子账号（只看对象上的 parent_user_id，不查库）。"""
    return user is not None and getattr(user, "parent_user_id", None) is not None


def _single_col(row, name):
    """读单列查询返回的一行：真 SQLAlchemy Row 与测试桩对象都兼容。

    真 Row 既支持下标也支持属性访问；部分旧测试的桩 DB
    （如 ``_stub_stream`` 的 ``_DB``）的 ``first()``/``all()``
    返回 ``types.SimpleNamespace``，只支持属性访问、不支持下标。
    这里先取属性、再试下标，两种形态都读得到。
    """
    if row is None:
        return None
    value = getattr(row, name, None)
    if value is not None:
        return value
    try:
        return row[0]
    except (TypeError, IndexError, KeyError):
        return None


def _root_id(db: Session, user_id: int) -> int:
    """沿 parent_user_id 跟到主账号 id（脏数据成环时在深度上限处停）。"""
    current = int(user_id)
    seen = {current}
    for _ in range(_MAX_PARENT_HOPS):
        row = (db.query(models.WebUser.parent_user_id)
               .filter(models.WebUser.id == current).first())
        parent_id = _single_col(row, "parent_user_id")
        if parent_id is None:
            return current
        parent_id = int(parent_id)
        if parent_id in seen:
            logger.warning("子账号 parent 链成环，停在 user=%s", current)
            return current
        seen.add(parent_id)
        current = parent_id
    logger.warning("子账号 parent 链超过 %s 层，停在 user=%s",
                   _MAX_PARENT_HOPS, current)
    return current


def effective_user_id(db: Session, user_or_id) -> Optional[int]:
    """有效账号 id：子账号 → 主账号 id；主账号 → 自己。

    ``user_or_id`` 可传 WebUser 对象或 user_id。对象已带
    parent_user_id 时直接跟随（省一次查询）；只给 id 时查库解析。
    权益/门禁/计量等一切「按谁算」的消费点先过这里，再走既有单口。
    取不到 id（None / 无 id 的桩对象）返回 None——与播放链路既有
    的 getattr(user, "id", None) 容错口径一致，调用方按无归属处理。
    """
    if user_or_id is None:
        return None
    if isinstance(user_or_id, int):
        return _root_id(db, user_or_id)
    uid = getattr(user_or_id, "id", None)
    if uid is None:
        return None
    parent_id = getattr(user_or_id, "parent_user_id", None)
    if parent_id is None:
        return int(uid)
    return _root_id(db, int(parent_id))


def effective_user(db: Session, user: models.WebUser) -> models.WebUser:
    """有效账号对象：子账号 → 主账号行；主账号 → 自己。

    主账号行不存在（数据异常）时回退用户本人并记 warning——解析
    失败不许把人凭空变成无订阅，也不能抛错拖垮播放/展示路径。
    """
    root_id = effective_user_id(db, user)
    if root_id == int(user.id):
        return user
    parent = (db.query(models.WebUser)
              .filter(models.WebUser.id == root_id).first())
    if parent is None:
        logger.warning("子账号 %s 的主账号 %s 不存在，按本人处理",
                       user.id, root_id)
        return user
    return parent


def family_user_ids(db: Session, user_or_id) -> list[int]:
    """家庭全体 id：[主账号, 子账号...]（按 id 升序）。

    给「全家共用一份额度」的用量计数用（如求片月度已用量：主账号
    与每个子账号提交的都算进同一份套餐额度）。
    """
    root_id = effective_user_id(db, user_or_id)
    child_ids = [int(_single_col(r, "id")) for r in (db.query(models.WebUser.id)
                                .filter(models.WebUser.parent_user_id == root_id)
                                .order_by(models.WebUser.id.asc()).all())]
    return [root_id, *child_ids]


def children_of(db: Session, parent_id: int) -> list[models.WebUser]:
    """某主账号名下的全部子账号（含已禁用的，按创建时间升序）。"""
    return (db.query(models.WebUser)
            .filter(models.WebUser.parent_user_id == int(parent_id))
            .order_by(models.WebUser.created_at.asc(), models.WebUser.id.asc())
            .all())


def sub_account_limit(db: Session, user) -> int:
    """该用户（按有效账号）当前套餐声明的子账号可建上限，缺省 0。

    经 entitlements 单口读主账号当前套餐的 ``sub_account_limit``；
    未声明（无订阅/缺键）一律 0——没有声明就是没有这个卖点。
    """
    root_id = effective_user_id(db, user)
    declared = entitlements.get_user_entitlement(
        db, root_id, "sub_account_limit")
    return int(declared) if declared is not None else 0


def _team_member_ids(db: Session) -> set[int]:
    """team_members 归属（role=member）的账号 id 集。

    团队成员同为 parent 挂靠的 WebUser，但不是家庭子账号：名额
    与管理都归团队侧（backend/teams，名额读 plan_member_limit），
    家庭侧的计数与列表必须排除，否则团长建成员会吃掉家庭版
    名额（2026-11-18 团队批 4，两套产品不混算）。
    """
    rows = (db.query(models.TeamMember.user_id)
            .filter(models.TeamMember.role == "member")
            .all())
    return {int(_single_col(r, "user_id")) for r in rows
            if _single_col(r, "user_id") is not None}


def active_children_count(db: Session, parent_id: int) -> int:
    """在用（未禁用）子账号数：limit 数的是这个口径（见模块 docstring）。

    团队成员子账号不计（见 _team_member_ids）。
    """
    query = (db.query(models.WebUser)
             .filter(models.WebUser.parent_user_id == int(parent_id),
                     models.WebUser.is_active == True))  # noqa: E712
    team_ids = _team_member_ids(db)
    if team_ids:
        query = query.filter(models.WebUser.id.notin_(team_ids))
    return query.count()


# ---------------------------------------------------------------------------
# 生命周期：创建 / 删除 / 经济动作拦截
# ---------------------------------------------------------------------------

def create_sub_account(db: Session, parent: models.WebUser,
                       username: str, password: str) -> models.WebUser:
    """主账号创建一个子账号（校验顺序即报错优先级，见每条注释）。

    - 调用者是子账号 → 拒（子账号不能再建子账号）；
    - 主账号无生效中订阅 → 拒（订阅过期不许往家庭里加人）；
    - 在用子账号数已达当前套餐 sub_account_limit → 拒（limit=0
      的套餐在这里自然恒拒，这就是「非家庭用户无创建入口」的
      接口层口径，前端显隐只是同一结论的展示）；
    - 用户名格式与撞名走与开放注册完全相同的规则（同正则、
      同 usernames 大小写不敏感撞名检查、同文案）。
    """
    if is_sub_account(parent):
        raise SubAccountError("子账号不能再创建子账号", "is_sub_account")
    if not subscriptions.has_active_subscription(db, parent.id):
        raise SubAccountError(
            "主账号没有生效中的订阅，无法创建子账号", "no_active_subscription")
    limit = sub_account_limit(db, parent)
    if active_children_count(db, parent.id) >= limit:
        raise SubAccountError(
            "子账号数量已达当前套餐上限", "limit_reached")
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        raise SubAccountError(
            "用户名需为 3-32 位字母、数字或下划线", "bad_username")
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise SubAccountError(
            f"密码至少 {PASSWORD_MIN_LENGTH} 位", "bad_password")
    if find_users_by_username_ci(db, name):
        raise SubAccountError("用户名已被注册", "username_taken")
    if username_conflict(db, name):
        raise SubAccountError("用户名已被占用", "username_taken")
    user = models.WebUser(
        username=name,
        password_hash=hash_password(password),
        is_active=True,
        parent_user_id=int(parent.id),
    )
    db.add(user)
    commit_with_retry(db, label="创建子账号")
    db.refresh(user)
    logger.info("子账号已创建: parent=%s sub=%s (%s)",
                parent.id, user.id, user.username)
    return user


def disable_sub_account(db: Session, parent: models.WebUser,
                        sub_user_id: int) -> models.WebUser:
    """主账号删除（软禁用）一个自己的子账号：记录保留、登录立即失效。

    只许操作 parent_user_id == 自己的行；别人的子账号与主账号
    本人在这里都按「不存在」处理（不泄露归属信息）。
    """
    if is_sub_account(parent):
        raise SubAccountError("子账号不能管理子账号", "is_sub_account")
    child = (db.query(models.WebUser)
             .filter(models.WebUser.id == int(sub_user_id),
                     models.WebUser.parent_user_id == int(parent.id))
             .first())
    if child is None:
        raise SubAccountError("子账号不存在", "not_found")
    if child.is_active:
        child.is_active = False
        # 与改密/踢下线同口径：token_version +1，已发 token 立即作废
        # （各鉴权口另有 is_active 拦截，这里是双保险）。
        child.token_version = int(child.token_version or 0) + 1
        commit_with_retry(db, label="删除子账号")
        logger.info("子账号已禁用: parent=%s sub=%s", parent.id, child.id)
    return child


def forbid_sub_account(user) -> None:
    """经济动作统一拦截：子账号调用购买/充值/兑换/进货类接口时抛 403。

    子账号没有自己的订阅与钱包，这类动作一律回主账号做；各接口
    在入口调本函数，不许各写一份判定。
    """
    if is_sub_account(user):
        raise HTTPException(
            status_code=403,
            detail="子账号不能进行购买、充值、兑换等操作，请使用主账号")


def management_payload(db: Session, user: models.WebUser) -> dict:
    """用户中心「子账号」区的单一数据口（主/子两种视角）。

    - 子账号视角：只回归属说明（is_sub_account + 主账号用户名），
      前端据此显示「家庭子账号」标识，不渲染任何管理/购买入口；
    - 主账号视角：limit（当前套餐声明）、used（在用数）、
      can_create（订阅有效且未满额）与子账号清单（含已禁用的，
      带 is_active 让前端区分「已删除」）。
    """
    if is_sub_account(user):
        parent = effective_user(db, user)
        return {
            "is_sub_account": True,
            "parent_username": parent.username if parent is not None else None,
            "limit": 0,
            "used": 0,
            "can_create": False,
            "items": [],
        }
    limit = sub_account_limit(db, user)
    team_ids = _team_member_ids(db)
    children = [c for c in children_of(db, user.id)
                if int(c.id) not in team_ids]
    used = active_children_count(db, user.id)
    return {
        "is_sub_account": False,
        "parent_username": None,
        "limit": limit,
        "used": used,
        "can_create": bool(
            used < limit
            and subscriptions.has_active_subscription(db, user.id)),
        "items": [
            {
                "id": c.id,
                "username": c.username,
                "is_active": bool(c.is_active),
                "created_at": (c.created_at.isoformat()
                               if c.created_at else None),
            }
            for c in children
        ],
    }
