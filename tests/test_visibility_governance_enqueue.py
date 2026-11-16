"""可见性治理任务自动入队（闭环登记 #6）单测。

缺口：visibility_governance.create_task 此前零生产调用方——治理逻辑在、
复核队列的管理端也在，但扫描/识别链从不入队，队列永远是空的。

本批接线口径（与 identify_review 的识别收尾入队先例同形态）：
- 入队判定收口在 visibility_governance.maybe_enqueue_unbound：开关关 →
  不入队（行为与未接线时逐字一致）；只治理 movie/series；案 A 已绑定 →
  不入队；同条目已有 pending 的 shelf 任务 → 不重复建行（幂等）。
- 时机在 enrich_worker._enrich_apply 写库收尾：只对本轮落 done 的条目
  转调（pending 补轮/重试、failed 的身份未定，不入队）；入队失败静默
  只记 warning，审计旁路绝不拖垮识别收尾。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import tempfile
import uuid
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import models as em
from backend.emby_server import visibility_governance as vg


# ---------------------------------------------------------------------------
# A 部分：maybe_enqueue_unbound 单元（自有引擎，与 test_visibility_governance 同款）
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/vg_enqueue.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _mk(db, lib, item_type, name, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name, file_path=f"/m/{uuid.uuid4().hex}.mkv",
        **kw)
    db.add(it)
    db.flush()
    return it


@pytest.fixture()
def scene(db):
    lib = em.Library(guid=uuid.uuid4().hex, name="库",
                     collection_type="mixed", is_enabled=True)
    db.add(lib)
    db.flush()
    s = {"lib": lib}
    s["bound"] = _mk(db, lib, "movie", "已绑定", tmdb_id="123")
    s["unbound"] = _mk(db, lib, "movie", "未绑定")
    s["douban_only"] = _mk(db, lib, "movie", "只有豆瓣",
                           external_ids='{"douban": "1234567"}')
    s["unbound_series"] = _mk(db, lib, "series", "未绑定剧")
    bound_series = _mk(db, lib, "series", "已绑定剧", tmdb_id="999")
    s["ep_of_unbound"] = _mk(db, lib, "episode", "未绑定剧的集",
                             series_id=s["unbound_series"].id,
                             season_number=1, episode_number=1)
    s["ep_of_bound"] = _mk(db, lib, "episode", "已绑定剧的集",
                           series_id=bound_series.id,
                           season_number=1, episode_number=1)
    s["other"] = _mk(db, lib, "person", "人物不在治理内")
    return s


def _tasks(db, item_id=None):
    q = db.query(em.VisibilityGovernanceTask)
    if item_id is not None:
        q = q.filter(em.VisibilityGovernanceTask.item_id == item_id)
    return q.all()


def test_enqueue_switch_off_is_noop(db, scene, monkeypatch):
    """开关关：不入队、无任务无事件（与未接线时逐字一致）。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 0)
    assert vg.maybe_enqueue_unbound(db, scene["unbound"]) is False
    assert _tasks(db) == []
    rows, total = vg.list_events(db, item_id=scene["unbound"].id)
    assert total == 0


def test_enqueue_unbound_movie(db, scene, monkeypatch):
    """开关开 + 未绑定电影：入队一条 pending 的 shelf 复核任务 + 事件。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    item = scene["unbound"]
    assert vg.maybe_enqueue_unbound(db, item) is True
    tasks = _tasks(db, item.id)
    assert len(tasks) == 1
    assert tasks[0].status == "pending"
    assert tasks[0].surface == "shelf"
    assert tasks[0].reason == vg.AUTO_ENQUEUE_REASON
    assert tasks[0].item_name == "未绑定"
    rows, total = vg.list_events(db, item_id=item.id)
    assert total == 1
    assert rows[0].event_type == "task_created"
    assert rows[0].actor is None  # 系统动作


def test_enqueue_skips_bound_items(db, scene, monkeypatch):
    """已绑定（tmdb / 豆瓣单源身份）不入队：它们在货架上正常露出。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    assert vg.maybe_enqueue_unbound(db, scene["bound"]) is False
    # 2026-11-02 扩围：external_ids 任一来源键非空即已绑定
    assert vg.maybe_enqueue_unbound(db, scene["douban_only"]) is False
    assert _tasks(db) == []


def test_enqueue_series_but_not_episodes_or_others(db, scene, monkeypatch):
    """只治理 movie/series 本体：集跟随所属剧（剧入队即可），其余类型不入。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    assert vg.maybe_enqueue_unbound(db, scene["unbound_series"]) is True
    assert vg.maybe_enqueue_unbound(db, scene["ep_of_unbound"]) is False
    assert vg.maybe_enqueue_unbound(db, scene["ep_of_bound"]) is False
    assert vg.maybe_enqueue_unbound(db, scene["other"]) is False
    assert len(_tasks(db)) == 1


def test_enqueue_idempotent_while_pending(db, scene, monkeypatch):
    """幂等：同条目已有 pending 任务时不重复建行（连调两次仍一条）。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    item = scene["unbound"]
    assert vg.maybe_enqueue_unbound(db, item) is True
    db.commit()
    assert vg.maybe_enqueue_unbound(db, item) is False
    assert len(_tasks(db, item.id)) == 1


