"""部署自检端口防撞回归（2026-11-10 #19/#20 attempt 2 实证）。

事故：部署自检脚本写死端口 8010，同一台测试机上两个 PR 的自检 job 同时跑
撞端口——后起的探活撞上先跑那家的服务（就绪 0.0 秒），再拿自己的随机自检
账号去登录别人家的库 → 管理员登录 401，连带 6–8 项全红。

钉死三条：
1. 默认端口动态挑空闲端口，两次挑选不重复；
2. 撞端口时重试换端口，不认错服务；
3. 别人家的 200（无本进程启动标记）绝不算就绪。

打桩说明：start_service 的子进程启动步用假 Popen 替掉，不起真实服务；
健康探活用假 request 替掉；日志标记走真实文件（与生产同一路径）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import socket

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "deploy_check", REPO / "scripts" / "deploy_check.py")
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)


@pytest.fixture(autouse=True)
def _reset_check_state():
    dc.failures.clear()
    dc.warnings.clear()
    yield
    dc.failures.clear()
    dc.warnings.clear()


def _make_fake_popen(behaviors):
    """behaviors：每次 Popen 调用的行为 {"exit": 是否已退出, "log_lines": 写进日志的行}。

    日志行写进 start_service 传进来的 stdout 句柄并 flush，与生产同一文件。
    """
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.kwargs = kwargs
            self.env = kwargs.get("env") or {}
            calls.append(self)
            self._behavior = behaviors[min(len(calls) - 1, len(behaviors) - 1)]
            self.returncode = 1 if self._behavior.get("exit") else None
            stdout = kwargs.get("stdout")
            if stdout is not None:
                for line in self._behavior.get("log_lines", []):
                    stdout.write(line + "\n")
                stdout.flush()

        def poll(self):
            return 1 if self._behavior.get("exit") else None

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    FakePopen.calls = calls
    return FakePopen


def _healthy_request(method, url, **kwargs):
    return 200, '{"status": "healthy"}', {}


def test_pick_free_port_returns_distinct_ports():
    ports = {dc.pick_free_port() for _ in range(5)}
    assert len(ports) == 5
    assert all(1 <= p <= 65535 for p in ports)


def test_pick_free_port_is_actually_free():
    port = dc.pick_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # 挑完立刻能绑上才算空闲


def test_resolve_port_keeps_explicit_port_when_free():
    port = dc.pick_free_port()
    assert dc.resolve_port(port) == port


def test_resolve_port_picks_free_port_by_default():
    port = dc.resolve_port(None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_start_service_retries_on_address_in_use(monkeypatch, tmp_path):
    """第一家占了端口：第一次绑定失败退出，第二次换端口就绪。"""
    monkeypatch.setattr(dc, "request", _healthy_request)
    monkeypatch.setattr(dc.tempfile, "gettempdir", lambda: str(tmp_path))
    fake = _make_fake_popen([
        {"exit": True,
         "log_lines": ["ERROR: error while attempting to bind on address "
                       "('127.0.0.1', 18010): address already in use"]},
        {"exit": False, "log_lines": ["INFO:     Application startup complete."]},
    ])
    monkeypatch.setattr(dc.subprocess, "Popen", fake)

    proc, base, log_path = dc.start_service(
        "serve.py", 18010, timeout=10, label="EM（单进程模式）")

    assert len(fake.calls) == 2
    first_port = fake.calls[0].env["PORT"]
    second_port = fake.calls[1].env["PORT"]
    assert first_port == "18010"
    assert second_port != "18010"
    assert base == f"http://127.0.0.1:{second_port}"
    assert proc is fake.calls[1]
    assert dc.failures == []
    # 两轮日志各走各的文件，不互相覆盖
    assert pathlib.Path(log_path).name == f"deploy_check_{second_port}.log"
    assert (tmp_path / "deploy_check_18010.log").is_file()
    assert (tmp_path / f"deploy_check_{second_port}.log").is_file()


def test_start_service_rejects_foreign_200_without_startup_marker(monkeypatch, tmp_path):
    """别人家的服务先回 200、但本进程日志里没有启动完成标记：绝不算就绪。"""
    monkeypatch.setattr(dc, "request", _healthy_request)
    monkeypatch.setattr(dc.tempfile, "gettempdir", lambda: str(tmp_path))
    fake = _make_fake_popen([
        {"exit": False, "log_lines": ["INFO:     Waiting for application startup."]},
    ])
    monkeypatch.setattr(dc.subprocess, "Popen", fake)

    with pytest.raises(SystemExit):
        dc.start_service("serve.py", 18011, timeout=2, label="EM（单进程模式）")

    assert len(fake.calls) == 1  # 没绑定失败就不重试，只等超时
    assert any("在超时内就绪" in name for name in dc.failures)
