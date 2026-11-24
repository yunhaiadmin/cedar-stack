"""L-020: 换套餐履约锁外置过期——两笔异档订单并发履约钱天双给（P2·资金）。

根因：``economy._fulfill_order`` 先在 ``subscription_grant_lock`` 之外
读旧订阅（``_active_sub_in_realm``，无行锁）、把旧行置过期并 flush，
之后才调 ``_grant_subscription`` 进锁。``_claim_order`` 只锁订单行本身，
两笔**不同**订单互不排斥：同用户两笔异档订单近同时履约时（网关回调 /
钱包购买 / 自动续费三路都经 ``_fulfill_order``），双方都读到同一条旧
订阅、都把它置过期，后到者在发放锁内看到先到者新建的订阅，按「同服
顺延」再叠加一遍天数并翻档——旧套餐已在计价时折成钱抵扣过，等于
钱、天双给。

修法：把「读旧行 → 置过期 → 发放」整段搬进 ``subscription_grant_lock``
临界区（锁 key 与 ``_grant_subscription`` 内部同口径，advisory 锁同
事务内可重入），并发履约被串行化：后到者拿到锁时先到者已提交，它读
到的当前订阅是先到者的新档，置过期后另起一行，最终权益只按后履约
的一笔口径。

- ``test_concurrent_switch_orders_no_double_grant_pg``：真 PG 双线程
  竞态。确定性设计（不用 sleep 碰运气）：B 线程在发放入口被钩子暂停
  （此时旧行已置过期+flush、行锁在手），主线程确认 B 就位后才启动 C：
  - 修前：C 无锁可挡，直接读到旧行并置 c_read_done → B 被放行履约
    提交；C 的置过期 flush 在旧行行锁上等 B 提交，随后在发放锁内
    看到 B 的新订阅并顺延 +30 天 → 最终一行 60 天，断言变红。
  - 修后：C 被挡在 advisory lock 外（读不到旧行，c_read_done 永不
    置位），B 改由 pg_locks 观察到 C 正等在本锁的 key 上（granted=
    false 的 advisory 锁等待）后放行 → C 拿到锁时 B 已提交，C 置
    过期 B 的订阅、另起一行 30 天 → 断言通过。
  两种路径都有事件/pg_locks 的实证信号 + 60 秒活性兜底，不是定时猜测。
- ``test_sequential_switch_orders_last_one_wins``：串行语义护栏——
  先后履约两笔异档订单，最终只剩后一笔的一行 30 天（修前修后都应
  通过，保证搬锁没改坏正常换档口径）。

只有 ``DATABASE_URL`` 指向 postgres 时才跑，否则跳过（SQLite 的写
串行化与进程内 RLock 回退复现不出/不需要这个竞态，与 r52/L-010 的
PG 用例同口径）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import threading
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.api import economy


def _pg_url():
    """CI backend-pg job 的 DATABASE_URL；非 PG 返回 None（调用方跳过）。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    if url.startswith("postgresql://"):
        # 与 backend/database.py 同一口径：没装 psycopg(v3) 就显式走 psycopg2
        try:
            import psycopg  # noqa: F401
        except ImportError:
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


pytestmark = pytest.mark.skipif(
    _pg_url() is None, reason="需要真 PG（DATABASE_URL 指向 postgres）")


@pytest.fixture()
def pg():
    url = _pg_url()
    engine = create_engine(url, pool_size=10)
    # 与生产 init_db 同口径：先 pg_trgm 再 create_all（checkfirst，
    # 不动共享库里的其他表）。
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    models.Base.metadata.create_all(engine, checkfirst=True)
    Maker = sessionmaker(bind=engine)
    try:
        yield engine, Maker
    finally:
        engine.dispose()


def _seed(Maker):
    """旧档 A 生效中（剩 20 天）+ 异档订单 B、C 各 30 天待履约。"""
    suffix = uuid.uuid4().hex[:8]
    s = Maker()
    try:
        realm = models.ServerRealm(
            name=f"服-l020-{suffix}", slug=f"l020-{suffix}",
            url="", is_active=True)
        s.add(realm)
        s.commit()
        s.refresh(realm)

        def mk_plan(name):
            p = models.SubscriptionPlan(
                name=name, price=Decimal("30.00"), duration_days=30,
                is_active=True, sort_order=1, realm_id=realm.id)
            s.add(p)
            s.commit()
            s.refresh(p)
            return p

        plan_a = mk_plan(f"A档-{suffix}")
        plan_b = mk_plan(f"B档-{suffix}")
        plan_c = mk_plan(f"C档-{suffix}")
        u = models.WebUser(username=f"l020_{suffix}", password_hash="x",
                           is_active=True)
        s.add(u)
        s.commit()
        s.refresh(u)
        now = datetime.now()
        old_sub = models.UserSubscription(
            user_id=u.id, plan_id=plan_a.id, realm_id=realm.id,
            start_date=now - timedelta(days=10),
            end_date=now + timedelta(days=20), status="active")
        s.add(old_sub)

        def mk_order(tag, plan):
            o = models.SubscriptionOrder(
                order_id=f"L020-{suffix}-{tag}", user_id=u.id,
                plan_id=plan.id, amount=Decimal("30.00"),
                status="pending", days_granted=30)
            s.add(o)
            s.commit()
            s.refresh(o)
            return o

        order_b = mk_order("B", plan_b)
        order_c = mk_order("C", plan_c)
        return dict(user_id=u.id, realm_id=realm.id,
                    plan_b_id=plan_b.id, plan_c_id=plan_c.id,
                    order_b_id=order_b.id, order_c_id=order_c.id)
    finally:
        s.close()