def test_enqueue_again_after_review(db, scene, monkeypatch):
    """旧任务已处置（终态）不挡新一轮：再次终态仍未绑定可再入队。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    item = scene["unbound"]
    assert vg.maybe_enqueue_unbound(db, item) is True
    db.commit()
    task = _tasks(db, item.id)[0]
    vg.review_task(db, task.id, True, actor=7, note="看过了")
    db.commit()
    assert vg.maybe_enqueue_unbound(db, item) is True
    tasks = _tasks(db, item.id)
    assert len(tasks) == 2
    assert sum(1 for t in tasks if t.status == "pending") == 1


# ---------------------------------------------------------------------------
# B 部分：enrich 写库收尾端到端（与 test_enrich_completion_bar 同款夹具）
# ---------------------------------------------------------------------------

_fd, _p = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import enrich_worker  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （防 #1668 同族的全局引擎污染）。"""
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_p}"
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
def sdb():
    s = SessionLocal()
    try:
        s.query(em.VisibilityEvent).delete()
        s.query(em.VisibilityGovernanceTask).delete()
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _sitem(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "movie"),
        name=kw.pop("name", "片名"),
        file_path=kw.pop("file_path", ""),
        enrich_status="enriching",
        **kw,
    )
    db.add(it)
    db.commit()
    return it


def _sapply(db, it, fetched):
    with mock.patch("backend.emby_server.tmdb.tmdb_client") as tc:
        tc.configured = True
        enrich_worker._enrich_apply(db, it, fetched)
    db.commit()
    db.refresh(it)
    return it


BASE_OK = {"ok": True, "poster": None, "fanart": None, "external_subs": [],
           "nfo_data": None, "tmdb_hit": None, "tmdb_details": None,
           "error": None}


def _gtasks(db, item_id):
    return (db.query(em.VisibilityGovernanceTask)
            .filter(em.VisibilityGovernanceTask.item_id == item_id).all())


def test_enrich_done_unbound_movie_enqueues(sdb, monkeypatch):
    """识别终态 done + 仍未绑定（搜过无命中）：治理任务被自动入队。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    it = _sitem(sdb, name="逃避行", production_year=2023,
                poster_path="/img/p.jpg")
    _sapply(sdb, it, dict(BASE_OK))
    assert it.enrich_status == "done"
    assert not it.tmdb_id  # 前提：确实未绑定
    tasks = _gtasks(sdb, it.id)
    assert len(tasks) == 1
    assert tasks[0].status == "pending"
    assert tasks[0].surface == "shelf"


def test_enrich_done_unbound_switch_off_no_task(sdb, monkeypatch):
    """开关关：同样的终态不产生任何治理任务（现行行为逐字一致）。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 0)
    it = _sitem(sdb, name="逃避行", production_year=2023,
                poster_path="/img/p.jpg")
    _sapply(sdb, it, dict(BASE_OK))
    assert it.enrich_status == "done"
    assert _gtasks(sdb, it.id) == []


def test_enrich_done_bound_movie_no_task(sdb, monkeypatch):
    """识别终态 done 且已绑定（有 tmdb_id）：不入队。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    enrich_worker.save_enrich_tuning_config(sdb, {
        enrich_worker.CFG_ENRICH_FIELD_COMPLETENESS: False,
    })
    try:
        it = _sitem(sdb, name="007：女王密使", production_year=1969,
                    tmdb_id="1642")
        _sapply(sdb, it, dict(BASE_OK))
        assert it.enrich_status == "done"
        assert _gtasks(sdb, it.id) == []
    finally:
        from backend import models as _models
        from backend.integrations import store as _store
        sdb.query(_models.SystemConfig).filter(
            _models.SystemConfig.key
            == enrich_worker.CFG_ENRICH_FIELD_COMPLETENESS
        ).delete(synchronize_session=False)
        sdb.commit()
        _store.invalidate(enrich_worker.CFG_ENRICH_FIELD_COMPLETENESS)


def test_enrich_pending_unbound_no_task(sdb, monkeypatch):
    """未终态（核心字段不足退回 pending）：身份未定，不入队。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)
    it = _sitem(sdb, name="只有标题", production_year=None)
    fetched = dict(BASE_OK)
    fetched["douban_hit"] = {"id": "x", "title": "只有标题"}  # alt_hit 非空
    with mock.patch("backend.emby_server.altmeta.apply"), \
            mock.patch("backend.emby_server.tmdb.tmdb_client") as tc:
        tc.configured = True
        enrich_worker._enrich_apply(sdb, it, fetched)
    sdb.commit()
    sdb.refresh(it)
    assert it.enrich_status == "pending"
    assert _gtasks(sdb, it.id) == []


def test_enrich_enqueue_failure_is_silent(sdb, monkeypatch):
    """入队写失败不许拖垮识别收尾：条目照常 done、不抛异常。"""
    monkeypatch.setattr(vg, "VISIBILITY_GOVERNANCE_ENABLED", 1)

    def _boom(*a, **kw):
        raise RuntimeError("入队写失败（模拟）")

    monkeypatch.setattr(vg, "create_task", _boom)
    it = _sitem(sdb, name="逃避行", production_year=2023,
                poster_path="/img/p.jpg")
    _sapply(sdb, it, dict(BASE_OK))  # 不许抛
    assert it.enrich_status == "done"
