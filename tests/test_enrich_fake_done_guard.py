"""假完成堵漏回归（2026-11-23）：没真刮过不许标 done。

生产实测 757 条 series/movie：enrich_status='done' 且 tmdb_id / imdb_id /
external_ids 全空、enrich_attempts 全部 = 0、enrich_last_error 全空——
从没被真正刮过就被标了 done。产生路径有二，本文件逐条钉住：

1. ``_enrich_apply`` 尾部 has_core 口径：TMDB 搜索分支未进入
   （tmdb_client.configured=False——钥匙后填只刷新 API 进程、工人
   进程从不 refresh_keys）时，标题+年份齐即判 done，写 source='none'
   并清零 attempts，与「真搜过无命中」终态同形。
   → 守卫：无身份 + 没真搜过 → 打回 pending 退避重试，超限转
   failed，绝不 done。来源标签不作豁免（2026-11-23 跟进：生产
   23 条 done + source='nfo'/'multisource' + 无身份 + attempts=0
   正是旧条件「只拦 source 为空/none」的旁路；豆瓣来源同理，
   有豆瓣号落 external_ids 即算身份、无号即拦）。
2. ``TmdbClient._search_raw`` 把全部 key 401 / 未配置（status 401/0）
   当合法空结果返回 []，search() 随之「确认无命中」→ none 终态。
   → 改判 TmdbTransientError（没搜成 ≠ 搜过没有，与 search() docstring
   的既定原则一致）；_get 层 401→None 的旧口径不变（见
   test_tmdb_transient.py），404 的 L1 短缓存空结果不变。

同时钉住 attempts 证据纪律：真搜过无命中的终态保留 attempts>=1，
与假完成（attempts=0 的 done+无身份）从此可一条 SQL 区分。
"""
import os
import tempfile

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _p = tempfile.mkstemp(suffix=".db")
os.close(_fd)

import uuid  # noqa: E402
from unittest import mock  # noqa: E402

import pytest  # noqa: E402

