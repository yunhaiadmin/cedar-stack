# -*- coding: utf-8 -*-
"""合并副本 enrich 状态结算回归（2026-11-24）。

根因：``_claim_batch`` 的认领谓词带 ``not_merged``（merged_into_id 非空
的行永不认领，设计如此——合并副本元数据走主记录），但合并写入方给行打
``merged_into_id`` 时不清 ``enrich_status``：行当时是 pending 就永远卡
pending（生产实证 27,283 条 episode 全 merged+pending、attempts=0、
2026-11-22 重建导入后从未被认领）。

本文件钉住：
(a) 合并写入点结算：pending/enriching 行被合并 → enrich_status 变 done
    （episode_versions 合并主路径 + soft_delete 改指/晋升）；
(b) janitor 自愈巡检 ``merged_pending_patrol``：结算 merged+pending 行、
    不碰未合并的 pending 行；
(c) 解合并/晋升恢复独立：行无自有元数据证据 → 回 pending 等重刮；
    有自有证据 → 保持 done。判据（merge_enrich_sync.has_own_metadata）：
    last_scraped_at 非空，或有自有 poster（poster_path/primary_image_url），
    或有 overview，或 metadata_source 为真实来源（tmdb/nfo/douban/
    inherit 等，非空且非 'none'）；metadata_locked 行豁免不翻。
(d) 物理删除兜底 detach_merged_children 解开合并时同口径处理。
"""

import os
import tempfile

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
_fd, _p = tempfile.mkstemp(suffix=".db")
os.close(_fd)

import uuid  # noqa: E402
from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

from backend import database as _dbmod  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.emby_server import enrich_worker  # noqa: E402
from backend.emby_server import episode_versions as ev  # noqa: E402
from backend.emby_server import merge_enrich_sync as mes  # noqa: E402
from backend.emby_server import merge_versions_worker as mvw  # noqa: E402
from backend.emby_server import models as em  # noqa: E402
from backend.emby_server import soft_delete  # noqa: E402
from sqlalchemy import create_engine as _ce  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _hygiene_module_engine():
    """测试卫生：本文件独占临时库在模块夹具内换绑、模块结束原样还原
    （与 test_enrich_fake_done_guard 同款，防全局 engine 污染后续文件）。"""
    _hyg_saved_engine = _dbmod.engine
    _hyg_saved_factory = _dbmod.SessionLocal._factory
    _hyg_saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{_p}"
    _dbmod.engine = _ce(os.environ["DATABASE_URL"])
    _dbmod.configure_session_local(_sm(bind=_dbmod.engine))
    init_db()
    _hyg_engine = _dbmod.engine
    try:
        yield
    finally:
        _dbmod.engine = _hyg_saved_engine
        _dbmod.configure_session_local(_hyg_saved_factory)
        if _hyg_saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = _hyg_saved_url
        _hyg_engine.dispose()


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        s.query(em.MediaStream).delete()
        s.query(em.MediaItem).delete()
        s.commit()
        yield s
    finally:
        s.close()


def _item(db, **kw):
    it = em.MediaItem(
        guid=uuid.uuid4().hex,
        library_id=kw.pop("library_id", 1),
        item_type=kw.pop("item_type", "episode"),
        name=kw.pop("name", "S01E01"),
        file_path=kw.pop("file_path", "/x/S01E01.strm"),
        enrich_status=kw.pop("enrich_status", "pending"),
        **kw,
    )
    db.add(it)
    db.commit()
    return it


# ---------- helper 判据矩阵 ----------

def test_settle_only_pending_and_enriching():
    class R:
        pass

    for before, after in [("pending", "done"), ("enriching", "done"),
                          ("done", "done"), ("failed", "failed")]:
        r = R()
        r.enrich_status = before
        changed = mes.settle_merged_row(r)
        assert r.enrich_status == after
        assert changed == (before in ("pending", "enriching"))


