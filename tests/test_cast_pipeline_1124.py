"""演员表数据管线补齐（2026-11-24，诊断报告 metadata-client-matrix 缺口 #1/#3）。

背景：emby_people 全库仅覆盖 142 个条目。写演员的只有 enrich 单条路径
（_enrich_apply → _apply_cast 吃 TMDB details 的 credits）。其余路径：

1. 扫描批量入库（scanner）：预取已拿到带 credits 的 details，写库段只调
   apply/apply_details/apply_images（只写条目字段、无 db 句柄），credits
   被整段丢弃；隐式 series 落库（_apply_implicit_series_tmdb）同病。
2. 多源引擎（metasources/engine）：Hit/CollectResult 结构里没有 cast 载体，
   apply_to_item 无 db 参数，结构上写不了演员；Bangumi 搜索不返回演职员，
   但 v0 characters 接口有——归并确认 bangumi 编号后可拉一次补上。
3. enrich 写库的存量详情分支缺失：已绑 tmdb_id、搜索未命中、详情靠
   existing_id 兜底拿到时，三个 apply 分支一个都不进，apply_details
   从不执行 → 简介/类型/评分永远补不上、metadata_source='none' 不纠正
   （cast 靠 3.5 段无条件 _apply_cast 幸免）。终态 none 后绑上编号的
   3,300 部剧正是这条路。
4. Step-5 cast 补队只在 _enrich_apply 瞬间触发、从不巡检 done 存量，
   且 _searched_no_hit 轮次整段跳过——后绑编号的条目永远进不了补队。
   定向补拉入口 enqueue_details_refill 补上这条入队逻辑。

隔离临时 SQLite，不连网络（TMDB / Bangumi 全部 mock）。
"""
import json
import os
import tempfile
import uuid
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod
from sqlalchemy import create_engine as _ce
from sqlalchemy.orm import sessionmaker as _sm

_dbmod.DATABASE_TYPE = "sqlite"

import pytest

from backend.database import SessionLocal, init_db
from backend.emby_server import altmeta
from backend.emby_server import enrich_worker as ew
from backend.emby_server import models as em
from backend.emby_server import scanner
from backend.emby_server import tmdb as tmdb_mod
from backend.emby_server.metasources import engine as ms_engine
from backend.emby_server.metasources import sources as ms_sources
from backend.emby_server.metasources.engine import CollectResult
from backend.emby_server.metasources import breaker as provider_breaker
from backend.integrations import store


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
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


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        s.query(em.MediaStream).delete()
        s.query(em.EmbyPerson).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _breakers():
    provider_breaker.reset_all()
    yield
    provider_breaker.reset_all()


def _make_item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "series"),
        name=kw.pop("name", "测试"),
        file_path=kw.pop("file_path", "/tmp/x.mp4"),
        enrich_status=kw.pop("enrich_status", "pending"),
        **kw,
    )
    db.add(it)
    db.commit()
    return it


def _people(db, item):
    return (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.item_id == item.id)
            .order_by(em.EmbyPerson.sort_order).all())


DETAILS = {
    "id": 4242,
    "name": "补拉剧",
    "original_name": "Refill Show",
    "first_air_date": "2021-05-01",
    "overview": "这是补拉回来的简介。",
    "genres": [{"id": 18, "name": "剧情"}, {"id": 35, "name": "喜剧"}],
    "vote_average": 8.4,
    "external_ids": {"imdb_id": "tt4242"},
    "origin_country": ["CN"],
    "spoken_languages": [{"english_name": "Mandarin"}],
    "credits": {"cast": [
        {"name": "演员甲", "character": "角色甲",
         "profile_path": "/a.jpg", "id": 101},
        {"name": "演员乙", "character": "角色乙",
         "profile_path": None, "id": 102},
    ]},
}

CAST_ROWS = [
    {"name": "演员甲", "role": "角色甲", "image": "http://img/a.jpg",
     "sort_order": 0, "person_id": ""},
    {"name": "演员乙", "role": "角色乙", "image": "",
     "sort_order": 1, "person_id": ""},
]


