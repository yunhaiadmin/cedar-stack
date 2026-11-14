"""
用户经济系统 API：兑换码 / 支付下单 / 订单查询（前缀 /api/user/economy）

- GET  /exchange/config       兑换功能开关
- POST /exchange/redeem       兑换码核销（订阅 / 折扣权益）
- GET  /payment/methods       可用支付方式（按网关能力推导）
- GET  /payment/plans         订阅套餐列表（公开）
- GET  /payment/coupon/config 优惠券开关（关闭时用户端不显示优惠码输入框）
- POST /payment/coupon/quote  优惠码试算（能不能用、省多少、实付多少）
- POST /payment/order         创建支付订单（购买订阅），返回支付跳转 URL
- GET  /payment/orders        我的订单列表

能量/积分/活力货币已于 2026-11-13 删除（用户亲令「只用友站口径」）：
签到发分、积分台账/转让、能量买订阅、能量充值、活力兑换一并下线；
订阅发放（_grant_subscription）、人民币下单与履约链原样保留。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from backend import coupons, models, realms
from backend.integrations.store import switch_env_value as _switch_env_value
from backend.database import get_db
from backend.api.user import get_current_user
from backend.ratelimit import check_rate_limit
from backend.notifications import AdminEvent, notify_admin_event_quiet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/economy", tags=["用户端-经济系统"])

bearer_scheme_deps = None  # reserved


# ==================== 配置中心工具 ====================

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


def _get_bool_realm_config(db: Session, base: str, realm_id, default: bool) -> bool:
    """按服读 bool 开关（两服共存 P1-5 的统一读法）。

    base 必须先登记进 ``realms.REALM_CONFIG_BASES``，否则 ``realm_key``
    恒返回原键名、按服值永远读不到。默认服（legacy）仍读原键名，
    老部署与外部脚本不受影响；``realm_id`` 为 None 时按当前服判。
    读数走 realm_config 的 store 通道（TTL 缓存＋写时失效），与
    servers 等既有按服配置同一套口径。
    """
    raw = realms.realm_config(db, base, realm_id, "true" if default else "false")
    return str(raw).strip().lower() == "true"


# ==================== 日期口径工具 ====================

def _today_start() -> datetime:
    """「今日」起点：Asia/Shanghai 自然日 00:00（UTC naive 存储帧）。

    r34 口径收口：此前是服务器本地 00:00，生产容器为 UTC，签到/连签的
    「今日」实际在北京时间 08:00 才翻天（r18 F1、r30 L1、r33 F3 同类）。
    直接委托给 _today_start_utc_naive()，与 CheckinRecord.checkin_date 的
    写入帧保持一致（写入点唯一：_do_checkin_core）。
    """
    return _today_start_utc_naive()


def _today_start_utc_naive() -> datetime:
    """上海自然日 00:00，换算成 UTC naive——与入库时间列的存储帧对齐。

    生产容器系统时区为 UTC，``PointsLog.created_at`` 等列以 UTC naive 入库；
    直接用服务器本地 00:00 当「今日」起点，会让日限额在北京时间 08:00 才重置
    （r18 F1、r30 L1 同类问题）。返回的起点已是 UTC 帧，可直接与入库列比较。
    """
    from zoneinfo import ZoneInfo
    try:
        now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001 — 缺 tzdata 等极端情况回退
        now_sh = datetime.now().astimezone()
    start_sh = now_sh.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_sh.astimezone(timezone.utc).replace(tzinfo=None)


def _month_start_utc_naive() -> datetime:
    """上海自然月 1 日 00:00，换算成 UTC naive——与入库时间列的存储帧对齐。

    求片月度额度（media_seek.used_this_month、welfare_requests._month_start）
    此前用服务器本地月界，生产 UTC 下每月 1 日 08:00（北京时间）才重置。
    与 _today_start_utc_naive() 同一写法。
    """
    from zoneinfo import ZoneInfo
    try:
        now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001 — 缺 tzdata 等极端情况回退
        now_sh = datetime.now().astimezone()
    start_sh = now_sh.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_sh.astimezone(timezone.utc).replace(tzinfo=None)


class InsufficientPoints(ValueError):
    """余额不足异常（积分时代的扣减契约；积分台账已下线，保留类本身供
    尚未拆除的旧玩法模块引用，新代码不要再抛/捕它）"""


def lock_user_row(db: Session, user_id: int) -> None:
    """对用户行加写锁，持有到本事务提交/回滚（每日/滚动额度类「读用量-判上限-写」的串行化原语）。

    额度检查（今日已转出、今日群发言已得、7 天红包收发次数）都是先 SUM/COUNT 再判断：
    并发两个请求都读到同一旧用量、都判定未超限 → 上限被绕过。统一做法：检查前先锁住
    该用户行，再在锁内统计用量；第二个请求在锁上等前者提交后，重新统计就能看到前者的记录。
    - PostgreSQL：SELECT ... FOR UPDATE（READ COMMITTED 下锁后的新语句看得到已提交数据）；
    - SQLite：无行锁，用无副作用 UPDATE 取得库级写锁（同样持有到提交）。
    不需要新表/迁移。必须在统计用量之前调用。
    """
    from sqlalchemy import text as _text
    if db.get_bind().dialect.name == "postgresql":
        db.execute(_text("SELECT id FROM web_users WHERE id = :id FOR UPDATE"), {"id": user_id})
    else:
        db.execute(_text("UPDATE web_users SET id = id WHERE id = :id"), {"id": user_id})


# ==================== 兑换码 ====================

def _subscription_grant_lock_key(user_id: int, realm_id: int | None) -> int:
    """订阅发放串行锁的 64 位 key：按（用户，服）派生，与 setup.py 的 key 域隔离。"""
    return int.from_bytes(
        hashlib.sha256(f"aetrix-sub-grant:{user_id}:{realm_id or 0}".encode()).digest()[:8],
        "big", signed=True,
    )


_grant_thread_lock = threading.RLock()  # 非 PG（测试/SQLite）回退：进程内串行


def _grant_realm_for_key(db: Session, target_realm: int | None) -> int:
    """锁 key 用的服 id：纯读，不触发 ``ensure_default_realm`` 的建服+commit。

    ``target_realm`` 为 None 时（套餐未绑服），检查分支按「任意服」匹配、
    新建分支落到面板当前服——key 取当前服可读快照，保证并发双方算出同一个 key。
    与新建分支的 ``realms.active_realm_id(db)`` 语义一致（读路径相同），
    只是去掉了「无服时自动建服」的写路径。
    """
    if target_realm is not None:
        return target_realm
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == realms.CONFIG_ACTIVE_REALM).first()
    if row and str(row.value or "").strip().isdigit():
        realm = realms.realm_snapshot(db, int(row.value))
        if realm is not None and getattr(realm, "is_active", True):
            return realm.id
    legacy = realms.legacy_realm_id(db)
    return legacy if legacy is not None else 0


@contextmanager
def subscription_grant_lock(db: Session, user_id: int, realm_id: int | None):
    """订阅「先查生效行再顺延/新建」的串行锁（r52，闭 EDGE-1 缺口）。

    ``_grant_subscription`` 先 SELECT 生效行（``FOR UPDATE``）再决定顺延还是新建：
    无行可锁时，两个并发事务会各自 INSERT，产生双 active 行（到期提醒双发、
    订阅统计虚高）。行锁锁不住「不存在的行」，所以在这里按（用户，服）先串行：
    PG 用事务级 advisory lock（随事务提交/回滚自动释放，锁覆盖到 commit 之后，
    第二个事务的检查一定能看到第一个事务已提交的行）；SQLite/测试用进程内 RLock。

    锁序纪律：所有发放路径必须**先拿本锁、再拿任何行锁**
    （``_auto_renew_one`` 已按此重排），否则行锁与本锁会形成 AB-BA 死锁。
    """
    key = _subscription_grant_lock_key(user_id, realm_id)
    try:
        is_pg = db.bind.dialect.name == "postgresql"
    except Exception:
        is_pg = False
    if is_pg:
        # xact 级：事务结束自动释放，无需显式解锁
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
        yield
    else:
        with _grant_thread_lock:
            yield


def _grant_subscription(db: Session, user: models.WebUser, plan: models.SubscriptionPlan,
                        days: int, realm_id: int | None = None) -> models.UserSubscription:
    """发放订阅：**同一个服**已有有效订阅则顺延，否则新建

    订阅是一个服一个的：甲服的兑换码只能顺延甲服的会员，不能给乙服的会员加天数。

    去重靠"同服 active 订阅顺延"语义天然成立（重复发放只是顺延同一行，
    不会产生第二条 active 行），不需要幂等键——R49 删掉了从未被使用、
    也从未落库的 source/ref_id 形参（调用方以为有幂等键，实际没有，
    属误导性 API；追溯靠各调用方的 _audit details）。

    并发说明（r52 修 EDGE-1）：函数入口先拿 ``subscription_grant_lock``
    按（用户，服）串行，再做「查-改/查-增」。此前的 ``with_for_update()``
    只在行存在时有效，无行可锁时并发双事务会各自新建（双 active 行）。
    PostgreSQL 下是真正的串行；SQLite 会忽略行锁但写入本身是库级串行的，
    测试/单机路径另有进程内 RLock 兜底。
    """
    now = datetime.now()
    target_realm = realm_id if realm_id is not None else plan.realm_id
    # 锁 key 用只读快照（不触发建服+commit），与下面新建分支的落点一致
    grant_realm = _grant_realm_for_key(db, target_realm)
    with subscription_grant_lock(db, user.id, grant_realm):
        query = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user.id,
            models.UserSubscription.status == "active",
            models.UserSubscription.end_date > now,
        )
        if target_realm is not None:
            query = query.filter(models.UserSubscription.realm_id == target_realm)
        active = query.order_by(models.UserSubscription.end_date.desc()).with_for_update().first()

        if active:
            active.end_date = active.end_date + timedelta(days=days)
            active.updated_at = now
            # r48-⑫：续买/顺延到不同档位时，plan_id 跟随本次购买的档位
            # （展示「买的是哪个档」以最近一次购买为准；顺延天数逻辑不变）。
            active.plan_id = plan.id
            subscription = active
            # 续费顺延复用同一行：清掉上一周期的提醒去重记录，否则新周期
            # 的 7d/3d/1d/expired 档位会被旧记录挡住、用户再也收不到到期提醒。
            db.query(models.SubscriptionReminder).filter(
                models.SubscriptionReminder.subscription_id == active.id
            ).delete(synchronize_session=False)
        else:
            subscription = models.UserSubscription(
                user_id=user.id,
                plan_id=plan.id,
                realm_id=target_realm if target_realm is not None else realms.active_realm_id(db),
                start_date=now,
                end_date=now + timedelta(days=days),
                status="active",
            )
            db.add(subscription)
            db.flush()
    # 订阅分档权益（PR-1）：该用户的当前套餐可能已变（新建/顺延/换档），
    # 清掉他的权益解析缓存；同进程立即生效，跨进程靠 60 秒 TTL 兜底
    #（见 backend/entitlements.py）。本函数是购买/兑换/管理员发放/自动
    # 续订的共同发放口，挂在这里即全覆盖。
    from backend import entitlements as _entitlements

    _entitlements.invalidate(user_id=user.id)
    return subscription


@router.get("/exchange/config")
async def exchange_config(db: Session = Depends(get_db)):
    return {
        "enabled": _get_bool_config(db, "exchange_enabled", True),
        "invitee_note": "被邀请注册的奖励自动发放",
    }


def _best_discount_credit(db: Session, user_id: int) -> Optional[models.ExchangeDiscountCredit]:
    """用户最优的一张未用折扣权益（pct 最小=折扣最大），没有返回 None"""
    now = datetime.now()
    return (
        db.query(models.ExchangeDiscountCredit)
        .filter(
            models.ExchangeDiscountCredit.user_id == user_id,
            models.ExchangeDiscountCredit.status == "unused",
            or_(
                models.ExchangeDiscountCredit.expires_at.is_(None),
                models.ExchangeDiscountCredit.expires_at > now,
            ),
        )
        .order_by(models.ExchangeDiscountCredit.discount_pct.asc())
        .first()
    )


def _grant_discount_credit(db: Session, user_id: int, code: models.ExchangeCode) -> models.ExchangeDiscountCredit:
    """核销 discount 型兑换码：发一张折扣权益。调用方已做占位和并发保护；pct 合法性由调用方校验。"""
    credit = models.ExchangeDiscountCredit(
        user_id=user_id,
        exchange_code_id=code.id,
        discount_pct=code.discount_pct,
        expires_at=code.expires_at,
    )
    db.add(credit)
    return credit


def _exchange_discount_amount(list_price: Decimal, pct: int) -> tuple[Decimal, Decimal]:
    """兑换码折扣金额口径：与 coupons.compute 的 percent 一致（pct=实付百分比，85=八五折）。
    返回 (优惠额, 实付额)，两位小数。"""
    from backend.coupons import _money
    pct = max(1, min(99, int(pct or 0)))
    discount = _money(list_price * (Decimal("100") - Decimal(str(pct))) / Decimal("100"))
    if discount > list_price:
        discount = list_price
    paid = _money(list_price - discount)
    return discount, paid


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)


def _redeem_exchange_core(db: Session, user: models.WebUser, code_str: str) -> dict:
    """兑换码核销核心（同步，成功结尾 db.commit()；失败抛 HTTPException，detail 为用户可读文案）。

    HTTP 路由 POST /api/user/economy/exchange/redeem 与 TG Bot /redeem 命令共用同一实现
    （"横切能力只许一套"：不要复制第二套核销逻辑）。
    """
    user_id = user.id
    if not _get_bool_config(db, "exchange_enabled", True):
        raise HTTPException(status_code=403, detail="兑换功能未开启")

    code_str = code_str.strip().upper()
    code = db.query(models.ExchangeCode).filter(
        models.ExchangeCode.code == code_str
    ).first()

    now = datetime.now()
    if code is None or not code.is_active or (code.expires_at and code.expires_at < now):
        raise HTTPException(status_code=400, detail="兑换码无效或已过期")

    # H5：同一个账号同一张码最多兑换一次（max_uses 是总次数，不是每人次数）。
    # 先查（含升级前 used_by 里的历史），再在本事务里写核销记录：唯一约束兜住并发重复提交。
    from backend import codes as code_lib

    if code_lib.user_already_redeemed(db, code_lib.REDEMPTION_KIND_EXCHANGE, code, user_id):
        raise HTTPException(status_code=400, detail="你已经兑换过这个兑换码（每个账号限一次）")

    # 先原子占位再去发奖：只有仍可用的兑换码才会被 +1，并发下第二个请求 rowcount=0，
    # 因此不会出现「同一张单次码被同时核销两次、发两份奖励」。
    try:
        if not code_lib.record_redemption(
            db, code_lib.REDEMPTION_KIND_EXCHANGE, code.id, user_id
        ):
            raise HTTPException(status_code=400, detail="你已经兑换过这个兑换码（每个账号限一次）")
        claimed = (
            db.query(models.ExchangeCode)
            .filter(
                models.ExchangeCode.id == code.id,
                models.ExchangeCode.is_active.is_(True),
                or_(
                    models.ExchangeCode.expires_at.is_(None),
                    models.ExchangeCode.expires_at > now,
                ),
                or_(
                    models.ExchangeCode.max_uses.is_(None),
                    models.ExchangeCode.use_count < models.ExchangeCode.max_uses,
                ),
            )
            .update(
                {models.ExchangeCode.use_count: func.coalesce(models.ExchangeCode.use_count, 0) + 1},
                synchronize_session=False,
            )
        )
    except OperationalError:
        # SQLite 下读写事务升级失败（并发写入）：没发奖也没占位，让客户端重试
        db.rollback()
        raise HTTPException(status_code=409, detail="兑换码正在核销中，请稍后重试")
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=400, detail="兑换码已用尽或已过期")

    # 占位成功后才读回详情：use_count 是 SQL 级自增，身份映射里的旧实例要 refresh，
    # 否则下面判断「用满停用」会拿到过期的 0
    db.refresh(code)

    result: dict = {"success": True}
    if code.type == "subscription":
        plan = db.query(models.SubscriptionPlan).filter(
            models.SubscriptionPlan.id == code.plan_id
        ).first() if code.plan_id else None
        if not plan:
            # 已原子占位但无法履约：回滚，别白白吃掉一次使用次数
            db.rollback()
            raise HTTPException(status_code=400, detail="兑换码关联套餐不存在")
        subscription = _grant_subscription(
            db, user, plan, code.duration_days,
            # 兑换码按它自己所属的服发会员（未标注时回退到套餐的服）
            realm_id=getattr(code, "realm_id", None) or plan.realm_id,
        )
        result.update(reward_type="subscription", plan_name=plan.name,
                      days=code.duration_days,
                      end_date=subscription.end_date.isoformat(),
                      message=f"兑换成功，「{plan.name}」× {code.duration_days} 天")
    elif code.type == "discount":
        if not (1 <= (code.discount_pct or 0) <= 99):
            db.rollback()
            raise HTTPException(status_code=400, detail="该折扣码配置无效")
        _grant_discount_credit(db, user_id, code)
        result.update(reward_type="discount", discount_pct=code.discount_pct,
                      message=f"兑换成功，获得订阅 {code_lib.discount_pct_text(code.discount_pct)} 优惠，下次购买订阅自动抵扣")
    else:
        db.rollback()
        raise HTTPException(status_code=400, detail="兑换码类型不支持")

    # 核销审计（use_count 已在上面原子 +1，这里只补使用者并处理用满停用）
    used = [i for i in str(code.used_by or "").split(",") if i.strip()]
    if str(user_id) not in used:
        used.append(str(user_id))
    code.used_by = ",".join(used)[:500]
    if code.max_uses and (code.use_count or 0) >= code.max_uses:
        code.is_active = False
    db.commit()
    return result


@router.post("/exchange/redeem")
async def redeem_exchange_code(
    request: Request,
    req: RedeemRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """兑换码核销：subscription 型发订阅；discount 型发一张订阅折扣权益（points 型已随货币下线）

    占位 / 发奖 / 审计整段是同步 SQLAlchemy（体内一个 await 都没有），下放线程池执行；
    通知要走 WebSocket，必须留在事件循环上 await。
    """
    user_id = current_user.id
    allowed, _ = check_rate_limit(f"redeem:{user_id}", 10, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")

    result = await run_in_threadpool(_redeem_exchange_core, db, current_user, req.code)

    # r46：兑换已落库（权益已发放），通知走 quiet 守卫——失败只记日志不抛 500，
    # 避免用户重试时被"兑换码已使用"误导（已完成写成失败）。
    await notify_admin_event_quiet(
        event_type="economy.redeem",
        log_ctx=f"redeem user_id={user_id}",
        user_id=user_id,
        title="🎁 兑换成功",
        content=result["message"],
    )
    return result


@router.get("/exchange/discount-credit")
def my_discount_credit(
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我未使用的兑换码折扣权益（最优一张），前端在订阅购买区展示"""
    credit = _best_discount_credit(db, current_user.id)
    if credit is None:
        return {"has_credit": False}
    return {
        "has_credit": True,
        "discount_pct": credit.discount_pct,
        "expires_at": credit.expires_at.isoformat() if credit.expires_at else None,
    }


