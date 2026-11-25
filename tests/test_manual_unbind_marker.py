"""人工解绑标记 manual_unbound 回归（2026-11-25 用户亲点根治）。

实证背景（海报残余 82 条重识别报告 §5）：9 条错绑被人工解绑后，站内
巡检把无 tmdb 的 done 条目翻回 pending，工人按原 NFO/搜索证据重新
识别——证据源没动，解绑不持久：7 条被绑回（66305 实证来源是 NFO 里
写死的错号，stage record tmdb_id_source="nfo"）。

标记语义：manual_unbound=True 时——
① 巡检（干净度/假完成/none 回捞）不翻它 pending；
② 识别认领（identify 队列与单队列口径）排除它（fill 补资料不挡）；
③ 一切自动绑定路径跳过它：两段式 apply_identity（含 NFO 证据）、
   经典 _enrich_apply（tmdb_id 直写 / find_by_imdb 命中落库）、
   apply_nfo 的身份写入、tmdb.apply、宽松重识别孪生回填；
④ 管理员手动解绑置标记、手动绑定成功清除标记（手动永远优先）；
⑤ 无标记条目行为与基线一致（每组都带未标记对照行钉死）。
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import init_db  # noqa: E402

import pytest  # noqa: E402

from backend.api import admin_scrape  # noqa: E402
from backend.emby_server import enrich_worker as ew  # noqa: E402
from backend.emby_server import identify_two_stage as ts  # noqa: E402
from backend.emby_server import merge_versions_worker as mvw  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server import nfo as nfo_lib  # noqa: E402
from backend.emby_server.tmdb import tmdb_client  # noqa: E402
from backend.integrations import store  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （与 test_bind_tmdb / test_enrich_dirty_done_gate 同口径）。"""
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite:///" + _tmppath
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


def _clear_tuning_cache():
    ew._enrich_tuning_cache["at"] = 0.0
    ew._enrich_tuning_cache["cfg"] = None


def _set_cfg(key, value):
    _clear_tuning_cache()
    db = _dbmod.SessionLocal()
    try:
        store.write_values(db, {key: value}, {key: "manual-unbound 测试"})
        db.commit()
    finally:
        db.close()
    store.invalidate(key)
    _clear_tuning_cache()


@pytest.fixture(autouse=True)
def _flags():
    _set_cfg(ew.CFG_ENRICH_NONE_AUTO_RESCHEDULE, "1")
    _set_cfg("enrich_dirty_done_patrol", "1")
    yield


@pytest.fixture()
def db():
    s = _dbmod.SessionLocal()
    try:
        s.query(em.EmbyPerson).delete()
        s.query(em.EnrichStageRecord).delete()
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.query(em.Library).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _make_item(db, name="测试条目", **kw):
    lib = em.Library(guid=uuid.uuid4().hex, name="测试库",
                     collection_type="tvshows")
    db.add(lib)
    db.flush()
    kw.setdefault("enrich_status", "pending")
    kw.setdefault("item_type", "series")
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, name=name,
        sort_name=name, file_path=f"/m/{uuid.uuid4().hex}.mkv",
        production_year=2021, **kw)
    db.add(item)
    db.flush()
    return item


# ---------------------------------------------------------------------------
# ① 巡检不翻 pending
# ---------------------------------------------------------------------------

def test_dirty_patrol_skips_manual_unbound(db):
    """干净度巡检：无 tmdb 的 done 行本该被翻回（no_tmdb），带标记的不翻。"""
    marked = _make_item(
        db, "已解绑行", enrich_status="done", manual_unbound=True,
        file_fingerprint="fp-a", metadata_source="none",
        last_scraped_at=datetime.now() - timedelta(days=30))
    control = _make_item(
        db, "对照行", enrich_status="done",
        file_fingerprint="fp-b", metadata_source="none",
        last_scraped_at=datetime.now() - timedelta(days=30))
    stats = ew.dirty_done_patrol(db, details_fetcher=lambda tid, kind: None)
    db.refresh(marked)
    db.refresh(control)
    assert marked.enrich_status == "done"
    assert control.enrich_status == "pending"
    assert stats["requeued"] == 1


def test_fake_done_patrol_skips_manual_unbound(db):
    """假完成巡检：done + 无刮削时间戳的行本该被翻回，带标记的不翻。"""
    marked = _make_item(db, "已解绑行", enrich_status="done",
                        manual_unbound=True, last_scraped_at=None)
    control = _make_item(db, "对照行", enrich_status="done",
                         last_scraped_at=None)
    n = ew.fake_done_patrol(db)
    db.refresh(marked)
    db.refresh(control)
    assert marked.enrich_status == "done"
    assert control.enrich_status == "pending"
    assert n == 1


