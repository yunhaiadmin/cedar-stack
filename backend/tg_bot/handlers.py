from __future__ import annotations

import html
import logging
import threading
import time
from collections import deque
from datetime import datetime

from backend.integrations import store
from backend.models import SubscriptionPlan, TgBindCode, UserSubscription, WebUser
from backend.tg_bot import layout, login_token, role_gate
from backend.tg_bot.identity import resolve

logger = logging.getLogger(__name__)


def _cmd(code: str) -> str:
    """命令 chip：<code> 包裹后 Telegram 客户端可一键点击发送。"""
    return f"<code>{code}</code>"


def _bind_guide_text() -> str:
    return layout.card(
        layout.title("如何绑定账号"),
        [
            layout.row("1️⃣", "第一步", "登录网站 Dashboard"),
            layout.row("2️⃣", "第二步", "点击「<b>绑定 Telegram</b>」按钮"),
            layout.row("3️⃣", "第三步", "复制弹窗中显示的 <b>6 位绑定码</b>"),
            layout.row("4️⃣", "第四步", "回到这里发送：/bind 绑定码"),
            layout.row("🔔", "绑定后可收到", "求片进度通知"),
            layout.row("⏰", "绑定后可收到", "订阅到期提醒"),
            layout.row("📣", "绑定后可收到", "系统公告推送"),
        ],
    )


def _bind_success_text(db, user) -> str:
    username = html.escape(str(user.username or ""))
    now = datetime.now()
    sub = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user.id,
            UserSubscription.status == "active",
            UserSubscription.end_date > now,
        )
        .order_by(UserSubscription.end_date.desc())
        .first()
    )
    plan_name = "无"
    if sub:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        if plan:
            plan_name = html.escape(str(plan.name))
    has_sub = sub is not None
    return layout.card(
        layout.title("绑定成功！"),
        [
            layout.row("👤", "账号名称", username),
            layout.row("📦", "订阅服务", plan_name),
            layout.row("📊", "订阅状态", "有有效订阅" if has_sub else "无有效订阅"),
            layout.row("🔔", "您将收到", "求片进度通知、订阅到期提醒、系统公告推送"),
        ],
    )


# 绑定码只有 6 位数字：每个 Telegram 账号 10 分钟内最多尝试这么多次，防止枚举他人绑定码
# （绑定成功即可用 /start 一键免密登录该网页账号，等同账号接管）
BIND_ATTEMPTS_MAX = 5
BIND_ATTEMPTS_WINDOW = 600
# 猜错计数（A-111 起改口径）：旧口径把每次猜错记到全部待验证绑定码头上，
# 累计达上限即把所有人的码一并作废——任意几个 TG 账号互相串通猜错即可
# 作废全站待绑码（可用性 DoS）。现口径分两路，猜错只影响猜错者本人：
# 1）可归属到某个码行的猜错（提交的字符串命中一个已存在但当前不可绑定的
#    码行）：只给该行 fail_count+1，累计达 BIND_GLOBAL_FAIL_MAX 只作废该行；
# 2）无归属的猜错只记入全站失败信号窗：窗内全站猜错达 BIND_GLOBAL_FAIL_MAX
#    次，只对触发越线的那个 TG 账号加严锁定一个窗口期，绝不触碰他人绑定码。
BIND_GLOBAL_FAIL_MAX = 20

_bind_fail_lock = threading.Lock()
_bind_fail_events: deque = deque()
_bind_lockout_until: dict = {}


def _reset_bind_failure_state() -> None:
    """清空全站失败信号窗与账号锁定（测试隔离用）。"""
    with _bind_fail_lock:
        _bind_fail_events.clear()
        _bind_lockout_until.clear()


def _bind_lockout_active(tg_user_id: int) -> bool:
    """该 TG 账号是否处于全站猜错信号触发的加严锁定期。"""
    now = time.monotonic()
    with _bind_fail_lock:
        until = _bind_lockout_until.get(tg_user_id)
        if until is None:
            return False
        if until <= now:
            _bind_lockout_until.pop(tg_user_id, None)
            return False
        return True


def _record_bind_failure(db, now, tg_user_id: int, code: str) -> None:
    """记一次绑定码猜错：归属计数只动命中的码行，全站计数只作速率信号。"""
    # 1）归属计数：提交的字符串命中某个已存在码行时，只累计该行；
    #    待验证可绑定的码在 verify 主流程里已被命中返回，不会走到这里，
    #    故这里命中的只会是当前不可绑定的行（已用/已绑/已过期/Bot 发起）。
    try:
        row = (
            db.query(TgBindCode)
            .filter(TgBindCode.code == (code or "").strip())
            .order_by(TgBindCode.id.desc())
            .first()
        )
        if row is not None:
            row.fail_count = (row.fail_count or 0) + 1
            if (
                row.fail_count >= BIND_GLOBAL_FAIL_MAX
                and row.used_at is None
                and row.expires_at is not None
                and row.expires_at > now
            ):
                row.expires_at = now
                logger.warning("tg 绑定码自身猜错次数超限，已作废该码 id=%s", row.id)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("记录绑定码猜错次数失败：%s", exc)
    # 2）全站信号：窗内猜错达阈值，只锁定本次越线的猜错账号
    ts = time.monotonic()
    with _bind_fail_lock:
        _bind_fail_events.append(ts)
        cutoff = ts - BIND_ATTEMPTS_WINDOW
        while _bind_fail_events and _bind_fail_events[0] < cutoff:
            _bind_fail_events.popleft()
        tripped = len(_bind_fail_events) >= BIND_GLOBAL_FAIL_MAX
        if tripped:
            _bind_lockout_until[tg_user_id] = ts + BIND_ATTEMPTS_WINDOW
    if tripped:
        logger.warning(
            "tg 绑定码全站猜错信号越线，已临时锁定猜错账号 tg_user_id=%s", tg_user_id)


