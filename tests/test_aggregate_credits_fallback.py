"""补资料段剧集演员 aggregate_credits 回落（2026-11-25 第二轮诊断定案）。

背景：916 条残余重跑后 593 条 done 仅 7 条有演员。根因不是管线丢
详情——TMDB plain 详情的 credits.cast 对这批条目本身就是空的
（586/593）。TMDB 对剧集另有 /tv/{id}/aggregate_credits（全剧演员
汇总）：样本《X战警97》plain cast=0、aggregate cast=93；《Re：从零》
plain 6、aggregate 187。Jellyfin 的 TMDB 提供者对剧集正是优先用
AggregateCredits。本文件钉死回落机制：

① 剧集 plain cast 空 + aggregate 有 cast → 按 aggregate 建行，
   行形状与 cast_list 单一口径一致（roles 首个角色名、无 roles
   回落顶层 character、person_id / 头像 / 顺序同口径）；
② aggregate 也空 / 404 → 不建行、不报错、补资料其余行为不变；
③ 电影 plain cast 空 → 绝不调 aggregate（电影无此端点）；
④ 已有人物行的剧集 → 不触发回落（只填空不覆盖的红线）；
⑤ plain cast 非空 → 走 plain 路径，aggregate 零调用；
⑥ aggregate 瞬态失败 → 不抛错、不建行、不落任何「无演员」结论；
⑦ 生产分发：_enrich_apply_two_stage 须把 tmdb_client
   .aggregate_credits 注入补资料段（与 details 同款分发）；
⑧ TmdbClient.aggregate_credits：只打 /tv/{id}/aggregate_credits、
   L1 命中不重复发请求、404 落阴性标记、瞬态照旧抛
   TmdbTransientError（不吞成空结果）。

隔离内存 SQLite，不连网络（TMDB 全部替身 / mock）。
"""
import uuid
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import enrich_worker
from backend.emby_server import identify_two_stage as ts
from backend.emby_server import models as em
from backend.emby_server import tmdb as tmdb_mod
from backend.emby_server.tmdb import TmdbClient, aggregate_cast_list


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


def _make_item(db, name="某剧", **kw):
    """文字全齐的已绑条目（补资料段无字段可填、filled 应为 []）。"""
    lib = em.Library(guid=uuid.uuid4().hex, name="剧集库",
                     collection_type="tvshows")
    db.add(lib)
    db.flush()
    kw.setdefault("enrich_status", "pending")
    kw.setdefault("overview", "已有简介")
    kw.setdefault("community_rating", 8.4)
    kw.setdefault("genres", "剧情")
    kw.setdefault("poster_path", "/mnt/media/poster.jpg")
    kw.setdefault("backdrop_path", "/mnt/media/backdrop.jpg")
    item = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id,
        item_type=kw.pop("item_type", "series"),
        name=name, sort_name=name, file_path=f"/m/{uuid.uuid4().hex}.mkv",
        production_year=2021, **kw)
    db.add(item)
    db.flush()
    return item


def _people_rows(db, item_id):
    return (db.query(em.EmbyPerson)
            .filter(em.EmbyPerson.item_id == item_id)
            .order_by(em.EmbyPerson.sort_order).all())


#: plain 详情：credits 在、cast 为空——第二轮诊断里 586 条的实况。
PLAIN_DETAILS_EMPTY_CAST = {
    "id": 60300,
    "name": "某剧",
    "original_language": "ja",
    "overview": "已有简介",
    "vote_average": 8.4,
    "poster_path": "/p.jpg",
    "backdrop_path": "/b.jpg",
    "credits": {"cast": [], "crew": []},
}

#: aggregate_credits 载荷：roles 数组形态（一个演员可多个角色，
#: 取第一个）；第三条 roles 为空、靠顶层 character 回落。
AGGREGATE_PAYLOAD = {
    "id": 60300,
    "cast": [
        {"id": 111, "name": "演员甲", "profile_path": "/a.jpg", "order": 0,
         "roles": [{"character": "角色甲", "episode_count": 12},
                   {"character": "角色甲二", "episode_count": 1}],
         "total_episode_count": 13},
        {"id": 222, "name": "演员乙", "profile_path": None, "order": 1,
         "roles": [{"character": "角色乙", "episode_count": 8}],
         "total_episode_count": 8},
        {"id": 333, "name": "演员丙", "profile_path": "/c.jpg", "order": 2,
         "roles": [], "character": "角色丙", "total_episode_count": 0},
    ],
    "crew": [],
}


def _fetched_empty_cast():
    return {"tmdb_id": "60300",
            "tmdb_details": dict(PLAIN_DETAILS_EMPTY_CAST)}


class _CountingDetailsFetcher:
    """详情取数器替身（(tmdb_id, kind) 两参，与 tmdb_client.details 同签名）。"""

    def __init__(self, payload=None, exc=None):
        self.calls = []
        self.payload = payload
        self.exc = exc

    def __call__(self, tmdb_id, kind):
        self.calls.append((tmdb_id, kind))
        if self.exc is not None:
            raise self.exc
        return self.payload


