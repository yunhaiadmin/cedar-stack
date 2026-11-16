# -*- coding: utf-8 -*-
"""FMBY #23 能力门控执法（2026-10-27 第 5 路）。

#23 之前的状态：五能力位只声明不执法——can_list（扫描）、
can_refresh_credentials（刷新重试）、can_generate_play_target（变体
清单）三处有门，can_random_read 与 can_read_sidecar 全仓零调用方
执法，换链直访（无变体参数的老客户端）也不查能力。本文件钉：
声明 False 的提供者对应接口根本不被调（间谍计数为 0），而不是
调了之后才失败。

全部离线：DB 用内存 SQLite。

（本文件的 #24 播放总线消费端用例已随总线功能于 2026-11-17 删除。）
"""

import base64
import json
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from fastapi import Request
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from backend import models
from backend.database import Base
from backend.emby_server import mounts as mnt
from backend.emby_server import models as em
from backend.emby_server.mounts import (
    MountProvider,
    Pan115Mount,
    ProviderCapabilities,
)
from backend.emby_server.mount_sidecar import SidecarMountProvider


def _all_false():
    return ProviderCapabilities()


def _all_true_no_refresh():
    return ProviderCapabilities(
        can_list=True, can_random_read=True, can_read_sidecar=True,
        can_generate_play_target=True, can_refresh_credentials=False,
    )


# ---------------------------------------------------------------- #23：按段读门控

def test_random_read_gate_base_provider_never_touches_disk(tmp_path):
    (tmp_path / "f.mkv").write_bytes(b"0123456789")

    class _NoCap(mnt.LocalMount):
        def capabilities(self):
            return _all_false()

    prov = _NoCap(SimpleNamespace(
        mount_type="local", path=str(tmp_path), config="{}", name="t"))
    with pytest.raises(mnt.MountError, match="can_random_read"):
        prov.open_range("f.mkv", 0, 2)


def test_random_read_gate_remote_provider_never_requests():
    seen = []

    class _NoCapRemote(mnt.RemoteMount):
        def __init__(self):
            pass

        def capabilities(self):
            return _all_false()

        def resolve_play_target(self, rel):
            return mnt.PlayTarget("url", "http://x/f", {})

        def _request(self, method, url, headers=None, **kw):
            seen.append((method, url))
            return SimpleNamespace(status_code=206, content=b"xy")

    with pytest.raises(mnt.MountError, match="can_random_read"):
        _NoCapRemote().open_range("f.mkv", 0, 2)
    assert seen == []


def test_random_read_gate_pan115_never_resolves():
    calls = []

    class _NoCap115(Pan115Mount):
        def __init__(self):
            pass

        def capabilities(self):
            return _all_false()

        def resolve_play_target(self, rel):
            calls.append(rel)
            return mnt.PlayTarget("url", "http://x/115", {})

    with pytest.raises(mnt.MountError, match="can_random_read"):
        _NoCap115().open_range("f.mkv", 0, 2)
    assert calls == []


def test_random_read_gate_sidecar_never_calls_runtime():
    calls = []

    class _FakeRuntimeClient:
        def open_range(self, session_id, path, offset, length):
            calls.append((session_id, path))
            return {"data_b64": base64.b64encode(b"xy").decode(),
                    "byte_offset": 0, "byte_length": 2}

    prov = SidecarMountProvider.__new__(SidecarMountProvider)
    prov._capabilities = _all_false()
    prov.client = _FakeRuntimeClient()
    prov.session_id = "s-1"
    with pytest.raises(mnt.MountError, match="can_random_read"):
        prov.open_range("f.mkv", 0, 2)
    assert calls == []

    prov._capabilities = _all_true_no_refresh()
    assert prov.open_range("f.mkv", 0, 2) == b"xy"
    assert calls == [("s-1", "f.mkv")]


# ---------------------------------------------------------------- #23：旁挂门控

def test_sidecar_gate_method_and_function_refuse_false_provider():
    calls = []

    class _NoCap(MountProvider):
        def __init__(self):
            pass

        def capabilities(self):
            return _all_false()

        def read_text(self, rel):
            calls.append(rel)
            return "text"

    prov = _NoCap()
    with pytest.raises(mnt.MountError, match="can_read_sidecar"):
        prov.read_sidecar_text("a.nfo")
    with pytest.raises(mnt.MountError, match="can_read_sidecar"):
        mnt.read_sidecar_text_from(prov, "a.nfo")
    assert calls == []


def test_sidecar_gate_duck_provider_still_falls_back():
    class _Duck:
        def __init__(self):
            self.read_text_calls = []

        def read_text(self, rel):
            self.read_text_calls.append(rel)
            return "duck-text"

    duck = _Duck()
    assert mnt.read_sidecar_text_from(duck, "a.nfo") == "duck-text"
    assert duck.read_text_calls == ["a.nfo"]


# ---------------------------------------------------------------- #23：换链直访门控

@pytest.fixture()
def mount_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def test_generate_gate_module_resolve_refuses_false_provider(
        mount_db, monkeypatch, tmp_path):
    calls = []

    class _NoCapProvider(MountProvider):
        def capabilities(self):
            return _all_false()

        def resolve_play_target(self, rel):
            calls.append(rel)
            return mnt.PlayTarget("url", "http://x/f", {})

    monkeypatch.setitem(mnt._PROVIDERS, "testnocap", _NoCapProvider)
    mount = em.StorageMount(name="nocap-mount", mount_type="testnocap",
                            path="", config="{}", is_enabled=True)
    mount_db.add(mount)
    mount_db.commit()
    with pytest.raises(mnt.MountError, match="can_generate_play_target"):
        mnt.resolve_play_target(f"mount://{mount.id}/f.mkv", mount_db)
    assert calls == []


def test_generate_gate_module_resolve_strm_uses_sidecar_gate(
        mount_db, monkeypatch):
    """模块换链口对 .strm 按旁挂门控：旁挂 False 即使换链 True
    也挡，且 read_text 根本不被调；反之旁挂 True、换链 False
    的 .strm 提供者不被错杀（见 test_resolve_target_cache）。"""
    reads = []

    class _NoSidecarProvider(MountProvider):
        def capabilities(self):
            return ProviderCapabilities(can_generate_play_target=True)

        def read_text(self, rel):
            reads.append(rel)
            return "http://x/target.mkv"

        def resolve_play_target(self, rel):
            raise AssertionError(".strm 不许调换链口")

    monkeypatch.setitem(mnt._PROVIDERS, "testnoside",
                        _NoSidecarProvider)
    mount = em.StorageMount(name="noside-mount", mount_type="testnoside",
                            path="", config="{}", is_enabled=True)
    mount_db.add(mount)
    mount_db.commit()
    with pytest.raises(mnt.MountError, match="can_read_sidecar"):
        mnt.resolve_play_target(f"mount://{mount.id}/x.strm", mount_db)
    assert reads == []


def test_generate_gate_module_resolve_allows_true_provider(
        mount_db, tmp_path):
    (tmp_path / "f.mkv").write_bytes(b"0123456789")
    mount = em.StorageMount(name="local-mount", mount_type="local",
                            path=str(tmp_path), config="{}", is_enabled=True)
    mount_db.add(mount)
    mount_db.commit()
    target = mnt.resolve_play_target(f"mount://{mount.id}/f.mkv", mount_db)
    assert target.kind == "local"
    assert target.value.endswith("f.mkv")
