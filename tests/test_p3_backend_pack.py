# -*- coding: utf-8 -*-
"""网页媒体库审计 P3 后端打包修复回归（台账 2026-11-17）。

逐项对应台账：
- BE-03 Latest 先 limit 后折叠 → 多取候选折叠后再截断，页长不再短于 Limit；
- BE-04 随机排序分支 want_total=false 时不再白跑 COUNT，且返回 HasMore；
- BE-08 门状态（gate/phase2/phase3）拼入列表/去重/计数三层缓存键；
- BE-09 env.example 豆瓣身份注释与代码口径一致（任一在用来源都算绑定）；
- BE-12 Episodes 页内去重代表选取走码率优先（与 Versions 同口径）；
- BE-18 图片补下载巡检加播放信号门（任一用户的真实播放信号/追剧订阅）；
- BE-22 详情 DTO 签名判定统一走 playback_gate.sign_allowed_for_item；
- BE-25 Filters 聚合叠加身份门（门开时未绑定条目的 facet 值不进菜单）；
- BE-26 DateLastContentAdded 物化列 last_content_added_at（钩子维护＋
  回填＋排序 coalesce 回退旧标量子查询，行为不变、热路径不逐行算）。
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend import models as core_models
from backend.database import Base
from backend.emby_server import api, identity_gate
from backend.emby_server import items_dedup_cache as idc
from backend.emby_server import models as em


class FakeRedis:
    def __init__(self):
        self.strings = {}

    def get(self, key):
        v = self.strings.get(key)
        return v[0] if v else None

    def set(self, key, val, ex=None):
        self.strings[key] = (val, None)
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.strings:
                del self.strings[k]
                n += 1
        return n

    def zadd(self, name, mapping):
        return 0

    def zcard(self, name):
        return 0

    def zrange(self, name, start, end):
        return []

    def zrem(self, name, *members):
        return 0


@pytest.fixture()
def fake_backend():
    fb = FakeRedis()
    idc._set_backend_override(fb)
    yield fb
    idc._set_backend_override(None)
    idc._reset_bump_state()


@pytest.fixture()
def scene(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/p3.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    lib = em.Library(guid=uuid.uuid4().hex, name="库",
                     collection_type="mixed", is_enabled=True)
    db.add(lib)
    db.flush()
    user = core_models.WebUser(username="p3", password_hash="x",
                               is_active=True)
    db.add(user)
    db.flush()
    data = SimpleNamespace(
        db=db, engine=engine, lib=lib,
        user=SimpleNamespace(id=user.id, is_staff=False),
        user_row=user)
    yield data
    db.close()
    engine.dispose()


def _mk(db, lib, item_type, name, **kw):
    kw.setdefault("guid", uuid.uuid4().hex)
    kw.setdefault("library_id", lib.id)
    kw.setdefault("sort_name", name)
    kw.setdefault("is_hidden", False)
    it = em.MediaItem(item_type=item_type, name=name, **kw)
    db.add(it)
    db.flush()
    return it


def _req(params):
    return Request({"type": "http", "method": "GET",
                    "query_string": urlencode(params).encode(),
                    "headers": []})


# ---------------------------------------------------------------- BE-03

class TestBE03Latest:
    def test_latest_page_not_shortened_by_dedup(self, scene, monkeypatch):
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        # 同片三份未合并重复，占满最新三名；其后是另一部片
        for i in range(3):
            _mk(db, lib, "movie", "同片", tmdb_id="100",
                date_added=now - timedelta(hours=i))
        _mk(db, lib, "movie", "另一片", tmdb_id="200",
            date_added=now - timedelta(hours=5))
        db.commit()
        # 门状态钉关：本类只测 Latest 折叠力学，不依赖环境门默认值
        # （BE-06 后默认开，种子条目身份不齐时会先被门滤掉）。
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        monkeypatch.setattr(api, "_base_url", lambda request: "http://t")
        monkeypatch.setattr(api, "_prefetch_list_data", lambda *a, **k: None)
        monkeypatch.setattr(api, "_library_scope", lambda db, user: None)
        monkeypatch.setattr(
            api, "_item_dto",
            lambda e, base, uid, db: {"Id": e.guid, "Name": e.name})
        req = SimpleNamespace(query_params={"Limit": "2"})
        out = api.get_latest(req, scene.user, db)
        names = [d["Name"] for d in out]
        assert names == ["同片", "另一片"], (
            f"Latest Limit=2 应折叠后补满 2 条，实际 {names}")


# ---------------------------------------------------------------- BE-04

class TestBE04RandomNoTotal:
    def _seed(self, scene, n=30):
        db, lib = scene.db, scene.lib
        t0 = datetime(2026, 1, 1)
        for i in range(n):
            _mk(db, lib, "movie", f"片{i:02d}",
                file_path=f"/m/{uuid.uuid4().hex}.mkv", date_added=t0)
        db.commit()

    def test_no_count_and_has_more_when_total_unwanted(
            self, scene, fake_backend, monkeypatch):
        # 门状态钉关：只测随机分支的 COUNT/HasMore 力学（种子无身份，
        # BE-06 后门默认开会把结果全滤空，与本类断言无关）。
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        self._seed(scene)
        statements = []

        def _spy(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(scene.engine, "before_cursor_execute", _spy)
        try:
            res = api._query_items(
                _req({"Recursive": "true", "IncludeItemTypes": "Movie",
                      "SortBy": "Random", "Limit": "10",
                      "EnableTotalRecordCount": "false"}),
                scene.user, scene.db, "http://t")
        finally:
            event.remove(scene.engine, "before_cursor_execute", _spy)
        assert len(res["Items"]) == 10
        assert res["TotalRecordCount"] == len(res["Items"])
        assert res.get("HasMore") is True, "want_total=false 随机分支必须给 HasMore"
        bad = [s for s in statements
               if "count(" in s.lower() and "emby_items" in s]
        assert not bad, f"want_total=false 时不许再跑 COUNT：{bad[:1]}"

    def test_want_total_true_still_real_total(self, scene, fake_backend,
                                              monkeypatch):
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        self._seed(scene)
        res = api._query_items(
            _req({"Recursive": "true", "IncludeItemTypes": "Movie",
                  "SortBy": "Random", "Limit": "10"}),
            scene.user, scene.db, "http://t")
        assert res["TotalRecordCount"] == 30
        assert len(res["Items"]) == 10


# ---------------------------------------------------------------- BE-08

class TestBE08CacheKeyGateSegment:
    def _keys(self):
        from backend.emby_server import items_list_cache as lc
        vers = {1: "0"}
        return (
            lc._cache_key([1], "ph", vers),
            lc._counts_key([1], vers, "nav"),
            idc._cache_key(7, "ph", vers),
        )

    def test_keys_change_with_gate_state(self, monkeypatch):
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_PHASE2_ENABLED", 0)
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_PHASE3_ENABLED", 0)
        off = self._keys()
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 1)
        on = self._keys()
        assert on != off, "门开关翻转必须换缓存键，否则旧口径清单最长残留一个 TTL"
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_PHASE3_ENABLED", 1)
        p3 = self._keys()
        assert p3 != on, "三期翻转（代表选取变化）必须换缓存键"
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_PHASE2_ENABLED", 1)
        p2 = self._keys()
        assert p2 != p3, "二期翻转必须换缓存键"


# ---------------------------------------------------------------- BE-09

class TestBE09EnvExample:
    def test_douban_wording_matches_code(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent
                / "env.example").read_text(encoding="utf-8")
        assert "只有豆瓣身份不算" not in text, (
            "env.example 仍写着扩围前的旧口径（豆瓣不算），与 "
            "identity_gate.EXTERNAL_ID_KEYS（含 douban）矛盾")
        assert "douban" in identity_gate.EXTERNAL_ID_KEYS


# ---------------------------------------------------------------- BE-12

class TestBE12EpisodeRepresentative:
    def test_episodes_dedup_prefers_bitrate(self, scene, monkeypatch):
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        series = _mk(db, lib, "series", "剧", tmdb_id="300",
                     date_added=now)
        season = _mk(db, lib, "season", "第 1 季", series_id=series.id,
                     season_number=1, date_added=now)
        low = _mk(db, lib, "episode", "第 1 集·低码率",
                  series_id=series.id, parent_id=season.id,
                  season_number=1, episode_number=1,
                  bitrate=500_000, file_path="/m/low.mkv",
                  date_added=now)
        high = _mk(db, lib, "episode", "第 1 集·高码率",
                   series_id=series.id, parent_id=season.id,
                   season_number=1, episode_number=1,
                   bitrate=8_000_000, file_path="/m/high.mkv",
                   date_added=now)
        db.commit()
        monkeypatch.setattr(api, "_library_scope", lambda db, user: None)
        req = SimpleNamespace(query_params={})
        out = api.get_episodes(series.guid, req, scene.user, db)
        ids = [d["Id"] for d in out["Items"]]
        assert ids == [high.guid], (
            f"同集去重应保留码率优先的代表（与 Versions 同口径），实际 {ids}，"
            f"低码率行 guid={low.guid}")


# ---------------------------------------------------------------- BE-18

class TestBE18PatrolSignalGate:
    GOOD = "https://image.tmdb.org/t/p/w500/good.jpg"

    @pytest.fixture()
    def img(self, scene, monkeypatch, tmp_path):
        from backend.emby_server import image_store
        monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path / "images"))
        monkeypatch.setenv("EMBY_LOCALIZE_IMAGES", "1")  # 2026-11-23 起默认关，本文件测开启态行为
        monkeypatch.delenv("EMBY_IMAGE_BACKFILL", raising=False)
        monkeypatch.setattr(image_store, "enqueue_thumb_warm",
                            lambda *a, **k: None)
        calls = []

        def fake_download(url, attempts=1):
            calls.append(url)
            return b"fake-image-bytes"

        monkeypatch.setattr(image_store, "_download", fake_download)
        return calls

    def test_any_play_signal_helper(self, scene):
        from backend.emby_server import first_play
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        movie = _mk(db, lib, "movie", "无信号片", date_added=now)
        series = _mk(db, lib, "series", "剧", date_added=now)
        ep = _mk(db, lib, "episode", "集", series_id=series.id,
                 season_number=1, episode_number=1, date_added=now)
        db.commit()
        assert first_play.has_any_play_signal(db, movie) is False
        assert first_play.has_any_play_signal(db, series) is False
        db.add(em.UserMediaData(user_id=scene.user.id, item_id=movie.id,
                                played=True, play_count=1))
        db.commit()
        assert first_play.has_any_play_signal(db, movie) is True
        # 同剧任一集被看过 → 整剧有信号
        db.add(em.UserMediaData(user_id=scene.user.id, item_id=ep.id,
                                played=False, playback_position_ticks=10,
                                play_count=0))
        db.commit()
        assert first_play.has_any_play_signal(db, series) is True
        assert first_play.has_any_play_signal(db, ep) is True

    def test_follow_counts_as_signal(self, scene):
        from backend.emby_server import first_play
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        series = _mk(db, lib, "series", "被追的剧", date_added=now)
        db.commit()
        assert first_play.has_any_play_signal(db, series) is False
        db.add(em.SeriesFollow(user_id=scene.user.id,
                               series_item_id=series.id, status="active"))
        db.commit()
        assert first_play.has_any_play_signal(db, series) is True

    def test_patrol_skips_items_without_signal(self, scene, img):
        from backend.emby_server import enrich_worker
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        cold = _mk(db, lib, "movie", "没人看的片",
                   primary_image_url=self.GOOD, date_added=now)
        hot = _mk(db, lib, "movie", "有人看的片",
                  primary_image_url=self.GOOD, date_added=now)
        db.add(em.UserMediaData(user_id=scene.user.id, item_id=hot.id,
                                played=True, play_count=2))
        db.commit()
        n = enrich_worker.image_backlog_patrol(db)
        db.refresh(cold)
        db.refresh(hot)
        assert n == 1, "巡检只许补有真实播放信号的条目"
        assert not cold.poster_path
        assert hot.poster_path
        assert img == [self.GOOD]


# ---------------------------------------------------------------- BE-22

class TestBE22DtoSign:
    def _movie(self, scene):
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        movie = _mk(db, lib, "movie", "池片", tmdb_id="400",
                    file_path="/m/pool.mkv", container="mkv",
                    date_added=now)
        db.commit()
        return movie

    def test_pool_only_user_gets_signed_url(self, scene, monkeypatch):
        from backend.emby_server import playback_gate, pool_gate
        movie = self._movie(scene)
        # 未绑盘、仅池资格：流端点会放行，DTO 也必须给签名轨（同源判定）
        monkeypatch.setattr(playback_gate, "has_active_binding",
                            lambda db, uid: False)
        monkeypatch.setattr(pool_gate, "pool_sign_allowed",
                            lambda db, uid, item: True)
        dto = api._item_dto(movie, "http://t", scene.user.id, scene.db,
                            full=True, api_key="k")
        sources = dto.get("MediaSources") or []
        assert sources, "本地行应有一条 MediaSource"
        url = sources[0].get("DirectStreamUrl") or ""
        assert "sign=" in url, (
            f"仅池资格用户应拿到带签名地址，实际 {url!r}")

    def test_no_binding_no_pool_stays_unsigned(self, scene, monkeypatch):
        from backend.emby_server import playback_gate, pool_gate
        movie = self._movie(scene)
        monkeypatch.setattr(playback_gate, "has_active_binding",
                            lambda db, uid: False)
        monkeypatch.setattr(pool_gate, "pool_sign_allowed",
                            lambda db, uid, item: False)
        dto = api._item_dto(movie, "http://t", scene.user.id, scene.db,
                            full=True, api_key="k")
        sources = dto.get("MediaSources") or []
        assert sources
        url = sources[0].get("DirectStreamUrl") or ""
        assert "sign=" not in url


# ---------------------------------------------------------------- BE-25

class TestBE25FiltersGate:
    def _seed(self, scene):
        db, lib = scene.db, scene.lib
        now = datetime(2026, 11, 17, 12, 0, 0)
        _mk(db, lib, "movie", "已绑定片", tmdb_id="500", genres="动作",
            official_rating="R", production_year=2020, date_added=now)
        _mk(db, lib, "movie", "未绑定片", genres="冷门类",
            official_rating="X9", production_year=1999, date_added=now)
        db.commit()

    def _payload(self):
        api._FILTERS_CACHE.clear()
        return api._filters_payload

    def test_gate_on_hides_unbound_values(self, scene, monkeypatch):
        self._seed(scene)
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 1)
        payload = self._payload()(scene.db)
        assert "动作" in payload["Genres"]
        assert "冷门类" not in payload["Genres"], (
            "门开时未绑定条目独有的类型不许进筛选菜单")
        assert "X9" not in payload["OfficialRatings"]
        assert 1999 not in payload["Years"]

    def test_gate_off_keeps_all_values(self, scene, monkeypatch):
        self._seed(scene)
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        payload = self._payload()(scene.db)
        assert "冷门类" in payload["Genres"]
        assert "X9" in payload["OfficialRatings"]
        assert 1999 in payload["Years"]


# ---------------------------------------------------------------- BE-26

class TestBE26LastContentAdded:
    def test_column_and_index_declared(self):
        assert hasattr(em.MediaItem, "last_content_added_at"), (
            "MediaItem 必须有物化列 last_content_added_at")
        from sqlalchemy import Index
        names = [a.name for a in em.MediaItem.__table_args__
                 if isinstance(a, Index)]
        assert "idx_item_last_content_added" in names

    def test_hook_maintains_series_value(self, scene):
        db, lib = scene.db, scene.lib
        d0 = datetime(2026, 1, 1, 12, 0, 0)
        d1 = datetime(2026, 3, 1, 12, 0, 0)
        d2 = datetime(2026, 6, 1, 12, 0, 0)
        series = _mk(db, lib, "series", "剧", date_added=d0)
        db.commit()
        db.refresh(series)
        assert series.last_content_added_at == d0
        ep1 = _mk(db, lib, "episode", "集1", series_id=series.id,
                  season_number=1, episode_number=1, date_added=d1)
        db.commit()
        db.refresh(series)
        assert series.last_content_added_at == d1, (
            "新集入库必须刷新所属剧的物化值")
        ep2 = _mk(db, lib, "episode", "集2", series_id=series.id,
                  season_number=1, episode_number=2, date_added=d2)
        db.commit()
        db.refresh(series)
        assert series.last_content_added_at == d2
        # 软删最新集 → 回落到剩余集的最大值
        ep2.deleted_at = datetime(2026, 7, 1, 12, 0, 0)
        db.commit()
        db.refresh(series)
        assert series.last_content_added_at == d1

    def test_backfill_fills_null_rows(self, scene):
        from backend.emby_server import last_content
        db, lib = scene.db, scene.lib
        d0 = datetime(2026, 1, 1, 12, 0, 0)
        d1 = datetime(2026, 5, 1, 12, 0, 0)
        series = _mk(db, lib, "series", "剧", date_added=d0)
        _mk(db, lib, "episode", "集1", series_id=series.id,
            season_number=1, episode_number=1, date_added=d1)
        movie = _mk(db, lib, "movie", "片", date_added=d0)
        db.commit()
        # 模拟存量 NULL（钩子未覆盖的历史行 / 批量 Core 写入）
        db.query(em.MediaItem).update(
            {em.MediaItem.last_content_added_at: None},
            synchronize_session=False)
        db.commit()
        n = last_content.ensure_backfill(db)
        assert n >= 3
        db.refresh(series)
        db.refresh(movie)
        assert series.last_content_added_at == d1
        assert movie.last_content_added_at == d0

    def test_sort_behavior_preserved(self, scene, fake_backend, monkeypatch):
        # 门状态钉关：只测 DateLastContentAdded 排序力学（种子无身份，
        # BE-06 后门默认开会把结果全滤空，与本类断言无关）。
        monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 0)
        db, lib = scene.db, scene.lib
        old_series = _mk(db, lib, "series", "老剧新集",
                         date_added=datetime(2026, 1, 1, 12, 0, 0))
        _mk(db, lib, "episode", "新集", series_id=old_series.id,
            season_number=1, episode_number=1,
            date_added=datetime(2026, 6, 1, 12, 0, 0))
        new_series = _mk(db, lib, "series", "新剧无集",
                         date_added=datetime(2026, 3, 1, 12, 0, 0))
        db.commit()
        res = api._query_items(
            _req({"Recursive": "true", "IncludeItemTypes": "Series",
                  "SortBy": "DateLastContentAdded",
                  "SortOrder": "Descending"}),
            scene.user, db, "http://t")
        names = [d["Name"] for d in res["Items"]]
        assert names.index("老剧新集") < names.index("新剧无集"), (
            f"DateLastContentAdded 降序应按最新集入库时间排，实际 {names}")
