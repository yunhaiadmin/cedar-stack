# -*- coding: utf-8 -*-
"""公共贡献池 PR-6（goal-33）：激励结算执行器 + 管理端结算口。

口径（design §11 + 任务书定版，2026-11-15 激励改发美金）：
- 结算先影子跑：真发开关 pool_reward_payout_enabled 默认关——
  只算账落结算行（status=shadow）、不入账；开关开后
  重跑才真入账，且连跑不双发（明细唯一键 + 钱包幂等键双锚）；
- 金额（美元，Decimal 精确累计）：每次 counted 播放 $0.005
  （pool_reward_usd_per_play）＋ 每在线日 $0.025
  （pool_reward_usd_per_active_day）；入账按钱包账本精度
  （美分，ROUND_HALF_UP）取整；
- 月封顶跟套餐走：每人每月入账上限 = 本人当前付费套餐月价 ×
  比例（pool_reward_cap_ratio，默认 1）；无生效付费套餐按在售
  最低档月价兜底；超出不入账、不结转；
- 门槛双条件（在线 ≥7 天且 counted ≥10）不达 → skipped 不结；
- 单日 counted 达冻结线 → frozen 待审，管理员放行后按计量数发放；
- 违规清退（ejected + 违规标志在位）该期不结，标志清除后重跑可结；
- 子账号（parent_user_id 非空）不参与贡献结算 → skipped；
- 激励总开关 pool_reward_enabled 关 → 不建批（端点 400）；
- 称号「片库贡献者」状态驱动：active 出、suspended 不出。
周期口径：2026-11（上海自然月），界（UTC naive）=
[2026-10-31 16:00, 2026-11-30 16:00)。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from decimal import Decimal

from backend import config_service, models as base_models
from backend import wallet as wallet_svc
from backend.api.admin_core import get_current_admin
from backend.database import get_db
from backend.emby_server import models as em
from backend.emby_server import pool_gate, pool_reward
from backend.integrations import store

PERIOD = "2026-11"
P_START = datetime(2026, 10, 31, 16, 0)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_store_cache():
    yield
    store.invalidate()


def _set_cfg(db, **values):
    config_service.save_config(db, values, schema="pool_reward")
    db.commit()


def _user(db, name):
    user = base_models.WebUser(username=name, password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _member(db, user_id, status="active", **kw):
    row = em.PoolMember(
        user_id=user_id, status=status,
        joined_at=P_START - timedelta(days=1),
        status_changed_at=P_START - timedelta(days=1), **kw)
    db.add(row)
    db.commit()
    db.refresh(row)
    # H2 流水：周期开始前已 active（除非造数另有安排）。
    if status == "active":
        db.add(em.PoolMemberStatusEvent(
            member_id=row.id, from_status=None, to_status="active",
            reason="approve", created_at=P_START - timedelta(hours=1)))
        db.commit()
    return row


def _plays(db, member_id, count, start=datetime(2026, 11, 5, 2, 0),
           viewer0=1000, variant=555):
    for i in range(count):
        db.add(em.PoolPlayEvent(
            member_id=member_id, variant_id=variant, item_id=42,
            viewer_user_id=viewer0 + i,
            created_at=start + timedelta(minutes=i)))
    db.commit()


def _plan(db, name, price, days=30, active=True):
    plan = base_models.SubscriptionPlan(
        name=name, price=Decimal(str(price)), duration_days=days,
        is_active=active)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _subscription(db, user_id, plan_id):
    sub = base_models.UserSubscription(
        user_id=user_id, plan_id=plan_id, status="active",
        start_date=datetime(2026, 10, 1),
        end_date=datetime(2027, 6, 1))
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _balance(db, user_id):
    return wallet_svc.get_balance(db, user_id)


def _ledger_rows(db, user_id, kind="pool_reward"):
    return (db.query(base_models.WalletLedger)
            .filter(base_models.WalletLedger.user_id == user_id,
                    base_models.WalletLedger.kind == kind)
            .all())


def _detail(db, member_id):
    return (db.query(em.PoolRewardDetail)
            .filter(em.PoolRewardDetail.member_id == member_id)
            .order_by(em.PoolRewardDetail.id.desc()).first())





# ------------------------------------------------------------ 配置面


def test_reward_config_defaults_and_pool_config_untouched(db):
    cfg = pool_gate.reward_config_payload(db)
    assert cfg["pool_reward_enabled"] is False
    assert cfg["pool_reward_payout_enabled"] is False
    assert cfg["pool_reward_period"] == "monthly"
    # 美金额键（字符串落库、读侧 Decimal 解析）：定版默认费率。
    assert Decimal(str(cfg["pool_reward_usd_per_play"])) == Decimal("0.005")
    assert (Decimal(str(cfg["pool_reward_usd_per_active_day"]))
            == Decimal("0.025"))
    assert Decimal(str(cfg["pool_reward_cap_ratio"])) == Decimal("1")
    # 积分费率键已摘除，不许残留。
    assert "pool_reward_points_per_play" not in cfg
    assert "pool_reward_points_per_active_day" not in cfg
    assert "pool_reward_points_cap" not in cfg
    assert cfg["pool_reward_min_active_days"] == 7
    assert cfg["pool_reward_min_counted_plays"] == 10
    assert cfg["pool_reward_title_enabled"] is True
    # PR-1 的配置端点口径不许被激励键撑大（27 键含用户亲批的
    # 入池门槛键、大文件分流两键与选盘 v2 权重键）。
    assert len(pool_gate.pool_config_payload(db)) == 27


# ------------------------------------------------------------ 影子与真发


def test_shadow_run_books_rows_without_paying(db):
    user = _user(db, "contrib_shadow")
    member = _member(db, user.id)
    _plays(db, member.id, 10)
    _set_cfg(db, pool_reward_enabled=True)  # 真发开关仍关
    summary = pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail is not None
    assert detail.status == "shadow"
    # active 全期 30 天：金额 = 10*0.005 + 30*0.025 = $0.80（精确累计）。
    assert detail.counted_plays == 10
    assert detail.active_days == 30
    assert Decimal(str(detail.amount_usd)) == Decimal("0.8")
    # 影子跑不入账：余额 0、无钱包流水、积分影子数不再写。
    assert _balance(db, user.id) == Decimal("0")
    assert _ledger_rows(db, user.id) == []
    assert detail.points == 0
    assert summary["shadow"] == 1


def test_payout_idempotent_across_reruns(db):
    user = _user(db, "contrib_pay")
    member = _member(db, user.id)
    _plays(db, member.id, 10)
    _plan(db, "Global 国际版", "1.20")  # 在售目录（无订阅走兜底封顶 1.2）
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    # 真发 = 钱包入账 $0.80（< 封顶 1.2 全额）：流水 kind=pool_reward、
    # ref 锚明细、note 含结算周期；经验仍只留影子数不发放。
    detail = _detail(db, member.id)
    assert detail.status == "paid"
    assert Decimal(str(detail.amount_usd)) == Decimal("0.8")
    assert Decimal(str(detail.credited_usd)) == Decimal("0.8")
    assert _balance(db, user.id) == Decimal("0.8")
    rows = _ledger_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].ref_id == f"pool_reward:{detail.id}"
    assert PERIOD in (rows[0].note or "")
    assert detail.wallet_ledger_id == rows[0].id
    assert detail.xp == 10  # 10 counted * xp_per_play 1（影子数）
    # 连跑两次：不双发（余额与流水都不变）。
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    assert _balance(db, user.id) == Decimal("0.8")
    assert len(_ledger_rows(db, user.id)) == 1
    assert (db.query(em.PoolRewardDetail)
            .filter(em.PoolRewardDetail.member_id == member.id)
            .count()) == 1


def test_shadow_then_payout_enabled_rerun_pays_once(db):
    user = _user(db, "contrib_flip")
    member = _member(db, user.id)
    _plays(db, member.id, 10)
    _plan(db, "Global 国际版", "1.20")
    _set_cfg(db, pool_reward_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    assert _detail(db, member.id).status == "shadow"
    assert _balance(db, user.id) == Decimal("0")
    _set_cfg(db, pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    assert _detail(db, member.id).status == "paid"
    # 开闸补发只入账一次：$0.80、一条流水。
    assert _balance(db, user.id) == Decimal("0.8")
    assert len(_ledger_rows(db, user.id)) == 1
    assert _detail(db, member.id).xp == 10


# ------------------------------------------------------------ 门槛/上限/冻结/违规


def test_below_threshold_skipped(db):
    user = _user(db, "contrib_few")
    member = _member(db, user.id)
    _plays(db, member.id, 9)  # counted 9 < 门槛 10
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.status == "skipped"
    assert detail.reason == "below_threshold"


def test_active_days_below_threshold_skipped(db):
    user = _user(db, "contrib_shortlived")
    member = _member(db, user.id, status="suspended")
    # 只 active 两天（上海 11-03、11-04）就暂停到周期末。
    db.add_all([
        em.PoolMemberStatusEvent(
            member_id=member.id, from_status=None, to_status="active",
            reason="approve",
            created_at=datetime(2026, 11, 2, 16, 0)),
        em.PoolMemberStatusEvent(
            member_id=member.id, from_status="active",
            to_status="suspended", reason="manual",
            created_at=datetime(2026, 11, 4, 16, 0)),
    ])
    db.commit()
    _plays(db, member.id, 12)
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.active_days == 2
    assert detail.status == "skipped"
    assert detail.reason == "below_threshold"


def _whale(db, name):
    """高贡献量造数：1000 次 counted + 30 在线日 → 精确额 $5.75。"""
    user = _user(db, name)
    member = _member(db, user.id)
    _plays(db, member.id, 1000)
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    return user, _detail(db, member.id)


def test_cap_follows_own_plan_price(db):
    plan_a = _plan(db, "Global 国际版", "1.20")
    _plan(db, "Global 家庭版", "3.60")
    user = _user(db, "contrib_cap_a")
    member = _member(db, user.id)
    _subscription(db, user.id, plan_a.id)
    _plays(db, member.id, 1000)
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert Decimal(str(detail.amount_usd)) == Decimal("5.75")
    assert Decimal(str(detail.credited_usd)) == Decimal("1.2")
    assert _balance(db, user.id) == Decimal("1.2")


def test_cap_higher_plan_higher_cap(db):
    _plan(db, "Global 国际版", "1.20")
    plan_b = _plan(db, "Global 家庭版", "3.60")
    user = _user(db, "contrib_cap_b")
    member = _member(db, user.id)
    _subscription(db, user.id, plan_b.id)
    _plays(db, member.id, 1000)
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert Decimal(str(detail.amount_usd)) == Decimal("5.75")
    assert Decimal(str(detail.credited_usd)) == Decimal("3.6")
    assert _balance(db, user.id) == Decimal("3.6")


def test_cap_no_plan_falls_back_to_lowest_on_sale(db):
    _plan(db, "Global 国际版", "1.20")
    _plan(db, "Global 家庭版", "3.60")
    _plan(db, "已下架高价", "99.00", active=False)  # 下架不进兜底口径
    user, detail = _whale(db, "contrib_cap_floor")
    assert Decimal(str(detail.amount_usd)) == Decimal("5.75")
    assert Decimal(str(detail.credited_usd)) == Decimal("1.2")
    assert _balance(db, user.id) == Decimal("1.2")


def test_cap_ratio_config_scales_cap(db):
    plan_a = _plan(db, "Global 国际版", "1.20")
    user = _user(db, "contrib_cap_ratio")
    member = _member(db, user.id)
    _subscription(db, user.id, plan_a.id)
    _plays(db, member.id, 1000)
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True,
             pool_reward_cap_ratio="0.5")
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert Decimal(str(detail.credited_usd)) == Decimal("0.6")
    assert _balance(db, user.id) == Decimal("0.6")


def test_sub_account_excluded_from_settlement(db):
    parent = _user(db, "contrib_parent")
    sub_user = _user(db, "contrib_sub")
    sub_user.parent_user_id = parent.id
    db.commit()
    member = _member(db, sub_user.id)
    _plays(db, member.id, 10)
    _plan(db, "Global 国际版", "1.20")
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.status == "skipped"
    assert detail.reason == "sub_account"
    assert _balance(db, sub_user.id) == Decimal("0")
    assert _ledger_rows(db, sub_user.id) == []


def test_self_plays_not_counted_at_settlement(db):
    user = _user(db, "contrib_self")
    member = _member(db, user.id)
    # 全部是自己播自己的（同观众、不同变体绕开去重）：counted=0
    # → 门槛不达 skipped、防刷不计次。
    for i in range(12):
        db.add(em.PoolPlayEvent(
            member_id=member.id, variant_id=9000 + i, item_id=42,
            viewer_user_id=user.id,
            created_at=datetime(2026, 11, 5, 2, 0)
            + timedelta(minutes=i)))
    db.commit()
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.counted_plays == 0
    assert detail.status == "skipped"
    assert detail.reason == "below_threshold"
    assert _balance(db, user.id) == Decimal("0")


def test_daily_freeze_then_release_pays_capped(db):
    user = _user(db, "contrib_hot")
    member = _member(db, user.id)
    _plays(db, member.id, 5)  # 同日 5 播、冻结线 3 → counted 3、frozen
    _plan(db, "Global 国际版", "1.20")
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True,
             pool_reward_daily_play_freeze=3,
             pool_reward_min_counted_plays=3,
             pool_reward_min_active_days=0,
             pool_reward_usd_per_active_day="0")
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.status == "frozen"
    assert detail.counted_plays == 3
    # 管理员放行 → 按计量数（3 播 × $0.005 = $0.015）入账，美分
    # HALF_UP 取整为 $0.02；经验仍只留影子数不发放。
    released = pool_reward.release_frozen_detail(
        db, detail.id, actor_id=1)
    assert released.status == "paid"
    assert Decimal(str(released.amount_usd)) == Decimal("0.015")
    assert Decimal(str(released.credited_usd)) == Decimal("0.02")
    assert _balance(db, user.id) == Decimal("0.02")
    assert released.xp == 3


def test_violation_ejected_skipped_until_flag_cleared(db):
    user = _user(db, "contrib_bad")
    # 周期内全程 active（_member 已落 active 流水），周期后被违规
    # 清退——11 月这期先按违规清退不结，清标后重跑照常结。
    member = _member(db, user.id)
    member.status = "ejected"
    member.violation_flagged = True
    member.violation_reason = "刷量"
    member.violation_flagged_at = datetime(2026, 12, 5)
    member.violation_flagged_by = 1
    db.add(em.PoolMemberStatusEvent(
        member_id=member.id, from_status="active", to_status="ejected",
        reason="admin_eject", created_at=datetime(2026, 12, 5)))
    db.commit()
    _plays(db, member.id, 10)
    _plan(db, "Global 国际版", "1.20")
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.status == "skipped"
    assert detail.reason == "ejected_violation"
    assert _balance(db, user.id) == Decimal("0")
    # 清除违规标志后重跑 → 可结并入账 $0.80。
    pool_gate.clear_member_violation(db, member.id, actor_id=1)
    pool_reward.run_settlement(db, PERIOD, created_by=1)
    detail = _detail(db, member.id)
    assert detail.status == "paid"
    assert _balance(db, user.id) == Decimal("0.8")
    assert detail.xp == 10


def test_reward_disabled_refuses_to_build_batch(db):
    user = _user(db, "contrib_off")
    _member(db, user.id)
    with pytest.raises(pool_reward.PoolRewardError):
        pool_reward.run_settlement(db, PERIOD, created_by=1)
    assert db.query(em.PoolRewardSettlement).count() == 0
    assert db.query(em.PoolRewardDetail).count() == 0


# ------------------------------------------------------------ 称号


def test_title_payload_states(db):
    user = _user(db, "contrib_title")
    member = _member(db, user.id)
    payload = pool_reward.pool_identity_payload(db, user)
    assert payload == {"title": "片库贡献者"}
    member.status = "suspended"
    db.commit()
    assert pool_reward.pool_identity_payload(db, user) is None
    member.status = "active"
    db.commit()
    _set_cfg(db, pool_reward_title_enabled=False)
    assert pool_reward.pool_identity_payload(db, user) is None


# ------------------------------------------------------------ 管理端端点


@pytest.fixture()
def admin_user(db):
    user = base_models.WebUser(
        username="rewardadmin", password_hash="x",
        is_active=True, is_staff=True, admin_role="super")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def client(db, admin_user):
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_admin] = lambda: admin_user
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def test_reward_endpoints_preview_settle_release(db, client):
    user = _user(db, "contrib_api")
    member = _member(db, user.id)
    _plays(db, member.id, 5)
    _plan(db, "Global 国际版", "1.20")
    _set_cfg(db, pool_reward_enabled=True,
             pool_reward_payout_enabled=True,
             pool_reward_daily_play_freeze=3,
             pool_reward_min_counted_plays=3,
             pool_reward_min_active_days=0,
             pool_reward_usd_per_active_day="0")
    # 预览只算不落行。
    resp = client.get("/api/admin/pool/rewards/preview",
                      params={"period_key": PERIOD})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["member_id"] == member.id
    assert rows[0]["counted_plays"] == 3
    assert Decimal(str(rows[0]["amount_usd"])) == Decimal("0.015")
    assert Decimal(str(rows[0]["cap_usd"])) == Decimal("1.2")
    assert rows[0]["would_status"] == "frozen"
    assert db.query(em.PoolRewardDetail).count() == 0
    # 执行结算 → frozen 落行、不发钱。
    resp = client.post("/api/admin/pool/rewards/settle",
                       json={"period_key": PERIOD})
    assert resp.status_code == 200, resp.text
    detail = _detail(db, member.id)
    assert detail.status == "frozen"
    # 批次与明细查询。
    resp = client.get("/api/admin/pool/rewards/settlements")
    assert resp.status_code == 200
    batches = resp.json()["settlements"]
    assert len(batches) == 1
    assert batches[0]["period_key"] == PERIOD
    resp = client.get(
        f"/api/admin/pool/rewards/settlements/{batches[0]['id']}/details")
    assert resp.status_code == 200
    assert resp.json()["details"][0]["status"] == "frozen"
    # 放行 → 状态 paid 并入账（$0.015 → 美分取整 $0.02），明细回带金额。
    resp = client.post(
        f"/api/admin/pool/rewards/details/{detail.id}/release")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paid"
    assert Decimal(str(resp.json()["credited_usd"])) == Decimal("0.02")
    assert _balance(db, user.id) == Decimal("0.02")
    assert _detail(db, member.id).xp == 3


def test_reward_settle_endpoint_rejected_when_disabled(db, client):
    resp = client.post("/api/admin/pool/rewards/settle",
                       json={"period_key": PERIOD})
    assert resp.status_code == 400