def test_none_reschedule_skips_manual_unbound(db):
    """none 定时回捞：done+none+无海报+无 tmdb 的行本该被捞回，带标记的不捞。"""
    kw = dict(enrich_status="done", metadata_source="none",
              file_fingerprint="fp-x", enrich_next_retry_at=None)
    marked = _make_item(db, "已解绑行", manual_unbound=True, **kw)
    control = _make_item(db, "对照行", **kw)
    n = ew.enrich_none_auto_reschedule(db)
    db.refresh(marked)
    db.refresh(control)
    assert marked.enrich_status == "done"
    assert control.enrich_status == "pending"
    assert n == 1


# ---------------------------------------------------------------------------
# ② 识别认领排除（fill 不挡）
# ---------------------------------------------------------------------------

def test_identify_claim_skips_manual_unbound(db):
    marked = _make_item(db, "已解绑行", item_type="movie",
                        manual_unbound=True)
    control = _make_item(db, "对照行", item_type="movie")
    claimed = ew._claim_batch(db, 10, queue="identify")
    claimed_ids = [r.id for r in claimed]
    assert control.id in claimed_ids
    assert marked.id not in claimed_ids
    db.refresh(marked)
    assert marked.enrich_status == "pending"


def test_single_queue_claim_skips_manual_unbound(db):
    marked = _make_item(db, "已解绑行", item_type="movie",
                        manual_unbound=True)
    control = _make_item(db, "对照行", item_type="movie")
    claimed = ew._claim_batch(db, 10)
    claimed_ids = [r.id for r in claimed]
    assert control.id in claimed_ids
    assert marked.id not in claimed_ids


def test_fill_claim_still_claims_bound_manual_unbound(db):
    """fill 队列只补资料不产生绑定：仅 imdb 身份的带标记条目照常认领。"""
    item = _make_item(db, "仅imdb行", item_type="movie",
                      imdb_id="tt1234567", manual_unbound=True)
    claimed = ew._claim_batch(db, 10, queue="fill")
    assert item.id in [r.id for r in claimed]


# ---------------------------------------------------------------------------
# ③ 自动绑定路径跳过
# ---------------------------------------------------------------------------

def test_apply_identity_skips_manual_unbound(db):
    """两段式识别段：搜索证据再全，带标记条目也不落绑、不留识别记录。"""
    item = _make_item(db, "已解绑剧", manual_unbound=True)
    fetched = {"tmdb_id": "60300",
               "tmdb_details": {"overview": "简介", "imdb_id": "tt0903747"}}
    assert ts.apply_identity(db, item, fetched) is None
    db.flush()
    assert not item.tmdb_id
    assert not item.imdb_id
    rec = (db.query(em.EnrichStageRecord)
           .filter(em.EnrichStageRecord.item_id == item.id).first())
    assert rec is None


def test_apply_identity_nfo_evidence_skips_manual_unbound(db):
    """66305 案的复刻：NFO 里写死的编号（explicit 证据）也不许绑回。"""
    item = _make_item(db, "后人", manual_unbound=True)
    fetched = {"nfo_data": {"tmdb_id": "763220"}}
    assert ts.apply_identity(db, item, fetched) is None
    assert not item.tmdb_id
    # 对照：未标记条目同证据照常绑定（基线不变）
    control = _make_item(db, "对照剧")
    binding = ts.apply_identity(db, control, fetched)
    assert binding is not None
    assert control.tmdb_id == "763220"


def test_apply_nfo_identity_not_written_when_marked(db):
    """apply_nfo：带标记时文字照落、身份列（tmdb/imdb）一律不写。"""
    item = _make_item(db, "旧名", item_type="movie", manual_unbound=True)
    nfo_lib.apply_nfo(item, {"title": "后人", "tmdb_id": "763220",
                             "imdb_id": "tt7654321"}, "movie")
    assert not item.tmdb_id
    assert not item.imdb_id
    assert item.name == "后人"
    # 对照：未标记条目 NFO 身份照常写入（基线不变）
    control = _make_item(db, "对照", item_type="movie")
    nfo_lib.apply_nfo(control, {"tmdb_id": "763220",
                                "imdb_id": "tt7654321"}, "movie")
    assert control.tmdb_id == "763220"
    assert control.imdb_id == "tt7654321"