def _active_rows(db, user_id, realm_id):
    now = datetime.now()
    return (
        db.query(models.UserSubscription)
        .filter(
            models.UserSubscription.user_id == user_id,
            models.UserSubscription.realm_id == realm_id,
            models.UserSubscription.status == "active",
            models.UserSubscription.end_date > now,
        )
        .all()
    )


def _assert_last_order_wins(Maker, seed):
    """最终权益只按后履约的 C 单口径：一行、C 档、恰 30 天。"""
    s = Maker()
    try:
        rows = _active_rows(s, seed["user_id"], seed["realm_id"])
        assert len(rows) == 1, (
            f"生效订阅应为 1 行，实际 {len(rows)} 行: "
            f"{[(r.plan_id, r.start_date, r.end_date) for r in rows]}")
        row = rows[0]
        assert row.plan_id == seed["plan_c_id"], (
            f"最终档位应为后履约的 C 档 {seed['plan_c_id']}，实际 {row.plan_id}")
        span = row.end_date - row.start_date
        assert timedelta(days=29) <= span <= timedelta(days=31), (
            f"最终订阅时长应为一笔订单的 30 天，实际 {span}——"
            "两笔异档订单的天数被叠加（钱天双给）")
        order_c = s.get(models.SubscriptionOrder, seed["order_c_id"])
        assert order_c.status == "paid"
        assert order_c.subscription_id == row.id
    finally:
        s.close()


def test_sequential_switch_orders_last_one_wins(pg):
    """串行护栏：先后履约 B、C 两笔异档订单，最终只剩 C 的一行 30 天。"""
    _engine, Maker = pg
    seed = _seed(Maker)
    for oid in (seed["order_b_id"], seed["order_c_id"]):
        s = Maker()
        try:
            order = s.get(models.SubscriptionOrder, oid)
            economy._fulfill_order(s, subscription_order=order)
            s.commit()
        finally:
            s.close()
    _assert_last_order_wins(Maker, seed)


def test_concurrent_switch_orders_no_double_grant_pg(pg, monkeypatch):
    """真 PG 并发：两笔异档订单同时履约，最终只按后履约的一笔发。"""
    engine, Maker = pg
    seed = _seed(Maker)

    b_in_grant = threading.Event()   # B 已进入发放入口（旧行已置过期）
    c_read_done = threading.Event()  # 修前路径信号：C 已在锁外读到旧行
    errors = []

    real_grant = economy._grant_subscription
    real_active = economy._active_sub_in_realm

    # 本测试（用户，服）的 advisory 锁 key 两半，用于在 pg_locks 里
    # 精确认出 C 对这把锁的等待（修后路径信号），不被其他会话噪声干扰。
    lock_key = economy._subscription_grant_lock_key(
        seed["user_id"], seed["realm_id"])
    key_hi = (lock_key >> 32) & 0xFFFFFFFF
    key_lo = lock_key & 0xFFFFFFFF

    def c_blocked_on_grant_lock() -> bool:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                "AND granted = false AND classid = :hi AND objid = :lo "
                "LIMIT 1"), {"hi": key_hi, "lo": key_lo}).first()
        return row is not None

    def hooked_grant(db, user, plan, days, realm_id=None):
        if threading.current_thread().name == "fulfill-B":
            b_in_grant.set()
            # 等 C 的进度信号：修前是「C 已读到旧行」，修后是「C 正被
            # 挡在本锁上」。60 秒是活性兜底（两个信号必居其一），到点
            # 仍无信号说明测试装配坏了、直接报错，不靠定时猜测放行。
            deadline = time.monotonic() + 60
            while not c_read_done.is_set() and not c_blocked_on_grant_lock():
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "60s 内未观察到 C 的任何进度信号（既未读到旧行、"
                        "也未在 pg_locks 里等发放锁），测试装配异常")
                time.sleep(0.02)
        return real_grant(db, user, plan, days, realm_id=realm_id)

    def hooked_active(db, user_id, plan, now):
        row = real_active(db, user_id, plan, now)
        if threading.current_thread().name == "fulfill-C":
            c_read_done.set()
        return row

    monkeypatch.setattr(economy, "_grant_subscription", hooked_grant)
    monkeypatch.setattr(economy, "_active_sub_in_realm", hooked_active)

    def fulfill(order_pk):
        sess = Maker()
        try:
            order = sess.get(models.SubscriptionOrder, order_pk)
            economy._fulfill_order(sess, subscription_order=order)
            sess.commit()
        except Exception as e:  # noqa: BLE001 - 透传给主线程断言
            sess.rollback()
            errors.append(e)
        finally:
            sess.close()

    tb = threading.Thread(target=fulfill, args=(seed["order_b_id"],),
                          name="fulfill-B", daemon=True)
    tb.start()
    assert b_in_grant.wait(timeout=30), "B 未进入发放入口"
    tc = threading.Thread(target=fulfill, args=(seed["order_c_id"],),
                          name="fulfill-C", daemon=True)
    tc.start()
    tb.join(timeout=60)
    tc.join(timeout=60)
    assert not tb.is_alive() and not tc.is_alive(), "履约线程未在 60s 内结束"
    assert not errors, f"履约线程异常: {errors}"

    _assert_last_order_wins(Maker, seed)
