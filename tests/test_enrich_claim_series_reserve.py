"""认领向剧倾斜（S1）回归兜底。

背景（2026-11-23 刮削提速诊断）：单集必须等父剧（series/season）刮完
才能认领，98.3% 待刮单集被闸门锁住，闸门放行速度 = 剧完成速度
（约 250 部/小时，电影约 950 部/小时），而认领不向剧倾斜。

钉死的新口径：
① 每批先给「含待刮父级的组」（gate=1：组内有 pending 的 series/season
   行）预留 ``ENRICH_CLAIM_SERIES_RESERVE_PCT``（默认 50%）名额喂闸门
   关键路径；预留未用满自动回填普通组，电影不会被饿死；
② pct=0 关闭预留，认领顺序与旧口径逐字一致；
③ gate 判定只看组内 pending 父级行：父级已 done 的纯单集组 gate=0
   （不占预留），season pending 的组 gate=1；
④ 快照路径（TTL>0）与 A2 单查路径（TTL=0）口径一致。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import enrich_worker
from backend.emby_server import models as em

BASE = datetime(2026, 1, 1)


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


@pytest.fixture()
def lib(db):
    row = em.Library(guid=uuid.uuid4().hex, name="测试库",
                     collection_type="tvshows")
    db.add(row)
    db.flush()
    return row


def _item(db, lib, name, item_type="movie", minutes=0, **kw):
    kw.setdefault("enrich_status", "pending")
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name,
        file_path=f"/m/{uuid.uuid4().hex}.mkv",
        date_added=BASE + timedelta(minutes=minutes), **kw)
    db.add(row)
    db.flush()
    return row


def _seed_movies_first(db, lib):
    """电影组 date_added 更新（旧顺序电影在前），剧组更旧。

    返回 (series_rows, movie_rows)。每部剧只有 series 行可认领
    （单集被 parent_ready 挡住），每部电影自成一组。
    """
    series_rows = []
    for i in range(3):
        s = _item(db, lib, f"剧{i}", item_type="series", minutes=i)
        _item(db, lib, f"剧{i} E1", item_type="episode", series_id=s.id,
              season_number=1, episode_number=1, minutes=i)
        series_rows.append(s)
    movie_rows = [_item(db, lib, f"电影{i}", minutes=100 + i)
                  for i in range(6)]
    db.commit()
    return series_rows, movie_rows


# ---------- ① 预留：剧优先 + 电影回填 ----------

def test_series_reserve_prioritizes_gate_groups_and_backfills_movies(db, lib):
    series_rows, movie_rows = _seed_movies_first(db, lib)
    claimed = enrich_worker._claim_batch(db, 6)
    assert len(claimed) == 6
    claimed_ids = [r.id for r in claimed]
    # 预留线 = ceil(6 * 50%) = 3：前 3 个必须是剧父级（旧顺序下全是电影）
    assert set(claimed_ids[:3]) == {s.id for s in series_rows}
    # 电影拿到回填名额，不被饿死
    assert set(claimed_ids[3:]) <= {m.id for m in movie_rows}
    assert len(claimed_ids[3:]) == 3


def test_series_reserve_unused_quota_backfills(db, lib):
    # 只有 1 个 gate 组可认领 1 行，预留 3 个名额用不满 -> 回填电影
    s = _item(db, lib, "独剧", item_type="series", minutes=0)
    movies = [_item(db, lib, f"电影{i}", minutes=100 + i) for i in range(6)]
    db.commit()
    claimed = enrich_worker._claim_batch(db, 6)
    assert len(claimed) == 6
    assert claimed[0].id == s.id
    assert {r.id for r in claimed[1:]} == {m.id for m in movies[:5]} or \
        len([r for r in claimed if r.id in {m.id for m in movies}]) == 5


def test_series_reserve_single_group_may_overshoot_reserve(db, lib):
    # 单个 gate 组用当时真实 remaining 认领，可吃过预留线：
    # limit=4、预留=2，gate 组有 series+2 season 共 3 行可认领。
    s = _item(db, lib, "大剧", item_type="series", minutes=0)
    _item(db, lib, "大剧 S1", item_type="season", series_id=s.id,
          season_number=1, minutes=0)
    _item(db, lib, "大剧 S2", item_type="season", series_id=s.id,
          season_number=2, minutes=0)
    movies = [_item(db, lib, f"电影{i}", minutes=100 + i) for i in range(4)]
    db.commit()
    claimed = enrich_worker._claim_batch(db, 4)
    assert len(claimed) == 4
    gate_ids = {r.id for r in claimed if r.item_type in ("series", "season")}
    assert len(gate_ids) == 3, "单组应可认领 3 行、吃过预留线 2"
    assert len([r for r in claimed if r.id in {m.id for m in movies}]) == 1


# ---------- ② pct=0 回到旧顺序 ----------

def test_reserve_pct_zero_matches_old_order(db_factory, monkeypatch):
    outcomes = {}
    for label, pct in (("zero", 0), ("fifty", 50)):
        db = db_factory()
        try:
            lib_row = em.Library(guid=uuid.uuid4().hex, name="测试库",
                                 collection_type="tvshows")
            db.add(lib_row)
            db.flush()
            _seed_movies_first(db, lib_row)
            monkeypatch.setattr(
                enrich_worker, "ENRICH_CLAIM_SERIES_RESERVE_PCT", pct)
            enrich_worker.invalidate_claim_groups_cache()
            claimed = enrich_worker._claim_batch(db, 4)
            outcomes[label] = [r.name for r in claimed]
        finally:
            db.close()
    # 旧口径：电影 date_added 最新，先认领 4 部电影，剧不在批次内
    assert outcomes["zero"] == ["电影5", "电影4", "电影3", "电影2"]
    # 新口径同 fixture：预留 ceil(4*50%)=2 先给剧，再回填电影
    assert outcomes["fifty"][:2] != outcomes["zero"][:2]
    assert set(outcomes["fifty"]) != set(outcomes["zero"])


# ---------- ③ gate 判定 ----------

def test_gate_flags_in_snapshot(db, lib):
    # 父级已 done 的纯单集组：gate=0
    done_series = _item(db, lib, "已完剧", item_type="series",
                        enrich_status="done", minutes=0)
    _item(db, lib, "已完剧 E1", item_type="episode",
          series_id=done_series.id, season_number=1, episode_number=1,
          minutes=0)
    # season pending 的组：gate=1（series 已 done，只剩 season 待刮）
    s2 = _item(db, lib, "待季剧", item_type="series",
               enrich_status="done", minutes=1)
    season = _item(db, lib, "待季剧 S1", item_type="season",
                   series_id=s2.id, season_number=1, minutes=1)
    # 电影组：gate=0
    movie = _item(db, lib, "电影", minutes=2)
    db.commit()
    enrich_worker._claim_batch(db, 10)
    entry = next(iter(enrich_worker._CLAIM_GROUPS_CACHE.values()))
    groups = {gid: gate for gid, _latest, _lib, gate in entry["groups"]}
    assert groups[done_series.id] == 0, "父级已 done 的单集组不许占预留"
    assert groups[s2.id] == 1, "season pending 的组必须是 gate=1"
    assert groups[movie.id] == 0
    assert season.id is not None  # fixture 完整性


def test_gate_zero_episode_group_does_not_take_reserve(db, lib):
    # 纯单集组（gate=0）即使排在剧组前面也不占预留：
    # 电影/单集组更新、剧组更旧，limit=4 时预留先给剧。
    done_series = _item(db, lib, "已完剧", item_type="series",
                        enrich_status="done", minutes=50)
    eps = [_item(db, lib, f"已完剧 E{i}", item_type="episode",
                 series_id=done_series.id, season_number=1,
                 episode_number=i + 1, minutes=50) for i in range(3)]
    s = _item(db, lib, "待刮剧", item_type="series", minutes=0)
    db.commit()
    claimed = enrich_worker._claim_batch(db, 4)
    assert claimed[0].id == s.id, "预留必须先给 gate=1 的剧组"
    assert {r.id for r in claimed[1:]} == {e.id for e in eps}


# ---------- ④ 两段路径一致：快照开 / 关 ----------

def test_series_reserve_snapshot_off_path(db, lib, monkeypatch):
    monkeypatch.setattr(enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
    series_rows, movie_rows = _seed_movies_first(db, lib)
    claimed = enrich_worker._claim_batch(db, 6)
    assert len(claimed) == 6
    claimed_ids = [r.id for r in claimed]
    assert set(claimed_ids[:3]) == {s.id for s in series_rows}
    assert len([i for i in claimed_ids[3:]
                if i in {m.id for m in movie_rows}]) == 3


def test_series_reserve_pct_zero_snapshot_off_path(db, lib, monkeypatch):
    monkeypatch.setattr(enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
    monkeypatch.setattr(enrich_worker, "ENRICH_CLAIM_SERIES_RESERVE_PCT", 0)
    _series, movies = _seed_movies_first(db, lib)
    claimed = enrich_worker._claim_batch(db, 4)
    assert [r.id for r in claimed] == [movies[5].id, movies[4].id,
                                       movies[3].id, movies[2].id]
