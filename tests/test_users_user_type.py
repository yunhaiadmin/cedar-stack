"""用户管理 user_type 筛选回归测试。

（原 v2.55 公益服并入用户管理；welfare 类型已随福利服役族于 2026-11-14
删除，现只有 paid=有生效中订阅 / normal=其余两类；存量 welfare 筛选值
按未知值回落全部。）全部用隔离的内存 SQLite，不碰生产库。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.api.admin import list_users


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, username):
    u = models.WebUser(username=username, email=f"{username}@t.com", password_hash="x")
    db.add(u)
    db.commit()
    return u


def _sub(db, user_id, days_left=30):
    plan = models.SubscriptionPlan(name="test-plan", price=9.99, duration_days=30)
    db.add(plan)
    db.commit()
    s = models.UserSubscription(user_id=user_id, plan_id=plan.id, status="active",
                                end_date=datetime.now() + timedelta(days=days_left))
    db.add(s)
    db.commit()
    return s


@pytest.fixture()
def seeded(db):
    _user(db, "plain_one")
    _user(db, "plain_two")
    p1 = _user(db, "paid_user")
    _sub(db, p1.id, days_left=30)
    p2 = _user(db, "paid_user_two")
    _sub(db, p2.id, days_left=30)
    _user(db, "normal_user")
    return db


def _names(res):
    return sorted(u["username"] for u in res["users"])


def test_filter_paid(seeded):
    res = list_users(user_type="paid", db=seeded, current_admin=None)
    assert _names(res) == ["paid_user", "paid_user_two"]


def test_filter_normal(seeded):
    res = list_users(user_type="normal", db=seeded, current_admin=None)
    assert _names(res) == ["normal_user", "plain_one", "plain_two"]


def test_filter_all_default(seeded):
    res = list_users(db=seeded, current_admin=None)
    assert res["total"] == 5


def test_filter_invalid_falls_back_to_all(seeded):
    res = list_users(user_type="bogus", db=seeded, current_admin=None)
    assert res["total"] == 5


def test_filter_welfare_falls_back_to_all(seeded):
    # 存量筛选值 welfare 已无对应类型：按未知值回落全部，不报错
    res = list_users(user_type="welfare", db=seeded, current_admin=None)
    assert res["total"] == 5


def test_row_fields(seeded):
    res = list_users(db=seeded, current_admin=None)
    by_name = {u["username"]: u for u in res["users"]}
    assert by_name["paid_user"]["user_type"] == "paid"
    assert by_name["normal_user"]["user_type"] == "normal"
    # 福利四字段已随列删除，不再出现在行 DTO 里
    for row in res["users"]:
        assert "is_welfare" not in row
        assert "welfare_expires_at" not in row
        assert "welfare_grant_channel" not in row
