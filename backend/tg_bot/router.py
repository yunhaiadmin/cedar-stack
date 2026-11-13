"""Telegram 消息路由：把 getUpdates 的单个 update 分发到命令处理器。

B4 策略（全部可配置，见 backend/tg_bot/policy.py）：
- 总开关 bot_enabled：关闭后静默丢弃所有 update（poller 继续跑）
- 群白名单 bot_group_ids：非名单群聊的消息/callback 静默忽略，私聊不受影响
- 命令开关：关闭的命令回复"该功能已关闭"（/start /help 常开）
- 限流：每用户每命令间隔 bot_rate_limit_seconds 秒；每群每分钟最多
  bot_group_rate_limit 条；超限静默丢弃（用户级限流仍提示一句话）
- 消息自毁（二期接线，见 ephemeral.py）：群聊里机器人回复默认 30 秒
  自毁（红包本体豁免）、用户命令消息默认 3 秒自毁；私聊不自毁。
- 频道马甲治理：群里以外部频道身份（sender_chat）发言的消息默认删除
  ＋回说明（匿名管理员形态不处理，判别见 policy.mask_sender_chat）。
- 退群处置：chat_member 更新里已绑定用户退群/被踢时，按
  bot_leave_action（默认 cancel_welfare）只取消其公益资格。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from backend.tg_bot import policy

logger = logging.getLogger(__name__)

# 命令级限流：(tg_user_id, cmd) -> 上次通过时间的 monotonic 时间戳
_user_cmd_limits: dict[tuple[int, str], float] = {}
# 群级限流：chat_id -> 最近 60 秒内已处理消息的时间戳滑动窗口
_group_windows: dict[int, deque[float]] = {}
_rate_lock = threading.Lock()

# 内存结构上限：超过后触发一次清理，防止长期运行泄漏
_MAX_TRACKED_KEYS = 10000
# 清理时保留多久的记录（单调时钟秒）；远大于任何合理限流窗口即可
_RETENTION_SECONDS = 3600.0
_GROUP_WINDOW_SECONDS = 60.0

_GROUP_CHAT_TYPES = ("group", "supergroup")


def _is_group_chat(chat: dict) -> bool:
    """是否为群聊（group/supergroup）；type 缺失或未知时按私聊处理（不拦截）。"""
    return chat.get("type") in _GROUP_CHAT_TYPES


def _user_cmd_allow(tg_user_id: int, cmd: str, seconds: float) -> bool:
    """用户级命令限流：距离上次通过不足 seconds 秒则返回 False。"""
    now = time.monotonic()
    with _rate_lock:
        last = _user_cmd_limits.get((tg_user_id, cmd))
        if last is not None and now - last < seconds:
            return False
        _user_cmd_limits[(tg_user_id, cmd)] = now
        if len(_user_cmd_limits) > _MAX_TRACKED_KEYS:
            cutoff = now - max(seconds, _RETENTION_SECONDS)
            for k, v in list(_user_cmd_limits.items()):
                if v < cutoff:
                    del _user_cmd_limits[k]
        return True


def _group_allow(chat_id: int, per_minute: int) -> bool:
    """群级滑动窗口限流：60 秒内已处理数达到上限则返回 False（静默丢弃）。"""
    now = time.monotonic()
    window_start = now - _GROUP_WINDOW_SECONDS
    with _rate_lock:
        dq = _group_windows.get(chat_id)
        if dq is None:
            dq = deque()
            _group_windows[chat_id] = dq
        while dq and dq[0] < window_start:
            dq.popleft()
        if len(dq) >= per_minute:
            return False
        dq.append(now)
        if len(_group_windows) > _MAX_TRACKED_KEYS:
            for cid, old_dq in list(_group_windows.items()):
                while old_dq and old_dq[0] < window_start:
                    old_dq.popleft()
                if not old_dq:
                    del _group_windows[cid]
        return True


def _verify_bind_code(db, update: dict) -> None:
    """验证网页端发起的绑定码（流程 A）：纯 6 位数字消息走共享验证函数。"""
    from backend.tg_bot import handlers

    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    tg_user = msg.get("from") or {}
    tg_user_id = tg_user.get("id")
    chat_id = (msg.get("chat") or {}).get("id")
    if not tg_user_id or not chat_id:
        return
    reply = handlers.verify_bind_code(db, tg_user_id, chat_id, text)
    if reply:
        # 绑定码回复也是机器人群回复：群里照常挂 30 秒自毁，私聊不挂
        _send_reply(db, chat_id, reply,
                    autodelete=_is_group_chat(msg.get("chat") or {}))
    # reply 为 None → 不是有效的绑定码，静默忽略


def _send_reply(db, chat_id, text, reply_markup=None, *, autodelete=False):
    """机器人回复统一发送口（二期自毁接线）。

    autodelete=True 的发送点（仅群聊）走 send_message_ex 拿 message_id，
    再按 bot_msg_autodelete_seconds（默认 30 秒、0=关）挂自毁；
    延时为 0 时回落普通发送。私聊与豁免发送点（红包本体）不传此参数。
    发送失败或拿不到 message_id 时静默降级：自毁只是群面整洁手段，
    绝不让它影响回复本身。
    """
    from backend.tg_bot import sender

    if not autodelete:
        sender.send_message(db, chat_id, text, reply_markup)
        return
    from backend.tg_bot import ephemeral

    delay = ephemeral.bot_msg_delay_seconds(db)
    if delay <= 0:
        sender.send_message(db, chat_id, text, reply_markup)
        return
    ok, _err, message_id = sender.send_message_ex(db, chat_id, text, reply_markup)
    if ok and message_id:
        ephemeral.schedule_delete(chat_id, message_id, delay)


def _send_ephemeral_reply(db, chat_id, text, reply_markup, receiver_tg_id, *,
                          in_group: bool):
    """临时消息发送口（Bot API ephemeral，首个落点 /kk 群查人卡）。

    只发给 receiver_tg_id 本人。临时消息官方不保证送达，发送失败
    必须如实回落现行公开路径（_send_reply＋群自毁口径不变），绝不
    静默丢卡。发送成功把 (chat, receiver) → ephemeral_message_id
    记入进程内登记（面板回调的 message 缺该字段时兜底定位），并
    按 bot_msg_autodelete_seconds 挂 deleteEphemeralMessage 定时
    删除（延时 0=关时 schedule 自行不挂）。
    """
    from backend.tg_bot import ephemeral, sender

    ok, err, eph_id = sender.send_ephemeral_message(
        db, chat_id, text,
        receiver_user_id=int(receiver_tg_id), reply_markup=reply_markup)
    if not ok:
        logger.warning(
            "tg ephemeral send failed, fallback to public reply: "
            "chat=%s receiver=%s err=%s", chat_id, receiver_tg_id, err)
        _send_reply(db, chat_id, text, reply_markup=reply_markup,
                    autodelete=in_group)
        return
    if eph_id:
        ephemeral.note_ephemeral_message(
            chat_id, int(receiver_tg_id), eph_id)
        ephemeral.schedule_ephemeral_delete(
            chat_id, int(receiver_tg_id), eph_id,
            ephemeral.bot_msg_delay_seconds(db))


def _schedule_cmd_autodelete(db, chat_id, message_id):
    """群聊里用户命令消息本体挂 3 秒自毁（bot_cmd_autodelete_seconds）。

    延时为 0（关闭）时不挂。群内 /redeem 的原命令消息走既有即时删除、
    不经这里；万一某条消息已被即时删除又被挂上定时，到期删除失败也
    只在 ephemeral 内记一条日志，不外抛、不刷屏。
    """
    if not chat_id or not message_id:
        return
    from backend.tg_bot import ephemeral

    delay = ephemeral.bot_cmd_delay_seconds(db)
    if delay <= 0:
        return
    ephemeral.schedule_delete(chat_id, message_id, delay)


def _reply_private_only(db, chat_id, msg, *, schedule_cmd: bool = True):
    """私聊命令在群里被调用的统一回复：提示卡＋去私聊跳转按钮。

    所有私聊命令（private_only 集、P1 个人查询、群内 /redeem）共用
    这一种形态：handlers.private_only_card 文案＋private_only_markup
    深链按钮；回复按群面自毁口径发送，命令消息本体挂 3 秒自毁。
    /redeem 的原消息已即时删除（护码），不再重复挂定时
    （schedule_cmd=False）。
    """
    from backend.tg_bot import handlers

    _send_reply(db, chat_id, handlers.private_only_card(),
                reply_markup=handlers.private_only_markup(db),
                autodelete=True)
    if schedule_cmd:
        _schedule_cmd_autodelete(db, chat_id, msg.get("message_id"))


# P1 自助查询命令：个人数据，群聊里不执行（handler 层另有私聊 guard 双保险）
_P1_COMMANDS = ("/bindings", "/transfers", "/whyplay", "/wants")

# 群匿名管理员：以群/频道身份发言时，消息的 from 是 GroupAnonymousBot
# （固定 id 1087968824）且带 sender_chat——Telegram 协议上认不出背后是
# 哪位管理员，handler 里 resolve 永远查不到人。此前这类消息会被误当
# 未绑定用户回绑定指引（2026-10-30 站长群里匿名发 /redpacket 坐实）。
GROUP_ANONYMOUS_BOT_ID = 1087968824

ANON_ADMIN_TEXT = (
    "🎭 你现在是匿名管理员身份，我认不出是哪位管理员，这个操作办不了。\n"
    "请关掉匿名、用你本人身份再发一次就行；你的账号绑定是好的，不用重新绑定。"
)

# 需要发送者身份的命令全集：handler 内走 resolve / _bound_user /
# _p1_user，匿名发在 router 层统一拦下回 ANON_ADMIN_TEXT。
# 不在表内＝公开命令（/help /rank /requestboard /rules：不查
# 发送者身份、只读全站或群级数据），匿名发照常执行。
_IDENTITY_COMMANDS = {
    "/start", "/bind", "/points",
    "/account", "/me", "/request", "/myrequests", "/login", "/renew",
    "/govlog", "/level", "/report", "/propose", "/launch",
    "/redeem",
    "/bindings", "/transfers", "/whyplay", "/wants", "/reconcile",
    "/rankpush", "/playrank",
    "/kk",
}


def _is_anonymous_admin(msg: dict) -> bool:
    """消息是否为匿名身份（群匿名管理员 / 以频道身份发言）。

    判据任一命中：消息带 sender_chat，或 from 是 GroupAnonymousBot
    固定 id。私聊没有这种消息，天然不受影响。
    """
    if msg.get("sender_chat"):
        return True
    return (msg.get("from") or {}).get("id") == GROUP_ANONYMOUS_BOT_ID


# 频道马甲治理的群内说明（走 _send_reply 自毁口径，群里 30 秒自毁）
CHANNEL_MASK_TEXT = (
    "🎭 本群不允许以频道身份发言，这条消息已删除。\n"
    "请用你本人账号发言；频道确需在本群发言，请联系管理员加白名单。"
)

# 退群取消公益资格后私聊通知本人的文案
LEAVE_WELFARE_TEXT = (
    "👋 你已退出 Aetrix 交流群，按群规设置，你的公益资格已随之取消。\n"
    "重新进群后联系管理员即可恢复；你的账号、能量与查看凭证都不受影响。"
)


def _intercept_channel_mask(db, cfg, msg) -> bool:
    """频道马甲治理：外部频道身份在群里发言 → 删消息＋回说明。

    返回 True 表示已拦截（调用方直接结束分发）。判别在
    policy.mask_sender_chat：匿名管理员（sender_chat=本群）、关联频道
    自动转发、白名单频道、总开关关闭时都放行，落回既有分发（身份命令
    仍走 #1516 匿名说明口径，本函数不碰）。删除失败（bot 非群管理员
    等）只记日志，说明照发但受群级限流约束，防频道刷屏变说明刷屏。
    """
    from backend.tg_bot import sender

    chat = msg.get("chat") or {}
    if not _is_group_chat(chat):
        return False
    chat_id = chat.get("id")
    if not chat_id or not policy.is_group_allowed(cfg, chat_id):
        return False
    sender_chat = policy.mask_sender_chat(msg, chat_id)
    if sender_chat is None:
        return False
    if not policy.is_channel_guard_enabled(cfg):
        return False
    if policy.is_channel_whitelisted(cfg, sender_chat.get("id")):
        return False
    # 删除失败必须留痕（warning）：机器人不是群管理员/缺删消息权时
    # 马甲消息删不掉，此前返回值被忽略、异常只记 debug，线上无从发现；
    # 管理面板另有「群权限自检」可主动核对。
    try:
        _del_ok, _del_err = sender.delete_message(
            db, chat_id, msg.get("message_id"))
        if not _del_ok:
            logger.warning(
                "删除频道马甲消息失败 chat_id=%s: %s", chat_id, _del_err)
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除频道马甲消息失败 chat_id=%s: %s", chat_id, exc)
    logger.info("channel mask message intercepted: chat=%s sender_chat=%s",
                chat_id, sender_chat.get("id"))
    if _group_allow(chat_id, policy.group_rate_limit(cfg)):
        _send_reply(db, chat_id, CHANNEL_MASK_TEXT, autodelete=True)
    return True


def _apply_leave_cancel_welfare(db, tg_id: int, chat_id) -> None:
    """退群取消公益资格：与 welfare_expiry 的 auto_expire 同一口径——
    is_welfare=False + WelfareGrantLog(channel="group_leave", days=0)
    留痕；查看凭证、能量一律不动。原本就无资格的只记日志、不通知
    （避免 poller 重放或重复事件误报）；状态真正变化才私聊通知本人。
    """
    from backend import models
    from backend.tg_bot import sender
    from backend.tg_bot.identity import resolve

    user = resolve(db, int(tg_id))
    if user is None:
        return
    if not user.is_welfare:
        logger.info("group leave: user=%s 无公益资格，跳过 chat=%s",
                    user.id, chat_id)
        return
    user.is_welfare = False
    db.add(models.WelfareGrantLog(
        user_id=user.id, channel="group_leave", days=0, granted_by=None))
    db.commit()
    logger.info("group leave welfare cancelled: user=%s tg=%s chat=%s",
                user.id, tg_id, chat_id)
    try:
        sender.send_message(db, int(tg_id), LEAVE_WELFARE_TEXT)
    except Exception:  # noqa: BLE001
        logger.debug("退群取消资格通知发送失败（忽略）", exc_info=True)


def _handle_chat_member(db, cfg, cm: dict) -> None:
    """chat_member 更新：已绑定用户退群/被踢 → 按 bot_leave_action 处置。

    只处理名单内群、状态真正变为 left/kicked 的变动（old 已是
    left/kicked 的重复推送/重放直接跳过）。处置失败只记日志：
    成员事件不许把分发打崩、更不许给群里发"系统繁忙"。
    注意：Telegram 只在 bot 为群管理员时才稳定推送他人的成员变动，
    非管理员场景可能根本收不到这类 update（真机未验）。
    """
    try:
        chat = cm.get("chat") or {}
        if not _is_group_chat(chat):
            return
        chat_id = chat.get("id")
        if not chat_id or not policy.is_group_allowed(cfg, chat_id):
            return
        new_cm = cm.get("new_chat_member") or {}
        if new_cm.get("status") not in ("left", "kicked"):
            return
        old_status = (cm.get("old_chat_member") or {}).get("status")
        if old_status in ("left", "kicked"):
            return
        tg_user = new_cm.get("user") or {}
        tg_id = tg_user.get("id")
        if not tg_id or tg_user.get("is_bot"):
            return
        if policy.leave_action(cfg) != "cancel_welfare":
            logger.info("group leave not actioned (bot_leave_action=%s): "
                        "chat=%s tg=%s", policy.leave_action(cfg),
                        chat_id, tg_id)
            return
        # A-110：处置前须与对账用同一“在群”口径逐群复核——任一名单群
        # 仍在即跳过；有群接口失败且无群确认在群时 fail-closed 跳过
        # 本次处置（事件重放／下次对账再裁决），绝不在“未核验”时取消。
        # 名单为空时无法枚举他群，沿用事件群本身的判定（既有行为不变）。
        from backend.tg_bot import reconcile, sender

        groups = sorted(policy.parse_group_ids(cfg.get("bot_group_ids", "")))
        if groups:
            status = reconcile._member_status(
                db, groups, int(tg_id), sender.get_chat_member)
            if status is True:
                logger.info(
                    "group leave skipped (still member in another group): "
                    "chat=%s tg=%s", chat_id, tg_id)
                return
            if status is None:
                logger.info(
                    "group leave skipped (member status unverified): "
                    "chat=%s tg=%s", chat_id, tg_id)
                return
        _apply_leave_cancel_welfare(db, int(tg_id), chat_id)
    except Exception:
        logger.exception("chat_member handling failed")


# ---------------------------------------------------------------------------
# widget_query 分发器（Bot API 10.4，PR-2a）：按请求 data 的动作
# 命名空间（JSON 里的 "a" 字段）把 query 分发到对应组件。
#
# 新组件上线时在 _widget_query_routes 里
# 登记自己的动作名，绝不许再把全部 widget_query 直塞给单个组件
# ——否则两个组件会互相误答。未登记/解析不出的动作由分发器统一
# 回 error JSON 应答，绝不静默（不应答的 query 在 widget 侧
# 15 秒后 TIMEOUT）。
# ---------------------------------------------------------------------------


def _widget_query_action(widget_query: dict) -> str | None:
    """取 widget_query 请求 data 的动作命名空间（"a" 字段）。

    data 是 1–64 字节的 JSON 字符串；解析失败、不是对象、或 "a"
    不是非空字符串时返回 None（由分发器按未知动作统一应答）。
    """
    import json

    raw = (widget_query or {}).get("data") or ""
    try:
        req = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(req, dict):
        action = req.get("a")
        if isinstance(action, str) and action:
            return action
    return None


def _widget_query_routes() -> dict:
    """动作命名空间 → 处理函数（调用时懒加载，防循环导入）。"""
    from backend.tg_bot import pool_widget

    return {
        "pool_refresh": pool_widget.handle_widget_query,
    }


def _dispatch_widget_query(db, widget_query: dict) -> None:
    """按动作命名空间分发 widget_query；未知动作统一 error 应答。

    已登记动作 → 对应组件的 handle_widget_query；未登记 →
    分发器直接 answerWidgetQuery 回 {"error": "unknown_action"}
    并记日志，绝不静默。缺 query id 时无从应答，只记日志。
    """
    import json

    from backend.tg_bot import sender

    action = _widget_query_action(widget_query)
    handler = _widget_query_routes().get(action or "")
    if handler is not None:
        handler(db, widget_query)
        return
    qid = (widget_query or {}).get("id")
    if not qid:
        logger.warning(
            "widget_query 动作未知且缺 id，无法应答: %s", widget_query)
        return
    data = json.dumps(
        {"error": "unknown_action", "msg": "未知请求"},
        ensure_ascii=False)
    ok, err = sender.answer_widget_query(db, qid, data)
    if not ok:
        logger.warning(
            "widget_query 未知动作应答失败 qid=%s action=%s err=%s",
            qid, action, err)


def dispatch(db, update: dict) -> None:
    """分发单个 update 到对应的处理器。

    P0：处理器抛异常时不再只记日志——尽力给用户回一句看得见的失败提示
    （2026-10-21 教训：失败只记日志、用户端看着像成功）。
    """
    from backend.tg_bot import handlers, sender

    # 先取 chat_id：异常发生在任何一步时失败提示都有地方发
    if "callback_query" in update:
        _cq = update["callback_query"] or {}
        chat_id = ((_cq.get("message") or {}).get("chat") or {}).get("id")
    else:
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
    # 消息路径内会重新赋值；异常兜底回复据此决定是否挂自毁
    in_group = False

    try:
        # B4：批量读取全部 Bot 策略配置（1 次 DB 查询）
        cfg = policy.read_bot_config(db)

        # 总开关：关闭后完全不响应（poller 继续跑，offset 照常推进）
        if not policy.is_enabled(cfg):
            return

        # chat_member 更新不带 message：退群处置单独处理
        if "chat_member" in update:
            _handle_chat_member(db, cfg, update.get("chat_member") or {})
            return

        # bot 自身的成员变动：poller 已订阅（口径见 poller），不处理
        if "my_chat_member" in update:
            return

        # callback_query 走 B3（群白名单同样适用：在非名单群点的抢红包按钮静默忽略）
        if "callback_query" in update:
            cq = update["callback_query"]
            chat = ((cq.get("message") or {}).get("chat") or {})
            if _is_group_chat(chat) and not policy.is_group_allowed(cfg, chat.get("id")):
                return
            from backend.tg_bot import callbacks
            callbacks.handle_callback(db, cq)
            return

        # 内联搜片（@bot 片名）：没有 chat 上下文，结果走 answerInlineQuery
        if "inline_query" in update:
            from backend.tg_bot import inline_search

            inline_search.handle_inline_query(
                db, update["inline_query"] or {}, cfg)
            return

        # HTML widget 请求（Bot API 10.4）：走分发器按动作命名
        # 空间分发（见 _dispatch_widget_query），未知动作统一 error
        # 应答；shared_widget_query 来自转发的共享 widget，本机器人
        # 不用共享 widget，静默忽略（不处理、不报错）。
        if "widget_query" in update:
            _dispatch_widget_query(db, update["widget_query"] or {})
            return
        if "shared_widget_query" in update:
            return

        msg = update.get("message") or {}
        # 频道马甲治理：频道身份的媒体消息没有 text 也要拦，故在判空前拦截
        if _intercept_channel_mask(db, cfg, msg):
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        tg_user = msg.get("from") or {}
        if not chat_id:
            return
        in_group = _is_group_chat(chat)

        # 群白名单：非名单群聊静默忽略（私聊不受影响）
        if in_group and not policy.is_group_allowed(cfg, chat_id):
            return

        group_limit = policy.group_rate_limit(cfg)

        # 6 位纯数字 → 绑定码验证
        if text.isdigit() and len(text) == 6:
            if in_group and not _group_allow(chat_id, group_limit):
                return
            # 匿名发的绑定码认不出是谁，验了也绑不到本人：回匿名说明
            if _is_anonymous_admin(msg):
                _send_reply(db, chat_id, ANON_ADMIN_TEXT, autodelete=in_group)
                return
            _verify_bind_code(db, update)
            return

        # 群发言计分已随积分模块删除摘除（2026-11-13）：群内非命令
        # 文本不再有任何计分钩子，直接落到下方忽略分支。
        if in_group and not text.startswith("/"):
            return

        # 非命令文本忽略
        if not text.startswith("/"):
            return

        # 解析命令（去掉 @bot 后缀）
        first_token = text.split()[0]
        cmd = first_token.split("@")[0].lower()
        args = text[len(first_token):].strip()

        commands = {
            "/start": handlers.handle_start,
            "/help": handlers.handle_help,
            "/bind": handlers.handle_bind,
            "/points": handlers.handle_points,
            "/account": handlers.handle_account,
            "/me": handlers.handle_account,
            "/request": handlers.handle_request,
            "/myrequests": handlers.handle_my_requests,
            "/rank": handlers.handle_rank,
            "/govlog": handlers.handle_govlog,
            "/level": handlers.handle_account,
            "/report": handlers.handle_report,
            "/propose": handlers.handle_propose,
            "/launch": handlers.handle_direct_launch,
            "/requestboard": handlers.handle_request_board,
            "/rules": handlers.handle_rules,
            "/login": handlers.handle_login,
            "/renew": handlers.handle_login,
            "/redeem": handlers.handle_redeem,
            "/bindings": handlers.handle_bindings,
            "/transfers": handlers.handle_transfers,
            "/whyplay": handlers.handle_whyplay,
            "/wants": handlers.handle_wants,
            "/reconcile": handlers.handle_reconcile,
            "/rankpush": handlers.handle_rankpush,
            "/playrank": handlers.handle_playrank,
            "/kk": handlers.handle_whois,
        }
        fn = commands.get(cmd)
        if fn is None:
            _send_reply(db, chat_id, "未知命令，发送 /help 查看可用命令",
                        autodelete=in_group)
            return

        # 匿名管理员拦截：需要身份的命令统一回匿名说明，不许落进
        # handler 被 resolve→None 误回绑定指引（对方其实早已绑定）。
        # 唯一例外：群内 /redeem 的删消息护码分支在下方先于 handler
        # 执行、给的是私聊提示而非绑定指引，匿名发继续走它不拦。
        if (
            _is_anonymous_admin(msg)
            and cmd in _IDENTITY_COMMANDS
            and not (cmd == "/redeem" and in_group)
        ):
            _send_reply(db, chat_id, ANON_ADMIN_TEXT, autodelete=True)
            return

        # 求片和免密登录都可能泄露隐私/凭据，强制私聊。
        # 账号展示一家（/me、/account 与指向账号卡的 /level）不在本
        # 集合：群里放行（2026-10-28 用户亲定、2026-11-02 重申）——
        # 账号卡是固定 11 行汇总、不带登录链接等凭据，群里与私聊同
        # 形态渲染；登录与求片仍私聊限定，不许借此放宽任何一条。
        # /kk 是刻意例外（2026-11-01）：不在本集合——群里走 handler
        # 的管理员精简卡模式（is_staff 门禁 + 字段白名单，无审计痕迹），
        # 私聊仍出审计全卡；见 handlers.handle_whois 的 in_group 路径。
        private_only = {"/request", "/myrequests", "/login", "/renew", "/govlog",
                        "/report", "/propose", "/launch", "/reconcile", "/rankpush",
                        "/playrank"}
        if cmd in private_only and in_group:
            _reply_private_only(db, chat_id, msg)
            return

        # P1 个人查询在群里不执行（结果会公开给全群看），同统一形态
        if cmd in _P1_COMMANDS and in_group:
            _reply_private_only(db, chat_id, msg)
            return

        # /redeem 在群里发 = 把兑换码公开给全群：拒绝执行，尽量删掉原消息（没权限就算了），再回统一私聊提示
        if cmd == "/redeem" and in_group:
            try:
                sender.delete_message(db, chat_id, msg.get("message_id"))
            except Exception as exc:  # noqa: BLE001
                logger.debug("删除群内 /redeem 消息失败（忽略）: %s", exc)
            if _group_allow(chat_id, group_limit):
                _reply_private_only(db, chat_id, msg, schedule_cmd=False)
            return

        # 群级限流：超限静默丢弃，避免刷屏
        if in_group and not _group_allow(chat_id, group_limit):
            return

        # 用户命令消息 3 秒自毁：命令已识别、已过群白名单与群级限流，
        # 在此统一定时删除（私聊不挂；/redeem 即时删除分支已先行返回，
        # 私聊限定/P1 拦截分支在各自回复处已单独挂过，不重复）
        if in_group:
            _schedule_cmd_autodelete(db, chat_id, msg.get("message_id"))

        # 各命令开关：关闭后统一回复"该功能已关闭"（/start /help 常开）
        if not policy.is_command_enabled(cfg, cmd):
            _send_reply(db, chat_id, "该功能已关闭", autodelete=in_group)
            return

        # 用户级命令限流：间隔可配置
        tg_uid = tg_user.get("id") or 0
        if not _user_cmd_allow(tg_uid, cmd, policy.rate_limit_seconds(cfg)):
            _send_reply(db, chat_id, "操作太快了，稍后再试", autodelete=in_group)
            return

        if cmd == "/kk":
            # /kk 需要群聊上下文（私聊全卡 vs 群内管理员精简卡）
            # 与被回复消息的发送者（群内回复定位），通用签名拿不到，
            # 故单独分发；handler 默认参数保证私聊与
            # 直调行为逐字不变。
            reply_from = (msg.get("reply_to_message") or {}).get("from")
            reply = handlers.handle_whois(
                db, tg_user, chat_id, args,
                in_group=in_group, reply_from=reply_from)
        else:
            reply = fn(db, tg_user, chat_id, args)
        # handler 可返回 str 或 (text, reply_markup) 元组（一键登录按钮）。
        # 静默标记表示重放已经发过，不再刷第二条。
        if isinstance(reply, tuple) and reply and reply[0] == "__silent__":
            return
        if isinstance(reply, tuple) and reply and reply[0] == "__ephemeral__":
            # 临时消息标记（/__kk 群查人卡）：只发给标记里的接收人
            # 本人；发送失败在 _send_ephemeral_reply 内如实回落公开
            # 路径（群自毁口径不变），不许落到下面的普通元组发送。
            _marker, text, markup, receiver_tg_id = reply
            _send_ephemeral_reply(
                db, chat_id, text, markup, receiver_tg_id,
                in_group=in_group)
            return
        if isinstance(reply, tuple):
            text, markup = reply
        else:
            text, markup = reply, None
        if cmd == "/start" and not in_group:
            # 私聊主界面走图卡（caption 超限/发图失败时 send_panel 自动回退文本）
            # 注：router 内 send_panel 仅此私聊一处，群聊没有图卡发送点，
            # 故本期无需为图卡另走 send_photo_ex（sender 未动）。
            sender.send_panel(db, chat_id, text, reply_markup=markup)
        else:
            _send_reply(db, chat_id, text, reply_markup=markup,
                        autodelete=in_group)
    except Exception as exc:  # noqa: BLE001
        logger.error("tg dispatch failed: %s", exc, exc_info=True)
        if chat_id:
            try:
                _send_reply(db, chat_id, "系统繁忙，请稍后再试",
                            autodelete=in_group)
            except Exception:  # noqa: BLE001
                logger.debug("tg failure reply failed", exc_info=True)