def test_restore_criterion_matrix():
    class R:
        enrich_attempts = 3
        enrich_next_retry_at = datetime.now()
        last_scraped_at = None
        poster_path = None
        primary_image_url = None
        overview = None
        metadata_source = None
        metadata_locked = False

    # 无任何自有证据的 done → 回 pending，attempts/重试一并清零
    r = R()
    r.enrich_status = "done"
    assert mes.restore_unmerged_row(r) is True
    assert r.enrich_status == "pending"
    assert r.enrich_attempts == 0
    assert r.enrich_next_retry_at is None

    # 任一证据在 → 保持 done
    for field, value in [("last_scraped_at", datetime.now()),
                         ("poster_path", "/data/p.jpg"),
                         ("primary_image_url", "https://img/x.jpg"),
                         ("overview", "有简介"),
                         ("metadata_source", "inherit"),
                         ("metadata_source", "tmdb")]:
        r = R()
        r.enrich_status = "done"
        setattr(r, field, value)
        assert mes.restore_unmerged_row(r) is False, field
        assert r.enrich_status == "done"

    # metadata_source='none' 不算证据
    r = R()
    r.enrich_status = "done"
    r.metadata_source = "none"
    assert mes.restore_unmerged_row(r) is True

    # 锁定行豁免：转人工的行不许被巡检/解合并口径翻回 pending
    r = R()
    r.enrich_status = "done"
    r.metadata_locked = True
    assert mes.restore_unmerged_row(r) is False
    assert r.enrich_status == "done"

    # 本来就不是 done → 不动
    r = R()
    r.enrich_status = "pending"
    assert mes.restore_unmerged_row(r) is False
    assert r.enrich_status == "pending"


# ---------- (a) 合并写入点结算 ----------

def test_merge_pending_episode_settles_done(db):
    lo = _item(db, height=720, size=100, enrich_status="pending",
               file_path="/x/S01E01 - 720p.strm",
               series_id=100, season_number=1, episode_number=1)
    hi = _item(db, height=2160, size=200, enrich_status="pending",
               file_path="/x/S01E01 - 2160p.strm",
               series_id=100, season_number=1, episode_number=1)
    stats = ev.merge_episode_versions(db, library_id=1)
    db.commit()
    assert stats["merged"] == 1
    db.refresh(lo)
    db.refresh(hi)
    assert lo.merged_into_id == hi.id
    # 被合并的 pending 行必须当场结算 done，否则认领谓词 not_merged
    # 让它永远卡 pending（本 bug 的核心复现）
    assert lo.enrich_status == "done"
    # 主记录不被合并结算动到
    assert hi.merged_into_id is None
    assert hi.enrich_status == "pending"


def test_merge_enriching_episode_settles_done(db):
    lo = _item(db, height=720, size=100, enrich_status="enriching",
               file_path="/x/S01E01 - 720p.strm",
               series_id=100, season_number=1, episode_number=1)
    hi = _item(db, height=2160, size=200, enrich_status="pending",
               file_path="/x/S01E01 - 2160p.strm",
               series_id=100, season_number=1, episode_number=1)
    ev.merge_episode_versions(db, library_id=1)
    db.commit()
    db.refresh(lo)
    assert lo.merged_into_id == hi.id
    assert lo.enrich_status == "done"


def test_rehome_promote_and_repoint_settle(db):
    """soft_delete 改指守卫：晋升的分身无自有证据 → 回 pending；
    改指到新主的分身（pending）→ 结算 done。"""
    root = _item(db, item_type="series", name="正主剧", enrich_status="done",
                 file_path="")
    c_done = _item(db, name="分身甲", enrich_status="done",
                   merged_into_id=root.id, series_id=100,
                   season_number=1, episode_number=1)
    c_pend = _item(db, name="分身乙", enrich_status="pending",
                   merged_into_id=root.id, series_id=100,
                   season_number=1, episode_number=2)
    action = soft_delete.rehome_merged_children(db, root)
    db.commit()
    assert action == "promoted-child"
    db.refresh(c_done)
    db.refresh(c_pend)
    promoted = c_done if c_done.merged_into_id is None else c_pend
    repointed = c_pend if promoted is c_done else c_done
    assert repointed.merged_into_id == promoted.id
    # 晋升为独立行：无论原状态是「被结算的 done」还是 pending，
    # 无自有元数据证据就必须能被重新认领
    assert promoted.enrich_status == "pending"
    # 改指行仍是副本：pending 的当场结算 done，不再卡住
    assert repointed.enrich_status == "done"


