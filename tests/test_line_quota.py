# -*- coding: utf-8 -*-
"""播放线路（服）流量配额（2026-11-19 用户点选）。

钉住的口径：
- 计量旁路：traffic_meter 接受计量的同一笔字节（中继/本机文件腿、
  实际 yield 字节）再按「服务进程所属服」记一份日账；无用户归属
  不计、302 直链只记次不记字节——与用户流量账完全同口径；
- 配额：quota_bytes=0 不限额；已用 = 日账总和 − 重置偏移；用尽
  判定 used >= quota；
- 重置：当刻总和快照为偏移，当天重置前的字节也不再算——重置完
  立刻恢复可分配，之后的新字节重新计；
- 分流下发：账号卡每条线带 quota_exhausted；单线用尽只标该线，
  全部用尽时整体回落清零（不断播）；地址照常下发；
- 管理接口：PUT 可改配额（负数 422），POST reset-traffic 重置。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import line_quota, models, realms
from backend.emby_server import models as em
from backend.emby_server import portal, traffic_meter as tm

GB = 1024 ** 3
DAY = date(2026, 11, 19)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    tm.reset_pending()
    line_quota.invalidate()
    realms.invalidate_realm_cache()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        tm.reset_pending()
        line_quota.invalidate()
        realms.invalidate_realm_cache()


_seq = [0]


def _realm(db, slug, quota=0, url=""):
    _seq[0] += 1
    r = models.ServerRealm(
        name=f"线路-{slug}-{_seq[0]}", slug=f"{slug}{_seq[0]}", url=url,
        access_mode="paid", traffic_quota_bytes=quota)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _user(db):
    _seq[0] += 1
    u = models.WebUser(username=f"lq{_seq[0]}", password_hash="x",
                       is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _sub(db, user, realm, days=30):
    plan = models.SubscriptionPlan(name="月卡", duration_days=30, price=18)
    db.add(plan)
    db.commit()
    end = datetime.now() + timedelta(days=days)
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm.id,
        status="active", start_date=end - timedelta(days=30), end_date=end)
    db.add(sub)
    db.commit()
    return sub


def _seed_usage(db, realm_id, relay=0, local=0, day=DAY):
    row = em.RealmTrafficUsageDaily(
        realm_id=realm_id, day=day, relay_bytes=relay, local_bytes=local)
    db.add(row)
    db.commit()
    line_quota.invalidate()
    return row


def _realm_row(db, realm_id, day=DAY):
    return (db.query(em.RealmTrafficUsageDaily)
            .filter_by(realm_id=realm_id, day=day).first())


# ---------------------------------------------------------------------------
# 计量旁路：字节按进程所属服归账
# ---------------------------------------------------------------------------

def test_meter_attributes_bytes_to_process_realm(db):
    realm = _realm(db, "main")  # 第一个服 = 默认服（进程归属回落口径）
    user = _user(db)
    tm.note_bytes(600, "relay", user_id=user.id, day=DAY)
    tm.note_bytes(400, "local", user_id=user.id, day=DAY)
    tm.note_direct_play(user_id=user.id, day=DAY)
    assert tm.flush_now(db) == 1  # 用户账一行
    row = _realm_row(db, realm.id)
    assert row is not None
    assert row.relay_bytes == 600
    assert row.local_bytes == 400
    # 用户账口径不被旁路影响：直链只记次、字节分腿
    urow = db.query(em.TrafficUsageDaily).filter_by(user_id=user.id).first()
    assert (urow.relay_bytes, urow.local_bytes, urow.direct_plays) == (600, 400, 1)
    # 再落一笔：同日累计而不是覆盖
    tm.note_bytes(100, "relay", user_id=user.id, day=DAY)
    tm.flush_now(db)
    db.expire_all()
    assert _realm_row(db, realm.id).relay_bytes == 700


def test_meter_no_user_no_bytes_anywhere(db):
    _realm(db, "main")
    tm.note_bytes(999, "relay", user_id=None, day=DAY)
    assert tm.flush_now(db) == 0
    assert db.query(em.RealmTrafficUsageDaily).count() == 0
    assert db.query(em.TrafficUsageDaily).count() == 0


# ---------------------------------------------------------------------------
# 配额判定：不限 / 未用尽 / 用尽 / 剩余
# ---------------------------------------------------------------------------

def test_quota_zero_is_unlimited(db):
    realm = _realm(db, "free", quota=0)
    _seed_usage(db, realm.id, relay=500 * GB)
    view = line_quota.quota_view(db, realm)
    assert view["quota_bytes"] == 0
    assert view["used_bytes"] == 500 * GB
    assert view["remaining_bytes"] is None
    assert view["exhausted"] is False


def test_quota_exhaustion_and_remaining(db):
    realm = _realm(db, "cap", quota=1000)
    _seed_usage(db, realm.id, relay=600)
    view = line_quota.quota_view(db, realm)
    assert view["used_bytes"] == 600
    assert view["remaining_bytes"] == 400
    assert view["exhausted"] is False
    _seed_usage(db, realm.id, relay=400, day=date(2026, 11, 20))
    view = line_quota.quota_view(db, realm)
    assert view["used_bytes"] == 1000
    assert view["remaining_bytes"] == 0
    assert view["exhausted"] is True


def test_update_realm_rejects_negative_quota(db):
    realm = _realm(db, "neg")
    with pytest.raises(ValueError):
        realms.update_realm(db, realm, traffic_quota_bytes=-1)


# ---------------------------------------------------------------------------
# 重置：当刻归零、恢复分配、之后重新计
# ---------------------------------------------------------------------------

def test_reset_restores_allocation_same_day(db):
    realm = _realm(db, "rst", quota=1000)
    _seed_usage(db, realm.id, relay=1500)  # 今天就已超额
    assert line_quota.is_exhausted(db, realm) is True
    line_quota.reset_period(db, realm)
    view = line_quota.quota_view(db, realm)
    assert view["used_bytes"] == 0
    assert view["exhausted"] is False
    assert view["period_started_at"] is not None
    # 重置后的新字节重新计（同一天的行继续累计，靠偏移抵扣旧账）
    row = _realm_row(db, realm.id)
    row.relay_bytes += 300
    db.commit()
    line_quota.invalidate()
    assert line_quota.quota_view(db, realm)["used_bytes"] == 300


# ---------------------------------------------------------------------------
# 账号卡分流标记：单线用尽只标该线 / 全线用尽回落清零
# ---------------------------------------------------------------------------

def _cards(db, user):
    return {c["id"]: c for c in portal._user_realm_cards(user, db, None)}


def test_card_flags_single_exhausted_line(db):
    a = _realm(db, "a", quota=0, url="https://a.example.com")
    b = _realm(db, "b", quota=100, url="https://b.example.com")
    user = _user(db)
    _sub(db, user, a)
    _sub(db, user, b)
    _seed_usage(db, b.id, relay=100)
    cards = _cards(db, user)
    assert cards[a.id]["quota_exhausted"] is False
    assert cards[b.id]["quota_exhausted"] is True
    # 地址照常下发：已连这条线的外部客户端不受配额影响
    assert cards[b.id]["base_url"] == "https://b.example.com"


def test_card_flags_all_exhausted_fallback(db):
    a = _realm(db, "a", quota=100, url="https://a.example.com")
    b = _realm(db, "b", quota=100, url="https://b.example.com")
    user = _user(db)
    _sub(db, user, a)
    _sub(db, user, b)
    _seed_usage(db, a.id, relay=100)
    _seed_usage(db, b.id, relay=250)
    cards = _cards(db, user)
    # 全部用尽 → 整体回落：标记清零，一切照现行口径（不断播）
    assert cards[a.id]["quota_exhausted"] is False
    assert cards[b.id]["quota_exhausted"] is False


# ---------------------------------------------------------------------------
# 管理接口：改配额 / 重置 / 负数拒绝
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_client(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import realms as realms_api
    from backend.api.admin_core import get_current_admin
    from backend.database import get_db

    admin = models.WebUser(username="lqadmin", password_hash="x",
                           is_staff=True, is_active=True)
    db.add(admin)
    db.commit()
    app = FastAPI()
    app.include_router(realms_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_admin] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


def test_admin_api_update_quota_and_reset(admin_client, db):
    realm = _realm(db, "api", quota=0)
    resp = admin_client.put(f"/api/admin/realms/{realm.id}",
                            json={"traffic_quota_bytes": 2048})
    assert resp.status_code == 200
    body = resp.json()
    assert body["realm"]["traffic"]["quota_bytes"] == 2048
    _seed_usage(db, realm.id, relay=2048)
    resp = admin_client.get("/api/admin/realms")
    row = next(r for r in resp.json()["realms"] if r["id"] == realm.id)
    assert row["traffic"]["used_bytes"] == 2048
    assert row["traffic"]["exhausted"] is True
    resp = admin_client.post(f"/api/admin/realms/{realm.id}/reset-traffic")
    assert resp.status_code == 200
    traffic = resp.json()["realm"]["traffic"]
    assert traffic["used_bytes"] == 0
    assert traffic["exhausted"] is False


def test_admin_api_negative_quota_rejected(admin_client, db):
    realm = _realm(db, "api2")
    resp = admin_client.put(f"/api/admin/realms/{realm.id}",
                            json={"traffic_quota_bytes": -5})
    assert resp.status_code == 422
