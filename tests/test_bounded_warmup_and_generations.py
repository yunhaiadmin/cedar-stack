# -*- coding: utf-8 -*-
"""C-07：_WARMUP_LAST（预热冷却）与 _GEN（热缓存代际）改为有界。

钉住三件事：
1. 冷却语义逐字不变：120 秒内重复预热被拦、过期后放行；
   代际语义逐字不变：失效后旧代际写入被丢、新代际照常落盘。
2. 大量键下字典大小有界（上限 4096）：
   - 预热字典：先清过期键、再按时间戳删最旧键；
   - 代际字典：先清空闲超 1 小时的键、再清盘上无分段且空闲超 60 秒的键。
3. 删键不改变正确性：预热删键 ≡ 从未预热过；代际删键后旧捕获值
   仍被代际比对丢弃（删后代际回 0，旧捕获 ≥1 一律不匹配）。
"""
import os
import time

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

from backend.emby_server import api as api_mod  # noqa: E402
from backend.emby_server import relay_hot_cache as hc  # noqa: E402

SEG = hc.SEGMENT_SIZE
IDENT = "mount:C07FID"


@pytest.fixture()
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_HOT_CACHE_DIR", str(tmp_path / "hc"))
    monkeypatch.setenv("RELAY_HOT_CACHE_MIN_FREE_BYTES", "0")
    monkeypatch.setattr(hc, "max_bytes", lambda: 500 * 1024 * 1024)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_state():
    api_mod._WARMUP_LAST.clear()
    hc._GEN.clear()
    yield
    api_mod._WARMUP_LAST.clear()
    hc._GEN.clear()


# ---------------------------------------------------------------------------
# _WARMUP_LAST：冷却语义
# ---------------------------------------------------------------------------
def test_warmup_cooldown_blocks_within_window_then_expires():
    now = time.monotonic()
    item_id = 424242
    assert api_mod._warmup_cooldown_hit(item_id, now) is False
    # 120 秒内重复 → 拦
    assert api_mod._warmup_cooldown_hit(item_id, now + 10.0) is True
    assert api_mod._warmup_cooldown_hit(item_id, now + 119.0) is True
    # 过期后放行，并重新登记
    assert api_mod._warmup_cooldown_hit(item_id, now + 121.0) is False
    assert api_mod._warmup_cooldown_hit(item_id, now + 122.0) is True


def test_warmup_dict_bounded_drops_expired_first():
    now = time.monotonic()
    # 5000 个过期键 + 10 个新鲜键
    for i in range(5000):
        api_mod._WARMUP_LAST[i] = now - 200.0
    for i in range(5000, 5010):
        api_mod._WARMUP_LAST[i] = now - 1.0
    assert api_mod._warmup_cooldown_hit(999999, now) is False
    assert len(api_mod._WARMUP_LAST) <= api_mod._WARMUP_LAST_MAX_ENTRIES
    # 新鲜键一个不少，过期键被清
    for i in range(5000, 5010):
        assert i in api_mod._WARMUP_LAST
    assert 999999 in api_mod._WARMUP_LAST


def test_warmup_dict_bounded_drops_oldest_when_all_fresh():
    now = time.monotonic()
    for i in range(5000):
        api_mod._WARMUP_LAST[i] = now - 1.0 + i * 0.001
    assert api_mod._warmup_cooldown_hit(999999, now) is False
    assert len(api_mod._WARMUP_LAST) == api_mod._WARMUP_LAST_MAX_ENTRIES
    # 最旧的被删，最新的留着
    assert 0 not in api_mod._WARMUP_LAST
    assert 4999 in api_mod._WARMUP_LAST
    assert 999999 in api_mod._WARMUP_LAST


# ---------------------------------------------------------------------------
# _GEN：代际语义
# ---------------------------------------------------------------------------
def test_generation_semantics_preserved():
    assert hc.current_generation(IDENT) == 0
    assert hc._bump_generation(IDENT) == 1
    assert hc._bump_generation(IDENT) == 2
    assert hc.current_generation(IDENT) == 2
    assert hc._segment_write_current(IDENT, 2) is True
    assert hc._segment_write_current(IDENT, 1) is False
    hc.invalidate(IDENT)
    assert hc.current_generation(IDENT) == 3
    assert hc._segment_write_current(IDENT, 2) is False
    assert hc._segment_write_current(IDENT, 3) is True


def test_generations_bounded_drops_idle_entries(cache_env):
    now = time.monotonic()
    # 5000 个空闲超 1 小时的条目（touch=0）
    for i in range(5000):
        hc._GEN[f"mount:OLD{i}"] = (2, 0.0)
    # 一个活跃条目：刚 bump 过代际（读不建条目，见实现注释）
    hc._bump_generation("mount:KEEP")
    hc._bump_generation("mount:NEW")
    assert len(hc._GEN) <= hc._GEN_MAX_ENTRIES
    assert "mount:KEEP" in hc._GEN
    assert "mount:NEW" in hc._GEN
    assert "mount:OLD0" not in hc._GEN


def test_generations_bounded_second_tier_needs_no_segments(cache_env):
    total = 3 * SEG
    assert hc.note_validators(IDENT, total, etag='"v1"')
    assert hc.submit_segment(IDENT, 0, b"s" * SEG, total)
    hc.flush_writes()
    meta = hc.get_meta(IDENT)
    assert hc.segment_present(IDENT, meta, 0)
    now = time.monotonic()
    # 该身份盘上有分段、空闲 119 秒（>60 秒但 <1 小时；比其余条目
    # 新 1 秒，保证二级清理按 touch 排序时先删无分段的）
    hc._GEN[IDENT] = (hc.current_generation(IDENT), now - 119.0)
    # 其余 4095 个身份空闲 120 秒、盘上无分段
    for i in range(4095):
        hc._GEN[f"mount:EMPTY{i}"] = (1, now - 120.0)
    hc._bump_generation("mount:TRIGGER")
    assert len(hc._GEN) <= hc._GEN_MAX_ENTRIES
    # 有分段的身份被保留，无分段的被清掉
    assert IDENT in hc._GEN
    assert "mount:EMPTY0" not in hc._GEN


def test_stale_write_still_dropped_after_generation_pruned(cache_env):
    """删键后代际回 0，但失效后捕获的旧代际仍被比对丢弃（删后值 0
    只放行失效后重新规划的写入；旧捕获值 ≥1 一律不匹配）。"""
    total = 3 * SEG
    assert hc.note_validators(IDENT, total, etag='"v1"')
    old_gen = hc.current_generation(IDENT)  # 0
    hc.invalidate(IDENT)  # 0 -> 1，删目录
    new_gen = hc.current_generation(IDENT)  # 1
    assert hc._segment_write_current(IDENT, old_gen) is False
    assert hc._segment_write_current(IDENT, new_gen) is True
    # 把条目老化到空闲超 1 小时，填满触发清理：条目被删、代际回 0
    hc._GEN[IDENT] = (new_gen, 0.0)
    for i in range(hc._GEN_MAX_ENTRIES):
        hc._GEN[f"mount:FILL{i}"] = (1, 0.0)
    hc._bump_generation("mount:TRIGGER")
    assert IDENT not in hc._GEN
    assert hc.current_generation(IDENT) == 0
    # 失效后捕获的旧代际（1）仍被丢弃；失效后重新规划（0）才放行
    assert hc._segment_write_current(IDENT, new_gen) is False
    assert hc._segment_write_current(IDENT, 0) is True
