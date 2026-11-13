"""PA-039（分层口径）：死单不挡重建，落在确认层兑现，不藏行。

接在已合入的确认分支分流（test_pa039_throttle_failed_rebuild.py）
之后。本文件钉住的分层口径（曾试图在 _recent_user_jobs 一处
滤掉 failed，因与换绑豁免套件的既定分类冲突、CI 实证后撤销）：

- 最新单查询（有效状态）遇 failed 单 → 不指死单
  （_latest_job 的 statuses 参数，查询层）；
- 节流状态层看全量历史：同方案 failed 单仍构成 blocked、
  异方案 failed 单仍是换绑 waiver 证据（与
  test_transfer_rebind_waiver 同口径，终态不改变落点事实）；
- 真正在途 pending / 已成 done 单 → 节流口径不变（不许把节流修没）；
- 「死单不挡重建」原由找源确认层兑现（_throttled_confirm_response）；
  找源已随全删除批 4 整套删除，确认层端到端用例随之移除，本文件
  只保留状态层口径（节流判定本体在 transfer_variant，未动）。

不连生产库。查询层用内存 SQLite 单表。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.emby_server import models as em
from backend.emby_server import transfer_variant as tv

VALID = ("pending", "running", "done")


def _tiny_db():
    engine = create_engine("sqlite://")
    em.TransferJob.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _add(db, item_id, status, hours_ago=1, dest="userdrive:7"):
    db.add(em.TransferJob(
        item_id=item_id, trigger="playback", provider="drive",
        owner_scope="user", owner_ref="7", dest_drive_id=dest,
        status=status, created_at=datetime.now() - timedelta(hours=hours_ago)))
    db.commit()


def test_failed_only_same_scheme_throttle_state_stays_blocked():
    # 状态层口径：同方案 failed 单仍构成节流（与换绑豁免套件
    # test_mixed_recent_jobs_throttle_without_waiver 同口径）。
    # PA-039 的「死单不挡重建」不在状态层藏行，原在找源确认层
    # 兑现（_throttled_confirm_response）；找源已随全删除批 4 删除，
    # 确认层端到端用例随之移除，状态层口径仍由本文件钉住。
    db = _tiny_db()
    _add(db, 42, "failed")
    state, from_scheme = tv._user_throttle_state(db, 42, 7, "userdrive:7")
    assert state == "blocked"
    assert from_scheme == ""
    with pytest.raises(tv.TransferError) as excinfo:
        tv._raise_if_user_throttled(db, 42, 7, "userdrive:7")
    assert excinfo.value.kind == "throttled"


def test_failed_only_latest_valid_is_none_but_default_unchanged():
    db = _tiny_db()
    _add(db, 42, "failed")
    assert tv._latest_job(db, 42, "user", "7", statuses=VALID) is None
    # 默认不限状态：首播门禁/商店仍能看到 failed 行做各自的分流
    assert tv._latest_job(db, 42, "user", "7").status == "failed"


def test_live_plus_failed_latest_valid_points_at_live():
    db = _tiny_db()
    _add(db, 42, "pending", hours_ago=2)
    _add(db, 42, "failed", hours_ago=1)
    latest = tv._latest_job(db, 42, "user", "7", statuses=VALID)
    assert latest is not None and latest.status == "pending"


def test_pending_and_done_still_throttle():
    db = _tiny_db()
    _add(db, 42, "pending")
    state, _ = tv._user_throttle_state(db, 42, 7, "userdrive:7")
    assert state == "blocked"
    with pytest.raises(tv.TransferError) as excinfo:
        tv._raise_if_user_throttled(db, 42, 7, "userdrive:7")
    assert excinfo.value.kind == "throttled"

    db = _tiny_db()
    _add(db, 42, "done")
    state, _ = tv._user_throttle_state(db, 42, 7, "userdrive:7")
    assert state == "blocked"


def test_failed_only_other_scheme_is_rebind_waiver():
    # 只有死单、且落在另一方案：仍是换绑场景（与豁免套件
    # test_create_cross_job_waiver_direct 同口径——旧方案单的
    # 终态不改变其落点事实）。状态判定本身不记账：豁免行只在
    # 建单成功时由 _record_throttle_waiver 落，额度不被空耗。
    # （waiver 分支要查豁免计数表，本用例单独建两张表。）
    engine = create_engine("sqlite://")
    em.TransferJob.__table__.create(engine)
    em.TransferThrottleWaiver.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    _add(db, 42, "failed", dest="pan115:9")
    state, from_scheme = tv._user_throttle_state(db, 42, 7, "userdrive:7")
    assert state == "waiver"
    assert from_scheme == "pan115:"
    use_waiver, _ = tv._raise_if_user_throttled(db, 42, 7, "userdrive:7")
    assert use_waiver is True

