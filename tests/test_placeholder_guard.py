"""占位符防线（2026-11-23）。

2026-11-21/22 迁机重建时 .env 由 env.example 整体生成，
EMBY_PUBLIC_URL 的模板占位值 https://media.example.com 直接上线，
导致播放地址全错、server_realm_lines 的主线路也被种子成该假地址。
本文件钉住三道防线：
- env.example 生效行不许带 example.com/.org/.net 占位值；
- 线路/服地址写入口拒绝 RFC 2606 占位域名；
- ensure_realm_lines 不许用占位地址生成线路；
- 启动自检 detect_placeholder_config 能大声检出已生效的占位值（只报不改）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_self_heal, models, realms

ROOT = Path(__file__).resolve().parent.parent
_PLACEHOLDER_MARKERS = ("example.com", "example.org", "example.net")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_env_example_no_active_placeholder_values():
    """env.example 的生效行（非注释）里不许出现占位域名。"""
    offenders = []
    for line in (ROOT / "env.example").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1].strip().lower()
        if any(marker in value for marker in _PLACEHOLDER_MARKERS):
            offenders.append(line.strip())
    assert offenders == []


def test_env_example_emby_public_url_not_active():
    """EMBY_PUBLIC_URL 只能是注释行，绝不能以占位值生效。"""
    for line in (ROOT / "env.example").read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*EMBY_PUBLIC_URL\s*=", line):
            pytest.fail(f"EMBY_PUBLIC_URL 不许是生效行：{line!r}")


@pytest.mark.parametrize("url", [
    "https://media.example.com",
    "https://example.com",
    "https://cdn.example.org",
    "http://a.example.net:8443",
    "https://EXAMPLE.COM",
])
def test_validate_line_url_rejects_placeholder(url):
    with pytest.raises(ValueError, match="占位"):
        realms.validate_line_url(url)


def test_validate_line_url_accepts_real_url():
    assert realms.validate_line_url("https://cdn.aetrix.love") == \
        "https://cdn.aetrix.love"
    assert realms.validate_line_url("https://cdn.aetrix.love/") == \
        "https://cdn.aetrix.love"


def test_create_and_update_realm_reject_placeholder(db):
    with pytest.raises(ValueError, match="占位"):
        realms.create_realm(db, name="占位服", slug="placeholder1",
                            url="https://media.example.com")
    realm = realms.create_realm(db, name="真实服", slug="real1",
                                url="https://cdn.aetrix.love")
    with pytest.raises(ValueError, match="占位"):
        realms.update_realm(db, realm, url="https://media.example.com")
    # 拒绝后原值不变
    db.refresh(realm)
    assert realm.url == "https://cdn.aetrix.love"


def test_ensure_realm_lines_placeholder_public_url_creates_nothing(db, monkeypatch):
    """占位 env 地址不许被种子成主线路。"""
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://media.example.com")
    realm = models.ServerRealm(name="默认占位服", slug="main", url="")
    db.add(realm)
    db.commit()
    db.refresh(realm)
    assert realms.ensure_realm_lines(db, realm) == []
    assert db.query(models.ServerRealmLine).count() == 0


def test_ensure_realm_lines_real_public_url_creates_line(db, monkeypatch):
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://cdn.aetrix.love")
    realm = models.ServerRealm(name="默认真实服", slug="main", url="")
    db.add(realm)
    db.commit()
    db.refresh(realm)
    lines = realms.ensure_realm_lines(db, realm)
    assert len(lines) == 1
    assert lines[0].url == "https://cdn.aetrix.love"


def test_detect_placeholder_config_reports_env(monkeypatch):
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://media.example.com")
    problems = config_self_heal.detect_placeholder_config()
    assert any("EMBY_PUBLIC_URL" in p and "占位配置" in p for p in problems)


def test_detect_placeholder_config_clean_env(monkeypatch):
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://cdn.aetrix.love")
    assert config_self_heal.detect_placeholder_config() == []


def test_detect_placeholder_config_reports_db_rows(db, monkeypatch):
    monkeypatch.setenv("EMBY_PUBLIC_URL", "https://cdn.aetrix.love")
    realm = models.ServerRealm(name="检出服", slug="detect1", url="")
    db.add(realm)
    db.commit()
    db.refresh(realm)
    line = models.ServerRealmLine(
        realm_id=realm.id, name="主线路", url="https://media.example.com")
    db.add(line)
    db.add(models.SystemConfig(key="site_url",
                                 value="https://www.example.org"))
    db.commit()
    db.refresh(line)
    problems = config_self_heal.detect_placeholder_config(db)
    assert any(f"id={line.id}" in p for p in problems)
    assert any("site_url" in p for p in problems)
    # 只检出不改写：原值原样保留
    db.refresh(line)
    assert line.url == "https://media.example.com"
