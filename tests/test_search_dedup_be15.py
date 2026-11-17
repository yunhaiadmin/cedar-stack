"""BE-15：搜索结果同片去重（只留代表）。

审计证据：搜索分支提前 return、从不调用 dedup/代表层，同片跨库重复
（生产 531 组、553 行，全部为跨库）与合并副本可并列出现在结果里。
货架（按库浏览）天然只见本库一份，搜索是跨库面，必须在搜索面折叠。

钉住的口径（与货架代表层逐项对齐，差异只在分组不含库维度）：
- 分组身份与 dedup_key 相同：有 tmdb 用 (类型, tmdb)，无 tmdb 用
  (类型, 归一化标题, 年份)；仅去掉 library_id 一维（搜索跨库）；
- 代表选取复用 dedup.pick_primary（本地文件 > 海报 > tmdb > 简介 >
  id 最小，三期已绑定优先在同一函数内）；
- merged_into_id 非空的物理合并副本先剔除（与 deduplicate_items 一致）；
- 保序：每组占其首个成员在相关度序中的位置；
- 非 movie/series 原样透传（与 deduplicate_items 一致）；
- 不挂身份门：未绑定条目在搜索面照常出现（案 A「搜索宽」不变）；
- TotalRecordCount 报折叠后条数，与分页口径一致。
"""
import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend import models  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import api
from backend.emby_server import dedup as dedup_lib
from backend.emby_server import models as em
from backend.emby_server import search_api


def _g():
    return uuid.uuid4().hex


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("be15") / "items.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    t0 = datetime(2026, 1, 1)

    lib_a = em.Library(guid=_g(), name="电影A", collection_type="movies", is_enabled=True)
    lib_b = em.Library(guid=_g(), name="电影B", collection_type="movies", is_enabled=True)
    lib_t1 = em.Library(guid=_g(), name="剧集A", collection_type="tvshows", is_enabled=True)
    lib_t2 = em.Library(guid=_g(), name="剧集B", collection_type="tvshows", is_enabled=True)
    db.add_all([lib_a, lib_b, lib_t1, lib_t2])
    db.flush()
    seq = [0]

    def mk(lib, item_type, name, **kw):
        seq[0] += 1
        kw.setdefault("date_added", t0 + timedelta(hours=seq[0]))
        kw.setdefault("sort_name", name.lower() if name else "")
        it = em.MediaItem(guid=_g(), library_id=lib.id, item_type=item_type, name=name,
                          file_path=f"/m/{seq[0]}.mkv", **kw)
        db.add(it)
        db.flush()
        return it

    m = {}
    # 1) 跨库同片（同 tmdb）：B 份有海报 → 代表应为 B 份（pick_primary 口径）
    m["xj_a"] = mk(lib_a, "movie", "星际穿越", tmdb_id="157336", production_year=2014)
    m["xj_b"] = mk(lib_b, "movie", "星际穿越", tmdb_id="157336", production_year=2014,
                   poster_path="/p.jpg")
    # 6) 不同作品（异 tmdb、标题包含搜索词）：不许误折
    m["xj_sp"] = mk(lib_a, "movie", "星际穿越前传", tmdb_id="999001", production_year=2010)
    # 2) 合并副本：副本即便有海报也先剔除，只回主行
    m["dm_p"] = mk(lib_a, "movie", "盗梦空间", tmdb_id="27205", production_year=2010)
    m["dm_c"] = mk(lib_a, "movie", "盗梦空间", tmdb_id="27205", production_year=2010,
                   poster_path="/c.jpg", merged_into_id=m["dm_p"].id)
    # 3) 无 tmdb 跨库同名同年（未绑定条目）：A 份有简介 → 代表为 A 份；
    #    同时证明搜索面不挂身份门（无任何编号仍可搜到）
    m["wm_a"] = mk(lib_a, "movie", "无名之辈", production_year=2018, overview="简介")
    m["wm_b"] = mk(lib_b, "movie", "无名之辈", production_year=2018)
    # 4) 同名不同年：独立作品，不折
    m["wm_24"] = mk(lib_b, "movie", "无名之辈", production_year=2024)
    # 5) 跨库同剧（同 tmdb）：T2 份有简介 → 代表为 T2 份
    m["qq_t1"] = mk(lib_t1, "series", "权力的游戏", tmdb_id="1399")
    m["qq_t2"] = mk(lib_t2, "series", "权力的游戏", tmdb_id="1399", overview="x")

    user = models.WebUser(username="be15", password_hash="x", is_active=True)
    db.add(user)
    db.flush()
    db.commit()
    data = SimpleNamespace(
        db=db, m=m,
        user=SimpleNamespace(id=user.id, is_staff=True))
    yield data
    db.close()
    engine.dispose()


def _req(params):
    return Request({"type": "http", "method": "GET",
                    "query_string": urlencode(params).encode(), "headers": []})


def _search(seeded, term, **extra):
    params = {"SearchTerm": term, "Recursive": "true", **extra}
    return api._query_items(_req(params), seeded.user, seeded.db, "http://t")