def _set_step5_flag(value: str):
    ew._enrich_tuning_cache["at"] = 0.0
    ew._enrich_tuning_cache["cfg"] = None
    db = SessionLocal()
    try:
        store.write_values(db, {ew.CFG_ENRICH_FIELD_COMPLETENESS: value},
                           {ew.CFG_ENRICH_FIELD_COMPLETENESS: "测试开关"})
        db.commit()
    finally:
        db.close()
    store.invalidate(ew.CFG_ENRICH_FIELD_COMPLETENESS)
    ew._enrich_tuning_cache["at"] = 0.0
    ew._enrich_tuning_cache["cfg"] = None


# ---------------------------------------------------------------------------
# 3. enrich 写库：existing_id 兜底详情必须落文本字段 + 纠正 source
# ---------------------------------------------------------------------------

def test_enrich_apply_existing_id_details_fill_text(db):
    """已绑 tmdb_id、搜索未命中、详情靠 existing_id 兜底：简介/类型/评分
    必须由 apply_details 只填空落库，source='none' 纠正为 'tmdb'。"""
    _set_step5_flag("0")
    it = _make_item(
        db, name="补拉剧", tmdb_id="4242", metadata_source="none",
        overview=None, genres=None, community_rating=None,
        poster_path="/tmp/p.jpg", imdb_id="tt4242",
        production_year=2021, enrich_status="enriching")
    real = tmdb_mod.tmdb_client
    fake = SimpleNamespace(
        configured=True, apply_details=real.apply_details,
        apply_images=real.apply_images, apply=real.apply)
    fetched = {"ok": True, "poster": None, "fanart": None,
               "external_subs": [], "nfo_data": None, "tmdb_hit": None,
               "tmdb_details": dict(DETAILS), "error": None}
    with mock.patch.object(tmdb_mod, "tmdb_client", fake):
        ew._enrich_apply(db, it, fetched)
    db.commit()
    db.refresh(it)
    assert it.overview == "这是补拉回来的简介。"
    assert it.genres == "剧情,喜剧"
    assert it.community_rating == 8.4
    assert it.metadata_source == "tmdb", "已拿到 TMDB 详情，'none' 标记必须纠正"
    assert it.enrich_status == "done"
    assert [p.name for p in _people(db, it)] == ["演员甲", "演员乙"]


# ---------------------------------------------------------------------------
# 1. 扫描批量入库：守卫 helper + 隐式 series 落库写演员
# ---------------------------------------------------------------------------

def test_scan_cast_guard_writes_only_matching_id(db):
    """details 的 id 与条目最终 tmdb_id 不一致（复核拒绝的命中）绝不写演员。"""
    it = _make_item(db, item_type="movie", name="守卫片", tmdb_id="4242")
    scanner._apply_scan_cast(db, it, dict(DETAILS))
    db.commit()
    assert [p.name for p in _people(db, it)] == ["演员甲", "演员乙"]

    other = _make_item(db, item_type="movie", name="错位片", tmdb_id="9999")
    scanner._apply_scan_cast(db, other, dict(DETAILS))
    db.commit()
    assert _people(db, other) == [], "id 对不上（被拒命中）的详情不许落演员"

    scanner._apply_scan_cast(db, other, None)  # 无详情：静默 no-op


def test_scanner_implicit_series_writes_cast(db):
    """集扫描隐式建剧、TMDB 命中落库时，details 的 credits 必须写 emby_people。"""
    series = _make_item(db, item_type="series", name="隐式剧",
                        production_year=2021)
    hit = {"id": 4242, "name": "补拉剧", "overview": "hit 简介",
           "vote_average": 8.0, "first_air_date": "2021-05-01",
           "poster_path": "/x.jpg", "genre_ids": []}
    ctx = SimpleNamespace(
        series_tmdb_results={"g1": (hit, dict(DETAILS))})
    stats = {"scraped": 0}
    applied, rejected = scanner._apply_implicit_series_tmdb(
        ctx, series, "g1", stats, db=db)
    db.commit()
    assert applied and not rejected
    assert [p.name for p in _people(db, series)] == ["演员甲", "演员乙"]


# ---------------------------------------------------------------------------
# 2. 多源引擎：cast 载体 + Bangumi 补拉 + apply 落库
# ---------------------------------------------------------------------------

def test_collect_result_cast_roundtrip():
    r = CollectResult()
    r.cast = list(CAST_ROWS)
    r2 = CollectResult.from_dict(r.as_dict())
    assert r2.cast == CAST_ROWS


