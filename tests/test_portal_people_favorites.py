"""门户「演职人员收藏」（/api/user/emby/people/*）回归测试。

口径（2026-11-13 用户亲点整套做）：
- 人员没有实体表，收藏按「人员键」落 emby_user_person_favorites：
  优先 tmdb:<person_tmdb_id>，无 TMDB id 时 name:<归一化姓名>
  （归一化 = strip + 折叠连续空白 + casefold）；
- 详情接口负责把姓名键解析/升级为规范键，收藏状态按候选键集合判定
  （刮削后来补上 tmdb id，旧收藏仍算数）；
- 参演作品按人员键反查 credits，集/季收口到所属剧，走货架可见口径
  （软删/被合并副本不出），收藏是个人数据、只许操作自己的；
- 收藏行存姓名/头像快照，credits 被删后「我的收藏人员」列表仍可展示。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server import portal
from backend.models import WebUser

init_db()


def _guid():
    return uuid.uuid4().hex


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class _Seed:
    def __init__(self, db):
        self.db = db
        tag = _guid()[:8]
        self.a = WebUser(username="people_a_" + tag, password_hash="x")
        self.b = WebUser(username="people_b_" + tag, password_hash="x")
        db.add_all([self.a, self.b])
        db.flush()
        self.lib = em.Library(guid=_guid(), name="人员收藏测试库", collection_type="mixed")
        db.add(self.lib)
        db.flush()

    def item(self, item_type, name, **kw):
        it = em.MediaItem(guid=_guid(), library_id=self.lib.id, item_type=item_type, name=name, **kw)
        self.db.add(it)
        self.db.flush()
        return it

    def credit(self, item, name, role="角色", image=None, tmdb=None):
        p = em.EmbyPerson(item_id=item.id, name=name, role=role, image=image, person_tmdb_id=tmdb)
        self.db.add(p)
        self.db.flush()
        return p


@pytest.fixture()
def seed(db):
    s = _Seed(db)
    # 人员键是全局查找：演员名/TMDB id 必须按种子唯一，否则跨用例串扰
    tag = _guid()[:8]
    p1 = f"Actor One {tag}"
    p1_tmdb = str(7000000 + int(tag, 16) % 999999)
    p2a, p2b = f"Actor  Two {tag}", f"actor two {tag}"
    movie1 = s.item("movie", "人员电影一", production_year=2020, community_rating=8.1)
    movie2 = s.item("movie", "人员电影二", production_year=2022, community_rating=7.2)
    series = s.item("series", "人员剧集", production_year=2023, community_rating=9.0)
    ep = s.item("episode", "第一集", series_id=series.id, season_number=1, episode_number=1)
    hidden = s.item("movie", "隐藏电影", production_year=2021, is_hidden=True)
    gone = s.item("movie", "已删电影", production_year=2019,
                  deleted_at=datetime(2026, 1, 1))
    dupe = s.item("movie", "人员电影一副本", production_year=2020, merged_into_id=movie1.id)

    # 有 TMDB id 的演员：三部作品 + 隐藏/已删/副本各一份（作品列表只出前三部收口结果）
    s.credit(movie1, p1, role="Hero", image="http://img/one.jpg", tmdb=p1_tmdb)
    s.credit(movie2, p1, role="Villain", image="http://img/one.jpg", tmdb=p1_tmdb)
    s.credit(ep, p1, role="Guest", image="http://img/one.jpg", tmdb=p1_tmdb)
    s.credit(hidden, p1, role="Hidden", tmdb=p1_tmdb)
    s.credit(gone, p1, role="Gone", tmdb=p1_tmdb)
    s.credit(dupe, p1, role="Dupe", tmdb=p1_tmdb)
    # 无 TMDB id 的演员：两行写法不一致（多空格/大小写），归一化后是同一个人
    s.credit(movie1, p2a, role="Support")
    s.credit(movie2, p2b, role="Lead")
    db.commit()
    return {"s": s, "movie1": movie1, "movie2": movie2, "series": series, "ep": ep,
            "p1": p1, "p1_tmdb": p1_tmdb, "p1_key": f"tmdb:{p1_tmdb}",
            "p1_name_key": "name:" + portal.normalize_person_name(p1),
            "p2_key": "name:" + portal.normalize_person_name(p2a)}


def _toggle(db, user, key):
    return portal.toggle_person_favorite(portal.PersonFavoriteToggle(key=key), user, db)


def test_toggle_by_tmdb_key_roundtrip(db, seed):
    a = seed["s"].a
    res = _toggle(db, a, seed["p1_key"])
    assert res["is_favorite"] is True
    lst = portal.get_person_favorite_list(a, db)["items"]
    assert len(lst) == 1
    assert lst[0]["key"] == seed["p1_key"]
    assert lst[0]["name"] == seed["p1"]
    assert lst[0]["image"] == "http://img/one.jpg"
    assert lst[0]["tmdb_id"] == seed["p1_tmdb"]
    res = _toggle(db, a, seed["p1_key"])
    assert res["is_favorite"] is False
    assert portal.get_person_favorite_list(a, db)["items"] == []


def test_detail_canonicalizes_name_key_to_tmdb(db, seed):
    d = portal.get_person_detail(seed["p1_name_key"], seed["s"].a, db)
    assert d["key"] == seed["p1_key"]
    assert d["name"] == seed["p1"]
    assert d["tmdb_id"] == seed["p1_tmdb"]
    assert d["is_favorite"] is False


def test_toggle_by_name_key_lands_on_tmdb_key(db, seed):
    a = seed["s"].a
    res = _toggle(db, a, seed["p1_name_key"])
    assert res["is_favorite"] is True
    assert res["key"] == seed["p1_key"]
    lst = portal.get_person_favorite_list(a, db)["items"]
    assert [i["key"] for i in lst] == [seed["p1_key"]]
    # 反过来用 tmdb 键能取消同一个收藏（候选键集合判定）
    res = _toggle(db, a, seed["p1_key"])
    assert res["is_favorite"] is False


def test_detail_works_dedupes_and_filters(db, seed):
    d = portal.get_person_detail(seed["p1_key"], seed["s"].a, db)
    names = [w["name"] for w in d["works"]]
    # 集收口到剧；隐藏/已删/合并副本不出
    assert sorted(names) == ["人员剧集", "人员电影一", "人员电影二"]
    by_name = {w["name"]: w for w in d["works"]}
    assert by_name["人员电影一"]["role"] == "Hero"
    assert by_name["人员电影一"]["id"] == seed["movie1"].guid
    assert by_name["人员剧集"]["id"] == seed["series"].guid


def test_name_key_person_without_tmdb(db, seed):
    d = portal.get_person_detail(seed["p2_key"].upper().replace("NAME:", "name:"), seed["s"].a, db)
    assert d["key"] == seed["p2_key"]
    assert d["tmdb_id"] is None
    assert sorted(w["name"] for w in d["works"]) == ["人员电影一", "人员电影二"]


def test_favorites_are_per_user(db, seed):
    a, b = seed["s"].a, seed["s"].b
    _toggle(db, a, seed["p1_key"])
    assert portal.get_person_favorite_list(b, db)["items"] == []
    d = portal.get_person_detail(seed["p1_key"], b, db)
    assert d["is_favorite"] is False
    # B 取消不了 A 的收藏：B 自己切换只影响 B
    _toggle(db, b, seed["p1_key"])
    assert len(portal.get_person_favorite_list(a, db)["items"]) == 1
    assert len(portal.get_person_favorite_list(b, db)["items"]) == 1


def test_unknown_person_404(db, seed):
    with pytest.raises(HTTPException) as ei:
        portal.get_person_detail("tmdb:999999", seed["s"].a, db)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        _toggle(db, seed["s"].a, "tmdb:999999")
    assert ei.value.status_code == 404


def test_malformed_key_400(db, seed):
    with pytest.raises(HTTPException) as ei:
        portal.get_person_detail("garbage", seed["s"].a, db)
    assert ei.value.status_code == 400


def test_list_uses_snapshot_after_credits_deleted(db, seed):
    a = seed["s"].a
    _toggle(db, a, seed["p1_key"])
    db.query(em.EmbyPerson).filter(em.EmbyPerson.person_tmdb_id == seed["p1_tmdb"]).delete()
    db.commit()
    lst = portal.get_person_favorite_list(a, db)["items"]
    assert len(lst) == 1
    assert lst[0]["name"] == seed["p1"]
    assert lst[0]["image"] == "http://img/one.jpg"