class _CountingAggregateFetcher:
    """aggregate 取数器替身（(tmdb_id) 一参，与 aggregate_credits 同签名）。"""

    def __init__(self, payload=None, exc=None):
        self.calls = []
        self.payload = payload
        self.exc = exc

    def __call__(self, tmdb_id):
        self.calls.append(tmdb_id)
        if self.exc is not None:
            raise self.exc
        return self.payload


# ---------- ① plain cast 空 → aggregate 回落建行 ----------

def test_series_empty_plain_cast_falls_back_to_aggregate(db):
    item = _make_item(db, tmdb_id="60300")
    details_fetcher = _CountingDetailsFetcher(payload=None)
    agg_fetcher = _CountingAggregateFetcher(payload=AGGREGATE_PAYLOAD)
    filled = ts.apply_fill(
        db, item, _fetched_empty_cast(),
        tmdb_details_fetcher=details_fetcher,
        tmdb_aggregate_fetcher=agg_fetcher)
    db.flush()
    # fetched 已带 credits（plain 详情 IO 阶段已拉到）→ 详情取数器不许再调；
    # aggregate 恰好调 1 次、只传 tmdb_id。
    assert details_fetcher.calls == []
    assert agg_fetcher.calls == ["60300"]
    rows = _people_rows(db, item.id)
    expected = aggregate_cast_list(AGGREGATE_PAYLOAD, original_language="ja")
    assert len(rows) == len(expected) == 3
    for row, exp in zip(rows, expected):
        assert row.name == exp["name"]
        assert row.role == exp["role"]
        assert row.image == exp["image"]
        assert row.person_tmdb_id == exp["person_id"]
        assert row.sort_order == exp["sort_order"]
    # 映射口径逐项钉死：roles 取第一个角色名；roles 空回落顶层 character。
    assert rows[0].name == "演员甲" and rows[0].role == "角色甲"
    assert rows[0].person_tmdb_id == "111"
    assert rows[0].image.endswith("/w185/a.jpg")
    assert rows[1].image == ""
    assert rows[2].role == "角色丙"
    # 文字字段一个字都不许动。
    assert filled == []
    assert item.overview == "已有简介"


# ---------- ② aggregate 也空 / 404 → 静默不建行 ----------

def test_series_aggregate_empty_or_none_creates_no_rows(db):
    item = _make_item(db, tmdb_id="60300")
    agg_empty = _CountingAggregateFetcher(payload={"id": 60300, "cast": []})
    filled = ts.apply_fill(
        db, item, _fetched_empty_cast(), tmdb_aggregate_fetcher=agg_empty)
    db.flush()
    assert agg_empty.calls == ["60300"]
    assert _people_rows(db, item.id) == []
    assert filled == []
    # 404（取数器返回 None）同口径。
    item2 = _make_item(db, tmdb_id="60301")
    fetched2 = {"tmdb_id": "60301",
                "tmdb_details": dict(PLAIN_DETAILS_EMPTY_CAST, id=60301)}
    agg_none = _CountingAggregateFetcher(payload=None)
    ts.apply_fill(db, item2, fetched2, tmdb_aggregate_fetcher=agg_none)
    db.flush()
    assert agg_none.calls == ["60301"]
    assert _people_rows(db, item2.id) == []


# ---------- ③ 电影绝不调 aggregate ----------

def test_movie_empty_plain_cast_never_calls_aggregate(db):
    item = _make_item(db, item_type="movie", tmdb_id="60300")
    agg_fetcher = _CountingAggregateFetcher(payload=AGGREGATE_PAYLOAD)
    ts.apply_fill(
        db, item, _fetched_empty_cast(), tmdb_aggregate_fetcher=agg_fetcher)
    db.flush()
    assert agg_fetcher.calls == []
    assert _people_rows(db, item.id) == []


# ---------- ④ 已有人物行 → 不触发回落 ----------

def test_series_with_existing_people_rows_never_calls_aggregate(db, monkeypatch):
    from backend.emby_server.metasources import people_scrape as _ps
    monkeypatch.setattr(_ps, "PEOPLE_SCRAPE_ENABLED", 0)
    item = _make_item(db, tmdb_id="60300")
    db.add(em.EmbyPerson(item_id=item.id, name="演员甲", role="角色甲",
                         image="", sort_order=0, person_tmdb_id="111"))
    db.flush()
    agg_fetcher = _CountingAggregateFetcher(payload=AGGREGATE_PAYLOAD)
    ts.apply_fill(
        db, item, _fetched_empty_cast(), tmdb_aggregate_fetcher=agg_fetcher)
    db.flush()
    assert agg_fetcher.calls == []
    rows = _people_rows(db, item.id)
    assert len(rows) == 1 and rows[0].role == "角色甲"


# ---------- ⑤ plain cast 非空 → 走 plain、aggregate 零调用 ----------