def verify_bind_code(db, tg_user_id: int, chat_id: int, code: str) -> str | None:
    from backend.ratelimit import check_rate_limit

    if _bind_lockout_active(tg_user_id):
        logger.warning("tg 绑定码猜错信号锁定中，已拒绝 tg_user_id=%s", tg_user_id)
        return "绑定码尝试过于频繁，请 10 分钟后再试"
    allowed, _retry = check_rate_limit(
        f"tg_bind_attempt:{tg_user_id}", BIND_ATTEMPTS_MAX, BIND_ATTEMPTS_WINDOW)
    if not allowed:
        logger.warning("tg 绑定码尝试过于频繁，已拒绝 tg_user_id=%s", tg_user_id)
        return "绑定码尝试过于频繁，请 10 分钟后再试"
    now = datetime.now()
    record = (
        db.query(TgBindCode)
        .filter(
            TgBindCode.code == code.strip(),
            TgBindCode.user_id.isnot(None),
            TgBindCode.telegram_id.is_(None),
            TgBindCode.used_at.is_(None),
            TgBindCode.expires_at > now,
        )
        .order_by(TgBindCode.id.desc())
        .first()
    )
    if not record:
        _record_bind_failure(db, now, tg_user_id, code)
        return None
    user = db.query(WebUser).filter(WebUser.id == record.user_id).first()
    if not user:
        return None
    other = (
        db.query(WebUser)
        .filter(WebUser.telegram_id == tg_user_id, WebUser.id != user.id)
        .first()
    )
    if other:
        return "该 Telegram 账号已被其他用户绑定"
    user.telegram_id = tg_user_id
    record.telegram_id = tg_user_id
    record.used_at = now
    db.commit()
    # 管理员绑定成功当场补发对应角色的管理员命令菜单（命令 scope 原本
    # 只在 poller 启动时下发一次，不补发就得等重启才出现管理员菜单）。
    # 失败只记日志，不影响绑定成功回复。
    if (bool(getattr(user, "is_staff", False))
            and bool(getattr(user, "is_active", True))):
        try:
            from backend.tg_bot import command_scopes, poller

            command_scopes.deliver_staff_scope(
                db, int(tg_user_id), user, poller.BOT_COMMANDS)
        except Exception:  # noqa: BLE001
            logger.warning(
                "tg 绑定后补发管理员菜单失败 tg_user_id=%s",
                tg_user_id, exc_info=True)
    return _bind_success_text(db, user)


def _linked_name(tg_user: dict, fallback: str) -> str:
    """问候行用户名链接：有 Telegram id 时链到该用户，否则纯文本。"""
    name = html.escape(str(tg_user.get("first_name") or fallback or "朋友"))
    tg_id = tg_user.get("id")
    if tg_id:
        return f'<a href="tg://user?id={int(tg_id)}">{name}</a>'
    return name


def main_card_text(tg_user: dict, web_user) -> str:
    """主界面卡片正文（已批准样稿）：标语＋带用户名问候。"""
    fallback = getattr(web_user, "username", "") if web_user is not None else ""
    return (
        "✨ 想看的片，喊一声就来\n"
        f"🍉 你好呀，{_linked_name(tg_user, fallback)} 请选择功能 👇"
    )


def unbound_card_text(tg_user: dict, site_name: str) -> str:
    """未绑定主界面：同主卡骨架，信息行换成绑定状态与步骤。"""
    return layout.card(
        "✨ 想看的片，喊一声就来",
        [
            layout.row("🍉", "你好呀", f"{_linked_name(tg_user, '朋友')} 请选择功能 👇"),
            layout.row("🏠", "站点名称", site_name),
            layout.row("🔗", "账号绑定", "未绑定"),
            layout.row("1️⃣", "第一步", "在网页端登录 → <b>个人中心</b> → <b>绑定 Telegram</b> 获取 6 位绑定码"),
            layout.row("2️⃣", "第二步", "把绑定码发给我即可完成绑定"),
            layout.row("📋", "全部命令", "点左下角的 / 菜单查看"),
        ],
    )


