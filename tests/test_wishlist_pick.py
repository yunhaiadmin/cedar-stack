# -*- coding: utf-8 -*-
"""许愿池「选片投愿」测试（2026-11-09 用户亲定）

背景：许愿弹窗原先纯自由输入即可投下，落行只有片名、无 tmdb 绑定，
对不上号、重复投、到货对不上。定版改为：
1. 选片投愿为主路：点选 TMDB 候选才投，落行带 tmdb_id 且走求片主口
   的每日/月度额度判定（超额被拒）；
2. 同一 tmdb 已有未完结心愿 → 不新建行，创建动作转成附议（幂等）；
3. 原名投下为兜底：直调创建口无 tmdb 的行一律收敛——落行带
   title_only 待人工确认标记，管理端可区分。

全部用隔离的内存 SQLite，不碰网络、不碰生产库。创建走
``_create_media_seek_result``（路由同款富结果）；``_create_media_seek_sync``
的 int 返回契约（TG 机器人共用）由既有 test_media_seek 系列守。
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import media_seek, models
from backend.api import admin as admin_api
from backend.api import user as user_api
from backend.integrations import store


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    store.invalidate()
    session.close()


def _user(db, username="wisher"):
    user = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _payload(**overrides):
    data = {"movie_name": "星际穿越"}
    data.update(overrides)
    return user_api.MediaSeekRequest(**data)


def _create(db, user, name, **overrides):
    return user_api._create_media_seek_result(db, user, name, _payload(movie_name=name, **overrides))


def _rows(db):
    return db.query(models.MovieRequest).order_by(models.MovieRequest.id).all()


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if cfg is None:
        cfg = models.SystemConfig(key=key, value=value)
        db.add(cfg)
    else:
        cfg.value = value
    db.commit()


# ==================== 1. 选片投愿：带 tmdb 落行 + 额度 ====================

def test_pick_create_stores_tmdb_and_not_title_only(db):
    user = _user(db)
    result = _create(db, user, "星际穿越", tmdb_id="157336", type="movie", year="2014")
    assert result["created"] is True
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert row.tmdb_id == "157336"
    assert row.title_only is False, "选片行不带待确认标记"
    assert row.status == "pending"


def test_pick_create_over_daily_quota_rejected(db):
    user = _user(db)
    _set_config(db, media_seek.CONFIG_DAILY_LIMIT, "1")
    _create(db, user, "星际穿越", tmdb_id="157336", type="movie")
    with pytest.raises(HTTPException) as exc:
        _create(db, user, "盗梦空间", tmdb_id="27205", type="movie")
    assert exc.value.status_code == 429
    assert len(_rows(db)) == 1, "超额不得落行"


# ==================== 2. 同 tmdb 已有心愿 → 转附议 ====================

def test_duplicate_tmdb_becomes_vote_not_new_row(db):
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    first = _create(db, alice, "星际穿越", tmdb_id="157336", type="movie")

    result = _create(db, bob, "星际穿越", tmdb_id="157336", type="movie")
    assert result["created"] is False, "已有心愿不得新建行"
    assert result["request_id"] == first["request_id"]
    assert result["voted"] is True
    assert result["vote_count"] == 1

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].vote_count == 1
    votes = db.query(models.MovieRequestVote).all()
    assert len(votes) == 1 and votes[0].user_id == bob.id


def test_duplicate_tmdb_vote_is_idempotent(db):
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    _create(db, alice, "星际穿越", tmdb_id="157336", type="movie")
    _create(db, bob, "星际穿越", tmdb_id="157336", type="movie")
    again = _create(db, bob, "星际穿越", tmdb_id="157336", type="movie")
    assert again["created"] is False and again["voted"] is True
    assert again["vote_count"] == 1, "重复附议不重复计数"
    assert db.query(models.MovieRequestVote).count() == 1
    assert _rows(db)[0].vote_count == 1


def test_conversion_does_not_consume_create_quota(db):
    """附议转换不是新建：投的人当日创建额度已用完，仍可完成附议"""
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    _set_config(db, media_seek.CONFIG_DAILY_LIMIT, "1")
    _create(db, alice, "星际穿越", tmdb_id="157336", type="movie")
    _create(db, bob, "盗梦空间", tmdb_id="27205", type="movie")  # bob 当日额度用完
    result = _create(db, bob, "星际穿越", tmdb_id="157336", type="movie")
    assert result["created"] is False and result["voted"] is True


def test_own_duplicate_tmdb_still_conflict(db):
    alice = _user(db, "alice")
    _create(db, alice, "星际穿越", tmdb_id="157336", type="movie")
    with pytest.raises(HTTPException) as exc:
        _create(db, alice, "星际穿越", tmdb_id="157336", type="movie")
    assert exc.value.status_code == 409


def test_same_tmdb_other_season_is_separate_wish(db):
    """季参与查重：同剧不同季是两条独立心愿（与同名去重的季口径一致）"""
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    _create(db, alice, "绝命毒师", tmdb_id="1396", type="series", season="1")
    result = _create(db, bob, "绝命毒师", tmdb_id="1396", type="series", season="2")
    assert result["created"] is True
    assert len(_rows(db)) == 2
    # 同季再投 → 转附议
    again = _create(db, bob, "绝命毒师", tmdb_id="1396", type="series", season="1")
    assert again["created"] is False and again["voted"] is True


def test_ensure_vote_idempotent_unit(db):
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    row = models.MovieRequest(user_id=alice.id, movie_name="星际穿越",
                              type="movie", status="pending", vote_count=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    first = media_seek.ensure_vote(db, bob, row.id)
    assert first["voted"] is True and first["added"] is True
    assert first["vote_count"] == 1
    second = media_seek.ensure_vote(db, bob, row.id)
    assert second["voted"] is True and second["added"] is False
    assert second["vote_count"] == 1
    assert db.query(models.MovieRequestVote).count() == 1


# ==================== 3. 原名兜底：收敛为待确认标记 ====================

def test_name_only_create_marked_title_only(db):
    """直调创建口无 tmdb（旧自由输入形态）→ 落行带 title_only 待确认标记"""
    user = _user(db)
    result = _create(db, user, "深海之间")
    assert result["created"] is True
    assert result["title_only"] is True
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert row.tmdb_id is None
    assert row.title_only is True


def test_admin_list_exposes_title_only(db):
    """管理端清单要能区分原名兜底行：DTO 带 title_only"""
    user = _user(db)
    _create(db, user, "深海之间")
    _create(db, user, "星际穿越", tmdb_id="157336", type="movie")
    rows = admin_api.get_media_seeks(status_filter=None, realm_id=0,
                                     order=None, current_admin=user, db=db)
    by_name = {r["movie_name"]: r for r in rows}
    assert by_name["深海之间"]["title_only"] is True
    assert by_name["星际穿越"]["title_only"] is False
