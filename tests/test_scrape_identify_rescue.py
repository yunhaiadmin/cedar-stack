"""刮削没认上修复组合（2026-11-11 用户批准，查因见
reports/scrape-miss-sample-20261111.md）：

1. 文件夹名并行候选：存名是发布组垃圾串 / 季文件夹名时，用 file_path
   的标题文件夹名再搜一次（TMDB 重试 + 绑定门按文件夹名/年份开口、
   豆瓣兜底同口径），仍走现有置信闸，不绕闸直链。
2. 回捞口径补漏：_none_reschedulable_query 里 series 自身无指纹时，
   任一子集（episode）有指纹即算数；软删行显式排除（钉死口径：
   只读计数走 func.count 时 ORM 钩子包不住子查询，不能靠钩子）。
3. 年份回退：TMDB 带年搜有结果但被置信闸全灭时，也触发去年份重搜
   （旧口径只有带年零结果才触发）；去年份命中仍走置信闸 + 年份降级。
4. 存量重认通道：requeue_missing_metadata_top —— 按「done + 可见 +
   顶层 movie/series + 缺 tmdb/海报/简介任一」批量置 pending，
   带 limit 分批与 dry_run 干跑计数（生产可见口径约 1,220 部）。
"""
import os
import tempfile

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import enrich_worker as ew
from backend.emby_server import identify_two_stage as its
from backend.emby_server import scanner
from backend.emby_server import tmdb as tmdb_mod
from backend.emby_server.tmdb import TmdbClient


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （同 test_enrich_chain_step4 口径，防 #1668 式全局引擎毒化）。"""
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
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _make_item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "series"),
        name=kw.pop("name", "测试"),
        file_path=kw.pop("file_path", None),
        enrich_status=kw.pop("enrich_status", "done"),
        **kw,
    )
    db.add(it)
    db.commit()
    return it


# ============ 1a. 文件夹名推导（纯函数） ============

class TestFolderCandidateFromPath:
    def test_movie_garbage_name_folder_title(self):
        """《英雄无泪》生产样本：存名是发布组文件名串，文件夹带年份。"""
        path = ("mount://4/video/影库/英雄无泪(1984)/"
                "Heroes.Shed.No.Tears.1986.BluRay.1080p.x264.mkv")
        got = ew._folder_candidate_from_path(
            path, "Heroes.Shed.No.Tears.1986.BluRay.1080p.x264")
        assert got == ("英雄无泪", 1984)

    def test_release_group_intermediate_folder_skipped(self):
        """《婚姻保卫战》生产样本：文件父目录是发布组名，上上级才是标题夹。"""
        path = ("mount://3/video/国产剧/婚姻保卫战 (2010)/"
                "Marriage.Battle.complete.720p.WEB-DL.x264/第01集.mkv")
        got = ew._folder_candidate_from_path(path, "婚姻保卫战")
        # 标题夹清洗后与存名相同 → 不是「不一致候选」，返回 None；
        # 但发布组夹不得被当成候选标题（否则会拿垃圾串去搜）。
        assert got is None

    def test_release_group_folder_never_candidate_for_other_stored(self):
        path = ("mount://3/video/国产剧/婚姻保卫战 (2010)/"
                "Marriage.Battle.complete.720p.WEB-DL.x264/第01集.mkv")
        got = ew._folder_candidate_from_path(path, "完全不同的存名")
        assert got == ("婚姻保卫战", 2010)

    def test_season_folder_skipped(self):
        """存名「Season 01」的剧：季文件夹跳过，取剧名文件夹。"""
        path = "mount://4/video/英美剧/黑色孤儿 (2013)/Season 01/Orphan.Black.S01E01.mkv"
        got = ew._folder_candidate_from_path(path, "Season 01")
        assert got == ("黑色孤儿", 2013)

    def test_folder_same_as_stored_returns_none(self):
        path = "/video/英雄无泪 (1984)/movie.mkv"
        assert ew._folder_candidate_from_path(path, "英雄无泪") is None

    def test_no_qualifying_folder_returns_none(self):
        # 全是无年份无中文的发布组目录：不许拿垃圾串当候选
        path = "/video/GROUP.Release.1080p.WEB-DL/file.mkv"
        assert ew._folder_candidate_from_path(path, "别的存名") is None

    def test_empty_path_returns_none(self):
        assert ew._folder_candidate_from_path(None, "x") is None
        assert ew._folder_candidate_from_path("", "x") is None


# ============ 1b. series 无 file_path 时经子集推导 ============

