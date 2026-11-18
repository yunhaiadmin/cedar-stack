# -*- coding: utf-8 -*-
"""播放线路流量配额（2026-11-19 用户点选；粒度经用户纠正为「线路」）。

形态：服（ServerRealm）是服务器本身；大类是线路自己的分类
（ServerRealmLine.category，如「国际线路」），每个大类下挂多条
线路（主/副之分按排序）。配额、用量、重置都按线路计。

钉住的口径：
- 计量旁路：traffic_meter 接受计量的同一笔字节（中继/本机文件腿、
  实际 yield 字节）再按「请求实际进来的入口 host」记一份线路日账
  （入口 host 与线路 url 的 host 相同即归该线）；匹配不到任何线路
  只记用户账、不记线路账；无用户归属不计、302 直链只记次不记
  字节——与用户流量账完全同口径；
- 入口 host 取法：可信反代采信 X-Forwarded-Host、否则 Host 头
  （public_url.request_entry_host），归一口径两边一致（小写、
  默认端口不进键）；
- 配额：quota_bytes=0 不限额；已用 = 线路日账总和 − 重置偏移；
  用尽判定 used >= quota；一服两线各自计数、互不串账；
- 重置：逐线重置只归零该线（当刻总和快照为偏移，同日重置前的
  字节也不再算），之后的新字节重新计；
- 迁移兜底：服没有线路时按现行对外地址（realm.url 优先）自动
  生成一条「主线路」；新建带地址的服同步生成主线路；
- 分流下发：账号卡每服带 lines（主线路在前），每线带
  quota_exhausted；单线用尽只标该线，全部线路用尽时整体回落
  清零（不断播）；地址照常下发；
- 管理接口：线路增删改查（负配额 422、坏地址 400、跨服线路
  404）、逐线 reset-traffic。
- 挪服：更新线路时改选所属服只改 realm_id——线路 id 不变，
  流量日账（按 line_id）/配额/周期随线路走，账号卡按新归属
  下发；目标服不存在 404；主/副按两边各自排序自动重算；
  原服一条启用线路都不剩时账号卡 lines 为空、回退 base_url
  单行（现行口径，不动）。
- 大类与订阅打通：线路带 category（缺省「国际线路」，可自建）；
  套餐用 entitlements.line_category 声明大类（未声明按缺省）；
  账号卡下发时按用户有效套餐的大类过滤——国际套餐只见国际线、
  国内套餐只见国内线；团队资格无大类口径时回退订阅口径，再无
  则不过滤（纯浏览全量）；过滤在服务端 payload 层，base_url
  回退行不受影响。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend import line_quota, models, realms
from backend.emby_server import models as em
from backend.emby_server import portal, public_url, traffic_meter as tm

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


def _realm(db, slug, url=""):
    _seq[0] += 1
    r = models.ServerRealm(
        name=f"大类-{slug}-{_seq[0]}", slug=f"{slug}{_seq[0]}", url=url)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _line(db, realm, url, name="", quota=0):
    return realms.create_line(db, realm, name=name, url=url,
                              quota_bytes=quota)


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


def _seed_usage(db, line_id, relay=0, local=0, day=DAY):
    row = em.LineTrafficUsageDaily(
        line_id=line_id, day=day, relay_bytes=relay, local_bytes=local)
    db.add(row)
    db.commit()
    line_quota.invalidate()
    return row


def _line_row(db, line_id, day=DAY):
    return (db.query(em.LineTrafficUsageDaily)
            .filter_by(line_id=line_id, day=day).first())


# ---------------------------------------------------------------------------
# 入口 host：取法与归一
# ---------------------------------------------------------------------------

def _request(peer, headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": raw, "client": (peer, 5000)})


def test_entry_host_trusted_proxy_uses_forwarded_host():
    req = _request("127.0.0.1", {"host": "internal:8000",
                                 "x-forwarded-host": "cdn.example.com"})
    assert public_url.request_entry_host(req) == "cdn.example.com"


def test_entry_host_untrusted_peer_ignores_forwarded_host():
    req = _request("203.0.113.7", {"host": "emby.example.com",
                                   "x-forwarded-host": "evil.example.com"})
    assert public_url.request_entry_host(req) == "emby.example.com"


def test_host_key_normalization_matches_both_sides():
    nk = line_quota.normalize_entry_host
    assert nk("CDN.Example.COM:443") == "cdn.example.com"
    assert nk("example.com:8443") == "example.com:8443"
    assert nk(" example.com ") == "example.com"
    assert nk("a b.com") == ""
    assert nk("") == ""
    # 线路 url 侧与请求头侧归一到同一把键
    assert line_quota.host_key_of_url("https://cdn.example.com") == "cdn.example.com"
    assert line_quota.host_key_of_url("https://cdn.example.com:443/x") == "cdn.example.com"
    assert line_quota.host_key_of_url("http://example.com:8443") == "example.com:8443"
    assert line_quota.host_key_of_url("") == ""


# ---------------------------------------------------------------------------
# 计量旁路：字节按入口 host 归线路
# ---------------------------------------------------------------------------

def test_meter_attributes_bytes_by_entry_host(db):
    realm = _realm(db, "main", url="https://cdn.example.com")
    main = realms.ensure_realm_lines(db, realm)[0]  # 自动主线路
    sub = _line(db, realm, "https://www.example.com")
    user = _user(db)
    tm.note_bytes(600, "relay", user_id=user.id,
                  entry_host="cdn.example.com", day=DAY)
    tm.note_bytes(400, "local", user_id=user.id,
                  entry_host="www.example.com", day=DAY)
    tm.note_direct_play(user_id=user.id, day=DAY)
    assert tm.flush_now(db) == 1  # 用户账一行
    # 两条线各记各的，互不串账
    assert _line_row(db, main.id).relay_bytes == 600
    assert _line_row(db, main.id).local_bytes == 0
    assert _line_row(db, sub.id).relay_bytes == 0
    assert _line_row(db, sub.id).local_bytes == 400
    # 用户账口径不被旁路影响：直链只记次、字节分腿
    urow = db.query(em.TrafficUsageDaily).filter_by(user_id=user.id).first()
    assert (urow.relay_bytes, urow.local_bytes, urow.direct_plays) == (600, 400, 1)
    # 再落一笔：同线同日累计而不是覆盖
    tm.note_bytes(100, "relay", user_id=user.id,
                  entry_host="cdn.example.com", day=DAY)
    tm.flush_now(db)
    db.expire_all()
    assert _line_row(db, main.id).relay_bytes == 700


def test_meter_unmatched_host_counts_user_only(db):
    realm = _realm(db, "main", url="https://cdn.example.com")
    realms.ensure_realm_lines(db, realm)
    user = _user(db)
    tm.note_bytes(999, "relay", user_id=user.id,
                  entry_host="unknown.example.com", day=DAY)
    assert tm.flush_now(db) == 1  # 用户账照记
    assert db.query(em.LineTrafficUsageDaily).count() == 0
    assert tm.stats()["line_skipped_no_match"] == 999


def test_meter_no_entry_host_no_line_accounting(db):
    _realm(db, "main", url="https://cdn.example.com")
    user = _user(db)
    tm.note_bytes(500, "relay", user_id=user.id, day=DAY)
    tm.flush_now(db)
    assert db.query(em.LineTrafficUsageDaily).count() == 0
    assert db.query(em.TrafficUsageDaily).count() == 1


def test_meter_no_user_no_bytes_anywhere(db):
    _realm(db, "main", url="https://cdn.example.com")
    tm.note_bytes(999, "relay", user_id=None,
                  entry_host="cdn.example.com", day=DAY)
    assert tm.flush_now(db) == 0
    assert db.query(em.LineTrafficUsageDaily).count() == 0
    assert db.query(em.TrafficUsageDaily).count() == 0


# ---------------------------------------------------------------------------
# 线路生成：迁移兜底与新建同步
# ---------------------------------------------------------------------------

def test_ensure_generates_main_line_from_realm_url(db):
    realm = _realm(db, "mig", url="https://cdn.example.com")
    lines = realms.ensure_realm_lines(db, realm)
    assert len(lines) == 1
    assert lines[0].name == "主线路"
    assert lines[0].url == "https://cdn.example.com"
    # 再调不重复生成
    assert len(realms.ensure_realm_lines(db, realm)) == 1


def test_ensure_no_url_no_line(db):
    realm = _realm(db, "empty", url="")
    assert realms.ensure_realm_lines(db, realm) == []


def test_create_realm_with_url_generates_main_line(db):
    realm = realms.create_realm(db, name="国际线路x", slug="intlx",
                                url="https://intl.example.com")
    lines = realms.list_lines(db, realm.id)
    assert len(lines) == 1
    assert lines[0].name == "主线路"
    assert realms.primary_line_id(db, realm.id) == lines[0].id


def test_second_line_defaults_to_backup_name(db):
    realm = _realm(db, "names", url="https://a.example.com")
    realms.ensure_realm_lines(db, realm)
    second = _line(db, realm, "https://b.example.com")
    assert second.name == "副线路"


# ---------------------------------------------------------------------------
# 配额判定：不限 / 未用尽 / 用尽 / 两线独立
# ---------------------------------------------------------------------------

def test_quota_zero_is_unlimited(db):
    realm = _realm(db, "free")
    line = _line(db, realm, "https://a.example.com", quota=0)
    _seed_usage(db, line.id, relay=500 * GB)
    view = line_quota.quota_view(db, line)
    assert view["quota_bytes"] == 0
    assert view["used_bytes"] == 500 * GB
    assert view["remaining_bytes"] is None
    assert view["exhausted"] is False


def test_quota_exhaustion_and_remaining(db):
    realm = _realm(db, "cap")
    line = _line(db, realm, "https://a.example.com", quota=1000)
    _seed_usage(db, line.id, relay=600)
    view = line_quota.quota_view(db, line)
    assert view["used_bytes"] == 600
    assert view["remaining_bytes"] == 400
    assert view["exhausted"] is False
    _seed_usage(db, line.id, relay=400, day=date(2026, 11, 20))
    view = line_quota.quota_view(db, line)
    assert view["used_bytes"] == 1000
    assert view["remaining_bytes"] == 0
    assert view["exhausted"] is True


def test_two_lines_same_realm_count_independently(db):
    realm = _realm(db, "two")
    a = _line(db, realm, "https://a.example.com", quota=1000)
    b = _line(db, realm, "https://b.example.com", quota=1000)
    _seed_usage(db, a.id, relay=1000)
    _seed_usage(db, b.id, relay=10)
    assert line_quota.is_exhausted(db, a) is True
    assert line_quota.is_exhausted(db, b) is False
    assert line_quota.quota_view(db, b)["used_bytes"] == 10


def test_update_line_rejects_negative_quota(db):
    realm = _realm(db, "neg")
    line = _line(db, realm, "https://a.example.com")
    with pytest.raises(ValueError):
        realms.update_line(db, line, quota_bytes=-1)


# ---------------------------------------------------------------------------
# 重置：逐线归零，只动该线
# ---------------------------------------------------------------------------

def test_reset_only_that_line_same_day(db):
    realm = _realm(db, "rst")
    a = _line(db, realm, "https://a.example.com", quota=1000)
    b = _line(db, realm, "https://b.example.com", quota=1000)
    _seed_usage(db, a.id, relay=1500)  # 今天就已超额
    _seed_usage(db, b.id, relay=700)
    assert line_quota.is_exhausted(db, a) is True
    line_quota.reset_period(db, a)
    view_a = line_quota.quota_view(db, a)
    assert view_a["used_bytes"] == 0
    assert view_a["exhausted"] is False
    assert view_a["period_started_at"] is not None
    # 另一条线一字未动
    assert line_quota.quota_view(db, b)["used_bytes"] == 700
    # 重置后的新字节重新计（同一天的行继续累计，靠偏移抵扣旧账）
    row = _line_row(db, a.id)
    row.relay_bytes += 300
    db.commit()
    line_quota.invalidate()
    assert line_quota.quota_view(db, a)["used_bytes"] == 300


# ---------------------------------------------------------------------------
# 账号卡分流标记：按线下发 / 单线用尽只标该线 / 全线用尽回落清零
# ---------------------------------------------------------------------------

def _cards(db, user):
    return {c["id"]: c for c in portal._user_realm_cards(user, db, None)}


def test_card_lines_shape_and_single_exhausted(db):
    a = _realm(db, "a", url="https://a.example.com")
    realms.ensure_realm_lines(db, a)
    b = _realm(db, "b", url="https://b1.example.com")
    b_main = realms.ensure_realm_lines(db, b)[0]
    b_sub = _line(db, b, "https://b2.example.com", quota=0)
    realms.update_line(db, b_main, quota_bytes=100)
    user = _user(db)
    _sub(db, user, a)
    _sub(db, user, b)
    _seed_usage(db, b_main.id, relay=100)
    cards = _cards(db, user)
    # 线路按序下发：主线路在前、副线路其次
    b_lines = cards[b.id]["lines"]
    assert [l["name"] for l in b_lines] == ["主线路", "副线路"]
    assert b_lines[0]["is_primary"] is True
    assert b_lines[1]["is_primary"] is False
    # 单线用尽只标该线
    assert b_lines[0]["quota_exhausted"] is True
    assert b_lines[1]["quota_exhausted"] is False
    assert cards[a.id]["lines"][0]["quota_exhausted"] is False
    # 卡级标记 = 主线路标记；地址照常下发（已连客户端不受影响）
    assert cards[b.id]["quota_exhausted"] is True
    assert cards[b.id]["base_url"] == "https://b1.example.com"
    assert b_lines[0]["url"] == "https://b1.example.com"


def test_card_flags_all_lines_exhausted_fallback(db):
    a = _realm(db, "a", url="https://a.example.com")
    a_main = realms.ensure_realm_lines(db, a)[0]
    realms.update_line(db, a_main, quota_bytes=100)
    b = _realm(db, "b", url="https://b.example.com")
    b_main = realms.ensure_realm_lines(db, b)[0]
    realms.update_line(db, b_main, quota_bytes=100)
    user = _user(db)
    _sub(db, user, a)
    _sub(db, user, b)
    _seed_usage(db, a_main.id, relay=100)
    _seed_usage(db, b_main.id, relay=250)
    cards = _cards(db, user)
    # 全部线路用尽 → 整体回落：标记清零，一切照现行口径（不断播）
    for card in cards.values():
        assert card["quota_exhausted"] is False
        for line in card["lines"]:
            assert line["quota_exhausted"] is False


# ---------------------------------------------------------------------------
# 管理接口：线路增删改查 / 逐线重置 / 负数拒绝 / 跨服 404
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


def test_admin_api_line_crud_and_reset(admin_client, db):
    realm = _realm(db, "api", url="https://cdn.example.com")
    # 列表：迁移兜底生成的主线路在列
    resp = admin_client.get(f"/api/admin/realms/{realm.id}/lines")
    assert resp.status_code == 200
    lines = resp.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["name"] == "主线路"
    assert lines[0]["is_primary"] is True
    main_id = lines[0]["id"]
    # 新增副线路（带配额）
    resp = admin_client.post(
        f"/api/admin/realms/{realm.id}/lines",
        json={"url": "https://www.example.com", "quota_bytes": 2048})
    assert resp.status_code == 200
    sub_line = resp.json()["line"]
    assert sub_line["name"] == "副线路"
    assert sub_line["traffic"]["quota_bytes"] == 2048
    # 改配额
    resp = admin_client.put(
        f"/api/admin/realms/{realm.id}/lines/{sub_line['id']}",
        json={"quota_bytes": 4096})
    assert resp.status_code == 200
    assert resp.json()["line"]["traffic"]["quota_bytes"] == 4096
    # 用尽后逐线重置：只归零该线
    _seed_usage(db, sub_line["id"], relay=4096)
    _seed_usage(db, main_id, relay=123)
    resp = admin_client.get("/api/admin/realms")
    row = next(r for r in resp.json()["realms"] if r["id"] == realm.id)
    by_id = {l["id"]: l for l in row["lines"]}
    assert by_id[sub_line["id"]]["traffic"]["exhausted"] is True
    resp = admin_client.post(
        f"/api/admin/realms/{realm.id}/lines/{sub_line['id']}/reset-traffic")
    assert resp.status_code == 200
    traffic = resp.json()["line"]["traffic"]
    assert traffic["used_bytes"] == 0
    assert traffic["exhausted"] is False
    assert line_quota.quota_view(db, realms.get_line(db, main_id))["used_bytes"] == 123
    # 删除副线路
    resp = admin_client.delete(
        f"/api/admin/realms/{realm.id}/lines/{sub_line['id']}")
    assert resp.status_code == 200
    resp = admin_client.get(f"/api/admin/realms/{realm.id}/lines")
    assert [l["id"] for l in resp.json()["lines"]] == [main_id]


def test_admin_api_line_negative_quota_rejected(admin_client, db):
    realm = _realm(db, "api2", url="https://cdn.example.com")
    resp = admin_client.post(
        f"/api/admin/realms/{realm.id}/lines",
        json={"url": "https://x.example.com", "quota_bytes": -5})
    assert resp.status_code == 422


def test_admin_api_line_bad_url_rejected(admin_client, db):
    realm = _realm(db, "api3", url="https://cdn.example.com")
    resp = admin_client.post(
        f"/api/admin/realms/{realm.id}/lines",
        json={"url": "not-a-url"})
    assert resp.status_code == 400


def test_admin_api_line_cross_realm_404(admin_client, db):
    a = _realm(db, "xa", url="https://a.example.com")
    b = _realm(db, "xb", url="https://b.example.com")
    b_line = realms.ensure_realm_lines(db, b)[0]
    resp = admin_client.put(
        f"/api/admin/realms/{a.id}/lines/{b_line.id}",
        json={"quota_bytes": 1})
    assert resp.status_code == 404
    resp = admin_client.post(
        f"/api/admin/realms/{a.id}/lines/{b_line.id}/reset-traffic")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 挪组：改选所属大类（线路 id 不变，流量/配额/周期随线路走）
# ---------------------------------------------------------------------------

def test_admin_api_move_line_keeps_traffic_and_quota(admin_client, db):
    a = _realm(db, "mv-a", url="https://a.example.com")
    b = _realm(db, "mv-b", url="https://b.example.com")
    realms.ensure_realm_lines(db, b)
    resp = admin_client.post(
        f"/api/admin/realms/{a.id}/lines",
        json={"url": "https://a2.example.com", "quota_bytes": 4096})
    assert resp.status_code == 200
    sub = resp.json()["line"]
    _seed_usage(db, sub["id"], relay=1000)
    # 周期起点是线路行上的字段，挪组后必须原样保留
    line = realms.get_line(db, sub["id"])
    line.period_started_at = datetime(2026, 11, 1, 8, 30)
    db.commit()
    resp = admin_client.put(
        f"/api/admin/realms/{a.id}/lines/{sub['id']}",
        json={"realm_id": b.id})
    assert resp.status_code == 200
    moved = resp.json()["line"]
    assert moved["id"] == sub["id"]
    assert moved["realm_id"] == b.id
    assert moved["traffic"]["quota_bytes"] == 4096
    assert moved["traffic"]["used_bytes"] == 1000
    assert moved["traffic"]["period_started_at"] == "2026-11-01T08:30:00"
    # 原大类不再有这条线，目标大类按序下发（B 主线路在前、挪入的其次）
    resp = admin_client.get(f"/api/admin/realms/{a.id}/lines")
    assert [l["name"] for l in resp.json()["lines"]] == ["主线路"]
    resp = admin_client.get(f"/api/admin/realms/{b.id}/lines")
    b_lines = resp.json()["lines"]
    assert [l["id"] for l in b_lines] == [b_lines[0]["id"], sub["id"]]
    assert b_lines[0]["is_primary"] is True
    assert b_lines[1]["is_primary"] is False
    # 日账按 line_id 记：挪组后已用仍是同一笔账
    assert line_quota.quota_view(db, realms.get_line(db, sub["id"]))["used_bytes"] == 1000


def test_admin_api_move_line_to_missing_realm_404(admin_client, db):
    a = _realm(db, "mv-x", url="https://a.example.com")
    main = realms.ensure_realm_lines(db, a)[0]
    resp = admin_client.put(
        f"/api/admin/realms/{a.id}/lines/{main.id}",
        json={"realm_id": 999999})
    assert resp.status_code == 404
    # 挪不成，线路还留在原大类
    assert realms.get_line(db, main.id).realm_id == a.id


def test_admin_api_move_primary_recalculates_both_sides(admin_client, db):
    b = _realm(db, "mv-pb", url="https://b.example.com")
    b_main = realms.ensure_realm_lines(db, b)[0]
    a = _realm(db, "mv-pa", url="https://a.example.com")
    a_main = realms.ensure_realm_lines(db, a)[0]
    a_sub = _line(db, a, "https://a2.example.com")
    # 把 A 的主线路挪去 B：A 剩下的副线路自动成主，B 仍是原主线路为首
    resp = admin_client.put(
        f"/api/admin/realms/{a.id}/lines/{a_main.id}",
        json={"realm_id": b.id})
    assert resp.status_code == 200
    resp = admin_client.get(f"/api/admin/realms/{a.id}/lines")
    a_lines = resp.json()["lines"]
    assert [l["id"] for l in a_lines] == [a_sub.id]
    assert a_lines[0]["is_primary"] is True
    resp = admin_client.get(f"/api/admin/realms/{b.id}/lines")
    by_id = {l["id"]: l for l in resp.json()["lines"]}
    assert by_id[b_main.id]["is_primary"] is True
    assert by_id[a_main.id]["is_primary"] is False


def test_card_lines_follow_moved_line(db):
    a = _realm(db, "mv-ca", url="https://a.example.com")
    a_main = realms.ensure_realm_lines(db, a)[0]
    moved = _line(db, a, "https://a2.example.com", quota=100)
    b = _realm(db, "mv-cb", url="https://b.example.com")
    b_main = realms.ensure_realm_lines(db, b)[0]
    user = _user(db)
    _sub(db, user, a)
    _sub(db, user, b)
    _seed_usage(db, moved.id, relay=100)
    cards = _cards(db, user)
    assert [l["id"] for l in cards[a.id]["lines"]] == [a_main.id, moved.id]
    realms.update_line(db, moved, realm_id=b.id)
    cards = _cards(db, user)
    # 账号卡按新分组下发：A 只剩自己的主线路，B 多出挪入的线（账随线走）
    assert [l["id"] for l in cards[a.id]["lines"]] == [a_main.id]
    b_lines = cards[b.id]["lines"]
    assert [l["id"] for l in b_lines] == [b_main.id, moved.id]
    assert b_lines[1]["quota_exhausted"] is True
    assert cards[a.id]["base_url"] == "https://a.example.com"


def test_card_fallback_when_realm_left_without_active_line(db):
    a = _realm(db, "mv-fa", url="https://a.example.com")
    only_active = realms.ensure_realm_lines(db, a)[0]
    realms.create_line(db, a, name="停用线", url="https://a-off.example.com",
                       is_active=False)
    b = _realm(db, "mv-fb", url="https://b.example.com")
    realms.ensure_realm_lines(db, b)
    user = _user(db)
    _sub(db, user, a)
    realms.update_line(db, only_active, realm_id=b.id)
    cards = _cards(db, user)
    # 原大类一条启用线路都不剩：lines 下发空清单，用户端按现行口径
    # 回退 base_url 单行（地址照常下发，口径不动）
    assert cards[a.id]["lines"] == []
    assert cards[a.id]["base_url"] == "https://a.example.com"


# ---------------------------------------------------------------------------
# 线路大类：category 字段、缺省与改类
# ---------------------------------------------------------------------------

def test_auto_line_has_default_category(db):
    realm = _realm(db, "cat-auto", url="https://cdn.example.com")
    lines = realms.ensure_realm_lines(db, realm)
    # 自动生成的主线路带缺省大类；序列化（管理端/账号卡）都带 category
    assert lines[0].category == "国际线路"
    view = line_quota.line_view(db, lines[0], is_primary=True)
    assert view["category"] == "国际线路"
    card = line_quota.card_lines(db, realm)
    assert card[0]["category"] == "国际线路"


def test_create_and_update_line_category(db):
    realm = _realm(db, "cat-crud", url="https://cdn.example.com")
    realms.ensure_realm_lines(db, realm)
    cn = realms.create_line(db, realm, url="https://cn.example.com",
                            category="国内线路")
    assert cn.category == "国内线路"
    plain = realms.create_line(db, realm, url="https://www.example.com")
    assert plain.category == "国际线路"  # 不传 = 缺省大类
    realms.update_line(db, plain, category="国内线路")
    db.refresh(plain)
    assert plain.category == "国内线路"
    # 改大类只动 category 一列，与挪服（realm_id）互不干扰
    assert plain.realm_id == realm.id
    with pytest.raises(ValueError):
        realms.create_line(db, realm, url="https://x.example.com",
                           category="长" * 81)


def test_admin_api_line_category_roundtrip(admin_client, db):
    realm = _realm(db, "cat-api", url="https://cdn.example.com")
    resp = admin_client.post(
        f"/api/admin/realms/{realm.id}/lines",
        json={"url": "https://cn.example.com", "category": "国内线路"})
    assert resp.status_code == 200
    line = resp.json()["line"]
    assert line["category"] == "国内线路"
    resp = admin_client.put(
        f"/api/admin/realms/{realm.id}/lines/{line['id']}",
        json={"category": "国际线路"})
    assert resp.status_code == 200
    assert resp.json()["line"]["category"] == "国际线路"
    resp = admin_client.get(f"/api/admin/realms/{realm.id}/lines")
    by_id = {l["id"]: l for l in resp.json()["lines"]}
    assert by_id[line["id"]]["category"] == "国际线路"
    # 自动主线路（没经手 category）回缺省大类
    assert {l["category"] for l in by_id.values()} == {"国际线路"}


# ---------------------------------------------------------------------------
# 大类与订阅打通：账号卡下发按用户套餐大类过滤
# ---------------------------------------------------------------------------

def _sub_cat(db, user, realm, category=None, days=30):
    """带大类声明的订阅；category=None = 套餐不声明（缺省口径）。"""
    plan = models.SubscriptionPlan(
        name="月卡", duration_days=30, price=18,
        entitlements=({"line_category": category} if category else None))
    db.add(plan)
    db.commit()
    end = datetime.now() + timedelta(days=days)
    sub = models.UserSubscription(
        user_id=user.id, plan_id=plan.id, realm_id=realm.id,
        status="active", start_date=end - timedelta(days=30), end_date=end)
    db.add(sub)
    db.commit()
    return sub


def _team(db, owner):
    team = models.Team(
        owner_id=owner.id, status="active", plan_line="global",
        plan_expires_at=datetime.now() + timedelta(days=30))
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _two_category_realm(db, slug):
    """一个服、两个大类各一条线：自动主线路（国际）＋自建国内线。"""
    realm = _realm(db, slug, url="https://intl.example.com")
    intl = realms.ensure_realm_lines(db, realm)[0]
    cn = realms.create_line(db, realm, url="https://cn.example.com",
                            category="国内线路")
    return realm, intl, cn


def test_intl_plan_user_gets_only_intl_lines(db):
    realm, intl, cn = _two_category_realm(db, "flt-intl")
    user = _user(db)
    _sub_cat(db, user, realm, "国际线路")
    lines = _cards(db, user)[realm.id]["lines"]
    assert [l["id"] for l in lines] == [intl.id]
    assert lines[0]["category"] == "国际线路"


def test_domestic_plan_user_gets_only_domestic_lines(db):
    realm, intl, cn = _two_category_realm(db, "flt-cn")
    user = _user(db)
    _sub_cat(db, user, realm, "国内线路")
    lines = _cards(db, user)[realm.id]["lines"]
    assert [l["id"] for l in lines] == [cn.id]
    assert lines[0]["category"] == "国内线路"


def test_undeclared_plan_defaults_to_intl_lines(db):
    # 存量套餐不声明 line_category：按缺省大类（国际线路），现状不变
    realm, intl, cn = _two_category_realm(db, "flt-def")
    user = _user(db)
    _sub(db, user, realm)
    lines = _cards(db, user)[realm.id]["lines"]
    assert [l["id"] for l in lines] == [intl.id]


def test_no_plan_user_not_filtered(db):
    realm, intl, cn = _two_category_realm(db, "flt-none")
    user = _user(db)
    # 纯浏览（无套餐无资格）：resolver 不给结论，下发层不过滤、全量
    assert line_quota.user_line_category(db, user, realm.id) is None
    all_lines = line_quota.card_lines(db, realm, category=None)
    assert [l["id"] for l in all_lines] == [intl.id, cn.id]


def test_team_user_without_sub_gets_all_lines(db):
    realm, intl, cn = _two_category_realm(db, "flt-team")
    owner = _user(db)
    _team(db, owner)
    # 团队套餐无大类口径、本人又无个人订阅 → 不过滤，全量下发
    lines = _cards(db, owner)[realm.id]["lines"]
    assert [l["id"] for l in lines] == [intl.id, cn.id]


def test_team_fallback_to_subscription_category(db):
    a, a_intl, a_cn = _two_category_realm(db, "flt-ta")
    b, b_intl, b_cn = _two_category_realm(db, "flt-tb")
    owner = _user(db)
    _team(db, owner)
    _sub_cat(db, owner, b, "国内线路")  # 个人订阅在 B 服、大类国内
    cards = _cards(db, owner)
    # A 服靠团队资格下发：团队无大类 → 回退订阅口径（国内）
    assert [l["id"] for l in cards[a.id]["lines"]] == [a_cn.id]
    # B 服靠本服订阅：同口径只下发国内线
    assert [l["id"] for l in cards[b.id]["lines"]] == [b_cn.id]


def test_card_payload_follows_category_edit(db):
    realm, intl, cn = _two_category_realm(db, "flt-edit")
    user = _user(db)
    _sub_cat(db, user, realm, "国际线路")
    assert [l["id"] for l in _cards(db, user)[realm.id]["lines"]] == [intl.id]
    # 管理员把国内线改归国际大类 → 国际套餐用户的 payload 随之分组
    realms.update_line(db, cn, category="国际线路")
    got = {l["id"]: l["category"]
           for l in _cards(db, user)[realm.id]["lines"]}
    assert got == {intl.id: "国际线路", cn.id: "国际线路"}
