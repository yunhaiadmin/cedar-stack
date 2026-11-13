"""callback_query 处理：主菜单按钮（menu:*）+ 查人面板（whois:*）+ 对账确认（reconcile:*）+ 贡献池 widget（pool_widget）。

玩法类回调（红包/抽奖/老虎机/盲盒/对决/红包雨）已随批 9 断入口下线：
历史消息上的旧按钮点到时无分支命中、静默忽略。
"""
from __future__ import annotations

import logging

from backend import models
from . import ephemeral, layout, sender
from .identity import resolve

logger = logging.getLogger(__name__)

# 群菜单里走按钮回调临时回复的个人结果动作（Bot API 10.4 包1 PR-2）：
# 结果是点击者本人的，只给他看，不编辑进群共享面板。
_EPHEMERAL_RESULT_ACTIONS = ("points", "chatpoints")


def handle_callback(db, callback_query: dict) -> None:
    """处理 callback_query：菜单按钮（menu:*）+ 查人/对账/widget 回调。"""
    data = (callback_query.get("data") or "")
    if data.startswith("menu:"):
        _handle_menu(db, callback_query)
        return
    if data.startswith("whois:"):
        _handle_whois_callback(db, callback_query)
        return
    if data.startswith("reconcile:"):
        _handle_reconcile_callback(db, callback_query)
        return
    if data == "pool_widget":
        _handle_pool_widget(db, callback_query)
        return


def _handle_menu(db, callback_query: dict) -> None:
    """处理主菜单 inline 按钮（menu:*）：原地编辑消息展示对应页面。"""
    from . import handlers

    data = callback_query.get("data") or ""
    action = data.split(":", 1)[1] if ":" in data else ""
    cq_id = callback_query.get("id")
    from_user = callback_query.get("from") or {}
    tg_id = from_user.get("id")
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    if not chat_id or not message_id or not tg_id:
        return
    is_private = (chat_id == tg_id)
    tg_user = {"id": tg_id, "first_name": from_user.get("first_name")}

    # 先消除按钮 loading
    sender.answer_callback_query(db, cq_id)

    try:
        text, markup = _render_menu(db, tg_user, tg_id, chat_id, is_private, action)
    except _UnknownMenuAction:
        return  # 未知菜单：静默忽略
    except Exception:
        logger.exception("tg menu render failed: action=%s", action)
        text, markup = "系统繁忙，请稍后再试", handlers.back_markup()

    if action in _EPHEMERAL_RESULT_ACTIONS and not is_private:
        # 群菜单个人结果临时化（Bot API 10.4 包1 PR-2）：能量是
        # 点击者本人的结果，改走按钮回调临时回复只发给他，不再编辑
        # 进群共享面板。回执已在上方照常 answer（官方要求两者并存，
        # 且临时回复须在回调后 15 秒内发出，本函数同步发送满足）。
        # 临时卡只读不带按钮：导航仍回共享面板点，避免 menu:* 按钮
        # 落在不可编辑的临时消息上。发送失败回落下方现行共享面板
        # 编辑路径，结果绝不丢。
        ok, err, _eph_id = sender.send_ephemeral_message(
            db, chat_id, text, callback_query_id=cq_id)
        if ok:
            return
        logger.debug(
            "tg menu ephemeral result failed, fallback to panel edit: "
            "action=%s err=%s", action, err)

    has_photo = bool(msg.get("photo"))
    ok, err = sender.edit_panel(
        db, chat_id, message_id, text, markup, has_photo=has_photo)
    if not ok:
        logger.debug("tg menu edit failed: action=%s err=%s", action, err)
        # 导航回落：原地编辑被 TG 拒绝（消息不可编辑/内容未变/消息
        # 过旧等）时，删掉旧消息、新发目标面板，保证导航一定落到
        # 用户点的页面。旧行为只记日志就结束，用户被困在原面板上
        # 反复点、面板纹丝不动（2026-10-30 老虎机主界面实证）。
        # 回落发的是目标面板，绝不许重发当前面板。
        sender.delete_message(db, chat_id, message_id)
        sender.send_panel(
            db, chat_id, text, markup,
            prefer_photo=has_photo, photo_path=None)
    elif not is_private:
        # 群面板滑动自毁（PR-2）：编辑成功落地＝一次互动，把自毁
        # 重挂为「最后互动＋bot_msg 延时」，正点着不会准点消失。
        # 编辑失败走上面的删旧发新回落，旧消息已删、无可重挂，不进这里。
        _rearm_group_panel_autodelete(db, chat_id, message_id)