def main_markup(db, web_user, is_private: bool) -> dict:
    """新主界面按钮：分类入口在前、重要动作整行、管理员入口仅 staff。

    私聊登录 URL 是既有硬契约：生成成功时仍必须位于 [0][0]。
    """
    if web_user is None:
        if not is_private:
            return {"inline_keyboard": [
                [{"text": "🔗 绑定账号", "callback_data": "menu:bind"}],
                [{"text": "❓ 帮助", "callback_data": "menu:help"}],
            ]}
        return {"inline_keyboard": [
            [{"text": "🔗 绑定账号", "callback_data": "menu:bind"}],
            [{"text": "❓ 帮助", "callback_data": "menu:help"}],
        ]}
    if not is_private:
        return _group_menu_markup(db)
    rows: list[list[dict]] = []
    url = login_token.build_login_url(db, web_user)
    if url:
        rows.append([{"text": "🚀 一键免密进入控制面板", "url": url}])
    rows.append([
        {"text": "👥 用户功能", "callback_data": "menu:user"},
        {"text": "🌐 服务器", "callback_data": "menu:server"},
    ])
    rows.append([{"text": "🎟️ 使用兑换码", "callback_data": "menu:renewcode"}])
    if bool(getattr(web_user, "is_staff", False)):
        rows.append([{"text": "👮 管理", "callback_data": "menu:admin"}])
    return {"inline_keyboard": rows}


def _group_menu_markup(db) -> dict:
    """群聊主菜单。个人查询不进群菜单。"""
    rows: list[list[dict]] = [
        [
            {"text": "✅ 签到", "callback_data": "menu:checkin"},
            {"text": "✨ 能量", "callback_data": "menu:points"},
        ],
        [
            {"text": "🏆 能量榜", "callback_data": "menu:rank"},
        ],
    ]
    rows.extend([
        [
            {"text": "🔥 求片榜", "callback_data": "menu:requestboard"},
            {"text": "📜 群规", "callback_data": "menu:rules"},
        ],
        [
            {"text": "👤 账号中心", "callback_data": "menu:account"},
            {"text": "❓ 帮助", "callback_data": "menu:help"},
        ],
    ])
    return {"inline_keyboard": rows}


def private_only_card() -> str:
    """私聊命令在群里被调用时的统一提示卡（命令层与按钮层同一张）。"""
    return layout.card(
        layout.title("私聊使用"),
        [layout.row("🔒", "使用方式", "这是一条私聊命令，请私聊机器人使用")])


def private_only_markup(db) -> dict:
    """私聊提示卡按钮：「💬 点我去私聊」深链（可解析时）＋回主界面。

    深链用户名走既有来源 tg_bind.get_bot_username（缓存配置
    telegram_bot_username，缺失时 getMe 回填）；解析不到就不挂
    URL 按钮，绝不写死或猜测用户名。
    """
    rows: list[list[dict]] = []
    username = ""
    try:
        from backend.tg_bind import get_bot_username

        username = str(get_bot_username(db) or "").lstrip("@")
    except Exception:
        username = ""
    if username:
        rows.append([{"text": "💬 点我去私聊", "url": f"https://t.me/{username}"}])
    rows.append(layout.home_row())
    return {"inline_keyboard": rows}


# 用户面板分组（2026-10-30 用户亲批「按钮分一下，不要全铺开」）：
# 一级只放签到直达＋四个分组入口＋整行主界面；原功能按钮按组收进二级
# 分组面板。各功能按钮的 callback 与既有门禁一律不变，只新增分组翻页
# callback（menu:group_*，不查身份、不设门禁）与返回（复用 menu:user）。
USER_PANEL_GROUPS: dict[str, dict] = {
    "media": {
        "label": "🎬 求片",
        "buttons": [
            {"text": "🎬 求片", "callback_data": "menu:request"},
            {"text": "📋 我的求片", "callback_data": "menu:myrequests"},
            {"text": "⭐ 我的想看", "callback_data": "menu:wants"},
            {"text": "🔥 求片榜", "callback_data": "menu:requestboard"},
        ],
    },
    "drive": {
        "label": "📦 网盘转存",
        "buttons": [
            {"text": "🔗 绑定网盘", "callback_data": "menu:bindings"},
            {"text": "📦 我的转存", "callback_data": "menu:transfers"},
            {"text": "🤝 我的贡献池", "callback_data": "pool_widget"},
        ],
    },
    "account": {
        "label": "🎟️ 账号权益",
        "buttons": [
            {"text": "✨ 能量", "callback_data": "menu:points"},
            {"text": "🏆 能量榜", "callback_data": "menu:rank"},
            {"text": "🎟️ 续期", "callback_data": "menu:login"},
            {"text": "🔗 绑定账号", "callback_data": "menu:bind"},
            {"text": "🏅 成就", "callback_data": "menu:achievements"},
        ],
    },
    "help": {
        "label": "❓ 帮助中心",
        "buttons": [
            {"text": "❓ 为什么播不了", "callback_data": "menu:whyplay"},
            {"text": "❓ 帮助", "callback_data": "menu:help"},
        ],
    },
}


def _two_col(buttons: list[dict]) -> list[list[dict]]:
    """按钮按两列网格排布，落单的最后一个独占一行。"""
    return [list(buttons[i:i + 2]) for i in range(0, len(buttons), 2)]