class TestFolderCandidateSeriesViaEpisode:
    def test_series_from_episode_child_path(self, db):
        series = _make_item(db, name="Season 01", item_type="series",
                            file_path=None)
        _make_item(db, name="第1集", item_type="episode",
                   series_id=series.id,
                   file_path=("mount://4/video/英美剧/黑色孤儿 (2013)/"
                              "Season 01/Orphan.Black.S01E01.mkv"))
        snap = SimpleNamespace(id=series.id, item_type="series",
                               name="Season 01", file_path=None,
                               production_year=None, tmdb_id=None)
        assert ew._folder_title_candidate(db, snap) == ("黑色孤儿", 2013)

    def test_movie_direct_path(self, db):
        snap = SimpleNamespace(
            id=1, item_type="movie", tmdb_id=None,
            name="Heroes.Shed.No.Tears.1986.BluRay.1080p.x264",
            file_path=("mount://4/video/影库/英雄无泪(1984)/"
                       "Heroes.Shed.No.Tears.1986.BluRay.1080p.x264.mkv"))
        assert ew._folder_title_candidate(db, snap) == ("英雄无泪", 1984)

    def test_has_tmdb_id_returns_none(self, db):
        snap = SimpleNamespace(
            id=1, item_type="movie", tmdb_id="69161", name="垃圾串",
            file_path="mount://4/video/影库/英雄无泪(1984)/x.mkv")
        assert ew._folder_title_candidate(db, snap) is None

    def test_episode_kind_returns_none(self, db):
        snap = SimpleNamespace(
            id=1, item_type="episode", tmdb_id=None, name="第1集",
            file_path="mount://4/video/英美剧/黑色孤儿 (2013)/Season 01/x.mkv")
        assert ew._folder_title_candidate(db, snap) is None


# ============ 1c. 绑定标题硬门给文件夹命中开口 ============

class TestTitleGateExtraNames:
    _DETAILS = {"id": 69161, "title": "英雄无泪",
                "original_title": "Heroes Shed No Tears",
                "release_date": "1984-07-20"}

    def _item(self):
        # 存名与命中标题/原名归一后零交集（发布组串），标题门才判得出
        return SimpleNamespace(
            name="GROUP.Release.1080p.WEB-DL.x264",
            production_year=1986)

    def test_without_extra_names_rejected(self):
        # 旧口径基线：只拿垃圾存名对账 → 零交集硬拒（样本在旧门下必死）
        assert its.search_binding_title_reject_reason(
            self._DETAILS, self._item()) == "title_mismatch"

    def test_with_folder_title_passes(self):
        assert its.search_binding_title_reject_reason(
            self._DETAILS, self._item(),
            extra_names=("英雄无泪",)) is None

    def test_unrelated_extra_name_still_rejected(self):
        assert its.search_binding_title_reject_reason(
            self._DETAILS, self._item(),
            extra_names=("风云再起",)) == "title_mismatch"


# ============ 1d. post 集成：文件夹名重试命中且过绑定门 ============

def _tmdb_configured():
    return mock.patch.object(type(tmdb_mod.tmdb_client), "configured",
                             new_callable=mock.PropertyMock, return_value=True)


class TestFolderRetryInPost:
    def test_folder_hit_survives_gates(self):
        hit = {"id": 69161, "title": "英雄无泪",
               "original_title": "Heroes Shed No Tears",
               "release_date": "1984-07-20", "poster_path": "/x.jpg"}
        details = dict(hit, overview="清朝末年……", genres=[])

        def _work(need_search, name, year, kind, tmdb_id, want_details):
            if name == "英雄无泪":
                return hit, details
            return None, None

        item = SimpleNamespace(
            id=2676466, library_id=40, item_type="movie",
            name="Heroes.Shed.No.Tears.1986.BluRay.1080p.x264",
            file_path=("mount://4/video/影库/英雄无泪(1984)/"
                       "Heroes.Shed.No.Tears.1986.BluRay.1080p.x264.mkv"),
            production_year=1986,  # 存年是错的（文件夹/TMDB 均为 1984）
            poster_path=None, primary_image_url=None, imdb_id=None,
            aliases=None, tmdb_id=None, overview=None, genres=None,
            metadata_locked=False, metadata_source=None,
            enrich_status="enriching", enrich_attempts=0,
            repair_requested_at=None, season_number=None,
            episode_number=None, series_id=None)
        result = {"tmdb_hit": None, "tmdb_details": None, "ok": True,
                  "error": None, "nfo_data": None, "poster": None,
                  "fanart": None, "external_subs": []}
        ctx = {"kind": "movie", "needs_repair": False,
               "tmdb_plan": {"op": "search"},
               "folder_candidate": ("英雄无泪", 1984)}
        with _tmdb_configured(), \
                mock.patch.object(scanner, "_tmdb_work",
                                  side_effect=_work), \
                mock.patch.object(ew, "_should_multisource_fill_missing",
                                  return_value=False), \
                mock.patch.object(tmdb_mod, "prewarm_images",
                                  return_value=[]), \
                mock.patch.object(tmdb_mod, "cast_list", return_value=[]):
            out = ew._enrich_fetch_tmdb_post(
                result, item, ctx,
                batch_out={"hit": None, "details": None,
                           "transient": False})
        assert out.get("tmdb_hit") is not None
        assert str(out["tmdb_hit"]["id"]) == "69161"
        assert "tmdb_gate_rejected" not in out
        assert out.get("tmdb_hit_folder_title") == "英雄无泪"


