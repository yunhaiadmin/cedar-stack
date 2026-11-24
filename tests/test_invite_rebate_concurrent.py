"""L-021: 邀请返利计提并发回归（P2·资金）。

根因：invite_rebate.record_payment_rebate 的分档累计
（_invitee_prior_paid）与封顶累计（_accrued_total）是无锁
「读累计 → 定档/截断 → 插行」：同一邀请人的两笔付费并发计提（订阅履约
与充值回调可同时到达）会读到同一份旧累计，双双按 5% 前段计提、合计
突破封顶。唯一约束只挡同单重放，挡不住不同订单的并发。
修复：计提计算前先 lock_user_row 锁邀请人用户行，把临界区串行化
（与钱包入账 credit 锁同一行、同一先后顺序，不新增锁序）。

确定性设计（flush 钩子，r52/L-010 同款）：A 线程在计提行 flush 处暂停
（修后此时已持邀请人行锁、已按旧累计算完未落库），B 线程此时发起第二笔
计提——修前 B 直接走完落库（两笔都按旧累计计提，金额必错）；修后 B 被
挡在行锁外，等 A 提交后按新累计计提。最终两笔金额必须与串行执行一致
（分档、封顶各一场景；串行两种先后顺序的结果都接受，合计必须守恒）。

- SQLite 文件库版本：独立连接 + busy_timeout，真行锁语义下跑同一场景；
- 真 PG 版本：DATABASE_URL 指向 postgres 才跑（CI backend-pg job），
  生产口径（READ COMMITTED + SELECT ... FOR UPDATE）下复跑同一场景。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-invite-rebate-concurrent-key-32!")

import threading
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend import invite_rebate, models
from backend.integrations import store


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


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _set(db, key, value):
    row = db.query(models.SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(value)
    else:
        db.add(models.SystemConfig(key=key, value=str(value)))
    db.commit()
    store.invalidate(key)


def _setup(engine, tag, cap_fen):
    """建邀请人/被邀请人/邀请关系与返利配置，返回 (inviter_id, invitee_id)。"""
    Maker = sessionmaker(bind=engine)
    s = Maker()
    inviter = _user(s, f"l021-inviter-{tag}")
    invitee = _user(s, f"l021-invitee-{tag}")
    code = models.InvitationCode(
        code=f"L021{tag.upper()}"[:32], user_id=inviter.id,
        is_active=True, max_uses=0, use_count=1, reward_points=0)
    s.add(code)
    s.commit()
    s.refresh(code)
    s.add(models.InvitationRecord(
        inviter_id=inviter.id, invitee_id=invitee.id,
        code_id=code.id, reward_points=0))
    s.commit()
    # 钱包关停：计提只落 pending，不走即时入账（入账路径另有专测）
    _set(s, "wallet_enabled", "false")
    # 分档口径显式钉死（不依赖出厂默认，共享库里有残留配置也不受影响）
    _set(s, invite_rebate.CONFIG_MODE, "tiered")
    _set(s, invite_rebate.CONFIG_PERCENT, "5")
    _set(s, invite_rebate.CONFIG_TIER_PERCENT, "1")
    _set(s, invite_rebate.CONFIG_TIER_THRESHOLD, "5000")
    _set(s, invite_rebate.CONFIG_CAP, str(cap_fen))
    ids = (inviter.id, invitee.id)
    s.close()
    store.invalidate()
    return ids


def _run_pair(engine, tag, cap_fen, monkeypatch):
    """同邀请人同被邀请人两笔付费（0 / 0，合计跨 0 分档线）并发计提。

    A 先行并在计提行 flush 处暂停，B 随后发起；返回最终落库的两笔返利
    金额（升序）。断言 B 在 A 临界区未结束前不得走完（修后被行锁挡住）。
    """
    inviter_id, invitee_id = _setup(engine, tag, cap_fen)
    Maker = sessionmaker(bind=engine)

    entered = threading.Event()   # A 已到计提行 flush（临界区内）
    proceed = threading.Event()   # 主线程放行 A
    b_done = threading.Event()
    errors = []

    real_flush = Session.flush

    def hooked_flush(self):
        # 只拦第一次「带着新建 InviteRebateAccrual 的」flush（A 的计提插行）；
        # 其余 flush（查询 autoflush、B 的插行）不拦。
        if not entered.is_set() and any(
                isinstance(o, models.InviteRebateAccrual) for o in self.new):
            entered.set()
            if not proceed.wait(timeout=30):
                raise AssertionError("test hook: proceed 未在 30s 内放行")
        return real_flush(self)

    monkeypatch.setattr(Session, "flush", hooked_flush)

    def pay(ref, amount, done=None):
        s = Maker()
        try:
            payer = s.get(models.WebUser, invitee_id)
            invite_rebate.record_payment_rebate(
                s, payer, source=invite_rebate.SOURCE_SUBSCRIPTION_ORDER,
                source_ref=ref, paid_amount=Decimal(amount))
            s.commit()
        except Exception as e:  # noqa: BLE001 - 透传给主线程断言
            s.rollback()
            errors.append(e)
        finally:
            s.close()
            if done is not None:
                done.set()

    ta = threading.Thread(
        target=pay, args=(f"L021-{tag}-A", "30"), daemon=True)
    ta.start()
    assert entered.wait(timeout=30), "A 未进入计提临界区"
    tb = threading.Thread(
        target=pay, args=(f"L021-{tag}-B", "40", b_done), daemon=True)
    tb.start()
    # B 发起 3 秒后仍未完成 = 被邀请人行锁挡住（修前 PG 下 B 必在此窗口走完）
    time.sleep(3)
    assert not b_done.is_set(), (
        "并发计提未被串行化：B 在 A 持有临界区期间走完了计提")
    proceed.set()
    ta.join(timeout=30)
    tb.join(timeout=30)
    assert not errors, f"子线程异常: {errors}"

    s = Maker()
    rows = (s.query(models.InviteRebateAccrual)
            .filter_by(inviter_id=inviter_id)
            .order_by(models.InviteRebateAccrual.id).all())
    amounts = sorted(r.rebate_amount for r in rows)
    s.close()
    assert len(rows) == 2, f"应有 2 笔计提，实际 {len(rows)}"
    return amounts


# 串行口径（门槛 0、5%/1%）：先 0 后 0 → 1.50 + (20×5%+20×1%)=1.20；
# 先 0 后 0 → 2.00 + (10×5%+20×1%)=0.70。合计恒 2.70。
TIER_OPTIONS = [
    [Decimal("1.20"), Decimal("1.50")],
    [Decimal("0.70"), Decimal("2.00")],
]
# 封顶 .60（260 美分）：先 30 后 40 → 1.50 + min(1.20, 1.10)=1.10；
# 先 40 后 30 → 2.00 + min(0.70, 0.60)=0.60。合计恒 2.60。
CAP_OPTIONS = [
    [Decimal("1.10"), Decimal("1.50")],
    [Decimal("0.60"), Decimal("2.00")],
]


@pytest.fixture()
def sqlite_engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path}/l021.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    models.Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_concurrent_rebate_tiered_sqlite(sqlite_engine, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    amounts = _run_pair(sqlite_engine, tag, 0, monkeypatch)
    assert amounts in TIER_OPTIONS, f"分档金额与串行不一致: {amounts}"
    assert sum(amounts) == Decimal("2.70")


def test_concurrent_rebate_cap_sqlite(sqlite_engine, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    amounts = _run_pair(sqlite_engine, tag, 260, monkeypatch)
    assert amounts in CAP_OPTIONS, f"封顶金额与串行不一致: {amounts}"
    assert sum(amounts) == Decimal("2.60")


@pytest.mark.skipif(_pg_url() is None, reason="需要真 PG（DATABASE_URL 指向 postgres）")
def test_concurrent_rebate_tiered_and_cap_pg(monkeypatch):
    """真 PG 端到端：分档与封顶两场景各跑一轮（每轮新邀请人）。"""
    engine = create_engine(_pg_url(), pool_size=5)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    _tables = [
        models.WebUser.__table__,
        models.InvitationCode.__table__,
        models.InvitationRecord.__table__,
        models.InviteRebateAccrual.__table__,
        models.SystemConfig.__table__,
        models.InvitePromoterProfile.__table__,
    ]
    models.Base.metadata.create_all(engine, tables=_tables, checkfirst=True)
    try:
        tag = uuid.uuid4().hex[:8]
        amounts = _run_pair(engine, f"{tag}t", 0, monkeypatch)
        assert amounts in TIER_OPTIONS, f"PG 分档金额与串行不一致: {amounts}"
        assert sum(amounts) == Decimal("2.70")

        tag = uuid.uuid4().hex[:8]
        amounts = _run_pair(engine, f"{tag}c", 260, monkeypatch)
        assert amounts in CAP_OPTIONS, f"PG 封顶金额与串行不一致: {amounts}"
        assert sum(amounts) == Decimal("2.60")
    finally:
        engine.dispose()
