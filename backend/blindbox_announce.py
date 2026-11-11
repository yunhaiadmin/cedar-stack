# -*- coding: utf-8 -*-
"""盲盒中奖群报喜（2026-11-11 用户亲点）。

抽中大奖时，机器人往正式群（bot_group_ids 第一个群，与群氛围播报
group_cheer 同口径）发一条庆祝消息，给足情绪价值。挂点在服务层
blindbox.draw 成功交付之后——机器人抽盒与网站抽盒都经过 draw，
不许只挂某个端。

口径（用户 2026-11-11 亲定）：
- 只报大奖：kind=whitelist（任何档位）；kind=access_days 且
  days>=90；kind=reg_code / sub_code（任何天数）。以最终实际交付
  的奖品（effective prize）为准——兜底改发的 365 天注册码照报。
  积分、活力、7/30 天资格不报。
- 同一用户连中多注大奖逐注都报（2026-11-11 20:50 亲令取消
  60 分钟防刷限制，不做任何跳过）。
- 文案：庆祝口吻 + 站内用户名 + 档位名 + 奖品人话描述；白名单
  带「全场限量还剩 X 个」（blindbox._whitelist_remaining 口径）；
  标题句多条轮换随机选。文案不出现任何服务器/线路地址。
- 开关 blindbox_announce_enabled 默认开（缺行/读失败按开），
  关了不发；不另设其他开关。
- 报喜是 best effort：发送失败/任何异常只记日志，绝不影响抽盒
  结果与落账（本模块所有入口自吞异常）。
"""
from __future__ import annotations

import logging
import secrets

from sqlalchemy.orm import Session

from backend import models, realms

logger = logging.getLogger(__name__)

#: 报喜开关（SystemConfig；缺行=开，与群氛围播报开关同口径）。
ANNOUNCE_ENABLED_KEY = "blindbox_announce_enabled"

_TRUE = ("1", "true", "yes", "on")

#: 标题句轮换池（随机选一条）。
TITLES = (
    "🎉 欧皇诞生！",
    "🍀 天选之人！",
    "✨ 手气爆棚！",
    "🎊 大奖砸中啦！",
)


def announce_enabled(db: Session) -> bool:
    """报喜开关：缺行/读失败按开；只有明确存了假值才关。"""
    try:
        row = (db.query(models.SystemConfig)
               .filter(models.SystemConfig.key == ANNOUNCE_ENABLED_KEY)
               .first())
    except Exception:  # noqa: BLE001 读失败按开（默认开口径）
        logger.warning("盲盒报喜开关读取失败，按开处理", exc_info=True)
        return True
    if row is None:
        return True
    return str(row.value or "").strip().lower() in _TRUE


def is_big_prize(kind: str, params: dict | None) -> bool:
    """大奖口径：白名单 / >=90 天资格 / 任何注册码、订阅码。"""
    params = params or {}
    if kind == "whitelist":
        return True
    if kind in ("reg_code", "sub_code"):
        return True
    if kind == "access_days":
        try:
            return int(params.get("days") or 0) >= 90
        except (TypeError, ValueError):
            return False
    return False


def prize_label(db: Session, realm_id: int, kind: str,
                params: dict | None) -> str:
    """奖品人话描述；access_days 按 realm 语境措辞（公益资格/订阅），
    判定不了时用中性「资格」。"""
    params = params or {}
    if kind == "whitelist":
        return "白名单"
    if kind == "access_days":
        days = int(params.get("days") or 0)
        try:
            is_free = realms.is_free_realm(db, realm_id)
        except Exception:  # noqa: BLE001 判定不了用中性措辞
            is_free = None
        if is_free is True:
            return f"公益资格 {days} 天"
        if is_free is False:
            return f"订阅 {days} 天"
        return f"资格 {days} 天"
    if kind == "reg_code":
        return f"注册码（{int(params.get('days') or 30)} 天）"
    if kind == "sub_code":
        return f"订阅码（{int(params.get('days') or 30)} 天）"
    return kind


def build_message(db: Session, user, realm_id: int, tier_name: str,
                  kind: str, params: dict | None, cfg: dict) -> str:
    label = prize_label(db, realm_id, kind, params)
    username = str(getattr(user, "username", "") or "神秘玩家")
    line = f"恭喜 {username} 开「{tier_name}」抽中{label}"
    if kind == "whitelist":
        from backend import blindbox

        remaining = blindbox._whitelist_remaining(db, realm_id, cfg)
        if remaining is not None:
            line += f"，全场限量还剩 {remaining} 个，手慢无"
    line += "！"
    title = secrets.choice(TITLES)
    return (f"{title}\n{line}\n"
            "下一个欧皇会不会是你？来开一盒试试手气～")


def maybe_announce(db: Session, user, realm_id: int, tier_name: str,
                   kind: str, params: dict | None,
                   cfg: dict | None = None) -> bool:
    """够格且开关开时往正式群发一条报喜；返回是否发出。

    全程自吞异常：任何失败只记日志、返回 False，绝不把异常抛回
    抽盒主流程。
    """
    try:
        if not is_big_prize(kind, params):
            return False
        if not announce_enabled(db):
            return False
        from backend import blindbox

        if cfg is None:
            cfg = blindbox.get_realm_config(db, realm_id)
        from backend.tg_bot.group_cheer import target_group_id

        group_id = target_group_id(db)
        if group_id is None:
            logger.info("盲盒报喜跳过：未配置正式群（bot_group_ids）")
            return False
        text = build_message(db, user, realm_id, tier_name, kind,
                             params, cfg)
        from backend.tg_bot import sender

        ok, err = sender.send_message(db, group_id, text, parse_mode=None)
        if not ok:
            logger.warning("盲盒报喜发送失败 user=%s kind=%s err=%s",
                           getattr(user, "id", None), kind, err)
            return False
        return True
    except Exception:  # noqa: BLE001 报喜绝不影响抽盒
        logger.warning("盲盒报喜异常（已忽略）user=%s kind=%s",
                       getattr(user, "id", None), kind, exc_info=True)
        return False
