"""S2/S8 回归测试：批量 pre/post IO 段并发化 + worker 阶段耗时进 health。

S2（enrich-speed 提速包）：
- 等价性：同一多组混合批次，ENRICH_IO_WORKERS=1 与 =4 的每条 outcome
  与落库结果一致；
- 并发性：不同组 pre 同时在场（Barrier 证据）；同组内串行（并发计数峰值=1，
  holder/ctx 整季共享不许串味）；
- 异常隔离：单条 pre 抛异常不拖累其余条目；单条季海报瞬态走 mark_failed
  口径、其余条目照常成功。

S8：
- worker janitor 发布函数把 stage_stats/throughput 快照写 Redis 键
  ``aetrix:enrich:stage_stats``（含 ts）；
- health 侧本进程 stage_stats 为空时回退读 Redis 快照，新鲜（≤180s）
  采信、过期不采信。

脚手架复用 test_enrich_write_phase_zombie.py 的隔离 SQLite 方案。
"""
import json
import os
import tempfile
import threading
import time
import uuid

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)
from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

import pytest

from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import enrich_worker
from backend.emby_server import scan_progress as progress


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    _hyg_engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = _hyg_saved_engine
        _dbmod.configure_session_local(_hyg_saved_factory)
        if _hyg_saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _hyg_saved_url
        _hyg_engine.dispose()


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _make_item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "movie"),
        name=kw.pop("name", "测试"),
        file_path=kw.pop("file_path", "/tmp/x.mp4"),
        enrich_status=kw.pop("enrich_status", "pending"),
        **kw,
    )
    db.add(it)
    db.commit()
    return it


def _read_status_by_name(name):
    s = SessionLocal()
    try:
        r = s.query(em.MediaItem).filter(em.MediaItem.name == name).first()
        return r.enrich_status if r else None
    finally:
        s.close()


def _patch_common(monkeypatch, pre_fake, post_fake=None, apply_records=None):
    """_process_batch 的 mock 接缝：pre/post/父级推导/写库分发/继承联动。"""
    monkeypatch.setattr(enrich_worker, "_enrich_fetch_pre", pre_fake)
    if post_fake is not None:
        monkeypatch.setattr(enrich_worker, "_enrich_fetch_tmdb_post", post_fake)
    monkeypatch.setattr(
        enrich_worker, "_inherit_parent_info", lambda d, snapshot: None)
    monkeypatch.setattr(
        enrich_worker, "_folder_title_candidate", lambda d, snapshot: None)
    monkeypatch.setattr(
        enrich_worker, "_maybe_flip_inherit_children",
        lambda d, fetched: None)
    if apply_records is not None:
        def _fake_apply(d, fresh, fetched, queue=None):
            apply_records.append((fresh.name, fetched.get("marker")))
            fresh.enrich_status = "done"
        monkeypatch.setattr(enrich_worker, "_apply_write_phase", _fake_apply)


def _mixed_fixture(db):
    """多组混合批次：movie 组×2、series 组、episode 组（同剧两集）。"""
    m1 = _make_item(db, name="电影一", item_type="movie")
    series = _make_item(db, name="剧一", item_type="series", file_path=None)
    m2 = _make_item(db, name="电影二", item_type="movie")
    e1 = _make_item(db, name="剧一E1", item_type="episode",
                    series_id=series.id, season_number=1, episode_number=1)
    e2 = _make_item(db, name="剧一E2", item_type="episode",
                    series_id=series.id, season_number=1, episode_number=2)
    items = [m1, series, e1, e2, m2]
    snaps = [enrich_worker._snapshot_item(it) for it in items]
    db.rollback()
    return snaps


def _ok_pre(snapshot, holder=None, inherit_parent=None, folder_candidate=None):
    ctx = {"kind": snapshot.item_type or "movie", "needs_repair": False,
           "tmdb_plan": {"op": "none"}}
    return {"ok": True, "marker": snapshot.name}, ctx


