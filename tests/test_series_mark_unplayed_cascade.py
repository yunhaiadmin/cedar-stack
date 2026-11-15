"""剧集「整部移除观看记录」级联回归钉（审计 ⚠️1）。

口径：DELETE PlayedItems 打到剧集时，语义与电影的整部移除一致——
该剧自身 + 各季 + 各集的本人观看痕迹一并清掉（played=false、
play_count=0、断点与最远点清零），条目随之离开继续观看/下一集。
边界：只动本人记录；不动收藏；不动别的剧；电影单条目行为不变；
不可撤销（与电影现状一致）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid

import pytest

from backend import models
from backend.database import SessionLocal, init_db
from backend.emby_server import models as em
from backend.emby_server.api import mark_unplayed

init_db()

UNAME = "suc_alice"
UNAME2 = "suc_bob"
RUNTIME = 600_000_000


def _guid():
    return uuid.uuid4().hex


def _wipe(db, username):
    old = db.query(models.WebUser).filter(
        models.WebUser.username == username).first()
    if old:
        db.query(em.UserMediaData).filter(
            em.UserMediaData.user_id == old.id).delete(synchronize_session=False)
        db.query(models.WebUser).filter(models.WebUser.id == old.id).delete()
        db.commit()


@pytest.fixture(scope="module")
def seed():
    db = SessionLocal()
    _wipe(db, UNAME)
    _wipe(db, UNAME2)
    for stale in db.query(em.Library).filter(
            em.Library.name.like("suc级联%")).all():
        ids = [r[0] for r in db.query(em.MediaItem.id)
               .filter(em.MediaItem.library_id == stale.id).all()]
        if ids:
            db.query(em.MediaItem).filter(em.MediaItem.id.in_(ids)).delete(
                synchronize_session=False)
        db.query(em.Library).filter(em.Library.id == stale.id).delete()
    db.commit()

    user = models.WebUser(username=UNAME, password_hash="x", is_active=True,
                          is_staff=True)
    other = models.WebUser(username=UNAME2, password_hash="x", is_active=True,
                           is_staff=True)
    db.add_all([user, other])
    lib = em.Library(guid=_guid(), name="suc级联库", collection_type="tvshows",
                     is_enabled=True)
    db.add(lib)
    db.flush()

    def mk(name, itype, parent_id=None, series_id=None):
        it = em.MediaItem(guid=_guid(), library_id=lib.id, item_type=itype,
                          name=name, file_path=f"/tmp/none/{name}.mp4",
                          container="mp4", sort_name=name,
                          duration_ticks=RUNTIME, parent_id=parent_id,
                          series_id=series_id)
        db.add(it)
        db.flush()
        return it

    series = mk("suc剧", "series")
    season = mk("suc剧 S1", "season", parent_id=series.id, series_id=series.id)
    ep1 = mk("suc剧 E1", "episode", parent_id=season.id, series_id=series.id)
    ep2 = mk("suc剧 E2", "episode", parent_id=season.id, series_id=series.id)
    series2 = mk("suc邻居剧", "series")
    ep3 = mk("suc邻居剧 E1", "episode", series_id=series2.id)
    movie = mk("suc电影", "movie")

    def umd(uid, item, **kw):
        row = em.UserMediaData(user_id=uid, item_id=item.id, **kw)
        db.add(row)
        return row

    umd(user.id, series, played=True, play_count=3,
        playback_position_ticks=10, max_position_ticks=20)
    umd(user.id, season, played=True, play_count=1,
        playback_position_ticks=0, max_position_ticks=0)
    umd(user.id, ep1, played=True, play_count=2, is_favorite=True,
        playback_position_ticks=111, max_position_ticks=222)
    umd(user.id, ep2, played=False, play_count=1,
        playback_position_ticks=333, max_position_ticks=444)
    umd(user.id, ep3, played=True, play_count=5,
        playback_position_ticks=555, max_position_ticks=666)
    umd(user.id, movie, played=True, play_count=1,
        playback_position_ticks=777, max_position_ticks=888)
    umd(other.id, ep1, played=True, play_count=9,
        playback_position_ticks=999, max_position_ticks=999)
    db.commit()
    data = {"user_id": user.id, "other_id": other.id,
            "series": series.guid, "season_id": season.id,
            "ep1_id": ep1.id, "ep2_id": ep2.id, "ep3_id": ep3.id,
            "series_id": series.id, "movie": movie.guid,
            "movie_id": movie.id}
    db.close()
    return data


def _umd(user_id, item_id):
    db = SessionLocal()
    row = db.query(em.UserMediaData).filter(
        em.UserMediaData.user_id == user_id,
        em.UserMediaData.item_id == item_id).first()
    if row is not None:
        db.expunge(row)
    db.close()
    return row


def _assert_cleared(row):
    assert row is not None
    assert row.played is False
    assert row.play_count == 0
    assert row.playback_position_ticks == 0
    assert row.max_position_ticks == 0


def test_series_mark_unplayed_cascades_to_seasons_and_episodes(seed):
    db = SessionLocal()
    user = db.query(models.WebUser).filter(
        models.WebUser.id == seed["user_id"]).first()
    mark_unplayed(seed["series"], str(user.id), user=user, db=db)
    db.close()

    uid = seed["user_id"]
    # 剧自身 + 季 + 两集：全清
    _assert_cleared(_umd(uid, seed["series_id"]))
    _assert_cleared(_umd(uid, seed["season_id"]))
    _assert_cleared(_umd(uid, seed["ep1_id"]))
    _assert_cleared(_umd(uid, seed["ep2_id"]))
    # 收藏不动
    assert _umd(uid, seed["ep1_id"]).is_favorite is True
    # 别人的记录不动
    other_row = _umd(seed["other_id"], seed["ep1_id"])
    assert other_row.played is True
    assert other_row.play_count == 9
    assert other_row.playback_position_ticks == 999
    # 别的剧不动
    ep3 = _umd(uid, seed["ep3_id"])
    assert ep3.played is True
    assert ep3.play_count == 5
    assert ep3.playback_position_ticks == 555


def test_movie_mark_unplayed_unchanged(seed):
    db = SessionLocal()
    user = db.query(models.WebUser).filter(
        models.WebUser.id == seed["user_id"]).first()
    mark_unplayed(seed["movie"], str(user.id), user=user, db=db)
    db.close()
    _assert_cleared(_umd(seed["user_id"], seed["movie_id"]))
