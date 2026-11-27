"""认领候选组聚合缓存（减负 A1）回归兜底。

生产 pg_stat_statements 实证（2026-11-12 审计）：``_claim_batch`` 的
``coalesce(series_id,id) + max(date_added)`` GROUP BY 聚合四变体合计约
29 万次调用、单次 1.2~1.6 秒，是全站第一烧 CPU 的查询——16~32 个工人
线程每轮认领都把全部 pending 重新聚合一遍，而积压集合分钟级才有变化。

钉死的新口径：
① TTL 内（``ENRICH_CLAIM_GROUPS_TTL_SEC``，缺省 300 秒，S3 提速）同进程同口径的
   认领复用候选组快照，不再重复打聚合；快照只决定「先看哪些组」，
   组员仍走实时谓词 + 原子认领，抢不重、抢不丢；
② 入库事件同进程即时失效：新 pending 条目插入 / 条目转回 pending /
   父级（series/season）状态或合并关系变化 / 修复请求新到——下一轮
   立刻重新聚合，不等 TTL；
③ 缓存开与关（TTL=0）认领集合逐轮等价；
④ 已被整组认领完的组在快照内标记 consumed，不再逐轮重试其组员查询；
   恰好装满批次的组不标（可能还有余量）。
"""
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models as base_models
from backend.emby_server import enrich_worker
from backend.emby_server import models as em


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    base_models.Base.metadata.create_all(eng)
    em.Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture()
def agg_counter(engine):
    """统计候选组聚合（带 coalesce 的 GROUP BY）的真实执行次数。"""
    counts = {"agg": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "coalesce" in low and "group by" in low:
            counts["agg"] += 1

    return counts


@pytest.fixture()
def lib(db):
    row = em.Library(guid=uuid.uuid4().hex, name="测试库",
                     collection_type="tvshows")
    db.add(row)
    db.flush()
    return row


def _item(db, lib, name, item_type="movie", **kw):
    kw.setdefault("enrich_status", "pending")
    row = em.MediaItem(
        guid=uuid.uuid4().hex, library_id=lib.id, item_type=item_type,
        name=name, sort_name=name,
        file_path=f"/m/{uuid.uuid4().hex}.mkv", **kw)
    db.add(row)
    db.flush()
    return row


def _movies(db, lib, n, prefix="电影"):
    rows = [_item(db, lib, f"{prefix}{i}") for i in range(n)]
    db.commit()
    return rows


# ---------- ① TTL 内复用快照，不重复聚合 ----------

def test_second_claim_reuses_group_snapshot(db, lib, agg_counter):
    _movies(db, lib, 10)
    first = enrich_worker._claim_batch(db, 4)
    assert len(first) == 4
    assert agg_counter["agg"] == 1
    second = enrich_worker._claim_batch(db, 4)
    assert len(second) == 4
    assert agg_counter["agg"] == 1, "TTL 内第二轮不许再打全量聚合"
    assert {r.id for r in first}.isdisjoint({r.id for r in second})


def test_snapshot_groups_consumed_not_retried(db, lib, agg_counter):
    # 5 部电影 = 5 个单行组，date_added 显式递增 → 快照顺序确定为
    # 「最新在前」：rows[4], rows[3], rows[2], rows[1], rows[0]。
    # 第一轮 limit=2：rows[4] 组认领 1 行 < 当时余量 2 → 已抽干，
    # 标 consumed；rows[3] 组认领 1 行 == 当时余量 1（恰好装满）
    # → 不许标（判据见 _claim_batch 快照路径注释）。
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1)
    rows = [_item(db, lib, f"电影{i}",
                  date_added=base + timedelta(minutes=i)) for i in range(5)]
    db.commit()
    first = enrich_worker._claim_batch(db, 2)
    assert [r.id for r in first] == [rows[4].id, rows[3].id]
    entry = next(iter(enrich_worker._CLAIM_GROUPS_CACHE.values()))
    assert entry["consumed"] == {rows[4].id}
    # 第二轮：rows[3] 组先被重试（组员已 enriching → 0 行后转 consumed），
    # 再往后认领 rows[2]、rows[1]；全程不重新聚合。
    second = enrich_worker._claim_batch(db, 2)
    assert [r.id for r in second] == [rows[2].id, rows[1].id]
    assert agg_counter["agg"] == 1


