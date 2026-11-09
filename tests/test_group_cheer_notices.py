"""TG 正式群氛围播报：许愿到货点名报喜 + 会员升级祝贺。

不连生产库。口径：
- 到货群播报挂在 proactive.note_store_request_completed 同一收口，
  与私聊到货同批触发；新开关 tg_notify_group_arrival_enabled 默认开，
  关了只发私聊不发群。同一心愿行只报一次，多人附议合并成一条
  （点许愿人本人＋「还有 N 位朋友」）。
- 升级祝贺只挂在 member_level.add_xp 唯一收口：跨级才发、同级
  只报一次、降级不发；钩子抛错不影响发经验主流程。
- 群消息只发 bot_group_ids 的第一个群，纯文本，不贴链接。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import member_level as ml
from backend import models
from backend.emby_server import models as em
from backend.tg_bot import group_cheer, proactive

GROUP_ID = -100123


@pytest.fixture(autouse=True)
def _no_proactive_flusher(monkeypatch):
    # 关 proactive 刷盘器按用例设置（不许导入期写环境变量，
    # test_zz_env_import_hygiene 守卫口径）：免它用全局会话误发
    # 本文件入队的私聊行。
    monkeypatch.setenv("TG_PROACTIVE_FLUSHER", "0")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    ml.ensure_member_levels_seeded(session)
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


def _group_calls(sent):
    return [c for c in sent if c["chat_id"] == GROUP_ID]


def _cfg(db, key, value):
    db.add(models.SystemConfig(key=key, value=value, description=""))
    db.commit()


def _user(db, name, telegram_id=None):
    user = models.WebUser(
        username=name, password_hash="x", is_active=True,
        telegram_id=telegram_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _library_item(db, tmdb_id, name):
    library = em.Library(guid=f"lib-{tmdb_id}", name="库")
    db.add(library)
    db.commit()
    item = em.MediaItem(
        guid=f"item-{tmdb_id}",
        library_id=library.id,
        item_type="movie",
        name=name,
        tmdb_id=tmdb_id,
        file_path=f"/media/{tmdb_id}.mkv",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _request(db, user, tmdb_id, title, status="pending"):
    row = models.MovieRequest(
        user_id=user.id, movie_name=title, tmdb_id=tmdb_id, status=status,
        vote_count=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    db.add(models.MovieRequestVote(request_id=row.id, user_id=user.id))
    db.commit()
    return row


def _vote(db, request_row, user):
    db.add(models.MovieRequestVote(request_id=request_row.id, user_id=user.id))
    db.commit()


# ---------- 配置注册 ----------

def test_group_arrival_switch_registered_default_on():
    from backend import config_schema
    from backend.api import welfare_admin
    assert config_schema.WELFARE_CONFIG_SCHEMA[
        group_cheer.KEY_GROUP_ARRIVAL] == ("bool",)
    assert welfare_admin.WELFARE_CONFIG_KEYS[
        group_cheer.KEY_GROUP_ARRIVAL] == "true"


# ---------- 许愿到货报喜 ----------

def test_arrival_group_cheer_once_and_merged(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    owner = _user(db, "许愿人甲", telegram_id=1001)
    voter1 = _user(db, "附议乙", telegram_id=1002)
    voter2 = _user(db, "附议丙", telegram_id=1003)
    _library_item(db, "g1", "心愿片")
    row = _request(db, owner, "g1", "心愿片")
    _vote(db, row, voter1)
    _vote(db, row, voter2)

    assert proactive.sync_arrived_requests(db) == 1
    group = _group_calls(sent)
    assert len(group) == 1
    text = group[0]["text"]
    assert "许愿人甲" in text
    assert "《心愿片》" in text
    assert "还有 2 位朋友" in text
    assert "www.aetrix.love" in text
    assert "http" not in text

    # 重复触发（重复扫描/重复回调）不重发
    assert proactive.note_store_request_completed(db, row) == 0
    db.commit()
    assert proactive.sync_arrived_requests(db) == 0
    assert len(_group_calls(sent)) == 1


def test_arrival_group_cheer_solo_wisher(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    owner = _user(db, "独行侠", telegram_id=1101)
    _library_item(db, "g2", "冷门片")
    _request(db, owner, "g2", "冷门片")

    assert proactive.sync_arrived_requests(db) == 1
    group = _group_calls(sent)
    assert len(group) == 1
    assert "独行侠" in group[0]["text"]
    assert "还有" not in group[0]["text"]


def test_arrival_group_switch_off_private_still_claimed(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    _cfg(db, group_cheer.KEY_GROUP_ARRIVAL, "false")
    owner = _user(db, "许愿人甲", telegram_id=1201)
    _library_item(db, "g3", "安静到货")
    row = _request(db, owner, "g3", "安静到货")

    assert proactive.sync_arrived_requests(db) == 1
    assert _group_calls(sent) == []
    notices = db.query(models.TgProactiveNotice).filter(
        models.TgProactiveNotice.kind == proactive.KIND_ARRIVAL).all()
    assert len(notices) == 1
    assert notices[0].user_id == owner.id
    assert notices[0].status == "pending"


def test_arrival_group_send_failure_does_not_block(db, monkeypatch):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    owner = _user(db, "许愿人甲", telegram_id=1301)
    _library_item(db, "g4", "发送失败片")
    row = _request(db, owner, "g4", "发送失败片")
    state = {"fail_group": True}
    calls = []

    def _flaky(db, chat_id, text, reply_markup=None, parse_mode="HTML"):
        calls.append(chat_id)
        if chat_id == GROUP_ID and state["fail_group"]:
            return False, "网络炸了"
        return True, None

    monkeypatch.setattr("backend.tg_bot.sender.send_message", _flaky)
    # 到货主流程照常：状态翻 completed、私聊照常入队
    assert proactive.sync_arrived_requests(db) == 1
    db.refresh(row)
    assert row.status == "completed"
    notices = db.query(models.TgProactiveNotice).filter(
        models.TgProactiveNotice.kind == proactive.KIND_ARRIVAL).all()
    assert len(notices) == 1
    assert _group_calls([{"chat_id": c} for c in calls]) != []  # 尝试过发群

    # 发送恢复后可补发一次（失败不占幂等锚），补发后不再重发
    state["fail_group"] = False
    assert group_cheer.send_store_arrival_cheer(db, row.id) is True
    assert group_cheer.send_store_arrival_cheer(db, row.id) is False
    assert calls.count(GROUP_ID) == 2


def test_arrival_no_group_configured_skips_quietly(db, sent):
    owner = _user(db, "许愿人甲", telegram_id=1401)
    _library_item(db, "g5", "无群片")
    _request(db, owner, "g5", "无群片")
    assert proactive.sync_arrived_requests(db) == 1
    assert sent == [] or all(c["chat_id"] != GROUP_ID for c in sent)
    assert _group_calls(sent) == []


# ---------- 会员升级祝贺 ----------

def test_level_up_cheer_once_per_level(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    user = _user(db, "升级君")
    result = ml.add_xp(db, user, 100, "recharge")
    assert result["leveled_up"] is True
    db.commit()
    group = _group_calls(sent)
    assert len(group) == 1
    text = group[0]["text"]
    assert "升级君" in text
    assert "Lv.1" in text and "Lv.2" in text
    assert "影迷" in text

    # 同级内继续加经验：不发
    ml.add_xp(db, user, 10, "recharge")
    db.commit()
    assert len(_group_calls(sent)) == 1

    # 再跨一级：再发一条，且同级重复直调被幂等锚挡住
    ml.add_xp(db, user, 400, "recharge")
    db.commit()
    group = _group_calls(sent)
    assert len(group) == 2
    assert "Lv.3" in group[1]["text"] and "鉴赏家" in group[1]["text"]
    assert group_cheer.send_level_up_cheer(
        db, user.id, 2, 3) is False
    assert len(_group_calls(sent)) == 2


def test_level_down_no_cheer(db, sent):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    user = _user(db, "起伏君")
    ml.add_xp(db, user, 150, "recharge")
    db.commit()
    assert len(_group_calls(sent)) == 1
    result = ml.add_xp(db, user, -100, "refund")
    assert result["leveled_down"] is True
    db.commit()
    assert len(_group_calls(sent)) == 1


def test_level_up_hook_failure_does_not_break_add_xp(db, monkeypatch):
    _cfg(db, "bot_group_ids", str(GROUP_ID))
    user = _user(db, "稳住君")

    def _explode(*args, **kwargs):
        raise RuntimeError("报喜钩子坏了")

    monkeypatch.setattr(group_cheer, "queue_level_up_cheer", _explode)
    result = ml.add_xp(db, user, 100, "recharge")
    db.commit()
    assert result["leveled_up"] is True
    db.refresh(user)
    assert user.member_level == 2
    assert int(user.member_xp or 0) == 100
