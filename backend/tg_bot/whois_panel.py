# -*- coding: utf-8 -*-
"""TG Bot /whois 管理员面板（二期，EmbyBoss /kk 式）。

入口：handlers.handle_whois 在定位到具体用户（群里回复/带参、私聊
回复/带参）时返回 (精简卡文本, 本模块按钮)；翻页与写操作走
callbacks 的 whois:* 回调，每次点击都重新校验点击者 is_staff。

页面：
- main：第一页 = whois.render_whois_lite 精简卡（字段一字不改）＋
  子视图/写操作按钮；
- bind：绑定详情（各 provider 的状态与最近验证时间；绑定表无到期
  字段，不编造）；
- cred：公益资格页已随福利服役族于 2026-11-14 删除（按钮与页一并摘除）；
- ask/do：写操作二次确认——ask 只出确认页（写明对谁、做什么、
  多少），do 才真正执行；执行后回 main 页并回显最新状态。封禁/解封
  因 changed 标志本就幂等，不走 nonce。

写操作只许两类（用户 2026-11-02 点选），全部复用现行服务、不另
写一套：封禁/解封走 backend.user_lifecycle.set_user_active（与网站
管理端同一实现，停用同事务吊销 Emby 令牌＋抬 token_version）。
（赠凭证已随公益服删除摘除：其依赖的查看行写口已下线。赠资格两档
已随公益资格发放删除摘除，2026-11-14：其依赖的
portal.grant_welfare 已删。赠能量三档已随积分删除摘除，2026-11-13。）
每次写操作另写一条治理审计（governance.record_governance_audit）。
不做删号。
"""
from __future__ import annotations

import html
import logging

from backend.tg_bot import layout, role_gate, whois

logger = logging.getLogger(__name__)

#: 写操作预设档位（op → (动作名, 内容说明)），不做自由输入。
OPS: dict[str, tuple[str, str]] = {
    "ban": ("封禁账号", "停用账号并吊销其全部 Emby 令牌"),
    "unban": ("解封账号", "恢复账号启用状态"),
}


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def main_markup(user) -> dict:
    """面板第一页按钮：子视图＋写操作入口（封禁/解封按当前状态二选一）。"""
    uid = user.id
    ban_btn = (
        {"text": "✅ 解封账号", "callback_data": f"whois:ask:{uid}:unban"}
        if not bool(getattr(user, "is_active", True))
        else {"text": "⛔ 封禁账号", "callback_data": f"whois:ask:{uid}:ban"}
    )
    return {"inline_keyboard": [
        [
            {"text": "🔗 绑定详情", "callback_data": f"whois:bind:{uid}"},
        ],
        [
            ban_btn,
        ],
    ]}


def _back_markup(user, page: str = "main") -> dict:
    return {"inline_keyboard": [[
        {"text": "⬅️ 返回面板", "callback_data": f"whois:{page}:{user.id}"},
    ]]}


def main_page(db, user, tg_display_name: str | None = None,
              note: str | None = None) -> tuple[str, dict]:
    """面板第一页：精简卡（字段不动）＋按钮；note 为操作结果回显。"""
    text = whois.render_whois_lite(db, user, tg_display_name)
    if note:
        text = f"{text}\n\n{note}"
    return text, main_markup(user)


def bindings_page(db, user) -> tuple[str, dict]:
    """绑定详情页：每个绑定一行（provider＋账号名＋状态＋最近验证）。"""
    from backend.emby_server import models as em
    from backend.tg_bot.queries import BINDING_STATUS_TEXT, PROVIDER_TEXT

    rows = (
        db.query(em.UserStorageBinding)
        .filter(em.UserStorageBinding.user_id == user.id)
        .order_by(em.UserStorageBinding.id)
        .all()
    )
    lines = []
    if not rows:
        lines.append(layout.row("🔗", "网盘绑定", "未绑定任何网盘"))
    for row in rows:
        provider = PROVIDER_TEXT.get(row.provider, _esc(row.provider))
        status = BINDING_STATUS_TEXT.get(row.status, _esc(row.status))
        verified = (
            row.last_verified_at.strftime("%Y-%m-%d %H:%M")
            if row.last_verified_at else "—")
        lines.append(layout.row(
            "🔗", "网盘绑定",
            f"{provider}（{_esc(row.account_label)}）｜{status}"
            f"，最近验证 {verified}"))
    text = layout.card(
        layout.title("绑定详情", f"{user.username}（#{user.id}）"), lines)
    return text, _back_markup(user)