def _names(res):
    return [i["Name"] for i in res["Items"]]


# ---------------- /Items?SearchTerm= 端到端 ----------------

def test_search_cross_library_same_tmdb_folded(seeded):
    res = _search(seeded, "星际穿越")
    hits = [i for i in res["Items"] if i["Name"] == "星际穿越"]
    assert len(hits) == 1
    assert hits[0]["Id"] == seeded.m["xj_b"].guid  # 有海报的 B 份当代表


def test_search_total_and_pagination_match_folded(seeded):
    res = _search(seeded, "星际穿越")
    # 折叠后共 2 部：星际穿越（代表）+ 星际穿越前传（独立作品）
    assert res["TotalRecordCount"] == 2
    assert _names(res) == ["星际穿越", "星际穿越前传"]
    page1 = _search(seeded, "星际穿越", Limit=1, StartIndex=0)
    page2 = _search(seeded, "星际穿越", Limit=1, StartIndex=1)
    page3 = _search(seeded, "星际穿越", Limit=1, StartIndex=2)
    assert _names(page1) == ["星际穿越"]
    assert _names(page2) == ["星际穿越前传"]
    assert page3["Items"] == []
    assert page1["TotalRecordCount"] == page2["TotalRecordCount"] == 2


def test_search_merged_copy_not_returned(seeded):
    res = _search(seeded, "盗梦空间")
    assert res["TotalRecordCount"] == 1
    assert [i["Id"] for i in res["Items"]] == [seeded.m["dm_p"].guid]


def test_search_no_tmdb_cross_library_folded_and_unbound_visible(seeded):
    res = _search(seeded, "无名之辈")
    # 2018 组折成 1 条（A 份有简介当代表）+ 2024 独立 1 条
    assert res["TotalRecordCount"] == 2
    ids = {i["Id"] for i in res["Items"]}
    assert seeded.m["wm_a"].guid in ids
    assert seeded.m["wm_b"].guid not in ids
    assert seeded.m["wm_24"].guid in ids


def test_search_series_cross_library_folded(seeded):
    res = _search(seeded, "权力的游戏")
    assert res["TotalRecordCount"] == 1
    assert [i["Id"] for i in res["Items"]] == [seeded.m["qq_t2"].guid]


# ---------------- /Search/Hints 同口径 ----------------

def test_hints_cross_library_folded(seeded):
    res = search_api.search_hints(
        _req({"SearchTerm": "星际穿越"}), seeded.user, seeded.db)
    names = [h["Name"] for h in res["SearchHints"]]
    assert names.count("星际穿越") == 1
    hit = [h for h in res["SearchHints"] if h["Name"] == "星际穿越"][0]
    assert hit["Id"] == seeded.m["xj_b"].guid
    assert res["TotalRecordCount"] == 2  # 含独立作品「星际穿越前传」


# ---------------- 折叠函数单元口径 ----------------

class _Fake:
    def __init__(self, id, library_id, item_type, name, tmdb_id=None,
                 year=None, poster=None, overview=None, merged_into_id=None,
                 file_path="/f.mkv"):
        self.id = id
        self.library_id = library_id
        self.item_type = item_type
        self.name = name
        self.tmdb_id = tmdb_id
        self.production_year = year
        self.poster_path = poster
        self.primary_image_url = None
        self.overview = overview
        self.merged_into_id = merged_into_id
        self.file_path = file_path
        self.guid = f"guid-{id}"


def test_fold_key_drops_library_only():
    a = _Fake(1, 5, "movie", "片", tmdb_id="42")
    b = _Fake(2, 6, "movie", "片", tmdb_id="42")
    # 货架键含库：两行键不同；搜索键只差库维度：两行同组
    assert dedup_lib.dedup_key(a) != dedup_lib.dedup_key(b)
    assert dedup_lib.search_dedup_key(a) == dedup_lib.search_dedup_key(b)


def test_fold_preserves_order_and_picks_primary():
    items = [
        _Fake(1, 5, "movie", "A片", tmdb_id="1"),
        _Fake(2, 5, "movie", "B片", tmdb_id="2"),
        _Fake(3, 6, "movie", "A片", tmdb_id="1", poster="/p.jpg"),
    ]
    out = dedup_lib.deduplicate_search_items(items)
    # A 组占首现位置（第 1 位），代表为有海报的跨库份（id=3）
    assert [i.id for i in out] == [3, 2]
    assert out[0]._merged_count == 1


def test_fold_episode_passthrough_and_merged_dropped():
    items = [
        _Fake(1, 5, "episode", "E1"),
        _Fake(2, 5, "episode", "E1"),
        _Fake(3, 5, "movie", "片", tmdb_id="7", merged_into_id=99),
        _Fake(4, 6, "movie", "片", tmdb_id="7"),
    ]
    out = dedup_lib.deduplicate_search_items(items)
    assert [i.id for i in out] == [1, 2, 4]
