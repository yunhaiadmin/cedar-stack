"""C-08 可观测性打点：nginx 日志带 $request_time＋后端请求耗时聚合。

旧状态：nginx.conf 无 log_format（默认 combined、无耗时字段），
后端无任何请求计时——API 路由级延迟分位测不到。本文件钉住两半：
① nginx.conf 定义 aetrix_timed 格式且 server 块启用；
② RequestTimingMiddleware 按（方法、归一路径、状态）聚合直方图，
   透传不改行为、异常也记账、慢请求打 warning。
"""
from __future__ import annotations

import logging
import os
import pathlib
import re

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestNginxTimedLogFormat:
    def _conf(self) -> str:
        return (REPO / "docker" / "nginx.conf").read_text(encoding="utf-8")

    def test_log_format_defined_with_request_time(self):
        conf = self._conf()
        m = re.search(r"log_format\s+aetrix_timed\b[^;]*;", conf, re.S)
        assert m, "nginx.conf 必须定义 aetrix_timed 日志格式"
        block = m.group(0)
        assert "$request_time" in block
        assert "$upstream_response_time" in block

    def test_server_uses_timed_format(self):
        conf = self._conf()
        assert re.search(r"access_log\s+\S+\s+aetrix_timed\s*;", conf), \
            "server 块必须启用 aetrix_timed 访问日志"


class TestNormalizePath:
    def test_id_segments_collapsed(self):
        from backend.request_timing import normalize_path
        assert normalize_path("/Items/0123456789abcdef0123456789abcdef/Images/Primary") \
            == "/Items/{id}/Images/Primary"
        assert normalize_path("/Users/42/Items/7") == "/Users/{id}/Items/{id}"
        assert normalize_path("/api/user/subscriptions") == "/api/user/subscriptions"
        assert normalize_path("/") == "/"


class TestTimingMiddleware:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend import request_timing
        request_timing._reset_for_tests()

        app = FastAPI()

        @app.get("/Items/{item_id}/Images/Primary")
        def _img(item_id: str):
            return {"ok": True}

        @app.get("/boom")
        def _boom():
            raise RuntimeError("炸了")

        app.add_middleware(request_timing.RequestTimingMiddleware)
        return TestClient(app, raise_server_exceptions=False), request_timing

    def test_aggregates_by_normalized_route(self, client):
        tc, rt = client
        guid = "0123456789abcdef0123456789abcdef"
        assert tc.get(f"/Items/{guid}/Images/Primary").status_code == 200
        assert tc.get(f"/Items/{guid}/Images/Primary").status_code == 200
        snap = rt.snapshot()
        key = "GET /Items/{id}/Images/Primary 200"
        assert key in snap["routes"], f"聚合键缺失：{list(snap['routes'])}"
        entry = snap["routes"][key]
        assert entry["count"] == 2
        assert entry["total_ms"] >= 0
        assert sum(entry["buckets"]) == 2
        assert rt.approx_percentile_ms(entry, 0.95) > 0

    def test_downstream_exception_recorded_as_500(self, client):
        tc, rt = client
        assert tc.get("/boom").status_code == 500
        snap = rt.snapshot()
        assert snap["routes"].get("GET /boom 500", {}).get("count") == 1

    def test_slow_request_warns(self, client, monkeypatch, caplog):
        tc, rt = client
        monkeypatch.setattr(rt, "SLOW_LOG_MS", 0)
        with caplog.at_level(logging.WARNING, logger="backend.request_timing"):
            tc.get("/Items/0123456789abcdef0123456789abcdef/Images/Primary")
        assert any("慢请求" in r.message for r in caplog.records), \
            "超阈值请求必须打 warning（归一路径）"