def confirm_page(db, user, op: str) -> tuple[str, dict]:
    """写操作确认页：写明对谁、做什么、多少；确认按钮才执行。"""
    action_name, detail = OPS[op]
    uid = user.id
    text = layout.card(
        layout.title("确认操作"),
        [
            layout.row("👤", "操作对象", f"<b>{_esc(user.username)}</b>（#{uid}）"),
            layout.row("🛠️", "执行动作", action_name),
            layout.row("📦", "操作内容", detail),
            layout.row("⚠️", "提示说明", "点「确认执行」才会真正生效"),
        ],
    )
    markup = {"inline_keyboard": [
        [{"text": "✅ 确认执行", "callback_data": f"whois:do:{uid}:{op}"}],
        [{"text": "⬅️ 取消", "callback_data": f"whois:main:{uid}"}],
    ]}
    return text, markup


def _audit(db, action: str, admin, target, detail: dict) -> None:
    from backend import governance

    governance.record_governance_audit(
        db, action=action, actor_user_id=admin.id,
        target_user_id=target.id, target_ref=f"user:{target.id}",
        detail=detail)


def _manage_guard_refusal(db, admin, target, op: str) -> str | None:
    """管理员目标守卫：网站端 update_user 同款口径（PA-056）。

    - 目标是其他管理员且操作者非超管 → 拒绝（网站端 403 口径）；
    - 封禁最后一名可登录超管 → 拒绝（网站端 400 口径）。
    自禁不在此：ban 分支的自禁分支在先、口径保留；unban 自身不受影响。
    """
    from backend import admin_roles
    from backend.api.admins_admin import _locked_loginable_super_count

    if (bool(getattr(target, "is_staff", False))
            and target.id != admin.id
            and not admin_roles.is_super(admin)):
        return "⛔ 目标账号是管理员：只有超级管理员能管理其他管理员。"
    if (op == "ban"
            and admin_roles.role_of(target) == admin_roles.ROLE_SUPER
            and _locked_loginable_super_count(db) <= 1):
        return ("⛔ 不能封禁最后一名可登录的超级管理员："
                "请先给别的账号授予超级管理员。")
    return None


def execute(db, admin, target, op: str) -> str:
    """执行已确认的写操作，返回结果回显文案（面板 main 页展示）。

    全部走现行服务（见模块头注），本函数只做编排＋审计＋提交。
    封禁/解封前先过管理员目标守卫（PA-056，见 _manage_guard_refusal）。
    """
    from backend.user_lifecycle import set_user_active

    # 角色门（执行层）：只读审计角色一律拒绝写、只许读，落在最前面。
    refusal = role_gate.readonly_refusal(admin)
    if refusal:
        return refusal

    name = _esc(target.username)
    if op == "ban":
        if target.id == admin.id:
            return "⛔ 不能封禁自己的账号（与网站管理端口径一致）。"
        refusal = _manage_guard_refusal(db, admin, target, op)
        if refusal:
            return refusal
        changed = set_user_active(db, target, False)
        if changed:
            _audit(db, "whois_ban", admin, target, {"via": "tg_whois_panel"})
            db.commit()
            return f"✅ 已封禁 <b>{name}</b>：账号已停用，Emby 令牌已全部吊销。"
        return f"ℹ️ <b>{name}</b> 已经是停用状态，未做变更。"
    if op == "unban":
        refusal = _manage_guard_refusal(db, admin, target, op)
        if refusal:
            return refusal
        changed = set_user_active(db, target, True)
        if changed:
            _audit(db, "whois_unban", admin, target, {"via": "tg_whois_panel"})
            db.commit()
            return f"✅ 已解封 <b>{name}</b>：账号恢复启用（旧令牌不自动恢复）。"
        return f"ℹ️ <b>{name}</b> 已经是启用状态，未做变更。"
    return "未知操作。"


def render(db, admin, user, page: str, op: str | None = None) -> tuple[str, dict]:
    """面板翻页统一渲染口（callbacks 调用；admin 已过 is_staff 校验）。"""
    if page == "bind":
        return bindings_page(db, user)
    if page == "ask" and op in OPS:
        return confirm_page(db, user, op)
    if page == "do" and op in OPS:
        note = execute(db, admin, user, op)
        db.refresh(user)
        return main_page(db, user, note=note)
    return main_page(db, user)
