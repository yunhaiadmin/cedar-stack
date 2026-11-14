# -*- coding: utf-8 -*-
"""Telegram 正式群氛围播报：许愿到货点名报喜 + 会员升级祝贺。

两件事共用本模块的发送与幂等收口，不许在调用点各写一份：

- 许愿到货报喜：求片行到货（proactive.note_store_request_completed
  收口，与私聊到货同批触发）后，往正式群（bot_group_ids 第一个群，
  与播放排行榜/公告同一个群）发一条点名消息：许愿人站内用户名 +
  片名 + 附议人数合并（「还有 N 位朋友」），只提站名
  www.aetrix.love，不贴任何链接、不 @ TG 账号、不出隐私信息。
  开关 ``tg_notify_group_arrival_enabled`` 默认开；关了只发私聊、
  不发群。
- 会员升级祝贺：原由 member_level.add_xp 跨级时排队（等级体系已于
  2026-11-14 删除，现无自动触发点，queue/send 入口保留）：往同一个
  群发一条：用户名 + Lv.旧 → Lv.新 + 新等级名。只报升级，
  同级/降级不报；多级连跳只发一条（旧 → 最终）。
- 新贡献者欢迎：池成员审核通过时（pool_admin 审核收口，与站内信
  同一触发点、业务事务已提交后同步发），往同一个群发一条：站内
  用户名 + 加入公共片库 + 奖励口径一句。只欢迎通过，驳回不发；
  开关 ``tg_notify_group_pool_welcome_enabled`` 默认开；锚＝
  (group_pool_welcome, 成员行 id, 贡献者 id)，只发一次。

幂等：复用 proactive 私聊台账表 TgProactiveNotice 同款唯一约束
（kind, subject_id, user_id）当锚——到货锚＝(group_store_arrival,
心愿行 id, 许愿人 id)，升级锚＝(group_level_up, 新等级, 用户 id)。
锚行直接写终态 status="sent"：它只是台账锚、不是待发队列，
私聊 flusher 只领 pending/inflight，永远不会误发这些行。
发送失败时删锚，留待后续触发重试；失败只记日志，绝不阻塞
到货翻转、私聊入队与发经验主流程。

触发时机：到货与升级两个入口都只往会话 info 里排队，真正的
claim+发送在业务事务 commit 成功后（Session after_commit 钩子，
与 proactive 唤醒 flusher 同款机制）同步执行——回滚的事务不会
发出假消息；after_rollback 时排队项直接丢弃。新贡献者欢迎的审
核收口本身就跑在业务提交之后，等价于同一纪律下同步直发。发送
在业务线程里同步做：都是低频事件，失败有界（发送层 15 秒超时、
只试一次）。
"""

from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.integrations import store as _store
from backend.tg_bot import policy, sender

logger = logging.getLogger(__name__)

#: 到货群播报开关（SystemConfig；缺行=开，与私聊到货默认开同口径）
KEY_GROUP_ARRIVAL = "tg_notify_group_arrival_enabled"
#: 新贡献者欢迎开关（SystemConfig；缺行=开）
KEY_GROUP_POOL_WELCOME = "tg_notify_group_pool_welcome_enabled"

KIND_GROUP_ARRIVAL = "group_store_arrival"
KIND_GROUP_LEVEL_UP = "group_level_up"
KIND_GROUP_POOL_WELCOME = "group_pool_welcome"

#: 消息里只提站名，不贴链接（与群公告口径一致）
SITE_NAME = "www.aetrix.love"

_INFO_KEY = "group_cheer_pending"
_TRUE = frozenset({"1", "true", "yes", "on"})
_HOOK_INSTALLED = False


# ------------------------------------------------------------------ 钩子

def _install_commit_hook() -> None:
    """排队项随业务事务提交成功后再发送；回滚直接丢弃。"""
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    _HOOK_INSTALLED = True

    @event.listens_for(Session, "after_commit")
    def _after_commit(session):
        info = getattr(session, "info", None)
        if info is None:
            return
        pending = info.pop(_INFO_KEY, None)
        if not pending:
            return
        for item in pending:
            try:
                _dispatch(session.get_bind(), item)
            except Exception:
                logger.warning("群氛围播报发送失败 item=%s", item,
                               exc_info=True)

    @event.listens_for(Session, "after_rollback")
    def _after_rollback(session):
        info = getattr(session, "info", None)
        if info is not None:
            info.pop(_INFO_KEY, None)


_install_commit_hook()


def _enqueue(db: Session, item: tuple) -> None:
    try:
        db.info.setdefault(_INFO_KEY, []).append(item)
    except Exception:
        logger.warning("群氛围播报排队失败 item=%s", item, exc_info=True)