def test_engine_bangumi_cast_attach_and_apply(db):
    result = CollectResult()
    result.external_ids = {"bangumi": "385209"}
    with mock.patch.object(ms_sources, "fetch_bangumi_cast",
                           return_value=list(CAST_ROWS)) as fake:
        ms_engine._attach_bangumi_cast(result, "series")
    assert fake.call_count == 1
    assert result.cast == CAST_ROWS

    plain = CollectResult()
    with mock.patch.object(ms_sources, "fetch_bangumi_cast",
                           return_value=list(CAST_ROWS)) as fake2:
        ms_engine._attach_bangumi_cast(plain, "series")
    assert fake2.call_count == 0, "无 bangumi 编号不许发 characters 请求"
    assert plain.cast == []

    it = _make_item(db, item_type="series", name="爆焰")
    ms_engine.apply_to_item(it, result, db=db)
    db.commit()
    assert [p.name for p in _people(db, it)] == ["演员甲", "演员乙"]
    # 无 db 的旧调用口径：不写演员、不报错
    it2 = _make_item(db, item_type="series", name="爆焰2")
    ms_engine.apply_to_item(it2, result)
    db.commit()
    assert _people(db, it2) == []


def test_bridge_cast_carried_into_multisource_payload():
    """多源桥复核已拉到的 details（带 credits）过门后，cast 必须随
    multisource 载荷带到写库阶段（零新增请求）；被门拒绝时不许带。"""
    from backend.emby_server.metasources import config as ms_config

    details = {"id": 999, "title": "测试电影", "release_date": "2020-01-01",
               "credits": {"cast": [
                   {"name": "演员甲", "character": "角色甲",
                    "profile_path": "/a.jpg", "id": 101}]}}

    class _FakeTmdb:
        configured = True

        def details(self, tmdb_id, kind):
            return dict(details)

    class _DummySession:
        def close(self):
            pass

    item = SimpleNamespace(
        id=1, name="测试电影", item_type="movie", library_id=66,
        production_year=None, tmdb_id=None, imdb_id=None,
        enrich_status="pending")
    collected = CollectResult()
    collected.external_ids = {"tmdb": "999"}
    result: dict = {}
    with mock.patch.object(ew, "SessionLocal", lambda: _DummySession()), \
            mock.patch.object(ms_config, "read_config",
                              return_value=SimpleNamespace(enabled=True)), \
            mock.patch.object(ms_engine, "collect",
                              return_value=collected), \
            mock.patch.object(ew, "_douban_ban_downgrade_active",
                              return_value=False), \
            mock.patch.object(tmdb_mod, "tmdb_client", _FakeTmdb()):
        ok = ew._collect_multisource(item, "movie", result)
    assert ok is True
    cast = (result.get("multisource") or {}).get("cast") or []
    assert [c["name"] for c in cast] == ["演员甲"]


# ---------------------------------------------------------------------------
# Bangumi characters 映射
# ---------------------------------------------------------------------------

def _fake_urlopen(payload_or_exc):
    class _R:
        def read(self):
            return json.dumps(payload_or_exc).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _R()

    return _open


def test_bangumi_characters_actor_mapping():
    payload = [
        {"id": 1, "name": "角色A", "relation": "主角",
         "images": {"medium": "http://img/ca.jpg"},
         "actors": [{"id": 11, "name": "声优甲",
                     "images": {"medium": "http://img/pa.jpg"}}]},
        {"id": 2, "name": "角色B", "relation": "配角",
         "images": {"medium": "http://img/cb.jpg"},
         "actors": [{"id": 12, "name": "声优乙", "images": {}}]},
    ]
    with mock.patch.object(altmeta.urllib.request, "urlopen",
                           _fake_urlopen(payload)):
        rows = altmeta.fetch_bangumi_characters("385209", min_interval=0)
    assert rows[0]["name"] == "声优甲"
    assert rows[0]["role"] == "角色A"
    assert rows[0]["image"] == "http://img/pa.jpg"
    assert rows[1]["name"] == "声优乙"
    assert rows[1]["role"] == "角色B"
    assert rows[1]["image"] == ""