from backend import database as _dbmod  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import enrich_worker  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server import tmdb as tmdb_mod  # noqa: E402
from backend.emby_server.tmdb import TmdbClient, TmdbTransientError  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （与 test_enrich_completion_bar 同款，防全局 engine 污染后续文件）。"""
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
def db():
    s = SessionLocal()
    try:
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "series"),
        name=kw.pop("name", "片名"),
        file_path=kw.pop("file_path", ""),
        enrich_status="enriching",
        **kw,
    )
    db.add(it)
    db.commit()
    return it


BASE_OK = {"ok": True, "poster": None, "fanart": None, "external_subs": [],
           "nfo_data": None, "tmdb_hit": None, "tmdb_details": None,
           "error": None}


def _apply(db, it, fetched, configured):
    with mock.patch("backend.emby_server.tmdb.tmdb_client") as tc:
        tc.configured = configured
        enrich_worker._enrich_apply(db, it, fetched)
    db.commit()
    db.refresh(it)
    return it


# ---------- _enrich_apply 守卫 ----------

def test_unsearched_no_identity_series_blocked_from_done(db):
    """核心复现：TMDB 未配置（工人进程没钥匙）+ 标题年份齐 + 无身份。
    形态取生产的第二轮：首轮被字段级口径送进低优先级补齐轮
    （enrich_field_refill_done=True），补齐轮里 fc 块「直接终态化」
    不再入队、判定链旧口径直接 done——757 条就是这么落的终态。
    修复后守卫必须在这一轮把它打回 pending。"""
    it = _item(db, name="飞出个未来", production_year=1999,
               enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "pending", (
        "没真搜过的无身份条目被标 done，就是假完成")
    assert it.enrich_attempts == 1
    assert it.enrich_next_retry_at is not None
    assert "未执行" in (it.enrich_last_error or "")


def test_unsearched_no_identity_movie_with_poster_blocked(db):
    """有海报（has_core 靠标题+海报过线）同样不许 done——757 条里
    317 条有海报，正是靠这条口径混过终态的。"""
    it = _item(db, item_type="movie", name="王牌特工：特工学院",
               production_year=2014, poster_path="/img/p.jpg",
               enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "pending"
    assert it.enrich_attempts == 1


def test_searched_no_hit_terminal_keeps_attempts_evidence(db):
    """真搜过无命中仍是 done+none 终态（处方 5 不破坏），但 attempts
    必须留下证据（>=1），与假完成（attempts=0）可区分。"""
    it = _item(db, name="注定搜不到的剧", production_year=None)
    with mock.patch.object(enrich_worker, "_alias_tmdb_id",
                           return_value=None):
        _apply(db, it, dict(BASE_OK), configured=True)
    assert it.enrich_status == "done"
    assert it.metadata_source == "none"
    assert it.enrich_attempts >= 1, (
        "真终态把 attempts 清零，假完成与真终态又不可区分了")


def test_identity_present_unsearched_still_done(db):
    """已有任一身份（imdb-only）时守卫不拦：身份在手，没搜也不假。"""
    it = _item(db, item_type="movie", name="只有IMDb的片",
               production_year=2001, imdb_id="tt0123456",
               enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "done"


def test_real_source_without_identity_blocked(db):
    """跟进（2026-11-23）：来源标签不是身份——source='douban' 但
    tmdb/imdb/external_ids 全空，同样不许 done。

    旧版本用例断言「有来源即放行」，正是 23 条 nfo/multisource 假
    完成的旁路口径：apply 侧只落文字不落编号时，行上只有来源标签、
    没有身份。生产实测 done+无身份行里 douban 来源为 0 条，收紧
    不影响任何存量 honest 终态；真有豆瓣号时号会落 external_ids，
    见下一条用例。"""
    it = _item(db, item_type="movie", name="豆瓣兜底片",
               production_year=2010, poster_path="/img/p.jpg",
               metadata_source="douban", enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "pending"
    assert it.enrich_attempts == 1


def test_real_source_with_identity_not_blocked(db):
    """豆瓣号已落 external_ids 即算身份：守卫不拦，完成照旧。"""
    it = _item(db, item_type="movie", name="豆瓣认到号的片",
               production_year=2010, poster_path="/img/p.jpg",
               metadata_source="douban",
               external_ids='{"douban": "1234567"}',
               enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "done"


def test_nfo_source_without_identity_blocked(db):
    """跟进（2026-11-23）：source='nfo' 但无身份 → 打回，不许 done。
    形态取生产 10 条 nfo 假完成：NFO 落了文字（标题/年份），
    has_core 过线，但没落下任何编号。"""
    it = _item(db, item_type="movie", name="只有NFO的片",
               production_year=2019, poster_path="/img/p.jpg",
               metadata_source="nfo", enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "pending"
    assert it.enrich_attempts == 1


def test_multisource_without_identity_blocked(db):
    """跟进（2026-11-23）：source='multisource' 但无身份 → 打回。
    形态取生产 13 条 multisource 假完成。"""
    it = _item(db, item_type="series", name="只有多源文字的剧",
               production_year=2021, poster_path="/img/p.jpg",
               metadata_source="multisource",
               enrich_field_refill_done=True)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "pending"
    assert it.enrich_attempts == 1


def test_nfo_with_tmdb_id_in_file_not_blocked(db):
    """NFO 自带 tmdb_id 时 apply_nfo 落身份，守卫不拦：完成照旧。"""
    it = _item(db, item_type="movie", name="NFO带号的片",
               production_year=2019, enrich_field_refill_done=True)
    fetched = dict(BASE_OK)
    fetched["nfo_data"] = {"title": "NFO带号的片", "tmdb_id": "550",
                           "plot": "简介"}
    _apply(db, it, fetched, configured=False)
    assert it.enrich_status == "done"
    assert it.tmdb_id == "550"


def test_nfo_text_only_applied_still_blocked(db):
    """NFO 只落文字（无编号）时，即使本轮真实走了 apply_nfo，
    终态仍须拦——来源落过资料 ≠ 拿到身份。"""
    it = _item(db, item_type="movie", name="NFO纯文字的片",
               production_year=2019, enrich_field_refill_done=True)
    fetched = dict(BASE_OK)
    fetched["nfo_data"] = {"title": "NFO纯文字的片",
                           "plot": "简介", "year": 2019}
    _apply(db, it, fetched, configured=False)
    assert it.enrich_status == "pending"
    assert it.metadata_source == "nfo"
    assert it.tmdb_id is None


def test_episode_inherit_done_not_blocked(db):
    """season/episode 跟随 series 完成是设计（身份不落在自身），
    守卫只管 series/movie，不许误伤继承路径。"""
    it = _item(db, item_type="episode", name="某集", production_year=2020)
    _apply(db, it, dict(BASE_OK), configured=False)
    assert it.enrich_status == "done"


# ---------- _search_raw：401/未配置改判瞬态 ----------

@pytest.fixture(autouse=True)
def _clean_tmdb_cache(monkeypatch):
    """L1 类级缓存清零 + 关磁盘缓存 + 熔断复位（与 test_tmdb_transient
    同款卫生口径，防跨用例污染）。"""
    from backend.emby_server.metasources import breaker as provider_breaker
    monkeypatch.setenv("EMBY_TMDB_CACHE", "0")
    TmdbClient._cache.clear()
    provider_breaker.reset_all()
    yield
    TmdbClient._cache.clear()
    provider_breaker.reset_all()


class _Session:
    def __init__(self, answer):
        self.calls = []
        self._answer = answer

    def get(self, url, params):
        self.calls.append((url, dict(params)))
        return self._answer(url, params)


def _resp(status=200, payload=None):
    class _R:
        status_code = status

        def json(self):
            return {} if payload is None else payload

    _R.headers = {}
    return _R()


def _client(monkeypatch, answer, keys=("k1",)):
    monkeypatch.delenv("TMDB_API_KEYS", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb_mod, "_read_config_value", lambda db: "")
    client = TmdbClient()
    if keys:
        client._set_keys(list(keys), "env")
    client._limiter = tmdb_mod._RequestLimiter(rate=1_000_000.0, min_rate=1.0)
    client.session = _Session(answer)
    monkeypatch.setattr(client, "_ensure_session", lambda: None)
    return client


def test_search_raw_all_keys_401_raises_transient(monkeypatch):
    """全部 key 401（钥匙错误/失效）：搜索根本没发生过 → 抛瞬态，
    不许返回 [] 冒充「搜过了没有」（旧行为正是假完成产生路径）。"""
    client = _client(monkeypatch, lambda u, p: _resp(status=401))
    with pytest.raises(TmdbTransientError):
        client._search_raw("飞出个未来", 1999, "series")
    assert not TmdbClient._cache, "401 不许写 L1 空结果缓存"


def test_search_raw_unconfigured_raises_transient(monkeypatch):
    """未配置（无 key，status=0）：同样抛瞬态，不返回 []。"""
    client = _client(monkeypatch, lambda u, p: _resp(), keys=())
    assert client.configured is False
    with pytest.raises(TmdbTransientError):
        client._search_raw("飞出个未来", 1999, "series")


def test_search_raw_no_session_raises_transient(monkeypatch):
    """会话建不起来：抛瞬态（旧行为 return [] 同样冒充搜过）。"""
    client = _client(monkeypatch, lambda u, p: _resp())
    client.session = None
    with pytest.raises(TmdbTransientError):
        client._search_raw("飞出个未来", 1999, "series")


def test_search_raw_404_still_empty(monkeypatch):
    """其余非瞬态 4xx（404）旧口径不变：返回 []（不是本次改动范围）。"""
    client = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client._search_raw("不存在的片名xyz", 2022, "series") == []
