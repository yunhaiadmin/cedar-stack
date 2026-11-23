# -*- coding: utf-8 -*-
"""附议每日上限并发竞态回归测试（360° 审计 A-043）。

根因：
    media_seek.toggle_vote / ensure_vote 只对求片行加锁（with_for_update），
    每日上限是「先查 daily_votes_used 后插行」、全程不锁用户行。同一用户
    并发对**不同**求片附议时，两条请求锁的是不同的求片行、互不阻塞，
    各自读到同一旧用量（都 < 上限）→ 双双插入 → 突破每日上限（默认 20/天）。
    对照：求片创建路径（api/user.py）早已用 lock_user_row 串行化同一用户
    的「读用量-判上限-写」，附议路径漏了同一把锁。

修复：
    toggle_vote / ensure_vote 在读每日用量之前先 lock_user_row(db, user.id)。
    锁序统一为「用户行 → 求片行」：api/user.py 的查重转附议调用方已持
    用户行锁，同事务内重复取同一行锁无副作用，不引入反序死锁。

覆盖：
    a) test_vote_limit_check_runs_inside_user_row_lock —— 事件顺序断言：
       lock_user_row 必须先于 daily_votes_used（修前根本没调锁 → 失败）。
    b) test_vote_concurrent_pg —— 真 PG 双线程 barrier 对齐并发：上限=1
       时对两条不同求片同时附议，恰好一条成功、一条被上限拦下，库里
       当日附议行恰好 1 条（无 PG 的 DATABASE_URL 时 skip）。
    c) test_vote_limit_sequential_sqlite —— 串行语义回归：上限拦截、
       取消释放名额、ensure 幂等不被加锁破坏。

全部离线：不碰网络、不碰生产库。
"""
import os
import sys
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")

from backend import media_seek, models  # noqa: E402
from backend.api import economy  # noqa: E402
from backend.integrations import store  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具与辅助
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    store.invalidate()
    session.close()
    engine.dispose()


def _user(db, username):
    user = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _req(db, user, name):
    r = models.MovieRequest(user_id=user.id, movie_name=name, type="movie", status="pending")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _set_limit(db, value):
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == media_seek.CONFIG_DAILY_VOTE_LIMIT
    ).first()
    if cfg is None:
        db.add(models.SystemConfig(key=media_seek.CONFIG_DAILY_VOTE_LIMIT, value=str(value)))
    else:
        cfg.value = str(value)
    db.commit()


def _pg_url():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return None
    if url.startswith("postgresql://"):
        try:
            import psycopg  # noqa: F401
        except ImportError:
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


# ---------------------------------------------------------------------------
# a) 事件顺序：每日用量统计必须在用户行锁之后
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entry", ["toggle_vote", "ensure_vote"])
def test_vote_limit_check_runs_inside_user_row_lock(db, monkeypatch, entry):
    """lock_user_row 必须先于 daily_votes_used 被调用（修前：无锁 → 断言失败）。"""
    events = []

    real_lock = economy.lock_user_row

    def spy_lock(session, user_id):
        events.append("locked")
        return real_lock(session, user_id)

    monkeypatch.setattr(economy, "lock_user_row", spy_lock)

    real_used = media_seek.daily_votes_used

    def spy_used(session, user_id):
        events.append("used_read")
        return real_used(session, user_id)

    monkeypatch.setattr(media_seek, "daily_votes_used", spy_used)

    tag = uuid.uuid4().hex[:8]
    owner = _user(db, f"vote-owner-{tag}")
    voter = _user(db, f"vote-voter-{tag}")
    req = _req(db, owner, f"顺序断言-{tag}")

    result = getattr(media_seek, entry)(db, voter, req.id)
    assert result["voted"] is True

    assert "locked" in events, f"{entry} 全程没有调用 lock_user_row：{events}"
    assert "used_read" in events, f"{entry} 没有读每日用量：{events}"
    assert events.index("locked") < events.index("used_read"), (
        f"{entry} 的每日用量统计跑在了用户行锁之前，并发可突破上限：{events}"
    )


