"""Telegram 消息发送模块"""

import httpx

from pathlib import Path

from backend.integrations.telegram import token as get_telegram_token
from backend.tg_bot.layout import BRAND_IMAGE, caption_fits, panel_image


def _call(db, method: str, payload: dict) -> tuple[bool, str | None]:
    """
    调用 Telegram Bot API 的通用私有方法

    统一完成：读取 Bot Token（为空则直接失败）→ POST 请求 →
    校验 HTTP 状态码 → 检查响应 ok 字段

    :param db: 数据库会话对象
    :param method: Telegram API 方法名（如 sendMessage）
    :param payload: 请求负载（Telegram API 格式）
    :return: (是否成功, 错误信息)，成功时为 (True, None)
    """
    # 从数据库读取 Bot Token
    bot_token = get_telegram_token(db)

    # Token 为空时直接返回失败
    if not bot_token:
        return False, "token 未配置"

    # 拼接 API 地址
    url = f"https://api.telegram.org/bot{bot_token}/{method}"

    try:
        # 同步 POST 请求，超时 15 秒
        response = httpx.post(url, json=payload, timeout=15.0)
        response.raise_for_status()

        data = response.json()
        # Telegram API 返回 ok=false 时视为失败
        if not data.get("ok", False):
            return False, f"Telegram API 错误: {data.get('description', '未知错误')}"

        return True, None
    except httpx.HTTPStatusError as exc:
        # HTTP 状态码异常
        return False, f"HTTP 状态码错误: {exc.response.status_code}"
    except httpx.TimeoutException:
        # 请求超时
        return False, "请求超时"
    except httpx.RequestError as exc:
        # 网络请求异常
        return False, f"网络请求失败: {exc}"
    except ValueError:
        # 响应 JSON 解析失败
        return False, "响应解析失败"


