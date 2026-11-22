"""回归测试：per-SA 配额冷却、选号跳过、日志 SA 标识脱敏、admin 轮换路径口径。

背景（2026-11-22 SA 轮询阶段 1）：
- 轮换本体（随机步长 + 按 SA 分账号 token 缓存）已达标，但缺 per-SA 配额
  冷却：SA 撞 downloadQuotaExceeded / userRateLimitExceeded / 429 后，
  选号仍会继续抽到它往爆掉的配额上撞。本文件钉住冷却登记与跳过语义、
  全冷却回落（不许无号可用）、冷却时长抖动、以及三处结构化日志
  （drive_token_mint / drive_relay_sa_failover / drive_sa_affinity）
  只出 sha256 短哈希不出邮箱全称。
- admin 的 SA 轮换端点曾硬编码旧机路径（/root/.config/rclone/rclone.conf
  与 /opt/rclone-sa，新机容器内都不存在），钉住 env 可配 + 新机缺省口径
  （与 drive_changes 的 RCLONE_SA_DIR / MOUNT_RCLONE_CONF 同键同缺省）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("DATABASE_TYPE", "sqlite")

import pytest

try:
    from backend.emby_server import drive_auth
except ImportError:  # 兼容以 backend 为根的扁平导入路径
    from emby_server import drive_auth


SA_EMAILS = ("sa1@x", "sa2@x", "sa3@x")


def _make_sa(email: str) -> dict:
    return {"client_email": email, "private_key": f"private-key-for-{email}"}


def _make_fake_drive_changes(emails=SA_EMAILS):
    fake = mock.MagicMock()
    fake._discover_sa_files.return_value = [_make_sa(e) for e in emails]
    fake._sa_access_token.side_effect = (
        lambda sa, min_remaining=60: "token-for-" + sa["client_email"])
    rotation = {"i": 0}

    def _deterministic_rotation_start(n):
        s = rotation["i"] % n
        rotation["i"] += 1
        return s

    fake._sa_rotation_start.side_effect = _deterministic_rotation_start
    return fake


@pytest.fixture(autouse=True)
def reset_drive_auth_state():
    drive_auth._reset_state()
    yield
    drive_auth._reset_state()


@pytest.fixture
def fake_drive_changes(monkeypatch):
    fake = _make_fake_drive_changes()
    monkeypatch.setattr(drive_auth, "_load_drive_changes", lambda: fake)
    return fake


def _hash(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# sa_log_id 与冷却登记的触发分类
# ---------------------------------------------------------------------------

def test_sa_log_id_is_short_hash():
    assert drive_auth.sa_log_id("sa1@x") == _hash("sa1@x")
    assert len(drive_auth.sa_log_id("sa1@x")) == 8
    assert drive_auth.sa_log_id("") == "-"
    assert drive_auth.sa_log_id(None) == "-"


def test_note_cooldown_registers_only_quota_failures():
    assert drive_auth.note_sa_cooldown("sa1@x", status=429) is True
    assert drive_auth.sa_in_cooldown("sa1@x") is True

    assert drive_auth.note_sa_cooldown("sa2@x", status=403,
                                       detail="downloadQuotaExceeded") is True
    assert drive_auth.sa_in_cooldown("sa2@x") is True

    # 无文本可查的 403（中继 failover 口径）也登记
    assert drive_auth.note_sa_cooldown("sa3@x", status=403) is True
    assert drive_auth.sa_in_cooldown("sa3@x") is True


def test_note_cooldown_ignores_non_quota_failures():
    assert drive_auth.note_sa_cooldown("sa1@x", status=404) is False
    assert drive_auth.note_sa_cooldown("sa1@x", status=401) is False
    assert drive_auth.note_sa_cooldown(
        "sa1@x", status=403, detail="file not found") is False
    assert drive_auth.note_sa_cooldown("", status=429) is False
    assert drive_auth.note_sa_cooldown(None, status=429) is False
    assert drive_auth.sa_in_cooldown("sa1@x") is False


def test_cooldown_span_includes_jitter(monkeypatch):
    monkeypatch.setenv("DRIVE_SA_COOLDOWN_SECONDS", "100")
    monkeypatch.setenv("DRIVE_SA_COOLDOWN_JITTER_SECONDS", "50")
    seen = {}

    def _fake_uniform(a, b):
        seen["args"] = (a, b)
        return 25.0

    monkeypatch.setattr(drive_auth.random, "uniform", _fake_uniform)
    before = time.monotonic()
    assert drive_auth.note_sa_cooldown("sa1@x", status=429) is True
    until = drive_auth._sa_cooldown_until["sa1@x"]
    assert seen["args"] == (0.0, 50.0)
    # 基础 100 + 抖动 25（留出调用间流逝时间的宽容带）
    assert 125.0 <= until - before <= 130.0


def test_cooldown_span_without_jitter_is_exact_base(monkeypatch):
    monkeypatch.setenv("DRIVE_SA_COOLDOWN_SECONDS", "100")
    monkeypatch.setenv("DRIVE_SA_COOLDOWN_JITTER_SECONDS", "0")
    before = time.monotonic()
    assert drive_auth.note_sa_cooldown("sa1@x", status=429) is True
    until = drive_auth._sa_cooldown_until["sa1@x"]
    assert 100.0 <= until - before <= 105.0


# ---------------------------------------------------------------------------
# 选号跳过与回落
# ---------------------------------------------------------------------------

def test_pick_sa_skips_cooling_sa(fake_drive_changes):
    drive_auth.note_sa_cooldown("sa1@x", status=429)
    sa = drive_auth._pick_sa()
    assert sa is not None
    assert sa["client_email"] == "sa2@x"


def test_all_cooling_falls_back_instead_of_no_sa(fake_drive_changes):
    for email in SA_EMAILS:
        drive_auth.note_sa_cooldown(email, status=429)
    # 全冷却时回落现行行为（第一个可用 SA），不许无号可用
    token = drive_auth.get_drive_bearer_token()
    assert token == "token-for-sa1@x"


def test_cooldown_expiry_restores_pick(fake_drive_changes):
    drive_auth.note_sa_cooldown("sa1@x", status=429)
    drive_auth._sa_cooldown_until["sa1@x"] = time.monotonic() - 1.0
    assert drive_auth.sa_in_cooldown("sa1@x") is False
    sa = drive_auth._pick_sa()
    assert sa is not None
    assert sa["client_email"] == "sa1@x"


def test_affinity_drops_cooling_sa(fake_drive_changes):
    first = drive_auth._affinity_pick("file-key-1")
    assert first is not None and first["client_email"] == "sa1@x"
    drive_auth.note_sa_cooldown("sa1@x", status=429)
    second = drive_auth._affinity_pick("file-key-1")
    assert second is not None
    assert second["client_email"] != "sa1@x"


# ---------------------------------------------------------------------------
# 日志脱敏：三处结构化日志只出短哈希
# ---------------------------------------------------------------------------

def test_mint_log_uses_short_hash(fake_drive_changes, caplog):
    with caplog.at_level(logging.INFO):
        token = drive_auth.get_drive_bearer_token()
    assert token == "token-for-sa1@x"
    assert f"drive_token_mint sa={_hash('sa1@x')}" in caplog.text
    assert "sa1@x" not in caplog.text


def test_affinity_log_uses_short_hash(fake_drive_changes, caplog):
    with caplog.at_level(logging.INFO):
        drive_auth._affinity_pick("file-key-9")
    assert f"drive_sa_affinity new key=file-key-9 sa={_hash('sa1@x')}" in caplog.text
    assert "sa1@x" not in caplog.text


def test_relay_cooldown_registers_into_drive_auth_table():
    """streaming._note_relay_sa_cooldown 对 403/429 登记进同一张冷却表，
    且原有的按值失效行为不变；401 只失效不冷却。"""
    from backend.emby_server import streaming

    drive_auth._token_cache["sa9@x"] = ("TOK9", time.monotonic() + 3600)
    streaming._note_relay_sa_cooldown("https://drive.google.com/f", "TOK9", 429)
    assert drive_auth.sa_in_cooldown("sa9@x") is True
    # 失效仍发生：坏令牌已从缓存清掉
    assert drive_auth.sa_email_for_token("TOK9") is None

    drive_auth._token_cache["sa8@x"] = ("TOK8", time.monotonic() + 3600)
    streaming._note_relay_sa_cooldown("https://drive.google.com/f", "TOK8", 401)
    assert drive_auth.sa_in_cooldown("sa8@x") is False


# ---------------------------------------------------------------------------
# admin 轮换端点路径口径（env 可配 + 新机缺省）
# ---------------------------------------------------------------------------

def _call_rotate(monkeypatch, tmp_path):
    from backend.api import admin as admin_api
    from backend.emby_server import playback_tune as pt

    captured = {}

    def _fake_ensure(conf_path, sa_dir, state_path=None):
        captured["conf"] = conf_path
        captured["sa_dir"] = sa_dir
        return {"sa_file": "/x/a.json", "mode": "rotated",
                "changed": True, "needs_remount": True}

    monkeypatch.setattr(pt, "ensure_sa_rotation", _fake_ensure)
    monkeypatch.setattr(admin_api, "_audit", lambda *a, **k: None)
    with mock.patch("pathlib.Path.exists", return_value=True):
        out = admin_api.gdrive_sa_rotate(
            current_admin=mock.MagicMock(), db=mock.MagicMock())
    assert out["success"] is True
    return captured


def test_rotate_endpoint_default_paths_are_new_machine(monkeypatch, tmp_path):
    monkeypatch.delenv("RCLONE_SA_DIR", raising=False)
    monkeypatch.delenv("MOUNT_RCLONE_CONF", raising=False)
    captured = _call_rotate(monkeypatch, tmp_path)
    assert captured["conf"] == "/config/rclone/rclone.conf"
    assert captured["sa_dir"] == Path("/sa-accounts")


def test_rotate_endpoint_paths_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("RCLONE_SA_DIR", "/custom/sa")
    monkeypatch.setenv("MOUNT_RCLONE_CONF", "/custom/rclone.conf")
    captured = _call_rotate(monkeypatch, tmp_path)
    assert captured["conf"] == "/custom/rclone.conf"
    assert captured["sa_dir"] == Path("/custom/sa")
