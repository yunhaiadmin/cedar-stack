# -*- coding: utf-8 -*-
"""缺集补集通道测试（B 方案，2026-11-11 用户拍板）：绕开 MP 订阅
判存，按 Aetrix 缺集台账直接走 MP 搜索＋手动下载接口补集。

钉死口径：
- MP 客户端：信封 {success, data} 解析；success=false / HTTP 5xx
  → MpError，绝不把失败当空结果。
- 选种：先对身份（年份冲突直接拒，Plan B 1995 假种是实证教训），
  再按覆盖缺集数优先、做种数次之、体积合理（单集体积离谱的拒）；
  整季包覆盖全缺集时只下它一个。
- 工人：总开关默认关；token 未配整轮跳过（reason=not_configured）
  不调 MP；每日上限按「已发起」计数硬停；同剧同季冷却内不重复
  发起（含失败/无种）；MP 报错单部隔离、不许整轮抛异常。
- attempt 账只记事实（种子标题/站点/download_id/结果），本通道
  绝不直接改缺集台账的 resolved（销账归缺集核算工人）。

全部用隔离的内存 SQLite + httpx MockTransport / 假客户端，不碰
网络、不碰真实 MP。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — 注册 web / system_configs 等表
from backend.database import Base
from backend.emby_server import models as em
from backend.emby_server import mp_episode_fill as mf
from backend.integrations import store as config_store


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
def _calm_worker(monkeypatch):
    monkeypatch.setattr(mf, "_load_ratio", lambda: 0.0)
    mf._reset_circuit()


def _save_cfg(db, **values):
    from backend import config_service

    config_service.save_config(
        db, values, schema="mp_episode_fill",
        descriptions={k: "MP 补集通道测试" for k in values})
    db.commit()
    config_store.invalidate()


def _open_record(db, *, series_id=101, tmdb_id="89927", name="香水",
                 missing=None, first_detected_at=None):
    from backend import missing_episodes as me

    detail = missing if missing is not None else {1: [1, 3]}
    rec = em.MissingEpisodeRecord(
        series_item_id=series_id,
        tmdb_id=str(tmdb_id),
        series_name=name,
        library_id=45,
        missing_detail=me.missing_detail_to_json(detail),
        missing_count=me.missing_count(detail),
        status="open",
        first_detected_at=first_detected_at or datetime.now(),
        last_checked_at=datetime.now(),
    )
    db.add(rec)
    db.commit()
    return rec


# ---------------------------------------------------------------------------
# MP 客户端：信封解析
# ---------------------------------------------------------------------------

def _client(handler, base="http://mp.test:4500", token="tok-abc"):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return mf.MpClient(base, token, http=http)


def test_get_media_unwraps_envelope_and_sends_key():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={
            "success": True,
            "data": {"tmdb_id": 34000, "title": "逃亡者PLAN B",
                     "year": "2010"},
        })

    client = _client(handler)
    data = client.get_media(34000)
    assert data["title"] == "逃亡者PLAN B"
    assert data["tmdb_id"] == 34000
    assert "/api/v1/media/34000" in seen["url"]
    assert "media_source=themoviedb" in seen["url"]
    assert seen["key"] == "tok-abc"


def test_get_media_success_false_raises():
    def handler(request):
        return httpx.Response(200, json={
            "success": False, "message": "未找到", "data": None})

    with pytest.raises(mf.MpError):
        _client(handler).get_media(1)


def test_get_media_http_500_raises_not_empty():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(mf.MpError):
        _client(handler).get_media(1)


def test_search_title_unwraps_list_and_download_returns_id():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/v1/search/title":
            return httpx.Response(200, json={"success": True, "data": [
                {"torrent_info": {"title": "X S01E01", "seeders": 3}},
            ]})
        if request.url.path == "/api/v1/download/":
            body = json.loads(request.content)
            assert body["media_in"]["tmdb_id"] == 34000
            assert body["torrent_in"]["title"] == "X S01E01"
            return httpx.Response(200, json={
                "success": True, "data": {"download_id": "dl-1"}})
        return httpx.Response(404, json={})

    client = _client(handler)
    results = client.search_title("逃亡者PLAN B")
    assert results[0]["torrent_info"]["title"] == "X S01E01"
    dl = client.start_download(
        {"tmdb_id": 34000}, {"title": "X S01E01"})
    assert dl == "dl-1"


# ---------------------------------------------------------------------------
# 选种口径
# ---------------------------------------------------------------------------

_MEDIA = {"tmdb_id": 89927, "title": "香水", "original_title": "향수",
          "year": "2019"}


def _tor(title, seeders=5, size=1_500_000_000, site="NovaHD"):
    return {"title": title, "seeders": seeders, "size": size,
            "site_name": site, "enclosure": "http://x/t"}


def test_parse_coverage_forms():
    assert mf.parse_episode_coverage("X S01E05 1080p", 1) == {5}
    assert mf.parse_episode_coverage("X S01E05-E08", 1) == {5, 6, 7, 8}
    assert mf.parse_episode_coverage("X S01E05E06", 1) == {5, 6}
    assert mf.parse_episode_coverage("X S02E05", 1) is None
    pack = mf.parse_episode_coverage("X S01 完集 1080p", 1)
    assert pack == "pack"
    assert mf.parse_episode_coverage("X 1080p 无季号", 1) is None


def test_select_full_pack_covers_all_missing_wins():
    results = [
        {"torrent_info": _tor("香水 S01E01 1080p", seeders=50)},
        {"torrent_info": _tor("香水 S01E03 1080p", seeders=50)},
        {"torrent_info": _tor("香水 S01 全集 1080p", seeders=2,
                              size=32 * 700_000_000)},
    ]
    picked = mf.select_torrents(results, _MEDIA, 1, {1, 3})
    assert len(picked) == 1
    assert "全集" in picked[0]["title"]


def test_select_coverage_first_then_seeders():
    results = [
        {"torrent_info": _tor("香水 S01E01", seeders=99)},
        {"torrent_info": _tor("香水 S01E01-E03", seeders=1)},
    ]
    picked = mf.select_torrents(results, _MEDIA, 1, {1, 3})
    assert picked[0]["title"].endswith("S01E01-E03")


def test_select_rejects_wrong_year_seed():
    # 实证教训：《逃亡者PLAN B》唯一命中的是 1995 年假种，必须拒。
    media = {"tmdb_id": 34000, "title": "逃亡者PLAN B",
             "original_title": "", "year": "2010"}
    results = [{"torrent_info": _tor(
        "The Fugitive: Plan B 1995 NTSC DVD9", seeders=1,
        size=85_920_157_696)}]
    assert mf.select_torrents(results, media, 1, set(range(1, 21))) == []


def test_select_rejects_dead_and_absurd_size():
    results = [
        {"torrent_info": _tor("香水 S01E01", seeders=0)},
        {"torrent_info": _tor("香水 S01E03", seeders=5,
                               size=900_000_000_000)},
    ]
    assert mf.select_torrents(results, _MEDIA, 1, {1, 3}) == []


def test_select_combines_singles_until_covered():
    results = [
        {"torrent_info": _tor("香水 S01E03", seeders=5)},
        {"torrent_info": _tor("香水 S01E01", seeders=5)},
        {"torrent_info": _tor("香水 S01E07", seeders=5)},
    ]
    picked = mf.select_torrents(results, _MEDIA, 1, {1, 3})
    titles = {t["title"] for t in picked}
    assert titles == {"香水 S01E01", "香水 S01E03"}


# ---------------------------------------------------------------------------
# 工人
# ---------------------------------------------------------------------------

class FakeMp:
    """假 MP 客户端：按 tmdb 返回媒体/种子，记录下载调用。"""

    def __init__(self):
        self.media = {}
        self.results = {}
        self.downloads = []
        self.media_error = None

    def get_media(self, tmdb_id):
        if self.media_error is not None:
            raise self.media_error
        return self.media.get(str(tmdb_id))

    def search_title(self, keyword):
        return self.results.get(keyword, [])

    def start_download(self, media_in, torrent_in):
        self.downloads.append((media_in, torrent_in))
        return f"dl-{len(self.downloads)}"


def _enable(db, **over):
    values = {
        "mp_episode_fill_enabled": True,
        "mp_download_token": "tok-abc",
        "mp_episode_fill_daily_cap": 10,
        "mp_episode_fill_cooldown_hours": 24,
    }
    values.update(over)
    _save_cfg(db, **values)


def test_worker_disabled_by_default(db, factory):
    _open_record(db)
    fake = FakeMp()
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["ran"] is False
    assert stats["reason"] == "disabled"
    assert fake.downloads == []


def test_worker_skips_when_token_unconfigured(db, factory):
    _save_cfg(db, mp_episode_fill_enabled=True)
    _open_record(db)
    called = []
    stats = mf.fill_once(
        factory, client_factory=lambda cfg: called.append(1) or FakeMp())
    assert stats["ran"] is False
    assert stats["reason"] == "not_configured"
    assert called == []  # token 没配：连客户端都不许建


def test_worker_happy_path_records_attempt(db, factory):
    _enable(db)
    _open_record(db, missing={1: [1, 3]})
    fake = FakeMp()
    fake.media["89927"] = dict(_MEDIA)
    fake.results["香水"] = [
        {"torrent_info": _tor("香水 S01 全集 1080p", seeders=3,
                               size=32 * 700_000_000)}]
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["ran"] is True
    assert stats["started"] == 1
    assert len(fake.downloads) == 1
    s = factory()
    try:
        rows = s.query(em.MissingEpisodeFillAttempt).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "started"
        assert row.download_id == "dl-1"
        assert row.season_number == 1
        assert json.loads(row.missing_eps) == [1, 3]
        assert row.torrent_title.startswith("香水 S01")
        # 台账不许被本通道改成 resolved
        rec = s.query(em.MissingEpisodeRecord).first()
        assert rec.status == "open"
    finally:
        s.close()


def test_worker_no_match_records_and_cools_down(db, factory):
    _enable(db)
    _open_record(db, missing={1: [1]})
    fake = FakeMp()
    fake.media["89927"] = dict(_MEDIA)
    fake.results["香水"] = []
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["started"] == 0
    assert stats["no_match"] == 1
    # 第二轮：冷却内，不再重复搜
    stats2 = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats2["cooldown_skips"] == 1
    assert stats2["no_match"] == 0


def test_worker_daily_cap_hard_stops(db, factory):
    _enable(db, mp_episode_fill_daily_cap=1)
    _open_record(db, series_id=101, tmdb_id="89927", name="香水",
                 missing={1: [1]})
    _open_record(db, series_id=102, tmdb_id="89928", name="另一部",
                 missing={1: [2]})
    fake = FakeMp()
    fake.media["89927"] = dict(_MEDIA)
    fake.media["89928"] = {"tmdb_id": 89928, "title": "另一部",
                            "original_title": "", "year": "2020"}
    pack = [{"torrent_info": _tor("X S01 全集", seeders=3,
                                  size=10 * 700_000_000)}]
    fake.results["香水"] = pack
    fake.results["另一部"] = pack
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["started"] == 1
    assert stats["cap_reached"] is True
    assert len(fake.downloads) == 1


def test_worker_mp_500_isolated_no_crash(db, factory):
    _enable(db)
    _open_record(db, missing={1: [1]})
    fake = FakeMp()
    fake.media_error = mf.MpError("HTTP 500")
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["ran"] is True
    assert stats["errors"] == 1
    s = factory()
    try:
        rows = s.query(em.MissingEpisodeFillAttempt).all()
        assert len(rows) == 1
        assert rows[0].status == "error"
    finally:
        s.close()


def test_worker_identity_mismatch_no_download(db, factory):
    _enable(db)
    _open_record(db, tmdb_id="89927", missing={1: [1]})
    fake = FakeMp()
    fake.media["89927"] = {"tmdb_id": 12345, "title": "别的剧",
                            "year": "2019"}
    stats = mf.fill_once(factory, client_factory=lambda cfg: fake)
    assert stats["started"] == 0
    assert stats["errors"] == 1
    assert fake.downloads == []


def test_config_payload_defaults(db):
    payload = mf.mp_fill_config_payload(db)
    assert payload["mp_download_base"] == "http://167.17.76.115:4500"
    assert payload["mp_download_token"] == ""
    assert payload["mp_episode_fill_enabled"] is False
    assert payload["mp_episode_fill_daily_cap"] == 10
    assert payload["mp_episode_fill_cooldown_hours"] == 24
