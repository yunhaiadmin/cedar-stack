"""能力：邮件与模板（SMTP / seek.li 二选一）

站内通知渠道里的「邮件」早就实现了投递（``backend/notifications.py`` 的 ``EmailChannel``：
465 走 SSL、其余走 STARTTLS），但一直**没有任何界面能把它配起来**——这个模块补上配置与测试。

2026-11-24 新增 seek.li（NodeSeek Mail API）通道：``email_provider`` 切到 ``seekli`` 后
改走 ``SeekLiMailChannel``（HTTP 发信、每日上限 100 封）。找回密码与邮箱验证码同样经
``build_channel`` 构建渠道，切换后自动受益，不需要改调用点。

配置键沿用既有的 ``email_*``（通知服务正是按这个前缀加载渠道），所以保存后刷新一次通知服务，
站内通知就会同时投递到用户邮箱，不需要改调用点。seek.li 的两个键（``seekli_api_key`` /
``seekli_mailbox_id``）沿用 seek.li 侧命名、不带 ``email_`` 前缀，构建时单独读取。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

SPEC = {
    "title": "邮件与模板",
    "desc": "发信通道二选一：SMTP 服务器，或 seek.li 开发者 API；保存后站内通知会同时投递到用户邮箱，找回密码也走同一通道。",
    "group": "邮件与消息",
    "docs_hint": "SMTP：端口 465 走 SSL，其余端口走 STARTTLS（例如 587）。seek.li：API Key 需 mail:send 权限，每日上限 100 封。",
    "fields": [
        {"key": "email_enabled", "label": "启用邮件通知", "type": "bool", "default": "false"},
        {"key": "email_provider", "label": "发信通道", "type": "select", "default": "smtp",
         "options": ["smtp", "seekli"],
         "hint": "smtp = 传统 SMTP 服务器；seekli = seek.li 开发者 API（每日上限 100 封）"},
        {"key": "email_smtp_host", "label": "SMTP 服务器", "type": "str", "required": True,
         "placeholder": "smtp.example.com"},
        {"key": "email_smtp_port", "label": "端口", "type": "int", "default": "587"},
        {"key": "email_smtp_user", "label": "账号", "type": "str", "placeholder": "noreply@example.com"},
        {"key": "email_smtp_password", "label": "密码 / 授权码", "type": "secret"},
        {"key": "email_from_name", "label": "发件人显示名", "type": "str", "default": ""},
        {"key": "email_from_email", "label": "发件地址", "type": "str", "default": "",
         "hint": "留空则用 SMTP 账号"},
        {"key": "seekli_api_key", "label": "seek.li API Key", "type": "secret",
         "hint": "sk_live_ 开头，在 seek.li「设置 → 开发者 API」创建，需 mail:send 权限"},
        {"key": "seekli_mailbox_id", "label": "seek.li 发件邮箱 ID", "type": "int",
         "default": "82449", "hint": "发件邮箱 aetrix@seek.li 的 mailboxId"},
    ],
    "test_label": "发送测试邮件",
}

CONFIG_KEYS = ("email_enabled", "email_provider", "email_smtp_host", "email_smtp_port",
               "email_smtp_user", "email_smtp_password", "email_from_name",
               "email_from_email", "seekli_api_key", "seekli_mailbox_id")

#: seek.li 发件邮箱缺省 ID（aetrix@seek.li）；通知服务构建渠道时同口径
SEEKLI_DEFAULT_MAILBOX_ID = 82449


def load_config(db: Session) -> dict:
    """统一热读（只许这一套）：短 TTL 缓存，保存时失效"""
    from backend.integrations import store
    return store.get_values(db, CONFIG_KEYS, {k: "" for k in CONFIG_KEYS})


def _provider(cfg: dict) -> str:
    return (cfg.get("email_provider") or "smtp").strip().lower()


def _mailbox_id(cfg: dict) -> int:
    """seek.li 发件邮箱 ID：没配（空）用缺省 82449；配了非法值回 0（视为未配齐）"""
    text = (cfg.get("seekli_mailbox_id") or "").strip()
    if not text:
        return SEEKLI_DEFAULT_MAILBOX_ID
    try:
        return int(text)
    except ValueError:
        return 0


def build_channel(db: Session):
    """按当前配置构造通知渠道（与通知服务用的是同一个类）"""
    from backend.notifications import EmailChannel, SeekLiMailChannel

    cfg = load_config(db)
    if cfg.get("email_enabled", "").lower() != "true":
        return None
    if _provider(cfg) == "seekli":
        api_key = (cfg.get("seekli_api_key") or "").strip()
        mailbox_id = _mailbox_id(cfg)
        if not api_key or mailbox_id <= 0:
            return None
        return SeekLiMailChannel(api_key=api_key, mailbox_id=mailbox_id)
    if not cfg.get("email_smtp_host") or not cfg.get("email_smtp_user"):
        return None
    try:
        port = int(cfg.get("email_smtp_port") or 587)
    except ValueError:
        port = 587
    return EmailChannel(
        smtp_host=cfg.get("email_smtp_host"),
        smtp_port=port,
        smtp_user=cfg.get("email_smtp_user"),
        smtp_password=cfg.get("email_smtp_password"),
        from_name=cfg.get("email_from_name") or "",
        from_email=cfg.get("email_from_email") or "",
    )


def apply(db: Session) -> None:
    """保存后刷新通知渠道：不重启也能让邮件渠道立刻生效 / 失效"""
    from backend.notifications import refresh_channels

    refresh_channels()


def is_configured(values: dict) -> bool:
    """字段是否齐全（不看开关）"""
    if _provider(values) == "seekli":
        return bool((values.get("seekli_api_key") or "").strip()) \
            and _mailbox_id(values) > 0
    return bool(values.get("email_smtp_host")) and bool(values.get("email_smtp_user"))


def is_enabled(values: dict) -> bool:
    """当前是否真的能发信"""
    return str(values.get("email_enabled") or "").lower() == "true" and is_configured(values)


def test(db: Session, payload: dict) -> dict:
    """真实发一封信（默认发给「发件地址」或 SMTP 账号）；seek.li 无收件人时改为验邮箱"""
    cfg = load_config(db)
    channel = build_channel(db)
    if channel is None:
        if _provider(cfg) == "seekli":
            return {"ok": False, "message": "邮件渠道未启用或 seek.li 配置不全（先保存再测）"}
        return {"ok": False, "message": "邮件渠道未启用或缺少 SMTP 服务器 / 账号（先保存再测）"}
    from backend.notifications import SeekLiMailChannel

    if isinstance(channel, SeekLiMailChannel):
        return _test_seekli(channel, payload)
    to_email = (payload.get("to") or cfg.get("email_from_email")
                or cfg.get("email_smtp_user") or "").strip()
    if not to_email:
        return {"ok": False, "message": "请填写收件地址"}
    ok, error = channel._deliver(  # noqa: SLF001 — 直接验证投递路径，与通知走同一段代码
        to_email,
        "测试邮件 · 邮件与模板",
        "这是一封来自后台「系统设置 → 邮件与模板」的测试邮件。\n"
        "收到它说明 SMTP 配置可用，站内通知会同时投递到这个邮箱。",
    )
    if ok:
        return {"ok": True, "message": f"已投递到 {to_email}", "detail": {"to": to_email}}
    return {"ok": False, "message": f"投递失败：{error}", "detail": {"to": to_email}}


def _test_seekli(channel, payload: dict) -> dict:
    """seek.li 测试：填了收件人就真发一封；没填则验 key 并核对发件邮箱在不在、能不能发"""
    to_email = (payload.get("to") or "").strip()
    if to_email:
        ok, error = channel._deliver(  # noqa: SLF001 — 同上，直接验证投递路径
            to_email,
            "测试邮件 · 邮件与模板",
            "这是一封来自后台「系统设置 → 邮件与模板」的测试邮件。\n"
            "收到它说明 seek.li 发信配置可用，站内通知会同时投递到这个邮箱。",
        )
        if ok:
            return {"ok": True, "message": f"已投递到 {to_email}",
                    "detail": {"to": to_email}}
        return {"ok": False, "message": f"投递失败：{error}",
                "detail": {"to": to_email}}
    ok, result = channel.list_mailboxes()
    if not ok:
        return {"ok": False,
                "message": f"API Key 验证未通过：{result}"
                           "（若 key 只有 mail:send 权限，请填收件地址直接发一封测试邮件验证）"}
    for mailbox in result:
        if mailbox.get("id") == channel.mailbox_id:
            if mailbox.get("sendDisabled"):
                return {"ok": False,
                        "message": f"发件邮箱 {mailbox.get('address')} 已被禁止发信",
                        "detail": {"mailbox_id": channel.mailbox_id}}
            return {"ok": True,
                    "message": f"API Key 有效，发件邮箱 {mailbox.get('address')}"
                               f"（ID {channel.mailbox_id}）可发信",
                    "detail": {"mailbox_id": channel.mailbox_id,
                               "address": mailbox.get("address")}}
    return {"ok": False,
            "message": f"配置的发件邮箱 ID {channel.mailbox_id} 不在账号邮箱列表里",
            "detail": {"mailbox_id": channel.mailbox_id}}