# ============ 2. 回捞口径补漏 ============

class TestNoneReschedulableChildFingerprint:
    def _series(self, db, name, **kw):
        base = dict(item_type="series", metadata_source="none",
                    tmdb_id=None, poster_path=None, primary_image_url=None,
                    enrich_status="done", enrich_none_reschedules=0,
                    enrich_next_retry_at=None)
        base.update(kw)
        return _make_item(db, name=name, **base)

    def _query_ids(self, db):
        cutoff = datetime.now() + timedelta(days=1)
        # 长尾截止按两个生产调用点的形状传真实值（None 退路在
        # SQLAlchemy 2.x 下构造期即抛 ArgumentError，无人走——见回执）
        q = ew._none_reschedulable_query(
            db, cutoff, 6,
            longtail_cutoff=datetime.now() - timedelta(days=30))
        return {r.id for r in q.all()}

    def test_series_with_episode_fingerprint_included(self, db):
        s = self._series(db, "子集有指纹的剧", file_fingerprint=None)
        _make_item(db, name="第1集", item_type="episode", series_id=s.id,
                   file_fingerprint="fp-ep-1", enrich_status="done")
        assert s.id in self._query_ids(db)

    def test_series_without_any_fingerprint_excluded(self, db):
        s = self._series(db, "全家无指纹的剧", file_fingerprint=None)
        _make_item(db, name="第1集", item_type="episode", series_id=s.id,
                   file_fingerprint=None, enrich_status="done")
        assert s.id not in self._query_ids(db)

    def test_series_own_fingerprint_still_included(self, db):
        s = self._series(db, "自身有指纹的剧", file_fingerprint="fp-own")
        assert s.id in self._query_ids(db)

    def test_soft_deleted_series_not_counted_even_via_func_count(self, db):
        """软删口径钉死：只读计数走 with_entities(func.count) 时 ORM
        钩子包不住子查询——查询本身必须显式排除软删行。"""
        from sqlalchemy import func
        from backend.emby_server.soft_delete import include_deleted
        s = self._series(db, "已软删的剧", file_fingerprint="fp-del",
                         deleted_at=datetime.now())
        with include_deleted():
            sid = s.id  # 软删行实例的过期刷新会被钩子挡，先在放行区取 id
        cutoff = datetime.now() + timedelta(days=1)
        q = ew._none_reschedulable_query(
            db, cutoff, 6,
            longtail_cutoff=datetime.now() - timedelta(days=30))
        n = q.with_entities(func.count(em.MediaItem.id)).scalar()
        assert n == 0
        assert sid not in {r.id for r in q.all()}

    def test_soft_deleted_episode_fingerprint_does_not_count(self, db):
        s = self._series(db, "子集已软删的剧", file_fingerprint=None)
        _make_item(db, name="第1集", item_type="episode", series_id=s.id,
                   file_fingerprint="fp-ep-del", enrich_status="done",
                   deleted_at=datetime.now())
        assert s.id not in self._query_ids(db)

    def test_movie_still_needs_own_fingerprint(self, db):
        m = _make_item(db, name="无指纹电影", item_type="movie",
                       metadata_source="none", tmdb_id=None,
                       poster_path=None, primary_image_url=None,
                       enrich_status="done", enrich_none_reschedules=0,
                       enrich_next_retry_at=None, file_fingerprint=None)
        assert m.id not in self._query_ids(db)


# ============ 3. 年份回退：带年全灭 → 去年份重搜 ============

@pytest.fixture(autouse=True)
def _one_lang(monkeypatch):
    monkeypatch.setattr(
        tmdb_mod, "language_fallback_chain", lambda db=None: ["zh-CN"])


def _raw_bainiao(self, query, year, kind, lang=None):
    if year is not None:
        # 带年搜有结果，但全是置信闸会拒的（标题无关 + 年份也对不上，
        # Tier 1.2 的年份硬相等同样不满足）——旧口径到此为止判无命中。
        return [{"id": 999001, "title": "凤凰涅槃",
                 "original_title": "凤凰涅槃",
                 "release_date": "2011-01-01",
                 "original_language": "zh", "popularity": 3}]
    return [{"id": 308924, "title": "百鸟朝凤",
             "original_title": "百鸟朝凤",
             "release_date": "2016-05-06",
             "original_language": "zh", "popularity": 40}]


