"""经济系统冒烟测试（货币下线后）：邀请关系 → 兑换码 → 支付订单 → 能量码拒绝

2026-11-13 能量/积分/活力货币下线后，本冒烟只验仍存活的链路：
邀请只落关系（推广天数奖励仍发）、兑换码订阅/折扣核销、易支付签名、
订阅订单履约发订阅、历史充值单履约显性失败、能量型卡码拒绝核销。
直接调用函数级逻辑（不启动 HTTP 服务）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 不写死库文件名：交给 backend.database 解析（新装 aetrix_unified.db，老部署沿用原库）
os.environ.setdefault("DATABASE_TYPE", "sqlite")

from datetime import datetime, timedelta
from decimal import Decimal

from backend.database import SessionLocal, init_db
from backend import models
from backend.security import hash_password
from backend.api.economy import (
    _fulfill_order, _grant_subscription, _redeem_exchange_core,
    _yipay_sign, _verify_yipay_notify, FulfillmentError, send_fulfill_notifications,
)
from backend.api.invitation import apply_invitation, _generate_invite_code
from backend.codes import redeem_code as redeem_registration_code

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ✅" if cond else "  ❌") + f" {name}")


def main():
    init_db()
    db = SessionLocal()
    # 邀请人奖励只由推广发放：冒烟按「天数型」种配置（余额型已随货币下线）
    for k, v in (("promotion_reward_enabled", "true"),
                 ("promotion_reward_type", "days"),
                 ("promotion_reward_days", "7")):
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(models.SystemConfig(key=k, value=v))
    db.commit()
    now = datetime.now()

    # ---- 准备测试用户（唯一后缀避免与现有数据冲突） ----
    suffix = now.strftime("%H%M%S%f")
    u1 = models.WebUser(username=f"smoke_inv_{suffix}", password_hash=hash_password("secret123"))
    u2 = models.WebUser(username=f"smoke_invitee_{suffix}", password_hash=hash_password("secret123"))
    db.add_all([u1, u2])
    db.commit()
    db.refresh(u1)
    db.refresh(u2)

    # ---- 1. 邀请：只落关系 + 推广天数奖励，不再发积分 ----
    print("\n[1] 邀请系统")
    code_str = _generate_invite_code(db)
    code = models.InvitationCode(code=code_str, user_id=u1.id)
    db.add(code)
    db.commit()
    result = apply_invitation(db, u2, code_str.lower())  # 测试大小写归一
    db.commit()
    check("邀请码应用成功", result.get("applied") is True)
    check("邀请双方均不再获得积分", (u1.points or 0) == 0 and (u2.points or 0) == 0)
    record = db.query(models.InvitationRecord).filter(
        models.InvitationRecord.invitee_id == u2.id).first()
    check("邀请记录已建立", record is not None and record.inviter_id == u1.id)
    inviter_sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == u1.id,
        models.UserSubscription.status == "active").first()
    check("邀请人获得推广天数奖励（7 天订阅）",
          inviter_sub is not None and inviter_sub.end_date > now)

    # ---- 2. 兑换码（订阅型 + 折扣型 + 能量型拒绝） ----
    print("\n[2] 兑换码")
    plan = models.SubscriptionPlan(name=f"smoke_plan_{suffix}", price=Decimal("19.90"),
                                   duration_days=30)
    db.add(plan)
    db.commit()
    db.refresh(plan)

    ex_sub = models.ExchangeCode(code="SMOKESUB" + suffix[-4:], type="subscription",
                                 plan_id=plan.id, duration_days=15)
    ex_disc = models.ExchangeCode(code="SMOKEDISC" + suffix[-4:], type="discount",
                                  discount_pct=20)
    ex_pts = models.ExchangeCode(code="SMOKEPT" + suffix[-4:], type="points", points_value=88)
    db.add_all([ex_sub, ex_disc, ex_pts])
    db.commit()

    res = _redeem_exchange_core(db, u2, ex_sub.code)
    db.refresh(u2)
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id == u2.id,
        models.UserSubscription.status == "active").first()
    check("订阅型兑换发放", res.get("success") is True and sub is not None and sub.end_date > now)

    res = _redeem_exchange_core(db, u2, ex_disc.code)
    credit = db.query(models.ExchangeDiscountCredit).filter(
        models.ExchangeDiscountCredit.user_id == u2.id).first()
    check("折扣型兑换发放折扣权益", res.get("success") is True and credit is not None)

    from fastapi import HTTPException
    try:
        _redeem_exchange_core(db, u2, ex_pts.code)
        pts_rejected = False
    except HTTPException:
        db.rollback()
        pts_rejected = True
    db.refresh(ex_pts)
    check("能量型兑换码被拒绝且不消耗次数", pts_rejected and (ex_pts.use_count or 0) == 0)

    # ---- 3. 订阅顺延 ----
    print("\n[3] 订阅顺延")
    sub_id_before = sub.id
    end_before = sub.end_date
    sub2 = _grant_subscription(db, u2, plan, 10)
    db.commit()
    db.refresh(sub)
    check("已有订阅则顺延",
          sub2.id == sub_id_before
          and (sub.end_date - end_before).days == 10)

    # ---- 4. 能量型注册码拒绝核销（不烧码） ----
    print("\n[4] 能量卡码拒绝")
    reg_pts = models.RegistrationCode(
        code="SMOKEPTS" + suffix[-4:], code_type=2, max_uses=1, use_count=0,
        is_active=True, days=30, reward_type="points", points_value=50,
        expires_at=now + timedelta(days=30))
    db.add(reg_pts)
    db.commit()
    res = redeem_registration_code(db, u2, reg_pts.code)
    db.rollback()
    db.refresh(reg_pts)
    check("能量注册码核销被拒", res.get("success") is False and "下线" in (res.get("message") or ""))
    check("能量注册码未被消耗", (reg_pts.use_count or 0) == 0)

    # ---- 5. 支付签名（易支付 MD5） ----
    print("\n[5] 支付网关签名")
    key = "testkey123"
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "RB1234567890",
        "notify_url": "https://site.example/notify", "return_url": "https://site.example/return",
        "name": "订阅购买", "money": "19.90",
    }
    sign = _yipay_sign(params, key)
    check("签名生成（32 位 MD5）", len(sign) == 32)
    check("回调验签通过", _verify_yipay_notify({**params, "sign": sign, "sign_type": "MD5"}, key))
    check("篡改金额验签失败", not _verify_yipay_notify(
        {**params, "money": "0.01", "sign": sign, "sign_type": "MD5"}, key))

    # ---- 6. 订阅订单履约幂等 ----
    print("\n[6] 订单履约")
    import asyncio

    order = models.SubscriptionOrder(
        order_id=f"SMOKESUB{suffix}", user_id=u2.id, plan_id=plan.id,
        item_name=plan.name, amount=Decimal("19.90"),
        payment_method="alipay", status="pending")
    db.add(order)
    db.commit()

    loop = asyncio.get_event_loop()
    end_before = sub.end_date
    pending = _fulfill_order(db, subscription_order=order)
    db.commit()
    db.refresh(order)
    db.refresh(sub)
    check("订阅履约发放（顺延套餐天数）",
          order.status == "paid" and (sub.end_date - end_before).days == plan.duration_days)
    # 钱包在位且默认开：u2 付 ¥19.90 → 邀请人 u1 返利 ¥1.00 即时入账，
    # 履约待发通知是两条（订阅成功 + 返利到账），不再是旧的「恰一条」。
    sub_items = [p for p in pending
                 if p.get("event_type") == "economy.subscription_success"]
    rebate_items = [p for p in pending
                    if p.get("event_type") == "economy.invite_rebate"]
    check("履约返回待发通知（订阅成功恰一条＋返利到账在位）",
          isinstance(pending, list) and len(sub_items) == 1
          and len(rebate_items) == 1
          and rebate_items[0].get("user_id") == u1.id
          and "¥1.00" in (rebate_items[0].get("content") or ""))

    loop.run_until_complete(send_fulfill_notifications(pending))
    msg = db.query(models.StationMessage).filter(
        models.StationMessage.to_user_id == u2.id,
        models.StationMessage.title.like("%订阅购买成功%"),
    ).first()
    check("订阅成功站内信真的落库", msg is not None)

    pending2 = _fulfill_order(db, subscription_order=order)
    db.commit()
    check("重复回调幂等（不重复发货）", pending2 == [])

    # ---- 7. 历史充值单履约显性失败（转人工，不静默吞单） ----
    print("\n[7] 历史充值单")
    pkg = models.RechargePackage(name=f"smoke_pkg_{suffix}", amount=500, price=Decimal("25.00"))
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    recharge_order = models.RechargeOrder(
        order_id=f"SMOKERC{suffix}", user_id=u2.id, package_id=pkg.id,
        amount=500, price=Decimal("25.00"), payment_method="alipay", status="pending")
    db.add(recharge_order)
    db.commit()
    try:
        _fulfill_order(db, recharge_order=recharge_order)
        fulfill_raised = False
    except FulfillmentError:
        db.rollback()
        fulfill_raised = True
    db.refresh(recharge_order)
    check("充值单履约显性失败且订单保持 pending",
          fulfill_raised and recharge_order.status == "pending")

    # ---- 清理测试数据 ----
    uids = [u1.id, u2.id]
    db.query(models.StationMessage).filter(
        models.StationMessage.to_user_id.in_(uids)).delete(synchronize_session=False)
    db.query(models.ExchangeDiscountCredit).filter(
        models.ExchangeDiscountCredit.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(models.UserSubscription).filter(
        models.UserSubscription.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(models.PromotionReward).filter(
        models.PromotionReward.inviter_id.in_(uids)).delete(synchronize_session=False)
    for obj in [order, recharge_order, pkg, ex_sub, ex_disc, ex_pts, reg_pts, plan, record, code]:
        try:
            db.delete(obj)
        except Exception:
            pass
    db.query(models.WebUser).filter(models.WebUser.id.in_(uids)).delete(synchronize_session=False)
    db.commit()
    db.close()

    print(f"\n{'=' * 40}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("🎉 经济系统冒烟测试全部通过")


if __name__ == "__main__":
    main()
