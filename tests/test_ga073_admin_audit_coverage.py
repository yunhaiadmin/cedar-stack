"""GA-073：管理员写接口操作审计留痕回归。

PR #58 的覆盖脚本实跑揪出 20 个管理员写口调用后 AdminLog 零记录
（media_sets_api 15 + panel 2 + governance_report 3）。本文件逐个
端点直调，断言每个动作落一行 AdminLog：actor=当前管理员、
action/target 与既有统一口径（backend.api.admin_core._audit）一致。

隔离口径：本文件自建内存 SQLite + 自有 session，不碰全局 engine /
SessionLocal（导入期更不换绑，防 #1668 同族污染）。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import subscriptions
from backend.api import governance_report as gov_api
from backend.api import media_sets_api as ms_api
from backend.api import panel as panel_api
from backend.emby_server import media_sets as ms
from backend.emby_server import models as em
from backend.panel_achievements import seed_achievement_defs


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    previous = subscriptions._realm_resolver
    subscriptions.set_process_realm_resolver(None)
    try:
        yield session
    finally:
        subscriptions.set_process_realm_resolver(previous)
        session.close()
        engine.dispose()


@pytest.fixture()
def admin(db):
    user = models.WebUser(username="ga073-admin", password_hash="x",
                          is_active=True, is_staff=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(autouse=True)
def _collections_on(monkeypatch):
    monkeypatch.setattr(ms, "COLLECTIONS_ENABLED", 1)


def _logs(db, action):
    return (db.query(models.AdminLog)
              .filter(models.AdminLog.action == action).all())


def _assert_one_log(db, admin, action, target_type, target_id):
    rows = _logs(db, action)
    assert len(rows) == 1, f"{action} 应落且只落一行 AdminLog，实得 {len(rows)}"
    row = rows[0]
    assert row.admin_user_id == admin.id
    assert row.target_type == target_type
    assert row.target_id == target_id
    return row


def _seed_set(db, name="合集A", source_type="manual"):
    row = em.EmbyMediaSet(name=name, source_type=source_type)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_item(db, name="片一"):
    lib = em.Library(guid=f"lib-{name}", name=f"库-{name}", is_enabled=True)
    db.add(lib)
    db.commit()
    item = em.MediaItem(guid=f"item-{name}", library_id=lib.id,
                        item_type="movie", name=name,
                        file_path=f"/data/{name}.mkv")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------- media_sets

def test_media_set_create_audits(db, admin):
    out = ms_api.create_media_set(
        ms_api.MediaSetCreate(name="审计合集", source_type="manual"),
        admin, db)
    row = _assert_one_log(db, admin, "media_set_create",
                          "media_set", out["id"])
    assert row.details["source_type"] == "manual"


def test_media_set_create_from_hot_leaderboard_audits(db, admin, monkeypatch):
    item = _seed_item(db)
    from backend.emby_server import portal
    monkeypatch.setattr(
        portal, "hot_leaderboard_items",
        lambda s, w: ([{"item_id": item.id}], {}))
    monkeypatch.setattr(
        portal, "leaderboard_window",
        lambda period: (datetime(2026, 11, 11), datetime(2026, 11, 11)))
    out = ms_api.instantiate_from_hot_leaderboard(
        ms_api.HotBoardInstantiate(period="day"), admin, db)
    assert out["created"] is True
    row = _assert_one_log(db, admin, "media_set_create_from_hot_leaderboard",
                          "media_set", out["id"])
    assert row.details["members"] == 1


def test_media_set_create_from_genre_theme_audits(db, admin, monkeypatch):
    item = _seed_item(db)
    monkeypatch.setattr(ms, "genre_theme_item_ids",
                        lambda s, genre, **kw: [item.id])
    out = ms_api.instantiate_from_genre_theme(
        ms_api.GenreThemeInstantiate(genre="剧情"), admin, db)
    assert out["created"] is True
    row = _assert_one_log(db, admin, "media_set_create_from_genre_theme",
                          "media_set", out["id"])
    assert row.details["genre"] == "剧情"


def test_media_set_create_from_person_audits(db, admin):
    out = ms_api.instantiate_from_person(
        ms_api.PersonSetInstantiate(person_key="12345"), admin, db)
    _assert_one_log(db, admin, "media_set_create_from_person",
                    "media_set", out["id"])


def test_media_set_update_audits(db, admin):
    row = _seed_set(db)
    ms_api.patch_media_set(row.id, ms_api.MediaSetPatch(name="改名"),
                           admin, db)
    log = _assert_one_log(db, admin, "media_set_update",
                          "media_set", row.id)
    assert log.details["fields"] == ["name"]


def test_media_set_delete_audits(db, admin):
    row = _seed_set(db)
    ms_api.delete_media_set(row.id, admin, db)
    log = _assert_one_log(db, admin, "media_set_delete",
                          "media_set", row.id)
    assert log.details["name"] == "合集A"


def test_media_set_order_audits(db, admin):
    a = _seed_set(db, "合集A")
    b = _seed_set(db, "合集B")
    body = ms_api.SetOrderBody(orders=[
        ms_api.OrderEntry(id=a.id, sort_order=2),
        ms_api.OrderEntry(id=b.id, sort_order=1)])
    ms_api.order_media_sets(body, admin, db)
    log = _assert_one_log(db, admin, "media_set_order", "media_set", None)
    assert log.details["updated"] == 2


def test_media_set_member_add_audits(db, admin):
    row = _seed_set(db)
    item = _seed_item(db)
    ms_api.add_member(row.id, ms_api.MemberAdd(item_id=item.id,
                                               order_index=1), admin, db)
    log = _assert_one_log(db, admin, "media_set_member_add",
                          "media_set", row.id)
    assert log.details["item_id"] == item.id


def test_media_set_member_remove_audits(db, admin):
    row = _seed_set(db)
    item = _seed_item(db)
    db.add(em.EmbyMediaSetMember(set_id=row.id, item_id=item.id,
                                 order_index=1, member_source="manual"))
    db.commit()
    ms_api.remove_member(row.id, item.id, admin, db)
    log = _assert_one_log(db, admin, "media_set_member_remove",
                          "media_set", row.id)
    assert log.details["item_id"] == item.id


def test_media_set_member_order_audits(db, admin):
    row = _seed_set(db)
    item = _seed_item(db)
    db.add(em.EmbyMediaSetMember(set_id=row.id, item_id=item.id,
                                 order_index=1, member_source="manual"))
    db.commit()
    body = ms_api.MemberOrderBody(orders=[
        ms_api.MemberOrderEntry(item_id=item.id, order_index=5)])
    ms_api.order_members(row.id, body, admin, db)
    log = _assert_one_log(db, admin, "media_set_member_order",
                          "media_set", row.id)
    assert log.details["updated"] == 1


def test_media_set_rule_add_audits(db, admin):
    row = _seed_set(db, source_type="rule")
    ms_api.add_rule(row.id, ms_api.RuleCreate(rule_kind="genre",
                                              value_text="剧情"), admin, db)
    log = _assert_one_log(db, admin, "media_set_rule_add",
                          "media_set", row.id)
    assert log.details["rule_kind"] == "genre"


def test_media_set_rule_delete_audits(db, admin):
    row = _seed_set(db, source_type="rule")
    rule = em.EmbyMediaSetRule(set_id=row.id, rule_kind="genre",
                               is_exclusion=False, value_text="剧情")
    db.add(rule)
    db.commit()
    db.refresh(rule)
    ms_api.delete_rule(row.id, rule.id, admin, db)
    log = _assert_one_log(db, admin, "media_set_rule_delete",
                          "media_set", row.id)
    assert log.details["rule_id"] == rule.id


def test_media_set_refresh_audits(db, admin):
    row = _seed_set(db)
    ms_api.refresh_set(row.id, admin, db)
    log = _assert_one_log(db, admin, "media_set_refresh",
                          "media_set", row.id)
    assert log.details["status"] == "done"


def test_media_set_template_create_audits(db, admin):
    out = ms_api.create_template(
        ms_api.TemplateCreate(
            name="模板A",
            rules=[{"rule_kind": "genre", "value_text": "剧情"}]),
        admin, db)
    row = _assert_one_log(db, admin, "media_set_template_create",
                          "media_set_template", out["id"])
    assert row.details["rules"] == 1


def test_media_set_template_instantiate_audits(db, admin):
    tpl = ms_api.create_template(
        ms_api.TemplateCreate(
            name="模板B",
            rules=[{"rule_kind": "genre", "value_text": "剧情"}]),
        admin, db)
    db.query(models.AdminLog).delete()
    db.commit()
    out = ms_api.instantiate_template(
        tpl["id"], ms_api.TemplateInstantiate(), admin, db)
    row = _assert_one_log(db, admin, "media_set_template_instantiate",
                          "media_set_template", tpl["id"])
    assert row.details["media_set_id"] == out["id"]


# ---------------------------------------------------------------- panel

def test_panel_pet_stages_put_audits(db, admin):
    body = panel_api.PetStagesIn(stages=[
        {"name": "蛋", "icon": "🥚", "hours": 0},
        {"name": "馆灵", "icon": "🐉", "hours": 100}])
    panel_api.admin_pet_stages_put(body, admin, db)
    row = _assert_one_log(db, admin, "panel_pet_stages_update",
                          "system_config", None)
    assert row.details["key"] == "panel_pet_stages"
    assert row.details["stages"] == 2


def test_panel_achievement_put_audits(db, admin):
    seed_achievement_defs(db)
    body = panel_api.AchievementDefIn(
        name="回头客", desc="隔月再看", icon="🌀", sort=5, enabled=False)
    panel_api.admin_achievements_put("revisit", body, admin, db)
    row = _assert_one_log(db, admin, "panel_achievement_update",
                          "panel_achievement", None)
    assert row.details["key"] == "revisit"
    assert row.details["enabled"] is False


# ---------------------------------------------------------------- governance

def _seed_governance(db):
    realm = models.ServerRealm(name="公益服", slug="main",
                               access_mode="free", is_active=True)
    db.add(realm)
    db.commit()
    db.add(models.SystemConfig(key="active_realm_id", value=str(realm.id)))
    reporter = models.WebUser(username="reporter", password_hash="x",
                              is_active=True)
    target = models.WebUser(username="target", password_hash="x",
                            is_active=True)
    db.add_all([reporter, target])
    db.commit()
    db.refresh(reporter)
    db.refresh(target)
    return reporter, target


def test_governance_report_decide_audits(db, admin):
    reporter, _target = _seed_governance(db)
    report = models.GovernanceReport(
        reporter_id=reporter.id, target_type="media_seek", target_id=1,
        reason="spam", status="escalated")
    db.add(report)
    db.commit()
    db.refresh(report)
    gov_api.admin_decide_report(
        report.id, gov_api.DecideIn(decision="accepted"), db, admin)
    row = _assert_one_log(db, admin, "governance_report_decide",
                          "governance_report", report.id)
    assert row.details["decision"] == "accepted"


def test_governance_suspension_create_audits(db, admin):
    _reporter, target = _seed_governance(db)
    out = gov_api.admin_create_suspension(
        gov_api.SuspendIn(user_id=target.id, days=7), db, admin)
    row = _assert_one_log(db, admin, "governance_suspension_create",
                          "user", target.id)
    assert row.details["days"] == 7
    assert row.details["suspension_id"] == out["id"]


def test_governance_suspension_revoke_audits(db, admin):
    _reporter, target = _seed_governance(db)
    out = gov_api.admin_create_suspension(
        gov_api.SuspendIn(user_id=target.id, days=7), db, admin)
    db.query(models.AdminLog).delete()
    db.commit()
    gov_api.admin_revoke_suspension(out["id"], db, admin)
    _assert_one_log(db, admin, "governance_suspension_revoke",
                    "governance_suspension", out["id"])