def _dispatch(bind, item: tuple) -> None:
    """提交后派发：原会话在 after_commit 里处于 committed 态不能再发
    SQL，故按原会话的 bind 新开一个会话发送（与业务事务同库：隔离
    测试库、worker 自带会话都成立；不走全局 SessionLocal）。"""
    from sqlalchemy.orm import sessionmaker

    db = sessionmaker(bind=bind)()
    try:
        kind = item[0]
        if kind == "arrival":
            send_store_arrival_cheer(db, item[1])
        elif kind == "level_up":
            _, user_id, old_level, new_level = item
            send_level_up_cheer(db, user_id, old_level, new_level)
    finally:
        db.close()


# ------------------------------------------------------------------ 配置

def _config_on(db: Session, key: str, default: bool = True) -> bool:
    """缺行按 default；读失败按 default（与 proactive._config_on 同口径）。"""
    try:
        row = (
            db.query(models.SystemConfig)
            .filter(models.SystemConfig.key == key)
            .first()
        )
    except Exception:
        logger.warning("群氛围播报开关读取失败 key=%s", key, exc_info=True)
        return default
    if row is None:
        return default
    return str(row.value or "").strip().lower() in _TRUE


def group_arrival_enabled(db: Session) -> bool:
    return _config_on(db, KEY_GROUP_ARRIVAL, default=True)


def pool_welcome_enabled(db: Session) -> bool:
    return _config_on(db, KEY_GROUP_POOL_WELCOME, default=True)


def target_group_id(db: Session) -> int | None:
    """推送目标＝bot_group_ids 的第一个群（与排行榜同口径，取最小保确定性）。"""
    try:
        raw = _store.read_value(db, "bot_group_ids", "") or ""
    except Exception:
        logger.warning("群氛围播报读取 bot_group_ids 失败", exc_info=True)
        return None
    groups = policy.parse_group_ids(raw)
    return min(groups) if groups else None


# ------------------------------------------------------------------ 幂等锚

