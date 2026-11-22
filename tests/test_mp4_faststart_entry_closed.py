# -*- coding: utf-8 -*-
"""mp4_faststart 入口关停（2026-11-22 谷歌盘调用削减第一批第 5 项）。

生产复核零触发（三容器 env 无 MP4_FASTSTART_*、日志 [mp4-faststart] 命中 0、
DB 无相关任务/配置）后关停全部触发入口，模块本体保留：

- 扫描入库钩子（原 scanner 调 note_new_ingest）摘除——env 翻 1 也无入口可达；
- 存量脚本 scripts/mp4_faststart_backfill.py 硬关停——env=1 + --apply
  也不进 DB、不调 run_backfill、零 Drive 调用。

重启入口须改代码走 PR，不许只翻 env 复活整文件下载/回写路径。
全 mock，不真实调用 Drive。
"""
import importlib.util
import os

from backend.emby_server import mp4_faststart as fs


def _load_backfill_script():
    path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "mp4_faststart_backfill.py")
    spec = importlib.util.spec_from_file_location("mp4_faststart_backfill_entry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scanner_source_has_no_faststart_entry():
    path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "emby_server", "scanner.py")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "note_new_ingest" not in text
    assert "mp4_faststart" not in text


def test_backfill_script_closed_even_with_env_and_apply(monkeypatch):
    monkeypatch.setenv(fs.BACKFILL_ENV, "1")
    monkeypatch.setenv(fs.ON_INGEST_ENV, "1")
    mod = _load_backfill_script()
    touched = []

    def _fake_session_local():
        touched.append("session")
        raise AssertionError("入口已关停，不许建 DB 会话")

    monkeypatch.setattr("backend.database.SessionLocal", _fake_session_local)
    monkeypatch.setattr(
        mod, "run_backfill",
        lambda *a, **k: touched.append("run_backfill"), raising=False)
    assert mod.main(["--apply", "--limit", "3"]) == 0
    assert touched == []


def test_faststart_body_retained_but_unreachable_via_entries(monkeypatch):
    # 本体保留：重写函数仍在（将来走 PR 恢复入口时可用）。
    assert callable(fs.rewrite_drive_file)
    assert callable(fs.process_job)
    assert callable(fs.run_backfill)
    # 入口关停后，即使 env 全开，脚本入口也零 Drive 令牌请求。
    monkeypatch.setenv(fs.BACKFILL_ENV, "1")
    token_calls = []
    monkeypatch.setattr(
        "backend.emby_server.drive_auth.get_drive_bearer_token",
        lambda *a, **k: token_calls.append(a))
    mod = _load_backfill_script()
    monkeypatch.setattr(
        "backend.database.SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("不许建会话")))
    assert mod.main(["--apply"]) == 0
    assert token_calls == []
