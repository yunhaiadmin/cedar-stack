# -*- coding: utf-8 -*-
"""团队功能（2026-11-13，照 UHDNow 团队形态）端点回归测试。

覆盖：
- 配置默认值（起订 10TB / 子账号上限 100 / 未开通成员上限 5），全部来自
  SystemConfig 的 team 家族，改配置即改判定（不写死数值）；
- 建团/邀请码入团/成员上限/退出/团长移除/解散；
- 套餐申请校验（起订、子账号上限、重复待审、成员代团长申请被拒）；
- 管理端审核：通过→新建团队并开通套餐 / 通过→已有团队开通 /
  申请人已入他团时通过被拦 / 驳回→状态回退且不开通；
- 管理端配置保存整批校验。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api.admin_core import get_current_admin
from backend.api.user import get_current_user
from backend.database import get_db

TB = 10**12


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_user(db, username: str, **kw) -> models.WebUser:
    user = models.WebUser(
        username=username, password_hash="x", is_active=True, **kw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def users(db):
    return {
        "leader": _make_user(db, "teamleader"),
        "member": _make_user(db, "teammate"),
        "member2": _make_user(db, "teammate2"),
        "member3": _make_user(db, "teammate3"),
        "admin": _make_user(db, "teamadmin", is_staff=True, admin_role="super"),
    }


@pytest.fixture()
def client(db, users):
    from backend.main import app

    current = {"user": users["leader"]}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    app.dependency_overrides[get_current_admin] = lambda: users["admin"]
    try:
        yield TestClient(app, raise_server_exceptions=False), current
    finally:
        app.dependency_overrides.clear()


def _set_config(db, key: str, value: str) -> None:
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(models.SystemConfig(key=key, value=value))
    db.commit()


def _create_team(client, current, users, name="星际小队") -> dict:
    current["user"] = users["leader"]
    resp = client.post("/api/user/teams/create", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["team"]


# ==================== 配置 ====================

def test_config_defaults(client):
    c, _ = client
    resp = c.get("/api/user/teams/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team_plan_min_traffic_bytes"] == 10 * TB
    assert body["team_plan_max_members"] == 100
    assert body["team_default_member_limit"] == 5


def test_admin_config_roundtrip_and_validation(client, db):
    c, _ = client
    resp = c.put("/api/admin/teams/config",
                 json={"team_plan_max_members": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json()["team_plan_max_members"] == 3
    # 非法值整批拒绝（0 不在 1~ 范围内），且不落库
    resp = c.put("/api/admin/teams/config",
                 json={"team_plan_max_members": 0})
    assert resp.status_code == 400, resp.text
    resp = c.get("/api/admin/teams/config")
    assert resp.json()["team_plan_max_members"] == 3


# ==================== 建团 / 入团 / 成员管理 ====================

def test_create_team_and_mine(client, users):
    c, current = client
    team = _create_team(c, current, users)
    assert team["my_role"] == "owner"
    assert team["member_count"] == 1
    assert team["member_limit"] == 5  # 未开通套餐按默认上限
    assert team["plan_status"] == "none"
    assert team["invite_code"]

    resp = c.get("/api/user/teams/mine")
    assert resp.json()["team"]["name"] == "星际小队"
    # 已在团里再建团 → 拒绝
    resp = c.post("/api/user/teams/create", json={"name": "第二团"})
    assert resp.status_code == 400


def test_join_by_invite_code(client, users):
    c, current = client
    team = _create_team(c, current, users)
    code = team["invite_code"]

    current["user"] = users["member"]
    resp = c.post("/api/user/teams/join", json={"invite_code": code.lower()})
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["my_role"] == "member"
    assert resp.json()["team"]["member_count"] == 2
    # 重复入团被拒；错码 404
    resp = c.post("/api/user/teams/join", json={"invite_code": code})
    assert resp.status_code == 400
    current["user"] = users["member2"]
    resp = c.post("/api/user/teams/join", json={"invite_code": "ZZZZ9999"})
    assert resp.status_code == 404


def test_member_limit_uses_config(client, users, db):
    c, current = client
    _set_config(db, "team_default_member_limit", "2")
    team = _create_team(c, current, users)
    code = team["invite_code"]
    current["user"] = users["member"]
    assert c.post("/api/user/teams/join",
                  json={"invite_code": code}).status_code == 200
    current["user"] = users["member2"]
    resp = c.post("/api/user/teams/join", json={"invite_code": code})
    assert resp.status_code == 400
    assert "已满" in resp.json()["detail"]


def test_leave_remove_dissolve(client, users):
    c, current = client
    team = _create_team(c, current, users)
    code = team["invite_code"]
    current["user"] = users["member"]
    c.post("/api/user/teams/join", json={"invite_code": code})
    # 团长不能直接退出
    current["user"] = users["leader"]
    resp = c.post("/api/user/teams/leave")
    assert resp.status_code == 400
    # 团长移除成员
    resp = c.post(f"/api/user/teams/members/{users['member'].id}/remove")
    assert resp.status_code == 200, resp.text
    current["user"] = users["member"]
    assert c.get("/api/user/teams/mine").json()["team"] is None
    # 成员退出（重新加入后）
    c.post("/api/user/teams/join", json={"invite_code": code})
    resp = c.post("/api/user/teams/leave")
    assert resp.status_code == 200
    # 团长解散
    current["user"] = users["leader"]
    resp = c.post("/api/user/teams/dissolve")
    assert resp.status_code == 200
    assert c.get("/api/user/teams/mine").json()["team"] is None
    # 解散后邀请码失效
    current["user"] = users["member2"]
    resp = c.post("/api/user/teams/join", json={"invite_code": code})
    assert resp.status_code == 404


# ==================== 套餐申请 ====================

def _submit_app(c, **over):
    payload = {
        "team_name": "远航团",
        "line": "国际方向",
        "traffic_bytes": 20 * TB,
        "member_limit": 50,
        "note": "客服已联系",
    }
    payload.update(over)
    return c.post("/api/user/teams/applications", json=payload)


def test_application_validation(client, users, db):
    c, current = client
    current["user"] = users["leader"]
    # 低于起订 → 400
    resp = _submit_app(c, traffic_bytes=5 * TB)
    assert resp.status_code == 400
    # 子账号超过上限 → 400（上限改配置验证不写死）
    _set_config(db, "team_plan_max_members", "10")
    resp = _submit_app(c, member_limit=50)
    assert resp.status_code == 400
    # 合法 → pending；再提一份 → 400
    resp = _submit_app(c, member_limit=10)
    assert resp.status_code == 200, resp.text
    assert resp.json()["application"]["status"] == "pending"
    resp = _submit_app(c, member_limit=10)
    assert resp.status_code == 400


def test_member_cannot_apply_for_others_team(client, users):
    c, current = client
    team = _create_team(c, current, users)
    current["user"] = users["member"]
    c.post("/api/user/teams/join", json={"invite_code": team["invite_code"]})
    resp = _submit_app(c, team_name=None)
    assert resp.status_code == 400


def test_admin_approve_creates_team_with_plan(client, users):
    c, current = client
    current["user"] = users["leader"]
    app_id = _submit_app(c).json()["application"]["id"]

    resp = c.get("/api/admin/teams/applications")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = c.post(f"/api/admin/teams/applications/{app_id}/approve",
                  json={"review_note": "已收款"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["application"]["status"] == "approved"

    mine = c.get("/api/user/teams/mine").json()
    assert mine["team"]["name"] == "远航团"
    assert mine["team"]["my_role"] == "owner"
    assert mine["team"]["plan_status"] == "active"
    assert mine["team"]["plan_traffic_bytes"] == 20 * TB
    assert mine["team"]["plan_member_limit"] == 50
    assert mine["team"]["member_limit"] == 50
    # 重复审核被拒
    resp = c.post(f"/api/admin/teams/applications/{app_id}/approve",
                  json={})
    assert resp.status_code == 400


def test_admin_approve_existing_team_plan(client, users):
    c, current = client
    _create_team(c, current, users)
    resp = _submit_app(c, team_name=None)
    app_id = resp.json()["application"]["id"]
    # 提交后团队进入审核中
    mine = c.get("/api/user/teams/mine").json()
    assert mine["team"]["plan_status"] == "pending"

    resp = c.post(f"/api/admin/teams/applications/{app_id}/approve",
                  json={})
    assert resp.status_code == 200, resp.text
    mine = c.get("/api/user/teams/mine").json()
    assert mine["team"]["plan_status"] == "active"
    assert mine["team"]["plan_line"] == "国际方向"


def test_admin_reject_rolls_back_pending(client, users):
    c, current = client
    _create_team(c, current, users)
    app_id = _submit_app(c, team_name=None).json()["application"]["id"]
    resp = c.post(f"/api/admin/teams/applications/{app_id}/reject",
                  json={"review_note": "信息不全"})
    assert resp.status_code == 200, resp.text
    body = resp.json()["application"]
    assert body["status"] == "rejected"
    assert body["review_note"] == "信息不全"
    mine = c.get("/api/user/teams/mine").json()
    assert mine["team"]["plan_status"] == "none"


def test_admin_approve_blocked_when_applicant_in_other_team(client, users):
    c, current = client
    # leader 先提交建团申请
    current["user"] = users["leader"]
    app_id = _submit_app(c).json()["application"]["id"]
    # member 自建一团，把 leader 拉进去当成员
    current["user"] = users["member"]
    resp = c.post("/api/user/teams/create", json={"name": "别人的团"})
    code = resp.json()["team"]["invite_code"]
    current["user"] = users["leader"]
    c.post("/api/user/teams/join", json={"invite_code": code})
    # 审核通过应被拦下（申请人已是他团成员）
    resp = c.post(f"/api/admin/teams/applications/{app_id}/approve",
                  json={})
    assert resp.status_code == 400
    assert "其他团队" in resp.json()["detail"]