def test_bangumi_characters_character_fallback_and_failure():
    payload = [
        {"id": 1, "name": "角色A", "relation": "主角",
         "images": {"medium": "http://img/ca.jpg"}, "actors": []},
    ]
    with mock.patch.object(altmeta.urllib.request, "urlopen",
                           _fake_urlopen(payload)):
        rows = altmeta.fetch_bangumi_characters("1", min_interval=0)
    assert [r["name"] for r in rows] == ["角色A"], "全无演员时以角色名兜底"
    with mock.patch.object(altmeta.urllib.request, "urlopen",
                           _fake_urlopen(OSError("boom"))):
        assert altmeta.fetch_bangumi_characters("1", min_interval=0) == []


# ---------------------------------------------------------------------------
# 4/5. 定向补拉入口：人群口径 + 幂等
# ---------------------------------------------------------------------------

def test_enqueue_details_refill_population(db):
    a = _make_item(db, name="甲剧", tmdb_id="101", metadata_source="none",
                   overview=None, genres=None, enrich_status="done")
    b = _make_item(db, item_type="movie", name="乙片", tmdb_id="102",
                   overview="有简介", genres="剧情", community_rating=7.5,
                   enrich_status="done")  # 只缺演员
    c = _make_item(db, name="丙剧", tmdb_id="103", overview="有",
                   genres="剧情", community_rating=8.0,
                   enrich_status="done")
    p = em.EmbyPerson(item_id=c.id, name="现成演员", sort_order=0)
    db.add(p)
    db.commit()
    _make_item(db, name="丁剧", overview=None, enrich_status="done")  # 无编号
    _make_item(db, name="戊剧", tmdb_id="105", overview=None,
               enrich_status="done", enrich_field_refill_done=True)  # 补过
    _make_item(db, name="己剧", tmdb_id="106", overview=None,
               enrich_status="pending")  # 还在主队列
    _make_item(db, name="庚剧", tmdb_id="107", overview=None,
               enrich_status="done", metadata_locked=True)  # 锁定
    target = _make_item(db, name="辛剧正主", tmdb_id="108", overview="有",
                        genres="剧情", community_rating=9.0,
                        enrich_status="done")
    db.add(em.EmbyPerson(item_id=target.id, name="现成演员", sort_order=0))
    db.commit()
    _make_item(db, name="辛剧副本", tmdb_id="108", overview=None,
               enrich_status="done", merged_into_id=target.id)  # 合并副本
    _make_item(db, item_type="episode", name="壬集", tmdb_id="109",
               overview=None, enrich_status="done")  # 单集不在人群

    dry = ew.enqueue_details_refill(db, dry_run=True)
    assert dry["matched"] == 2, dry
    db.refresh(a)
    assert a.enrich_status == "done", "dry-run 不许改状态"

    out = ew.enqueue_details_refill(db)
    assert out["matched"] == 2 and out["enqueued"] == 2
    assert set(out["ids"]) == {a.id, b.id}
    db.commit()
    for it in (a, b):
        db.refresh(it)
        assert it.enrich_status == "pending"
        assert it.enrich_priority == ew.ENRICH_PRIORITY_FIELD_REFILL == -10
        assert it.enrich_field_refill_done is True
        assert it.enrich_attempts == 0
        assert it.enrich_next_retry_at is None

    again = ew.enqueue_details_refill(db)
    assert again["matched"] == 0, "已入队/已补过的人群不许重复入队"

# ---------------------------------------------------------------------------
# 门拒回落已存编号（2026-11-25）：搜索命中被绑定复核门拒绝后，详情须回落
# 已存编号拉取，否则 914 条 done 条目永久无演员。
#
# 缺口：_run_tmdb_batch Round 3 / _tmdb_work 都是「有命中只拉命中详情，
# 无命中才拉 existing_id 兜底」；命中被门拒后 _enrich_fetch_tmdb_post 把
# 详情整段 pop 掉、_apply_cast 拿到 None 静默跳过，条目以 done 终态。
# 修复点：门拒 pop 之后用已存编号拉一次详情（走存量详情分支）；被拒命中
# 仍丢弃、不写新绑定；门拒时记结构化日志。
# ---------------------------------------------------------------------------

STORED_DETAILS_65942 = {
    "id": 65942,
    "name": "Re：从零开始的异世界生活",
    "original_name": "Re:ゼロから始める異世界生活",
    "first_air_date": "2016-04-04",
    "overview": "菜月昴被召唤到异世界。",
    "genres": [{"id": 16, "name": "动画"}],
    "vote_average": 8.4,
    "external_ids": {"imdb_id": "tt65942"},
    "origin_country": ["JP"],
    "spoken_languages": [{"english_name": "Japanese"}],
    "credits": {"cast": [
        {"name": "演员甲", "character": "角色甲",
         "profile_path": "/a.jpg", "id": 101},
        {"name": "演员乙", "character": "角色乙",
         "profile_path": None, "id": 102},
    ]},
}