# ---------------------------------------------------------------------------
# b) 真 PG 双线程竞态：上限=1 时恰好一条附议成功
# ---------------------------------------------------------------------------
def test_vote_concurrent_pg():
    """同一用户 barrier 对齐后同时对两条不同求片附议：修后恰好一条成功、
    一条被每日上限拦下，当日附议行恰好 1 条。"""
    url = _pg_url()
    if not url:
        pytest.skip("未配置 PostgreSQL 的 DATABASE_URL，跳过真并发竞态测试")

    engine = create_engine(url, pool_size=5, max_overflow=5)
    SessionLocal = sessionmaker(bind=engine)
    store.invalidate()

    tag = uuid.uuid4().hex[:8]
    setup = SessionLocal()
    try:
        owner_id = _user(setup, f"pg-vote-owner-{tag}").id
        voter_id = _user(setup, f"pg-vote-voter-{tag}").id
        req_a_id = _req(setup, setup.get(models.WebUser, owner_id), f"并发附议甲-{tag}").id
        req_b_id = _req(setup, setup.get(models.WebUser, owner_id), f"并发附议乙-{tag}").id
        _set_limit(setup, 1)
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results = []
    guard = threading.Lock()

    def worker(request_id):
        session = SessionLocal()
        try:
            voter = session.get(models.WebUser, voter_id)
            barrier.wait(timeout=10)
            out = media_seek.toggle_vote(session, voter, request_id)
            with guard:
                results.append(("ok", out["voted"]))
        except ValueError as exc:
            session.rollback()
            with guard:
                results.append(("limited", str(exc)))
        except Exception as exc:  # noqa: BLE001 - 线程意外异常带回主线程断言
            session.rollback()
            with guard:
                results.append(("error", repr(exc)))
        finally:
            session.close()

    threads = [
        threading.Thread(target=worker, args=(req_a_id,)),
        threading.Thread(target=worker, args=(req_b_id,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok = [r for r in results if r[0] == "ok"]
    limited = [r for r in results if r[0] == "limited"]
    errors = [r for r in results if r[0] == "error"]

    assert not errors, f"并发线程出现意外异常：{errors}"
    assert len(ok) == 1, f"期望恰好一条附议成功，实际 {results}"
    assert len(limited) == 1, f"期望恰好一条被每日上限拦下，实际 {results}"

    check = SessionLocal()
    try:
        rows = check.query(models.MovieRequestVote).filter(
            models.MovieRequestVote.user_id == voter_id,
        ).count()
    finally:
        check.close()

    assert rows == 1, f"该用户当日附议行应恰好 1 条，实际 {rows}"

    store.invalidate()
    engine.dispose()


# ---------------------------------------------------------------------------
# c) SQLite 串行语义：上限 / 取消释放 / ensure 幂等
# ---------------------------------------------------------------------------
def test_vote_limit_sequential_sqlite(db):
    """加锁没破坏正常语义：满额拦截、取消后名额释放、ensure 重复调用幂等。"""
    tag = uuid.uuid4().hex[:8]
    owner = _user(db, f"vote-seq-owner-{tag}")
    voter = _user(db, f"vote-seq-voter-{tag}")
    req1 = _req(db, owner, f"串行甲-{tag}")
    req2 = _req(db, owner, f"串行乙-{tag}")
    req3 = _req(db, owner, f"串行丙-{tag}")
    _set_limit(db, 2)

    assert media_seek.toggle_vote(db, voter, req1.id)["voted"] is True
    # ensure 幂等：重复调用不重复计数、不占新名额
    assert media_seek.ensure_vote(db, voter, req1.id)["added"] is False
    assert media_seek.toggle_vote(db, voter, req2.id)["voted"] is True

    with pytest.raises(ValueError, match="上限"):
        media_seek.toggle_vote(db, voter, req3.id)
    db.rollback()

    # 取消一条 → 名额释放 → 第三条可投
    assert media_seek.toggle_vote(db, voter, req1.id)["voted"] is False
    assert media_seek.toggle_vote(db, voter, req3.id)["voted"] is True