# ---------- ② 入库事件即时失效 ----------

def test_new_pending_insert_invalidates_snapshot(db, lib, agg_counter):
    _movies(db, lib, 3)
    first = enrich_worker._claim_batch(db, 2)
    assert len(first) == 2
    assert agg_counter["agg"] == 1
    new_row = _item(db, lib, "新入库")
    db.commit()
    # 新条目同进程立即可见：下一轮重新聚合（计数 +1）且新条目被认领
    second = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2
    assert new_row.id in {r.id for r in second}


def test_series_done_invalidates_snapshot(db, lib, agg_counter):
    series = _item(db, lib, "剧", item_type="series")
    ep = _item(db, lib, "剧 E1", item_type="episode", series_id=series.id,
               season_number=1, episode_number=1)
    db.commit()
    first = enrich_worker._claim_batch(db, 10)
    assert [r.id for r in first] == [series.id]  # 父级未终态，单集被挡
    assert agg_counter["agg"] == 1
    series_row = db.query(em.MediaItem).filter(
        em.MediaItem.id == series.id).first()
    series_row.enrich_status = "done"
    db.commit()
    second = enrich_worker._claim_batch(db, 10)
    assert [r.id for r in second] == [ep.id]
    assert agg_counter["agg"] == 2, "父级终态变化必须让快照失效重聚合"


# ---------- ③ 缓存开/关等价 ----------

def _seed_mixed(db, lib):
    series = _item(db, lib, "剧", item_type="series")
    _item(db, lib, "剧 S1", item_type="season", series_id=series.id,
          season_number=1)
    _item(db, lib, "剧 E1", item_type="episode", series_id=series.id,
          season_number=1, episode_number=1)
    _item(db, lib, "剧 E2", item_type="episode", series_id=series.id,
          season_number=1, episode_number=2)
    movies = [_item(db, lib, f"电影{i}") for i in range(6)]
    db.commit()
    return series, movies


