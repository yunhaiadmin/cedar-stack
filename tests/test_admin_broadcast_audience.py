"""管理端广播「人群发送」回归测试。

覆盖六种人群的解析口径（与 backend/subscriptions.py 的有效订阅判定一致：
status == "active" 且 end_date > now，按 end_date 现算）、preview 只计数
不落库、发送只落目标人群、plan 缺 plan_id / 未知 audience 报错。

到期日全部用相对 now 的固定偏移（±3/±5/±30/±40 天），与 7/30 天阈值之间
留足余量，不用墙钟窗口。
"""

import asyncio
import os
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models


@pytest.fixture()
def db():
    # StaticPool + check_same_thread=False：广播链路经 run_in_threadpool 切线程，
    # 内存库必须单连接跨线程共享（与 test_station_message_fk 同款）。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user(db, username, *, is_staff=False, is_active=True):
    u = models.WebUser(
        username=username, password_hash="x",
        is_staff=is_staff, is_active=is_active,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_plan(db, name):
    p = models.SubscriptionPlan(
        name=name, price=Decimal("1.20"), duration_days=30)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_sub(db, user, plan, *, status, end_offset_days):
    s = models.UserSubscription(
        user_id=user.id,
        plan_id=plan.id,
        status=status,
        start_date=datetime.now() - timedelta(days=30),
        end_date=datetime.now() + timedelta(days=end_offset_days),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def crowd(db):
    """固定人群（id 在断言里按名取，全是精确集合）：

    - admin：管理员、无订阅 → all / none
    - valid_a：套餐 A 有效订阅（+30 天）→ all / subscribed / plan A / expiring(30)
    - valid_b：套餐 B 有效订阅（+3 天）→ all / subscribed / plan B / expiring(7)
    - expired_date：status 仍 active 但已过期 5 天 → all / expired
    - expired_status：status=expired、过期 40 天 → all / expired
    - cancelled_future：status=cancelled、到期日在 +20 天 → 只在 all
      （无有效订阅，但最近到期日未过，不算「已过期未续费」）
    - never：无任何订阅记录 → all / none
    - inactive_valid：已停用账号但持有效订阅 → 任何人群都不含（基准 is_active）
    """
    plan_a = _make_plan(db, "Global 国际版")
    plan_b = _make_plan(db, "Global 家庭版")
    users = {
        "admin": _make_user(db, "admin_a", is_staff=True),
        "valid_a": _make_user(db, "valid_a"),
        "valid_b": _make_user(db, "valid_b"),
        "expired_date": _make_user(db, "expired_date"),
        "expired_status": _make_user(db, "expired_status"),
        "cancelled_future": _make_user(db, "cancelled_future"),
        "never": _make_user(db, "never_sub"),
        "inactive_valid": _make_user(db, "inactive_valid", is_active=False),
    }
    _make_sub(db, users["valid_a"], plan_a, status="active", end_offset_days=30)
    _make_sub(db, users["valid_b"], plan_b, status="active", end_offset_days=3)
    _make_sub(db, users["expired_date"], plan_a, status="active", end_offset_days=-5)
    _make_sub(db, users["expired_status"], plan_a, status="expired", end_offset_days=-40)
    _make_sub(db, users["cancelled_future"], plan_a, status="cancelled", end_offset_days=20)
    _make_sub(db, users["inactive_valid"], plan_a, status="active", end_offset_days=30)
    return {"users": users, "plan_a": plan_a, "plan_b": plan_b}


def _ids(crowd, *names):
    return sorted(crowd["users"][n].id for n in names)


def _resolve(db, audience, plan_id=None, days=7):
    from backend.api.admin import _resolve_broadcast_audience
    return _resolve_broadcast_audience(db, audience, plan_id, days)


def test_resolve_all(db, crowd):
    assert _resolve(db, "all") == _ids(
        crowd, "admin", "valid_a", "valid_b", "expired_date",
        "expired_status", "cancelled_future", "never")


def test_resolve_subscribed(db, crowd):
    assert _resolve(db, "subscribed") == _ids(crowd, "valid_a", "valid_b")


def test_resolve_plan(db, crowd):
    assert _resolve(db, "plan", plan_id=crowd["plan_a"].id) == _ids(crowd, "valid_a")
    assert _resolve(db, "plan", plan_id=crowd["plan_b"].id) == _ids(crowd, "valid_b")


def test_resolve_expiring(db, crowd):
    # 默认 7 天：只有 +3 天到期的 valid_b；放宽到 30 天：valid_a（+30 天）也进来
    assert _resolve(db, "expiring", days=7) == _ids(crowd, "valid_b")
    assert _resolve(db, "expiring", days=30) == _ids(crowd, "valid_a", "valid_b")


def test_resolve_expired(db, crowd):
    assert _resolve(db, "expired") == _ids(crowd, "expired_date", "expired_status")


def test_resolve_none(db, crowd):
    assert _resolve(db, "none") == _ids(crowd, "admin", "never")


def test_resolve_plan_requires_plan_id(db, crowd):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _resolve(db, "plan")
    assert exc_info.value.status_code == 400


def test_resolve_unknown_audience(db, crowd):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _resolve(db, "everybody")
    assert exc_info.value.status_code == 400


def _patch_persist_session(db):
    """让 notifications 的广播落库用测试会话（它自己 next(get_db()) 开会话）"""
    return patch("backend.notifications.get_db", new=lambda: iter([db]))


def test_preview_counts_without_persisting(db, crowd):
    from backend.api.admin import BroadcastPreviewRequest, broadcast_preview
    admin = crowd["users"]["admin"]

    result = asyncio.run(broadcast_preview(
        request=BroadcastPreviewRequest(audience="subscribed"),
        current_admin=admin, db=db))
    assert result == {"count": 2}

    result = asyncio.run(broadcast_preview(
        request=BroadcastPreviewRequest(audience="all"),
        current_admin=admin, db=db))
    assert result == {"count": 7}

    # 预览只计数：一条站内消息都不许落
    assert db.query(models.StationMessage).count() == 0


def test_broadcast_send_persists_only_to_audience(db, crowd):
    from backend.api.admin import BroadcastMessageRequest, broadcast_message
    admin = crowd["users"]["admin"]
    # 落库段会关掉它开的会话（测试里即 fixture 会话），期望集合先算好
    expected = _ids(crowd, "expired_date", "expired_status")

    with _patch_persist_session(db):
        result = asyncio.run(broadcast_message(
            request=BroadcastMessageRequest(
                title="续费提醒", content="您的订阅已过期",
                audience="expired"),
            current_admin=admin, db=db))

    assert result["notified_users"] == 2
    rows = db.query(models.StationMessage).all()
    assert sorted(r.to_user_id for r in rows) == expected
    assert all(r.title == "续费提醒" for r in rows)


def test_broadcast_send_all_keeps_legacy_behavior(db, crowd):
    """不传人群字段（旧调用方）= 全站广播：全部启用账号各一条，停用账号没有"""
    from backend.api.admin import BroadcastMessageRequest, broadcast_message
    admin = crowd["users"]["admin"]
    expected = _ids(
        crowd, "admin", "valid_a", "valid_b", "expired_date",
        "expired_status", "cancelled_future", "never")

    with _patch_persist_session(db):
        result = asyncio.run(broadcast_message(
            request=BroadcastMessageRequest(title="系统广播", content="维护通知"),
            current_admin=admin, db=db))

    assert result["notified_users"] == 7
    rows = db.query(models.StationMessage).all()
    assert sorted(r.to_user_id for r in rows) == expected


def test_notify_all_users_still_covers_all_active(db, crowd):
    """notify_all_users（公告发布等既有调用方）行为不变：全部启用账号"""
    from backend.notifications import notify_all_users
    expected = _ids(
        crowd, "admin", "valid_a", "valid_b", "expired_date",
        "expired_status", "cancelled_future", "never")

    with _patch_persist_session(db):
        count = asyncio.run(notify_all_users(
            event_type="system.broadcast", title="公告", content="内容"))

    assert count == 7
    rows = db.query(models.StationMessage).all()
    assert sorted(r.to_user_id for r in rows) == expected
