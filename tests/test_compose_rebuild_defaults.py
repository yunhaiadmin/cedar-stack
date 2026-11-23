"""回归：主 compose 的缺省口径必须与 2026-11-22 迁机重建的生产实证一致。

两个陷阱（当时靠服务器侧 override 临时救火，本测试把口径钉进仓库）：
1. MOUNT_RCLONE_RC_URL 缺省曾是 http://rclone:5572，但主 compose 里根本
   没有 rclone 服务、新机上也解析不到这个名字，挂载全断。缺省改走
   host.docker.internal（宿主机上跑 rcd），且三服务都要有
   host.docker.internal:host-gateway 的 extra_hosts 兜底解析。
2. aetrix-api 的 8000 曾是全网发布（8000:8000），重建时安装向导对公网
   敞开。发布必须收紧到 127.0.0.1（健康检查/部署脚本都走本地口，
   公网入口走 web/tls-bridge）。

纯文本断言，不依赖 yaml 解析。
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = REPO / "docker-compose.yml"
BACKEND_SERVICES = ("aetrix-api", "aetrix-ea", "aetrix-worker")


def _service_blocks(text: str) -> dict[str, str]:
    """按 2 空格缩进的服务名切分 services 段，返回 {服务名: 段文本}。"""
    lines = text.splitlines()
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    in_services = False
    for line in lines:
        if line == "services:":
            in_services = True
            continue
        if in_services and line and not line.startswith(" "):
            break  # 顶层键（如 volumes:），services 段结束
        if not in_services:
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            current = m.group(1)
            blocks[current] = []
            continue
        if current is not None:
            blocks[current].append(line)
    return {name: "\n".join(body) for name, body in blocks.items()}


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_no_rclone_service_name_default_remains():
    text = _text()
    assert "rclone:5572" not in text
    assert text.count(
        "MOUNT_RCLONE_RC_URL: ${MOUNT_RCLONE_RC_URL:-http://host.docker.internal:5572}"
    ) == 3


def test_all_backend_services_have_host_gateway():
    blocks = _service_blocks(_text())
    for name in BACKEND_SERVICES:
        assert name in blocks, f"compose 缺少服务段 {name}"
        assert "host.docker.internal:host-gateway" in blocks[name], (
            f"{name} 缺少 extra_hosts host-gateway 兜底解析"
        )
    assert _text().count("host.docker.internal:host-gateway") == 3


def test_api_8000_binds_loopback_only():
    blocks = _service_blocks(_text())
    api = blocks["aetrix-api"]
    assert "127.0.0.1:8000:8000" in api
    # 不许再出现不带主机地址的全网发布写法
    assert not re.search(r"^\s*-\s*8000:8000\s*$", api, re.MULTILINE)
    assert not re.search(r"^\s*-\s*\"8000:8000\"\s*$", api, re.MULTILINE)