# ==================== 支付下单（易支付兼容网关） ====================

class PaymentMethod(BaseModel):
    id: str
    name: str
    enabled: bool


@router.get("/payment/methods", response_model=list[PaymentMethod])
async def payment_methods(db: Session = Depends(get_db)):
    # 公益服转换：购买开关关了，方法列表也必须全灭——否则前端仍显示
    # “可用”，用户点单才 503（P0 支付网关空的老坑）。购买口本身的
    # 关闭逻辑不动，只改列表展示；开关开时保持现有行为。
    # P1-5：购买开关按服——支付方式呈现在当前服口径下判
    purchase_enabled = _get_bool_realm_config(
        db, "subscription_purchase_enabled", realms.active_realm_id(db), True)
    methods = [
        PaymentMethod(id="alipay", name="支付宝", enabled=purchase_enabled),
        PaymentMethod(id="wxpay", name="微信支付", enabled=purchase_enabled),
    ]
    if _get_config(db, "payment_qqpay_enabled", "false").lower() == "true":
        methods.append(PaymentMethod(id="qqpay", name="QQ 钱包", enabled=purchase_enabled))
    return methods


@router.get("/payment/plans")
def payment_plans(realm_id: Optional[int] = None, db: Session = Depends(get_db)):
    """订阅套餐（购买订阅）

    套餐是一个服一个的：默认只展示**当前服**的可购套餐（``realm_id=0`` 看全部服）。
    用户买哪一份，会员就开在哪个服。

    同时下发该服的**接入方式**：公益服（``is_free``）不需要卖会员，用户端的钱包页
    据此把「开通会员」换成「公益服 · 免费开放」的说明，不再推付费引导。
    """
    from backend import entitlements as _entitlements

    scope_id = None if realm_id == 0 else (realm_id or realms.active_realm_id(db))
    # P1-5：购买开关按服判——看哪个服就用哪个服的开关；看全部服
    # （realm_id=0）时按当前服口径（单服部署与改前行为一致）。
    if not _get_bool_realm_config(
            db, "subscription_purchase_enabled",
            scope_id if scope_id is not None else realms.active_realm_id(db), True):
        return {"enabled": False, "plans": []}
    query = realms.scope(db.query(models.SubscriptionPlan),
                         models.SubscriptionPlan.realm_id, scope_id)
    plans = query.filter(
        models.SubscriptionPlan.is_active == True  # noqa: E712
    ).order_by(models.SubscriptionPlan.sort_order).all()
    free = realms.is_free_realm(db, scope_id) if scope_id else False
    return {
        "enabled": True,
        "realm_id": scope_id,
        "access_mode": "free" if free else "paid",
        "is_free": free,
        "access_note": realms.access_note_of(db, scope_id) if scope_id else "",
        "plans": [
            {
                "id": p.id, "name": p.name, "description": p.description,
                "price": float(p.price), "duration_days": p.duration_days,
                "features": p.features, "is_popular": p.is_popular,
                # 订阅分档权益（经判定单口 parse 归一：未声明即 {}，坏值剔除）；
                # 用户端商店据此自动生成权益文案行，展示永远等于实际生效值
                "entitlements": _entitlements.parse_entitlements(p.entitlements),
                "realm_id": p.realm_id,
                "realm_name": (p.realm.name if p.realm else ""),
            }
            for p in plans
        ],
    }