def test_cache_on_off_claim_equivalence(db_factory, monkeypatch):
    results = {}
    for mode in ("on", "off"):
        db = db_factory()
        try:
            lib = em.Library(guid=uuid.uuid4().hex, name="测试库",
                             collection_type="tvshows")
            db.add(lib)
            db.flush()
            _seed_mixed(db, lib)
            if mode == "off":
                monkeypatch.setattr(
                    enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
            else:
                monkeypatch.setattr(
                    enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 30)
            enrich_worker.invalidate_claim_groups_cache()
            rounds = []
            for _ in range(3):
                claimed = enrich_worker._claim_batch(db, 3)
                rounds.append(sorted(r.name for r in claimed))
            results[mode] = rounds
        finally:
            db.close()
    assert results["on"] == results["off"]


# ---------- TTL=0 回到旧路径 ----------

def test_ttl_zero_aggregates_every_round(db, lib, agg_counter, monkeypatch):
    monkeypatch.setattr(enrich_worker, "ENRICH_CLAIM_GROUPS_TTL_SEC", 0)
    _movies(db, lib, 6)
    enrich_worker._claim_batch(db, 2)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "TTL=0 必须逐字走旧路径：每轮都聚合"


# ---------- S3：TTL 默认 300 + worker 自写不作废 ----------

def test_claim_groups_ttl_default_is_300():
    assert enrich_worker.ENRICH_CLAIM_GROUPS_TTL_SEC == 300


def test_self_write_series_done_does_not_invalidate(db, lib, agg_counter):
    series = _item(db, lib, "剧", item_type="series")
    _item(db, lib, "剧 E1", item_type="episode", series_id=series.id,
          season_number=1, episode_number=1)
    _movies(db, lib, 6)
    first = enrich_worker._claim_batch(db, 2)
    assert len(first) == 2
    assert agg_counter["agg"] == 1
    # worker 处理段内：series pending -> done 的自写 flush 不作废快照
    with enrich_worker._claim_snapshot_self_write():
        row = db.query(em.MediaItem).filter(em.MediaItem.id == series.id).first()
        # series 可能已被第一轮认领为 enriching；无论原状态如何，
        # 置 done 都属于「series enrich_status 变为非 pending」
        row.enrich_status = "done"
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1, "自写标记内 series->done 不许作废快照"


def test_self_write_new_pending_insert_invalidates(db, lib, agg_counter):
    _movies(db, lib, 6)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        new_row = _item(db, lib, "自写期新入库")
        db.commit()
    second = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2, "自写标记内新 pending 插入仍须即时失效"
    assert new_row.id in {r.id for r in second}


def test_self_write_back_to_pending_invalidates(db, lib, agg_counter):
    done_row = _item(db, lib, "已完成", enrich_status="done")
    _movies(db, lib, 6)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        row = db.query(em.MediaItem).filter(em.MediaItem.id == done_row.id).first()
        row.enrich_status = "pending"
        db.commit()
    second = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2, "自写标记内打回 pending 仍须即时失效"
    assert done_row.id in {r.id for r in second}


def test_self_write_repair_request_invalidates(db, lib, agg_counter):
    from datetime import datetime
    target = _item(db, lib, "待修复", enrich_status="done")
    _movies(db, lib, 6)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        row = db.query(em.MediaItem).filter(em.MediaItem.id == target.id).first()
        row.repair_requested_at = datetime.now()
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "自写标记内新修复请求仍须即时失效"


def test_outside_self_write_series_done_still_invalidates(db, lib, agg_counter):
    series = _item(db, lib, "剧2", item_type="series")
    _item(db, lib, "剧2 E1", item_type="episode", series_id=series.id,
          season_number=1, episode_number=1)
    db.commit()
    enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 1
    row = db.query(em.MediaItem).filter(em.MediaItem.id == series.id).first()
    row.enrich_status = "done"
    db.commit()
    enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2, "标记外 series 状态变化仍须失效（旧行为）"


def test_self_write_bulk_update_non_pending_does_not_invalidate(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        (db.query(em.MediaItem)
         .filter(em.MediaItem.id == movies[5].id)
         .update({"enrich_status": "done"}, synchronize_session=False))
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1, "自写标记内批量 UPDATE 非 pending 不许作废"


def test_self_write_bulk_update_no_status_does_not_invalidate(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        (db.query(em.MediaItem)
         .filter(em.MediaItem.id == movies[5].id)
         .update({"name": "改名"}, synchronize_session=False))
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1, "自写标记内未设置 enrich_status 的批量 UPDATE 不许作废"


def test_self_write_bulk_update_to_pending_invalidates(db, lib, agg_counter):
    done_row = _item(db, lib, "批量已完成", enrich_status="done")
    _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        (db.query(em.MediaItem)
         .filter(em.MediaItem.id == done_row.id)
         .update({"enrich_status": "pending"}, synchronize_session=False))
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "自写标记内批量打回 pending 仍须失效"


def test_self_write_bulk_delete_invalidates(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    with enrich_worker._claim_snapshot_self_write():
        (db.query(em.MediaItem)
         .filter(em.MediaItem.id == movies[5].id)
         .delete(synchronize_session=False))
        db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "自写标记内批量 DELETE 保守仍须失效"


# ---------- L-012：流式失效防抖合并（扫描期入库 flush 不再逐次作废快照） ----------

def test_streaming_invalidations_coalesce_to_one_rebuild(
        db, lib, agg_counter, monkeypatch):
    """扫描期形态量化复现：认领间隔里连续多次入库失效，只许一次重建。

    改前（硬失效）：每次入库 flush 都 gen+1，下一轮认领必重打聚合——
    两次插入后认领聚合计数即 +1（本用例改前在此断言处红）。
    改后：孤立失效仍立即生效；一旦失效在上一份待生效失效未被认领
    消费前再次到达（流式），进入防抖窗口，窗口内认领复用旧快照，
    聚合执行次数不增；窗口过后重建一次，新条目全部被认领（不漏）。
    """
    monkeypatch.setattr(
        enrich_worker, "ENRICH_CLAIM_GROUPS_MIN_REFRESH_SEC", 3600)
    _movies(db, lib, 10)
    first = enrich_worker._claim_batch(db, 2)
    assert len(first) == 2
    assert agg_counter["agg"] == 1
    # 模拟扫描期：两次独立入库（各自 commit），失效连续到达
    row_a = _item(db, lib, "扫描入库A")
    db.commit()
    row_b = _item(db, lib, "扫描入库B")
    db.commit()
    # 防抖窗口内认领：复用旧快照，不重打聚合；新组不在旧快照里，
    # 本轮只认领旧快照组（陈旧度 ≤ 既有 TTL 设计已接受的范围）
    second = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 1, (
        "流式失效未被合并：窗口内认领又重打了一次全量聚合")
    claimed_ids = {r.id for r in second}
    assert row_a.id not in claimed_ids
    assert row_b.id not in claimed_ids
    assert claimed_ids.isdisjoint({r.id for r in first}), "不许双认领"
    # 窗口过后（把最小刷新间隔调到 0 等价于窗口已过）：重建一次，
    # 两条新入库都被认领——防抖只许延迟，不许漏认领
    monkeypatch.setattr(
        enrich_worker, "ENRICH_CLAIM_GROUPS_MIN_REFRESH_SEC", 0)
    third = enrich_worker._claim_batch(db, 10)
    assert agg_counter["agg"] == 2
    third_ids = {r.id for r in third}
    assert row_a.id in third_ids
    assert row_b.id in third_ids


def test_min_refresh_zero_restores_immediate_invalidation(
        db, lib, agg_counter, monkeypatch):
    """防抖关（=0）时逐字回到旧口径：流式失效也逐次即时重建。"""
    monkeypatch.setattr(
        enrich_worker, "ENRICH_CLAIM_GROUPS_MIN_REFRESH_SEC", 0)
    _movies(db, lib, 10)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    _item(db, lib, "关防抖入库A")
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2
    _item(db, lib, "关防抖入库B")
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 3, "MIN_REFRESH=0 必须逐轮即时失效重建"


def test_min_refresh_default_is_60():
    assert enrich_worker.ENRICH_CLAIM_GROUPS_MIN_REFRESH_SEC == 60


def test_rebuild_on_one_key_refreshes_other_key_consumed(
        db, lib, agg_counter):
    """跨键快照陈旧隐患（L-012 防抖回归）：父级未终态时该组不进
    fill 快照的认领范围；父级转终态后必须能被 fill 认领——identify
    键的重建消费待生效失效时须 bump 代际，让 fill 键下轮懒重建；
    否则 fill 会拿着旧快照把组跳过到 TTL 到期（单集迟迟不进 fill
    队列）。旧硬失效口径靠整表清空天然没此问题。"""
    series = _item(db, lib, "跨键剧", item_type="series")
    fill_ep = _item(db, lib, "跨键剧 E1", item_type="episode",
                    series_id=series.id, season_number=1, episode_number=1,
                    tmdb_id="1399")
    db.commit()
    # fill 先行：父级未终态，单集被挡（组级父级门在 groups_q 里，
    # 该组不进 fill 快照的认领范围），fill 认领为空
    assert enrich_worker._claim_batch(db, 10, queue="fill") == []
    # identify 认领父级
    first = enrich_worker._claim_batch(db, 10, queue="identify")
    assert [r.id for r in first] == [series.id]
    agg_after_claims = agg_counter["agg"]
    # 父级转终态（标记外写入 → 待生效失效）
    row = db.query(em.MediaItem).filter(em.MediaItem.id == series.id).first()
    row.enrich_status = "done"
    db.commit()
    enrich_worker._claim_batch(db, 10, queue="identify")
    assert agg_counter["agg"] > agg_after_claims, "identify 键应已重建"
    # identify 的重建消费失效时必须 bump 代际，fill 键下轮重建（聚合
    # 计数再 +1）且单集可认领；若 fill 拿旧快照，组不在其中、单集
    # 会被跳过到 TTL 到期
    agg_before_fill = agg_counter["agg"]
    fill = enrich_worker._claim_batch(db, 10, queue="fill")
    assert agg_counter["agg"] > agg_before_fill, "fill 键应已随代际 bump 重建"
    assert [r.id for r in fill] == [fill_ep.id], (
        "fill 键旧快照未随失效刷新，单集被跳过")


# ---------- C-01（2026-11-28 优化扫荡）：标记外批量 UPDATE 按列收窄作废 ----------
# 深读结论：_bulk_watch 旧口径对 emby_items 的任何批量 UPDATE/DELETE 都记
# 失效（认领三件套除外），而同进程的探测抢单（每约 3.3 秒一次、只写
# probe_* 列）、探测回填（video_codec 等）都在持续触发——快照在稳态下被
# 这些无关写打回重建（防抖后约每 60 秒一组键重建一次 + 代际 bump 连带
# 其他键），全量聚合的 28 个 external_ids LIKE + 父级 EXISTS 谓词又不可
# 索引，积压大时单次重建 1.2~1.9 秒。收窄口径：批量 UPDATE 只动与认领
# 无关的列 → 不作废；动了认领相关列（状态/重试/优先级/修复请求/锁/解绑/
# 合并/软删/父子归属/类型/库/入库时间/身份三件）→ 照旧作废；DELETE 与
# values 读不到时保守照旧作废。

def test_bulk_update_probe_columns_does_not_invalidate(db, lib, agg_counter):
    from datetime import datetime
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    # 探测抢单的同款批量 UPDATE（只写 probe_status / probe_claimed_at）
    (db.query(em.MediaItem)
     .filter(em.MediaItem.id == movies[5].id)
     .update({"probe_status": "probing", "probe_claimed_at": datetime.now()},
             synchronize_session=False))
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1, "只动探测列的批量 UPDATE 不许作废补全快照"


def test_bulk_update_probe_result_columns_does_not_invalidate(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    # 探测完成回填的同款批量 UPDATE（编码/时长/探测时间）
    (db.query(em.MediaItem)
     .filter(em.MediaItem.id == movies[5].id)
     .update({"video_codec": "hevc", "duration_ticks": 123456,
              "last_probed_at": None},
             synchronize_session=False))
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1, "只动探测结果列的批量 UPDATE 不许作废补全快照"


def test_bulk_update_back_to_pending_invalidates_outside_self_write(
        db, lib, agg_counter):
    done_row = _item(db, lib, "批量已完成", enrich_status="done")
    _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    # janitor 回捞的同款批量 UPDATE（打回 pending）——PR #79 的事故面，
    # 收窄后仍必须失效
    (db.query(em.MediaItem)
     .filter(em.MediaItem.id == done_row.id)
     .update({"enrich_status": "pending"}, synchronize_session=False))
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "批量打回 pending 仍须失效（回捞行要能被认领）"


def test_bulk_update_series_link_invalidates(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    # 改父子归属会改变组键（coalesce(series_id, id)），仍须失效
    (db.query(em.MediaItem)
     .filter(em.MediaItem.id == movies[5].id)
     .update({"series_id": movies[0].id}, synchronize_session=False))
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "改 series_id 的批量 UPDATE 仍须失效"


def test_bulk_delete_invalidates_outside_self_write(db, lib, agg_counter):
    movies = _movies(db, lib, 8)
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 1
    (db.query(em.MediaItem)
     .filter(em.MediaItem.id == movies[5].id)
     .delete(synchronize_session=False))
    db.commit()
    enrich_worker._claim_batch(db, 2)
    assert agg_counter["agg"] == 2, "批量 DELETE 保守仍须失效"