def _handle_whois_callback(db, callback_query: dict) -> None:
    """处理 /whois 管理员面板按钮（whois:<page>:<user_id>[:<op>]）。

    每次点击都重新校验点击者：未绑定或非管理员、目标不存在时只给
    点按者弹 toast 拒绝提示，绝不改写共享面板消息（PA-057：群面板是
    共享消息，路人一点击就改写会毁掉管理员的面板）。渲染与写操作
    全在 whois_panel（写操作二次确认在 ask/do 两步里，do 才执行）。
    """
    from . import whois_panel

    data = callback_query.get("data") or ""
    parts = data.split(":")
    cq_id = callback_query.get("id")
    from_user = callback_query.get("from") or {}
    tg_id = from_user.get("id")
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    # 临时消息面板（/kk 群卡临时化）：message 带 ephemeral_message_id
    # 时走临时分支；两个 id 都没带时查进程内登记兜底定位。普通消息
    # （带 message_id）永远走现行分支，登记不许劫持普通面板的点击。
    eph_id = msg.get("ephemeral_message_id")
    if eph_id is None and not message_id and chat_id and tg_id:
        eph_id = ephemeral.ephemeral_message_for(chat_id, int(tg_id))
    if (not chat_id or not tg_id or len(parts) < 3
            or (not message_id and not eph_id)):
        return
    sender.answer_callback_query(db, cq_id)
    try:
        page = parts[1]
        target_id = int(parts[2])
        op = parts[3] if len(parts) > 3 else None
        # PA-058：赠送 do 回调第五段为一次性 nonce（其余回调无此段）。
        nonce = parts[4] if len(parts) > 4 else None
    except (IndexError, ValueError):
        return
    caller = resolve(db, int(tg_id))
    if caller is None or not bool(getattr(caller, "is_staff", False)):
        # PA-057：只弹 toast，不改写共享面板消息。
        sender.answer_callback_query(db, cq_id, text="仅管理员可用")
        return
    target = (
        db.query(models.WebUser)
        .filter(models.WebUser.id == target_id)
        .first()
    )
    if target is None:
        # PA-057：同款改法，目标不存在也不改写共享面板消息。
        sender.answer_callback_query(db, cq_id, text="目标账号不存在")
        return
    try:
        text, markup = whois_panel.render(db, caller, target, page, op,
                                          nonce=nonce)
    except Exception:
        logger.warning("tg whois panel render failed: %s", data, exc_info=True)
        return
    if eph_id is not None:
        # 临时消息面板：官方规定来自临时消息的回调必须走
        # editEphemeralMessageText 改内容（replace 语义禁用）；编辑
        # 成功按临时消息口径续期（deleteEphemeralMessage 定时）。
        # 接收人就是面板接收人本人——只有他看得见、点得到这块面板。
        ok, _err = sender.edit_ephemeral_message_text(
            db, chat_id, int(tg_id), int(eph_id), text, markup)
        if ok:
            _rearm_ephemeral_panel_autodelete(
                db, chat_id, int(tg_id), int(eph_id))
        return
    ok, _err = sender.edit_message_text(db, chat_id, message_id, text, markup)
    if ok and chat_id != int(tg_id):
        # 群面板续命：与 menu 面板同一口径，互动后重挂自毁定时。
        _rearm_group_panel_autodelete(db, chat_id, message_id)


def _rearm_group_panel_autodelete(db, chat_id: int, message_id: int) -> None:
    """群面板互动续命接线：按当前配置重挂该面板消息的自毁定时器。

    只由 _handle_menu 在群聊且面板编辑成功后调用；私聊不自毁。
    旁路纪律：重挂失败只记 debug 日志，绝不影响面板本身。
    """
    try:
        ephemeral.rearm_delete(db, chat_id, message_id)
    except Exception:
        logger.debug(
            "tg panel autodelete rearm failed: chat=%s message=%s",
            chat_id, message_id, exc_info=True)