# ---------- (b) janitor 自愈巡检 ----------

def test_merged_pending_patrol_settles_only_merged(db):
    primary = _item(db, enrich_status="pending")
    m1 = _item(db, enrich_status="pending", merged_into_id=primary.id)
    m2 = _item(db, enrich_status="pending", merged_into_id=primary.id)
    free_pending = _item(db, enrich_status="pending")
    m_done = _item(db, enrich_status="done", merged_into_id=primary.id)
    n = enrich_worker.merged_pending_patrol(db)
    assert n == 2
    for it in (m1, m2, free_pending, m_done, primary):
        db.refresh(it)
    assert m1.enrich_status == "done"
    assert m2.enrich_status == "done"
    # 未合并的 pending 是正常待刮队列，巡检绝不能碰
    assert free_pending.enrich_status == "pending"
    assert primary.enrich_status == "pending"
    assert m_done.enrich_status == "done"
    # 幂等：第二轮无可结算
    assert enrich_worker.merged_pending_patrol(db) == 0


# ---------- (c) 解合并恢复独立 ----------

def test_unmerge_without_own_metadata_back_to_pending(db):
    primary = _item(db, enrich_status="done")
    copy = _item(db, enrich_status="done", merged_into_id=primary.id,
                 enrich_attempts=5)
    assert mvw.unmerge_version(db, copy.id) is True
    db.commit()
    db.refresh(copy)
    assert copy.merged_into_id is None
    assert copy.enrich_status == "pending"
    assert copy.enrich_attempts == 0


def test_unmerge_with_own_metadata_stays_done(db):
    primary = _item(db, enrich_status="done")
    copy = _item(db, enrich_status="done", merged_into_id=primary.id,
                 overview="自己的简介", last_scraped_at=datetime.now(),
                 metadata_source="inherit")
    assert mvw.unmerge_version(db, copy.id) is True
    db.commit()
    db.refresh(copy)
    assert copy.merged_into_id is None
    assert copy.enrich_status == "done"


def test_orphan_unmerge_back_to_pending(db):
    # 目标行用批量 UPDATE 打软删标记：直接种 deleted_at 的 ORM 对象在
    # commit 后属性过期重载会被可见性钩子挡住（ObjectDeletedError）
    dead = _item(db, enrich_status="done")
    dead_id = dead.id
    db.query(em.MediaItem).filter(em.MediaItem.id == dead_id).update(
        {"deleted_at": datetime.now()}, synchronize_session=False)
    db.commit()
    db.expunge_all()
    orphan = _item(db, enrich_status="done", merged_into_id=dead_id,
                   series_id=100, season_number=1, episode_number=1)
    orphan_id = orphan.id
    stats = ev.merge_episode_versions(db, library_id=1)
    db.commit()
    assert stats["unmerged_orphans"] == 1
    row = db.query(em.MediaItem).filter(em.MediaItem.id == orphan_id).first()
    assert row.merged_into_id is None
    assert row.enrich_status == "pending"


# ---------- (d) 物理删除兜底 detach ----------

def test_detach_restores_independence_with_rescrape(db):
    primary = _item(db, enrich_status="done")
    no_ev = _item(db, enrich_status="done", merged_into_id=primary.id)
    has_ev = _item(db, enrich_status="done", merged_into_id=primary.id,
                   overview="自己的简介",
                   last_scraped_at=datetime.now(),
                   metadata_source="tmdb")
    n = soft_delete.detach_merged_children(db, [primary.id])
    db.commit()
    assert n == 2
    db.refresh(no_ev)
    db.refresh(has_ev)
    assert no_ev.merged_into_id is None
    assert no_ev.enrich_status == "pending"
    assert has_ev.merged_into_id is None
    assert has_ev.enrich_status == "done"
