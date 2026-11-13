#!/usr/bin/env python3
"""订单关单与退款冒烟测试（v2.9.0；2026-11-13 货币下线改版）

重点：

1. **关单只针对未支付订单**：`pending → closed`，不碰权益、不打扰用户；
   已支付订单走退款，重复关单 / 对已支付订单关单都要被拒；
2. **订阅退款回滚天数**：按履约时记下的 `subscription_id` / `days_granted` 精确回滚；
   回滚后已过期则置 `cancelled`（真实撤销，与自然到期不是一回事），
   若用户还有别的来源的天数则保持生效中；也可只退款不回滚权益；
3. **历史充值单（能量充值）**：货币已下线，补单发货必须显性失败（400 +
   转人工处理），不许静默吞掉、也不许把订单标成已支付；这类订单只能关单收口；
4. **退过的订单不能再被履约**：晚到/重放的支付回调与「补单」都不能把退过的订单再发一次货；
5. **留痕与可见性**：订单带 `refunded_at` / `refund_reason`，写审计，
   用户收到站内信、用户端订单列表能看到状态与原因；管理端有退款/关单记录列表；
6. **鉴权**：普通用户与未登录都碰不到这些接口；
7. **支付回调金额核对**：验签之外金额必须与订单精确相等才发货；
8. **履约缺料必 fail**：套餐/用户被删时回调返回 fail、订单保持未支付、
   优惠券额度不被 consume，修好后恢复正常。

（原「充值退款按积分账本冲正 + 撤回返利」「余额不足/允许扣负」两节已随
能量货币于 2026-11-13 下线删除：积分冲正腿中和、返利体系删除。）

用法：python scripts/smoke_test_refunds.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_TYPE"] = "sqlite"
os.environ["REDIS_ENABLED"] = "false"
os.environ["SECRET_KEY"] = "e2e-test-secret-key-not-for-production"

WORK = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(WORK, 'refunds.db')}"

from datetime import datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from backend import models, realms  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.security import hash_password  # noqa: E402

init_db()

from backend.main import app  # noqa: E402
from backend.integrations.store import invalidate  # noqa: E402

client = TestClient(app)

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


# ==================== 播种 ====================

with SessionLocal() as db:
    realm = realms.active_realm(db)
    staff = models.WebUser(username="refund_staff", password_hash=hash_password("staffpass123"),
                           is_staff=True, admin_role="super", is_active=True)
    buyer = models.WebUser(username="refund_buyer", password_hash=hash_password("buyerpass123"),
                           is_active=True, points=0)
    db.add_all([staff, buyer])
    db.commit()

    # 历史充值套餐行保留（recharge_orders.package_id 引用它造历史单）
    pkg = models.RechargePackage(name="100 积分包（历史）", amount=100, price=100, bonus=0, is_active=True)
    plan = models.SubscriptionPlan(name="月卡", price=30, duration_days=30,
                                   realm_id=realm.id, is_active=True)
    db.add_all([pkg, plan])
    db.commit()

    realm_id, plan_id, pkg_id = realm.id, plan.id, pkg.id
    buyer_id = buyer.id


def make_recharge_order(order_id: str) -> None:
    with SessionLocal() as db:
        db.add(models.RechargeOrder(order_id=order_id, user_id=buyer_id, package_id=pkg_id,
                                    amount=100, price=100, payment_method="alipay",
                                    status="pending"))
        db.commit()


def make_subscription_order(order_id: str) -> None:
    with SessionLocal() as db:
        db.add(models.SubscriptionOrder(order_id=order_id, user_id=buyer_id, plan_id=plan_id,
                                        item_name="月卡", amount=30, payment_method="alipay",
                                        status="pending"))
        db.commit()


def points_of(user_id: int) -> int:
    with SessionLocal() as db:
        return int(db.query(models.WebUser.points).filter(
            models.WebUser.id == user_id).scalar() or 0)


def order_row(order_id: str):
    with SessionLocal() as db:
        row = db.query(models.RechargeOrder).filter(
            models.RechargeOrder.order_id == order_id).first()
        if row:
            return row
        return db.query(models.SubscriptionOrder).filter(
            models.SubscriptionOrder.order_id == order_id).first()


def station_titles(user_id: int) -> list[str]:
    with SessionLocal() as db:
        return [m.title for m in db.query(models.StationMessage).filter(
            models.StationMessage.to_user_id == user_id).order_by(models.StationMessage.id).all()]


def subscriptions_of(user_id: int) -> int:
    """该用户**生效中**的订阅条数"""
    with SessionLocal() as db:
        return db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id,
            models.UserSubscription.status == "active",
        ).count()


def sub_days_total(user_id: int) -> int:
    with SessionLocal() as db:
        rows = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id).all()
        return sum(int((r.end_date - r.start_date).days) for r in rows
                   if r.end_date and r.start_date)


r = client.post("/api/user/auth/login", json={"username": "refund_staff", "password": "staffpass123"})
check("管理员登录成功", r.status_code == 200, f"status={r.status_code}")
ADMIN_H = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = client.post("/api/user/auth/login", json={"username": "refund_buyer", "password": "buyerpass123"})
check("普通用户登录成功", r.status_code == 200, f"status={r.status_code}")
USER_H = {"Authorization": f"Bearer {r.json()['access_token']}"}

# ==================== 1. 关单 ====================

print("\n--- 关单：未支付订单作废 ---")

make_recharge_order("RC-CLOSE-1")
r = client.post("/api/admin/economy/orders/RC-CLOSE-1/close", headers=ADMIN_H)
check("可关闭待支付订单", r.status_code == 200 and r.json()["status"] == "closed",
      f"status={r.status_code} {r.text[:120]}")
row = order_row("RC-CLOSE-1")
check("订单记为已关闭并带时间", row.status == "closed" and row.closed_at is not None)
check("关单不碰积分", points_of(buyer_id) == 0)
check("关单不打扰用户（他并没付钱）", "↩️ 订单已退款" not in station_titles(buyer_id))

r = client.post("/api/admin/economy/orders/RC-CLOSE-1/close", headers=ADMIN_H)
check("重复关单被拒", r.status_code == 400, f"status={r.status_code}")

r = client.post("/api/admin/economy/orders/RC-CLOSE-1/refund", headers=ADMIN_H, json={"reason": "x"})
check("已关闭订单不能退款", r.status_code == 400, f"status={r.status_code} {r.text[:80]}")

r = client.post("/api/admin/economy/orders/NOT-EXIST/close", headers=ADMIN_H)
check("不存在的订单返回 404", r.status_code == 404, f"status={r.status_code}")

# ==================== 2. 历史充值单：补单发货显性拒绝 ====================

print("\n--- 历史充值单：能量已下线，补单转人工 ---")

make_recharge_order("RC-REFUND-1")
r = client.post("/api/admin/economy/orders/RC-REFUND-1/mark-paid", headers=ADMIN_H)
check("历史充值单补单被显性拒绝（400）", r.status_code == 400, f"status={r.status_code} {r.text[:120]}")
check("拒绝说明含转人工口径", "人工" in r.text, r.text[:160])
check("拒绝后订单仍是待支付（不静默标 paid）", order_row("RC-REFUND-1").status == "pending",
      order_row("RC-REFUND-1").status)
check("拒绝后不发任何积分", points_of(buyer_id) == 0, str(points_of(buyer_id)))

# 待支付的历史充值单只能关单收口
r = client.post("/api/admin/economy/orders/RC-REFUND-1/close", headers=ADMIN_H)
check("历史充值单可关单收口", r.status_code == 200 and order_row("RC-REFUND-1").status == "closed",
      f"status={r.status_code}")

# 直接调履约核心重放：同样显性失败、不发货
with SessionLocal() as db:
    from backend.api.economy import FulfillmentError, _fulfill_order
    order = db.query(models.RechargeOrder).filter(
        models.RechargeOrder.order_id == "RC-REFUND-1").first()
    raised = False
    try:
        _fulfill_order(db, recharge_order=order)
    except FulfillmentError:
        raised = True
    db.rollback()
check("履约核心对充值单抛 FulfillmentError", raised)
check("重放后仍不发货", points_of(buyer_id) == 0, str(points_of(buyer_id)))

# ==================== 3. 订阅退款：精确回滚天数 ====================

print("\n--- 订阅退款：按订单回滚天数 ---")

make_subscription_order("SO-REFUND-1")
r = client.post("/api/admin/economy/orders/SO-REFUND-1/mark-paid", headers=ADMIN_H)
check("订阅订单履约成功", r.status_code == 200, f"status={r.status_code} {r.text[:100]}")
row = order_row("SO-REFUND-1")
check("履约时记下了订阅与天数",
      row.subscription_id is not None and row.days_granted == 30,
      f"{row.subscription_id} / {row.days_granted}")

with SessionLocal() as db:
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row.subscription_id).first()
    end_before = sub.end_date

r = client.post("/api/admin/economy/orders/SO-REFUND-1/refund", headers=ADMIN_H,
                json={"reason": "用户申请退款"})
body = r.json() if r.status_code == 200 else {}
check("订阅退款成功", r.status_code == 200, f"status={r.status_code} {r.text[:160]}")
check("回滚了 30 天", body.get("revoked_days") == 30, str(body))
check("积分冲正恒为 0（货币已下线）", body.get("revoked_points") == 0, str(body))

with SessionLocal() as db:
    sub = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row.subscription_id).first()
    check("到期时间精确回退 30 天",
          abs((end_before - sub.end_date).days) == 30,
          f"{end_before} → {sub.end_date}")
    check("回滚后已过期 → 状态置为 cancelled（真实撤销）", sub.status == "cancelled", sub.status)

row = order_row("SO-REFUND-1")
check("订单记为已退款并留原因", row.status == "refunded"
      and row.refunded_at is not None and row.refund_reason == "用户申请退款",
      f"{row.status} / {row.refund_reason}")
check("退款后用户不再是会员", client.get("/api/user/auth/me", headers=USER_H).json()["is_vip"] is False)
check("用户收到订阅退款站内信", "↩️ 订单已退款" in station_titles(buyer_id))
with SessionLocal() as db:
    kinds = [m.message_type for m in db.query(models.StationMessage).filter(
        models.StationMessage.to_user_id == buyer_id,
        models.StationMessage.title == "↩️ 订单已退款").all()]
check("退款归到 economy 类（不复用 subscription.expired 语义）",
      kinds and all(k == "economy" for k in kinds), str(kinds))

r = client.post("/api/admin/economy/orders/SO-REFUND-1/refund", headers=ADMIN_H, json={"reason": "再来一次"})
check("重复退款被拒", r.status_code == 400, f"status={r.status_code}")

# 退过的订单不能因为回调/补单再发一次货
r = client.post("/api/admin/economy/orders/SO-REFUND-1/mark-paid", headers=ADMIN_H)
check("退过的订阅单不再被补单履约", subscriptions_of(buyer_id) == 0,
      f"subs={subscriptions_of(buyer_id)}")

# 用户还有别的来源的天数时：只回滚这一笔，不整单撤销
print("\n--- 订阅退款：用户还有别的天数时保持生效 ---")

with SessionLocal() as db:
    # 先手动给 60 天（比如卡码发的），再买 30 天
    db.add(models.UserSubscription(user_id=buyer_id, plan_id=plan_id, realm_id=realm_id,
                                   start_date=datetime.now(),
                                   end_date=datetime.now() + timedelta(days=60),
                                   status="active"))
    db.commit()

make_subscription_order("SO-REFUND-2")
client.post("/api/admin/economy/orders/SO-REFUND-2/mark-paid", headers=ADMIN_H)
with SessionLocal() as db:
    row2 = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == "SO-REFUND-2").first()
    sub2 = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row2.subscription_id).first()
    end_before2 = sub2.end_date

r = client.post("/api/admin/economy/orders/SO-REFUND-2/refund", headers=ADMIN_H,
                json={"reason": "重复购买"})
check("有剩余天数时可退款", r.status_code == 200, f"status={r.status_code} {r.text[:120]}")
with SessionLocal() as db:
    sub2 = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row2.subscription_id).first()
    check("只回滚这一笔的 30 天", abs((end_before2 - sub2.end_date).days) == 30,
          f"{end_before2} → {sub2.end_date}")
    check("仍有剩余天数 → 保持生效中", sub2.status == "active", sub2.status)
check("用户仍是会员", client.get("/api/user/auth/me", headers=USER_H).json()["is_vip"] is True)

# 不退权益（只改状态）的选项
print("\n--- 退款可选不回滚权益（仅记账）---")

make_subscription_order("SO-REFUND-3")
client.post("/api/admin/economy/orders/SO-REFUND-3/mark-paid", headers=ADMIN_H)
with SessionLocal() as db:
    row3 = db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == "SO-REFUND-3").first()
    sub3 = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row3.subscription_id).first()
    end_before3 = sub3.end_date
r = client.post("/api/admin/economy/orders/SO-REFUND-3/refund", headers=ADMIN_H,
                json={"reason": "客服补偿", "revoke_entitlement": False})
check("可只退款不回滚权益（补偿场景）", r.status_code == 200 and r.json()["revoked_days"] == 0,
      f"status={r.status_code} {r.text[:120]}")
with SessionLocal() as db:
    sub3 = db.query(models.UserSubscription).filter(
        models.UserSubscription.id == row3.subscription_id).first()
    check("不回滚时到期时间不变", sub3.end_date == end_before3, f"{end_before3} → {sub3.end_date}")

# ==================== 4. 留痕 / 记录列表 / 用户端可见性 ====================

print("\n--- 留痕与可见性 ---")

r = client.get("/api/admin/economy/refunds", headers=ADMIN_H)
check("可读退款/关单记录", r.status_code == 200, f"status={r.status_code}")
records = r.json()["records"]
ids = {x["order_id"] for x in records}
check("记录里包含退款与关单两类", "SO-REFUND-1" in ids and "RC-CLOSE-1" in ids, str(sorted(ids)))
check("记录带原因与用户名",
      all(x.get("username") for x in records) and
      any(x["order_id"] == "SO-REFUND-1" and x["reason"] == "用户申请退款" for x in records),
      str(records[:2]))

with SessionLocal() as db:
    logs = db.query(models.AdminLog).filter(
        models.AdminLog.action.in_(("economy_refund_order", "economy_close_order"))).all()
    check("关单与退款都写了审计", len(logs) >= 4, f"count={len(logs)}")

r = client.get("/api/user/economy/payment/orders", headers=USER_H)
check("用户端订单列表可读", r.status_code == 200, f"status={r.status_code}")
refunded = [o for o in r.json()["orders"] if o["order_id"] == "SO-REFUND-1"]
check("用户端能看到退款状态与原因",
      bool(refunded) and refunded[0]["status"] == "refunded"
      and refunded[0]["refund_reason"] == "用户申请退款",
      str(refunded[:1]))

# ==================== 5. 鉴权 ====================

print("\n--- 鉴权 ---")

check("未登录不能关单",
      client.post("/api/admin/economy/orders/RC-CLOSE-1/close").status_code in (401, 403))
check("普通用户不能关单",
      client.post("/api/admin/economy/orders/RC-CLOSE-1/close", headers=USER_H).status_code in (401, 403))
check("普通用户不能退款",
      client.post("/api/admin/economy/orders/SO-REFUND-1/refund", headers=USER_H,
                  json={"reason": "x"}).status_code in (401, 403))
check("普通用户看不到退款记录",
      client.get("/api/admin/economy/refunds", headers=USER_H).status_code in (401, 403))

# ==================== 6. 支付回调：金额必须与订单对得上 ====================

# 验签只证明「这条通知来自网关」，不证明钱数对不对。
# 旧实现只看验签 + 订单号，回调报 0.01 元也照样发货。
# 这里用**独立的买家**走订阅单（充值单已随货币下线，见第 2 节）。

print("\n--- 支付回调：金额核对 ---")

from backend.api.economy import _yipay_sign  # noqa: E402

GATEWAY_KEY = "refund-notify-key"
with SessionLocal() as db:
    for key, value in (("payment_partner_key", GATEWAY_KEY), ("payment_partner_id", "1001")):
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
        if row:
            row.value = value
        else:
            db.add(models.SystemConfig(key=key, value=value))
    notify_buyer = models.WebUser(username="refund_notify_buyer",
                                  password_hash=hash_password("notifypass123"),
                                  is_active=True, points=0)
    db.add(notify_buyer)
    db.commit()
    invalidate("payment_partner_key", "payment_partner_id")  # 热缓存失效：测试直接写库，手动清缓存
    notify_buyer_id = notify_buyer.id


def make_notify_order(order_id: str, price: str = "30.00") -> None:
    with SessionLocal() as db:
        db.add(models.SubscriptionOrder(order_id=order_id, user_id=notify_buyer_id, plan_id=plan_id,
                                        item_name="月卡", amount=Decimal(price),
                                        payment_method="alipay", status="pending"))
        db.commit()


def notify_call(order_id: str, money: str | None, key: str = GATEWAY_KEY):
    params = {
        "pid": "1001", "trade_no": "T-NOTIFY", "out_trade_no": order_id,
        "type": "alipay", "name": "月卡", "trade_status": "TRADE_SUCCESS",
    }
    if money is not None:
        params["money"] = money
    params["sign"] = _yipay_sign(params, key)
    params["sign_type"] = "MD5"
    return client.get("/api/user/economy/payment/notify", params=params)


make_notify_order("SO-NOTIFY-LOW")
r = notify_call("SO-NOTIFY-LOW", "0.01")
check("少付钱的回调被拒（fail）", r.text.strip() == "fail", f"{r.status_code} {r.text[:60]}")
check("被拒后订单仍未支付", order_row("SO-NOTIFY-LOW").status == "pending",
      order_row("SO-NOTIFY-LOW").status)
check("被拒后不发货（无订阅）", subscriptions_of(notify_buyer_id) == 0,
      str(subscriptions_of(notify_buyer_id)))

make_notify_order("SO-NOTIFY-HIGH")
r = notify_call("SO-NOTIFY-HIGH", "30.01")
check("多付/报错金额也不放行（必须精确相等）", r.text.strip() == "fail", f"{r.text[:60]}")
check("依然未发货", subscriptions_of(notify_buyer_id) == 0, str(subscriptions_of(notify_buyer_id)))

make_notify_order("SO-NOTIFY-EXACT")
r = notify_call("SO-NOTIFY-EXACT", "30.00")
check("金额对得上就发货", r.text.strip() == "success", f"{r.text[:60]}")
check("订单已支付且订阅到账（30 天）", order_row("SO-NOTIFY-EXACT").status == "paid"
      and sub_days_total(notify_buyer_id) == 30, f"{sub_days_total(notify_buyer_id)}")

# 网关没带 money 的旧口径：只记日志，不能把正常付款卡死
make_notify_order("SO-NOTIFY-NOMONEY")
r = notify_call("SO-NOTIFY-NOMONEY", None)
check("回调未带金额时仍按订单发货（兼容旧网关）",
      r.text.strip() == "success" and sub_days_total(notify_buyer_id) == 60,
      f"{r.text[:60]} / {sub_days_total(notify_buyer_id)}")

make_notify_order("SO-NOTIFY-BADSIGN")
r = notify_call("SO-NOTIFY-BADSIGN", "30.00", key="wrong-key")
check("错密钥的回调仍被拦（验签之前）", r.text.strip() == "fail" and sub_days_total(notify_buyer_id) == 60,
      str(sub_days_total(notify_buyer_id)))


# ==================== 7. 履约缺料：套餐/用户被删时必须 fail（不能静默 success）====================

print("\n--- 履约缺料：下单后套餐被删 ---")

# 旧实现：`_fulfill_order` 里 `if user and plan:` 不成立就什么都不做，
# 但订单已经被 `_claim_order` 原子标成 paid、优惠券还会被 consume——
# 用户付了钱、订单显示已支付、权益一点没到，还没有任何报错。
# 现在抛 FulfillmentError → 回调回滚并返回 fail（网关会重试 / 转人工）。

from backend import coupons as coupons_mod  # noqa: E402


def make_coupon_usage(order_id: str, user_id: int) -> int:
    """造一条「已预订」的优惠券额度（真实链路由 coupons.reserve 产生）"""
    with SessionLocal() as db:
        coupon = models.CouponCode(code=f"COUP-{order_id}", kind="subscription",
                                   discount_type="fixed", value=5, max_uses=10,
                                   use_count=1, is_active=True)
        db.add(coupon)
        db.flush()
        usage = models.CouponUsage(coupon_id=coupon.id, user_id=user_id, order_id=order_id,
                                   kind="subscription", status=coupons_mod.RESERVED,
                                   list_price=Decimal("30.00"), discount_amount=Decimal("5.00"),
                                   paid_amount=Decimal("25.00"))
        db.add(usage)
        db.commit()
        return usage.id


def make_doomed_subscription_order(order_id: str, *, user_id: int, plan_id_) -> int:
    usage_id = make_coupon_usage(order_id, user_id)
    with SessionLocal() as db:
        db.add(models.SubscriptionOrder(
            order_id=order_id, user_id=user_id, plan_id=plan_id_, item_name="限时套餐",
            amount=Decimal("25.00"), payment_method="alipay", status="pending",
            coupon_usage_id=usage_id,
        ))
        db.commit()
    return usage_id


# 7.1 套餐在下单之后被删掉
with SessionLocal() as db:
    doomed_plan = models.SubscriptionPlan(name="即将下架的套餐", price=30, duration_days=30,
                                         is_active=True, realm_id=realm_id)
    db.add(doomed_plan)
    db.commit()
    doomed_plan_id = doomed_plan.id

doomed_order = "SO-NOTIFY-NOPLAN"
make_doomed_subscription_order(doomed_order, user_id=notify_buyer_id, plan_id_=doomed_plan_id)
before_subs = subscriptions_of(notify_buyer_id)
before_days_total = sub_days_total(notify_buyer_id)

with SessionLocal() as db:
    db.query(models.SubscriptionPlan).filter(
        models.SubscriptionPlan.id == doomed_plan_id).delete(synchronize_session=False)
    db.commit()

r = notify_call(doomed_order, "25.00")
check("套餐被删后回调返回 fail（不再静默 success）", r.text.strip() == "fail",
      f"{r.status_code} {r.text[:80]}")
check("缺料时订单保持未支付（没有被标成 paid）",
      order_row(doomed_order).status == "pending", str(order_row(doomed_order).status))
check("缺料时不给用户发订阅", subscriptions_of(notify_buyer_id) == before_subs,
      f"{before_subs} -> {subscriptions_of(notify_buyer_id)}")
with SessionLocal() as db:
    usage = db.query(models.CouponUsage).filter(
        models.CouponUsage.order_id == doomed_order).first()
check("缺料时优惠券额度没有被 consume（还留在 reserved）",
      usage is not None and usage.status == coupons_mod.RESERVED,
      f"status={getattr(usage, 'status', None)}")

# 7.2 用户在下单之后被删（订阅订单指向一个不存在的用户）
orphan_order = "SO-NOTIFY-NOUSER"
with SessionLocal() as db:
    ghost = models.WebUser(username="refund_ghost", password_hash="x", is_active=True)
    db.add(ghost)
    db.commit()
    ghost_id = ghost.id
    db.add(models.SubscriptionOrder(
        order_id=orphan_order, user_id=ghost_id, plan_id=plan_id, item_name="月卡",
        amount=Decimal("30.00"), payment_method="alipay", status="pending",
    ))
    db.commit()
    db.query(models.WebUser).filter(models.WebUser.id == ghost_id).delete(
        synchronize_session=False)
    db.commit()

r = notify_call(orphan_order, "30.00")
check("用户被删后回调返回 fail", r.text.strip() == "fail", f"{r.status_code} {r.text[:80]}")
check("用户被删时订单保持未支付", order_row(orphan_order).status == "pending",
      str(order_row(orphan_order).status))

# 7.3 同一笔缺料订单：人工补单（后台）也要给出可读的 400，而不是 500
r = client.post(f"/api/admin/economy/orders/{doomed_order}/mark-paid", headers=ADMIN_H,
                json={})
check("人工补单遇到缺料返回 400 + 说明（不是 500）",
      r.status_code == 400 and "履约失败" in r.text, f"{r.status_code} {r.text[:120]}")
check("补单失败后订单仍未支付", order_row(doomed_order).status == "pending",
      str(order_row(doomed_order).status))

# 7.4 修好之后（套餐重建 + 补单）履约应当恢复正常
with SessionLocal() as db:
    fixed_plan = models.SubscriptionPlan(name="恢复的套餐", price=25, duration_days=30,
                                         is_active=True, realm_id=realm_id)
    db.add(fixed_plan)
    db.commit()
    db.query(models.SubscriptionOrder).filter(
        models.SubscriptionOrder.order_id == doomed_order
    ).update({"plan_id": fixed_plan.id}, synchronize_session=False)
    db.commit()

r = notify_call(doomed_order, "25.00")
check("缺料修好后回调恢复正常（success）", r.text.strip() == "success",
      f"{r.status_code} {r.text[:80]}")
check("修好后订单支付并发出订阅（顺延 +30 天）",
      order_row(doomed_order).status == "paid"
      and sub_days_total(notify_buyer_id) == before_days_total + 30,
      f"{order_row(doomed_order).status} / {sub_days_total(notify_buyer_id)}")

print("\n" + "=" * 60)
if failures:
    print(f"❌ {len(failures)}/{checks} 项失败：")
    for name in failures:
        print(f"   - {name}")
    sys.exit(1)
print(f"✅ 订单退款/关单冒烟测试全部通过（{checks} 项）")