def _call_ex(db, method: str, payload: dict) -> tuple[bool, str | None, dict | None]:
    """_call 的带结果版本：成功时第三项为 Telegram 响应里的 result。

    需要 message_id 的新接口（send_message_ex 等）走这里；
    _call 本体与既有调用方、打桩接缝一字不动。
    """
    bot_token = get_telegram_token(db)
    if not bot_token:
        return False, "token 未配置", None
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        response = httpx.post(url, json=payload, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            return False, f"Telegram API 错误: {data.get('description', '未知错误')}", None
        result = data.get("result")
        return True, None, result if isinstance(result, dict) else None
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP 状态码错误: {exc.response.status_code}", None
    except httpx.TimeoutException:
        return False, "请求超时", None
    except httpx.RequestError as exc:
        return False, f"网络请求失败: {exc}", None
    except ValueError:
        return False, "响应解析失败", None


def send_message(
    db,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """
    向指定 Telegram 聊天发送消息

    :param db: 数据库会话对象
    :param chat_id: 目标聊天 ID
    :param text: 消息文本内容（HTML 转义后的文本；parse_mode 默认为 HTML）
    :param reply_markup: 可选的键盘标记（Telegram API 格式）
    :param parse_mode: Telegram 解析模式，默认 "HTML"；传 None 则不带该字段
    :return: (是否成功, 错误信息)，成功时为 (True, None)
    """
    # 构造请求负载
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return _call(db, "sendMessage", payload)


def send_message_ex(
    db,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None, int | None]:
    """send_message 的带 message_id 版本（消息自毁调度等场景用）。

    负载构造与 send_message 完全一致；成功时第三项为新消息的
    message_id，失败时为 None。旧 send_message 签名/行为不变。
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    ok, err, result = _call_ex(db, "sendMessage", payload)
    if not ok or not result:
        return ok, err, None
    message_id = result.get("message_id")
    return True, None, int(message_id) if isinstance(message_id, int) else None


def _call_multipart(db, method: str, payload: dict, files: dict) -> tuple[bool, str | None]:
    """调用需要 multipart/form-data 的 Telegram API（如 sendPhoto）。"""
    bot_token = get_telegram_token(db)
    if not bot_token:
        return False, "token 未配置"
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        response = httpx.post(url, data=payload, files=files, timeout=20.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            return False, f"Telegram API 错误: {data.get('description', '未知错误')}"
        return True, None
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP 状态码错误: {exc.response.status_code}"
    except httpx.TimeoutException:
        return False, "请求超时"
    except httpx.RequestError as exc:
        return False, f"网络请求失败: {exc}"
    except ValueError:
        return False, "响应解析失败"


def _call_multipart_ex(db, method: str, payload: dict, files: dict) -> tuple[bool, str | None, dict | None]:
    """_call_multipart 的带结果版本；既有调用方与打桩接缝不变。"""
    bot_token = get_telegram_token(db)
    if not bot_token:
        return False, "token 未配置", None
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        response = httpx.post(url, data=payload, files=files, timeout=20.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            return False, f"Telegram API 错误: {data.get('description', '未知错误')}", None
        result = data.get("result")
        return True, None, result if isinstance(result, dict) else None
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP 状态码错误: {exc.response.status_code}", None
    except httpx.TimeoutException:
        return False, "请求超时", None
    except httpx.RequestError as exc:
        return False, f"网络请求失败: {exc}", None
    except ValueError:
        return False, "响应解析失败", None


def _photo_upload(path) -> tuple[str, str]:
    """按扩展名给出 multipart 上传的 (文件名, MIME)；未知扩展名按 PNG。"""
    suffix = Path(path).suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        suffix, "image/png")
    return Path(path).name, mime


def send_photo(
    db,
    chat_id: int,
    caption: str,
    reply_markup: dict | None = None,
    photo_path=None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """发送品牌图卡片；caption 必须由调用方保证在 Telegram 上限内。"""
    if not caption_fits(caption):
        return False, "caption 超过 1024 字符"
    path = panel_image(photo_path)
    try:
        photo_file = open(path, "rb")
    except OSError as exc:
        return False, f"品牌图不可用: {exc}"
    payload: dict[str, str] = {"chat_id": str(chat_id), "caption": caption}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        import json

        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    name, mime = _photo_upload(path)
    with photo_file:
        return _call_multipart(
            db, "sendPhoto", payload, {"photo": (name, photo_file, mime)}
        )


def send_photo_ex(
    db,
    chat_id: int,
    caption: str,
    reply_markup: dict | None = None,
    photo_path=None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None, int | None]:
    """send_photo 的带 message_id 版本；前置校验与旧接口一致。"""
    if not caption_fits(caption):
        return False, "caption 超过 1024 字符", None
    path = panel_image(photo_path)
    try:
        photo_file = open(path, "rb")
    except OSError as exc:
        return False, f"品牌图不可用: {exc}", None
    payload: dict[str, str] = {"chat_id": str(chat_id), "caption": caption}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        import json

        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    name, mime = _photo_upload(path)
    with photo_file:
        ok, err, result = _call_multipart_ex(
            db, "sendPhoto", payload, {"photo": (name, photo_file, mime)}
        )
    if not ok or not result:
        return ok, err, None
    message_id = result.get("message_id")
    return True, None, int(message_id) if isinstance(message_id, int) else None


def send_panel(
    db,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    *,
    prefer_photo: bool = True,
    photo_path=None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """优先发送图卡；caption 放不下、资产不可用或 API 失败时回退纯文本。

    photo_path 是面板专属图覆盖（默认品牌图）；覆盖图缺失时发送层
    自动回退品牌图，品牌图也发不出才回退纯文本，面板不会发不出去。
    """
    if prefer_photo and caption_fits(text):
        ok, _err = send_photo(
            db, chat_id, text, reply_markup, photo_path=photo_path, parse_mode=parse_mode)
        if ok:
            return True, None
    return send_message(db, chat_id, text, reply_markup, parse_mode=parse_mode)


def edit_message_media(
    db,
    chat_id: int,
    message_id: int,
    caption: str,
    reply_markup: dict | None = None,
    photo_path=None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """原地替换图卡消息的图片与 caption（editMessageMedia）。

    caption 编辑换不了图片，面板要换专属图时走这里；photo_path
    缺失时回退品牌图（与 send_photo 同一解析口）。失败由调用方决定
    是否回退普通 caption 编辑。
    """
    if not caption_fits(caption):
        return False, "caption 超过 1024 字符"
    path = panel_image(photo_path)
    try:
        photo_file = open(path, "rb")
    except OSError as exc:
        return False, f"面板图不可用: {exc}"
    import json

    media: dict[str, str] = {
        "type": "photo", "media": "attach://photo", "caption": caption}
    if parse_mode is not None:
        media["parse_mode"] = parse_mode
    payload: dict[str, str] = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "media": json.dumps(media, ensure_ascii=False),
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    name, mime = _photo_upload(path)
    with photo_file:
        return _call_multipart(
            db, "editMessageMedia", payload, {"photo": (name, photo_file, mime)}
        )


def edit_message_caption(
    db,
    chat_id: int,
    message_id: int,
    caption: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """原地编辑图片消息的 caption 与按钮（图卡面板间切换专用）。"""
    if not caption_fits(caption):
        return False, "caption 超过 1024 字符"
    payload = {"chat_id": chat_id, "message_id": message_id, "caption": caption}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call(db, "editMessageCaption", payload)


def edit_panel(
    db,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
    *,
    has_photo: bool = False,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """按原消息形态编辑面板：图卡走 caption，旧纯文本消息走 text。

    图卡的新内容超过 caption 上限时不能硬塞，改为另发一条完整纯文本面板；
    旧消息保留在原处，新面板的返回按钮仍可继续工作。
    """
    if has_photo:
        if caption_fits(text):
            return edit_message_caption(
                db, chat_id, message_id, text, reply_markup, parse_mode=parse_mode
            )
        return send_message(db, chat_id, text, reply_markup, parse_mode=parse_mode)
    return edit_message_text(db, chat_id, message_id, text, reply_markup, parse_mode=parse_mode)


def delete_message(db, chat_id: int, message_id: int) -> tuple[bool, str | None]:
    """删除一条消息（群里需要 Bot 有删除权限；失败由调用方忽略）。"""
    return _call(db, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def edit_message_text(
    db,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """
    原地编辑指定消息的文本与键盘（不重发新消息）

    用途：抢红包后原地更新按钮消息、菜单按钮切换页面

    :param db: 数据库会话对象
    :param chat_id: 目标聊天 ID
    :param message_id: 要编辑的消息 ID
    :param text: 新的消息文本内容（HTML 转义后的文本）
    :param reply_markup: 可选的键盘标记（Telegram API 格式）
    :param parse_mode: Telegram 解析模式，默认 "HTML"；传 None 则不带该字段
    :return: (是否成功, 错误信息)，成功时为 (True, None)
    """
    # 构造请求负载
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return _call(db, "editMessageText", payload)


def answer_callback_query(db, callback_query_id: str, text: str | None = None, show_alert: bool = False) -> tuple[bool, str | None]:
    """
    回复按钮点击的回执请求

    用途：按钮点击回执，可选弹出提示文本

    :param db: 数据库会话对象
    :param callback_query_id: 回调查询 ID
    :param text: 可选的提示文本，非空时才携带
    :param show_alert: 为 True 时以弹窗形式展示提示文本
    :return: (是否成功, 错误信息)，成功时为 (True, None)
    """
    # 构造请求负载
    payload = {
        "callback_query_id": callback_query_id,
    }
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True

    return _call(db, "answerCallbackQuery", payload)


# ---------------------------------------------------------------------------
# 临时消息（Bot API 10.2+/10.4 ephemeral）：横切只许这一套 wrapper。
#
# 临时消息只对指定接收人可见，官方不保证送达（接收人离线可能收不到），
# 消失时点由客户端决定、无固定时长。标识字段以官方 API 参考为准：
# Message.ephemeral_message_id（10.2 新增），发送结果与
# callback_query.message 都读这个字段。
# 限制（官方）：按钮不许带 web_app / login_url；来自临时消息的回调，
# replace_callback_query_message 必须为 False、改用 editEphemeralMessage*；
# editEphemeralMessageText 的 chat_id 只收 supergroup。
# ---------------------------------------------------------------------------


def _ephemeral_params(
    *,
    receiver_user_id: int | None = None,
    callback_query_id: str | None = None,
    replace_callback_query_message: bool = False,
) -> dict:
    """组装 ephemeral_message_parameters：只放非空字段，replace 仅 True 时放。"""
    params: dict = {}
    if receiver_user_id is not None:
        params["receiver_user_id"] = receiver_user_id
    if callback_query_id is not None:
        params["callback_query_id"] = callback_query_id
    if replace_callback_query_message:
        params["replace_callback_query_message"] = True
    return params


def send_ephemeral_message(
    db,
    chat_id: int,
    text: str,
    *,
    receiver_user_id: int | None = None,
    callback_query_id: str | None = None,
    replace_callback_query_message: bool = False,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None, int | None]:
    """
    发送临时消息，返回 (是否成功, 错误信息, ephemeral_message_id)。

    两种形态（二选一，官方 EphemeralMessageParameters）：
    - 指定接收人：传 receiver_user_id，只发给该成员（与 callback_query_id
      同给时，receiver 必须就是按按钮的人）。
    - 回调回复：只传 callback_query_id，只给按按钮的人看；须在收到回调
      后 15 秒内发送，且仍必须照常 answerCallbackQuery 回执。
    两者都不给不构成临时消息，本函数本地拒绝、绝不降级成普通消息发出。
    ephemeral_message_id 取自发送结果 Message 的同名字段；结果里没有
    该字段时返回 None（调用方据此跳过定时删除登记）。
    """
    if receiver_user_id is None and callback_query_id is None:
        return False, "临时消息缺少接收人或回调限定", None
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "ephemeral_message_parameters": _ephemeral_params(
            receiver_user_id=receiver_user_id,
            callback_query_id=callback_query_id,
            replace_callback_query_message=replace_callback_query_message),
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    ok, err, result = _call_ex(db, "sendMessage", payload)
    if not ok or not result:
        return ok, err, None
    raw = result.get("ephemeral_message_id")
    return True, None, int(raw) if isinstance(raw, int) else None


def edit_ephemeral_message_text(
    db,
    chat_id: int,
    receiver_user_id: int,
    ephemeral_message_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "HTML",
) -> tuple[bool, str | None]:
    """原地编辑一条临时消息（三元组定位：chat_id 只收 supergroup）。

    来自临时消息的回调必须走本方法改内容，不许用 replace 语义。
    官方注：不保证用户收到编辑事件。返回 (是否成功, 错误信息)。
    """
    payload: dict = {
        "chat_id": chat_id,
        "receiver_user_id": receiver_user_id,
        "ephemeral_message_id": ephemeral_message_id,
        "text": text,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call(db, "editEphemeralMessageText", payload)


def delete_ephemeral_message(
    db,
    chat_id: int,
    receiver_user_id: int,
    ephemeral_message_id: int,
) -> tuple[bool, str | None]:
    """删除一条临时消息（三元组定位）。官方注：不保证用户收到删除事件。"""
    return _call(db, "deleteEphemeralMessage", {
        "chat_id": chat_id,
        "receiver_user_id": receiver_user_id,
        "ephemeral_message_id": ephemeral_message_id,
    })


def set_my_commands(db, commands: list[tuple[str, str]], scope: dict | None = None) -> tuple[bool, str | None]:
    """
    注册 Bot 命令菜单（客户端输入框旁的「菜单」按钮里显示）。

    幂等：相同命令重复注册无副作用；失败只返回错误不抛异常，
    由调用方决定是否重试。

    :param db: 数据库会话对象
    :param commands: [(command, description)]，command 不带斜杠、小写字母数字下划线
    :param scope: 可选 BotCommandScope（如 {"type": "chat", "chat_id": 123}）；
        不传 = default scope（全体用户），payload 与旧行为逐字一致
    :return: (是否成功, 错误信息)，成功时为 (True, None)
    """
    payload = {
        "commands": [
            {"command": command, "description": description}
            for command, description in commands
        ]
    }
    if scope is not None:
        payload["scope"] = scope
    return _call(db, "setMyCommands", payload)


def send_dice(db, chat_id: int, emoji: str = "🎰") -> tuple[bool, str | None, int | None, int | None]:
    """发 Telegram 原生骰子/老虎机动画（sendDice）。

    老虎机的唯一结果来源：平台返回 result.dice.value（老虎机为 1-64），
    服务端不自造随机数，结算只认这个值。

    :param db: 数据库会话对象（读 bot token 用；无 token 环境传 None 也可）
    :param chat_id: 目标会话
    :param emoji: 骰子表情，老虎机固定 🎰
    :return: (是否成功, 错误信息, dice.value, message_id)
    """
    ok, err, result = _call_ex(db, "sendDice", {"chat_id": chat_id, "emoji": emoji})
    if not ok or not isinstance(result, dict):
        return False, err, None, None
    dice = result.get("dice") or {}
    value = dice.get("value")
    if not isinstance(value, int):
        return False, "sendDice 响应缺少 dice.value", None, None
    message_id = result.get("message_id")
    return True, None, value, message_id if isinstance(message_id, int) else None


def send_widget(db, chat_id: int, html: str, *, public_data: str | None = None,
                chat_data: str | None = None, caption: str | None = None,
                allow_queries: bool = False, width: int = 0,
                height: int = 0) -> tuple[bool, str | None, int | None]:
    """发 HTML widget 消息（Bot API 10.4 sendWidget）。

    html 为 UTF-8 文本（≤32768 字节、服务端 zlib-9 后 ≤4096 字节，
    不含 NUL）；public_data/chat_data 为字符串（各 ≤1024 字节），
    不传表示无状态。width/height 为尺寸锚点，0 = 自动（见官方
    Widgets 指南 Sizes 一节）。生产环境目前仅限私聊、自动
    protected content、不可转发。成功时第三项为 message_id。
    """
    payload: dict = {
        "chat_id": chat_id,
        "html": html,
        "width": width,
        "height": height,
        "allow_queries": allow_queries,
    }
    if public_data is not None:
        payload["public_data"] = public_data
    if chat_data is not None:
        payload["chat_data"] = chat_data
    if caption is not None:
        payload["caption"] = caption
    ok, err, result = _call_ex(db, "sendWidget", payload)
    if not ok or not isinstance(result, dict):
        return False, err, None
    message_id = result.get("message_id")
    return True, None, int(message_id) if isinstance(message_id, int) else None


def answer_widget_query(db, widget_query_id: str,
                        data: str | None = None) -> tuple[bool, str | None]:
    """应答 widget 请求（Bot API 10.4 answerWidgetQuery）。

    data 为回给 widget 的结果文本（≤1024 字节），只有发起请求的
    widget 能收到；须在 query 到达后 15 秒内应答，否则 widget 侧
    TIMEOUT。失败也应答（带 error 的 data），绝不留空。
    """
    payload: dict = {"widget_query_id": widget_query_id}
    if data is not None:
        payload["data"] = data
    return _call(db, "answerWidgetQuery", payload)


def edit_message_widget_data(db, chat_id: int | None = None,
                             message_id: int | None = None, *,
                             inline_message_id: str | None = None,
                             public_data: str | None = None,
                             chat_data: str | None = None,
                             ) -> tuple[bool, str | None]:
    """改 widget 状态（Bot API 10.4 editMessageWidgetData）。

    原消息按 chat_id + message_id 定位，经 bot 发出的按
    inline_message_id 定位；不传的字段保持原值。内容未变时
    Telegram 报 "message is not modified"，调用方按已完成处理。
    """
    payload: dict = {}
    if inline_message_id is not None:
        payload["inline_message_id"] = inline_message_id
    else:
        payload["chat_id"] = chat_id
        payload["message_id"] = message_id
    if public_data is not None:
        payload["public_data"] = public_data
    if chat_data is not None:
        payload["chat_data"] = chat_data
    return _call(db, "editMessageWidgetData", payload)


# 三重门·身份门：在群成员列表里才算自己人
_MEMBER_STATUSES = ("creator", "administrator", "member", "restricted")


def get_chat_member(db, chat_id: int, user_id: int) -> tuple[bool | None, str]:
    """查用户在群里的成员状态（三重门·身份门）。

    :return: (is_member, status)
        - (True, status)：在群里，status 为 creator/administrator/member/restricted
        - (False, status)：不在群里（left/kicked）或查无此人
        - (None, "api_error")：TG API 调用失败（网络/限流/token问题），
          调用方应 fail-open（放行但记日志），可用性优先
    """
    import httpx

    from backend.integrations.telegram import token as get_telegram_token

    bot_token = get_telegram_token(db)
    if not bot_token:
        return None, "api_error"
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/getChatMember",
            json={"chat_id": chat_id, "user_id": user_id},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None, "api_error"
    if not data.get("ok"):
        # 用户不在群里时 TG 返回 ok=false（"user not found" / "chat not found"）
        desc = str(data.get("description", "")).lower()
        if "not found" in desc or "chat not found" in desc:
            return False, "not_found"
        return None, "api_error"
    status = str((data.get("result") or {}).get("status", ""))
    return (status in _MEMBER_STATUSES), status


def answer_inline_query(
    db,
    inline_query_id: str,
    results: list,
    *,
    cache_time: int = 30,
    is_personal: bool = True,
    switch_pm_text: str | None = None,
    switch_pm_parameter: str | None = None,
) -> tuple[bool, str | None]:
    """回答内联查询（answerInlineQuery）：@bot 片名的候选卡片走这里。

    is_personal 默认 True：结果按人不同（绑定状态 / 可见库范围），
    Telegram 不得把一个人的结果缓存给另一个人。
    switch_pm_text/parameter：给「先去私聊办手续」类引导用（如未绑定
    时引导去绑定），点了跳私聊并带上 /start 参数。
    """
    payload: dict = {
        "inline_query_id": inline_query_id,
        "results": results,
        "cache_time": cache_time,
        "is_personal": is_personal,
    }
    if switch_pm_text:
        payload["switch_pm_text"] = switch_pm_text
        payload["switch_pm_parameter"] = switch_pm_parameter or "start"
    return _call(db, "answerInlineQuery", payload)


def pin_message(db, chat_id: int, message_id: int) -> tuple[bool, str | None]:
    """置顶指定消息（pinChatMessage，静默置顶不额外通知全员）。

    抽奖公告落库后置顶用；失败只以 (False, err) 返回，调用方记日志不回滚。
    """
    return _call(db, "pinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": True,
    })


def unpin_message(db, chat_id: int, message_id: int) -> tuple[bool, str | None]:
    """取消置顶指定消息（unpinChatMessage）。开奖/取消时收掉公告置顶用。"""
    return _call(db, "unpinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })


def get_me(db) -> tuple[bool, str | None, dict | None]:
    """查询机器人自身信息（getMe）；成功时第三项为 result（含 id）。"""
    return _call_ex(db, "getMe", {})


def get_chat_member_raw(db, chat_id: int, user_id: int) -> tuple[bool, str | None, dict | None]:
    """查询成员原始信息（getChatMember）；成功时第三项为完整 result。

    get_chat_member 只回 (是否成员, status)，判断机器人发言权限还需要
    administrator 的 can_post_messages 等字段，故另开原始口；既有
    get_chat_member 本体与调用方一字不动。
    """
    return _call_ex(db, "getChatMember", {"chat_id": chat_id, "user_id": user_id})
