# -*- coding: utf-8 -*-
"""TG Bot 群-号一致性对账（embyboss 借鉴第 3 项）。

管理员私聊 /reconcile 触发：把站内已绑号用户逐一拿去名单群核验
在群状态（sender.get_chat_member），找出不一致：

- ② 已绑号但不在任何名单群；
- ① 在群但没绑站内号：Telegram Bot API 没有群成员枚举接口
  （只有 getChatMember 单人核验），无法全量统计——报告里如实
  标注不可枚举，绝不拿已知样本冒充全量。

本模块只报告、零处置：不建行、不改行、不提交事务。
（原唯一处置「取消 ③ 的公益资格」已随福利服役族于 2026-11-14
删除：公益资格整族下线后无可取消之物，确认/执行/回执三件套与
callbacks 的 reconcile:* 回调一并摘除。）

核验是顺序执行：getChatMember 一人一次 HTTP，且 token 读取走
调用方 session（SQLAlchemy Session 非线程安全，不共用并发）。
单次核验人数有上限（MAX_CHECKS），超出的在报告覆盖说明里标注。
"""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field

from backend import models
from backend.tg_bot import layout, policy, sender

logger = logging.getLogger(__name__)

# 单次对账最多逐一核验的已绑用户数；超出部分不核验，报告如实标注
MAX_CHECKS = 500
# 报告卡每类明细最多列几人，超出截断并注明总数
DETAIL_LIMIT = 10
# 确认卡名单最多列几人，超出截断并注明总数
# 避免连点按钮反复打 TG 接口；执行时仍逐一复核，不拿缓存做裁决
CACHE_TTL_SECONDS = 600.0

_CACHE: dict[int, tuple[float, "ReconcileResult"]] = {}


@dataclass
class ReconcileResult:
    groups: list[int] = field(default_factory=list)
    bound_total: int = 0
    checked: int = 0
    not_in_group: list = field(default_factory=list)     # ② WebUser
    unknown: list = field(default_factory=list)          # 接口失败未核验者

    @property
    def truncated(self) -> bool:
        return self.checked < self.bound_total


def clear_cache() -> None:
    _CACHE.clear()


def cache_result(admin_tg_id: int, result: ReconcileResult) -> None:
    _CACHE[int(admin_tg_id)] = (time.monotonic(), result)


def cached_result(admin_tg_id: int) -> ReconcileResult | None:
    hit = _CACHE.get(int(admin_tg_id))
    if hit is None:
        return None
    ts, result = hit
    if time.monotonic() - ts > CACHE_TTL_SECONDS:
        _CACHE.pop(int(admin_tg_id), None)
        return None
    return result


def _member_status(db, groups, tg_id, checker):
    """逐群核验：任一群在群即在群；全部群明确不在才算不在；
    有群接口失败且无群确认在群 → 未核验（None），绝不算作不在群。"""
    saw_error = False
    for chat_id in groups:
        is_member, _status = checker(db, chat_id, int(tg_id))
        if is_member is True:
            return True
        if is_member is None:
            saw_error = True
    return None if saw_error else False


def run_reconcile(db, cfg=None, checker=None) -> ReconcileResult:
    """执行一次对账（全只读）。checker 默认 sender.get_chat_member。"""
    if cfg is None:
        cfg = policy.read_bot_config(db)
    if checker is None:
        checker = sender.get_chat_member
    groups = sorted(policy.parse_group_ids(cfg.get("bot_group_ids", "")))
    result = ReconcileResult(groups=groups)
    if not groups:
        return result
    bound = (
        db.query(models.WebUser)
        .filter(models.WebUser.telegram_id.isnot(None))
        .order_by(models.WebUser.id.asc())
        .all()
    )
    result.bound_total = len(bound)
    targets = bound[:MAX_CHECKS]
    result.checked = len(targets)
    for user in targets:
        status = _member_status(db, groups, user.telegram_id, checker)
        if status is True:
            continue
        if status is None:
            result.unknown.append(user)
        else:
            result.not_in_group.append(user)
    return result


def _name_list(users, limit: int) -> str:
    """名单展示：站内用户名（TG 号）；站内不存 TG 用户名，只出号。"""
    shown = users[:limit]
    parts = [
        f"{html.escape(str(u.username or ''))}（TG {u.telegram_id}）"
        for u in shown
    ]
    text = "、".join(parts) if parts else "—"
    if len(users) > len(shown):
        text += f" …等共 {len(users)} 人"
    return text


_COVERAGE_NOTE_1 = (
    "在群未绑号无法全量统计：Telegram Bot API 没有群成员枚举接口，"
    "只能逐人核验已知账号；没绑号的群成员 bot 无从枚举。"
)


def render_report(result: ReconcileResult) -> tuple[str, dict | None]:
    """对账报告卡（只报告，零处置）。"""
    if not result.groups:
        return layout.card(
            layout.title("群号一致性对账"),
            [
                layout.row("⚠️", "未配置名单群",
                           "bot_group_ids 为空，没有可核验的群，本次无法对账"),
            ],
            notes=[_COVERAGE_NOTE_1],
        ), layout.back_markup()
    rows = [
        layout.row("👥", "名单群", f"{len(result.groups)} 个"),
        layout.row("🔗", "已绑号用户", result.bound_total),
        layout.row("🧾", "本次已核验", result.checked),
        layout.row("🚪", "已绑但不在群", f"{len(result.not_in_group)} 人"),
        layout.row("❓", "未核验", f"{len(result.unknown)} 人"),
    ]
    notes = []
    if result.not_in_group:
        notes.append("不在群明细：" + _name_list(result.not_in_group, DETAIL_LIMIT))
    if result.unknown:
        notes.append(
            "未核验明细（接口失败，不算不在群）："
            + _name_list(result.unknown, DETAIL_LIMIT))
    notes.append(_COVERAGE_NOTE_1)
    if result.truncated:
        notes.append(
            f"覆盖说明：已绑号共 {result.bound_total} 人，单次最多核验 "
            f"{MAX_CHECKS} 人，本次只核验前 {result.checked} 人，结论仅覆盖这部分。")
    if result.unknown:
        notes.append("覆盖说明：未核验者是 TG 接口失败，状态未知，不计入不在群。")
    markup_rows = [layout.home_row()]
    return layout.card(layout.title("群号一致性对账"), rows, notes), {
        "inline_keyboard": markup_rows,
    }