def _rearm_ephemeral_panel_autodelete(db, chat_id: int, receiver_user_id: int,
                                      ephemeral_message_id: int) -> None:
    """临时消息面板互动续命接线：与 _rearm_group_panel_autodelete 同纪律。

    只由 _handle_whois_callback 在临时面板编辑成功后调用；旁路纪律
    相同：重挂失败只记 debug 日志，绝不影响面板本身。
    """
    try:
        ephemeral.rearm_ephemeral_delete(
            db, chat_id, receiver_user_id, ephemeral_message_id)
    except Exception:
        logger.debug(
            "tg ephemeral panel autodelete rearm failed: chat=%s "
            "receiver=%s eph=%s",
            chat_id, receiver_user_id, ephemeral_message_id, exc_info=True)


def _render_menu(db, tg_user: dict, tg_id: int, chat_id: int, is_private: bool,
                 action: str) -> tuple[str, dict | None]:
    """渲染菜单页面，返回 (text, markup)。未知 action 返回 None 标记。"""
    from . import handlers

    text: str
    markup: dict | None
    if action in ("main", "home", "panel"):
        text, markup = handlers.menu_main(db, tg_user, is_private)
    elif action == "user":
        user = resolve(db, int(tg_id))
        if user is None:
            text, markup = handlers._bind_guide_text(), handlers.back_markup()
        elif not is_private:
            text = layout.card(
                layout.title("用户面板"),
                [layout.row("🔒", "使用方式", "这个功能请私聊机器人使用")])
            markup = handlers.back_markup()
        else:
            from . import features

            text = features.user_panel(db, user, display_name=tg_user.get("first_name"))
            markup = handlers.user_panel_markup()
    elif action.startswith("group_"):
        # 用户面板二级分组：只是翻页，不查身份、不设门禁；
        # 组内功能按钮各自的私聊/绑定守卫在它们自己的渲染路径上。
        group_key = action[len("group_"):]
        if group_key not in handlers.USER_PANEL_GROUPS:
            raise _UnknownMenuAction(action)
        text = handlers.user_panel_group_text(group_key)
        markup = handlers.user_panel_group_markup(group_key)
    elif action == "server":
        text = handlers.handle_server(db, tg_user, chat_id, "")
        markup = handlers.back_markup()
    elif action in ("admin", "admin_rankpush"):
        # admin_rankpush 是已下线的推榜子页入口（2026-11-10 砍子面板）：
        # 历史消息上的旧按钮点到时直接落回管理面板（推今日榜/推本周榜
        # 两个动作已并到管理面板上），不报错、不空白。
        text = handlers.handle_admin(db, tg_user, chat_id, "")
        admin_user = resolve(db, int(tg_id))
        if admin_user is not None and bool(getattr(admin_user, "is_staff", False)):
            markup = handlers.admin_markup(admin_user)
        else:
            markup = handlers.back_markup()
    elif action in ("admin_whois", "admin_reconcile",
                    "admin_rankpush_day", "admin_rankpush_week",
                    "admin_botperms"):
        # 管理面板子页：每页重新验 is_staff（面板按钮裁剪不是安全边界）；
        # 写动作的角色门在各 handler/执行层（viewer 旧按钮点到也会被拒）。
        admin_user = resolve(db, int(tg_id))
        if admin_user is None:
            text = handlers._bind_guide_text()
            markup = handlers.back_markup()
        elif not bool(getattr(admin_user, "is_staff", False)):
            text = layout.card(
                layout.title("管理面板"),
                [layout.row("🔒", "访问状态", "仅管理员可用")])
            markup = handlers.back_markup()
        elif action == "admin_whois":
            text = layout.card(layout.title("查人"), [
                layout.row("🔍", "私聊用法",
                           "/kk 站内名 / TG 号 / IP / 设备，定位到人出管理面板"),
                layout.row("👥", "群里用法",
                           "回复某人的消息发送 /kk，出精简卡（自动删除）"),
                layout.row("🎛️", "面板动作",
                           "查到人后可封禁/解封、赠送能量/公益/查看权；"
                           "只读审计角色仅查看"),
            ])
            markup = handlers.admin_back_markup()
        elif action == "admin_reconcile":
            # 等价于私聊 /reconcile：复用其报告卡与原有按钮 markup。
            reply = handlers.handle_reconcile(db, tg_user, chat_id, "")
            if isinstance(reply, tuple):
                text, markup = reply
                if markup is None:
                    markup = handlers.admin_back_markup()
            else:
                text, markup = reply, handlers.admin_back_markup()
        elif action in ("admin_rankpush_day", "admin_rankpush_week"):
            text = handlers.handle_rankpush(
                db, tg_user, chat_id,
                "week" if action == "admin_rankpush_week" else "")
            markup = handlers.admin_back_markup()
        else:  # admin_botperms
            from . import bot_perms

            text = bot_perms.render_card(db)
            markup = handlers.admin_back_markup()
    elif action == "renewcode":
        text = handlers.handle_redeem(db, tg_user, chat_id, "")
        markup = handlers.back_markup()
    elif action == "points":
        text = handlers.handle_points(db, tg_user, chat_id, "")
        markup = handlers.back_markup()
    elif action == "chatpoints":
        # 发言能量已并入 /points 输出：按钮入口一律指向合并后面板。
        text = handlers.handle_points(db, tg_user, chat_id, "")
        markup = handlers.back_markup()
    elif action in ("account", "request", "myrequests", "rank", "requestboard", "rules", "login", "govlog", "level", "report", "propose", "launch"):
        # 凭据类与求片类仍私聊限定；账号展示（account/level）群里放行
        # （2026-10-28 用户亲定、2026-11-02 重申）：落下面正常渲染分支
        # 出点击者本人的账号卡，与命令层 /me 口径一致。
        if not is_private and action in ("request", "myrequests", "login", "govlog", "report", "propose", "launch"):
            text = layout.card(
                layout.title("私聊使用"),
                [layout.row("🔒", "使用方式", "这个功能请私聊机器人使用")])
            markup = handlers.back_markup()
        else:
            fn = {
                "account": handlers.handle_account,
                "request": handlers.handle_request,
                "myrequests": handlers.handle_my_requests,
                "rank": handlers.handle_rank,
                "requestboard": handlers.handle_request_board,
                "rules": handlers.handle_rules,
                "login": handlers.handle_login,
                "govlog": handlers.handle_govlog,
                "level": handlers.handle_account,
                "report": handlers.handle_report,
                "propose": handlers.handle_propose,
                "launch": handlers.handle_direct_launch,
            }[action]
            reply = fn(db, tg_user, chat_id, "")
            if isinstance(reply, tuple):
                text, markup = reply
            else:
                text, markup = reply, handlers.back_markup()
    elif action in ("bindings", "transfers", "whyplay", "wants"):
        # 个人查询私聊限定（与命令层口径一致）：群里点到只回私聊卡，
        # 绝不把点击者本人的绑定/转存/想看数据编辑进群共享消息。
        if not is_private:
            text = handlers.private_only_card()
            markup = handlers.private_only_markup(db)
        else:
            fn = {
                "bindings": handlers.handle_bindings,
                "transfers": handlers.handle_transfers,
                "whyplay": handlers.handle_whyplay,
                "wants": handlers.handle_wants,
            }[action]
            text = fn(db, tg_user, chat_id, "")
            markup = handlers.back_markup()
    elif action == "achievements":
        # 成就是个人数据卡：私聊限定（与 bindings 等私人查询同口径），
        # 群里点到只回私聊提示卡，绝不把点击者本人的成就记录编辑进
        # 群共享消息。未绑定按现行口径回绑定指引。
        if not is_private:
            text = handlers.private_only_card()
            markup = handlers.private_only_markup(db)
        else:
            user = resolve(db, int(tg_id))
            if user is None:
                text, markup = handlers._bind_guide_text(), handlers.back_markup()
            else:
                from . import features

                text = features.achievements_card(db, user)
                markup = handlers.group_back_markup("account", "账号组")
    elif action == "bind":
        user = resolve(db, int(tg_id))
        if user is not None:
            text = layout.card(
                layout.title("账号绑定"),
                [layout.row("✅", "绑定状态", "你的账号已绑定，无需重复绑定；如需更换绑定请联系客服解绑")])
        else:
            text = handlers._bind_guide_text()
        markup = handlers.back_markup()
    elif action == "help":
        text = handlers.handle_help(db, tg_user, chat_id, "")
        markup = handlers.back_markup()
    else:
        raise _UnknownMenuAction(action)
    return text, markup


