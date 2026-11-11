# -*- coding: utf-8 -*-
"""盲盒（双服通用）后端测试（2026-11-11 用户亲定，方案 v2 修订）。

钉死口径：
- 一切按 realm 分开：access_mode=free 是公益服、paid 是付费服
  （realms.is_free_realm 单口）；档位/奖池/记录/上限/预算按 realm。
- 奖品分通用（points / access_days / reg_code / sub_code）与公益
  专属（vitality / whitelist）：非公益 realm 保存含专属奖的奖池
  必须被拒；期望返还率 >100% 必须被拒。
- 抽盒：服务端权重随机；一抽一事务——扣分+定奖+发放原子完成，
  发放失败整体回滚不扣分，只留一条 charged=False 的失败审计行。
- access_days 双服分发：公益→grant_welfare(channel=blindbox)；
  付费→有在期订阅顺延 end_date，无在期订阅走档位兜底奖，不吞奖。
- 白名单库存抽空走档位兜底奖；每日消费上限拦截；活动窗外停售；
  总开关默认关。发放失败（charged 未发放）记录可经 redeliver 补发。

全部用隔离内存 SQLite，不碰网络、不碰真实配置。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import json
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — 注册 web / system_configs 等表
from backend import blindbox as bb
from backend.database import Base
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


def _realm(db, slug, access_mode):
    from backend import models

    r = models.ServerRealm(
        name=f"服-{slug}", slug=slug, url="", access_mode=access_mode,
        is_active=True,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _user(db, name="alice", points=10000):
    from backend import models

    u = models.WebUser(username=name, password_hash="x", points=points)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _prize(kind, weight, **params):
    return {"kind": kind, "weight": weight, "params": params}


def _tier(key="t1", price=100, prizes=None, fallback=None, name="测试盒"):
    return {
        "key": key,
        "name": name,
        "price": price,
        "enabled": True,
        "sort": 0,
        "fallback_prize": fallback or _prize("points", 1, amount=10),
        "prizes": prizes if prizes is not None else [
            _prize("points", 90, amount=50),
            _prize("points", 10, amount=200),
        ],
    }


def _save(db, realm_id, tiers, *, master=True, realm_enabled=True,
          starts_at="", ends_at="", daily_spend_cap=1000,
          access_days_daily_budget=3650, whitelist_stock_total=3):
    if master:
        bb.save_master_switch(db, True)
    bb.save_realm_config(db, realm_id, {
        "enabled": realm_enabled,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "daily_spend_cap": daily_spend_cap,
        "access_days_daily_budget": access_days_daily_budget,
        "whitelist_stock_total": whitelist_stock_total,
        "tiers": tiers,
    })
    db.commit()
    config_store.invalidate()


# ---------------------------------------------------------------------------
# 概率分布（纯权重抽取，大样本落在误差内）
# ---------------------------------------------------------------------------

def test_pick_distribution_within_tolerance():
    prizes = [
        _prize("points", 70, amount=10),
        _prize("points", 20, amount=50),
        _prize("points", 10, amount=100),
    ]
    counts = [0, 0, 0]
    n = 60000
    for _ in range(n):
        idx = bb.pick_prize_index(prizes)
        counts[idx] += 1
    for got, want in zip(counts, (0.70, 0.20, 0.10)):
        assert abs(got / n - want) < 0.01, (counts, want)


def test_pick_weights_need_not_sum_to_100():
    prizes = [_prize("points", 2, amount=1), _prize("points", 6, amount=2)]
    counts = [0, 0]
    n = 40000
    for _ in range(n):
        counts[bb.pick_prize_index(prizes)] += 1
    assert abs(counts[0] / n - 0.25) < 0.01


# ---------------------------------------------------------------------------
# 配置校验：专属奖限制 + 返还率红线
# ---------------------------------------------------------------------------

def test_paid_realm_rejects_welfare_only_prizes(db):
    realm = _realm(db, "paid1", "paid")
    for kind in ("vitality", "whitelist"):
        with pytest.raises(bb.BlindboxError):
            _save(db, realm.id, [_tier(prizes=[_prize(kind, 1, amount=1)])])


def test_free_realm_accepts_welfare_only_prizes(db):
    realm = _realm(db, "free1", "free")
    _save(db, realm.id, [_tier(prizes=[
        _prize("vitality", 1, amount=1),
        _prize("points", 9, amount=10),
    ])])


def test_return_rate_over_100_rejected(db):
    realm = _realm(db, "free2", "free")
    # 售价 100，必出 1000 积分 → 返还率 1000%
    with pytest.raises(bb.BlindboxError):
        _save(db, realm.id, [_tier(price=100, prizes=[
            _prize("points", 1, amount=1000)])])


def test_expected_return_rate_computation(db):
    realm = _realm(db, "free3", "free")
    tier = _tier(price=100, prizes=[
        _prize("points", 90, amount=50),
        _prize("points", 10, amount=200),
    ])
    rate = bb.expected_return_rate(db, tier)
    assert rate == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# 抽盒主流程：开关 / 时间窗 / 上限 / 原子性
# ---------------------------------------------------------------------------

def test_master_switch_default_off(db):
    realm = _realm(db, "free4", "free")
    user = _user(db)
    _save(db, realm.id, [_tier()], master=False)
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")
    db.rollback()
    assert db.query(type(user)).get(user.id).points == 10000


def test_draw_success_spends_and_delivers_points(db):
    realm = _realm(db, "free5", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(prizes=[_prize("points", 1, amount=50)])])
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "points"
    assert result["balance"] == 10000 - 100 + 50
    from backend import models

    draws = db.query(models.BlindboxDraw).all()
    assert len(draws) == 1
    assert draws[0].status == "delivered"
    assert draws[0].charged is True
    logs = db.query(models.PointsLog).filter(
        models.PointsLog.user_id == user.id).all()
    types = sorted(l.type for l in logs)
    assert types == ["blindbox_prize", "blindbox_spend"]


def test_daily_spend_cap_blocks_second_draw(db):
    realm = _realm(db, "free6", "free")
    user = _user(db)
    _save(db, realm.id, [_tier()], daily_spend_cap=150)
    bb.draw(db, user, realm.id, "t1")
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")
    db.rollback()
    from backend import models

    assert db.query(models.BlindboxDraw).filter_by(status="delivered").count() == 1


def test_activity_window_blocks_draw(db):
    realm = _realm(db, "free7", "free")
    user = _user(db)
    past = (datetime.now() - timedelta(days=2)).isoformat()
    past_end = (datetime.now() - timedelta(days=1)).isoformat()
    _save(db, realm.id, [_tier()], starts_at=past, ends_at=past_end)
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")
    db.rollback()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    _save(db, realm.id, [_tier()], starts_at=future, ends_at="")
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")


def test_delivery_failure_rolls_back_and_leaves_audit(db, monkeypatch):
    realm = _realm(db, "free8", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(prizes=[_prize("points", 1, amount=50)])])

    import backend.api.economy as economy

    def boom(*a, **kw):
        raise RuntimeError("发放通道炸了")

    monkeypatch.setattr(economy, "_add_points", boom)
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")
    from backend import models

    db.expire_all()
    assert db.query(models.WebUser).get(user.id).points == 10000
    draws = db.query(models.BlindboxDraw).all()
    assert len(draws) == 1
    assert draws[0].status == "failed"
    assert draws[0].charged is False


def test_insufficient_points_rejected_without_charge(db):
    realm = _realm(db, "free9", "free")
    user = _user(db, points=50)
    _save(db, realm.id, [_tier(price=100)])
    with pytest.raises(bb.BlindboxError):
        bb.draw(db, user, realm.id, "t1")
    db.rollback()
    from backend import models

    assert db.query(models.WebUser).get(user.id).points == 50


# ---------------------------------------------------------------------------
# 并发：同用户多线程同抽，不重复扣分、不重复出奖
# ---------------------------------------------------------------------------

def test_concurrent_draws_consistent(tmp_path):
    # 文件库 + 默认连接池：每线程独立连接，才是真并发；
    # 内存 StaticPool 全线程共用一条连接，事务会互相串扰，不作数。
    engine = create_engine(
        f"sqlite:///{tmp_path}/blindbox-concurrent.db",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, expire_on_commit=False)
    setup = make()
    config_store.invalidate()
    try:
        realm = _realm(setup, "free10", "free")
        user = _user(setup, points=1000)
        _save(setup, realm.id, [_tier(price=100, prizes=[
            _prize("points", 1, amount=10)])])
        realm_id, user_id = realm.id, user.id
    finally:
        setup.close()
    errors = []

    def work():
        s = make()
        try:
            from backend import models

            u = s.query(models.WebUser).get(user_id)
            bb.draw(s, u, realm_id, "t1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    from backend import models

    s = make()
    try:
        final = s.query(models.WebUser).get(user_id)
        assert final.points == 1000 - 5 * 100 + 5 * 10
        assert s.query(models.BlindboxDraw).filter_by(
            status="delivered").count() == 5
    finally:
        s.close()
        config_store.invalidate()


# ---------------------------------------------------------------------------
# 库存奖（白名单）抽空走兜底
# ---------------------------------------------------------------------------

def test_whitelist_stock_exhausted_falls_back(db):
    realm = _realm(db, "free11", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(
        price=4000,
        prizes=[_prize("whitelist", 1)],
        fallback=_prize("points", 1, amount=777),
    )], whitelist_stock_total=1, daily_spend_cap=100000)
    first = bb.draw(db, user, realm.id, "t1")
    assert first["prize"]["kind"] == "whitelist"
    assert first["prize"].get("code")
    second = bb.draw(db, user, realm.id, "t1")
    assert second["prize"]["kind"] == "points"
    assert second["prize"]["fallback_from"] == "whitelist"
    from backend import models

    codes = db.query(models.RegistrationCode).filter_by(code_type=3).all()
    assert len(codes) == 1


# ---------------------------------------------------------------------------
# access_days 双服分发
# ---------------------------------------------------------------------------

def test_access_days_free_realm_grants_welfare(db):
    realm = _realm(db, "free12", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(price=500, prizes=[
        _prize("access_days", 1, days=30)])])
    before = datetime.now()
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "access_days"
    from backend import models

    u = db.query(models.WebUser).get(user.id)
    assert u.is_welfare is True
    assert u.welfare_expires_at is not None
    assert u.welfare_expires_at > before + timedelta(days=29)
    logs = db.query(models.WelfareGrantLog).filter_by(
        user_id=user.id, channel="blindbox").all()
    assert len(logs) == 1


def _add_subscription(db, user, realm, days=30):
    from backend import models

    plan = models.SubscriptionPlan(
        name=f"套餐-{realm.slug}", price=0, duration_days=30,
        realm_id=realm.id, is_active=True)
    db.add(plan)
    db.flush()
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm.id,
        start_date=now, end_date=now + timedelta(days=days),
        status="active")
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def test_access_days_paid_realm_extends_subscription(db):
    realm = _realm(db, "paid2", "paid")
    user = _user(db)
    sub = _add_subscription(db, user, realm, days=30)
    old_end = sub.end_date
    _save(db, realm.id, [_tier(price=200, prizes=[
        _prize("access_days", 1, days=7)])])
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "access_days"
    from backend import models

    s2 = db.query(models.UserSubscription).get(sub.id)
    assert s2.end_date >= old_end + timedelta(days=7) - timedelta(seconds=5)


def test_access_days_paid_realm_no_subscription_falls_back(db):
    realm = _realm(db, "paid3", "paid")
    user = _user(db)
    _save(db, realm.id, [_tier(
        price=500,
        prizes=[_prize("access_days", 1, days=30)],
        fallback=_prize("sub_code", 1, days=30),
    )])
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "sub_code"
    assert result["prize"]["fallback_from"] == "access_days"
    assert result["prize"].get("code")
    from backend import models

    codes = db.query(models.RegistrationCode).filter_by(code_type=2).all()
    assert len(codes) == 1
    assert codes[0].realm_id == realm.id


def test_access_days_budget_exceeded_falls_back(db):
    realm = _realm(db, "free13", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(
        price=500,
        prizes=[_prize("access_days", 1, days=30)],
        fallback=_prize("points", 1, amount=450),
    )], access_days_daily_budget=30)
    first = bb.draw(db, user, realm.id, "t1")
    assert first["prize"]["kind"] == "access_days"
    second = bb.draw(db, user, realm.id, "t1")
    assert second["prize"]["kind"] == "points"
    assert second["prize"]["fallback_from"] == "access_days"


# ---------------------------------------------------------------------------
# 活力与注册码奖品
# ---------------------------------------------------------------------------

def test_vitality_prize_uses_vitality_ledger(db):
    realm = _realm(db, "free14", "free")
    user = _user(db)
    user.vitality = 5  # 活力上限默认 14，满格发放会被现行口径钳制
    db.commit()
    _save(db, realm.id, [_tier(prizes=[_prize("vitality", 1, amount=3)])])
    bb.draw(db, user, realm.id, "t1")
    from backend import models

    db.expire_all()  # 活力走原生 SQL 更新，须绕开会话身份映射读新值
    u = db.query(models.WebUser).get(user.id)
    assert u.vitality == 8
    logs = db.query(models.VitalityLog).filter_by(user_id=user.id).all()
    assert any(l.delta == 3 for l in logs)


def test_reg_code_prize_lands_in_card_pack(db):
    realm = _realm(db, "free15", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(price=500, prizes=[
        _prize("reg_code", 1, days=30)])])
    result = bb.draw(db, user, realm.id, "t1")
    code = result["prize"].get("code")
    assert code
    pack = bb.my_card_pack(db, user)
    assert any(c["code"] == code and not c["used"] for c in pack)


# ---------------------------------------------------------------------------
# 补发：charged 未发放记录可补发且不重复扣分
# ---------------------------------------------------------------------------

def test_redeliver_charged_undelivered_draw(db):
    realm = _realm(db, "free16", "free")
    user = _user(db, points=500)
    _save(db, realm.id, [_tier()])
    from backend import models

    rec = models.BlindboxDraw(
        draw_key="test-redeliver-1", user_id=user.id, realm_id=realm.id,
        tier_key="t1", tier_name="测试盒", price=100,
        prize_kind="points", prize_params={"amount": 50}, prize_value=50,
        status="delivery_failed", charged=True,
        delivery={"error": "模拟发放中断"},
    )
    db.add(rec)
    db.commit()
    out = bb.redeliver(db, rec.id)
    assert out["status"] == "delivered"
    u = db.query(models.WebUser).get(user.id)
    assert u.points == 500 + 50  # 只补奖，不再扣售价
    again = bb.redeliver(db, rec.id)
    assert again["status"] == "delivered"
    u = db.query(models.WebUser).get(user.id)
    assert u.points == 550  # 幂等：已发放的不重复发


# ---------------------------------------------------------------------------
# 用户侧读口：档位与概率表 / 我的记录 / 统计
# ---------------------------------------------------------------------------

def test_public_tiers_show_probabilities(db):
    realm = _realm(db, "free17", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(prizes=[
        _prize("points", 90, amount=50),
        _prize("points", 10, amount=200),
    ])])
    view = bb.public_tiers(db, realm.id)
    assert view["enabled"] is True
    tiers = view["tiers"]
    assert len(tiers) == 1
    detail = tiers[0]["prizes"]
    assert sorted(p["probability"] for p in detail) == [
        pytest.approx(0.1), pytest.approx(0.9)]
    assert abs(sum(p["probability"] for p in detail) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# API 层：管理端保存红线 / 用户端抽盒 / 补发端点
# ---------------------------------------------------------------------------

def test_admin_api_rejects_return_rate_over_100(db):
    from fastapi import HTTPException

    from backend.api import admin_blindbox as adm

    realm = _realm(db, "free19", "free")
    admin = _user(db, name="root")
    payload = adm.BlindboxRealmConfigRequest(
        realm_id=realm.id,
        config={"enabled": True, "tiers": [_tier(prizes=[
            _prize("points", 1, amount=1000)])]})
    with pytest.raises(HTTPException) as ei:
        adm.put_blindbox_config(payload, db, admin)
    assert ei.value.status_code == 400


def test_admin_api_config_roundtrip_and_user_draw(db):
    from backend.api import admin_blindbox as adm
    from backend.api import user as user_api

    realm = _realm(db, "free20", "free")
    admin = _user(db, name="root2")
    user = _user(db, name="bob")
    adm.put_blindbox_master(adm.BlindboxMasterRequest(enabled=True), db, admin)
    out = adm.put_blindbox_config(adm.BlindboxRealmConfigRequest(
        realm_id=realm.id,
        config={"enabled": True, "tiers": [_tier(prizes=[
            _prize("points", 1, amount=50)])]}), db, admin)
    assert out["success"] is True
    got = adm.get_blindbox_config(realm.id, db, admin)
    assert got["config"]["tiers"][0]["key"] == "t1"
    view = user_api.blindbox_tiers(realm.id, user, db)
    assert view["enabled"] is True
    result = user_api.blindbox_draw(
        user_api.BlindboxDrawRequest(realm_id=realm.id, tier_key="t1"),
        user, db)
    assert result["prize"]["kind"] == "points"
    mine = user_api.blindbox_my_draws(realm.id, 50, 0, user, db)
    assert mine["total"] == 1
    stats = adm.get_blindbox_stats(realm.id, db, admin)
    assert stats["draws"] == 1 and stats["revenue"] == 100


def test_admin_api_redeliver_endpoint(db):
    from backend.api import admin_blindbox as adm

    realm = _realm(db, "free21", "free")
    admin = _user(db, name="root3")
    user = _user(db, name="carol", points=500)
    _save(db, realm.id, [_tier()])
    from backend import models

    rec = models.BlindboxDraw(
        draw_key="api-redeliver-1", user_id=user.id, realm_id=realm.id,
        tier_key="t1", tier_name="测试盒", price=100,
        prize_kind="points", prize_params={"amount": 40}, prize_value=40,
        status="delivery_failed", charged=True, delivery={})
    db.add(rec)
    db.commit()
    out = adm.redeliver_blindbox_draw(rec.id, db, admin)
    assert out["success"] is True and out["status"] == "delivered"


def test_stats_count_flow_and_payout(db):
    realm = _realm(db, "free18", "free")
    user = _user(db)
    _save(db, realm.id, [_tier(prizes=[_prize("points", 1, amount=50)])])
    bb.draw(db, user, realm.id, "t1")
    bb.draw(db, user, realm.id, "t1")
    stats = bb.draw_stats(db, realm.id)
    assert stats["draws"] == 2
    assert stats["revenue"] == 200
    assert stats["payout_value"] == 100
    assert stats["actual_return_rate"] == pytest.approx(0.5)
