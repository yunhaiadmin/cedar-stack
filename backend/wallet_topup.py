# -*- coding: utf-8 -*-
"""钱包在线充值的汇率服务（钱包批 3）：自动抓市场价 + 缓存 + 换算。

汇率口径（人民币/美元，即多少人民币换 1 美元）：

- 市场价自动抓取：主源 Frankfurter（欧洲央行参考汇率，日更），
  备源 open.er-api.com（日更）；两源都免费免 key、都回 rates.CNY。
- 解析顺序（resolve_market_rate）：
  1. 手填强制覆盖开（wallet_topup_rate_manual_override）→ 手填键
     wallet_topup_cny_rate 的生效值（有库行用库行、无库行用出厂 7.2）；
  2. 自动缓存（wallet_topup_rate_auto）在 → 用缓存；缓存超 1 小时
     时懒刷新一次（短超时，失败不阻塞、用旧缓存）；
  3. 从没抓到过 → 当场抓一次；抓不到 → 回落手填键生效值。
- 生效汇率（effective_rate）＝ 市场价 × (1 + 缓冲%)，缓冲键
  wallet_topup_rate_buffer_pct（默认 2）；订单快照存生效汇率，
  下单后价格再变不影响已下单（见 models.WalletTopupOrder）。
- 抓价只在充值下单与钱包信息接口被调用时懒触发，不进任何热路径、
  不起常驻线程/定时器；缓存写 SystemConfig 两键
  （wallet_topup_rate_auto / wallet_topup_rate_fetched_at，机器内部
  键，不进管理面板）。

换算口径（compute_topup，全程 Decimal、整数分、ROUND_HALF_UP）：
充值按美元计价——用户选定美元额（美分），应付与到账都由它算：
应付人民币分 = 美元美分 × 生效汇率 × (1 + fee_pct%)（一次取整到分，
手续费加在应付侧）；到账美分 = 美元美分（全额到账，不扣费）。
例：$9.80 @7.344、费 0 → 应付 7197 分 = ¥71.97，到账 980 美分；
$10 @7.344、费 10% → 应付 8078 分 = ¥80.78，到账仍 1000 美分。

注意：resolve/effective 在刷新缓存时会写库并提交——调用方必须在
暂存其他写入之前调用（下单端点与钱包信息接口都满足）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from backend import wallet as wallet_service
from backend.integrations import store

logger = logging.getLogger(__name__)

#: 汇率源（主 → 备）：都回 JSON {"rates": {"CNY": <人民币/美元>}}
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY"
ERAPI_URL = "https://open.er-api.com/v6/latest/USD"
RATE_SOURCES = (FRANKFURTER_URL, ERAPI_URL)

#: 自动汇率缓存键（SystemConfig，机器内部键，不进管理面板/注册表）
CACHE_RATE_KEY = "wallet_topup_rate_auto"
CACHE_AT_KEY = "wallet_topup_rate_fetched_at"

#: 缓存新鲜期：超过 1 小时才懒刷新（两源都是日更参考价，足够充值场景）
CACHE_TTL_SECONDS = 3600
#: 抓价短超时：失败不阻塞下单/预览，用旧缓存或兜底价继续
FETCH_TIMEOUT_SECONDS = 3.0


# ==================== 抓取（测试全 mock 本层 _fetch_json） ====================

def _fetch_json(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> dict:
    import httpx

    resp = httpx.get(url, timeout=timeout,
                     headers={"User-Agent": "aetrix-wallet-topup/1.0"})
    resp.raise_for_status()
    return resp.json()


def _fetch_market_rate() -> Optional[Decimal]:
    """按主 → 备顺序抓市场价；全失败返回 None（不抛，调用方走兜底）。"""
    for url in RATE_SOURCES:
        try:
            data = _fetch_json(url)
            raw = ((data or {}).get("rates") or {}).get("CNY")
            rate = Decimal(str(raw))
            if rate > 0:
                return rate
            logger.warning("汇率源返回非法价：%s → %r", url, raw)
        except Exception:  # noqa: BLE001 — 单源失败换下一源，全失败走兜底
            logger.warning("汇率抓取失败：%s", url, exc_info=True)
    return None


# ==================== 缓存 ====================

def _read_cache(db: Session) -> tuple[Optional[Decimal], Optional[datetime]]:
    raw = store.get_value(db, CACHE_RATE_KEY, "", ttl=0)
    rate = None
    try:
        parsed = Decimal(str(raw).strip())
        if parsed > 0:
            rate = parsed
    except (InvalidOperation, ValueError):
        rate = None
    fetched_at = None
    raw_at = store.get_value(db, CACHE_AT_KEY, "", ttl=0)
    if raw_at:
        try:
            fetched_at = datetime.fromisoformat(str(raw_at).strip())
        except ValueError:
            fetched_at = None
    return rate, fetched_at


def _write_cache(db: Session, rate: Decimal) -> None:
    store.write_values(db, {
        CACHE_RATE_KEY: str(rate),
        CACHE_AT_KEY: datetime.now().isoformat(),
    })
    db.commit()


def _is_stale(fetched_at: Optional[datetime]) -> bool:
    if fetched_at is None:
        return True
    return (datetime.now() - fetched_at).total_seconds() > CACHE_TTL_SECONDS


# ==================== 解析 ====================

def _manual_rate(db: Session) -> Decimal:
    """手填键生效值（库行优先、无库行/坏值回出厂默认 7.2）。"""
    value = wallet_service.wallet_config(db)["wallet_topup_cny_rate"]
    rate = Decimal(str(value))
    return rate if rate > 0 else Decimal("7.2")


def resolve_market_rate(db: Session, *, allow_refresh: bool = True) -> Decimal:
    """市场价解析（顺序见模块头注）。刷新缓存时会写库并提交。"""
    config = wallet_service.wallet_config(db)
    if config["wallet_topup_rate_manual_override"]:
        return _manual_rate(db)

    cached, fetched_at = _read_cache(db)
    if cached is not None:
        if allow_refresh and _is_stale(fetched_at):
            fresh = _fetch_market_rate()
            if fresh is not None:
                _write_cache(db, fresh)
                return fresh
            logger.warning("汇率刷新失败，沿用旧缓存价 %s", cached)
        return cached

    if allow_refresh:
        fresh = _fetch_market_rate()
        if fresh is not None:
            _write_cache(db, fresh)
            return fresh
    logger.warning("汇率从未抓到且本次抓取失败，回落手填价")
    return _manual_rate(db)


def effective_rate(db: Session, *, allow_refresh: bool = True) -> Decimal:
    """生效汇率 = 市场价 × (1 + 缓冲%)，归一到 6 位小数（快照口径）。"""
    market = resolve_market_rate(db, allow_refresh=allow_refresh)
    buffer_pct = wallet_service.wallet_config(db)[
        "wallet_topup_rate_buffer_pct"]
    effective = market * (Decimal(100 + int(buffer_pct)) / Decimal(100))
    return effective.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


# ==================== 换算 ====================

def compute_topup(amount_usd_fen: int, rate: Decimal,
                  fee_pct: int) -> tuple[int, int, int]:
    """美元美分 → (应付人民币分, 其中手续费人民币分, 到账美分)。

    应付按模块头注公式一次取整；手续费分量 = 应付 − 无费应付
    （无费应付同样取整到分），仅作订单对账拆分，应付总额为准。
    到账恒为选定美元额全额（手续费不扣到账）。
    """
    if rate <= 0:
        raise wallet_service.WalletError("汇率异常，请稍后再试", "bad_rate")
    usd_fen = Decimal(int(amount_usd_fen))
    base_cny = (usd_fen * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    cny_fen = (usd_fen * rate * Decimal(100 + int(fee_pct)) / Decimal(100)
               ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cny_fen), int(cny_fen - base_cny), int(amount_usd_fen)


__all__ = [
    "CACHE_AT_KEY", "CACHE_RATE_KEY", "CACHE_TTL_SECONDS",
    "FETCH_TIMEOUT_SECONDS", "RATE_SOURCES",
    "compute_topup", "effective_rate", "resolve_market_rate",
]