@router.get("/payment/coupon/config")
def coupon_config(db: Session = Depends(get_db)):
    """优惠券开关：关闭时用户端不展示优惠码输入框，避免填了才报错"""
    return {"enabled": coupons.enabled(db)}


class CouponQuoteRequest(BaseModel):
    code: str = Field(default="", description="用户填写的优惠码（可空；空时按「没填码自动用最优兑换码折扣权益」口径试算，与下单一致）")
    kind: str = Field(..., description="subscription")
    item_id: int = Field(..., description="充值套餐ID 或 套餐ID")


@router.post("/payment/coupon/quote")
def coupon_quote(
    req: CouponQuoteRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """优惠码试算（下单前预览）

    用 POST 而不是 GET：优惠码不该进访问日志/浏览器历史（与卡码预检同一考虑）。
    下单时走的是同一套口径（优惠券/权益直接对原价算），不会出现「预览 8 折、实付全价」。
    code 可空：空时按「没填优惠券自动用最优兑换码折扣权益」口径试算（与下单一致），
    否则有未用权益的用户看到的预览价会高于实付价。
    返回里带 member_discount_pct/amount，前端可算出最终实付。
    """
    allowed, _ = check_rate_limit(f"coupon-quote:{current_user.id}", 30, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    code = (req.code or "").strip()
    if code:
        result = coupons.quote(db, user=current_user, code=code,
                               kind=req.kind, item_id=req.item_id)
    else:
        # 空码试算：充值没有「自动权益」一说，仍要求填码；订阅走自动权益口径。
        if req.kind != "subscription":
            raise HTTPException(status_code=400, detail="请填写优惠码")
        list_price, _, item_name = coupons.resolve_item(db, req.kind, req.item_id)
        result = {
            "valid": True,
            "code": "",
            "coupon_id": 0,
            "item_name": item_name,
            "list_price": float(list_price),
            "discount_amount": 0.0,
            "paid_amount": float(list_price),
            "discount_type": "percent",
            "value": 0,
        }
    # 会员等级折扣已随等级体系于 2026-11-14 删除：折扣一律走套餐价/
    # 优惠码/兑换权益。member_discount_* 字段保留出参兼容、恒为 0。
    # 与 create_payment_order 同一口径：优惠券/权益直接对原价算。
    # 预览与下单必须一致。注意：自动权益在下单时不分付费/公益服
    # （没填码就用），这里同样。
    member_discount_pct = 0
    member_discount_amount = Decimal("0.00")
    exchange_discount_pct = 0
    if req.kind == "subscription":
        _list = Decimal(str(result.get("list_price", 0)))
        _base = _list
        if code:
            # 对中间价重算优惠券（与下单逻辑一致）
            coupon_obj = db.query(models.CouponCode).filter(
                models.CouponCode.id == result["coupon_id"]).first()
            if coupon_obj is not None:
                c_discount, c_paid = coupons.compute(_base, coupon_obj)
                result["discount_amount"] = float(member_discount_amount + c_discount)
                result["paid_amount"] = float(c_paid)
        else:
            # 没填码：自动用最优兑换码折扣权益（与下单逻辑一致，直接对原价算）
            credit = _best_discount_credit(db, current_user.id)
            if credit is not None:
                exchange_discount_pct = credit.discount_pct
                c_discount, c_paid = _exchange_discount_amount(_base, credit.discount_pct)
                result["discount_amount"] = float(member_discount_amount + c_discount)
                result["paid_amount"] = float(c_paid)
    result["member_discount_pct"] = member_discount_pct
    result["member_discount_amount"] = float(member_discount_amount)
    result["exchange_discount_pct"] = exchange_discount_pct
    return result


class CreateOrderRequest(BaseModel):
    kind: str = Field(..., description="subscription=购买订阅")
    item_id: Optional[int] = Field(default=None, description="套餐ID")
    # P3 修复（审查）：payment_method 无白名单，任意字符串进网关。限定可选值。
    payment_method: str = Field(default="alipay", pattern="^(alipay|wxpay|qqpay)$")
    coupon_code: str = Field(default="", description="优惠码（可空）；下单时占额度，付款转已用，关单/退款自动还回")


def _yipay_sign(params: dict, key: str) -> str:
    """易支付 MD5 签名：按 key ASCII 升序拼接 a=b&...&<key>"""
    filtered = {k: v for k, v in params.items()
                if v not in (None, "") and k != "sign" and k != "sign_type"}
    qs = urlencode(sorted(filtered.items()))
    return hashlib.md5(f"{qs}{key}".encode("utf-8")).hexdigest()


@router.post("/payment/order")
def create_payment_order(
    request: Request,
    req: CreateOrderRequest,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建支付订单，返回易支付跳转 URL（epay 标准提交）"""
    allowed, _ = check_rate_limit(f"order:{current_user.id}", 10, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="下单过于频繁，请稍后再试")

    # P2 修复（审查）：用户可无限创建 pending 订单（10/min = 600/小时），
    # 每单都 coupons.reserve 占额度，可耗尽一张券的 max_uses。限制每用户
    # pending 订单数（默认 5，可配）。
    try:
        max_pending = int(_get_config(db, "max_pending_orders_per_user", "5") or 5)
    except ValueError:
        max_pending = 5
    if max_pending > 0:
        pending = (
            db.query(models.RechargeOrder).filter(
                models.RechargeOrder.user_id == current_user.id,
                models.RechargeOrder.status == "pending",
            ).count()
            + db.query(models.SubscriptionOrder).filter(
                models.SubscriptionOrder.user_id == current_user.id,
                models.SubscriptionOrder.status == "pending",
            ).count()
        )
        if pending >= max_pending:
            raise HTTPException(
                status_code=429,
                detail=f"待支付订单过多（{pending} 笔），请先完成或取消后再下单",
            )

    gateway = _get_config(db, "payment_gateway_url", "").strip().rstrip("/")
    pid = _get_config(db, "payment_partner_id", "").strip()
    key = _get_config(db, "payment_partner_key", "").strip()
    if not gateway or not pid or not key:
        raise HTTPException(status_code=503, detail="支付通道未配置，请联系管理员")

    site_url = _get_config(db, "site_url", "").strip().rstrip("/")
    notify_url = f"{site_url}/api/user/economy/payment/notify"
    return_url = f"{site_url}/wallet?paid=1"

    order_id = f"RB{int(time.time() * 1000)}{uuid.uuid4().hex[:6].upper()}"

    if req.kind == "subscription":
        plan = db.query(models.SubscriptionPlan).options(
            joinedload(models.SubscriptionPlan.realm)
        ).filter(
            models.SubscriptionPlan.id == req.item_id,
            models.SubscriptionPlan.is_active == True,  # noqa: E712
        ).first()
        if not plan:
            raise HTTPException(status_code=404, detail="套餐不存在")
        # P1-5：购买开关按服判（套餐属于哪个服就用哪个服的开关；
        # 默认服仍读原键名，与改前行为一致）。
        if not _get_bool_realm_config(db, "subscription_purchase_enabled", plan.realm_id, True):
            raise HTTPException(status_code=403, detail="订阅购买未开启")
        item_name = f"订阅购买 - {plan.name}"
        amount = plan.price
        order = models.SubscriptionOrder(
            order_id=order_id, user_id=current_user.id,
            plan_id=plan.id, item_name=plan.name,
            amount=amount, payment_method=req.payment_method, status="pending",
        )
    else:
        raise HTTPException(status_code=400, detail="kind 必须是 subscription")

    # 会员等级折扣已随等级体系于 2026-11-14 删除：优惠券/权益直接对
    # 原价算。member_discount_* 恒为 0（字段保留兼容订单快照口径）。
    list_price = Decimal(str(amount))
    member_discount_pct = 0
    member_discount_amount = Decimal("0.00")
    coupon_base = list_price

    # 优惠券：先用 quote 校验券有效性（按原价校验门槛），再对中间价重算折扣
    discount_amount = member_discount_amount
    paid_amount = coupon_base
    preview = None
    coupon_code = (req.coupon_code or "").strip()
    credit = None
    if coupon_code:
        preview = coupons.quote(db, user=current_user, code=coupon_code,
                                kind=req.kind, item_id=req.item_id)
        coupon_obj = db.query(models.CouponCode).filter(
            models.CouponCode.id == preview["coupon_id"]).first()
        if coupon_obj is None:
            raise HTTPException(status_code=400, detail="优惠码不存在")
        c_discount, c_paid = coupons.compute(coupon_base, coupon_obj)
        discount_amount = member_discount_amount + c_discount
        paid_amount = c_paid
        coupon_code = preview["code"]
    elif req.kind == "subscription":
        # 兑换码折扣权益：没填优惠券时自动用最优的一张（pct 最小=折扣最大），
        # 不与优惠券叠加，直接对原价算（与上面的优惠券分支同口径）。
        credit = _best_discount_credit(db, current_user.id)
        if credit is not None:
            c_discount, c_paid = _exchange_discount_amount(coupon_base, credit.discount_pct)
            discount_amount = member_discount_amount + c_discount
            paid_amount = c_paid

    # 订单上快照「原价 / 优惠 / 实付」，并让订单金额一律等于**实付**：
    # 对账、邀请返利比例、退款都以用户真付的钱为准，不能按原价算。
    order.list_price = list_price
    order.discount_amount = discount_amount
    if req.kind == "recharge":
        order.price = paid_amount
    else:
        order.amount = paid_amount

    db.add(order)
    if credit is not None:
        # 兑换码折扣权益：下单即核销（一张只用一次）。条件 UPDATE 防并发双下单抢同一张。
        claimed = (
            db.query(models.ExchangeDiscountCredit)
            .filter(
                models.ExchangeDiscountCredit.id == credit.id,
                models.ExchangeDiscountCredit.status == "unused",
            )
            .update(
                {"status": "used", "used_order_id": order_id},
                synchronize_session=False,
            )
        )
        if not claimed:
            db.rollback()
            raise HTTPException(status_code=409, detail="折扣权益已被使用，请刷新后重试")
    if preview is not None:
        coupon = db.query(models.CouponCode).filter(
            models.CouponCode.id == preview["coupon_id"]).first()
        if coupon is None:
            raise HTTPException(status_code=400, detail="优惠码不存在")
        # 占额度（条件 UPDATE，并发也超不了总限）；失败则整笔下单回滚
        # reserve 记录的是优惠券口径：list_price=中间价，discount=仅优惠券部分
        usage = coupons.reserve(db, coupon=coupon, user=current_user, order_id=order_id,
                                kind=req.kind, list_price=coupon_base,
                                discount=discount_amount - member_discount_amount,
                                paid=paid_amount)
        order.coupon_usage_id = usage.id
    db.commit()

    params = {
        "pid": pid,
        "type": req.payment_method,
        "out_trade_no": order_id,
        "notify_url": notify_url,
        "return_url": return_url,
        "name": item_name,
        "money": f"{float(paid_amount):.2f}",
    }
    params["sign"] = _yipay_sign(params, key)
    params["sign_type"] = "MD5"
    pay_url = f"{gateway}/submit.php?{urlencode(params)}"

    order.payment_url = pay_url
    db.commit()

    logger.info("创建支付订单: user=%s order=%s kind=%s amount=%s discount=%s",
                current_user.id, order_id, req.kind, paid_amount, discount_amount)
    return {
        "success": True,
        "order_id": order_id,
        "amount": float(paid_amount),
        "list_price": float(list_price),
        "discount_amount": float(discount_amount),
        "member_discount_pct": member_discount_pct,
        "member_discount_amount": float(member_discount_amount),
        "coupon_code": coupon_code,
        "exchange_discount_pct": credit.discount_pct if credit is not None else 0,
        "pay_url": pay_url,
        "message": "订单已创建，正在跳转支付",
    }


@router.get("/payment/orders")
def my_orders(
    kind: Optional[str] = None,
    limit: int = 30,
    current_user: models.WebUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我的订单列表（充值 + 订阅合并）

    带优惠快照：原价 / 优惠金额 / 实付 / 用的哪个码。用户端要能自己对账
    （“我明明用了券，为什么订单显示全价”）。
    """
    orders: list[dict] = []

    if kind in (None, "recharge"):
        for o in db.query(models.RechargeOrder).filter(
            models.RechargeOrder.user_id == current_user.id
        ).order_by(models.RechargeOrder.created_at.desc()).limit(limit).all():
            orders.append({
                "order_id": o.order_id, "kind": "recharge",
                # 历史充值单（积分时代）：只标「充值（历史）」，不再写能量字样
                "item_name": "充值（历史）",
                "amount": float(o.price), "status": o.status,
                "list_price": float(o.list_price or 0),
                "discount_amount": float(o.discount_amount or 0),
                "coupon_code": "",
                "coupon_usage_id": o.coupon_usage_id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                # 退款留痕：用户端要能看到“为什么退了/退了多少”，否则只会来问客服
                "refunded_at": o.refunded_at.isoformat() if o.refunded_at else None,
                "refund_reason": o.refund_reason or "",
            })

    if kind in (None, "subscription"):
        for o in db.query(models.SubscriptionOrder).filter(
            models.SubscriptionOrder.user_id == current_user.id
        ).order_by(models.SubscriptionOrder.created_at.desc()).limit(limit).all():
            orders.append({
                "order_id": o.order_id, "kind": "subscription",
                "item_name": f"订阅 - {o.item_name}",
                "amount": float(o.amount), "status": o.status,
                "list_price": float(o.list_price or 0),
                "discount_amount": float(o.discount_amount or 0),
                "coupon_code": "",
                "coupon_usage_id": o.coupon_usage_id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                "refunded_at": o.refunded_at.isoformat() if o.refunded_at else None,
                "refund_reason": o.refund_reason or "",
            })

    # 优惠码回填（一次查完，不给每条订单配一个查询）
    usage_ids = {o["coupon_usage_id"] for o in orders if o.get("coupon_usage_id")}
    if usage_ids:
        rows = db.query(models.CouponUsage.id, models.CouponCode.code).join(
            models.CouponCode, models.CouponCode.id == models.CouponUsage.coupon_id
        ).filter(models.CouponUsage.id.in_(usage_ids)).all()
        code_map = {uid: code for uid, code in rows}
        for o in orders:
            o["coupon_code"] = code_map.get(o["coupon_usage_id"], "")
    for o in orders:
        o.pop("coupon_usage_id", None)

    orders.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {"orders": orders[:limit]}


# ==================== 支付回调 ====================

def _verify_yipay_notify(params: dict, key: str) -> bool:
    """易支付回调验签（MD5）

    比较用 ``hmac.compare_digest``：签名是回调方给的字符串，逐字节短路比较
    会把「对了几位」变成可测量的时间差（v2.30.0）。
    """
    sign = str(params.get("sign") or "")
    expected = _yipay_sign(params, key)
    return hmac.compare_digest(sign.lower(), expected.lower())


def _order_paid_amount(recharge_order=None, subscription_order=None) -> Optional[Decimal]:
    """订单**应付金额**（实付口径）

    下单时把订单金额统一写成实付（原价 / 优惠 / 实付三个快照见 ``create_payment_order``），
    所以这里读的就是用户真正该付的钱。
    """
    order = recharge_order if recharge_order is not None else subscription_order
    if order is None:
        return None
    raw = order.price if recharge_order is not None else order.amount
    if raw is None:
        return None
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _notify_amount(value) -> Optional[Decimal]:
    """回调里报的金额（解析不了返回 None，不是 0）"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _amount_mismatch(recharge_order, subscription_order, raw: dict) -> Optional[str]:
    """回调金额与订单是否对不上（对不上时返回给人看的说明）

    验签只证明「这条通知来自网关」；它不证明**钱数对不对**。网关配错价、订单号被复用、
    上游按别的金额收款时，旧实现照样发货（积分 / 会员都按订单发货），而钱只收了一部分。

    所以回调里报了金额就逐笔核对：对不上 **不发货**（返回 fail，让网关重试并让运维看见）。
    回调没带金额时只能记一行日志按原逻辑发货——不能因为「读不到 money」把正常付款卡死。
    """
    expected = _order_paid_amount(recharge_order, subscription_order)
    reported = _notify_amount(raw.get("money"))
    if expected is None:
        return None
    if reported is None:
        logger.warning("支付回调未带金额，无法核对（按订单实付 %s 发货）: order=%s",
                       expected, raw.get("out_trade_no"))
        return None
    if reported != expected:
        return f"金额不符：订单应付 {expected}，回调报 {reported}"
    return None


def _claim_order(db: Session, model, order) -> bool:
    """把订单从「未支付」原子地推进到 ``paid``：并发回调 / 人工补单只有一个能拿到那一行

    旧实现的判定与发货之间是读-改-写（``if status not in (paid, refunded, closed)``）：
    网关重试回调和管理员补单撞在一起时，两个事务都读到 ``pending`` → 双倍积分 / 双份订阅。
    SQLite 的单写锁能部分兜住，跳机器部署的 PostgreSQL 下就是实打实的资损。

    现在先做条件 ``UPDATE ... WHERE status NOT IN (...)``：

    - 拿到 1 行 → 由本请求发货（并发第二个请求会在行锁上等到提交，然后拿到 0 行）；
    - 拿到 0 行 → 已被别人履约（或已退款/关单），跳过发货，幂等成立。

    更新与发货在同一事务里，失败会一起回滚；``paid_at`` 不再由调用方另行赋值。
    """
    if order is None or order.status in ("paid", "refunded", "closed"):
        return False
    claimed = (
        db.query(model)
        .filter(model.id == order.id, model.status.notin_(("paid", "refunded", "closed")))
        .update({"status": "paid", "paid_at": datetime.now()}, synchronize_session=False)
    )
    if not claimed:
        return False
    # 让当前事务里的 ORM 对象与刚写入的行保持一致（后续代码与提交都用它）
    order.status = "paid"
    order.paid_at = datetime.now()
    return True


class FulfillmentError(RuntimeError):
    """履约缺料：订单该发的东西已经不存在了（套餐被删 / 用户被删等）

    这类情况**必须**让调用方回滚并把回调判成失败（网关会重试，或者转人工补单），
    不能静默返回 success——「钱收了、订单标了 paid、用户什么都没拿到」是最坏的结果，
    而且优惠券还会被照常 ``consume`` 掉。
    """


def _fulfill_order(db: Session, recharge_order=None, subscription_order=None) -> list:
    """订单履约：订阅发放，幂等（重复回调不重复发货）

    **同步函数**（体内一个 await 都没有）：async 路由调用它时必须用
    ``await run_in_threadpool(...)`` 下放线程池，别把一次提交压在事件循环上。

    **返回待发送的通知列表，不要在这里就发出去。**

    履约事务（订阅 / 订单状态）还没 ``commit`` 时，通知服务会另开一个 session 写站内信，
    在 SQLite 上这属于「读事务升级为写事务」，会被直接判为 ``database is locked``（不会等锁），
    结果是「充值成功」这类站内信被静默丢弃（只留一行日志），而钱已经收了。
    所以通知由调用方在 ``db.commit()`` 之后统一发：见 ``send_fulfill_notifications``。
    """
    # 已退款 / 已关闭的订单绝不再履约：支付回调可能晚到或重放（网关重试、管理员已经
    # 关单后又收到回调），只判 `!= "paid"` 会把退过的订单又发一遍货。
    pending: list = []
    if recharge_order:
        # 能量充值已下线（2026-11-13）：历史 pending 充值单的回调不再自动发能量，
        # 显性失败让网关/人工补单通道看见，转人工处理，不静默吞单。
        raise FulfillmentError(
            f"能量充值已下线：充值订单 {recharge_order.order_id} 无法自动履约，需人工处理"
        )

    if subscription_order and _claim_order(db, models.SubscriptionOrder, subscription_order):
        plan = db.query(models.SubscriptionPlan).filter(
            models.SubscriptionPlan.id == subscription_order.plan_id
        ).first()
        user = db.query(models.WebUser).filter(
            models.WebUser.id == subscription_order.user_id
        ).first()
        if user is None or plan is None:
            # 下单之后、支付回调之前套餐被删（或用户被删）：订单会被标成 paid 但用户
            # 什么都拿不到，优惠券还会被 consume 掉——旧实现就是在这里静静地什么都不做，
            # 然后回调返回 success。现在抛出去：调用方回滚 + 返回 fail，转人工处理。
            raise FulfillmentError(
                f"订阅履约缺料：订单 {subscription_order.order_id}"
                f"（套餐 {'已删除' if plan is None else '正常'}、"
                f"用户 {'已删除' if user is None else '正常'}）"
            )
        if user and plan:
            subscription = _grant_subscription(
                db, user, plan,
                plan.duration_days,
                realm_id=plan.realm_id,
            )
            # 记下「这条订单开出的是哪份订阅、多少天」：退款按这笔精确回滚，
            # 不靠猜用户当前那笔生效中的订阅（可能来自卡码/兑换码/别的订单）。
            subscription_order.subscription_id = subscription.id
            subscription_order.days_granted = plan.duration_days
            pending.append(dict(
                event_type="economy.subscription_success",
                user_id=user.id,
                title="🎉 订阅购买成功",
                content=(f"「{plan.name}」已开通，"
                         f"到期时间 {subscription.end_date.strftime('%Y-%m-%d')}。"),
                related_id=subscription.id,
            ))
            # 邀请返利计提（2026-11-14 钱包时代）：付款人是被邀请人时，
            # 按 invite_rebate 的配置口径（默认分档）给邀请人计提返利；
            # 钱包就绪则同事务入账并给邀请人排一条到账站内信。保存点
            # 隔离：返利侧失败绝不拖垮订阅履约；但绝不静默——按 r39
            # 口径给付款人追加一条 partial 通知，请其联系客服补发。
            try:
                with db.begin_nested():
                    from backend import invite_rebate as _invite_rebate

                    _accrual = _invite_rebate.record_payment_rebate(
                        db, user,
                        source=_invite_rebate.SOURCE_SUBSCRIPTION_ORDER,
                        source_ref=subscription_order.order_id,
                        paid_amount=subscription_order.amount)
                if _accrual is not None and _accrual.status == _invite_rebate.STATUS_CREDITED:
                    pending.append(dict(
                        event_type="economy.invite_rebate",
                        user_id=_accrual.inviter_id,
                        title="💰 邀请返利到账",
                        content=(f"好友付费 ¥{subscription_order.amount}，"
                                 f"返利 ¥{_accrual.rebate_amount:.2f} 已存入你的钱包余额。"),
                        related_id=None,
                    ))
            except Exception:  # noqa: BLE001
                logger.exception("邀请返利计提失败: %s", subscription_order.order_id)
                pending.append(dict(
                    event_type=AdminEvent.FULFILL_PARTIAL,
                    user_id=user.id,
                    title="⚠️ 订阅成功，邀请返利计提失败",
                    content=(f"订单 {subscription_order.order_id} 的订阅已开通，"
                             "但邀请返利计提失败，请联系客服补发。"),
                    related_id=None,
                ))
        coupons.consume(db, subscription_order.coupon_usage_id)

    return pending


async def send_fulfill_notifications(pending: list) -> None:
    """履约事务提交之后发送站内信（失败只记日志，不影响已到账的订单）"""
    # r46：内联 try/except 并入统一 quiet 守卫（横切只许一套）。
    for item in pending or []:
        await notify_admin_event_quiet(
            log_ctx=f"fulfill user_id={item.get('user_id')}", **item)


@router.api_route("/payment/notify", methods=["GET", "POST"], response_class=PlainTextResponse)
async def payment_notify(request: Request, db: Session = Depends(get_db)):
    """易支付异步回调：验签 → 履约 → 返回 success

    唯一必须 await 的是读表单与发通知（网关回调最忌讳全站压在事件循环上等一次提交）；
    验签 / 查单 / 履约整段是同步 SQLAlchemy，下放线程池执行。
    """
    raw = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            raw.update({k: v for k, v in form.items() if isinstance(v, str)})
        except Exception:  # noqa: BLE001
            pass

    out_trade_no = raw.get("out_trade_no", "")
    trade_status = raw.get("trade_status", "")
    logger.info("支付回调: order=%s status=%s", out_trade_no, trade_status)

    def _notify() -> tuple:
        """验签 / 查单 / 金额核对 / 履约 → (应答文本, 待发通知)

        履约失败一律返回 "fail"（网关会重试，或者转人工补单），只有真的发出货
        才返回 "success"——不能出现「钱收了、订单标了 paid、用户什么都没拿到」。
        """
        key = _get_config(db, "payment_partner_key", "").strip()
        if not key:
            return "fail", []
        if not _verify_yipay_notify(raw, key):
            logger.warning("支付回调验签失败: order=%s", out_trade_no)
            return "fail", []
        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return "success", []  # 非成功状态直接确认，避免重复通知

        recharge_order = db.query(models.RechargeOrder).filter(
            models.RechargeOrder.order_id == out_trade_no
        ).first()
        subscription_order = db.query(models.SubscriptionOrder).filter(
            models.SubscriptionOrder.order_id == out_trade_no
        ).first()

        if not recharge_order and not subscription_order:
            logger.warning("支付回调订单不存在: %s", out_trade_no)
            return "fail", []

        mismatch = _amount_mismatch(recharge_order, subscription_order, raw)
        if mismatch:
            logger.error("支付回调被拒（%s）: order=%s", mismatch, out_trade_no)
            return "fail", []

        try:
            pending = _fulfill_order(db, recharge_order=recharge_order,
                                     subscription_order=subscription_order)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("订单履约失败: %s", out_trade_no)
            return "fail", []
        return "success", pending

    reply, pending = await run_in_threadpool(_notify)

    # 先提交再发通知：见 _fulfill_order 的说明（履约中另开会话写站内信会撞 SQLite 写锁）
    if pending:
        await send_fulfill_notifications(pending)
    return reply


@router.api_route("/payment/return", methods=["GET"], response_class=JSONResponse)
async def payment_return(request: Request, db: Session = Depends(get_db)):
    """同步跳转：返回前端跳转地址（前端钱包页轮询订单状态）"""
    order_id = request.query_params.get("out_trade_no", "")
    return {"success": True, "order_id": order_id, "redirect": f"/wallet?order={order_id}"}
