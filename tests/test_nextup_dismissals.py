"""「隐藏下一集」回归测试（审计 ❌1 推导方案落地）。

真实起因：友站首页下一集卡有垃圾桶（隐藏这部剧的下一集推送），我方
后端没有落点、前端不渲染。本单补齐：
- 新表 user_nextup_dismissals（用户 × 剧 → 关闭时那一集 guid）；
- 门户口 POST /next-up/{episode_guid}/hide、/unhide、GET /next-up/hidden
  （与继续观看 L9 藏同形状）；
- get_next_up 主列表按剧排除「已关闭且被关那集仍是当前下一集」的剧；
  下一集变了（真看了新集）关闭自动失效、卡片回来；
- 详情页 SeriesId 直达分支不受关闭影响；只动本人记录。
"""

import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models as core
from backend.database import Base
from backend.emby_server import api, models as em


# ---------------------------------------------------------------- 场景


@pytest.fixture
def db():
    path = os.path.join(tempfile.mkdtemp(), "nextup_dismiss.db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        yield s


def _mk(db, lib, item_type, name, series=None, season=None, episode=None):
    import uuid
    it = em.MediaItem(
        guid=uuid.uuid4().hex[:32],
        library_id=lib.id,
        item_type=item_type,
        name=name,
        sort_name=name,
        series_id=series.id if series else None,
        parent_id=None,
        season_number=season,
        episode_number=episode,
        is_hidden=False,
    )
    db.add(it)
    db.flush()
    return it


def _watched(db, user, item, hours_ago=1):
    db.add(em.UserMediaData(
        user_id=user.id, item_id=item.id, played=True,
        play_count=1, last_played_at=datetime.now() - timedelta(hours=hours_ago),
    ))
    db.flush()


@pytest.fixture
def scene(db):
    lib = em.Library(guid="L" * 32, name="动漫剧", collection_type="tvshows", paths="")
    db.add(lib)
    db.flush()
    series_a = _mk(db, lib, "series", "剧A")
    a1 = _mk(db, lib, "episode", "剧A E01", series=series_a, season=1, episode=1)
    a2 = _mk(db, lib, "episode", "剧A E02", series=series_a, season=1, episode=2)
    a3 = _mk(db, lib, "episode", "剧A E03", series=series_a, season=1, episode=3)
    user = core.WebUser(username="u1", password_hash="x")
    other = core.WebUser(username="u2", password_hash="x")
    db.add_all([user, other])
    db.flush()
    _watched(db, user, a1)
    db.commit()
    return SimpleNamespace(db=db, user=user, other=other, lib=lib,
                           series_a=series_a, a1=a1, a2=a2, a3=a3)


def _dismiss(db, user, series, episode):
    row = em.UserNextUpDismissal(
        user_id=user.id, series_id=series.id,
        dismissed_episode_guid=episode.guid,
    )
    db.add(row)
    db.commit()
    return row


def _call(db, user, series_id=None):
    params = {"Limit": "20"}
    if series_id:
        params["SeriesId"] = series_id
    req = SimpleNamespace(query_params=params)
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(api, "_base_url", lambda request: "http://test")
        mp.setattr(api, "_prefetch_list_data", lambda *a, **k: None)
        mp.setattr(api, "_item_dto", lambda e, base, uid, db: {"Id": e.guid, "Name": e.name})
        return api.get_next_up(req, user, db)


# ------------------------------------------------------- get_next_up 过滤


def test_dismissed_series_excluded_from_next_up(scene):
    _dismiss(scene.db, scene.user, scene.series_a, scene.a2)
    res = _call(scene.db, scene.user)
    assert res["TotalRecordCount"] == 0
    assert res["Items"] == []


def test_dismissal_auto_invalidates_when_next_episode_changes(scene):
    """真看了被关的那集 → 下一集变成 E03，关闭自动失效、卡片回来推 E03。"""
    _dismiss(scene.db, scene.user, scene.series_a, scene.a2)
    _watched(scene.db, scene.user, scene.a2)
    scene.db.commit()
    res = _call(scene.db, scene.user)
    assert res["TotalRecordCount"] == 1
    assert res["Items"][0]["Name"] == "剧A E03"


def test_dismissal_scoped_to_owner_only(scene):
    """别人关的剧不影响我。"""
    _dismiss(scene.db, scene.other, scene.series_a, scene.a2)
    res = _call(scene.db, scene.user)
    assert res["TotalRecordCount"] == 1
    assert res["Items"][0]["Name"] == "剧A E02"


def test_series_id_branch_unaffected_by_dismissal(scene):
    """详情页直达（SeriesId 点名）不被关闭拦：关闭只管首页推送面。"""
    _dismiss(scene.db, scene.user, scene.series_a, scene.a2)
    res = _call(scene.db, scene.user, series_id=scene.series_a.guid)
    assert res["TotalRecordCount"] == 1
    assert res["Items"][0]["Name"] == "剧A E02"


# ------------------------------------------------------------- 门户端点

_fd, _tmppath = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from backend import database as _dbmod  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402

from backend.database import SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """与 test_media_library_endpoints 同口径：模块内换绑临时库、结束还原，
    不在导入期动全局引擎（#1668 卫生纪律）。"""
    saved_engine = _dbmod.engine
    saved_factory = _dbmod.SessionLocal._factory
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmppath}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = saved_engine
        _dbmod.configure_session_local(saved_factory)
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        engine.dispose()


