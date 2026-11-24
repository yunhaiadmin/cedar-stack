"""L-010: 并发核销两张不同批次的续期码产生双 active 订阅行（P2）。

根因：``codes.grant_membership_days`` 是「SELECT 生效行（FOR UPDATE）→ 无行则
INSERT」；行不存在时行锁锁不住，两并发事务各自 INSERT，用户实得 30 天而非
60 天（与 r52 修掉的 ``economy._grant_subscription`` 同款缺口）。修复：复用
``economy.subscription_grant_lock`` 把查-增串行化，锁下沉进
``grant_membership_days``（卡码核销 / invite_trial / promotion 三处调用方
共用口，一次覆盖）。

- ``test_concurrent_grant_membership_days_serialized``：SQLite 确定性版本
  （r52 同款 flush 钩子）：A 在 insert 分支的 flush 处暂停（仍持有串行锁），
  B 此时发起发放——修前 B 直接走完（无锁），修后 B 被挡在锁外。最终行数
  不在此断言（SQLite 回退 RLock 在 commit 前释放，见 r52 测试同款注释）；
  单行累加语义由串行用例覆盖。
- ``test_concurrent_redeem_two_codes_pg``：真 PG 端到端（``redeem_code`` 两
  线程 barrier 对齐，两张不同批次的 30 天码）：修前 2 条 active 行、各约
  30 天；修后 1 条行、约 60 天。只有 ``DATABASE_URL`` 指向 postgres 时才
  跑，否则跳过（SQLite 写锁掩盖竞态）。
- ``test_sequential_grants_extend_single_row``：串行两次发放仍是 1 行、
  天数累加（回归既有语义）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import threading
import time
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import codes, models


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


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _mk_session(engine):
    return sessionmaker(bind=engine)()


def _make_user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_realm(db, slug):
    r = models.ServerRealm(name=f"服-{slug}", slug=slug, url="", is_active=True)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _make_plan(db, realm_id):
    """_resolve_plan 能直接命中的在售套餐（避免新建计划分支触发额外 flush）。"""
    p = models.SubscriptionPlan(
        name="卡码开通", price=0, duration_days=30,
        is_active=True, sort_order=1, realm_id=realm_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


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


def test_concurrent_grant_membership_days_serialized(engine, monkeypatch):
    """并发双发放：第二个必须等第一个走完临界区，不能各自新建。

    确定性设计：A 线程在 insert 分支的 flush 处暂停（仍持有串行锁），
    B 线程此时发起发放——修前 B 会直接走完（无锁），修后 B 必须被挡在锁外。
    """
    s0 = _mk_session(engine)
    u = _make_user(s0, "race-u")
    realm = _make_realm(s0, "race")
    _make_plan(s0, realm.id)
    # 跨线程不传 ORM 对象（s0 关闭后属性过期）：只传主键，各线程自建会话重查
    user_id, realm_id = u.id, realm.id
    s0.close()

    entered = threading.Event()   # A 已进入临界区（flush 被暂停）
    proceed = threading.Event()   # 主线程放行 A
    b_done = threading.Event()
    errors = []

    real_flush = Session.flush

    def hooked_flush(self):
        # 只拦第一次「带着新建 UserSubscription 的」flush（A 的 insert 分支）；
        # 查之前的 autoflush（无 pending）不拦。
        if not entered.is_set() and self.new:
            entered.set()
            if not proceed.wait(timeout=30):
                raise AssertionError("test hook: proceed 未在 30s 内放行")
        return real_flush(self)

    monkeypatch.setattr(Session, "flush", hooked_flush)

    def grant_a():
        s1 = _mk_session(engine)
        try:
            user = s1.get(models.WebUser, user_id)
            codes.grant_membership_days(s1, user, 30, realm_id)
            s1.commit()
        except Exception as e:  # noqa: BLE001 - 透传给主线程断言
            errors.append(e)
        finally:
            s1.close()

    def grant_b():
        s2 = _mk_session(engine)
        try:
            user = s2.get(models.WebUser, user_id)
            codes.grant_membership_days(s2, user, 30, realm_id)
            s2.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            s2.close()
        b_done.set()

    ta = threading.Thread(target=grant_a, daemon=True)
    ta.start()
    assert entered.wait(timeout=30), "A 未进入临界区"
    tb = threading.Thread(target=grant_b, daemon=True)
    tb.start()
    # B 发起 3 秒后仍未完成 = 被串行锁挡住（修前这里 3 秒内必完成，测试变红）
    time.sleep(3)
    assert not b_done.is_set(), "并发发放未被串行化：B 在 A 持有锁期间走完了临界区"
    proceed.set()
    ta.join(timeout=30)
    tb.join(timeout=30)
    assert not errors, f"子线程异常: {errors}"
    # 注：不在此处断言最终行数——sqlite 回退路径的 RLock 在 commit 前释放，
    # B 的检查可能排在 A 的 commit 之前（测试环境 artifact）；生产 PG 用 xact 级
    # advisory lock，锁覆盖到事务提交之后，不存在该窗口。串行语义由上面的阻塞
    # 断言覆盖；单行累加语义由 test_sequential_grants_extend_single_row 覆盖。


def test_sequential_grants_extend_single_row(engine):
    """串行两次发放：仍是 1 行，天数累加（回归既有语义）。"""
    s = _mk_session(engine)
    u = _make_user(s, "seq-u")
    realm = _make_realm(s, "seq")
    _make_plan(s, realm.id)
    t0 = datetime.now()
    codes.grant_membership_days(s, u, 30, realm.id)
    s.commit()
    codes.grant_membership_days(s, u, 30, realm.id)
    s.commit()
    rows = _active_rows(s, u.id, realm.id)
    assert len(rows) == 1
    assert rows[0].end_date >= t0 + timedelta(days=59)
    s.close()


@pytest.mark.skipif(_pg_url() is None, reason="需要真 PG（DATABASE_URL 指向 postgres）")
def test_concurrent_redeem_two_codes_pg():
    """真 PG 端到端：同一用户并发核销两张不同批次的 30 天续期码。

    修前：两笔 redeem 均 success，但产生 2 条 active 行、各约 30 天（复现
    脚本第 1 轮即复现）；修后：1 条行、约 60 天。跑 3 轮，每轮新用户+新码，
    任一轮出现双行即失败。
    """
    url = _pg_url()
    engine = create_engine(url, pool_size=5)
    # 与生产 init_db 同口径：先 pg_trgm 再 create_all；只建本测试触及的表，
    # 不动共享库里的其他表。
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    _tables = [
        models.WebUser.__table__,
        models.ServerRealm.__table__,
        models.SystemConfig.__table__,
        models.SubscriptionPlan.__table__,
        models.UserSubscription.__table__,
        models.RegistrationCode.__table__,
        models.CodeRedemption.__table__,
        models.CodeBatchClaim.__table__,
    ]
    models.Base.metadata.create_all(engine, tables=_tables, checkfirst=True)
    Maker = sessionmaker(bind=engine)
    try:
        for rnd in range(1, 4):
            suffix = uuid.uuid4().hex[:8]
            s = Maker()
            realm = models.ServerRealm(
                name=f"服-race-{suffix}", slug=f"race-{suffix}",
                url="", is_active=True)
            s.add(realm)
            s.commit()
            s.refresh(realm)
            _make_plan(s, realm.id)
            u = _make_user(s, f"race-{suffix}")
            uid, realm_id = u.id, realm.id
            c1 = models.RegistrationCode(
                code=f"REN-{suffix}-A", code_type=2, days=30, max_uses=1,
                is_active=True, batch_id=f"batch-{suffix}-a", realm_id=realm_id)
            c2 = models.RegistrationCode(
                code=f"REN-{suffix}-B", code_type=2, days=30, max_uses=1,
                is_active=True, batch_id=f"batch-{suffix}-b", realm_id=realm_id)
            s.add_all([c1, c2])
            s.commit()
            s.close()

            barrier = threading.Barrier(2)
            out = [None, None]
            errors = []

            def worker(raw, idx):
                sess = Maker()
                try:
                    user = sess.query(models.WebUser).filter_by(id=uid).first()
                    barrier.wait(timeout=10)
                    res = codes.redeem_code(sess, user, raw)
                    sess.commit()
                    out[idx] = bool(res.get("success"))
                except Exception as e:  # noqa: BLE001
                    sess.rollback()
                    errors.append(e)
                    out[idx] = f"ERR {type(e).__name__}: {e}"
                finally:
                    sess.close()

            t0 = datetime.now()
            t1 = threading.Thread(target=worker, args=(f"REN-{suffix}-A", 0))
            t2 = threading.Thread(target=worker, args=(f"REN-{suffix}-B", 1))
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)
            assert not errors, f"round {rnd} 子线程异常: {errors}"
            assert out == [True, True], f"round {rnd} 核销结果异常: {out}"
            s = Maker()
            rows = _active_rows(s, uid, realm_id)
            assert len(rows) == 1, (
                f"round {rnd}: 并发核销产生 {len(rows)} 条 active 订阅行 "
                f"(各约 {sorted(round((x.end_date - t0).total_seconds() / 86400, 1) for x in rows)} 天)，"
                "第二张码的天数未叠加")
            assert rows[0].end_date >= t0 + timedelta(days=59), (
                f"round {rnd}: 单行天数不足 59 天: {rows[0].end_date - t0}")
            s.close()
    finally:
        engine.dispose()