def test_enrich_apply_tmdb_id_branch_skips_manual_unbound(db):
    """经典写库分支 A（fetched 带 tmdb_id+详情）：身份与候选 imdb 都不落。"""
    item = _make_item(db, "已解绑片", item_type="movie", manual_unbound=True,
                      enrich_status="enriching")
    fetched = {"tmdb_id": "10775",
               "tmdb_details": {"id": 10775, "overview": "错候选简介",
                                "external_ids": {"imdb_id": "tt9999999"}}}
    ew._enrich_apply(db, item, fetched)
    db.flush()
    assert not item.tmdb_id
    assert not item.imdb_id


def test_enrich_apply_find_by_imdb_hit_skips_manual_unbound(db):
    """经典写库分支 B：find_by_imdb 的命中以 tmdb_hit 形态落库——带标记
    条目不许被「转正」成绑定（metasources engine 的 IMDb 桥产物）。"""
    hit = {"id": 10775, "title": "Buntús Cainte", "overview": "错候选",
           "vote_average": 7.0}
    item = _make_item(db, "无间道", item_type="movie", manual_unbound=True,
                      enrich_status="enriching")
    ew._enrich_apply(db, item, {"tmdb_hit": hit})
    db.flush()
    assert not item.tmdb_id
    # 对照：未标记条目同命中照常落绑（基线不变）
    control = _make_item(db, "对照片", item_type="movie",
                         enrich_status="enriching")
    ew._enrich_apply(db, control, {"tmdb_hit": hit})
    db.flush()
    assert control.tmdb_id == "10775"


def test_tmdb_apply_skips_manual_unbound(db):
    """tmdb.apply 叶守卫：扫描首刮与经典写库共用入口，带标记一律不落。"""
    hit = {"id": 555, "title": "某片", "overview": "简介"}
    item = _make_item(db, "已解绑片", item_type="movie", manual_unbound=True)
    tmdb_client.apply(item, hit, "movie")
    assert not item.tmdb_id
    control = _make_item(db, "对照片", item_type="movie")
    tmdb_client.apply(control, hit, "movie")
    assert control.tmdb_id == "555"


def test_loose_repair_skips_manual_unbound(db):
    """宽松重识别：同库孪生回填是自动绑入口，带标记的剧不许被抄回编号。"""
    lib = em.Library(guid=uuid.uuid4().hex, name="剧集库",
                     collection_type="tvshows")
    db.add(lib)
    db.flush()

    def _series(name, **kw):
        kw.setdefault("enrich_status", "done")
        it = em.MediaItem(
            guid=uuid.uuid4().hex, library_id=lib.id, item_type="series",
            name=name, sort_name=name,
            file_path=f"/m/{uuid.uuid4().hex}.mkv",
            production_year=2020, **kw)
        db.add(it)
        db.flush()
        return it

    _series("双子剧", tmdb_id="999")
    marked = _series("双子剧", manual_unbound=True)
    control = _series("双子剧")
    mvw._repair_series_tmdb_loose(db, {"enabled": True, "batch_limit": 100})
    db.flush()
    assert not marked.tmdb_id
    assert control.tmdb_id == "999"


# ---------------------------------------------------------------------------
# ④ 管理端：解绑置标记、手动绑定清除标记
# ---------------------------------------------------------------------------

class _NoTmdb:
    """TMDB 未配置替身：绑定走纯落库路径（与 test_bind_tmdb 同口径）。"""
    configured = False


def test_admin_unbind_sets_marker_and_bind_clears_it(db, monkeypatch):
    monkeypatch.setattr(admin_scrape, "tmdb_client", _NoTmdb())
    staff = SimpleNamespace(id=1)
    item = _make_item(db, "被解绑的片", item_type="movie", tmdb_id="10775",
                      metadata_locked=True, enrich_status="done")
    db.commit()

    # 解绑：置标记、清绑定、解锁（旧口径）
    res = admin_scrape.bind_tmdb_id(
        item.id, admin_scrape.TmdbBindRequest(tmdb_id=""), staff, db)
    assert res["success"] is True and res["unbound"] is True
    db.refresh(item)
    assert not item.tmdb_id
    assert item.manual_unbound is True
    assert item.metadata_locked is False

    # 手动绑定：清标记、落绑、加锁（旧口径）
    res = admin_scrape.bind_tmdb_id(
        item.id,
        admin_scrape.TmdbBindRequest(tmdb_id="392044", verify=False),
        staff, db)
    assert res["success"] is True and res["unbound"] is False
    db.refresh(item)
    assert item.tmdb_id == "392044"
    assert item.manual_unbound is False
    assert item.metadata_locked is True
    assert any("人工解绑标记" in n for n in res["notes"])
