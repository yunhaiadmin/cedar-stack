"""L-039: 会话上报首报并发建行竞态回归（P2）。

根因：compat_routes._get_or_create_umd 是「先 SELECT 查无、再 db.add」
的查-插两步，查与调用方（_record_progress / _record_stopped）的
commit 之间无锁、无 ON CONFLICT、无 IntegrityError 兜底。同一
(user_id, item_id) 的 Progress 与 Stopped 并发首报时两路都查无、都
INSERT，一路撞唯一约束 uq_umdata_user_item → 请求 500。
生产实证：2026-11-25 04:24:04 UTC，POST /emby/Sessions/Playing/Stopped
返回 500（行最终由先赢的一路落库，数据未丢，但 500 不该出现）。
修复：_get_or_create_umd 建行改走 _upsert_session 同款存点防线——
begin_nested 存点内 flush 抢唯一键，撞键只回滚存点内的 insert 本体，
再重查复用 winner 的行。

确定性设计（flush 钩子，L-021 同款）：A 线程在第一笔带新建
UserMediaData 的 flush 处暂停（此时 A 尚未真正写库），B 线程随后把
同一 (user, item) 的首报完整走完并提交；放行 A 后——修前 A 的
INSERT 撞唯一约束抛 IntegrityError（errors 非空判红）；修后 A 在
存点内撞键、回滚、重查到 B 的行并在其上落自己的进度。最终必须
恰好一行、字段等于「B 先落、A 后覆盖」的串行结果，与线程调度无关。

- SQLite 文件库版本：独立 engine + busy_timeout，普通门禁里跑；
- 真 PG 版本：DATABASE_URL 指向 postgres 才跑（CI backend-pg job），
  生产口径（READ COMMITTED）下复跑同一场景。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend import models
from backend.database import Base
from backend.emby_server import models as em
from backend.emby_server.compat_routes import _apply_play_progress, _get_or_create_umd

RUNTIME = 600_000_000  # 60 秒（ticks），与种子条目时长一致

_TABLES = [
    models.WebUser.__table__,
    em.Library.__table__,
    em.MediaItem.__table__,
    em.UserMediaData.__table__,
]


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


def _create_tables(engine):
    Base.metadata.create_all(engine, tables=_TABLES, checkfirst=True)


def _seed(engine):
    """建用户/库/条目，返回 (user_id, item_id)。每场独立种子互不干扰。"""
    Maker = sessionmaker(bind=engine)
    tag = uuid.uuid4().hex[:12]
    s = Maker()
    user = models.WebUser(username=f"l039-{tag}", password_hash="x",
                          is_active=True)
    s.add(user)
    lib = em.Library(guid=uuid.uuid4().hex, name=f"l039库{tag}",
                     collection_type="movies", is_enabled=True)
    s.add(lib)
    s.flush()
    item = em.MediaItem(guid=uuid.uuid4().hex, library_id=lib.id,
                        item_type="movie", name=f"l039片{tag}",
                        file_path=f"/tmp/none/{tag}.mp4", container="mp4",
                        sort_name=f"l039片{tag}", duration_ticks=RUNTIME)
    s.add(item)
    s.commit()
    ids = (user.id, item.id)
    s.close()
    return ids


def _report(Maker, user_id, item_id, pos, finished, errors, done=None):
    """一路首报的落库段：与 _record_progress / _record_stopped 共用的
    「取/建行 → 落进度 → 提交」原样序列（竞态只发生在这段里）。"""
    s = Maker()
    try:
        user = s.get(models.WebUser, user_id)
        item = s.get(em.MediaItem, item_id)
        umd = _get_or_create_umd(s, user, item)
        _apply_play_progress(umd, pos, finished)
        s.commit()
    except Exception as e:  # noqa: BLE001 - 透传给主线程断言
        s.rollback()
        errors.append(e)
    finally:
        s.close()
        if done is not None:
            done.set()


def _run_pair(engine, a_pos, a_finished, b_pos, b_finished, monkeypatch):
    """A 首报在建行 flush 处暂停，B 首报完整走完后放行 A。

    返回最终落库的全部 UMD 行（(position, max_position, played,
    play_count) 元组列表）。断言两路都不得抛错（修前 A 必抛
    IntegrityError）。
    """
    user_id, item_id = _seed(engine)
    Maker = sessionmaker(bind=engine)

    entered = threading.Event()   # A 已到建行 flush（临界区内）
    proceed = threading.Event()   # 主线程放行 A
    b_done = threading.Event()
    errors = []

    real_flush = Session.flush

    def hooked_flush(self):
        # 只拦第一次「带着新建 UserMediaData 的」flush（A 的建行）；
        # 其余 flush（查询 autoflush、B 的建行与提交）不拦。
        if not entered.is_set() and any(
                isinstance(o, em.UserMediaData) for o in self.new):
            entered.set()
            if not proceed.wait(timeout=30):
                raise AssertionError("test hook: proceed 未在 30s 内放行")
        return real_flush(self)

    monkeypatch.setattr(Session, "flush", hooked_flush)

    ta = threading.Thread(
        target=_report,
        args=(Maker, user_id, item_id, a_pos, a_finished, errors),
        daemon=True)
    ta.start()
    assert entered.wait(timeout=30), "A 未进入首报建行临界区"
    tb = threading.Thread(
        target=_report,
        args=(Maker, user_id, item_id, b_pos, b_finished, errors, b_done),
        daemon=True)
    tb.start()
    assert b_done.wait(timeout=30), "B 未在 30s 内完成首报"
    proceed.set()
    ta.join(timeout=30)
    tb.join(timeout=30)
    assert not errors, f"子线程异常: {errors}"

    s = Maker()
    rows = (s.query(em.UserMediaData)
            .filter_by(user_id=user_id, item_id=item_id).all())
    result = [(r.playback_position_ticks, r.max_position_ticks,
               bool(r.played), r.play_count) for r in rows]
    s.close()
    return result


# 场景一（Progress × Progress）：B 先落 pos=222M，A 后落 pos=111M。
# 串行结果：position 被 A 覆盖为 111M；max 只增不减保留 B 的 222M。
PROGRESS_EXPECTED = [(111_000_000, 222_000_000, False, 0)]
# 场景二（Stopped 完播 × Progress）：B 先落 pos=500M，A 以 95% 完播
# 收尾。串行结果：played 置真、play_count=1、position 与 max 按完播
# 口径清零。
STOPPED_EXPECTED = [(0, 0, True, 1)]


@pytest.fixture()
def sqlite_engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path}/l039.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    _create_tables(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_umd_first_report_progress_race_sqlite(sqlite_engine, monkeypatch):
    rows = _run_pair(sqlite_engine, 111_000_000, False,
                     222_000_000, False, monkeypatch)
    assert rows == PROGRESS_EXPECTED, f"并发首报结果与串行不一致: {rows}"


def test_umd_first_report_stopped_race_sqlite(sqlite_engine, monkeypatch):
    rows = _run_pair(sqlite_engine, 570_000_000, True,
                     500_000_000, False, monkeypatch)
    assert rows == STOPPED_EXPECTED, f"并发首报结果与串行不一致: {rows}"


def test_umd_repeat_report_sequential_sqlite(sqlite_engine):
    """重复首报（串行）：同 (user, item) 连报三次仍恰好一行。"""
    user_id, item_id = _seed(sqlite_engine)
    Maker = sessionmaker(bind=sqlite_engine)
    errors = []
    for pos in (100_000_000, 200_000_000, 300_000_000):
        _report(Maker, user_id, item_id, pos, False, errors)
    assert not errors, f"串行重复首报异常: {errors}"
    s = Maker()
    rows = (s.query(em.UserMediaData)
            .filter_by(user_id=user_id, item_id=item_id).all())
    result = [(r.playback_position_ticks, r.max_position_ticks,
               bool(r.played), r.play_count) for r in rows]
    s.close()
    assert result == [(300_000_000, 300_000_000, False, 0)], result


@pytest.mark.skipif(_pg_url() is None,
                    reason="需要真 PG（DATABASE_URL 指向 postgres）")
def test_umd_first_report_race_pg():
    """真 PG 端到端：Progress 竞态与 Stopped 竞态两场景各跑一轮。"""
    engine = create_engine(_pg_url(), pool_size=5)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    _create_tables(engine)
    try:
        with pytest.MonkeyPatch.context() as mp:
            rows = _run_pair(engine, 111_000_000, False,
                             222_000_000, False, mp)
        assert rows == PROGRESS_EXPECTED, f"PG 并发首报与串行不一致: {rows}"
        with pytest.MonkeyPatch.context() as mp:
            rows = _run_pair(engine, 570_000_000, True,
                             500_000_000, False, mp)
        assert rows == STOPPED_EXPECTED, f"PG 并发首报与串行不一致: {rows}"
    finally:
        engine.dispose()
