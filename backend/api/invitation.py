"""
邀请返利 API（统一后端）

端点（前缀 /api/user/invite）：
- GET  /config          邀请规则（开关）——公开
- GET  /my-code         我的邀请码（自动生成），含统计
- GET  /records         我邀请的记录
- apply_invitation      供注册流程调用的内部函数（只落关系，不再发积分）
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend import models
from backend.integrations.store import switch_env_value as _switch_env_value
from backend.database import get_db
from backend.api.user import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/invite", tags=["用户端-邀请返利"])

CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_invite_code(db: Session) -> str:
    """生成不冲突的邀请码（8 位）"""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not db.query(models.InvitationCode).filter(
            models.InvitationCode.code == code
        ).first():
            return code


def _mask_invite_code(code_str: str | None) -> str:
    """邀请码掩码（A-128：日志只许记掩码，不许记原文）。

    只露首尾各 1 位：8 位 36 字母码露首尾各 2 位只剩 36^4 ≈ 170 万种、
    对无鉴权注册口可枚举；露首尾各 1 位剩 36^6，不可枚举，
    配合 user id / 时间戳仍可定位到具体一次注册尝试。
    短码（≤2 位）直接全掩。
    """
    s = (code_str or "").strip()
    if not s:
        return ""
    if len(s) <= 2:
        return "****"
    return f"{s[0]}{'*' * (len(s) - 2)}{s[-1]}"


def _get_config(db: Session, key: str, default: str) -> str:
    # A 类摘面板开关（2026-10-21）：env 显式设置即接管，口径见 integrations.store
    _env_v = _switch_env_value(key)
    if _env_v is not None:
        return _env_v
    config = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    return config.value if config and config.value is not None else default


def _get_int_config(db: Session, key: str, default: int) -> int:
    try:
        return int(_get_config(db, key, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool_config(db: Session, key: str, default: bool) -> bool:
    return _get_config(db, key, "true" if default else "false").strip().lower() == "true"


def get_invite_config(db: Session) -> dict:
    """邀请配置全貌：总开关 + 返利口径 + 福利试用口径（均为配置键现读）。

    能量/积分奖励已随货币下线（2026-11-13）；钱包时代（2026-11-14）起：
    返利按 invite_rebate 的模式/比例/分档键计提进钱包，福利邀请按
    invite_trial 的天数/门槛/月次数键给被邀请人发试用。邀请页的规则
    文案由前端按这份配置生成，数字不在代码里写死。
    """
    from backend import invite_rebate, invite_trial

    return {
        "enabled": _get_bool_config(db, "invitation_enabled", True),
        "rebate": invite_rebate.config_snapshot(db),
        "trial": invite_trial.config(db),
    }


def _mask_username(name: str | None) -> str:
    """用户名脱敏（邀请记录/返利明细展示用）：露首尾、中间打码。

    邀请人只需要认得出「是哪位好友」，不需要完整用户名（隐私口径与
    机器人查人卡一致：最小披露）。注销用户统一显示「已注销」。
    """
    s = (name or "").strip()
    if not s:
        return "已注销"
    if len(s) == 1:
        return "*"
    if len(s) == 2:
        return f"{s[0]}*"
    return f"{s[0]}***{s[-1]}"


def _claim_invitation_use(db: Session, code: models.InvitationCode, now: datetime) -> bool:
    """**原子占位**一次邀请码消耗：抢不到（已停用 / 过期 / 用尽）返回 False。

    「先读 use_count 判断、再写 use_count + 1」是读-改-写：同一张 max_uses=2 的邀请码
    被多个注册请求同时提交时，各方都读到 use_count < 2，于是都发奖、都写回 +1——
    超发（发奖数超过 max_uses）且丢更新（写回互相覆盖）。与兑换码 claim_code 同一写法：
    条件 UPDATE 把"是否可用"表达完整，只有仍然可用的那一行会被 +1，并发下第二个请求
    拿到 rowcount=0，直接拒绝。**不提交**——占位与发奖必须同一个事务，调用方提交前
    崩溃时两者一起回滚，不会留下"占了名额但没发奖"。
    """
    claimed = (
        db.query(models.InvitationCode)
        .filter(
            models.InvitationCode.id == code.id,
            models.InvitationCode.is_active.is_(True),
            or_(
                models.InvitationCode.expires_at.is_(None),
                models.InvitationCode.expires_at > now,
            ),
            or_(
                models.InvitationCode.max_uses.is_(None),
                models.InvitationCode.max_uses == 0,
                func.coalesce(models.InvitationCode.use_count, 0)
                < models.InvitationCode.max_uses,
            ),
        )
        .update(
            {models.InvitationCode.use_count:
             func.coalesce(models.InvitationCode.use_count, 0) + 1},
            synchronize_session=False,
        )
    )
    if not claimed:
        return False
    # 注意：不要在这里手写 `code.use_count += 1`。
    # bulk UPDATE 用的是 synchronize_session=False，session 里的 code 实例保持干净；
    # 一旦给它赋值就会变脏，后续 flush 会把这个"读时旧值 +1"的 stale 值写回 DB，
    # 把并发下其它事务刚 +1 好的计数值覆盖掉——正是本修复要消灭的丢更新。
    # 本函数之后没有任何逻辑再读 code.use_count（promotion.grant 只读 code.id），
    # 所以实例保持未同步是最安全的。
    return True


def apply_invitation(db: Session, user: models.WebUser, invite_code: str) -> dict:
    """注册流程调用：应用邀请码，双向发奖，写邀请记录。失败静默（不阻塞注册）"""
    config = get_invite_config(db)
    if not config["enabled"] or not invite_code:
        return {"applied": False}

    code_str = invite_code.strip().upper()
    code = db.query(models.InvitationCode).filter(
        models.InvitationCode.code == code_str,
        models.InvitationCode.is_active == True,  # noqa: E712
    ).first()
    if not code or code.user_id == user.id:
        return {"applied": False}
    if code.max_uses and code.use_count >= code.max_uses:
        return {"applied": False}

    now = datetime.now()
    if code.expires_at and code.expires_at < now:
        return {"applied": False}

    # 白名单（可选）：内测码 / 渠道码只给指定的人用。空名单 = 不限，
    # 所以存量邀请码行为完全不变。
    if (code.whitelist or "").strip():
        allowed = {w.strip().lower() for w in code.whitelist.split(",") if w.strip()}
        if (user.username or "").strip().lower() not in allowed:
            logger.info("邀请码白名单未命中，跳过: code_id=%s user=%s", code.id, user.id)
            return {"applied": False}

    inviter = db.query(models.WebUser).filter(
        models.WebUser.id == code.user_id
    ).first()
    if not inviter:
        return {"applied": False}

    # 幂等：同一个被邀请人只能建立一次关系（重试/重复调用不得重复发奖）
    existing = db.query(models.InvitationRecord).filter(
        models.InvitationRecord.invitee_id == user.id
    ).first()
    if existing:
        logger.info("邀请关系已存在，跳过重复发奖: invitee=%s", user.id)
        return {"applied": False}

    # P0 原子占位：把"读 use_count 判断 + 写 use_count+1"合成一次条件 UPDATE。
    # 读检查（上面那行 max_uses 判断）只是快速失败；真正的名额以这一次 UPDATE 为准，
    # 并发下抢不到名额的直接拒绝，不会走到后面的发奖。占位失败整体回滚，不留半截状态。
    if not _claim_invitation_use(db, code, now):
        db.rollback()
        logger.info("邀请码名额并发占位失败: code_id=%s user=%s", code.id, user.id)
        return {"applied": False}

    # 记录邀请关系（reward_points 先占位，promotion 发放后回填实际数值）
    record = models.InvitationRecord(
        inviter_id=inviter.id,
        invitee_id=user.id,
        code_id=code.id,
        reward_points=0,
    )
    db.add(record)

    # 邀请人奖励（2026-10-23 去重后由 promotion 唯一发放）：数值 = 码上
    # 专属奖励优先、否则推广配置（余额积分或会员天数）。它不 flush：
    # 下面这一次 flush 会把邀请关系、被邀请人奖励、邀请人奖励一起落盘，
    # 任何一处冲突都整体回滚。发放值回填进邀请关系记录，口径与明细一致。
    from backend import promotion as promotion_rewards

    granted = promotion_rewards.grant(db, inviter, user, code)
    inviter_reward = int(granted.reward_value) if granted else 0
    record.reward_points = inviter_reward
    inviter_balance = int(inviter.points or 0)

    try:
        # 唯一索引 invitee_id 兜底：并发重复调用时让冲突在此暴露并整体回滚，
        # 不会出现「发了奖但没有关系记录」
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.info("邀请关系并发重复，已回滚: invitee=%s", user.id)
        return {"applied": False}
    except OperationalError:
        # 数据库写锁竞争（SQLite 读写事务升级失败）：整体回滚，不留下半截发奖
        db.rollback()
        logger.warning("邀请发奖遇到写锁竞争，已回滚: invitee=%s", user.id)
        return {"applied": False}

    # 福利邀请：给被邀请人发试用订阅（见面礼，体验权益不可变现）。
    # 必须排在上面的 flush 之后：试用模块的配置/用量查询会触发 autoflush，
    # 若排在 flush 前，并发败者的关系行会被提前 flush 撞唯一索引、毒化
    # session，让最终 flush 抛 PendingRollbackError 逃出本函数的静默口径。
    # 保存点隔离：试用发放失败只回滚试用本身，邀请关系与邀请人奖励
    # 照常落盘（与推广奖励「失败不阻塞」的口径一致，但不许静默——记日志）。
    try:
        with db.begin_nested():
            from backend import invite_trial

            invite_trial.grant_invitee_trial(db, inviter, user)
    except Exception:  # noqa: BLE001
        logger.warning("福利邀请试用发放失败，已跳过: inviter=%s invitee=%s",
                       inviter.id, user.id, exc_info=True)

    logger.info("邀请关系建立: inviter=%s invitee=%s code_id=%s",
                inviter.id, user.id, code.id)
    return {
        "applied": True,
        "inviter_id": inviter.id,
        "inviter_reward": inviter_reward,
    }


# ==================== Endpoints ====================

class InviteConfigResponse(BaseModel):
    enabled: bool
    rebate: dict = {}
    trial: dict = {}


@router.get("/config", response_model=InviteConfigResponse)
async def invite_config(db: Session = Depends(get_db)):
    return InviteConfigResponse(**get_invite_config(db))


def _active_code(db: Session, user_id: int):
    return db.query(models.InvitationCode).filter(
        models.InvitationCode.user_id == user_id,
        models.InvitationCode.is_active == True,  # noqa: E712
    ).first()


def _create_self_code(db: Session, user: models.WebUser) -> models.InvitationCode:
    """生成一张自助邀请码并提交（撞号重试一次）。"""
    # 自助码恒写 reward_points=0：语义是「跟随全局配置」（r31 起 reward_points
    # 是本码专属奖励字段，>0 才覆盖全局；自助码不该冻结生成时的配置快照）。
    # 撞号重试：极低概率下两个并发请求生成了同码，唯一索引拦下后重生成一次。
    created = None
    for _attempt in range(2):
        created = models.InvitationCode(
            code=_generate_invite_code(db),
            user_id=user.id,
            reward_points=0,
        )
        db.add(created)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.info("邀请码并发撞号，已重生成重试: user=%s", user.id)
            created = None
            continue
        break
    if created is None or created.id is None:
        raise HTTPException(status_code=500, detail="邀请码生成失败，请重试")
    db.refresh(created)
    return created


def _my_code_payload(db: Session, current_user: models.WebUser) -> dict:
    from backend import invite_rebate, invite_trial

    code = _active_code(db, current_user.id)
    if not code:
        code = _create_self_code(db, current_user)

    invited_count = db.query(models.InvitationRecord).filter(
        models.InvitationRecord.inviter_id == current_user.id
    ).count()
    totals = invite_rebate.totals_for_inviter(db, current_user.id)

    return {
        "code": code.code,
        "use_count": code.use_count or 0,
        "max_uses": code.max_uses,
        "invited_count": invited_count,
        "username": current_user.username,
        "config": get_invite_config(db),
        "rebate_totals": {
            "credited": f"{totals['credited']:.2f}",
            "pending": f"{totals['pending']:.2f}",
            "count": totals["count"],
        },
        "welfare": invite_trial.welfare_status(db, current_user),
    }


@router.get("/my-code")
def my_code(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的邀请码（没有则自动生成）+ 邀请统计 + 返利汇总 + 福利资格"""
    return _my_code_payload(db, current_user)


