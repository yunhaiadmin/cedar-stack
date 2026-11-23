# -*- coding: utf-8 -*-
"""运维脚本与流节点管理口加固回归（360° 审计 A-069 / A-070 / A-071 / A-072）。

A-069：scripts/rotate-secrets.sh 原本轮换 JWT_SECRET_KEY / REDIS_PASSWORD /
CRYPTO_KEY 三个全仓零读取的键（会话实际只认 SECRET_KEY、凭据密封键由
SECRET_KEY 派生、Redis 走无密码 REDIS_URL），jwt 项还谎称「所有现有会话
将失效」。改判：jwt/user 统一轮换真正生效的 SECRET_KEY 并如实警告
（会话失效＋旧密封凭据解不开）；redis/crypto 两项删除并明示原因。

A-070：rotate-secrets.sh 的 PG 容器默认名是旧名 aetrix_postgres（现行
aetrix-postgres），且 ALTER 被 2>/dev/null || true 吞错后无条件报 ✓、
.env 还先于 ALTER 被改写。改判：默认名对齐现行；先 ALTER 成功、再改
.env、最后才报成功，失败响亮退出且不动 .env。

A-071：scripts/update.sh 的更新前备份只有 SQLite 路径，默认的 PG 部署
每次都 warn 后无备份照常 pull/build/up。改判：postgres 容器在跑时先跑
scripts/backup_db.sh（pg_dump＋校验），失败即阻断更新。

A-072：流节点 register/unregister 原走静态 X-Panel-Key（require_panel_key
兼收静态头），与 bundle 口只认 HMAC 时效签名不对称——静态键躺在每台
节点的 .env/脚本/历史里，拿到就能注册假节点/注销真节点。改判：两个写口
与 bundle 同口径只认签名头；只读 list 保留原口（审计范围外）。

脚本类用静态断言（与 tests/test_nginx_*.py 同口径）；A-072 用行为测试
（真调端点函数验 401/放行）。
"""
import os
import pathlib
import re
import sys

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
sys.path.insert(0, ".")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

REPO = pathlib.Path(__file__).resolve().parent.parent


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ---------- A-069 / A-070：rotate-secrets.sh ----------

def test_rotate_secrets_no_dead_key_writes():
    text = _read("scripts/rotate-secrets.sh")
    for dead in ("JWT_SECRET_KEY", "REDIS_PASSWORD", "CRYPTO_KEY"):
        assert f"s/^{dead}=" not in text, f"不许再写无人读的键 {dead}"


def test_rotate_secrets_default_container_name_current():
    text = _read("scripts/rotate-secrets.sh")
    assert "DB_CONTAINER:-aetrix-postgres" in text
    assert "DB_CONTAINER:-aetrix_postgres" not in text


def test_rotate_secrets_alter_not_swallowed_and_precedes_env_write():
    text = _read("scripts/rotate-secrets.sh")
    alter_idx = text.index("ALTER USER")
    env_idx = text.index("s/^POSTGRES_PASSWORD=")
    assert alter_idx < env_idx, "必须先 ALTER 成功再改 .env，失败时两者不许不一致"
    alter_line = next(l for l in text.splitlines() if "ALTER USER" in l)
    assert "|| true" not in alter_line, "ALTER 不许被 || true 吞错"
    assert "ON_ERROR_STOP" in text, "psql 必须带 ON_ERROR_STOP 让失败响亮退出"


# ---------- A-071：update.sh ----------

def test_update_sh_pg_backup_blocks_update():
    text = _read("scripts/update.sh")
    m = re.search(r'bash\s+"\$SCRIPT_DIR/backup_db\.sh"\s*\|\|\s*die', text)
    assert m, "PG 部署更新前必须跑 backup_db.sh 且失败即 die 阻断"
    assert m.start() < text.index("run git pull --ff-only"), "备份必须在拉取更新之前"


def test_update_sh_header_matches_pg_behavior():
    text = _read("scripts/update.sh")
    head = text[:text.index('set -Eeuo pipefail')]
    assert "没有备份" not in head, "头部注释不许再声称 PG 等于无备份（行为已改）"


# ---------- A-072：部署脚本注册改签名 ----------

def test_deploy_script_register_uses_signed_headers():
    text = _read("scripts/deploy-streaming-node.sh")
    assert "X-Panel-Key:" not in text, "部署脚本不许再向任何端点发送静态 X-Panel-Key 头"
    reg = text[text.index("/api/admin/stream-nodes/register") - 200:]
    assert "X-Panel-Sign" in reg, "注册调用必须带 HMAC 签名头"


# ---------- A-072：服务端行为 ----------

@pytest.fixture()
def db():
    from backend import models
    from backend.integrations import store

    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    store.invalidate()
    yield session
    store.invalidate()
    session.close()
    engine.dispose()


def _request(method, path, headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http", "method": method, "path": path,
        "headers": raw, "query_string": b"",
    }
    return Request(scope)


@pytest.fixture()
def node_secret(monkeypatch):
    monkeypatch.setenv("NODE_SHARED_SECRET", "test-node-secret")
    return "test-node-secret"


def test_register_rejects_static_panel_key(db, node_secret):
    from backend.emby_server import stream_nodes

    req = _request("POST", "/api/admin/stream-nodes/register",
                   {"X-Panel-Key": node_secret})
    with pytest.raises(HTTPException) as exc:
        stream_nodes.api_register(
            req, {"url": "https://stream.example.com", "name": "n1", "weight": 100}, db)
    assert exc.value.status_code == 401


def test_register_accepts_signed_headers(db, node_secret):
    from backend import node_auth
    from backend.emby_server import stream_nodes

    headers = node_auth.signed_headers(
        "POST", "http://main/api/admin/stream-nodes/register", node_secret)
    req = _request("POST", "/api/admin/stream-nodes/register", headers)
    out = stream_nodes.api_register(
        req, {"url": "https://stream.example.com", "name": "n1", "weight": 100}, db)
    assert out["ok"] is True
    assert out["node"]["url"] == "https://stream.example.com"


def test_unregister_rejects_static_and_accepts_signed(db, node_secret):
    from backend import node_auth
    from backend.emby_server import stream_nodes

    stream_nodes.register_node(db, "https://stream.example.com", "n1", 100)
    db.commit()

    req = _request("DELETE", "/api/admin/stream-nodes", {"X-Panel-Key": node_secret})
    with pytest.raises(HTTPException) as exc:
        stream_nodes.api_unregister(req, "https://stream.example.com", db)
    assert exc.value.status_code == 401

    headers = node_auth.signed_headers(
        "DELETE", "http://main/api/admin/stream-nodes", node_secret)
    req = _request("DELETE", "/api/admin/stream-nodes", headers)
    out = stream_nodes.api_unregister(req, "https://stream.example.com", db)
    assert out["ok"] is True


def test_list_still_accepts_static_panel_key(db, node_secret):
    """只读 list 不在本条范围：保持 require_panel_key 原口，避免误伤运维查询。"""
    from backend.emby_server import stream_nodes

    req = _request("GET", "/api/admin/stream-nodes", {"X-Panel-Key": node_secret})
    out = stream_nodes.list_nodes(req, db)
    assert "nodes" in out
