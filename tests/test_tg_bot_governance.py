"""TG Bot 等级治理 bot 补完：/level、/report 与新版 dashboard。

/propose、/launch 已随公益服删除于 2026-11-19 整条下线（命令映射、
菜单注册与 handler 一并摘除），本文件只钉它们不再出现。

只测本模块：DB 用内存 SQLite；Telegram 发送全部 patch 掉，不发真实请求。
版式依据：用户 2026-10-29 08:44 亲批 dashboard 样例（逐字结构）与
等级公益服活动治理设计稿权利表。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.emby_server import models as em
from backend.tg_bot import features, handlers, poller, sender

TICKS = 10_000_000
GROUP = -100123
TG = 900001


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db, tg=TG, name="alice", is_welfare=False, welfare_expires_at=None):
    row = models.WebUser(
        username=name, password_hash="x", telegram_id=tg,
    )
    db.add(row)
    db.commit()
    return row


def _tg(tg=TG, first_name="Alice"):
    return {"id": tg, "first_name": first_name}


def _realm(db):
    realm = models.ServerRealm(name="主服", slug="main")
    db.add(realm)
    db.commit()
    db.add(models.SystemConfig(key="active_realm_id", value=str(realm.id)))
    db.commit()
    return realm


def _watch(db, user_id, hours_list):
    for i, h in enumerate(hours_list):
        item = em.MediaItem(
            guid=f"test-item-{user_id}-{i}", library_id=1,
            item_type="movie", name=f"片{i}",
            duration_ticks=int(h * 3600 * TICKS),
        )
        db.add(item)
        db.flush()
        db.add(em.PlaybackSession(
            user_id=user_id, item_id=item.id,
            position_ticks=int(h * 3600 * TICKS),
            session_key=f"test-sess-{user_id}-{i}",
        ))
    db.commit()


def _binding(db, user_id, provider, status="active"):
    db.add(em.UserStorageBinding(
        user_id=user_id, provider=provider, account_label="盘",
        credential_id=1, status=status))
    db.commit()


def _seek(db, user_id, name="求片A", realm_id=None, status="pending"):
    row = models.MovieRequest(
        user_id=user_id, movie_name=name, status=status, realm_id=realm_id)
    db.add(row)
    db.commit()
    return row


def _freeze(db, user_id):
    db.add(models.GovernanceFreeze(
        user_id=user_id, reason="test", created_by=user_id,
        expires_at=datetime.now() + timedelta(days=30)))
    db.commit()


def _dispatch(db, monkeypatch, text, chat_id=TG, chat_type="private"):
    sent = []

    def fake_send(db_, chat_id_, text_, reply_markup=None, parse_mode="HTML"):
        sent.append(text_)
        return (True, None)

    monkeypatch.setattr(sender, "send_message", fake_send)

    def fake_send_ex(db_, chat_id_, text_, reply_markup=None, parse_mode="HTML"):
        sent.append(text_)
        return (True, None, 1)

    monkeypatch.setattr(sender, "send_message_ex", fake_send_ex)
    monkeypatch.setattr(
        "backend.tg_bot.ephemeral.schedule_delete", lambda *a, **k: True)
    update = {
        "update_id": 1,
        "message": {"message_id": 1, "from": _tg(), "chat": {"id": chat_id, "type": chat_type},
                    "text": text},
    }
    import backend.tg_bot.router as router_mod
    router_mod.dispatch(db, update)
    return sent


# ---------- 新版 dashboard：样例逐字结构 ----------

def test_dashboard_sample_layout_exact(db):
    """用户批准的卡片式用户面板逐字结构：▎标题＋「· emoji 标签 | 值」信息行。

    存量查看行（2030 年到期）
    照旧播种：行在效也不再进面板（2026-11-09 凭证行已删），逐字比对
    正好钉住这一点。
    """
    row = _user(db, tg=TG, name="shunxian2580", is_welfare=True, welfare_expires_at=datetime(2026, 11, 27))
    db.add(models.EmbyViewUnlock(
        user_id=row.id, realm_id=1, expires_at=datetime(2030, 11, 27)))
    db.commit()
    _binding(db, row.id, "drive")
    text = features.compact_dashboard(db, row, display_name="Shun")
    expected = "\n".join([
        "▎<b>欢迎进入用户面板！</b>Shun",
        f"· 🆔 <b>用户编号</b> | {row.id}",
        "· 👤 <b>账号名称</b> | <b>shunxian2580</b>",
        "· 🔗 <b>网盘绑定</b> | Drive 已绑 ✓",
        "· 📦 <b>订阅状态</b> | 暂无",
    ])
    assert text == expected


def test_level_rights_table(db):
    _realm(db)
    row = _user(db)
    text = features.handle_level(db, _tg(), TG, "")
    # member_xp 已删、等级恒 Lv.1（_level_info 兼容口径）
    assert "⭐ Lv.1 初幕" in text
    assert "经验 0／100" in text
    assert "距 Lv.2 影迷还差 100 经验" in text
    # 提案／直接发起支线已随公益服删除下线，权表不再展示这两项
    assert "本月提案" not in text
    assert "本月直接发起" not in text
    assert "附议权重" in text and "每人 1 票" in text
    assert "举报复核" in text and "我的权重：无（Lv.3 起获得）" in text


def test_level_no_realm_still_shows_rights_table(db):
    # 公益服门已删：无服时权表照常展示，不再有「仅公益服开放」分支
    row = _user(db)
    text = features.handle_level(db, _tg(), TG, "")
    assert "⭐ Lv.1 初幕" in text
    assert "治理权表仅公益服开放" not in text
    assert "附议权重" in text


def test_level_frozen(db):
    _realm(db)
    row = _user(db)
    _freeze(db, row.id)
    text = features.handle_level(db, _tg(), TG, "")
    assert "治理特权已被冻结" in text
    assert "附议仍按 1 票计" in text


def test_level_unbound(db):
    assert "绑定" in features.handle_level(db, _tg(), TG, "")


# ---------- /report ----------

def test_report_success_and_duplicate(db):
    realm = _realm(db)
    reporter = _user(db)
    seek = _seek(db, reporter.id, realm_id=realm.id)
    text = features.handle_report(db, _tg(), TG, f"{seek.id} 刷屏 重复发了好几次")
    assert "举报已提交" in text and "刷屏／广告" in text
    dup = features.handle_report(db, _tg(), TG, f"{seek.id} 违规")
    assert "已举报过该求片" in dup


def test_report_bad_reason_and_missing_args(db):
    _realm(db)
    _user(db)
    assert "刷屏、重复、违规" in features.handle_report(db, _tg(), TG, "1 不知道")
    assert "/report 求片编号" in features.handle_report(db, _tg(), TG, "")


def test_report_frozen(db):
    realm = _realm(db)
    reporter = _user(db)
    _freeze(db, reporter.id)
    seek = _seek(db, reporter.id, realm_id=realm.id)
    assert "治理特权已被冻结" in features.handle_report(db, _tg(), TG, f"{seek.id} 违规")


# ---------- /propose /launch 已下线 ----------

def test_propose_launch_handlers_removed(db):
    _realm(db)
    _user(db)
    assert not hasattr(features, "handle_propose")
    assert not hasattr(features, "handle_direct_launch")
    assert not hasattr(handlers, "handle_propose")
    assert not hasattr(handlers, "handle_direct_launch")


# ---------- 路由与菜单 ----------

def test_level_renders_card_in_group_report_private_only(db, monkeypatch):
    # /level 是账号卡别名、账号展示群里放行（2026-10-28 定、2026-11-02
    # 重申）：群里直接回账号卡；report 仍私聊限定、只回提示。
    _user(db)
    monkeypatch.setattr("backend.tg_bot.router._user_cmd_limits", {})
    sent = _dispatch(db, monkeypatch, "/level", chat_id=GROUP, chat_type="group")
    assert sent and "账号名称" in sent[0] and "请私聊" not in sent[0]
    sent = _dispatch(db, monkeypatch, "/report 1 违规", chat_id=GROUP, chat_type="group")
    assert sent and "请私聊" in sent[0]


def test_bot_commands_cover_new_commands():
    registered = {cmd for cmd, _desc in poller.BOT_COMMANDS}
    assert "report" in registered
    assert "propose" not in registered  # 已随公益服删除下线
    assert "launch" not in registered   # 已随公益服删除下线
    assert "level" not in registered  # /level 已摘出菜单（手输别名保留）