class _UnknownMenuAction(Exception):
    """未知菜单 action：调用方静默忽略。"""
def _handle_reconcile_callback(db, callback_query: dict) -> None:
    """对账处置二次确认（reconcile:ask / reconcile:do / reconcile:abort）。

    每一步都重新验门：仅私聊点击 + 已绑定 + is_staff（现行口径），
    任一不满足只回拒绝卡、绝不动数据。ask 只渲染确认卡（零写入）；
    do 才执行取消资格（reconcile.execute_cancel_welfare，内部对
    每个目标再逐一复核在群状态）；abort 只回放弃卡。
    """
    from . import handlers, reconcile

    data = callback_query.get("data") or ""
    step = data.split(":", 1)[1] if ":" in data else ""
    if step not in ("ask", "do", "abort"):
        return
    cq_id = callback_query.get("id")
    from_user = callback_query.get("from") or {}
    tg_id = from_user.get("id")
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    if not chat_id or not message_id or not tg_id:
        return
    sender.answer_callback_query(db, cq_id)
    if chat_id != int(tg_id):
        return  # 只认私聊里的点击：对账与处置都是私聊管理员动作
    user = resolve(db, int(tg_id))
    if user is None or not bool(getattr(user, "is_staff", False)):
        text = layout.card(
            layout.title("群号一致性对账"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
        sender.edit_panel(db, chat_id, message_id, text,
                          handlers.back_markup(),
                          has_photo=bool(msg.get("photo")))
        return
    if step == "ask":
        text, markup = reconcile.render_confirm(db, int(tg_id))
    elif step == "abort":
        text, markup = reconcile.render_abort()
    else:
        result = reconcile.cached_result(int(tg_id))
        if result is None:
            result = reconcile.run_reconcile(db)
            reconcile.cache_result(int(tg_id), result)
        done, skipped = reconcile.execute_cancel_welfare(db, user, result)
        text, markup = reconcile.render_receipt(done, skipped)
    sender.edit_panel(db, chat_id, message_id, text, markup,
                      has_photo=bool(msg.get("photo")))

def _handle_pool_widget(db, callback_query: dict) -> None:
    """发贡献池私聊面板 widget（Bot API 10.4 sendWidget，私聊限定）。

    生产环境 widget 仅限私聊：群里点到（旧面板残留等）只回 toast、不发。
    未绑定账号 toast；不是池成员 toast（面板无内容可看）。
    """
    from backend.tg_bot import pool_widget as pool_widget_mod

    cb_id = callback_query.get("id")
    from_user = callback_query.get("from") or {}
    tg_id = from_user.get("id")
    msg = callback_query.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not cb_id or not tg_id or not chat_id:
        return

    def _answer(text: str, show_alert: bool = False) -> None:
        sender.answer_callback_query(db, cb_id, text=text, show_alert=show_alert)

    if int(chat_id) != int(tg_id):
        _answer("我的贡献池仅支持私聊，群里看不了", show_alert=True)
        return
    user = resolve(db, int(tg_id))
    if user is None:
        _answer("请先绑定账号后再查看贡献池", show_alert=True)
        return
    ok, err, _mid = pool_widget_mod.send_pool_widget(db, chat_id, user)
    if not ok:
        _answer(err or "我的贡献池发送失败", show_alert=True)
        return
    _answer("我的贡献池已发出")

