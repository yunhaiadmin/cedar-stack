# -*- coding: utf-8 -*-
"""盲盒引擎（双服通用新功能，2026-11-11 用户亲定，方案 v2 修订）。

口径（方案第十四节 v2，覆盖前文冲突处）：
- 通用能力：一套引擎服务所有服（server_realms），档位、奖池、
  上限、预算、库存、抽盒记录全部按 realm_id 分开配置与记账；
  公益服（access_mode=free，realms.is_free_realm 单口判定）先上线，
  付费服由管理员照配即用，零代码改动。
- 奖品分两类：通用奖 points / access_days / reg_code / sub_code；
  公益专属奖 vitality / whitelist——保存奖池时非公益 realm 选了
  专属奖直接拒绝（validate_realm_config）。
- access_days（天数奖）单一适配口发放：公益服走
  portal.grant_welfare(channel="blindbox")；付费服有在期订阅
  （subscriptions.active_subscription）时走
  codes.grant_membership_days 顺延，无在期订阅走档位兜底奖
  （fallback_prize，如订阅码/等值积分），不许吞奖、不新建订阅。
- 发放只许调用现有通道：积分 economy._spend_points/_add_points
  （台账 type=blindbox_spend/blindbox_prize）、活力
  vitality._add_vitality、卡码 codes.render_code + RegistrationCode
  行（source="blindbox"）。本模块不新造第二套账。
- 一抽一事务：定奖（含库存/预算复核）→扣分→发放→落
  BlindboxDraw(delivered) 同事务提交；中途失败整体回滚不扣分，
  只补一条 status=failed、charged=False 的审计行。charged=True
  未发放（delivery_failed）的记录可经 redeliver 补发（幂等、
  不重复扣分）。grant_welfare 是全站唯一自提交发放口，必须在本
  抽其它写都 flush 后最后调用，借它的 commit 落整抽。
- 概率以权重表达、合计自动归一；抽取用 secrets 随机源。
  期望返还率按可配估值表（blindbox_prize_values）折算，保存时
  >100% 拒绝。库存/预算/上限计数全部从 blindbox_draws 聚合，
  不另建计数表。每日口径按服务器本地日界。
- 总开关 blindbox_enabled 默认关；活动时间窗、每人每日消费
  上限、资格类奖品（access_days）每日预算、白名单库存总量都
  在 realm 配置内，0/空 = 不限制（总开关与 realm 开关除外）。
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, time as dtime, timedelta

from sqlalchemy.orm import Session

from backend import models, realms, subscriptions
from backend.integrations import store as config_store

logger = logging.getLogger(__name__)


class BlindboxError(Exception):
    """盲盒业务错误（配置非法 / 停售 / 上限 / 发放失败等）。"""


# ---------------------------------------------------------------------------
# 配置（家族 blindbox：master 开关 + 按 realm 的 JSON 文档 + 估值表）
# ---------------------------------------------------------------------------

PRIZE_KINDS = ("points", "vitality", "access_days",
               "reg_code", "sub_code", "whitelist")
WELFARE_ONLY_KINDS = ("vitality", "whitelist")
# 兜底奖不许再是库存奖或天数奖（否则会连环改发/吞奖）。
FALLBACK_FORBIDDEN_KINDS = ("whitelist", "access_days")

PRIZE_VALUES_DEFAULTS: dict = {
    # 活力 10 分/点（现行兑换口径）；资格天数按公益续期口径折算
    # （7 天 150 / 30 天 450 / 90 天约 1200 / 365 天约 4000），
    # 其它天数按 access_day_rate 线性折算；白名单记账值 3000。
    "vitality_per_point": 10,
    "access_day_values": {"7": 150, "30": 450, "90": 1200, "365": 4000},
    "access_day_rate": 15,
    "whitelist": 3000,
}

_REALM_CFG_DEFAULTS: dict = {
    "enabled": False,
    "starts_at": "",
    "ends_at": "",
    "daily_spend_cap": 0,
    "access_days_daily_budget": 0,
    "whitelist_stock_total": 0,
    "tiers": [],
}


def _raw_values(db: Session) -> dict:
    defaults = {
        "blindbox_enabled": "false",
        "blindbox_realms": "",
        "blindbox_prize_values": "",
    }
    try:
        return config_store.get_values(db, list(defaults), defaults)
    except Exception:  # noqa: BLE001 配置层异常回落默认（全关）
        logger.warning("盲盒配置读取失败，回落默认（关闭）", exc_info=True)
        return defaults


def master_enabled(db: Session) -> bool:
    return str(_raw_values(db).get("blindbox_enabled", "")).strip().lower() == "true"


def save_master_switch(db: Session, enabled: bool) -> None:
    from backend import config_service

    config_service.save_config(
        db, {"blindbox_enabled": bool(enabled)}, schema="blindbox",
        descriptions={"blindbox_enabled": "盲盒全站总开关"})
    config_store.invalidate()


def _realms_doc(db: Session) -> dict:
    """按 realm 分开的配置文档（直读 SystemConfig，不走缓存——
    保存路径要读到最新值做合并）。"""
    row = (db.query(models.SystemConfig)
           .filter(models.SystemConfig.key == "blindbox_realms").first())
    if row is None or not str(row.value or "").strip():
        return {}
    try:
        doc = json.loads(row.value)
    except (TypeError, ValueError):
        logger.warning("blindbox_realms 配置不是合法 JSON，按空处理")
        return {}
    return doc if isinstance(doc, dict) else {}


def get_realm_config(db: Session, realm_id: int) -> dict:
    doc = _realms_doc(db)
    raw = doc.get(str(realm_id)) or {}
    cfg = dict(_REALM_CFG_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


def prize_values(db: Session) -> dict:
    raw = str(_raw_values(db).get("blindbox_prize_values", "") or "").strip()
    values = dict(PRIZE_VALUES_DEFAULTS)
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            values.update(parsed)
    return values


def save_prize_values(db: Session, values: dict) -> dict:
    if not isinstance(values, dict):
        raise BlindboxError("估值表必须是 JSON 对象")
    merged = dict(PRIZE_VALUES_DEFAULTS)
    merged.update(values)
    for key in ("vitality_per_point", "access_day_rate", "whitelist"):
        try:
            merged[key] = int(merged[key])
        except (TypeError, ValueError):
            raise BlindboxError(f"估值表 {key} 必须是整数")
        if merged[key] < 0:
            raise BlindboxError(f"估值表 {key} 不许为负")
    day_values = merged.get("access_day_values") or {}
    if not isinstance(day_values, dict):
        raise BlindboxError("估值表 access_day_values 必须是对象")
    cleaned = {}
    for days, val in day_values.items():
        try:
            cleaned[str(int(days))] = int(val)
        except (TypeError, ValueError):
            raise BlindboxError("估值表 access_day_values 的键/值必须是整数")
    merged["access_day_values"] = cleaned
    from backend import config_service

    config_service.save_config(
        db, {"blindbox_prize_values": json.dumps(merged, ensure_ascii=False)},
        schema="blindbox",
        descriptions={"blindbox_prize_values": "盲盒奖品积分等值估值表"})
    config_store.invalidate()
    return merged


def prize_value(db: Session, kind: str, params: dict | None) -> int:
    """单个奖品按当前估值表折算的积分等值。"""
    params = params or {}
    values = prize_values(db)
    if kind == "points":
        return int(params.get("amount") or 0)
    if kind == "vitality":
        return int(params.get("amount") or 0) * int(values["vitality_per_point"])
    if kind in ("access_days", "reg_code", "sub_code"):
        days = int(params.get("days") or 0)
        table = values.get("access_day_values") or {}
        if str(days) in table:
            return int(table[str(days)])
        return days * int(values["access_day_rate"])
    if kind == "whitelist":
        return int(values["whitelist"])
    return 0


# ---------------------------------------------------------------------------
# 奖池校验与期望返还率
# ---------------------------------------------------------------------------

def _check_prize(prize, *, is_free: bool, is_fallback: bool, where: str) -> dict:
    if not isinstance(prize, dict):
        raise BlindboxError(f"{where}：奖品必须是对象")
    kind = str(prize.get("kind") or "")
    if kind not in PRIZE_KINDS:
        raise BlindboxError(f"{where}：未知奖品类型 {kind!r}")
    if kind in WELFARE_ONLY_KINDS and not is_free:
        raise BlindboxError(
            f"{where}：{kind} 是公益专属奖品，非公益服奖池不许选用")
    if is_fallback and kind in FALLBACK_FORBIDDEN_KINDS:
        raise BlindboxError(
            f"{where}：兜底奖不许是 {kind}（库存奖/天数奖会连环改发）")
    try:
        weight = float(prize.get("weight") or 0)
    except (TypeError, ValueError):
        raise BlindboxError(f"{where}：权重必须是数字")
    if not is_fallback and weight <= 0:
        raise BlindboxError(f"{where}：权重必须为正")
    params = prize.get("params") or {}
    if not isinstance(params, dict):
        raise BlindboxError(f"{where}：params 必须是对象")
    params = dict(params)
    if kind in ("points", "vitality"):
        try:
            amount = int(params.get("amount") or 0)
        except (TypeError, ValueError):
            raise BlindboxError(f"{where}：amount 必须是整数")
        if amount <= 0:
            raise BlindboxError(f"{where}：amount 必须为正")
        params["amount"] = amount
    elif kind in ("access_days", "reg_code", "sub_code"):
        try:
            days = int(params.get("days") or 0)
        except (TypeError, ValueError):
            raise BlindboxError(f"{where}：days 必须是整数")
        if days <= 0:
            raise BlindboxError(f"{where}：days 必须为正")
        params["days"] = days
    return {"kind": kind, "weight": weight, "params": params}


def _validate_tier(db: Session, tier, *, is_free: bool, seen_keys: set) -> dict:
    if not isinstance(tier, dict):
        raise BlindboxError("档位必须是对象")
    key = str(tier.get("key") or "").strip()
    if not key:
        raise BlindboxError("档位 key 不能为空")
    if key in seen_keys:
        raise BlindboxError(f"档位 key 重复：{key}")
    seen_keys.add(key)
    try:
        price = int(tier.get("price") or 0)
    except (TypeError, ValueError):
        raise BlindboxError(f"档位 {key}：售价必须是整数")
    if price <= 0:
        raise BlindboxError(f"档位 {key}：售价必须为正")
    prizes_raw = tier.get("prizes") or []
    if not isinstance(prizes_raw, list) or not prizes_raw:
        raise BlindboxError(f"档位 {key}：奖池不能为空")
    prizes = [
        _check_prize(p, is_free=is_free, is_fallback=False,
                     where=f"档位 {key} 奖池")
        for p in prizes_raw
    ]
    fallback_raw = tier.get("fallback_prize")
    if not fallback_raw:
        raise BlindboxError(f"档位 {key}：必须配置兜底奖 fallback_prize")
    fallback = _check_prize(fallback_raw, is_free=is_free, is_fallback=True,
                            where=f"档位 {key} 兜底奖")
    fallback["weight"] = 1
    normalized = {
        "key": key,
        "name": str(tier.get("name") or key),
        "price": price,
        "enabled": bool(tier.get("enabled", True)),
        "sort": int(tier.get("sort") or 0),
        "fallback_prize": fallback,
        "prizes": prizes,
    }
    rate = expected_return_rate(db, normalized)
    if rate > 1.0:
        raise BlindboxError(
            f"档位 {key}：期望返还率 {rate:.1%} 超过 100%，拒绝保存")
    normalized["expected_return_rate"] = round(rate, 4)
    return normalized


def expected_return_rate(db: Session, tier: dict) -> float:
    """期望返还率 = Σ(权重占比 × 奖品积分等值) / 售价（兜底奖是
    条件改发，不计入期望）。"""
    price = int(tier.get("price") or 0)
    if price <= 0:
        return 0.0
    prizes = tier.get("prizes") or []
    total_weight = sum(float(p.get("weight") or 0) for p in prizes)
    if total_weight <= 0:
        return 0.0
    expect = sum(
        float(p.get("weight") or 0) / total_weight
        * prize_value(db, str(p.get("kind") or ""), p.get("params") or {})
        for p in prizes
    )
    return expect / price


def validate_realm_config(db: Session, realm_id: int, cfg: dict) -> dict:
    """校验并归一化一个 realm 的盲盒配置（保存与接口共用这一份，
    不许两处各校验一遍）。"""
    realm = db.query(models.ServerRealm).filter(
        models.ServerRealm.id == realm_id).first()
    if realm is None:
        raise BlindboxError(f"服不存在：{realm_id}")
    if not isinstance(cfg, dict):
        raise BlindboxError("配置必须是对象")
    is_free = realms.is_free_realm(db, realm_id)
    out = dict(_REALM_CFG_DEFAULTS)
    out["enabled"] = bool(cfg.get("enabled", False))
    for key in ("starts_at", "ends_at"):
        value = str(cfg.get(key) or "").strip()
        if value:
            try:
                datetime.fromisoformat(value)
            except ValueError:
                raise BlindboxError(f"{key} 必须是 ISO 时间（如 2026-11-11T20:00:00）")
        out[key] = value
    for key in ("daily_spend_cap", "access_days_daily_budget",
                "whitelist_stock_total"):
        try:
            num = int(cfg.get(key) or 0)
        except (TypeError, ValueError):
            raise BlindboxError(f"{key} 必须是整数")
        if num < 0:
            raise BlindboxError(f"{key} 不许为负")
        out[key] = num
    tiers_raw = cfg.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise BlindboxError("tiers 必须是数组")
    seen: set = set()
    out["tiers"] = [
        _validate_tier(db, t, is_free=is_free, seen_keys=seen)
        for t in tiers_raw
    ]
    return out


def save_realm_config(db: Session, realm_id: int, cfg: dict) -> dict:
    """校验后把该 realm 的配置并入 blindbox_realms 文档落库
    （不提交，调用方统一 commit；config_service 本层也不提交）。"""
    normalized = validate_realm_config(db, realm_id, cfg)
    doc = _realms_doc(db)
    doc[str(realm_id)] = normalized
    from backend import config_service

    config_service.save_config(
        db, {"blindbox_realms": json.dumps(doc, ensure_ascii=False)},
        schema="blindbox",
        descriptions={"blindbox_realms": "盲盒按服配置（档位/奖池/上限）"})
    config_store.invalidate()
    return normalized


# ---------------------------------------------------------------------------
# 抽取（服务端权重随机，合计自动归一）
# ---------------------------------------------------------------------------

def pick_prize_index(prizes: list) -> int:
    weights = [float(p.get("weight") or 0) for p in prizes]
    total = sum(weights)
    if total <= 0:
        raise BlindboxError("奖池权重合计必须为正")
    x = (secrets.randbelow(10 ** 12) / 10 ** 12) * total
    acc = 0.0
    for idx, w in enumerate(weights):
        acc += w
        if x < acc:
            return idx
    return len(weights) - 1


# ---------------------------------------------------------------------------
# 计数口径（全部从 blindbox_draws 聚合）
# ---------------------------------------------------------------------------

def _today_start() -> datetime:
    return datetime.combine(datetime.now().date(), dtime.min)


def _window_start(cfg: dict) -> datetime:
    raw = str(cfg.get("starts_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.min


def _user_spent_today(db: Session, user_id: int, realm_id: int) -> int:
    rows = (db.query(models.BlindboxDraw)
            .filter(models.BlindboxDraw.user_id == user_id,
                    models.BlindboxDraw.realm_id == realm_id,
                    models.BlindboxDraw.charged.is_(True),
                    models.BlindboxDraw.status.in_(
                        ("delivered", "delivery_failed")),
                    models.BlindboxDraw.created_at >= _today_start())
            .all())
    return sum(int(r.price or 0) for r in rows)


def _access_days_used_today(db: Session, realm_id: int) -> int:
    rows = (db.query(models.BlindboxDraw)
            .filter(models.BlindboxDraw.realm_id == realm_id,
                    models.BlindboxDraw.prize_kind == "access_days",
                    models.BlindboxDraw.status == "delivered",
                    models.BlindboxDraw.created_at >= _today_start())
            .all())
    return sum(int((r.prize_params or {}).get("days") or 0) for r in rows)


def _whitelist_used(db: Session, realm_id: int, cfg: dict) -> int:
    return (db.query(models.BlindboxDraw)
            .filter(models.BlindboxDraw.realm_id == realm_id,
                    models.BlindboxDraw.prize_kind == "whitelist",
                    models.BlindboxDraw.status == "delivered",
                    models.BlindboxDraw.created_at >= _window_start(cfg))
            .count())


# ---------------------------------------------------------------------------
# 发放（只走现有通道）
# ---------------------------------------------------------------------------

def _issue_code(db: Session, user: models.WebUser, realm_id: int,
                kind: str, days: int, draw_key: str) -> str:
    from backend import codes

    if kind == "reg_code":
        code_type, reward_type = 1, "subscription"
    elif kind == "sub_code":
        code_type, reward_type = 2, "subscription"
    else:  # whitelist
        code_type, reward_type, days = 3, "welfare", -1
    raw = None
    for index in range(20):
        candidate = codes.render_code(
            code_type=code_type, days=days, index=index)
        if not db.query(models.RegistrationCode).filter(
                models.RegistrationCode.code == candidate).first():
            raw = candidate
            break
    if raw is None:
        raise BlindboxError("卡码生成冲突，发放失败")
    db.add(models.RegistrationCode(
        code=raw, max_uses=1, use_count=0, is_active=True,
        note=f"盲盒奖品 draw:{draw_key}", created_by=user.id,
        code_type=code_type, days=days, source="blindbox",
        realm_id=realm_id, reward_type=reward_type,
        points_value=0, discount_pct=0, welfare_include_view=False,
    ))
    db.flush()
    return raw


def _deliver(db: Session, user: models.WebUser, realm_id: int,
             kind: str, params: dict, draw_key: str) -> dict:
    """发放一个奖品（access_days 公益分支除外——grant_welfare 自
    提交，须由 draw/redeliver 最后单独走）。返回发放结果快照。"""
    from backend.api import economy

    ref = f"blindbox:{draw_key}"
    if kind == "points":
        amount = int(params.get("amount") or 0)
        economy._add_points(db, user, amount, "blindbox_prize",
                            "盲盒奖品：积分", ref)
        db.flush()
        return {"amount": amount}
    if kind == "vitality":
        from backend import vitality

        amount = int(params.get("amount") or 0)
        vitality._add_vitality(db, user.id, amount, "blindbox")
        db.flush()
        return {"amount": amount}
    if kind in ("reg_code", "sub_code", "whitelist"):
        code = _issue_code(db, user, realm_id, kind,
                           int(params.get("days") or 30), draw_key)
        return {"code": code}
    if kind == "access_days":
        # 付费服顺延（调用前已确认有在期订阅，否则已改发兜底）。
        from backend import codes

        days = int(params.get("days") or 0)
        sub = codes.grant_membership_days(db, user, days, realm_id=realm_id)
        return {"days": days,
                "subscription_id": sub.id,
                "end_date": sub.end_date.isoformat() if sub.end_date else None}
    raise BlindboxError(f"未知奖品类型：{kind}")


def _deliver_welfare_days(db: Session, user: models.WebUser,
                          record: models.BlindboxDraw, days: int) -> None:
    """公益 access_days：grant_welfare 自带 commit，是本抽的提交
    点——调用前 record 必须已置 delivered 并 flush，grant 的提交
    会把扣分+记录一并落盘；成功后再补一条到期快照并 commit。"""
    from backend.emby_server import portal

    new_exp = portal.grant_welfare(db, user, "blindbox", days)
    record.delivery = {
        "days": days,
        "channel": "blindbox",
        "welfare_expires_at": (new_exp.isoformat()
                               if isinstance(new_exp, datetime) else None),
    }
    db.commit()


def _resolve_effective_prize(db: Session, user: models.WebUser,
                             realm_id: int, cfg: dict, tier: dict,
                             picked: dict, is_free: bool):
    """库存/预算/无订阅复核：命中不可发放情形时改发档位兜底奖，
    返回 (kind, params, fallback_from)。"""
    kind = str(picked.get("kind") or "")
    params = dict(picked.get("params") or {})
    fallback = tier["fallback_prize"]

    def use_fallback():
        return (str(fallback["kind"]), dict(fallback.get("params") or {}), kind)

    if kind == "whitelist":
        total = int(cfg.get("whitelist_stock_total") or 0)
        # 库存复核要串行：锁住 realm 行，防并发双抽同一份库存。
        (db.query(models.ServerRealm)
         .filter(models.ServerRealm.id == realm_id)
         .with_for_update().first())
        if total <= 0 or _whitelist_used(db, realm_id, cfg) >= total:
            return use_fallback()
    elif kind == "access_days":
        days = int(params.get("days") or 0)
        budget = int(cfg.get("access_days_daily_budget") or 0)
        if budget > 0 and _access_days_used_today(db, realm_id) + days > budget:
            return use_fallback()
        if not is_free and subscriptions.active_subscription(
                db, user.id, realm_id) is None:
            return use_fallback()
    return kind, params, None


# ---------------------------------------------------------------------------
# 抽盒主流程
# ---------------------------------------------------------------------------

def _parse_dt(value: str):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _activity_open(cfg: dict) -> tuple[bool, str]:
    if not cfg.get("enabled"):
        return False, "该服盲盒未开启"
    now = datetime.now()
    starts = _parse_dt(cfg.get("starts_at", ""))
    ends = _parse_dt(cfg.get("ends_at", ""))
    if starts and now < starts:
        return False, "活动尚未开始"
    if ends and now > ends:
        return False, "活动已结束"
    return True, ""


def draw(db: Session, user: models.WebUser, realm_id: int,
         tier_key: str) -> dict:
    """抽一盒：一抽一事务（定奖→扣分→发放→落账原子完成）。"""
    from backend.api import economy

    if not master_enabled(db):
        raise BlindboxError("盲盒活动未开启")
    cfg = get_realm_config(db, realm_id)
    open_ok, why = _activity_open(cfg)
    if not open_ok:
        raise BlindboxError(why)
    tier = next((t for t in cfg.get("tiers") or []
                 if t.get("key") == tier_key and t.get("enabled", True)), None)
    if tier is None:
        raise BlindboxError("档位不存在或已下架")
    is_free = realms.is_free_realm(db, realm_id)
    price = int(tier["price"])

    # 用户行锁：把「读今日已消费→判上限→扣分」串行化（economy 口径）。
    economy.lock_user_row(db, user.id)
    cap = int(cfg.get("daily_spend_cap") or 0)
    if cap > 0 and _user_spent_today(db, user.id, realm_id) + price > cap:
        db.rollback()
        raise BlindboxError("已达今日盲盒消费上限")

    picked = tier["prizes"][pick_prize_index(tier["prizes"])]
    kind, params, fallback_from = _resolve_effective_prize(
        db, user, realm_id, cfg, tier, picked, is_free)

    draw_key = uuid.uuid4().hex
    record = models.BlindboxDraw(
        draw_key=draw_key, user_id=user.id, realm_id=realm_id,
        tier_key=tier["key"], tier_name=tier.get("name") or tier["key"],
        price=price, prize_kind=kind, prize_params=params,
        prize_value=prize_value(db, kind, params),
        fallback_from=fallback_from,
        status="delivery_failed", charged=True, delivery={},
    )
    try:
        db.add(record)
        db.flush()
        economy._spend_points(db, user, price, "blindbox_spend",
                              f"盲盒抽盒：{record.tier_name}",
                              f"blindbox:{draw_key}")
        if kind == "access_days" and is_free:
            record.status = "delivered"
            record.delivered_at = datetime.now()
            record.delivery = {"days": int(params.get("days") or 0)}
            db.flush()
            _deliver_welfare_days(db, user, record,
                                  int(params.get("days") or 0))
        else:
            record.delivery = _deliver(db, user, realm_id, kind, params,
                                        draw_key)
            record.status = "delivered"
            record.delivered_at = datetime.now()
            db.commit()
    except Exception as exc:  # noqa: BLE001 发放任一步失败：整抽回滚
        db.rollback()
        _write_failed_audit(db, user, realm_id, tier, kind, params,
                            fallback_from, exc)
        if isinstance(exc, BlindboxError):
            raise
        raise BlindboxError(f"抽盒失败，未扣分：{exc}") from exc
    # 大奖群报喜（best effort：maybe_announce 自吞异常，这里再兜
    # 一层导入失败；报喜成败绝不影响抽盒结果与落账）。
    try:
        from backend import blindbox_announce

        blindbox_announce.maybe_announce(
            db, user, realm_id, record.tier_name, kind, params, cfg=cfg)
    except Exception:  # noqa: BLE001
        logger.warning("盲盒大奖报喜调用失败（已忽略）", exc_info=True)
    balance = (db.query(models.WebUser.points)
               .filter(models.WebUser.id == user.id).scalar())
    return {
        "draw_id": record.id,
        "draw_key": draw_key,
        "tier_key": tier["key"],
        "price": price,
        "prize": _prize_view(record),
        "balance": balance,
    }


def _write_failed_audit(db: Session, user, realm_id: int, tier: dict,
                        kind: str, params: dict, fallback_from,
                        exc: Exception) -> None:
    """整抽回滚后补一条未扣分的失败审计行（best effort，写失败
    只记日志，不许把原始错误盖掉）。"""
    try:
        db.add(models.BlindboxDraw(
            draw_key=uuid.uuid4().hex, user_id=user.id, realm_id=realm_id,
            tier_key=tier.get("key"), tier_name=tier.get("name"),
            price=int(tier.get("price") or 0),
            prize_kind=kind, prize_params=params, prize_value=0,
            fallback_from=fallback_from,
            status="failed", charged=False, delivery={},
            error=str(exc)[:500],
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("盲盒失败审计行写入失败", exc_info=True)


def _prize_view(record: models.BlindboxDraw) -> dict:
    view = {
        "kind": record.prize_kind,
        "params": record.prize_params or {},
        "value": record.prize_value,
    }
    delivery = record.delivery or {}
    if delivery.get("code"):
        view["code"] = delivery["code"]
    if record.fallback_from:
        view["fallback_from"] = record.fallback_from
    return view


def redeliver(db: Session, draw_id: int) -> dict:
    """补发一条 charged=True 未发放的记录：只补奖、不再扣分；
    已发放的重复调用直接回现状（幂等）。"""
    record = db.query(models.BlindboxDraw).filter(
        models.BlindboxDraw.id == draw_id).first()
    if record is None:
        raise BlindboxError("抽盒记录不存在")
    if record.status == "delivered":
        return {"draw_id": record.id, "status": "delivered",
                "prize": _prize_view(record)}
    if record.status != "delivery_failed" or not record.charged:
        raise BlindboxError("该记录未扣分或状态不可补发")
    user = db.query(models.WebUser).filter(
        models.WebUser.id == record.user_id).first()
    if user is None:
        raise BlindboxError("中奖用户不存在")
    is_free = realms.is_free_realm(db, record.realm_id)
    params = dict(record.prize_params or {})
    try:
        if record.prize_kind == "access_days" and is_free:
            record.status = "delivered"
            record.delivered_at = datetime.now()
            record.delivery = {"days": int(params.get("days") or 0),
                               "redelivered": True}
            db.flush()
            _deliver_welfare_days(db, user, record,
                                  int(params.get("days") or 0))
            record.delivery = {**(record.delivery or {}),
                               "redelivered": True}
            db.commit()
        else:
            record.delivery = _deliver(db, user, record.realm_id,
                                       record.prize_kind, params,
                                       record.draw_key)
            record.status = "delivered"
            record.delivered_at = datetime.now()
            record.error = None
            db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise BlindboxError(f"补发失败：{exc}") from exc
    return {"draw_id": record.id, "status": record.status,
            "prize": _prize_view(record)}


# ---------------------------------------------------------------------------
# 读口：用户侧档位表 / 我的记录 / 我的卡包 / 统计
# ---------------------------------------------------------------------------

def public_tiers(db: Session, realm_id: int) -> dict:
    cfg = get_realm_config(db, realm_id)
    open_ok, _ = _activity_open(cfg)
    enabled = bool(master_enabled(db) and open_ok)
    tiers = []
    for tier in sorted(cfg.get("tiers") or [],
                       key=lambda t: (t.get("sort") or 0, t.get("key") or "")):
        if not tier.get("enabled", True):
            continue
        total = sum(float(p.get("weight") or 0) for p in tier["prizes"]) or 1.0
        tiers.append({
            "key": tier["key"],
            "name": tier.get("name") or tier["key"],
            "price": tier["price"],
            "expected_return_rate": round(
                expected_return_rate(db, tier), 4),
            "prizes": [
                {
                    "kind": p["kind"],
                    "params": p.get("params") or {},
                    "probability": float(p.get("weight") or 0) / total,
                    "value": prize_value(db, p["kind"], p.get("params") or {}),
                }
                for p in tier["prizes"]
            ],
            "fallback_prize": {
                "kind": tier["fallback_prize"]["kind"],
                "params": tier["fallback_prize"].get("params") or {},
            },
        })
    return {
        "realm_id": realm_id,
        "enabled": enabled,
        "starts_at": cfg.get("starts_at") or "",
        "ends_at": cfg.get("ends_at") or "",
        "daily_spend_cap": int(cfg.get("daily_spend_cap") or 0),
        "whitelist_stock_remaining": _whitelist_remaining(db, realm_id, cfg),
        "tiers": tiers,
    }


def _whitelist_remaining(db: Session, realm_id: int, cfg: dict):
    total = int(cfg.get("whitelist_stock_total") or 0)
    if total <= 0:
        return None
    return max(total - _whitelist_used(db, realm_id, cfg), 0)


def my_draws(db: Session, user: models.WebUser, realm_id: int | None = None,
             limit: int = 50, offset: int = 0):
    q = db.query(models.BlindboxDraw).filter(
        models.BlindboxDraw.user_id == user.id)
    if realm_id is not None:
        q = q.filter(models.BlindboxDraw.realm_id == realm_id)
    total = q.count()
    rows = (q.order_by(models.BlindboxDraw.id.desc())
            .offset(offset).limit(limit).all())
    return total, rows


def draw_view(record: models.BlindboxDraw) -> dict:
    return {
        "id": record.id,
        "draw_key": record.draw_key,
        "realm_id": record.realm_id,
        "tier_key": record.tier_key,
        "tier_name": record.tier_name,
        "price": record.price,
        "prize": _prize_view(record),
        "status": record.status,
        "charged": bool(record.charged),
        "created_at": (record.created_at.isoformat()
                       if record.created_at else None),
        "delivered_at": (record.delivered_at.isoformat()
                         if record.delivered_at else None),
    }


def my_card_pack(db: Session, user: models.WebUser) -> list:
    rows = (db.query(models.RegistrationCode)
            .filter(models.RegistrationCode.source == "blindbox",
                    models.RegistrationCode.created_by == user.id)
            .order_by(models.RegistrationCode.id.desc()).all())
    return [
        {
            "id": c.id,
            "code": c.code,
            "code_type": c.code_type,
            "days": c.days,
            "used": int(c.use_count or 0) >= int(c.max_uses or 1),
            "is_active": bool(c.is_active),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in rows
    ]


def draw_stats(db: Session, realm_id: int | None = None) -> dict:
    q = db.query(models.BlindboxDraw).filter(
        models.BlindboxDraw.status == "delivered")
    if realm_id is not None:
        q = q.filter(models.BlindboxDraw.realm_id == realm_id)
    rows = q.all()
    revenue = sum(int(r.price or 0) for r in rows)
    payout = sum(int(r.prize_value or 0) for r in rows)
    by_kind: dict = {}
    for r in rows:
        bucket = by_kind.setdefault(r.prize_kind, {"draws": 0, "value": 0})
        bucket["draws"] += 1
        bucket["value"] += int(r.prize_value or 0)
    failed = db.query(models.BlindboxDraw).filter(
        models.BlindboxDraw.status.in_(("failed", "delivery_failed")))
    if realm_id is not None:
        failed = failed.filter(models.BlindboxDraw.realm_id == realm_id)
    return {
        "realm_id": realm_id,
        "draws": len(rows),
        "revenue": revenue,
        "payout_value": payout,
        "actual_return_rate": (payout / revenue) if revenue else 0.0,
        "by_kind": by_kind,
        "failed_draws": failed.count(),
    }