def user_panel_markup() -> dict:
    """用户面板一级按钮：签到直达＋四个分组入口两列排，末行回主界面。"""
    rows: list[list[dict]] = [
        [
            {"text": "✅ 签到", "callback_data": "menu:checkin"},
            {"text": USER_PANEL_GROUPS["media"]["label"],
             "callback_data": "menu:group_media"},
        ],
        [
            {"text": USER_PANEL_GROUPS["drive"]["label"],
             "callback_data": "menu:group_drive"},
            {"text": USER_PANEL_GROUPS["account"]["label"],
             "callback_data": "menu:group_account"},
        ],
        [
            {"text": USER_PANEL_GROUPS["help"]["label"],
             "callback_data": "menu:group_help"},
        ],
        layout.home_row(),
    ]
    return {"inline_keyboard": rows}


def user_panel_group_text(group_key: str) -> str:
    """二级分组面板正文：只有标题行，不重复用户面板的信息行。"""
    return layout.card(layout.title(USER_PANEL_GROUPS[group_key]["label"]))


def user_panel_group_markup(group_key: str) -> dict:
    """二级分组面板按钮：该组功能按钮（两列）＋整行返回用户面板。"""
    rows = _two_col(USER_PANEL_GROUPS[group_key]["buttons"])
    rows.append([{"text": "↩ 返回用户面板", "callback_data": "menu:user"}])
    return {"inline_keyboard": rows}


def _menu_markup(db, web_user, is_private: bool) -> dict:
    """兼容旧调用：私聊返回新主界面网格，群聊返回群聊网格。"""
    return main_markup(db, web_user, is_private)


def back_markup() -> dict:
    """菜单子页统一返回入口：整行主界面按钮。"""
    return layout.back_markup()


def group_back_markup(group_key: str, label: str) -> dict:
    """分组功能卡返回所属分组面板：整行按钮（menu:group_*）。"""
    return {"inline_keyboard": [[{
        "text": f"↩ 返回{label}",
        "callback_data": f"menu:group_{group_key}",
    }]]}


def menu_main(db, tg_user: dict, is_private: bool) -> tuple[str, dict]:
    """主界面卡片（文本, 按钮），供 /start 与 menu:main 回调复用。"""
    web_user = resolve(db, int(tg_user.get("id") or 0))
    if web_user is None:
        site_name = html.escape(str(store.get_value(db, "site_name", "Aetrix")))
        return unbound_card_text(tg_user, site_name), main_markup(db, None, is_private)
    return main_card_text(tg_user, web_user), main_markup(db, web_user, is_private)


def handle_server(db, tg_user: dict, chat_id: int, args: str) -> str:
    """服务器面板：只读复用管理端 bot_status，不新增状态口径。"""
    site_name = html.escape(str(store.get_value(db, "site_name", "Aetrix")))
    rows = [layout.row("🏠", "站点名称", site_name)]
    try:
        from backend.api.tg_bot_admin import bot_status

        status = bot_status(db)
        rows += [
            layout.row("🤖", "机器人", "运行中" if status.get("poller_active") else "未运行"),
            layout.row("👥", "已绑定用户", status.get("bound_users", 0)),
            layout.row("💓", "最近心跳", status.get("last_heartbeat") or "暂无"),
        ]
        if status.get("last_error"):
            rows.append(layout.row("⚠️", "最近错误", html.escape(str(status["last_error"]))[:300]))
    except Exception:
        rows.append(layout.row("🤖", "机器人", "状态暂不可用"))
    return layout.card(layout.title("服务器"), rows)


