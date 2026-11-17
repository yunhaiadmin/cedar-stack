# -*- coding: utf-8 -*-
"""网页媒体库审计 BE-02 / BE-14 回归（2026-11-17）。

BE-02：Views 库计数（_library_item_count）不排除合并副本，相对用户
可翻到的代表数虚高（生产 movie +19% / series +18.5% 量级）。计数口径
必须与列表代表口径对齐：ORM 口径 = deleted_at IS NULL（全局钩子）
AND merged_into_id IS NULL。扫描器写 item_count 列时早已排除副本，
差的是实时计数回退与身份门现算两条路径（及列表投影的同口径副本）。

BE-14：搜索候选先按 sort_name 截断 500 再做相关性排序，且把截断后
条数报成 TotalRecordCount——热门词下最相关的条目可能被挤出候选，
总数也小于真实命中数。修法：截断前先按 SQL 粗相关度（精确 > 前缀
> 包含）排序，总数报同一 query 的真实 COUNT。/Items 搜索分支与
/Search/Hints 同形态同修。候选池上限本身是防整库载入内存的护栏，
保留：可翻页范围为粗相关度前 500 条，总数报全部命中。
"""
import os
import tempfile
import uuid
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models as core  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import api, identity_gate, list_projection
from backend.emby_server import models as em
from backend.emby_server import search_api


class _QP(dict):
    """仿 Starlette QueryParams：get + getlist。"""

    def getlist(self, key):
        v = self.get(key)
        return [v] if v else []


@pytest.fixture()
def db():
    path = os.path.join(tempfile.mkdtemp(), "be02be14.db")
    engine = create_engine(f"sqlite:///{path}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        yield s
    engine.dispose()


def _mk(db, lib_id, item_type, name, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib_id, item_type=item_type,
        name=name, sort_name=kw.pop("sort_name", name),
        file_path=f"/m/{uuid.uuid4().hex}.mkv", is_hidden=False,
        date_added=datetime.now(), **kw)
    db.add(it)
    db.flush()
    return it


# ==================== BE-02 ====================

@pytest.fixture()
def count_scene(db):
    """一库：代表 movie×2 + 其合并副本×1、代表 series×1 + 副本×1、
    已下架代表 movie×1、单集×1（不入 Views 计数）。代表口径 = 3。"""
    lib = em.Library(guid=uuid.uuid4().hex, name="计数库",
                     collection_type="mixed", is_enabled=True)
    db.add(lib)
    db.flush()
    lib.item_count = None  # 强制走实时计数回退（构造期赋 None 会被列默认 0 吃掉）
    db.flush()
    a = _mk(db, lib.id, "movie", "代表电影A", tmdb_id="100")
    _mk(db, lib.id, "movie", "代表电影A副本", tmdb_id="100",
        merged_into_id=a.id)
    _mk(db, lib.id, "movie", "代表电影B", tmdb_id="101")
    s = _mk(db, lib.id, "series", "代表剧S", tmdb_id="200")
    _mk(db, lib.id, "series", "代表剧S副本", tmdb_id="200",
        merged_into_id=s.id)
    _mk(db, lib.id, "movie", "已下架电影", tmdb_id="102",
        deleted_at=datetime.now())
    _mk(db, lib.id, "episode", "某集", series_id=s.id)
    db.commit()
    return SimpleNamespace(db=db, lib=lib)


def test_library_item_count_excludes_merged_gate_off(count_scene,
                                                     monkeypatch):
    monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
    n = api._library_item_count(count_scene.db, count_scene.lib)
    assert n == 3, f"Views 计数应为代表数 3（2 电影 + 1 剧），实际 {n}"


def test_library_item_count_excludes_merged_gate_on(count_scene,
                                                    monkeypatch):
    monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 1)
    n = api._library_item_count(count_scene.db, count_scene.lib)
    assert n == 3, f"身份门现算应为已绑定代表数 3，实际 {n}"


def test_projection_live_child_count_excludes_merged(count_scene):
    n = list_projection._live_child_count(count_scene.db, count_scene.lib)
    assert n == 3, f"列表投影现算口径应与 Views 一致 = 3，实际 {n}"


# ==================== BE-14 ====================

TERM = "星际"
FILLERS = 505


@pytest.fixture()
def search_scene(db):
    """506 条同词命中：505 条「包含」级填充 + 1 条与词完全相等的条目。

    填充的 sort_name 全部排在精确条目之前（精确条目 sort_name 垫底），
    旧实现按 sort_name 截断 500 时精确条目进不了候选集。
    """
    lib = em.Library(guid=uuid.uuid4().hex, name="搜索库",
                     collection_type="movies", is_enabled=True)
    db.add(lib)
    db.flush()
    for i in range(1, FILLERS + 1):
        _mk(db, lib.id, "movie", f"星际填充{i:03d}")
    exact = _mk(db, lib.id, "movie", TERM, sort_name="星际填充999")
    user = core.WebUser(username="u_be14", password_hash="x")
    db.add(user)
    db.commit()
    return SimpleNamespace(db=db, lib=lib, exact=exact, user=user)


def _call_items(db, user, **params):
    req = SimpleNamespace(query_params=_QP(params))
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(api, "_library_scope", lambda db, user: None)
        mp.setattr(api, "_prefetch_list_data", lambda *a, **k: None)
        mp.setattr(api, "_item_dto",
                   lambda e, base, uid, db: {"Id": e.guid, "Name": e.name,
                                             "Type": e.item_type})
        return api._query_items(req, user, db, "http://test")


def test_items_search_total_is_real_hit_count(search_scene):
    res = _call_items(search_scene.db, search_scene.user,
                      SearchTerm=TERM, Recursive="true", Limit="20")
    assert res["TotalRecordCount"] == FILLERS + 1, (
        f"总数应报真实命中 {FILLERS + 1}，实际 {res['TotalRecordCount']}")


def test_items_search_exact_match_survives_truncation(search_scene):
    res = _call_items(search_scene.db, search_scene.user,
                      SearchTerm=TERM, Recursive="true", Limit="20")
    names = [i["Name"] for i in res["Items"]]
    assert names and names[0] == TERM, (
        f"完全匹配条目应排第一，实际首页 {names[:3]}")


def test_items_search_pool_edge_page_reachable(search_scene):
    """候选池边界页可达：池上限 500 是防整库载入内存的既有护栏（保留），
    池内按粗相关度取前 500——StartIndex=499 应恰好拿到池内最后 1 条；
    旧实现下这一页拿到的是按 sort_name 截断的任意填充条且总数虚报。"""
    res = _call_items(search_scene.db, search_scene.user,
                      SearchTerm=TERM, Recursive="true",
                      StartIndex="499", Limit="20")
    assert len(res["Items"]) == 1, (
        f"池边界页应有 1 条，实际 {len(res['Items'])} 条")
    assert res["TotalRecordCount"] == FILLERS + 1


def test_search_hints_total_is_real_hit_count(search_scene, monkeypatch):
    monkeypatch.setattr(search_api.library_scope, "effective_ids_safe",
                        lambda db, user: None)
    req = SimpleNamespace(
        query_params=_QP({"SearchTerm": TERM, "Limit": "20"}),
        base_url="http://test/")
    res = search_api.search_hints(req, search_scene.user, search_scene.db)
    assert res["TotalRecordCount"] == FILLERS + 1, (
        f"Hints 总数应报真实命中 {FILLERS + 1}，实际 "
        f"{res['TotalRecordCount']}")
    names = [h["Name"] for h in res["SearchHints"]]
    assert names and names[0] == TERM, (
        f"完全匹配条目应排第一，实际 {names[:3]}")
