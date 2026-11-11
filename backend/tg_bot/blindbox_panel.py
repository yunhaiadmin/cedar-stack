# -*- coding: utf-8 -*-
"""盲盒机器人面板（PR-2：公益服用户入口，私聊限定）。

口径（方案第十四节 v2）：
- 机器人只服务公益服：面板固定落在第一个公益 realm（access_mode
  =free）上，档位/奖池/记录全部走 backend.blindbox 的同一套引擎
  与账（public_tiers / draw / my_draws / my_card_pack），不另起口径。
- 三层骨架：主界面 → 用户面板「福利活动」分组 → 盲盒功能卡
  （首页列档位 → 档位页公布全概率 → 抽盒；卡包/记录是子页）。
- 私聊限定：卡包里是卡码（凭据类），抽盒是本人消费——群里点到
  只回提示卡，不渲染、不抽（门禁在 callbacks._render_menu 与
  _handle_blindbox_draw 双层）。
- 总开关/该服未开启时面板只显示「暂未开启」，抽盒按钮不出现。
- 抽盒回调按 callback id 进程内去重（有界、10 分钟）：poller
  重投递同一 callback 不许二次扣分开盒。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

from backend import blindbox as bb
from backend import models, realms
from backend.tg_bot import layout

logger = logging.getLogger(__name__)

# 抽盒回调去重窗口（进程内）：同一 callback id 10 分钟内只处理一次
_DRAW_SEEN_TTL_SECONDS = 600.0
_DRAW_SEEN_MAX = 1024
_draw_seen: "OrderedDict[str, float]" = OrderedDict()
_draw_seen_lock = threading.Lock()
_now = time.monotonic


def claim_draw_callback(cb_id: str) -> bool:
    """登记一次抽盒回调：首次返回 True，重复投递返回 False。"""
    if not cb_id:
        return True
    now = _now()
    with _draw_seen_lock:
        while _draw_seen:
            oldest_id, oldest_at = next(iter(_draw_seen.items()))
            if now - oldest_at <= _DRAW_SEEN_TTL_SECONDS and \
                    len(_draw_seen) < _DRAW_SEEN_MAX:
                break
            _draw_seen.popitem(last=False)
        if cb_id in _draw_seen:
            return False
        _draw_seen[cb_id] = now
        return True


def welfare_realm_id(db) -> int | None:
    """机器人面板落点的公益服：第一个公益 realm（按 id 序）。"""
    for realm in realms.list_realms(db):
        if realms.is_free_realm(db, realm.id):
            return int(realm.id)
    return None


def prize_label(kind: str, params: dict | None) -> str:
    """奖品大白话（机器人面：points 叫能量，access_days 是公益资格）。"""
    params = params or {}
    if kind == "points":
        return f"{int(params.get('amount') or 0)} 能量"
    if kind == "vitality":
        return f"{int(params.get('amount') or 0)} 活力"
    if kind == "access_days":
        return f"公益资格 {int(params.get('days') or 0)} 天"
    if kind == "reg_code":
        return f"注册码（{int(params.get('days') or 0)} 天）"
    if kind == "sub_code":
        return f"订阅码（{int(params.get('days') or 0)} 天）"
    if kind == "whitelist":
        return "白名单"
    return str(kind)


def _pct(frac: float) -> str:
    text = f"{float(frac) * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _nav_rows() -> list[list[dict]]:
    return [
        [{"text": "↩ 返回福利活动", "callback_data": "menu:group_welfare"}],
        layout.home_row(),
    ]


def _closed_card() -> tuple[str, dict]:
    text = layout.card(
        layout.title("盲盒"),
        [layout.row("🎁", "当前状态", "盲盒暂未开启，敬请期待")],
    )
    return text, {"inline_keyboard": _nav_rows()}


def _open_payload(db, user: models.WebUser):
    """(realm_id, public_tiers)；未开启时 payload['enabled'] 为 False。"""
    realm_id = welfare_realm_id(db)
    if realm_id is None:
        return None, None
    return realm_id, bb.public_tiers(db, realm_id)


def render_home(db, user: models.WebUser) -> tuple[str, dict]:
    """盲盒首页卡：余额＋档位列表（价格/期望返还率明盘）。"""
    _realm_id, payload = _open_payload(db, user)
    if not payload or not payload.get("enabled"):
        return _closed_card()
    rows = [
        layout.row("⚡", "我的能量", int(user.points or 0)),
    ]
    if payload.get("whitelist_stock_remaining") is not None:
        rows.append(layout.row(
            "🎟️", "白名单剩余",
            f"{int(payload['whitelist_stock_remaining'])} 个"))
    for tier in payload["tiers"]:
        rows.append(layout.row(
            "🎁", layout.esc(tier["name"]),
            f"{int(tier['price'])} 能量 · 期望返还 "
            f"{_pct(tier.get('expected_return_rate') or 0)}"))
    notes = ["点档位看全概率，确认了再抽；概率开盒前全公布。"]
    if int(payload.get("daily_spend_cap") or 0) > 0:
        notes.append(f"每人每日消费上限 {int(payload['daily_spend_cap'])} 能量。")
    text = layout.card(layout.title("盲盒"), rows, notes)
    buttons: list[list[dict]] = []
    tiers = payload["tiers"]
    for i in range(0, len(tiers), 2):
        buttons.append([
            {"text": f"🎁 {t['name']} · {int(t['price'])}",
             "callback_data": f"menu:blindbox_tier:{t['key']}"}
            for t in tiers[i:i + 2]
        ])
    buttons.append([
        {"text": "🎒 我的卡包", "callback_data": "menu:blindbox_codes"},
        {"text": "📜 我的记录", "callback_data": "menu:blindbox_draws"},
    ])
    buttons.extend(_nav_rows())
    return text, {"inline_keyboard": buttons}


def render_tier(db, user: models.WebUser,
                tier_key: str) -> tuple[str, dict]:
    """档位页：全概率表＋兜底说明＋抽盒按钮。"""
    _realm_id, payload = _open_payload(db, user)
    if not payload or not payload.get("enabled"):
        return _closed_card()
    tier = next((t for t in payload["tiers"] if t["key"] == tier_key), None)
    if tier is None:
        text = layout.card(
            layout.title("盲盒"),
            [layout.row("🎁", "档位", "这个档位已下架")],
        )
        return text, {"inline_keyboard": [
            [{"text": "↩ 盲盒首页", "callback_data": "menu:blindbox"}],
            *_nav_rows(),
        ]}
    rows = [
        layout.row("💰", "售价", f"{int(tier['price'])} 能量"),
        layout.row("⚡", "我的能量", int(user.points or 0)),
        layout.row("📊", "期望返还",
                   _pct(tier.get("expected_return_rate") or 0)),
    ]
    for prize in tier["prizes"]:
        rows.append(layout.row(
            "·", layout.esc(prize_label(prize["kind"], prize["params"])),
            _pct(prize.get("probability") or 0)))
    fallback = tier.get("fallback_prize") or {}
    notes = [
        "兜底：中到库存/预算不足的奖时改发"
        + prize_label(fallback.get("kind"), fallback.get("params"))
        + "，不会吞奖。",
    ]
    text = layout.card(
        layout.title(f"盲盒 · {layout.esc(tier['name'])}"), rows, notes)
    markup = {"inline_keyboard": [
        [{"text": f"🎁 抽一盒 · {int(tier['price'])} 能量",
          "callback_data": f"blindbox_draw:{tier['key']}"}],
        [{"text": "↩ 换个档位", "callback_data": "menu:blindbox"}],
        *[[{"text": "↩ 返回福利活动",
            "callback_data": "menu:group_welfare"}]],
        layout.home_row(),
    ]}
    return text, markup


def render_codes(db, user: models.WebUser) -> tuple[str, dict]:
    """我的卡包：盲盒赢得的卡码（含是否已用）。"""
    codes = bb.my_card_pack(db, user)
    rows = []
    for c in codes[:15]:
        kind = {1: "注册码", 2: "订阅码", 3: "白名单码"}.get(
            int(c.get("code_type") or 0), "卡码")
        state = "已使用" if c.get("used") else "未使用"
        rows.append(layout.row(
            "🎟️", kind,
            f"<code>{layout.esc(c.get('code'))}</code> · {state}"))
    if not rows:
        rows.append(layout.row("🎒", "卡包", "还没有从盲盒赢到卡码"))
    text = layout.card(layout.title("我的卡包"), rows,
                       ["卡码在网站补给站「核销中心」使用。"])
    return text, {"inline_keyboard": [
        [{"text": "↩ 盲盒首页", "callback_data": "menu:blindbox"}],
        *_nav_rows(),
    ]}


def render_draws(db, user: models.WebUser) -> tuple[str, dict]:
    """我的记录：最近抽盒（奖品/状态/时间）。"""
    realm_id = welfare_realm_id(db)
    _total, records = bb.my_draws(db, user, realm_id, limit=10)
    rows = []
    for r in records:
        view = bb.draw_view(r)
        prize = view["prize"]
        label = prize_label(prize.get("kind"), prize.get("params"))
        if prize.get("fallback_from"):
            label += "（兜底改发）"
        status = {"delivered": "已发放",
                  "delivery_failed": "发放失败",
                  "failed": "失败未扣分"}.get(view["status"], view["status"])
        when = (view.get("created_at") or "")[:16].replace("T", " ")
        rows.append(layout.row(
            "🎁", layout.esc(view.get("tier_name") or ""),
            f"{label} · {status} · {layout.esc(when)}"))
    if not rows:
        rows.append(layout.row("📜", "记录", "还没有抽过盒"))
    text = layout.card(layout.title("我的抽盒记录"), rows)
    return text, {"inline_keyboard": [
        [{"text": "↩ 盲盒首页", "callback_data": "menu:blindbox"}],
        *_nav_rows(),
    ]}


def do_draw(db, user: models.WebUser, tier_key: str):
    """抽一盒：走引擎 draw（扣分+定奖+发放一抽一事务）。

    返回 (ok, text, markup)：成功是开盒结果卡；失败 ok=False、
    text 是人话原因（未扣分），markup 为 None（调用方已先应答
    回调，失败原因走私聊消息送达）。
    """
    realm_id = welfare_realm_id(db)
    if realm_id is None:
        return False, "盲盒暂未开启", None
    try:
        result = bb.draw(db, user, realm_id, tier_key)
    except bb.BlindboxError as exc:
        return False, str(exc), None
    except Exception:  # noqa: BLE001 引擎外异常统一人话，不漏技术细节
        logger.exception("tg blindbox draw failed: user=%s tier=%s",
                         user.id, tier_key)
        try:
            db.rollback()
        except Exception:
            pass
        return False, "系统繁忙，请稍后再试", None
    prize = result["prize"]
    rows = [
        layout.row("🎁", "开出",
                   layout.esc(prize_label(prize["kind"], prize["params"]))),
    ]
    if prize.get("code"):
        rows.append(layout.row(
            "🎟️", "卡码", f"<code>{layout.esc(prize['code'])}</code>"))
    if prize.get("fallback_from"):
        rows.append(layout.row(
            "🛟", "兜底改发",
            "原奖库存/预算不足，已按规则改发上面的奖"))
    rows.append(layout.row("💰", "本盒", f"{int(result['price'])} 能量"))
    if result.get("balance") is not None:
        rows.append(layout.row("⚡", "剩余能量", int(result["balance"])))
    text = layout.card(layout.title("开盒结果"), rows,
                       ["卡码也可以在「我的卡包」里随时查看。"])
    markup = {"inline_keyboard": [
        [{"text": "🎁 再抽一盒",
          "callback_data": f"blindbox_draw:{result['tier_key']}"}],
        [{"text": "🎒 我的卡包", "callback_data": "menu:blindbox_codes"},
         {"text": "📜 我的记录", "callback_data": "menu:blindbox_draws"}],
        [{"text": "↩ 盲盒首页", "callback_data": "menu:blindbox"}],
        layout.home_row(),
    ]}
    return True, text, markup