def _claim(db: Session, kind: str, subject_id: int, user_id: int,
           title: str) -> bool:
    """插入一行终态台账锚。已有行（含并发同插）返回 False，不抛。

    用保存点，撞唯一约束只回滚这一插，不冲掉外层事务。
    """
    row = models.TgProactiveNotice(
        kind=kind,
        subject_id=int(subject_id),
        user_id=int(user_id),
        title=(title or "")[:255],
        detail="",
        status="sent",
        attempts=0,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        _drop_row(db, row)
        return False
    except Exception:
        _drop_row(db, row)
        logger.warning("群氛围播报占锚失败 kind=%s subject=%s",
                       kind, subject_id, exc_info=True)
        return False
    return True


def _drop_row(db: Session, row) -> None:
    try:
        if row in db:
            db.expunge(row)
    except Exception:
        pass


def _unclaim(db: Session, kind: str, subject_id: int, user_id: int) -> None:
    """发送失败后删锚，让后续触发还能重试（失败不占坑）。"""
    try:
        (db.query(models.TgProactiveNotice)
         .filter(models.TgProactiveNotice.kind == kind,
                 models.TgProactiveNotice.subject_id == int(subject_id),
                 models.TgProactiveNotice.user_id == int(user_id))
         .delete(synchronize_session=False))
        db.commit()
    except Exception:
        logger.warning("群氛围播报删锚失败 kind=%s subject=%s",
                       kind, subject_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


# ------------------------------------------------------------------ 共用发送

def send_group_cheer(db: Session, *, kind: str, subject_id: int,
                     claim_user_id: int, title: str, text: str) -> bool:
    """占锚 → 发群 → 成功落锚 / 失败删锚。返回是否实际发出了。

    未配群（bot_group_ids 为空）时不占锚、静默跳过：日后配上群，
    同一事件若再被触发仍可补发。发送异常只记日志、返回 False。
    """
    group_id = target_group_id(db)
    if group_id is None:
        logger.info("群氛围播报未配置交流群（bot_group_ids），跳过 kind=%s",
                    kind)
        return False
    if not _claim(db, kind, subject_id, claim_user_id, title):
        return False
    try:
        ok, err = sender.send_message(db, group_id, text, parse_mode=None)
    except Exception:
        logger.warning("群氛围播报发送异常 kind=%s subject=%s",
                       kind, subject_id, exc_info=True)
        ok, err = False, "exception"
    if not ok:
        logger.warning("群氛围播报发送失败 kind=%s subject=%s err=%s",
                       kind, subject_id, err)
        _unclaim(db, kind, subject_id, claim_user_id)
        return False
    db.commit()
    return True


# ------------------------------------------------------------------ 到货报喜

def queue_store_arrival_cheer(db: Session, request_row) -> bool:
    """到货收口排队（proactive.note_store_request_completed 调用）。

    只排队不发送：真正发送在业务事务提交后。开关关着不排队。
    调用方负责包 try/except；本函数自身异常也不外抛给到货主流程。
    """
    if getattr(request_row, "status", "") != "completed":
        return False
    request_id = getattr(request_row, "id", None)
    if request_id is None:
        return False
    if not group_arrival_enabled(db):
        return False
    _enqueue(db, ("arrival", int(request_id)))
    return True


def send_store_arrival_cheer(db: Session, request_id: int) -> bool:
    """给已到货的心愿行发群报喜。同一行只发一次（锚），多人附议合并一条。"""
    if not group_arrival_enabled(db):
        return False
    row = (db.query(models.MovieRequest)
           .filter(models.MovieRequest.id == int(request_id)).first())
    if row is None or row.status != "completed":
        return False
    owner = (db.query(models.WebUser)
             .filter(models.WebUser.id == row.user_id).first())
    owner_name = (getattr(owner, "username", None) or "").strip() or "有位朋友"
    title = (row.movie_name or "").strip() or "这部片"
    voter_ids = {
        int(uid) for (uid,) in (
            db.query(models.MovieRequestVote.user_id)
            .filter(models.MovieRequestVote.request_id == row.id)
            .all()
        ) if uid is not None
    }
    others = len(voter_ids - {int(row.user_id)})
    if others > 0:
        text = (
            f"🎉 心愿达成！{owner_name} 许的《{title}》到货啦，"
            f"还有 {others} 位朋友也一起许了这部——"
            f"打开 {SITE_NAME} 就能看，恭喜大家！"
        )
    else:
        text = (
            f"🎉 心愿达成！{owner_name} 许的《{title}》到货啦——"
            f"打开 {SITE_NAME} 就能看，恭喜！"
        )
    return send_group_cheer(
        db, kind=KIND_GROUP_ARRIVAL, subject_id=int(row.id),
        claim_user_id=int(row.user_id), title=title, text=text)


# ------------------------------------------------------------------ 升级祝贺

def queue_level_up_cheer(db: Session, user_id: int, old_level: int,
                         new_level: int) -> bool:
    """升级收口排队（原由 member_level.add_xp 跨级时调用；等级体系已于
    2026-11-14 删除，现无自动调用方，函数保留供手动触发）。只报升级。"""
    if int(new_level) <= int(old_level):
        return False
    _enqueue(db, ("level_up", int(user_id), int(old_level), int(new_level)))
    return True


# 等级名兜底（原 member_level.DEFAULT_LEVELS 的名称部分，等级体系于
# 2026-11-14 删除后就地保留为只读展示常量）
_DEFAULT_LEVEL_NAMES = {1: "初幕", 2: "影迷", 3: "鉴赏家",
                        4: "放映师", 5: "造梦者", 6: "传奇"}


def _level_name(db: Session, level: int) -> str:
    row = (db.query(models.MemberLevel)
           .filter(models.MemberLevel.level == int(level)).first())
    name = (getattr(row, "name", None) or "").strip() if row is not None else ""
    if name:
        return name
    return _DEFAULT_LEVEL_NAMES.get(int(level), "")


def send_level_up_cheer(db: Session, user_id: int, old_level: int,
                        new_level: int) -> bool:
    """给一次跨级发群祝贺。同一用户同一新等级只发一次（锚）。"""
    if int(new_level) <= int(old_level):
        return False
    user = (db.query(models.WebUser)
            .filter(models.WebUser.id == int(user_id)).first())
    if user is None:
        return False
    username = (user.username or "").strip() or "有位朋友"
    name = _level_name(db, new_level)
    name_part = f"「{name}」" if name else ""
    text = (
        f"🎉 恭喜 {username} 升级！从 Lv.{int(old_level)} 升到 "
        f"Lv.{int(new_level)}{name_part}，又向前一步啦！"
    )
    return send_group_cheer(
        db, kind=KIND_GROUP_LEVEL_UP, subject_id=int(new_level),
        claim_user_id=int(user_id), title=f"Lv.{int(new_level)}", text=text)


# ------------------------------------------------------------------ 新贡献者欢迎

def send_pool_welcome(db: Session, member) -> bool:
    """池成员审核通过后，群里欢迎新贡献者一条。

    审核收口（pool_admin._notify_member_review）在业务事务已提交后
    同步调用（与站内信同一触发点）。文案只提站内用户名与奖励口径，
    不带盘标签/容量/绑定信息、不贴链接。锚＝(group_pool_welcome,
    成员行 id, 贡献者 id)，同一成员只欢迎一次；驳回不走本函数。
    """
    if not pool_welcome_enabled(db):
        return False
    member_id = getattr(member, "id", None)
    user_id = getattr(member, "user_id", None)
    if member_id is None or user_id is None:
        return False
    user = (db.query(models.WebUser)
            .filter(models.WebUser.id == int(user_id)).first())
    if user is None:
        return False
    username = (user.username or "").strip() or "有位朋友"
    text = (
        f"🎉 欢迎 {username} 加入公共片库，成为新的贡献者！"
        "贡献有回报：被看一次 +1 分、在线一天 +5 分，每月最高 900 分，"
        "还有专属称号「片库贡献者」。谢谢分享！"
    )
    return send_group_cheer(
        db, kind=KIND_GROUP_POOL_WELCOME, subject_id=int(member_id),
        claim_user_id=int(user_id), title=username, text=text)
