"""管理端分页/性能批回归（admin-fix-b）：

1. 工单列表真分页信封 + keyword（标题/发起人用户名）+ 最新消息只取每单一条后仍正确；
2. 图覆盖列表 offset + total 信封；
3. 操作日志 offset + total 信封；
4. 订阅列表批量取 plan 后响应字段与改前等值（字段级断言）；
6. 邀请福利码状态过滤下推 SQL：500 条窗口外的符合项不漏；
7. API Key 列表 limit/offset 上限（形状保持 {items,total}）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-admin-pagination-perf-key-32ch!")

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import admin as admin_api
from backend.api import admin_apikey as apikey_api
from backend.api import admin_artwork as artwork_api
from backend.api import admin_economy as economy_api
from backend.api import admin_invitation as invitation_api
from backend.emby_server import models as em


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    try:
        em.Base.metadata.create_all(engine)
    except Exception:
        pass
    session = sessionmaker(bind=engine)()
    session.add(models.ServerRealm(id=1, name="默认服", slug="main", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _user(db, username):
    u = models.WebUser(username=username, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _ticket(db, user, title, status="open", msgs=()):
    t = models.Ticket(user_id=user.id, title=title, status=status,
                      category="other", priority="medium")
    db.add(t)
    db.commit()
    db.refresh(t)
    base = datetime(2026, 1, 1, 10, 0, 0)
    for i, text in enumerate(msgs):
        db.add(models.TicketMessage(
            ticket_id=t.id, user_id=user.id, message=text,
            created_at=base + timedelta(minutes=i)))
    db.commit()
    return t


# ---------- 1. 工单分页 / keyword / 最新消息 ----------

def test_tickets_pagination_envelope_and_latest_message(db):
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    for i in range(5):
        _ticket(db, alice, f"问题{i}", msgs=[f"首条{i}", f"最新{i}"])
    out = admin_api.get_tickets(current_admin=admin, db=db, limit=2, offset=0)
    assert out["total"] == 5
    assert len(out["tickets"]) == 2
    page2 = admin_api.get_tickets(current_admin=admin, db=db, limit=2, offset=4)
    assert len(page2["tickets"]) == 1
    ids = {t["id"] for t in out["tickets"]} | {t["id"] for t in page2["tickets"]}
    assert len(ids) == 3  # 两页不重叠
    first = out["tickets"][0]
    assert first["latest_message"].startswith("最新")
    assert first["user_name"] == "alice"


def test_tickets_keyword_matches_title_and_username(db):
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    _ticket(db, alice, "播放卡顿求助")
    _ticket(db, bob, "账号问题")
    by_title = admin_api.get_tickets(keyword="卡顿", current_admin=admin, db=db)
    assert by_title["total"] == 1
    assert by_title["tickets"][0]["title"] == "播放卡顿求助"
    by_user = admin_api.get_tickets(keyword="bob", current_admin=admin, db=db)
    assert by_user["total"] == 1
    assert by_user["tickets"][0]["user_name"] == "bob"


def test_tickets_status_filter_kept(db):
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    _ticket(db, alice, "开着的", status="open")
    _ticket(db, alice, "关了的", status="closed")
    out = admin_api.get_tickets(status_filter="closed",
                                current_admin=admin, db=db)
    assert out["total"] == 1
    assert out["tickets"][0]["title"] == "关了的"


# ---------- 2. 图覆盖 offset ----------

def test_artwork_overrides_offset_and_total(db):
    lib = em.Library(name="库", guid="lib-g-1")
    db.add(lib)
    db.commit()
    db.refresh(lib)
    for i in range(4):
        item = em.MediaItem(name=f"片{i}", guid=f"g-{i}", item_type="movie",
                            library_id=lib.id)
        db.add(item)
        db.commit()
        db.refresh(item)
        db.add(em.MediaItemArtworkOverride(
            item_id=item.id, kind="Primary",
            image_url=f"https://img.example/{i}.jpg",
            created_at=datetime(2026, 1, 1) + timedelta(days=i),
            updated_at=datetime(2026, 1, 1) + timedelta(days=i)))
        db.commit()
    page1 = artwork_api.list_overrides(limit=2, offset=0, db=db)
    assert page1["total"] == 4
    assert len(page1["items"]) == 2
    page2 = artwork_api.list_overrides(limit=2, offset=2, db=db)
    assert len(page2["items"]) == 2
    names = {r["item_name"] for r in page1["items"]}
    names |= {r["item_name"] for r in page2["items"]}
    assert names == {"片0", "片1", "片2", "片3"}


# ---------- 3. 操作日志 offset ----------

def test_admin_logs_offset_and_total(db):
    admin = _user(db, "admin1")
    for i in range(5):
        db.add(models.AdminLog(admin_user_id=admin.id, action=f"act{i}",
                               target_type="user", target_id=i,
                               created_at=datetime(2026, 1, 1) + timedelta(hours=i)))
    db.commit()
    page1 = admin_api.get_admin_logs(limit=2, offset=0,
                                     current_admin=admin, db=db)
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page3 = admin_api.get_admin_logs(limit=2, offset=4,
                                     current_admin=admin, db=db)
    assert len(page3["items"]) == 1
    actions = {r["action"] for r in page1["items"]}
    actions |= {r["action"] for r in page3["items"]}
    assert len(actions) == 3


# ---------- 4. 订阅列表字段等值 ----------

def test_economy_subscriptions_fields_unchanged(db):
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    plan = models.SubscriptionPlan(name="全球版", price=Decimal("10"),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=alice.id, plan_id=plan.id, realm_id=1, status="active",
        start_date=now - timedelta(days=1), end_date=now + timedelta(days=29))
    db.add(sub)
    db.commit()
    db.refresh(sub)
    out = economy_api.economy_list_subscriptions(current_admin=admin, db=db)
    assert out["total"] == 1
    row = out["subscriptions"][0]
    assert row == {
        "id": sub.id,
        "user_id": alice.id,
        "username": "alice",
        "plan_name": "全球版",
        "start_date": sub.start_date.isoformat(),
        "end_date": sub.end_date.isoformat(),
        "days_left": row["days_left"],
        "status": "active",
    }
    assert set(out["summary"]) == {"active", "expiring_7d", "expired"}


def test_economy_subscriptions_missing_plan_fallback(db):
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=alice.id, plan_id=9999, realm_id=1, status="active",
        start_date=now, end_date=now + timedelta(days=10))
    db.add(sub)
    db.commit()
    out = economy_api.economy_list_subscriptions(current_admin=admin, db=db)
    assert out["subscriptions"][0]["plan_name"] == "套餐 #9999"


# ---------- 6. 福利码状态过滤下推 ----------

def test_trial_codes_status_filter_beyond_500_window(db):
    admin = _user(db, "admin1")
    creator = _user(db, "creator")
    now = datetime.now()
    # 最旧的 10 条是 pending（未过期），其后 505 条 used：
    # 旧实现先按 id desc 取 500 条（全是 used）再 Python 过滤 pending → 0 条。
    rows = []
    for i in range(10):
        rows.append(models.InviteTrialCode(
            code=f"PEND{i:04d}", creator_id=creator.id, days=7,
            status="pending", created_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=28)))
    for i in range(505):
        rows.append(models.InviteTrialCode(
            code=f"USED{i:04d}", creator_id=creator.id, days=7,
            status="used", used_by=creator.id,
            created_at=now - timedelta(days=1), used_at=now,
            expires_at=now + timedelta(days=28)))
    db.add_all(rows)
    db.commit()
    out = invitation_api.invite_trial_codes(limit=50, status="pending",
                                            current_admin=admin, db=db)
    assert out["total"] == 10
    assert all(c["status"] == "pending" for c in out["codes"])


def test_trial_codes_expired_includes_lazy_expired_pending(db):
    admin = _user(db, "admin1")
    creator = _user(db, "creator")
    now = datetime.now()
    db.add(models.InviteTrialCode(
        code="LAZYEXP1", creator_id=creator.id, days=7, status="pending",
        created_at=now - timedelta(days=40),
        expires_at=now - timedelta(days=10)))
    db.add(models.InviteTrialCode(
        code="REALEXP1", creator_id=creator.id, days=7, status="expired",
        created_at=now - timedelta(days=40),
        expires_at=now - timedelta(days=10)))
    db.commit()
    out = invitation_api.invite_trial_codes(limit=50, status="expired",
                                            current_admin=admin, db=db)
    assert out["total"] == 2
    assert all(c["status"] == "expired" for c in out["codes"])


# ---------- 4b/5. realms 订阅字段等值 + 概览 ----------

def test_realm_subscriptions_fields_unchanged(db):
    from backend.api import realms as realms_api
    admin = _user(db, "admin1")
    alice = _user(db, "alice")
    plan = models.SubscriptionPlan(name="服套餐", price=Decimal("10"),
                                   duration_days=30, realm_id=1)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    now = datetime.now()
    sub = models.UserSubscription(
        user_id=alice.id, plan_id=plan.id, realm_id=1, status="active",
        start_date=now - timedelta(days=1), end_date=now + timedelta(days=29))
    db.add(sub)
    db.commit()
    db.refresh(sub)
    out = realms_api.realm_subscriptions(
        realm_id=0, _=admin, db=db)
    assert out["total"] == 1
    row = out["subscriptions"][0]
    assert row["plan_name"] == "服套餐"
    assert row["realm_name"] == "默认服"
    assert row["username"] == "alice"
    assert row["status"] == "active"


def test_realms_overview_summary_consistent(db):
    from backend.api import realms as realms_api
    admin = _user(db, "admin1")
    out = realms_api.realms_overview(_=admin, db=db)
    assert out["summary"]["total_realms"] == 1
    assert len(out["realms"]) == 1
    assert out["realms"][0]["name"] == "默认服"
    assert out["realms"][0]["is_default"] is True
    # summary 必须与逐服 stats 同一份数据汇总（单服时逐键相等）
    for key in ("libraries", "items", "plans", "active_subscriptions",
                "subscribers", "nodes", "nodes_online", "pending_requests"):
        assert out["summary"][key] == out["realms"][0]["stats"][key]


# ---------- 7. API Key 列表上限 ----------

def test_api_keys_limit_offset(db):
    admin = _user(db, "admin1")
    for i in range(5):
        db.add(models.ApiKey(key_id=f"kid{i:08d}", key_hash=f"h{i:060d}",
                             name=f"k{i}", scopes=["read"], is_active=True))
    db.commit()
    page1 = apikey_api.list_api_keys(db=db, current_admin=admin,
                                     limit=2, offset=0)
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page2 = apikey_api.list_api_keys(db=db, current_admin=admin,
                                     limit=2, offset=4)
    assert len(page2["items"]) == 1
    ids = {k["key_id"] for k in page1["items"]}
    ids |= {k["key_id"] for k in page2["items"]}
    assert len(ids) == 3