# 被拒命中：2005 年的同名旧版，年份与条目 2016 差 11 年 → year_mismatch。
HIT_DETAILS_2005 = {
    "id": 65733,
    "name": "Re:Zero 2005版",
    "original_name": "Re:Zero 2005",
    "first_air_date": "2005-04-04",
    "overview": "这是被拒命中的简介。",
    "genres": [{"id": 16, "name": "动画"}],
    "vote_average": 5.0,
    "credits": {"cast": [
        {"name": "错绑演员", "character": "错角",
         "profile_path": None, "id": 901},
    ]},
}

STORED_DETAILS_WUJIANDAO = {
    "id": 14636,
    "name": "无间道",
    "original_name": "無間道",
    "first_air_date": "2002-12-12",
    "overview": "卧底与内鬼的故事。",
    "genres": [{"id": 80, "name": "犯罪"}],
    "vote_average": 8.4,
    "external_ids": {"imdb_id": "tt14636"},
    "origin_country": ["HK"],
    "spoken_languages": [{"english_name": "Cantonese"}],
    "credits": {"cast": [
        {"name": "演员甲", "character": "角色甲",
         "profile_path": "/a.jpg", "id": 101},
        {"name": "演员乙", "character": "角色乙",
         "profile_path": None, "id": 102},
    ]},
}

# 被拒命中：年份与条目 2002 一致、标题与「无间道」零交集 → title_mismatch。
HIT_DETAILS_BHABHI = {
    "id": 14310,
    "name": "Bhabhi",
    "original_name": "Bhabhi",
    "first_air_date": "2002-01-01",
    "overview": "这是被拒命中的简介。",
    "genres": [{"id": 18, "name": "剧情"}],
    "vote_average": 5.0,
    "credits": {"cast": [
        {"name": "错绑演员", "character": "错角",
         "profile_path": None, "id": 901},
    ]},
}


