"""贡献池私聊面板组件（Bot API 10.4 Widgets，PR-2b）。

生产环境 widget 目前仅限私聊；本面板只在私聊发送，chat_data 只放
本人摘要（盘数/状态、今日与本月被看数据）。数据口径复用
pool_digest.collect_digest_stats（每日小结同一口径），不新增采集；
面板是小结的「随时可查」版，不替代每晚 21:00 的小结推送。

协议（技术骨架复用 slots_widget 已验证的协议层）：
- 入口回调 pool_widget（callbacks._handle_pool_widget）：私聊限定、
  未绑定 toast、非池成员 toast；发 widget 时发送口核对
  chat_id＝本人 telegram_id，不符不发。
- widget 内点「刷新」→ TelegramWidget.request(
  JSON '{"a":"pool_refresh"}')（≤64 字节）；bot 收 widget_query，
  15 秒内 answerWidgetQuery 回 result JSON。
- 成功 result：{"active","pending","suspended","viewers","plays",
  "month"}；失败：{"error": 键, "msg": 文案}。
- 刷新只读：重投递天然幂等；防连点靠组件内 5 秒节流＋服务端
  每用户 5 次/5 秒滑动窗口（进程内，见 _refresh_allowed）。
- HTML 资产 backend/tg_bot/assets/pool_widget.html，压缩后 ≤4096 字节。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import zlib
from collections import deque
from pathlib import Path

from backend import models
from backend.emby_server import models as em
from backend.tg_bot import pool_digest, sender
from backend.tg_bot.identity import resolve

logger = logging.getLogger(__name__)

# 官方上限（https://core.telegram.org/bots/widgets.md，与 slots 同口径）
WIDGET_HTML_MAX_BYTES = 32768
WIDGET_HTML_COMPRESSED_MAX_BYTES = 4096  # 服务端 zlib-9 压缩后
WIDGET_STATE_MAX_BYTES = 1024            # public_data / chat_data 各自
WIDGET_ANSWER_MAX_BYTES = 1024           # answerWidgetQuery 的 data

# 刷新防连点：每用户 5 次/5 秒（进程内滑动窗口）
_REFRESH_LIMIT_COUNT = 5
_REFRESH_LIMIT_WINDOW_SECONDS = 5.0

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "pool_widget.html"

# 时钟接缝（测试可替换）：默认单调时钟
_now = time.monotonic
_refresh_hits: dict[int, deque] = {}
_refresh_lock = threading.Lock()


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


def collect_user_summary(db, user_id: int, now_sh=None) -> dict:
    """本人贡献池摘要：盘数/状态＋今日与本月被看数据。

    被看口径与 pool_digest.collect_digest_stats 同源（同一函数）；
    盘数直接读 pool_members（active/pending/suspended 三态计数，
    已退出/拒绝/清退等终态不计入面板）。today/month 为 0 也返回，
    与小结「当天 0 次不发」不同——面板随时可查，0 也要如实显示。
    """
    now = now_sh or pool_digest._now_shanghai()
    members = (
        db.query(em.PoolMember.status)
        .filter(em.PoolMember.user_id == int(user_id))
        .all()
    )
    counts = {"active": 0, "pending": 0, "suspended": 0}
    for (status,) in members:
        if status in counts:
            counts[status] += 1
    stats = pool_digest.collect_digest_stats(db, now).get(int(user_id)) or {}
    return {
        "active": counts["active"],
        "pending": counts["pending"],
        "suspended": counts["suspended"],
        "viewers": int(stats.get("viewers") or 0),
        "plays": int(stats.get("plays") or 0),
        "month": int(stats.get("month_plays") or 0),
    }


def member_count(db, user_id: int) -> int:
    """本人池成员行总数（终态也算）：0 表示从未加入过贡献池。"""
    return (
        db.query(em.PoolMember.id)
        .filter(em.PoolMember.user_id == int(user_id))
        .count()
    )


def chat_data_json(summary: dict) -> str:
    """chatData：本人摘要（仅私聊可用，群聊绝不能写个人数据）。"""
    data = json.dumps(
        {
            "active": int(summary["active"]),
            "pending": int(summary["pending"]),
            "suspended": int(summary["suspended"]),
            "viewers": int(summary["viewers"]),
            "plays": int(summary["plays"]),
            "month": int(summary["month"]),
        },
        ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_STATE_MAX_BYTES
    return data


def _summary_json(summary: dict) -> str:
    """refresh 应答的 result JSON（与 chatData 同内容）。"""
    data = chat_data_json(summary)
    assert len(data.encode()) <= WIDGET_ANSWER_MAX_BYTES
    return data


def _error_json(error: str, msg: str) -> str:
    data = json.dumps(
        {"error": error, "msg": msg}, ensure_ascii=False)
    assert len(data.encode()) <= WIDGET_ANSWER_MAX_BYTES
    return data


def _refresh_allowed(user_id: int) -> bool:
    """刷新防连点：每用户 5 次/5 秒滑动窗口（进程内）。

    刷新只读、无副作用，重投递天然幂等；本窗口只防刷，不作
    正确性保证（多进程各自一份，准静态口径可接受）。
    """
    now = _now()
    with _refresh_lock:
        dq = _refresh_hits.setdefault(int(user_id), deque())
        while dq and dq[0] <= now - _REFRESH_LIMIT_WINDOW_SECONDS:
            dq.popleft()
        if not dq:
            _refresh_hits.pop(int(user_id), None)
            dq = _refresh_hits.setdefault(int(user_id), deque())
        if len(dq) >= _REFRESH_LIMIT_COUNT:
            return False
        dq.append(now)
        return True


def send_pool_widget(db, chat_id: int, user: models.WebUser):
    """私聊发贡献池面板 widget（static widget，allow_queries）。

    返回 (ok, err, message_id)，与 sender.send_widget 同口径。
    发送口核对 chat_id＝本人 telegram_id（仅私聊）；未绑定、
    非本人、从未加入贡献池时不发，由调用方 toast 告知。
    """
    if user is None:
        return False, "请先绑定账号后再查看贡献池", None
    tg_id = getattr(user, "telegram_id", None)
    if not tg_id or int(chat_id) != int(tg_id):
        return False, "我的贡献池仅支持私聊查看", None
    if member_count(db, user.id) == 0:
        return False, "你还没有加入公共贡献池", None
    try:
        html = load_widget_html()
    except (OSError, ValueError) as exc:
        logger.error("pool widget html 不可用: %s", exc)
        return False, "我的贡献池暂不可用", None
    return sender.send_widget(
        db, chat_id, html,
        chat_data=chat_data_json(collect_user_summary(db, user.id)),
        caption="🤝 我的贡献池 · 点开查看今日被看数据",
        allow_queries=True)


def handle_widget_query(db, widget_query: dict) -> None:
    """处理 widget_query 更新：refresh → 重算本人摘要 → 应答。

    每个 query 都必须应答（15 秒内），否则 widget 侧 TIMEOUT：
    解析失败/资格失败也回 error JSON；DB 异常时尽力回 system 错误。
    本函数不抛异常（router 的兜底回复拿不到 widget 上下文）。
    """
    qid = (widget_query or {}).get("id")
    from_user = (widget_query or {}).get("from") or {}
    tg_id = from_user.get("id")
    chat = (widget_query or {}).get("chat") or {}
    chat_id = chat.get("id")
    message_id = (widget_query or {}).get("message_id")
    if not qid or not tg_id:
        logger.warning("pool widget query 缺 id/from，无法应答: %s",
                       widget_query)
        return

    def _answer(data: str) -> None:
        ok, err = sender.answer_widget_query(db, qid, data)
        if not ok:
            logger.warning("pool answerWidgetQuery 失败 qid=%s err=%s",
                           qid, err)

    try:
        # 生产环境 widget 仅限私聊；群聊 query 只可能来自测试服，
        # 本机器人不向群里发 widget，直接拒答。
        if chat and chat.get("type") not in (None, "private"):
            _answer(_error_json(
                "private_only", "我的贡献池仅支持私聊，群里看不了"))
            return
        try:
            req = json.loads((widget_query or {}).get("data") or "")
        except (ValueError, TypeError):
            req = None
        if not isinstance(req, dict) or req.get("a") != "pool_refresh":
            _answer(_error_json("bad_request", "未知请求，请重新打开面板"))
            return
        user = resolve(db, int(tg_id))
        if user is None:
            _answer(_error_json(
                "unbound", "请先私聊机器人绑定账号后再查看贡献池"))
            return
        if not _refresh_allowed(user.id):
            _answer(_error_json(
                "rate_limited", "刷新太频繁，稍后再试"))
            return
        summary = collect_user_summary(db, user.id)
        _answer(_summary_json(summary))

        # 刷新后更新 widget 状态（chatData，私聊）。「内容未变」类
        # 失败只记日志，不影响本次应答。
        try:
            sender.edit_message_widget_data(
                db, chat_id, message_id,
                chat_data=chat_data_json(summary))
        except Exception:
            logger.debug("pool widget state edit failed", exc_info=True)
    except Exception:
        logger.exception("pool widget query failed: qid=%s", qid)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            _answer(_error_json("system", "系统繁忙，请稍后再试"))
        except Exception:
            logger.debug("pool widget error answer failed", exc_info=True)
