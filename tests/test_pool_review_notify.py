"""公共池成员审核结果的 TG 通知（站内信之外的补强）。

口径：
- 审核通过/驳回后，除了既有站内信，还要走 proactive 管道给成员
  本人一条 TG 私聊（新 kind pool_review_approved /
  pool_review_rejected，subject=成员行 id，claim 幂等只发一次）；
  未绑 TG 跳过不报错。文案与站内信同口径，通过额外带一句奖励
  口径（1 分/次、5 分/天、月封顶 900、称号「片库贡献者」）。
- 私聊偏好门控跟「贡献回响」同类（CAT_POOL_DIGEST /
  tg_notify_pool_digest）：用户关了就不发 TG，站内信照常。
- 仅通过时，另走 group_cheer 收口在正式群发一条欢迎：只提站内
  用户名，不带盘标签/容量/绑定/链接；新开关
  tg_notify_group_pool_welcome_enabled 默认开，锚幂等不重发。
- 通知层任何异常都不许把审核接口拖挂：审核照常成功。
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.api import pool_admin
from backend.emby_server import models as em
from backend.tg_bot import group_cheer, proactive

GROUP_ID = -100123
CHAT_ID = 777001


@pytest.fixture(autouse=True)
def _no_proactive_flusher(monkeypatch):
    # 不许导入期写环境变量（test_zz_env_import_hygiene 口径）：
    # 用例期关掉 proactive 后台刷盘线程，免它用全局会话误发。
    monkeypatch.setenv("TG_PROACTIVE_FLUSHER", "0")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def sent(monkeypatch):
    calls = []

    def _fake(db, chat_id, text, reply_markup=None, parse_mode="HTML"):
        calls.append({
            "chat_id": chat_id, "text": text,
            "reply_markup": reply_markup, "parse_mode": parse_mode,
        })
        return True, None

    monkeypatch.setattr("backend.tg_bot.sender.send_message", _fake)
    return calls


@pytest.fixture()
def station_calls(monkeypatch):
    calls = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(
        "backend.notifications.notify_admin_event_quiet", _fake)
    return calls


def _cfg(db, key, value):
    db.add(models.SystemConfig(key=key, value=value, description=""))
    db.commit()


def _user(db, name, telegram_id=None, **kw):
    user = models.WebUser(
        username=name, password_hash="x", is_active=True,
        telegram_id=telegram_id, **kw,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _member(db, user, status="active"):
    member = em.PoolMember(
        user_id=user.id, binding_id=None, provider="drive", status=status,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _notices(db, user_id=None):
    q = db.query(models.TgProactiveNotice)
    if user_id is not None:
        q = q.filter(models.TgProactiveNotice.user_id == user_id)
    return q.order_by(models.TgProactiveNotice.id).all()


def _flush(db):
    return proactive.flush_due(
        db, now=datetime.now() + timedelta(seconds=3600))


def _group_calls(sent):
    return [c for c in sent if c["chat_id"] == GROUP_ID]


def _dm_calls(sent):
    return [c for c in sent if c["chat_id"] == CHAT_ID]


# ---------------------------------------------------------------- 私聊 claim


def test_approve_claims_dm_with_reward_copy(db):
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    assert proactive.note_pool_member_review(db, member, approved=True)
    db.commit()

    rows = _notices(db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == proactive.KIND_POOL_REVIEW_APPROVED
    assert row.subject_id == member.id
    assert row.status == "pending"
    assert "贡献池申请已通过" in row.title
    # 奖励口径一句：被看 1 分/次、在线 5 分/天、月封顶 900、称号
    assert "1 分" in row.detail and "5 分" in row.detail
    assert "900" in row.detail and "片库贡献者" in row.detail


def test_reject_claims_dm_without_reward_copy(db):
    user = _user(db, "dabao", telegram_id=CHAT_ID)
    member = _member(db, user, status="rejected")

    assert proactive.note_pool_member_review(db, member, approved=False)
    db.commit()

    rows = _notices(db, user.id)
    assert len(rows) == 1
    assert rows[0].kind == proactive.KIND_POOL_REVIEW_REJECTED
    assert "未通过" in rows[0].title
    assert "重新申请" in rows[0].detail


def test_claim_idempotent_and_flush_sends_once(db, sent):
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    assert proactive.note_pool_member_review(db, member, approved=True)
    db.commit()
    # 同一审核重复触发：claim 幂等，不许再入队
    assert not proactive.note_pool_member_review(db, member, approved=True)
    db.commit()

    assert _flush(db) == 1
    dms = _dm_calls(sent)
    assert len(dms) == 1
    assert "贡献池申请已通过" in dms[0]["text"]
    assert "片库贡献者" in dms[0]["text"]
    assert _notices(db, user.id)[0].status == "sent"
    # 再刷一轮也不重发
    assert _flush(db) == 0
    assert len(_dm_calls(sent)) == 1


def test_unbound_user_skipped(db):
    user = _user(db, "nogtg")  # 没绑 Telegram
    member = _member(db, user)

    assert not proactive.note_pool_member_review(db, member, approved=True)
    db.commit()
    assert _notices(db, user.id) == []


def test_pref_off_mutes_dm(db, sent):
    user = _user(db, "quiet", telegram_id=CHAT_ID,
                 tg_notify_pool_digest=False)
    member = _member(db, user)

    assert proactive.note_pool_member_review(db, member, approved=True)
    db.commit()
    assert _flush(db) == 0
    assert _dm_calls(sent) == []
    assert _notices(db, user.id)[0].status == "muted"


def test_seek_pref_off_does_not_mute_pool_dm(db, sent):
    # 关的是「求片到货类」偏好，不许误伤贡献回响类的审核私聊
    user = _user(db, "seekoff", telegram_id=CHAT_ID, tg_notify_seek=False)
    member = _member(db, user)

    assert proactive.note_pool_member_review(db, member, approved=True)
    db.commit()
    assert _flush(db) == 1
    assert len(_dm_calls(sent)) == 1


# ---------------------------------------------------------------- 群欢迎


def test_group_welcome_once_and_clean_text(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    assert group_cheer.send_pool_welcome(db, member)
    calls = _group_calls(sent)
    assert len(calls) == 1
    text = calls[0]["text"]
    assert "tms" in text and "公共片库" in text
    assert "片库贡献者" in text and "900" in text
    # 不许出现链接/地址，也不许带盘标签、容量、绑定信息
    assert "http" not in text and "aetrix.love" not in text

    # 锚幂等：重复触发不重发
    assert not group_cheer.send_pool_welcome(db, member)
    assert len(_group_calls(sent)) == 1


def test_group_welcome_switch_off(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    _cfg(db, "tg_notify_group_pool_welcome_enabled", "false")
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    assert not group_cheer.send_pool_welcome(db, member)
    assert _group_calls(sent) == []


# ---------------------------------------------------------------- 审核收口接线


def test_review_wiring_approve_sends_all_channels(db, sent, station_calls):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    admin = _user(db, "boss")
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    pool_admin._notify_member_review(db, member, approved=True,
                                     admin_id=admin.id)

    # 站内信照常走既有事件
    assert len(station_calls) == 1
    assert station_calls[0]["event_type"] == "pool.member_approved"
    assert station_calls[0]["user_id"] == user.id
    # TG 私聊已入队
    rows = _notices(db, user.id)
    kinds = {r.kind for r in rows}
    assert proactive.KIND_POOL_REVIEW_APPROVED in kinds
    # 群欢迎已发一条
    assert len(_group_calls(sent)) == 1


def test_review_wiring_reject_no_group(db, sent, station_calls):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    admin = _user(db, "boss")
    user = _user(db, "dabao", telegram_id=CHAT_ID)
    member = _member(db, user, status="rejected")

    pool_admin._notify_member_review(db, member, approved=False,
                                     admin_id=admin.id)

    assert len(station_calls) == 1
    assert station_calls[0]["event_type"] == "pool.member_rejected"
    kinds = {r.kind for r in _notices(db, user.id)}
    assert proactive.KIND_POOL_REVIEW_REJECTED in kinds
    # 驳回不发群
    assert _group_calls(sent) == []


def test_review_wiring_tg_failure_silent(db, station_calls, monkeypatch):
    admin = _user(db, "boss")
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user)

    def _boom(*a, **kw):
        raise RuntimeError("tg down")

    monkeypatch.setattr(proactive, "note_pool_member_review", _boom)
    monkeypatch.setattr(group_cheer, "send_pool_welcome", _boom)

    # 不许抛：站内信仍发出，审核收口安然返回
    pool_admin._notify_member_review(db, member, approved=True,
                                     admin_id=admin.id)
    assert len(station_calls) == 1


def test_approve_endpoint_survives_notify_failure(db, station_calls,
                                                 monkeypatch):
    admin = _user(db, "boss", is_staff=True)
    user = _user(db, "tms", telegram_id=CHAT_ID)
    member = _member(db, user, status="pending")

    def _approve(db_, member_id, actor_id=None):
        m = (db_.query(em.PoolMember)
             .filter(em.PoolMember.id == member_id).first())
        m.status = "active"
        return m

    monkeypatch.setattr(pool_admin.pool_gate, "approve_pool_member",
                        _approve)

    def _boom(*a, **kw):
        raise RuntimeError("tg down")

    monkeypatch.setattr(proactive, "note_pool_member_review", _boom)
    monkeypatch.setattr(group_cheer, "send_pool_welcome", _boom)

    class _Req:
        client = None

    payload = pool_admin.pool_approve_member(
        member.id, _Req(), current_admin=admin, db=db)
    assert payload["status"] == "active"
    assert len(station_calls) == 1
