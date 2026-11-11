# -*- coding: utf-8 -*-
"""盲盒中奖群报喜测试（2026-11-11 用户亲点）。

钉死口径：
- 只报大奖：whitelist（任何档位）/ access_days >= 90 天 /
  reg_code、sub_code（任何天数）；以最终实际交付的奖品为准
  （兜底改发的 365 天注册码照报）。积分、活力、7/30 天资格不报。
- 报喜发往正式群（bot_group_ids 第一个群），带站内用户名、档位名、
  奖品人话描述；白名单带「全场限量还剩 X 个」。
- 同一用户短时间连中多注大奖逐注都报（2026-11-11 20:50 用户亲令
  取消 60 分钟防刷限制）。
- 开关 blindbox_announce_enabled 默认开（缺行按开），关了不发。
- 发送失败/异常绝不影响抽盒结果与落账。

全部用隔离内存 SQLite，发送层 monkeypatch 捕获，不碰真实 TG。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — 注册 web / system_configs 等表
from backend import blindbox as bb
from backend import blindbox_announce as ann
from backend.database import Base
from backend.integrations import store as config_store

GROUP_ID = -1001234567890


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(factory):
    s = factory()
    config_store.invalidate()
    try:
        yield s
    finally:
        config_store.invalidate()
        s.close()


@pytest.fixture()
def sent(monkeypatch):
    calls = []

    def fake_send(db_, chat_id, text, reply_markup=None,
                  parse_mode="HTML", reply_parameters=None):
        calls.append({"chat_id": chat_id, "text": text})
        return True, None

    monkeypatch.setattr("backend.tg_bot.sender.send_message", fake_send)
    return calls


def _set_config(db, key, value):
    from backend import models

    row = (db.query(models.SystemConfig)
           .filter(models.SystemConfig.key == key).first())
    if row is None:
        db.add(models.SystemConfig(key=key, value=value))
    else:
        row.value = value
    db.commit()
    config_store.invalidate()


def _realm(db, slug, access_mode):
    from backend import models

    r = models.ServerRealm(
        name=f"服-{slug}", slug=slug, url="", access_mode=access_mode,
        is_active=True,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _user(db, name="alice", points=100000):
    from backend import models

    u = models.WebUser(username=name, password_hash="x", points=points)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _prize(kind, weight, **params):
    return {"kind": kind, "weight": weight, "params": params}


def _tier(prizes, fallback=None, price=100, name="至尊盒"):
    return {
        "key": "t1",
        "name": name,
        "price": price,
        "enabled": True,
        "sort": 0,
        "fallback_prize": fallback or _prize("points", 1, amount=10),
        "prizes": prizes,
    }


def _save(db, realm_id, tiers, *, whitelist_stock_total=3):
    bb.save_master_switch(db, True)
    bb.save_realm_config(db, realm_id, {
        "enabled": True,
        "starts_at": "",
        "ends_at": "",
        "daily_spend_cap": 0,
        "access_days_daily_budget": 3650,
        "whitelist_stock_total": whitelist_stock_total,
        "tiers": tiers,
    })
    db.commit()
    config_store.invalidate()


def _setup(db, access_mode="free", tiers=None, stock=3):
    realm = _realm(db, f"r{access_mode}", access_mode)
    user = _user(db)
    # 注册码 30 天估值 450，单奖位档位售价须 >=450 才过返还率校验。
    _save(db, realm.id,
          tiers or [_tier([_prize("reg_code", 1, days=30)], price=500)],
          whitelist_stock_total=stock)
    _set_config(db, "bot_group_ids", str(GROUP_ID))
    return realm, user


# ---------------------------------------------------------------------------
# 够格矩阵（直接走报喜口）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,params,big", [
    ("whitelist", {}, True),
    ("access_days", {"days": 90}, True),
    ("access_days", {"days": 365}, True),
    ("access_days", {"days": 89}, False),
    ("access_days", {"days": 30}, False),
    ("access_days", {"days": 7}, False),
    ("reg_code", {"days": 30}, True),
    ("reg_code", {"days": 365}, True),
    ("sub_code", {"days": 7}, True),
    ("points", {"amount": 1000}, False),
    ("vitality", {"amount": 3}, False),
])
def test_big_prize_matrix(db, sent, kind, params, big):
    realm, user = _setup(db)
    cfg = bb.get_realm_config(db, realm.id)
    ok = ann.maybe_announce(db, user, realm.id, "至尊盒", kind, params,
                            cfg=cfg)
    assert ok is big
    assert len(sent) == (1 if big else 0)
    if big:
        assert sent[0]["chat_id"] == GROUP_ID
        assert "alice" in sent[0]["text"]
        assert "至尊盒" in sent[0]["text"]


def test_prize_labels_free_vs_paid(db, sent):
    free_realm, user = _setup(db, "free")
    cfg = bb.get_realm_config(db, free_realm.id)
    assert ann.maybe_announce(db, user, free_realm.id, "豪华盒",
                              "access_days", {"days": 90}, cfg=cfg)
    assert "公益资格 90 天" in sent[-1]["text"]

    paid_realm = _realm(db, "rpaid2", "paid")
    _save(db, paid_realm.id, [_tier([_prize("points", 1, amount=10)])])
    cfg2 = bb.get_realm_config(db, paid_realm.id)
    assert ann.maybe_announce(db, user, paid_realm.id, "豪华盒",
                              "access_days", {"days": 90}, cfg=cfg2)
    assert "订阅 90 天" in sent[-1]["text"]


def test_whitelist_message_shows_remaining(db, sent):
    realm, user = _setup(db, stock=3)
    cfg = bb.get_realm_config(db, realm.id)
    assert ann.maybe_announce(db, user, realm.id, "至尊盒",
                              "whitelist", {}, cfg=cfg)
    assert "白名单" in sent[0]["text"]
    assert "还剩 3 个" in sent[0]["text"]


# ---------------------------------------------------------------------------
# 抽盒集成（挂点在 draw 成功交付之后）
# ---------------------------------------------------------------------------

def test_draw_reg_code_announces(db, sent):
    realm, user = _setup(db)
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "reg_code"
    assert len(sent) == 1
    assert "注册码（30 天）" in sent[0]["text"]


def test_draw_points_no_announce(db, sent):
    realm, user = _setup(
        db, tiers=[_tier([_prize("points", 1, amount=50)])])
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "points"
    assert sent == []


def test_draw_whitelist_announces_with_remaining(db, sent):
    # 白名单估值 3000，单奖位档位售价须 >=3000 才过返还率校验。
    realm, user = _setup(
        db, tiers=[_tier([_prize("whitelist", 1)], price=3000)], stock=3)
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "whitelist"
    assert len(sent) == 1
    assert "白名单" in sent[0]["text"]
    assert "还剩 2 个" in sent[0]["text"]


def test_draw_fallback_reg_code_365_announces(db, sent):
    """白名单库存抽空改发兜底 365 天注册码：以实际交付为准，照报。"""
    realm, user = _setup(
        db,
        tiers=[_tier([_prize("whitelist", 1)],
                     fallback=_prize("reg_code", 1, days=365),
                     price=3000)],
        stock=0,
    )
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "reg_code"
    assert result["prize"].get("fallback_from") == "whitelist"
    assert len(sent) == 1
    assert "注册码（365 天）" in sent[0]["text"]


def test_draw_two_big_wins_both_announce(db, sent):
    """同用户短时间连中两注大奖：逐注都报，不跳过（无防刷限制）。"""
    realm, user = _setup(db)
    bb.draw(db, user, realm.id, "t1")
    bb.draw(db, user, realm.id, "t1")
    assert len(sent) == 2


def test_announce_switch_off_no_send(db, sent):
    realm, user = _setup(db)
    _set_config(db, "blindbox_announce_enabled", "false")
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "reg_code"
    assert sent == []


def test_announce_sender_error_draw_still_succeeds(db, monkeypatch):
    realm, user = _setup(db)

    def boom(db_, chat_id, text, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("backend.tg_bot.sender.send_message", boom)
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "reg_code"
    from backend import models

    record = (db.query(models.BlindboxDraw)
              .filter(models.BlindboxDraw.id == result["draw_id"]).first())
    assert record is not None
    assert record.status == "delivered"
    assert record.charged is True


def test_no_group_configured_no_send_no_error(db, sent):
    realm = _realm(db, "rnogroup", "free")
    user = _user(db)
    _save(db, realm.id, [_tier([_prize("reg_code", 1, days=30)], price=500)])
    result = bb.draw(db, user, realm.id, "t1")
    assert result["prize"]["kind"] == "reg_code"
    assert sent == []