class TestYearFallbackAfterGateReject:
    def test_gate_rejected_with_year_falls_back_to_no_year(self, monkeypatch):
        """《百鸟朝凤》样本：存年 2013、TMDB 2016——带年经闸全灭后
        去年份重搜应命中，且年份降级不否决 Tier 2 精确命中。"""
        monkeypatch.setattr(TmdbClient, "_search_raw", _raw_bainiao)
        hit = TmdbClient().search("百鸟朝凤", 2013, "movie")
        assert hit is not None
        assert str(hit["id"]) == "308924"

    def test_no_year_fallback_still_gated(self, monkeypatch):
        """去年份也不是无条件直链：去年份结果同样过闸，无关标题照拒。"""
        def _raw_garbage(self, query, year, kind, lang=None):
            return [{"id": 999002, "title": "凤凰涅槃",
                     "original_title": "凤凰涅槃",
                     "release_date": "2011-01-01",
                     "original_language": "zh", "popularity": 3}]
        monkeypatch.setattr(TmdbClient, "_search_raw", _raw_garbage)
        assert TmdbClient().search("百鸟朝凤", 2013, "movie") is None


# ============ 4. 存量重认通道：干跑计数 + 分批置 pending ============

class TestRequeueMissingMetadataTop:
    def _seed(self, db):
        rows = {}
        rows["no_tmdb"] = _make_item(
            db, name="缺身份", item_type="movie", tmdb_id=None,
            poster_path="/p.jpg", overview="有简介")
        rows["no_overview"] = _make_item(
            db, name="缺简介", item_type="series", tmdb_id="111",
            poster_path="/p.jpg", overview=None)
        rows["no_poster"] = _make_item(
            db, name="缺海报", item_type="movie", tmdb_id="222",
            poster_path=None, primary_image_url=None, overview="有简介")
        rows["full"] = _make_item(
            db, name="齐全", item_type="movie", tmdb_id="333",
            poster_path="/p.jpg", overview="有简介")
        rows["pending"] = _make_item(
            db, name="未终态", item_type="movie", tmdb_id=None,
            enrich_status="pending")
        rows["deleted"] = _make_item(
            db, name="已软删", item_type="movie", tmdb_id=None,
            deleted_at=datetime.now())
        rows["merged"] = _make_item(
            db, name="已合并", item_type="movie", tmdb_id=None,
            merged_into_id=999)
        rows["episode"] = _make_item(
            db, name="单集不算顶层", item_type="episode", tmdb_id=None)
        return rows

    def test_dry_run_counts_visible_caliber_and_writes_nothing(self, db):
        rows = self._seed(db)
        # 软删行实例的过期刷新会被钩子挡，id 与校验都走放行区
        from backend.emby_server.soft_delete import include_deleted
        with include_deleted():
            ids = {k: r.id for k, r in rows.items()}
        out = ew.requeue_missing_metadata_top(db, dry_run=True)
        assert out["matched"] == 3
        assert out["requeued"] == 0
        assert out["by_layer"]["no_tmdb"] == 1
        assert out["by_layer"]["no_poster"] == 1
        assert out["by_layer"]["no_overview"] == 1
        # 干跑零写入：逐行读回状态不变（软删行要绕钩子才看得见）
        with include_deleted():
            for key, rid in ids.items():
                fresh = db.query(em.MediaItem).filter(
                    em.MediaItem.id == rid).one()
                assert fresh.enrich_status == (
                    "pending" if key == "pending" else "done")

    def test_execute_flips_limited_batch_with_priority(self, db):
        rows = self._seed(db)
        out = ew.requeue_missing_metadata_top(db, limit=2, dry_run=False)
        assert out["matched"] == 3
        assert out["requeued"] == 2
        db.expire_all()
        flipped = [r for r in (rows["no_tmdb"], rows["no_overview"],
                               rows["no_poster"])
                   if db.query(em.MediaItem).filter(
                       em.MediaItem.id == r.id).one().enrich_status
                   == "pending"]
        assert len(flipped) == 2
        for r in flipped:
            fresh = db.query(em.MediaItem).filter(
                em.MediaItem.id == r.id).one()
            assert fresh.enrich_priority == ew.ENRICH_PRIORITY_RETRY_UNMATCHED
            assert fresh.enrich_attempts == 0
        # 第二批把剩下的 1 条也翻掉
        out2 = ew.requeue_missing_metadata_top(db, limit=10, dry_run=False)
        assert out2["requeued"] == 1

    def test_count_helper_matches(self, db):
        self._seed(db)
        out = ew.count_missing_metadata_top(db)
        assert out["matched"] == 3