@router.post("/my-code/regenerate")
def regenerate_my_code(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """换一个邀请码（邀请页标题旁的刷新钮）：作废当前码、生成新码。

    失效口径：旧码即刻停用——已分享出去的旧链接/旧码不能再绑定新的
    邀请关系；**已建立的邀请关系、已产生的返利与试用都不受影响**
    （关系行独立于码存在）。管理员发的渠道码不在此处理（本端点只
    轮换 my-code 当前展示的这张自助码）。
    """
    old = _active_code(db, current_user.id)
    if old is not None:
        old.is_active = False
        db.commit()
        logger.info("邀请码已轮换: user=%s old_code_id=%s", current_user.id, old.id)
    return _my_code_payload(db, current_user)


@router.get("/records")
def my_invitation_records(
    limit: int = 50,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我邀请的用户列表：脱敏名 + 注册时间 + 是否已付费 + 每人累计返利。

    返利口径（2026-11-14 起）：读钱包时代的计提台账
    （invite_rebate_accruals，元，pending+credited 合计）；旧积分返利
    台账（PointsLog）已随货币下线冻结，不再混进这个数字。
    """
    from backend import invite_rebate
    from backend.agents import money

    records = db.query(models.InvitationRecord).filter(
        models.InvitationRecord.inviter_id == current_user.id
    ).order_by(models.InvitationRecord.created_at.desc()).limit(min(limit, 200)).all()

    invitee_ids = [r.invitee_id for r in records]
    rebate_totals = invite_rebate.totals_by_invitee(db, current_user.id, invitee_ids)
    paid_ids = {
        uid for (uid,) in db.query(models.SubscriptionOrder.user_id)
        .filter(models.SubscriptionOrder.user_id.in_(invitee_ids),
                models.SubscriptionOrder.status == "paid")
        .distinct().all()
    } if invitee_ids else set()
    names = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_(invitee_ids)).all()
    } if invitee_ids else {}

    items = []
    for r in records:
        items.append({
            "id": r.id,
            "invitee_username": _mask_username(names.get(r.invitee_id)),
            "reward_points": r.reward_points,
            "paid": r.invitee_id in paid_ids,
            "rebate_total": float(money(rebate_totals.get(r.invitee_id, 0))),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"total": len(items), "records": items}


@router.get("/rebates")
def my_rebates(
    limit: int = 50,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的返利明细（计提台账逐笔）：谁付的费、付了多少、返多少、到账没。"""
    from backend import invite_rebate

    rows = (
        db.query(models.InviteRebateAccrual)
        .filter(models.InviteRebateAccrual.inviter_id == current_user.id)
        .order_by(models.InviteRebateAccrual.id.desc())
        .limit(min(limit, 200))
        .all()
    )
    names = {
        u.id: u.username for u in db.query(models.WebUser)
        .filter(models.WebUser.id.in_({r.invitee_id for r in rows})).all()
    } if rows else {}
    totals = invite_rebate.totals_for_inviter(db, current_user.id)
    return {
        "total_rebate": float(totals["credited"] + totals["pending"]),
        "credited": f"{totals['credited']:.2f}",
        "pending": f"{totals['pending']:.2f}",
        "rebates": [
            invite_rebate.accrual_dto(r, _mask_username(names.get(r.invitee_id)))
            for r in rows
        ],
    }


@router.get("/promotions")
def my_promotions(
    limit: int = 50,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的推广奖励明细（v2.44.0）：邀请成功后另发的那一笔

    与「返利台账」分开：返利是被邀请人充值后按比例分的，这里是邀请即发的一次性
    奖励，类型可以是余额也可以是会员有效期——合成一张表会说不清「这笔是什么」。
    """
    from backend import promotion

    items = promotion.rewards_for(db, current_user.id, limit)
    return {
        "total": len(items),
        "config": promotion.config(db),
        "rewards": items,
    }