def _ok_post(result, snapshot, tmdb_ctx, batch_out=None):
    out = dict(result)
    out["ok"] = True
    return out


class TestS2Equivalence:
    def test_workers1_and_workers4_same_outcome(self, db, monkeypatch):
        results = {}
        for workers in (1, 4):
            db.query(em.MediaItem).delete()
            db.commit()
            snaps = _mixed_fixture(db)
            records = []
            _patch_common(monkeypatch, _ok_pre, _ok_post, records)
            monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", workers)
            enrich_worker._process_batch(db, snaps)
            db.rollback()
            statuses = {name: _read_status_by_name(name) for name in
                        ("电影一", "电影二", "剧一", "剧一E1", "剧一E2")}
            results[workers] = (sorted(records), statuses)
        assert results[1] == results[4]
        # 五条全部落 done、写库标记与条目一一对应
        assert results[4][1] == {name: "done" for name in
                                  ("电影一", "电影二", "剧一", "剧一E1", "剧一E2")}
        assert results[4][0] == sorted(
            [(n, n) for n in ("电影一", "电影二", "剧一", "剧一E1", "剧一E2")])


class TestS2Concurrency:
    def test_different_groups_run_concurrently(self, db, monkeypatch):
        """3 个不同组的 pre 必须同时在场：串行跑 Barrier 必然超时失败。"""
        items = [_make_item(db, name=f"并发电影{i}", item_type="movie")
                 for i in range(3)]
        snaps = [enrich_worker._snapshot_item(it) for it in items]
        db.rollback()
        barrier = threading.Barrier(3)

        def _barrier_pre(snapshot, holder=None, inherit_parent=None,
                         folder_candidate=None):
            barrier.wait(timeout=15)
            return {"ok": True, "marker": snapshot.name}, None

        records = []
        _patch_common(monkeypatch, _barrier_pre, None, records)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", 4)
        enrich_worker._process_batch(db, snaps)
        assert sorted(n for n, _ in records) == ["并发电影0", "并发电影1", "并发电影2"]

    def test_same_group_stays_serial(self, db, monkeypatch):
        """同组（同 series_id）内 pre 并发计数峰值必须 =1（holder 共享语义）。"""
        series = _make_item(db, name="串行剧", item_type="series", file_path=None)
        eps = [_make_item(db, name=f"串行剧E{i}", item_type="episode",
                          series_id=series.id, season_number=1,
                          episode_number=i + 1)
               for i in range(3)]
        snaps = [enrich_worker._snapshot_item(it) for it in eps]
        db.rollback()
        lock = threading.Lock()
        state = {"cur": 0, "peak": 0}

        def _counting_pre(snapshot, holder=None, inherit_parent=None,
                          folder_candidate=None):
            with lock:
                state["cur"] += 1
                state["peak"] = max(state["peak"], state["cur"])
            time.sleep(0.05)
            with lock:
                state["cur"] -= 1
            return {"ok": True, "marker": snapshot.name}, None

        records = []
        _patch_common(monkeypatch, _counting_pre, None, records)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", 4)
        enrich_worker._process_batch(db, snaps)
        assert state["peak"] == 1
        assert len(records) == 3


