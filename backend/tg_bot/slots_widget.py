"""TG Bot HTML 版老虎机（Bot API 10.4 Widgets，2026-11-09）。

生产环境 widget 目前仅限私聊、自动 protected content、不可转发；
群聊继续走 sendDice 版（backend/tg_bot/callbacks._handle_slots_bet），
不得拆除。客户端须 ≥13.2（Android/iOS/macOS）/ Desktop ≥7.4，
旧客户端只显示「不支持的媒体」占位符＋caption。

协议（与 ~/workspace/tg-slots-widget/ PoC 同款，官方指南
https://core.telegram.org/bots/widgets.md）：
- widget 点拉杆时在点击处理内同步
  TelegramWidget.request(JSON '{"a":"spin","bet":N}')（≤64 字节）；
  bot 收 widget_query 更新，15 秒内 answerWidgetQuery 回 result。
- 成功 result：{"symbols": [...], "win": int, "balance": int,
  "pool": int}；失败：{"error": <slots.py reason 键>, "msg": 文案}。
- publicData（共享状态）= {"pool": 奖池, "pay": 赔付表版本}；
  chatData（私聊本消息）= {"balance": 余额, "pool": 奖池}。
  群聊 chatData 全员同一份，绝不能写个人余额——本模块只接私聊。
- 结果来源：服务端 secrets 生成 1–64 均匀随机数，走同一
  slots.decode_dice；结算复用 begin_spin / settle_spin 全套；
  spin_key = "tgwq:<widget_query id>"（重投递幂等）。
- widget 代码发送后不可变：赔付表只用于演示与页脚，真实结算
  永远以服务端 result 为准；pay 版本由赔付表派生，改表即变版。

做不到的点（见模块末尾注释与测试）：
- widget 消息本身不自毁（私聊本无自毁口径；群版 30 秒自毁不适用，
  因生产环境 widget 发不到群里）。
- 每局结果除 widget 内显示外，仍按既有私聊口径另发一行结果卡
  （slots_result_text），聊天记录里留档；widget 内结果与卡一致。
- 旧客户端看不到 widget 内容，只能看到 caption；须用新版客户端。
- TON 钱包类 query（WidgetQueryType*）不支持：只认 data 型 spin 请求。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import zlib
from pathlib import Path

from backend import models
from backend.tg_bot import sender
from backend.tg_bot.identity import resolve

logger = logging.getLogger(__name__)

# 官方上限（https://core.telegram.org/bots/widgets.md）
WIDGET_HTML_MAX_BYTES = 32768
WIDGET_HTML_COMPRESSED_MAX_BYTES = 4096  # 服务端 zlib-9 压缩后
WIDGET_STATE_MAX_BYTES = 1024            # public_data / chat_data 各自
WIDGET_ANSWER_MAX_BYTES = 1024           # answerWidgetQuery 的 data
WIDGET_REQUEST_MAX_BYTES = 64            # widget 发来的 data

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "slots_widget.html"


def widget_html_stats() -> dict:
    """widget.html 体积断言口径：未压缩 / zlib-9 压缩后字节数。"""
    raw = _ASSET_PATH.read_bytes()
    return {
        "raw_bytes": len(raw),
        "compressed_bytes": len(zlib.compress(raw, 9)),
        "has_nul": b"\x00" in raw,
    }


def load_widget_html() -> str:
    """读 widget.html 并校验官方上限；超限即 ValueError（不发）。"""
    stats = widget_html_stats()
    if stats["has_nul"]:
        raise ValueError("widget html 含 NUL 字符，服务端会拒绝")
    if stats["raw_bytes"] > WIDGET_HTML_MAX_BYTES:
        raise ValueError(
            f"widget html {stats['raw_bytes']} 字节，超过 "
            f"{WIDGET_HTML_MAX_BYTES} 上限")
    if stats["compressed_bytes"] > WIDGET_HTML_COMPRESSED_MAX_BYTES:
        raise ValueError(
            f"widget html 压缩后 {stats['compressed_bytes']} 字节，超过 "
            f"{WIDGET_HTML_COMPRESSED_MAX_BYTES} 上限")
    return _ASSET_PATH.read_text(encoding="utf-8")


def pay_version(db) -> int:
    """赔付表版本号：由 pay_table 派生，改表即变版。

    widget 代码发送后不可变，赔付表只用于演示与页脚；真实结算
    以服务端 result 为准，版本号只供 widget 对齐展示用。
    """
    from backend import slots as slots_svc

    blob = json.dumps(
        slots_svc.pay_table(db), sort_keys=True, ensure_ascii=False)
    return int(hashlib.md5(blob.encode()).hexdigest()[:6], 16)


def public_data_json(db) -> str:
    """publicData：奖池摘要＋赔付表版本（共享状态，全员可见）。"""
    from backend import slots as slots_svc

    data = json.dumps(
        {"pool": int(slots_svc.pool_balance(db)), "pay": pay_version(db)},
        ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_STATE_MAX_BYTES
    return data


def chat_data_json(balance: int, pool: int) -> str:
    """chatData：本消息余额摘要（仅私聊可用，群聊绝不能写个人余额）。"""
    data = json.dumps(
        {"balance": int(balance), "pool": int(pool)}, ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_STATE_MAX_BYTES
    return data


def _result_json(symbols, win: int, balance: int, pool: int) -> str:
    data = json.dumps(
        {"symbols": list(symbols), "win": int(win),
         "balance": int(balance), "pool": int(pool)},
        ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_ANSWER_MAX_BYTES
    return data


def _error_json(error: str, msg: str) -> str:
    data = json.dumps(
        {"error": error, "msg": msg}, ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_ANSWER_MAX_BYTES
    return data


def _settle_result_json(db, spin) -> str:
    """已结算 spin 的 result JSON（重投递复用，不重摇、不重发奖）。"""
    from backend import slots as slots_svc

    reels = tuple(spin.reels.split(",")) if spin.reels else ()
    if not reels and spin.dice_value:
        reels = slots_svc.decode_dice(int(spin.dice_value))
    win = int(spin.base_payout or 0) + int(spin.pool_payout or 0)
    user = db.query(models.WebUser).filter(
        models.WebUser.id == spin.user_id).first()
    balance = int(user.points or 0) if user is not None else 0
    return _result_json(reels, win, balance, slots_svc.pool_balance(db))


def send_slots_widget(db, chat_id: int, user: models.WebUser):
    """私聊发 HTML 版老虎机 widget（static widget，allow_queries）。

    返回 (ok, err, message_id)，与 sender.send_widget 同口径。
    未绑定/总开关关闭时不发，由调用方先判（本函数双保险再判一次）。
    """
    from backend import slots as slots_svc

    if user is None:
        return False, "请先绑定账号后再玩老虎机", None
    if not slots_svc.is_enabled(db):
        return False, "🎰 老虎机暂未开启", None
    try:
        html = load_widget_html()
    except (OSError, ValueError) as exc:
        logger.error("slots widget html 不可用: %s", exc)
        return False, "HTML 版老虎机暂不可用", None
    return sender.send_widget(
        db, chat_id, html,
        public_data=public_data_json(db),
        chat_data=chat_data_json(
            int(user.points or 0), slots_svc.pool_balance(db)),
        caption="🎰 能量老虎机 · HTML 版",
        allow_queries=True)


def handle_widget_query(db, widget_query: dict) -> None:
    """处理 widget_query 更新：开局 → 服务端随机 → 结算 → 应答。

    每个 query 都必须应答（15 秒内），否则 widget 侧 TIMEOUT：
    解析失败/资格失败也回 error JSON；DB 异常时尽力回 system 错误。
    本函数不抛异常（router 的兜底回复拿不到 widget 上下文）。
    """
    from backend import slots as slots_svc
    from backend.tg_bot import handlers

    qid = (widget_query or {}).get("id")
    from_user = (widget_query or {}).get("from") or {}
    tg_id = from_user.get("id")
    chat = (widget_query or {}).get("chat") or {}
    chat_id = chat.get("id")
    message_id = (widget_query or {}).get("message_id")
    if not qid or not tg_id:
        logger.warning("slots widget query 缺 id/from，无法应答: %s",
                       widget_query)
        return

    def _answer(data: str) -> None:
        ok, err = sender.answer_widget_query(db, qid, data)
        if not ok:
            logger.warning("slots answerWidgetQuery 失败 qid=%s err=%s",
                           qid, err)

    try:
        # 生产环境 widget 仅限私聊；群聊 query 只可能来自测试服，
        # 本机器人不向群里发 widget，直接拒答、不扣款。
        if chat and chat.get("type") not in (None, "private"):
            _answer(_error_json(
                "private_only", "HTML 版老虎机仅支持私聊，群里请用普通版"))
            return
        try:
            req = json.loads((widget_query or {}).get("data") or "")
        except (ValueError, TypeError):
            req = None
        if not isinstance(req, dict) or req.get("a") != "spin":
            _answer(_error_json("bad_request", "未知请求，请重新打开老虎机"))
            return
        bet = req.get("bet")
        if isinstance(bet, bool) or not isinstance(bet, int):
            _answer(_error_json("bad_request", "注额档位无效"))
            return

        user = resolve(db, int(tg_id))
        if user is None:
            _answer(_error_json(
                "unbound", "请先私聊机器人绑定账号后再玩老虎机"))
            return

        spin_key = f"tgwq:{qid}"
        try:
            begin = slots_svc.begin_spin(
                db, user, bet, spin_key, origin="private")
        except Exception:
            logger.exception("slots widget begin failed: user=%s bet=%s",
                             user.id, bet)
            db.rollback()
            _answer(_error_json("system", "系统繁忙，请稍后再试"))
            return
        if not begin.get("ok"):
            extra = {k: v for k, v in begin.items()
                     if k not in ("ok", "reason")}
            _answer(_error_json(
                begin.get("reason") or "system",
                slots_svc.reason_message(
                    begin.get("reason") or "", **extra)))
            return

        spin = begin["spin"]
        if begin.get("duplicate"):
            if spin.status == "settled":
                # 重投递：只回既有结果，不重摇、不重发奖、不补发结果卡
                _answer(_settle_result_json(db, spin))
            else:
                _answer(_error_json(
                    "in_flight",
                    slots_svc.reason_message("in_flight")))
            return

        # 服务端随机：1–64 均匀，走同一 decode_dice（widget 不参与定结果）
        dice_value = secrets.randbelow(64) + 1
        try:
            result = slots_svc.settle_spin(
                db, spin, dice_value, origin="private")
        except Exception:
            logger.exception("slots widget settle failed: spin=%s", spin.id)
            db.rollback()
            _answer(_error_json("settle_failed", "结算异常，请联系管理员"))
            return
        spin = result["spin"]
        total = int(result["base_payout"]) + int(result["pool_payout"])
        user = db.query(models.WebUser).filter(
            models.WebUser.id == spin.user_id).first()
        balance = int(user.points or 0) if user is not None else 0
        pool = slots_svc.pool_balance(db)
        _answer(_result_json(result["reels"], total, balance, pool))

        # 结算后更新 widget 状态：奖池摘要（publicData）＋余额摘要
        # （chatData，私聊）。「内容未变」类失败只记日志，不影响本局。
        try:
            sender.edit_message_widget_data(
                db, chat_id, message_id,
                public_data=public_data_json(db),
                chat_data=chat_data_json(balance, pool))
        except Exception:
            logger.debug("slots widget state edit failed", exc_info=True)

        # 既有私聊口径保留：另发一行结果卡进聊天记录（与 widget 内一致）
        try:
            sender.send_panel(
                db, chat_id, handlers.slots_result_text(db, spin),
                handlers.slots_markup(db))
        except Exception:
            logger.debug("slots widget result card failed", exc_info=True)
    except Exception:
        logger.exception("slots widget query failed: qid=%s", qid)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            _answer(_error_json("system", "系统繁忙，请稍后再试"))
        except Exception:
            logger.debug("slots widget error answer failed", exc_info=True)
