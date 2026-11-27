"""流量额度真拦截（2026-11-15 用户亲点「拦截」，取代 2026-11-13
「只展示不拦」旧边界）。

口径要点（与实现同源，改一处要同步改这里）：
- 判定只许复用 traffic_quota.usage_summary 的 exhausted（套餐配了
  月额度 enabled 且有效已用 >= 额度）；enabled=False 永不拦。
- 只拦「起播」：无 Range 头或 Range 从 0 开始；Range 续传放行，
  已经开始的响应永不中断；302/直链永不拦（不耗本站流量）。
- 两条计量腿都堵：中继腿（api._observe_traffic 出口替换）与本机
  文件腿（video_stream 两处 serve_file 前的 _raise_if_traffic_exhausted）。
- 签发侧（PlaybackInfo）：已知中继类变体源摘除、全是已知中继类时
  403 + error_code=TRAFFIC_EXHAUSTED；类型未知的源不动（取流侧裁决）。
- 判定异常一律放行 + 记日志（拦截是账房的事，不许拖垮播放）；
  开关 traffic_enforce_enabled 默认开，关掉全部放行。
- 管理员/staff 同样过拦截（与播放门禁「管理员无豁免」同口径）——
  判定函数不看 is_staff，本文件用 staff 用户实证。
"""
import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse

from backend import entitlements as ent
from backend import models
from backend import traffic_gate as tg
from backend import traffic_quota as tq
from backend.emby_server import api as ea_api
from backend.emby_server import models as em
from backend.emby_server import traffic_meter as tm
from backend.integrations import store as _store

GB = 1024 ** 3
_CONFIG_KEYS = [tq.CONFIG_DEFAULT_QUOTA, tg.TRAFFIC_ENFORCE_KEY]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    _store.invalidate(*_CONFIG_KEYS)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()
    tm.reset_pending()
    tg.invalidate_cache()
    tg.set_current_range(None)
    tm.set_current_user(None)
    yield s
    s.close()
    _store.invalidate(*_CONFIG_KEYS)
    ent._USER_PLAN_CACHE.clear()
    ent._PLAN_SNAPSHOT_CACHE.clear()
    tm.reset_pending()
    tg.invalidate_cache()
    tg.set_current_range(None)
    tm.set_current_user(None)


_seq = [0]


def _user(db, staff=False):
    _seq[0] += 1
    u = models.WebUser(username=f"tg{_seq[0]}", password_hash="x",
                       is_staff=staff)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _plan(db, quota_bytes):
    _seq[0] += 1
    entitlements = ({"monthly_traffic_bytes": quota_bytes}
                    if quota_bytes is not None else None)
    p = models.SubscriptionPlan(
        name=f"tplan{_seq[0]}", price=10, duration_days=90,
        entitlements=entitlements)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _sub(db, user, plan):
    now = datetime.now()
    s = models.UserSubscription(
        user_id=user.id, plan_id=plan.id,
        start_date=now - timedelta(days=10),
        end_date=now + timedelta(days=80), status="active")
    db.add(s)
    db.commit()
    db.refresh(s)
    ent.invalidate(user.id)
    return s


def _usage(db, user, relay=0, local=0, day=None):
    row = em.TrafficUsageDaily(
        user_id=user.id, day=day or date.today(),
        relay_bytes=relay, local_bytes=local, direct_plays=0)
    db.add(row)
    db.commit()
    return row


def _credit(db, user, bytes_credited):
    start, end, _sub_row = tq.current_period(db, user.id)
    _seq[0] += 1
    row = em.TrafficExchangeLog(
        user_id=user.id, kind="balance_buy", days_deducted=0,
        bytes_credited=bytes_credited, period_start=start,
        period_end=end, idempotency_key=f"tg-test-{_seq[0]}")
    db.add(row)
    db.commit()
    return row