def test_series_plain_cast_present_never_calls_aggregate(db):
    from backend.emby_server.tmdb import cast_list
    details = dict(PLAIN_DETAILS_EMPTY_CAST)
    details["credits"] = {"cast": [
        {"id": 111, "name": "演员甲", "character": "角色甲",
         "profile_path": "/a.jpg", "order": 0},
        {"id": 222, "name": "演员乙", "character": "角色乙",
         "profile_path": None, "order": 1},
    ]}
    item = _make_item(db, tmdb_id="60300")
    agg_fetcher = _CountingAggregateFetcher(payload=AGGREGATE_PAYLOAD)
    ts.apply_fill(
        db, item, {"tmdb_id": "60300", "tmdb_details": details},
        tmdb_aggregate_fetcher=agg_fetcher)
    db.flush()
    assert agg_fetcher.calls == []
    rows = _people_rows(db, item.id)
    assert len(rows) == len(cast_list(details)) == 2
    assert rows[0].name == "演员甲"


# ---------- ⑥ aggregate 瞬态失败 → 不抛错、不建行 ----------

def test_aggregate_transient_error_no_rows_no_raise(db):
    item = _make_item(db, tmdb_id="60300")
    agg_fetcher = _CountingAggregateFetcher(
        exc=tmdb_mod.TmdbTransientError("TMDB 429 全 key 限流"))
    filled = ts.apply_fill(
        db, item, _fetched_empty_cast(), tmdb_aggregate_fetcher=agg_fetcher)
    db.flush()
    assert agg_fetcher.calls == ["60300"]
    assert _people_rows(db, item.id) == []
    assert filled == []


# ---------- ⑦ 生产分发：wrapper 经 tmdb_client 注入 aggregate ----------

def test_wrapper_fill_uses_tmdb_client_aggregate_credits(db):
    item = _make_item(db, tmdb_id="60300")
    with mock.patch("backend.emby_server.tmdb.tmdb_client") as fake_client:
        fake_client.details.return_value = dict(PLAIN_DETAILS_EMPTY_CAST)
        fake_client.aggregate_credits.return_value = AGGREGATE_PAYLOAD
        enrich_worker._enrich_apply_two_stage(
            db, item, _fetched_empty_cast(), identify=False, fill=True)
    db.flush()
    assert fake_client.aggregate_credits.call_count == 1
    assert fake_client.aggregate_credits.call_args[0] == ("60300",)
    assert len(_people_rows(db, item.id)) == 3
    assert item.enrich_status == "done"


# ---------- ⑧ TmdbClient.aggregate_credits 口径 ----------

@pytest.fixture(autouse=True)
def _clean_tmdb_cache():
    TmdbClient._cache.clear()
    yield
    TmdbClient._cache.clear()


def _client(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEYS", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    return TmdbClient()


def _patch_disk_cache(monkeypatch):
    """磁盘缓存替身：恒未命中，落盘只记账（不碰真实 /data/tmdb-cache）。"""
    saved = {"details": [], "negative": []}
    monkeypatch.setattr(tmdb_mod.tmdb_cache, "load_details",
                        lambda *a, **k: (False, None))
    monkeypatch.setattr(
        tmdb_mod.tmdb_cache, "save_details",
        lambda endpoint, tid, data, lang="", raw=None:
            saved["details"].append((endpoint, tid)) or True)
    monkeypatch.setattr(
        tmdb_mod.tmdb_cache, "save_details_negative",
        lambda endpoint, tid, lang="":
            saved["negative"].append((endpoint, tid)) or True)
    return saved


def test_client_aggregate_credits_path_and_l1_cache(monkeypatch):
    client = _client(monkeypatch)
    saved = _patch_disk_cache(monkeypatch)
    calls = []

    def _fake_get(path, params):
        calls.append(path)
        return dict(AGGREGATE_PAYLOAD), 200

    monkeypatch.setattr(client, "_get_with_status", _fake_get)
    data = client.aggregate_credits("60300")
    assert data["id"] == 60300 and len(data["cast"]) == 3
    assert calls == ["/tv/60300/aggregate_credits"]
    assert saved["details"] == [("tv_aggregate_credits", "60300")]
    assert saved["negative"] == []
    # 第二次同 id：L1 命中，不再发请求。
    data2 = client.aggregate_credits("60300")
    assert data2["id"] == 60300
    assert calls == ["/tv/60300/aggregate_credits"]


def test_client_aggregate_credits_404_marks_negative(monkeypatch):
    client = _client(monkeypatch)
    saved = _patch_disk_cache(monkeypatch)
    monkeypatch.setattr(client, "_get_with_status",
                        lambda path, params: (None, 404))
    assert client.aggregate_credits("99999") is None
    assert saved["negative"] == [("tv_aggregate_credits", "99999")]
    assert saved["details"] == []


def test_client_aggregate_credits_transient_raises(monkeypatch):
    client = _client(monkeypatch)
    _patch_disk_cache(monkeypatch)

    def _boom(path, params):
        raise tmdb_mod.TmdbTransientError("TMDB 服务端错误 HTTP 503")

    monkeypatch.setattr(client, "_get_with_status", _boom)
    with pytest.raises(tmdb_mod.TmdbTransientError):
        client.aggregate_credits("60300")