def _gate_item(**kw):
    defaults = dict(
        id=1054, library_id=1, item_type="series",
        name="Re：从零开始的异世界生活",
        file_path=None, size=0, container="", production_year=2016,
        poster_path=None, primary_image_url=None, imdb_id=None, aliases=None,
        tmdb_id="65942", overview=None, genres=None, metadata_locked=False,
        metadata_source=None, enrich_status="enriching", enrich_attempts=0,
        repair_requested_at=None, season_number=None, episode_number=None,
        series_id=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _gate_post_ctx(**kw):
    ctx = {"kind": "series", "needs_repair": False,
           "tmdb_plan": {"op": "search",
                         "name": "Re：从零开始的异世界生活",
                         "year": 2016, "kind": "series",
                         "existing_id": "65942"}}
    ctx.update(kw)
    return ctx


class _StubProgress:
    def __init__(self):
        self.stages = []

    def note_stage(self, s):
        self.stages.append(s)


def _run_gate_post(result, item, tmdb_ctx, details_fn, batch_out):
    """驱动 _enrich_fetch_tmdb_post：details() 按调用方给的函数分发，
    其余网络方向全部 mock（本节用例不许真发请求）。"""
    from backend.emby_server import douban as dbn
    with (
        mock.patch.object(type(tmdb_mod.tmdb_client), "configured",
                          new_callable=mock.PropertyMock, return_value=True),
        mock.patch.object(tmdb_mod.tmdb_client, "details",
                          side_effect=details_fn),
        mock.patch.object(tmdb_mod, "poster_language", return_value="system"),
        mock.patch.object(tmdb_mod, "prewarm_images", return_value=[]),
        mock.patch.object(ew, "_douban_fallback_on_tmdb_miss",
                          return_value=False),
        mock.patch.object(ew, "_should_multisource_on_tmdb_miss",
                          return_value=False),
        mock.patch.object(ew, "_collect_multisource_guarded",
                          return_value=False),
        mock.patch.object(ew, "progress", _StubProgress()),
        mock.patch.object(altmeta, "search", return_value=None),
        mock.patch.object(dbn.client, "search", return_value=None),
    ):
        return ew._enrich_fetch_tmdb_post(result, item, tmdb_ctx,
                                          batch_out=batch_out)


def _stored_details_fn(calls):
    """details() 分发：只认已存编号，其他 id 一律报错（防误调）。"""
    def _fn(tmdb_id, kind, *a, **k):
        calls.append((str(tmdb_id), kind))
        if str(tmdb_id) == "65942":
            return dict(STORED_DETAILS_65942)
        if str(tmdb_id) == "14636":
            return dict(STORED_DETAILS_WUJIANDAO)
        raise AssertionError(f"details() 不应被调到 {tmdb_id!r}")
    return _fn


def test_gate_rejected_year_falls_back_to_stored_id_batch():
    """批量路：命中年份被门拒 → 回落已存编号拉详情，cast 与文本都有源；
    被拒命中丢弃、不写新绑定。"""
    item = _gate_item()
    result = {"tmdb_hit": None, "tmdb_details": None,
              "ok": True, "error": None, "nfo_data": None}
    batch_out = {"hit": {"id": 65733, "name": "Re:Zero 2005版"},
                 "details": dict(HIT_DETAILS_2005), "transient": False}
    calls = []
    out = _run_gate_post(result, item, _gate_post_ctx(),
                         _stored_details_fn(calls), batch_out)
    assert out.get("tmdb_gate_rejected") == "year_mismatch"
    assert "tmdb_id" not in out, "被拒命中不许留 tmdb_id（不写新绑定）"
    assert "tmdb_hit" not in out, "被拒命中不许留 tmdb_hit"
    assert calls == [("65942", "series")], calls
    assert out.get("tmdb_details_fallback_existing") is True
    details = out.get("tmdb_details") or {}
    assert details.get("id") == 65942, "回落详情必须是已存编号的，不是被拒命中的"
    assert (details.get("credits") or {}).get("cast"), "回落详情须带 credits 供 _apply_cast"


def test_gate_rejected_title_falls_back_to_stored_id_batch():
    """批量路：标题硬门被拒（年份一致、标题零交集）→ 同样回落已存编号。"""
    item = _gate_item(id=119174, name="无间道", production_year=2002,
                      tmdb_id="14636")
    result = {"tmdb_hit": None, "tmdb_details": None,
              "ok": True, "error": None, "nfo_data": None}
    batch_out = {"hit": {"id": 14310, "name": "Bhabhi"},
                 "details": dict(HIT_DETAILS_BHABHI), "transient": False}
    calls = []
    out = _run_gate_post(
        result, item,
        _gate_post_ctx(tmdb_plan={"op": "search", "name": "无间道",
                                  "year": 2002, "kind": "series",
                                  "existing_id": "14636"}),
        _stored_details_fn(calls), batch_out)
    assert out.get("tmdb_gate_rejected") == "title_mismatch"
    assert "tmdb_id" not in out
    assert "tmdb_hit" not in out
    assert calls == [("14636", "series")], calls
    assert out.get("tmdb_details_fallback_existing") is True
    assert (out.get("tmdb_details") or {}).get("id") == 14636


def test_gate_rejected_year_falls_back_to_stored_id_single():
    """单条路：batch_out=None 时走 _tmdb_work，门拒后同样回落已存编号。"""
    item = _gate_item(id=2001, name="门拒回落单条剧", production_year=2016)
    result = {"tmdb_hit": None, "tmdb_details": None,
              "ok": True, "error": None, "nfo_data": None}
    calls = []

    def _tmdb_work(need_search, name, year, kind, existing_id, want_details):
        assert need_search is True
        assert str(existing_id) == "65942"
        return ({"id": 65733, "name": "Re:Zero 2005版"},
                dict(HIT_DETAILS_2005))

    with mock.patch.object(scanner, "_tmdb_work", side_effect=_tmdb_work):
        out = _run_gate_post(result, item, _gate_post_ctx(),
                             _stored_details_fn(calls), None)
    assert out.get("tmdb_gate_rejected") == "year_mismatch"
    assert "tmdb_id" not in out
    assert "tmdb_hit" not in out
    assert calls == [("65942", "series")], calls
    assert out.get("tmdb_details_fallback_existing") is True
    assert (out.get("tmdb_details") or {}).get("id") == 65942


def test_gate_rejected_without_stored_id_no_fallback():
    """守卫：条目本身无已存编号时，门拒后不拉详情、不崩、details() 一次不调。"""
    item = _gate_item(tmdb_id=None)
    result = {"tmdb_hit": None, "tmdb_details": None,
              "ok": True, "error": None, "nfo_data": None}
    batch_out = {"hit": {"id": 65733, "name": "Re:Zero 2005版"},
                 "details": dict(HIT_DETAILS_2005), "transient": False}
    calls = []
    out = _run_gate_post(result, item, _gate_post_ctx(),
                         _stored_details_fn(calls), batch_out)
    assert out.get("tmdb_gate_rejected") == "year_mismatch"
    assert out.get("tmdb_details") is None
    assert calls == [], calls


def test_gate_fallback_transient_retries_later():
    """回落取详情遇瞬态错误 → 整条标 ok=False 待重试，不许静默标 done。"""
    item = _gate_item()
    result = {"tmdb_hit": None, "tmdb_details": None,
              "ok": True, "error": None, "nfo_data": None}
    batch_out = {"hit": {"id": 65733, "name": "Re:Zero 2005版"},
                 "details": dict(HIT_DETAILS_2005), "transient": False}

    def _boom(tmdb_id, kind, *a, **k):
        raise tmdb_mod.TmdbTransientError("boom")

    out = _run_gate_post(result, item, _gate_post_ctx(), _boom, batch_out)
    assert out.get("tmdb_gate_rejected") == "year_mismatch"
    assert out.get("ok") is False
    assert "transient" in (out.get("error") or "")
    assert out.get("tmdb_details") is None


def test_gate_fallback_details_fill_text_and_cast(db):
    """写库侧：门拒＋回落标记的详情走存量详情分支——文本落库、source 纠正、
    演员表经 3.5 段写上；tmdb_id 不变（仍是已存编号）。"""
    it = _make_item(
        db, name="Re：从零开始的异世界生活", tmdb_id="65942",
        metadata_source="none", overview=None, genres=None,
        community_rating=None, poster_path="/tmp/p.jpg", imdb_id="tt65942",
        production_year=2016, enrich_status="enriching")
    real = tmdb_mod.tmdb_client
    fake = SimpleNamespace(
        configured=True, apply_details=real.apply_details,
        apply_images=real.apply_images, apply=real.apply)
    fetched = {"ok": True, "poster": None, "fanart": None,
               "external_subs": [], "nfo_data": None, "tmdb_hit": None,
               "tmdb_details": dict(STORED_DETAILS_65942),
               "tmdb_gate_rejected": "year_mismatch",
               "tmdb_details_fallback_existing": True, "error": None}
    with mock.patch.object(tmdb_mod, "tmdb_client", fake):
        ew._enrich_apply(db, it, fetched)
    db.commit()
    db.refresh(it)
    assert it.tmdb_id == "65942", "回落不许改条目的 tmdb_id"
    assert it.overview == "菜月昴被召唤到异世界。"
    assert it.genres == "动画"
    assert it.community_rating == 8.4
    assert it.metadata_source == "tmdb", "已拿到已存编号的详情，'none' 必须纠正"
    assert [p.name for p in _people(db, it)] == ["演员甲", "演员乙"]


def test_gate_rejected_without_fallback_stays_inert(db):
    """守卫：门拒且无回落标记、无详情时，写库侧不写演员、不填文本、不改身份。"""
    it = _make_item(
        db, name="Re：从零开始的异世界生活", tmdb_id="65942",
        metadata_source="none", overview=None, genres=None,
        community_rating=None, poster_path="/tmp/p.jpg", imdb_id="tt65942",
        production_year=2016, enrich_status="enriching")
    real = tmdb_mod.tmdb_client
    fake = SimpleNamespace(
        configured=True, apply_details=real.apply_details,
        apply_images=real.apply_images, apply=real.apply)
    fetched = {"ok": True, "poster": None, "fanart": None,
               "external_subs": [], "nfo_data": None, "tmdb_hit": None,
               "tmdb_details": None,
               "tmdb_gate_rejected": "year_mismatch", "error": None}
    with mock.patch.object(tmdb_mod, "tmdb_client", fake):
        ew._enrich_apply(db, it, fetched)
    db.commit()
    db.refresh(it)
    assert it.tmdb_id == "65942"
    assert it.overview is None
    assert _people(db, it) == []
