# -*- coding: utf-8 -*-
"""缺集全套测试（2026-11-10 用户亲点）：自动算缺集＋管理员清单
＋用户一键求补。

钉死口径：
- 应有集＝TMDB 已播出（air_date ≤ 今天，Asia/Shanghai）的集；
  第 0 季（特别篇）默认不算、开配置才算；集号为空的实有行不计
  实有、也不单独报缺。
- TMDB 失败/无数据 → 整部跳过、下轮再试，绝不把失败算成全缺。
- 核算补齐 → 台账行 resolved + 该剧补缺集求片置 completed 并走
  现成到货通知收口；部分补齐只刷新求片快照、不发通知。
- 求补落单走 movie_requests 同一张表（request_kind 区分）、同一
  套额度/附议收口；同用户幂等（409）、他人转附议；整片求片与
  补缺集求片互不误撞查重。
- 读时/扫描侧「在库即完成」联动不许碰补缺集行（剧本来就在库）。

全部用隔离的内存 SQLite，不碰网络：TMDB 走 TmdbClient 类属性
打桩（纪律：只打类属性，不打单例实例属性）。

注：tests/test_missing_episodes.py 是 EA 展示侧现算的既有测试，
与本功能（落库核算＋求补闭环）不是一回事，故另起本文件。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — 注册 web_users / movie_requests 等表
from backend import media_seek, missing_episodes as me, models
from backend.api import user as user_api
from backend.database import Base
from backend.emby_server import missing_episodes_worker as mw
from backend.emby_server import models as em
from backend.emby_server.tmdb import TmdbClient, TmdbTransientError
from backend.integrations import store as config_store

TODAY = date(2026, 11, 10)
PAST = "2026-10-01"
FUTURE = "2026-12-01"


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    s = factory()
    config_store.invalidate()
    try:
        yield s
    finally:
        config_store.invalidate()
        s.close()


@pytest.fixture(autouse=True)
def _worker_env(monkeypatch):
    monkeypatch.setenv("MISSING_EPISODES_ENABLED", "1")
    monkeypatch.setenv("MISSING_EPISODES_JITTER_SEC", "0")
    monkeypatch.setenv("MISSING_EPISODES_REFRESH_DAYS", "0")
    monkeypatch.setattr(mw, "_load_ratio", lambda: 0.0)


class FakeTmdb:
    """TMDB 打桩数据：details 按 tmdb_id、season_episodes 按 (id, 季)。"""

    def __init__(self):
        self.details_map = {}
        self.season_map = {}

    def install(self, monkeypatch):
        fake = self

        def details(_client, tmdb_id, kind):
            value = fake.details_map.get(str(tmdb_id))
            if isinstance(value, Exception):
                raise value
            return value

        def season_episodes(_client, tmdb_id, season_number):
            value = fake.season_map.get(
                (str(tmdb_id), int(season_number)), [])
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(TmdbClient, "details", details)
        monkeypatch.setattr(TmdbClient, "season_episodes", season_episodes)


@pytest.fixture()
def tmdb(monkeypatch):
    fake = FakeTmdb()
    fake.install(monkeypatch)
    return fake


def _series(db, name="测试剧", tmdb_id="100", library_id=1):
    it = em.MediaItem(
        guid=f"g-series-{name}-{tmdb_id or 'x'}-{os.urandom(3).hex()}",
        library_id=library_id, item_type="series", name=name,
        tmdb_id=tmdb_id, file_path="",
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _episode(db, series, season, ep_number):
    it = em.MediaItem(
        guid=f"g-ep-{series.id}-{season}-{ep_number}-{os.urandom(3).hex()}",
        library_id=series.library_id, item_type="episode",
        name=f"E{ep_number}", file_path="mount://1/x.mkv",
        series_id=series.id, season_number=season,
        episode_number=ep_number,
    )
    db.add(it)
    db.commit()
    return it


def _user(db, username="wisher", telegram_id=None):
    user = models.WebUser(
        username=username, password_hash="x", is_active=True,
        telegram_id=telegram_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_config(db, key, value):
    cfg = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if cfg is None:
        cfg = models.SystemConfig(key=key, value=value)
        db.add(cfg)
    else:
        cfg.value = value
    db.commit()
    config_store.invalidate()


def _details(*season_numbers):
    return {"seasons": [
        {"season_number": n, "episode_count": 0} for n in season_numbers]}


def _eps(*pairs):
    """(集号, 播出日) → TMDB 集字典列表。"""
    return [{"episode_number": n, "air_date": d, "name": f"第{n}集"}
            for n, d in pairs]


def _record(db, series_id):
    return db.query(em.MissingEpisodeRecord).filter(
        em.MissingEpisodeRecord.series_item_id == series_id).first()


def _audit(factory, **cfg_over):
    cfg = mw.missing_episodes_config()
    cfg.update(cfg_over)
    return mw.audit_once(session_factory=factory, cfg=cfg, today=TODAY)


def _open_record(db, series, detail, tmdb_id="100"):
    row = em.MissingEpisodeRecord(
        series_item_id=series.id, tmdb_id=tmdb_id,
        series_name=series.name, library_id=series.library_id,
        missing_detail=me.missing_detail_to_json(detail),
        missing_count=me.missing_count(detail), status="open",
        first_detected_at=datetime(2026, 11, 1, 9, 0, 0),
        last_checked_at=datetime(2026, 11, 10, 8, 0, 0))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 1. 应有/实有对表
# ---------------------------------------------------------------------------

def test_audit_computes_missing_excluding_unaired_and_specials(
        db, factory, tmdb):
    series = _series(db)
    tmdb.details_map["100"] = _details(0, 1, 2)
    tmdb.season_map[("100", 0)] = _eps((1, PAST), (2, PAST))
    tmdb.season_map[("100", 1)] = _eps(
        (1, PAST), (2, PAST), (3, PAST), (4, PAST), (5, PAST),
        (6, FUTURE))  # 第 6 集还没播，不算缺
    tmdb.season_map[("100", 2)] = _eps((1, PAST), (2, PAST), (3, PAST))
    _episode(db, series, 0, 1)
    _episode(db, series, 1, 1)
    _episode(db, series, 1, 3)
    _episode(db, series, 1, None)  # 集号为空的行：不计实有
    _episode(db, series, 2, 1)

    stats = _audit(factory)
    assert stats["checked"] == 1
    row = _record(db, series.id)
    assert row is not None and row.status == "open"
    detail = me.normalize_missing_detail(row.missing_detail)
    assert detail == {1: [2, 4, 5], 2: [2, 3]}, "第 0 季默认不算、未播不算缺"
    assert row.missing_count == 5
    assert row.first_detected_at is not None


def test_specials_counted_when_enabled(db, factory, tmdb):
    series = _series(db)
    tmdb.details_map["100"] = _details(0, 1)
    tmdb.season_map[("100", 0)] = _eps((1, PAST), (2, PAST))
    tmdb.season_map[("100", 1)] = _eps((1, PAST))
    _episode(db, series, 0, 1)
    _episode(db, series, 1, 1)

    _audit(factory, include_specials=True)
    row = _record(db, series.id)
    assert row.status == "open"
    assert me.normalize_missing_detail(row.missing_detail) == {0: [2]}


def test_no_tmdb_series_skipped_and_counted(db, factory, tmdb):
    _series(db, name="无号剧", tmdb_id=None)
    known = _series(db, name="有号剧", tmdb_id="100")
    tmdb.details_map["100"] = _details(1)
    tmdb.season_map[("100", 1)] = _eps((1, PAST))
    _episode(db, known, 1, 1)

    stats = _audit(factory)
    assert stats["skipped_no_tmdb"] == 1
    assert stats["checked"] == 1


# ---------------------------------------------------------------------------
# 2. TMDB 失败跳过不误报
# ---------------------------------------------------------------------------

def test_tmdb_transient_error_skips_series(db, factory, tmdb):
    series = _series(db)
    tmdb.details_map["100"] = TmdbTransientError("网络挂了")

    stats = _audit(factory)
    assert stats["checked"] == 0
    assert stats["tmdb_skipped"] == 1
    assert _record(db, series.id) is None, "失败不许落台账、更不许报全缺"


def test_tmdb_season_none_skips_whole_series(db, factory, tmdb):
    series = _series(db)
    tmdb.details_map["100"] = _details(1, 2)
    tmdb.season_map[("100", 1)] = _eps((1, PAST), (2, PAST))
    tmdb.season_map[("100", 2)] = None  # 这一季拿不到 → 整部跳过
    _episode(db, series, 1, 1)

    stats = _audit(factory)
    assert stats["checked"] == 0
    assert _record(db, series.id) is None


# ---------------------------------------------------------------------------
# 3. resolved 流转 / 补齐通知 / 部分补齐
# ---------------------------------------------------------------------------

def _setup_missing_series(db, tmdb):
    series = _series(db)
    tmdb.details_map["100"] = _details(1)
    tmdb.season_map[("100", 1)] = _eps((1, PAST), (2, PAST), (3, PAST))
    _episode(db, series, 1, 1)
    return series


def test_full_fill_resolves_and_completes_request(db, factory, tmdb):
    series = _setup_missing_series(db, tmdb)
    _audit(factory)
    assert _record(db, series.id).status == "open"

    user = _user(db, telegram_id=4242)
    result = user_api._create_missing_episode_request(db, user, "100")
    assert result["created"] is True

    _episode(db, series, 1, 2)
    _episode(db, series, 1, 3)
    _audit(factory)

    row = _record(db, series.id)
    assert row.status == "resolved" and row.resolved_at is not None
    req = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert req.status == "completed", "补齐后补缺集求片要自动完成"
    notices = db.query(models.TgProactiveNotice).filter(
        models.TgProactiveNotice.subject_id == req.id,
        models.TgProactiveNotice.kind == "store_arrival").all()
    assert len(notices) == 1 and notices[0].user_id == user.id, \
        "到货通知要走现成 proactive 收口入队"


def test_partial_fill_updates_snapshot_without_notice(db, factory, tmdb):
    series = _setup_missing_series(db, tmdb)
    _audit(factory)
    user = _user(db, telegram_id=4242)
    result = user_api._create_missing_episode_request(db, user, "100")

    _episode(db, series, 1, 2)  # 只补一集，还缺第 3 集
    _audit(factory)

    row = _record(db, series.id)
    assert row.status == "open"
    assert me.normalize_missing_detail(row.missing_detail) == {1: [3]}
    req = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert req.status == "pending", "部分补齐不许把求片置完成"
    assert me.request_detail_missing(req.request_detail) == {1: [3]}, \
        "求片上的缺集快照要跟着刷新"
    assert db.query(models.TgProactiveNotice).count() == 0, "部分补齐不发通知"


def test_series_gone_invalidates_open_record(db, factory, tmdb):
    series = _setup_missing_series(db, tmdb)
    _audit(factory)
    assert _record(db, series.id).status == "open"

    series.deleted_at = datetime.now()
    db.commit()
    stats = _audit(factory)
    assert stats["invalidated"] == 1
    assert _record(db, series.id).status == "resolved"


# ---------------------------------------------------------------------------
# 4. 求补落单：幂等 / 附议 / 额度 / 与整片求片互不误撞
# ---------------------------------------------------------------------------

def test_missing_request_creates_row_with_snapshot(db):
    series = _series(db)
    _open_record(db, series, {1: [2, 5]})
    user = _user(db)

    result = user_api._create_missing_episode_request(db, user, "100")
    assert result["created"] is True
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert row.request_kind == "missing_episodes"
    assert row.title_only is False
    assert row.status == "pending"
    detail = me.parse_request_detail(row.request_detail)
    assert detail["series_item_id"] == series.id
    assert me.request_detail_missing(row.request_detail) == {1: [2, 5]}


def test_missing_request_rejected_when_not_missing(db):
    _series(db)  # 在库但没有 open 缺集记录
    user = _user(db)
    with pytest.raises(HTTPException) as exc:
        user_api._create_missing_episode_request(db, user, "100")
    assert exc.value.status_code == 400


def test_missing_request_rejected_when_series_not_in_library(db):
    user = _user(db)
    with pytest.raises(HTTPException) as exc:
        user_api._create_missing_episode_request(db, user, "100")
    assert exc.value.status_code == 400


def test_missing_request_same_user_conflict(db):
    series = _series(db)
    _open_record(db, series, {1: [2]})
    user = _user(db)
    user_api._create_missing_episode_request(db, user, "100")
    with pytest.raises(HTTPException) as exc:
        user_api._create_missing_episode_request(db, user, "100")
    assert exc.value.status_code == 409


def test_missing_request_other_user_becomes_vote(db):
    series = _series(db)
    _open_record(db, series, {1: [2]})
    first = _user(db, username="first")
    second = _user(db, username="second")
    r1 = user_api._create_missing_episode_request(db, first, "100")
    r2 = user_api._create_missing_episode_request(db, second, "100")
    assert r2["created"] is False and r2["voted"] is True
    assert r2["request_id"] == r1["request_id"]
    rows = db.query(models.MovieRequest).filter(
        models.MovieRequest.request_kind == "missing_episodes").all()
    assert len(rows) == 1 and rows[0].vote_count == 1


def test_missing_request_daily_quota_applies(db):
    series = _series(db)
    _open_record(db, series, {1: [2]})
    _set_config(db, media_seek.CONFIG_DAILY_LIMIT, "1")
    user = _user(db)
    # 先用一条普通求片占掉今日额度（与补缺集共用同一本账）
    payload = user_api.MediaSeekRequest(movie_name="别的片", tmdb_id="999")
    user_api._create_media_seek_result(db, user, "别的片", payload)
    with pytest.raises(HTTPException) as exc:
        user_api._create_missing_episode_request(db, user, "100")
    assert exc.value.status_code == 429


def test_seek_and_missing_requests_do_not_collide(db):
    series = _series(db)
    _open_record(db, series, {1: [2]})
    user = _user(db)
    other = _user(db, username="other")
    # 先有补缺集求片在漂
    user_api._create_missing_episode_request(db, user, "100")
    # 另一个人求整片（同 tmdb）：不许被转成补缺集那条的附议
    payload = user_api.MediaSeekRequest(
        movie_name="测试剧", tmdb_id="100", type="series")
    result = user_api._create_media_seek_result(db, other, "测试剧", payload)
    assert result["created"] is True
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()
    assert (row.request_kind or "seek") == "seek"


def test_missing_info_endpoint_payload(db):
    series = _series(db)
    _open_record(db, series, {1: [2, 5]})
    user = _user(db)
    payload = user_api._missing_info_payload(db, user, "100")
    assert payload["in_library"] is True
    assert payload["has_missing"] is True
    assert payload["missing_count"] == 2
    assert payload["missing_text"] == "第 1 季缺第 2、5 集"
    assert payload["request"] is None

    user_api._create_missing_episode_request(db, user, "100")
    payload = user_api._missing_info_payload(db, user, "100")
    assert payload["request"] is not None
    assert payload["request"]["mine"] is True


# ---------------------------------------------------------------------------
# 5. 在库联动不许误完成补缺集行
# ---------------------------------------------------------------------------

def test_sync_paths_skip_missing_requests(db):
    from backend.api.store_requests import _sync_row
    from backend.tg_bot import proactive

    series = _series(db)  # 剧本来就在库：tmdb 一定对得上
    _open_record(db, series, {1: [2]})
    user = _user(db)
    result = user_api._create_missing_episode_request(db, user, "100")
    req = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == result["request_id"]).first()

    assert _sync_row(db, req) is False
    db.refresh(req)
    assert req.status == "pending"
    changed = proactive.sync_arrived_requests(db)
    assert changed == 0
    db.refresh(req)
    assert req.status == "pending", "扫描侧联动不许把补缺集行按「在库」误完成"


# ---------------------------------------------------------------------------
# 6. 管理员清单 API
# ---------------------------------------------------------------------------

def _admin_list(db, **kw):
    from backend.api import admin_missing_episodes as adm
    args = dict(limit=50, offset=0, sort="missing_count", q="",
                library_id=None, db=db, admin=None)
    args.update(kw)
    return adm.list_missing_episodes(**args)


def test_admin_list_pagination_sort_filter(db):
    s1 = _series(db, name="甲剧", tmdb_id="100", library_id=1)
    s2 = _series(db, name="乙剧", tmdb_id="200", library_id=2)
    s3 = _series(db, name="丙剧", tmdb_id="300", library_id=1)
    _open_record(db, s1, {1: [2, 3, 4]}, tmdb_id="100")
    _open_record(db, s2, {1: [2]}, tmdb_id="200")
    _open_record(db, s3, {1: [2]}, tmdb_id="300")
    # resolved 行不进清单
    done = _open_record(db, _series(db, name="丁剧", tmdb_id="400"),
                        {1: [9]}, tmdb_id="400")
    done.status = "resolved"
    db.commit()

    res = _admin_list(db)
    assert res["total"] == 3
    assert [i["series_name"] for i in res["items"]] == ["甲剧", "乙剧", "丙剧"], \
        "默认按缺集数倒序"

    page = _admin_list(db, limit=2, offset=2)
    assert page["total"] == 3 and len(page["items"]) == 1

    by_name = _admin_list(db, q="乙")
    assert by_name["total"] == 1
    assert by_name["items"][0]["series_name"] == "乙剧"

    by_lib = _admin_list(db, library_id=2)
    assert by_lib["total"] == 1
    assert by_lib["items"][0]["missing_text"] == "第 1 季缺第 2 集"


def test_admin_list_requesters_and_summary(db):
    from backend.api import admin_missing_episodes as adm

    series = _series(db, name="甲剧", tmdb_id="100")
    _open_record(db, series, {1: [2, 3]})
    _series(db, name="无号剧", tmdb_id=None)
    first = _user(db, username="first")
    second = _user(db, username="second")
    user_api._create_missing_episode_request(db, first, "100")
    user_api._create_missing_episode_request(db, second, "100")

    res = _admin_list(db)
    assert res["items"][0]["requesters"] == 2, "求补人数＝发起人＋附议人"

    summary = adm.missing_episodes_summary(db=db, admin=None)
    assert summary["open_series"] == 1
    assert summary["total_missing"] == 2
    assert summary["skipped_no_tmdb"] == 1


def test_missing_text_formatting():
    assert me.missing_text({1: [2, 5]}) == "第 1 季缺第 2、5 集"
    assert me.missing_text({1: [2], 2: [1, 3]}) == \
        "第 1 季缺第 2 集，第 2 季缺第 1、3 集"
    assert me.missing_text({}) == ""
    folded = me.missing_text({1: list(range(1, 10))})
    assert folded == "第 1 季缺 9 集"