class TestS2ExceptionIsolation:
    def test_one_pre_failure_does_not_block_others(self, db, monkeypatch):
        items = [_make_item(db, name=n, item_type="movie")
                 for n in ("好一", "坏条", "好二")]
        snaps = [enrich_worker._snapshot_item(it) for it in items]
        db.rollback()

        def _flaky_pre(snapshot, holder=None, inherit_parent=None,
                       folder_candidate=None):
            if snapshot.name == "坏条":
                raise RuntimeError("pre boom")
            return {"ok": True, "marker": snapshot.name}, None

        records = []
        _patch_common(monkeypatch, _flaky_pre, None, records)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", 4)
        enrich_worker._process_batch(db, snaps)
        assert sorted(n for n, _ in records) == ["好一", "好二"]
        assert _read_status_by_name("好一") == "done"
        assert _read_status_by_name("好二") == "done"
        assert _read_status_by_name("坏条") != "done"

    def test_season_poster_transient_marks_failed_only_that_item(
            self, db, monkeypatch):
        from backend.emby_server.tmdb import TmdbTransientError
        series = _make_item(db, name="季海报剧", item_type="series",
                            file_path=None)
        movie = _make_item(db, name="季海报电影", item_type="movie")
        snaps = [enrich_worker._snapshot_item(it) for it in (series, movie)]
        db.rollback()

        def _pre_tmdbid(snapshot, holder=None, inherit_parent=None,
                        folder_candidate=None):
            return {"ok": True, "marker": snapshot.name, "tmdb_id": "999"}, None

        def _season_io(d, snapshot, fetched):
            if snapshot.item_type == "series":
                raise TmdbTransientError("season poster transient")

        records = []
        _patch_common(monkeypatch, _pre_tmdbid, None, records)
        monkeypatch.setattr(
            enrich_worker, "_enrich_season_posters_io", _season_io)
        monkeypatch.setattr(enrich_worker, "ENRICH_IO_WORKERS", 4)
        enrich_worker._process_batch(db, snaps)
        assert [n for n, _ in records] == ["季海报电影"]
        assert _read_status_by_name("季海报电影") == "done"
        assert _read_status_by_name("季海报剧") != "done"


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)


class TestS8StageStatsBridge:
    def test_worker_publishes_stage_stats(self):
        progress.reset()
        try:
            progress.note_stage("enrich_item", 12.0)
            fake = _FakeRedis()
            ok = enrich_worker._publish_stage_stats_to_redis(redis_client=fake)
            assert ok is True
            raw = fake.store.get("aetrix:enrich:stage_stats")
            assert raw, "必须写入约定 Redis 键"
            payload = json.loads(raw)
            assert "ts" in payload and abs(payload["ts"] - time.time()) < 60
            assert "enrich_item" in payload["stages"]
            assert "done_per_min" in payload["throughput"]
        finally:
            progress.reset()

    def test_health_falls_back_to_fresh_redis_snapshot(self, monkeypatch):
        from backend import health_report
        monkeypatch.setattr(progress, "stage_stats", lambda: {})
        fake = _FakeRedis()
        stages = {"enrich_item": {"label": "x", "count": 3, "ms": 30,
                                  "avg_ms": 10, "last_at": None}}
        fake.store["aetrix:enrich:stage_stats"] = json.dumps(
            {"ts": time.time(), "stages": stages, "throughput": {}})
        monkeypatch.setattr(_dbmod, "redis_client", fake)
        assert health_report._enrich_stage_stats() == stages

    def test_health_ignores_stale_redis_snapshot(self, monkeypatch):
        from backend import health_report
        monkeypatch.setattr(progress, "stage_stats", lambda: {})
        fake = _FakeRedis()
        fake.store["aetrix:enrich:stage_stats"] = json.dumps(
            {"ts": time.time() - 181, "stages": {"enrich_item": {}}, 
             "throughput": {}})
        monkeypatch.setattr(_dbmod, "redis_client", fake)
        assert health_report._enrich_stage_stats() == {}

    def test_health_prefers_local_stats(self, monkeypatch):
        from backend import health_report
        local = {"enrich_item": {"label": "x", "count": 1, "ms": 5,
                                 "avg_ms": 5, "last_at": None}}
        monkeypatch.setattr(progress, "stage_stats", lambda: dict(local))
        fake = _FakeRedis()
        fake.store["aetrix:enrich:stage_stats"] = json.dumps(
            {"ts": time.time() - 9999, "stages": {"other": {}}, 
             "throughput": {}})
        monkeypatch.setattr(_dbmod, "redis_client", fake)
        assert health_report._enrich_stage_stats() == local