def _tables():
    return (em.UserNextUpDismissal, em.UserMediaData, em.MediaItem,
            em.Library, core.WebUser)


@pytest.fixture()
def pdb():
    s = SessionLocal()
    try:
        for table in _tables():
            s.query(table).delete()
        s.commit()
        yield s
    finally:
        try:
            for table in _tables():
                s.query(table).delete()
            s.commit()
        except Exception:
            s.rollback()
        finally:
            s.close()


@pytest.fixture()
def pclient(pdb):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: SessionLocal()
    try:
        yield TestClient(app, raise_server_exceptions=False), app
    finally:
        app.dependency_overrides.clear()


def _as_user(app, user):
    from backend.api.user import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def pscene(pdb):
    lib = em.Library(guid="P" * 32, name="剧库", collection_type="tvshows", paths="")
    pdb.add(lib)
    pdb.flush()
    series_a = _mk(pdb, lib, "series", "剧A")
    a1 = _mk(pdb, lib, "episode", "剧A E01", series=series_a, season=1, episode=1)
    a2 = _mk(pdb, lib, "episode", "剧A E02", series=series_a, season=1, episode=2)
    _mk(pdb, lib, "episode", "剧A E03", series=series_a, season=1, episode=3)
    movie = _mk(pdb, lib, "movie", "电影X")
    user = core.WebUser(username="p1", password_hash="x", is_active=True)
    other = core.WebUser(username="p2", password_hash="x", is_active=True)
    pdb.add_all([user, other])
    pdb.flush()
    _watched(pdb, user, a1)
    pdb.commit()
    return SimpleNamespace(db=pdb, user=user, other=other,
                           series_a=series_a, a2=a2, movie=movie)


def test_hide_unhide_flow(pclient, pscene):
    client, app = pclient
    _as_user(app, pscene.user)
    r = client.post(f"/api/user/media-library/next-up/{pscene.a2.guid}/hide")
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is True
    rows = pscene.db.query(em.UserNextUpDismissal).all()
    assert len(rows) == 1
    assert rows[0].series_id == pscene.series_a.id
    assert rows[0].dismissed_episode_guid == pscene.a2.guid
    # 幂等：重复点不产生第二行、不报错
    r = client.post(f"/api/user/media-library/next-up/{pscene.a2.guid}/hide")
    assert r.status_code == 200
    assert pscene.db.query(em.UserNextUpDismissal).count() == 1
    # 已藏清单（仍生效）里有这部剧
    items = client.get("/api/user/media-library/next-up/hidden").json()["items"]
    assert [it["series_guid"] for it in items] == [pscene.series_a.guid]
    assert items[0]["episode_guid"] == pscene.a2.guid
    # 恢复后清单清空、行删除
    r = client.post(f"/api/user/media-library/next-up/{pscene.a2.guid}/unhide")
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] is False
    assert pscene.db.query(em.UserNextUpDismissal).count() == 0
    assert client.get("/api/user/media-library/next-up/hidden").json()["items"] == []


def test_hidden_list_scoped_and_stale_excluded(pclient, pscene):
    client, app = pclient
    _as_user(app, pscene.user)
    client.post(f"/api/user/media-library/next-up/{pscene.a2.guid}/hide")
    # 别人看不到我的已藏清单
    _as_user(app, pscene.other)
    assert client.get("/api/user/media-library/next-up/hidden").json()["items"] == []
    # 已失效的关闭（被关那集已不是当前下一集）不进清单：
    # 直接落一行「关的是 E01」的陈旧记录——当前下一集是 E02，不匹配。
    stale = em.UserNextUpDismissal(
        user_id=pscene.other.id, series_id=pscene.series_a.id,
        dismissed_episode_guid="0" * 32)
    pscene.db.add(stale)
    pscene.db.commit()
    assert client.get("/api/user/media-library/next-up/hidden").json()["items"] == []


def test_hide_rejects_unknown_and_non_episode(pclient, pscene):
    client, app = pclient
    _as_user(app, pscene.user)
    r = client.post(f"/api/user/media-library/next-up/{'9' * 32}/hide")
    assert r.status_code == 404
    r = client.post(f"/api/user/media-library/next-up/{pscene.movie.guid}/hide")
    assert r.status_code == 400
    r = client.post(f"/api/user/media-library/next-up/{pscene.series_a.guid}/hide")
    assert r.status_code == 400