def _config(db, key, value):
    row = (db.query(models.SystemConfig)
           .filter_by(key=key).one_or_none())
    if row is None:
        db.add(models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()
    _store.invalidate(key)


def _exhausted_user(db, staff=False):
    """额度 10GB、本周期已用 10GB 的用户（恰好用尽）。"""
    u = _user(db, staff=staff)
    _sub(db, u, _plan(db, 10 * GB))
    _usage(db, u, relay=6 * GB, local=4 * GB)
    return u


# ---------------------------------------------------------------------------
# 起播判定：Range 解析
# ---------------------------------------------------------------------------

class TestStartRange:
    @pytest.mark.parametrize("header", [
        None, "", "bytes=0-", "bytes=0-1023", "bytes=0-1023,2048-4095",
        "garbage",
    ])
    def test_start_requests(self, header):
        assert tg.is_start_range(header) is True

    @pytest.mark.parametrize("header", [
        "bytes=1-", "bytes=1024-", "bytes=1024-2047", "bytes=-500",
    ])
    def test_resume_requests(self, header):
        assert tg.is_start_range(header) is False


# ---------------------------------------------------------------------------
# 判定：复用 usage_summary 的 exhausted + 开关 + 异常放行 + 缓存
# ---------------------------------------------------------------------------

class TestGateDecision:
    def test_exhausted_user_blocked_on_start(self, db):
        u = _exhausted_user(db)
        assert tg.user_traffic_exhausted(db, u.id) is True
        assert tg.metered_start_blocked(db, u.id, None) is True
        assert tg.metered_start_blocked(db, u.id, "bytes=0-") is True

    def test_staff_not_exempt(self, db):
        u = _exhausted_user(db, staff=True)
        assert tg.metered_start_blocked(db, u.id, None) is True

    def test_resume_range_allowed_when_exhausted(self, db):
        u = _exhausted_user(db)
        assert tg.metered_start_blocked(db, u.id, "bytes=123456-") is False

    def test_under_quota_allowed(self, db):
        u = _user(db)
        _sub(db, u, _plan(db, 10 * GB))
        _usage(db, u, relay=9 * GB)
        assert tg.user_traffic_exhausted(db, u.id) is False
        assert tg.metered_start_blocked(db, u.id, None) is False

    def test_quota_not_enabled_never_blocked(self, db):
        u = _user(db)
        _sub(db, u, _plan(db, None))  # 套餐没配月额度 → enabled=False
        _usage(db, u, relay=500 * GB)
        assert tg.user_traffic_exhausted(db, u.id) is False
        assert tg.metered_start_blocked(db, u.id, None) is False

    def test_credit_restores_play(self, db):
        u = _exhausted_user(db)
        assert tg.user_traffic_exhausted(db, u.id) is True
        _credit(db, u, 10 * GB)  # 购流量/换流量抵扣把有效已用压回 0
        tg.invalidate_cache(u.id)
        assert tg.user_traffic_exhausted(db, u.id) is False
        assert tg.metered_start_blocked(db, u.id, None) is False

    def test_switch_off_allows_everything(self, db):
        u = _exhausted_user(db)
        _config(db, tg.TRAFFIC_ENFORCE_KEY, "false")
        assert tg.user_traffic_exhausted(db, u.id) is False
        assert tg.metered_start_blocked(db, u.id, None) is False

    def test_decision_error_fails_open(self, db, monkeypatch):
        u = _exhausted_user(db)

        def _boom(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(tq, "usage_summary", _boom)
        assert tg.user_traffic_exhausted(db, u.id) is False
        assert tg.metered_start_blocked(db, u.id, None) is False

    def test_cache_avoids_requery_within_ttl(self, db, monkeypatch):
        u = _exhausted_user(db)
        calls = []
        real = tq.usage_summary

        def _counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        monkeypatch.setattr(tq, "usage_summary", _counting)
        assert tg.user_traffic_exhausted(db, u.id) is True
        assert tg.user_traffic_exhausted(db, u.id) is True
        assert len(calls) == 1
        tg.invalidate_cache(u.id)
        assert tg.user_traffic_exhausted(db, u.id) is True
        assert len(calls) == 2

    def test_no_user_never_blocked(self, db):
        assert tg.user_traffic_exhausted(db, None) is False
        assert tg.metered_start_blocked(db, None, None) is False


# ---------------------------------------------------------------------------
# 中继腿出口：_observe_traffic 在字节响应未发出前替换成 403
# ---------------------------------------------------------------------------

def _byte_response(status=200, headers=None):
    async def _gen():
        yield b"x" * 16

    return StreamingResponse(_gen(), status_code=status, headers=headers or {})


@pytest.fixture()
def egress(db, monkeypatch):
    """把出口判定的自开会话接到测试库，并返回造 exhausted 用户的工具。"""
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(tg, "_open_session", lambda: factory())
    return db


class TestRelayEgress:
    def test_exhausted_start_replaced_with_403(self, egress):
        u = _exhausted_user(egress)
        tm.set_current_user(u.id)
        tg.set_current_range(None)
        resp = ea_api._observe_traffic(_byte_response(200))
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 403

    def test_exhausted_206_start_replaced_with_403(self, egress):
        u = _exhausted_user(egress)
        tm.set_current_user(u.id)
        tg.set_current_range("bytes=0-1023")
        resp = ea_api._observe_traffic(_byte_response(
            206, {"Content-Range": "bytes 0-1023/8192"}))
        assert resp.status_code == 403

    def test_exhausted_resume_passes_through(self, egress):
        u = _exhausted_user(egress)
        tm.set_current_user(u.id)
        tg.set_current_range("bytes=4096-")
        original = _byte_response(
            206, {"Content-Range": "bytes 4096-8191/8192"})
        resp = ea_api._observe_traffic(original)
        assert resp is original
        assert resp.status_code == 206

    def test_direct_302_never_blocked(self, egress):
        u = _exhausted_user(egress)
        tm.set_current_user(u.id)
        tg.set_current_range(None)
        original = RedirectResponse("https://example.invalid/f", status_code=302)
        resp = ea_api._observe_traffic(original)
        assert resp is original
        assert resp.status_code == 302

    def test_under_quota_passes_through(self, egress):
        u = _user(egress)
        _sub(egress, u, _plan(egress, 10 * GB))
        _usage(egress, u, relay=1 * GB)
        tm.set_current_user(u.id)
        tg.set_current_range(None)
        original = _byte_response(200)
        resp = ea_api._observe_traffic(original)
        assert resp is original
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 本机文件腿：video_stream 两处 serve_file 前的拦截 helper
# ---------------------------------------------------------------------------

def _req(range_header=None):
    headers = {} if range_header is None else {"range": range_header}
    return SimpleNamespace(headers=headers)


class TestLocalLegGate:
    def test_exhausted_start_raises_403_with_message(self, db):
        u = _exhausted_user(db)
        with pytest.raises(HTTPException) as exc:
            ea_api._raise_if_traffic_exhausted(db, u, _req())
        assert exc.value.status_code == 403
        assert exc.value.detail == tg.TRAFFIC_EXHAUSTED_MESSAGE

    def test_exhausted_resume_allowed(self, db):
        u = _exhausted_user(db)
        ea_api._raise_if_traffic_exhausted(db, u, _req("bytes=999-"))

    def test_under_quota_allowed(self, db):
        u = _user(db)
        _sub(db, u, _plan(db, 10 * GB))
        ea_api._raise_if_traffic_exhausted(db, u, _req())

    def test_both_serve_file_sites_gated(self):
        import inspect
        src = inspect.getsource(ea_api.video_stream)
        # 定义 1 处 + 两处 serve_file 调用前各 1 处
        assert src.count("_raise_if_traffic_exhausted") >= 2
        assert src.count("serve_file(") >= 2


# ---------------------------------------------------------------------------
# 签发侧：PlaybackInfo 源分类与摘除
# ---------------------------------------------------------------------------

GUID = "c871c37df8a46cfb6f85a5e85b6d625d"


def _variant_source(variant, remote):
    return {"Id": f"{GUID}:{variant}", "IsRemote": remote,
            "DirectStreamUrl": f"https://h/emby/Videos/{GUID}/stream.mkv"
                                 f"?MediaSourceId={GUID}:{variant}"
                                 f"&variant={variant}",
            "DirectPlayUrl": "x"}


class TestSourceClassification:
    def test_relay_variant_is_metered(self):
        assert tg.classify_source(_variant_source("drive_relay", False)) == "metered"
        assert tg.classify_source(_variant_source("mount_relay", False)) == "metered"
        assert tg.classify_source(_variant_source("local_file", False)) == "metered"
        assert tg.classify_source(_variant_source("byos_drive_relay", False)) == "metered"

    def test_direct_variant_is_direct(self):
        assert tg.classify_source(_variant_source("drive_direct", True)) == "direct"
        assert tg.classify_source(_variant_source("byos115_direct", True)) == "direct"
        assert tg.classify_source(_variant_source("byos_drive_direct", True)) == "direct"

    def test_alt_media_source_is_direct(self):
        src = {"Id": GUID, "IsRemote": False,
               "DirectStreamUrl": "https://www.googleapis.com/drive/v3/files/f1?alt=media",
               "RequiredHttpHeaders": {"Authorization": "Bearer x"}}
        assert tg.classify_source(src) == "direct"

    def test_plain_local_stream_source_is_unknown(self):
        src = {"Id": GUID, "IsRemote": False,
               "DirectStreamUrl": f"https://h/emby/Videos/{GUID}/stream.mkv?api_key=k"}
        assert tg.classify_source(src) == "unknown"


class TestIssuanceGate:
    def test_mixed_sources_keep_direct_only(self, db):
        u = _exhausted_user(db)
        sources = [_variant_source("drive_relay", False),
                   _variant_source("drive_direct", True)]
        kept, descriptors = ea_api._apply_traffic_gate_to_sources(
            db, u, sources, [{"kind": "drive_relay"}, {"kind": "drive_direct"}])
        assert [s["Id"] for s in kept] == [f"{GUID}:drive_direct"]
        assert descriptors == [{"kind": "drive_direct"}]

    def test_all_metered_sources_raise_403_with_error_code(self, db):
        u = _exhausted_user(db)
        sources = [_variant_source("drive_relay", False),
                   _variant_source("mount_relay", False)]
        with pytest.raises(HTTPException) as exc:
            ea_api._apply_traffic_gate_to_sources(db, u, sources, [])
        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == tg.TRAFFIC_EXHAUSTED_CODE
        assert exc.value.detail["message"] == tg.TRAFFIC_EXHAUSTED_MESSAGE

    def test_unknown_source_kept_for_stream_side_decision(self, db):
        u = _exhausted_user(db)
        src = {"Id": GUID, "IsRemote": False,
               "DirectStreamUrl": f"https://h/emby/Videos/{GUID}/stream.mkv?api_key=k"}
        kept, _ = ea_api._apply_traffic_gate_to_sources(db, u, [src], [])
        assert kept == [src]

    def test_not_exhausted_sources_untouched(self, db):
        u = _user(db)
        _sub(db, u, _plan(db, 10 * GB))
        sources = [_variant_source("drive_relay", False)]
        kept, descriptors = ea_api._apply_traffic_gate_to_sources(
            db, u, sources, [{"kind": "drive_relay"}])
        assert kept == sources
        assert descriptors == [{"kind": "drive_relay"}]