def handle_admin(db, tg_user: dict, chat_id: int, args: str) -> str:
    """管理面板：仅网站 staff 可见，入口只挂既有只读/治理动作。"""
    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None:
        return _bind_guide_text()
    if not bool(getattr(user, "is_staff", False)):
        return layout.card(
            layout.title("管理面板"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    from backend import admin_roles

    return layout.card(
        layout.title("管理面板！", user.username),
        [
            layout.row("👤", "管理员账号", html.escape(str(user.username or ""))),
            layout.row("🛡️", "权限",
                       admin_roles.role_label(admin_roles.role_of(user))),
            layout.row("🌐", "服务器", "可从下方按钮查看机器人状态"),
            layout.row("📋", "治理审计", "私聊发送 /govlog（仅 Lv.6 且公益服）"),
        ],
    )


def handle_rankpush(db, tg_user: dict, chat_id: int, args: str) -> str:
    """管理员手动推送观影榜海报到交流群（/rankpush [week]），供验收。

    身份门照 /admin 口径：TG 绑定的站账号且 is_staff；未绑定或非管理员
    一律回同一张拒绝卡、不推送、不生成海报。默认推今日榜，args=week 推周榜。
    实际推送走 tg_bot.rankpush.push_rank_board（与定时推送同一条路，
    含总开关/空榜/未配群判定与发分幂等）。
    """
    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None or not bool(getattr(user, "is_staff", False)):
        return layout.card(
            layout.title("观影榜推送"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    # 角色门：推榜含榜单奖励发放，是写操作，只读审计角色拒绝。
    refusal = role_gate.readonly_refusal(user)
    if refusal:
        return layout.card(
            layout.title("观影榜推送", user.username),
            [layout.row("🔒", "访问状态", refusal)],
        )
    from backend.tg_bot import rankpush

    period = "week" if str(args or "").strip().lower() == "week" else "day"
    label = "本周观影榜" if period == "week" else "今日观影榜"
    res = rankpush.push_rank_board(db, period)
    reason = res.get("reason")
    if res.get("pushed"):
        rows = [
            layout.row("✅", "推送", f"{label}已推送到交流群"),
            layout.row("👥", "上榜", f"{len(res.get('rows') or [])} 人"),
        ]
        if res.get("rewards"):
            total = sum(g["amount"] for g in res["rewards"])
            rows.append(layout.row("🎁", "榜单奖励", f"已发放 {total} 能量"))
        return layout.card(layout.title("观影榜推送", user.username), rows)
    hint = {
        "disabled": "海报推送已关闭（bot_rankpush_enabled），先在管理后台打开",
        "empty": f"{label}暂时没人观影，榜是空的，没有推送",
        "no_group": "还没配置交流群（bot_group_ids），无法推送",
    }.get(reason or "", "推送失败，请看服务日志")
    return layout.card(
        layout.title("观影榜推送", user.username),
        [layout.row("⚠️", "未推送", hint)],
    )


def handle_playrank(db, tg_user: dict, chat_id: int, args: str) -> str:
    """管理员手动推送每日播放排行榜到交流群（/playrank），供验收。

    身份门照 /rankpush 口径：TG 绑定的站账号且 is_staff；未绑定或
    非管理员一律回同一张拒绝卡、不推送。推榜是写操作，只读审计
    角色拒绝。实际推送走 tg_bot.playrank.push_daily_rank（与定时
    推送同一条路，含总开关/空榜/未配群判定）。
    """
    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None or not bool(getattr(user, "is_staff", False)):
        return layout.card(
            layout.title("每日播放排行榜推送"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    refusal = role_gate.readonly_refusal(user)
    if refusal:
        return layout.card(
            layout.title("每日播放排行榜推送", user.username),
            [layout.row("🔒", "访问状态", refusal)],
        )
    from backend.tg_bot import playrank

    res = playrank.push_daily_rank(db)
    if res.get("pushed"):
        return layout.card(
            layout.title("每日播放排行榜推送", user.username),
            [
                layout.row("✅", "推送", "今日播放排行榜已推送到交流群"),
                layout.row("👥", "观看时长榜", f"{len(res.get('users') or [])} 人"),
                layout.row("📺", "剧集榜", f"{len(res.get('series') or [])} 部"),
                layout.row("🎬", "电影榜", f"{len(res.get('movies') or [])} 部"),
            ],
        )
    hint = {
        "disabled": "每日播放排行榜已关闭（bot_playrank_enabled），先在管理后台打开",
        "empty": "今天暂时没人播放，榜是空的，没有推送",
        "no_group": "还没配置交流群（bot_group_ids），无法推送",
    }.get(res.get("reason") or "", "推送失败，请看服务日志")
    return layout.card(
        layout.title("每日播放排行榜推送", user.username),
        [layout.row("⚠️", "未推送", hint)],
    )


def admin_markup(user=None) -> dict:
    """管理面板按钮：两列网格＋末行回主界面，写入口只给可写角色。

    读入口（服务器/治理审计/查人说明/群号对账/群权限自检）对全部
    管理员开放，含只读审计；写入��（推榜）只对
    can_write 的角色出现——藏按钮只是方便，真正的门在执行层。
    推榜不设选日/周子页（2026-11-10 砍子面板）：今日榜/本周榜两个
    推送动作直接挂在本面板上，点一次即推。
    user=None（旧调用）按不可写处理（fail-closed）。
    """
    buttons = [
        {"text": "🌐 服务器", "callback_data": "menu:server"},
        {"text": "📋 治理审计", "callback_data": "menu:govlog"},
        {"text": "👤 查人", "callback_data": "menu:admin_whois"},
        {"text": "🔁 群号对账", "callback_data": "menu:admin_reconcile"},
    ]
    if role_gate.can_write(user):
        buttons += [
            {"text": "📅 推今日榜", "callback_data": "menu:admin_rankpush_day"},
            {"text": "🗓️ 推本周榜", "callback_data": "menu:admin_rankpush_week"},
        ]
    buttons.append(
        {"text": "🛡️ 群权限自检", "callback_data": "menu:admin_botperms"})
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append(layout.home_row())
    return {"inline_keyboard": rows}


def admin_back_markup() -> dict:
    """管理子页返回键：回管理面板＋回主界面。"""
    return {"inline_keyboard": [
        [{"text": "⬅️ 返回管理面板", "callback_data": "menu:admin"}],
        layout.home_row(),
    ]}


def group_start_text() -> str:
    """群内 /start 入口卡：只说明私密操作入口与群内通知口径，不带个人数据。

    学参照形态：群里输 /start 只回简短入口提示＋功能按钮（按钮由
    main_markup 按绑定状态给群菜单/未绑定菜单），账号信息不在群里铺开。
    """
    return layout.card(
        layout.title("私密操作入口"),
        [
            layout.row("🔒", "操作入口", "这里是私密操作入口，个人功能请点下方按钮直达，或私聊我使用"),
            layout.row("📣", "群内通知", "群里只保留入库喜报等通知"),
        ],
    )


def handle_start(db, tg_user: dict, chat_id: int, args: str) -> str | tuple[str, dict | None]:
    site_name = html.escape(str(store.get_value(db, "site_name", "Aetrix")))
    telegram_id = tg_user.get("id")
    web_user = resolve(db, int(telegram_id)) if telegram_id else None
    is_private = (chat_id == telegram_id)

    if not is_private:
        # 群里：只回入口卡＋功能按钮（绑定与否同形态），不渲染账号卡。
        return group_start_text(), main_markup(db, web_user, False)

    if web_user:
        return main_card_text(tg_user, web_user), main_markup(db, web_user, True)

    return unbound_card_text(tg_user, site_name), main_markup(db, None, is_private)


def handle_help(db, tg_user: dict, chat_id: int, args: str) -> str:
    return layout.card(
        layout.title("使用帮助"),
        [
            layout.row("🤖", "主要用途", "公益服快捷操作：签到领能量、查能量，以及接收求片进度、订阅到期等通知"),
            layout.row("📋", "全部命令", "点左下角的 / 菜单查看"),
            layout.row("💬", "遇到问题", "请在网页端联系客服"),
        ],
        _bind_guide_text().split("\n"),
    )


def handle_bind(db, tg_user: dict, chat_id: int, args: str) -> str:
    telegram_id = tg_user.get("id")
    if not telegram_id:
        return "暂时无法获取你的 Telegram ID，请稍后再试"
    if resolve(db, int(telegram_id)):
        return "✅ 你的账号已绑定，无需重复绑定；如需更换绑定请联系客服解绑。"
    if not args.strip():
        return _bind_guide_text()
    msg = verify_bind_code(db, int(telegram_id), chat_id, args.strip())
    return msg if msg else "绑定码无效或已过期，请在网页端重新获取绑定码"


def handle_checkin(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.api.economy import _do_checkin_core
    from fastapi import HTTPException

    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None:
        return _bind_guide_text()
    # PA-029：停用账号经机器人签到领能量须拦（与 PA-019 博彩门同口径，
    # 落机器人侧入口，不改 tg_bot resolve 层与网站核心）。
    if not getattr(user, "is_active", True):
        return "账号已被停用"
    try:
        award = _do_checkin_core(db, user)
    except HTTPException as e:
        return html.escape(str(e.detail))
    except Exception:
        return "签到失败，请稍后再试"
    rows = [layout.row("📅", "连签天数", f"已连续签到 <b>{award['streak']}</b> 天")]
    if award["points_awarded"] > 0:
        rows.insert(0, layout.row("💰", "本次能量", f"<b>+{award['points_awarded']}</b>"))
    else:
        rows.append(layout.row("💡", "本次能量", "能量仅限公益服用户领取"))
    if award.get("vitality_gained", 0) > 0:
        rows.append(layout.row("⚡", "活力数值", f"+{award['vitality_gained']}"))
    return layout.card(layout.title("签到成功！"), rows)


def handle_points(db, tg_user: dict, chat_id: int, args: str) -> str:
    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None:
        return _bind_guide_text()
    points = int(user.points or 0)
    rows = [layout.row("✨", "能量余额", points)]
    try:
        from backend import vitality as _vitality

        cfg = _vitality.get_vitality_config(db)
        if cfg.get("enabled") and getattr(user, "is_welfare", False):
            v = int(getattr(user, "vitality", None) or 0)
            vmax = int(cfg.get("max", 14) or 14)
            filled = round(v / vmax * 10) if vmax > 0 else 0
            bar = "▓" * filled + "░" * (10 - filled)
            rows.append(layout.row("⚡", "活力数值", f"{v}/{vmax} <code>{bar}</code>"))
    except Exception:
        pass
    # 群发言能量已并入 /points 输出（菜单简化）：同一段行与 /chatpoints
    # 独立卡共用 _chatpoints_section，计分口径一字不改。读状态失败时
    # 只略过该段（与上方活力块同一容错口径），不拖垮能量主卡。
    try:
        chat_rows, chat_notes = _chatpoints_section(db, user)
        rows.extend(chat_rows)
    except Exception:
        chat_notes = []
    return layout.card(layout.title("我的能量"), rows, chat_notes)


def _chatpoints_section(db, user) -> tuple[list[str], list[str]]:
    """群发言能量段（信息行＋备注）：/points 合并展示与 /chatpoints 共用。

    口径全部取自 points.get_chat_points_status（与发分侧同一只读口），
    本段只负责渲染，不改任何计分语义。
    """
    from backend import points as points_mod

    status = points_mod.get_chat_points_status(db, user.id)
    earned = status["earned_today"]
    cap = status["daily_cap"]
    per_msg = status["per_msg"]
    if per_msg == 0:
        return ([
            layout.row("💬", "发言奖励", "当前已关闭，在群里发言暂不发放能量"),
            layout.row("🏆", "累计能量", f"<b>{status['total_earned']}</b> 能量"),
        ], [])
    filled = round(earned / cap * 10) if cap > 0 else 0
    bar = "▓" * filled + "░" * (10 - filled)
    rows = [
        layout.row("📅", "今日已得", f"<b>{earned:g}</b> / {cap} 能量 <code>{bar}</code>"),
        layout.row("🏆", "累计能量", f"<b>{status['total_earned']}</b> 能量"),
        layout.row("📏", "发言规则", f"在公益群里发言每条 +{per_msg:g} 能量，每天最多 {cap} 能量"),
    ]
    notes = []
    if earned >= cap:
        notes.append("✅ 今日发言能量已达上限，明天再继续。")
    return rows, notes


def handle_chatpoints(db, tg_user: dict, chat_id: int, args: str) -> str:
    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None:
        return _bind_guide_text()
    rows, notes = _chatpoints_section(db, user)
    return layout.card(layout.title("群发言能量"), rows, notes)


def handle_redeem(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.api.economy import _redeem_exchange_core
    from fastapi import HTTPException

    user = resolve(db, int(tg_user.get("id") or 0))
    if user is None:
        return _bind_guide_text()
    # PA-029：停用账号经机器人兑码须拦（与签到门同口径，入口处拦，
    # 码本身不受影响、仍可由正常账号兑换）。
    if not getattr(user, "is_active", True):
        return "账号已被停用"
    code_str = (args or "").strip()
    if not code_str:
        return layout.card(
            layout.title("兑换码兑换"),
            [
                layout.row("🎟️", "使用方法", f"{_cmd('/redeem')} <i>兑换码</i>"),
                layout.row("💡", "兑换示例", _cmd('/redeem ABCD1234')),
            ],
        )
    try:
        result = _redeem_exchange_core(db, user, code_str)
    except HTTPException as e:
        return html.escape(str(e.detail))
    except Exception:
        return "兑换失败，请稍后再试"
    return layout.card(
        layout.title("兑换结果"),
        [layout.row("🎁", "兑换结果", html.escape(str(result.get("message", "兑换成功"))))],
    )


def handle_account(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_account as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_request(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_request as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_my_requests(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_my_requests as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_login(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_login as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_rank(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_rank as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_govlog(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_govlog as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_request_board(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_request_board as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_rules(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_rules as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_level(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_level as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_report(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_report as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_propose(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_propose as _impl
    return _impl(db, tg_user, chat_id, args)

def handle_direct_launch(db, tg_user, chat_id, args):
    from backend.tg_bot.features import handle_direct_launch as _impl
    return _impl(db, tg_user, chat_id, args)
# ---- P1 自助查询（只读）：统一先拦群聊、再验绑定，最后调 queries 只读渲染 ----
# 群聊里查个人数据会把结果公开给全群：一律只许私聊（私聊 chat_id == tg id）。
def _p1_user(db, tg_user: dict, chat_id: int):
    """P1 前置：返回 (user, error_text)，error_text 非空时直接回复它。"""
    from backend.tg_bot import queries

    tg_id = tg_user.get("id")
    if not tg_id or chat_id != tg_id:
        return None, queries.PRIVATE_ONLY_TEXT
    user = resolve(db, int(tg_id))
    if user is None:
        return None, _bind_guide_text()
    return user, None


def handle_bindings(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.tg_bot import queries

    user, err = _p1_user(db, tg_user, chat_id)
    if err:
        return err
    return queries.render_bindings(db, user)


def handle_transfers(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.tg_bot import queries

    user, err = _p1_user(db, tg_user, chat_id)
    if err:
        return err
    return queries.render_transfers(db, user)


def handle_wants(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.tg_bot import queries

    user, err = _p1_user(db, tg_user, chat_id)
    if err:
        return err
    return queries.render_wants(db, user)


def handle_whyplay(db, tg_user: dict, chat_id: int, args: str) -> str:
    from backend.tg_bot import queries

    user, err = _p1_user(db, tg_user, chat_id)
    if err:
        return err
    return queries.diagnose_whyplay(db, user, args)


def handle_reconcile(db, tg_user: dict, chat_id: int, args: str):
    """群-号一致性对账（仅管理员，embyboss 借鉴第 3 项）。

    双门与 handle_whois 同一口径：私聊限定（handler 层 guard，
    router 的 private_only 另有一层）+ resolve 后 is_staff 判定，
    一字不改判定逻辑；非管理员回现行拒绝卡，未绑定回绑定指引。
    本命令只出报告卡（全只读，零处置）；处置走报告卡按钮的
    二次确认（callbacks 的 reconcile:ask / reconcile:do）。
    """
    from backend.tg_bot import queries, reconcile

    tg_id = tg_user.get("id")
    if not tg_id or chat_id != int(tg_id):
        return queries.PRIVATE_ONLY_TEXT
    user = resolve(db, int(tg_id))
    if user is None:
        return _bind_guide_text()
    if not bool(getattr(user, "is_staff", False)):
        return layout.card(
            layout.title("群号一致性对账"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    result = reconcile.run_reconcile(db)
    reconcile.cache_result(int(tg_id), result)
    return reconcile.render_report(result)


def handle_whois(db, tg_user: dict, chat_id: int, args: str,
                 *, in_group: bool = False,
                 reply_from: dict | None = None) -> str:
    """管理员查人（只读，embyboss 借鉴第 2 项 + 群内精简卡）。

    私聊（默认路径）：双门与 handle_admin 同一口径：私聊限定
    （handler 层 guard，router 的 private_only 另有一层）+ resolve
    后 is_staff 判定；非管理员回现行拒绝卡，未绑定回绑定指引。
    二期面板：回复定位或带参定位到具体用户时出管理员面板第一页
    （whois_panel.main_page：精简卡＋翻页/写操作按钮）；定位不到
    具体用户（IP/设备等关键词）仍走 whois.render_whois 审计全卡
    搜索，口径不变。

    群聊（router 显式传 in_group=True 才进，2026-11-01 用户拍板）：
    同一 is_staff 判定，出面板第一页；见 _whois_group。
    """
    from backend.tg_bot import queries, whois, whois_panel

    tg_id = tg_user.get("id")
    if in_group:
        return _whois_group(db, tg_id, args, reply_from)
    if not tg_id or chat_id != int(tg_id):
        return queries.PRIVATE_ONLY_TEXT
    user = resolve(db, int(tg_id))
    if user is None:
        return _bind_guide_text()
    if not bool(getattr(user, "is_staff", False)):
        return layout.card(
            layout.title("账号反查"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    if reply_from and reply_from.get("id"):
        target = resolve(db, int(reply_from["id"]))
        if target is not None:
            name = (f"{reply_from.get('first_name') or ''} "
                    f"{reply_from.get('last_name') or ''}").strip()
            if not name:
                name = str(reply_from.get("username") or "")
            return whois_panel.main_page(db, target, name or None)
    keyword = (args or "").strip()
    if not keyword:
        return layout.card(
            layout.title("账号反查"),
            [
                layout.row("🔍", "用法", "私聊发送 /kk 关键词"),
                layout.row("🧭", "可查", "IP 地址、TG 号、站内用户名 / Emby 名、设备或客户端名"),
            ],
        )
    target = whois.find_lite_user(db, keyword)
    if target is not None:
        return whois_panel.main_page(db, target)
    return whois.render_whois(db, keyword)


def _ephemeral_panel(page: tuple, receiver_tg_id) -> tuple:
    """把管理员面板页包成临时消息标记（Bot API 10.4 包1）。

    标记形状：("__ephemeral__", 文案, 按钮,
    接收人 TG 号)，由 router 识别后只发给接收人本人（发送失败回落
    现行公开路径）。只有定位到具体人的面板页走临时消息；拒绝卡 /
    绑定指引 / 用法 / 查无此人等不带个人数据的卡维持现行公开路径。
    """
    text, markup = page
    return ("__ephemeral__", text, markup, receiver_tg_id)


def _whois_group(db, tg_id, args: str, reply_from: dict | None) -> str:
    """群内精简卡路径（仅管理员，2026-11-01 用户拍板）。

    门禁顺序与私聊全卡一致：调用者未绑定→绑定指引、非管理员→现行
    🔒 拒绝卡（卡里绝不带被查人任何信息）。目标定位：优先被回复
    消息的发送者（reply_from，凭其 TG 号走 identity 链），其次
    带参（站内用户名 / TG 号，whois.find_lite_user 只查 web_users）。
    查无此人 / 对方未绑定一律回明示卡，不报错、不建行；定位成功出
    管理员面板第一页（whois_panel.main_page：精简卡＋按钮，精简卡
    字段仍只走 whois.render_whois_lite 的字段白名单）。面板页经
    _ephemeral_panel 包成临时消息标记，只发给发起管理员本人。
    """
    from backend.tg_bot import whois, whois_panel

    if not tg_id:
        return _bind_guide_text()
    caller = resolve(db, int(tg_id))
    if caller is None:
        return _bind_guide_text()
    if not bool(getattr(caller, "is_staff", False)):
        return layout.card(
            layout.title("账号反查"),
            [layout.row("🔒", "访问状态", "仅管理员可用")],
        )
    if reply_from and reply_from.get("id"):
        target = resolve(db, int(reply_from["id"]))
        if target is None:
            return layout.card(
                layout.title("账号速览"),
                [layout.row("📭", "结果", "对方还没有绑定站内账号")],
            )
        name = (f"{reply_from.get('first_name') or ''} "
                f"{reply_from.get('last_name') or ''}").strip()
        if not name:
            name = str(reply_from.get("username") or "")
        return _ephemeral_panel(
            whois_panel.main_page(db, target, name or None), tg_id)
    keyword = (args or "").strip()
    if not keyword:
        return layout.card(
            layout.title("账号速览"),
            [
                layout.row("🔍", "用法", "回复某人的消息发送 /kk"),
                layout.row("🧭", "也可", "/kk 站内用户名 或 /kk TG 号"),
            ],
        )
    target = whois.find_lite_user(db, keyword)
    if target is None:
        return layout.card(
            layout.title("账号速览"),
            [layout.row("📭", "结果", "没查到这个账号")],
        )
    return _ephemeral_panel(whois_panel.main_page(db, target), tg_id)
