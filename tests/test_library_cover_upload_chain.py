"""媒体库封面直传链回归（2026-11-19 缺口修复）：

断点不在后端：上传/删除端点、管理端 API 封装都早已存在，缺的是
EmbyAdmin.vue 封面区根本没有上传控件。本文件把两件事钉住：

- 后端链实测：上传 → cover_path 落库落盘 → 用户端解析到这张图 →
  删除 → 解析回落（不再是 uploaded）；
- 前端契约：封面区必须真的接上 uploadLibraryCover / removeLibraryCover，
  服务器页必须真的接上 saveEmbyServerName（防再被改没）。
"""
import io
import os
import pathlib

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.emby_server import library_covers, models as em
from backend.emby_server import portal

REPO = pathlib.Path(__file__).resolve().parent.parent


class _FakeUpload:
    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(data)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 450), (200, 60, 60)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def cover_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBY_IMAGE_DIR", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    lib = em.Library(name="电影库", guid="lib-cover-chain", collection_type="movies",
                     is_enabled=True)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    yield db, lib, tmp_path
    db.close()
    engine.dispose()


def test_upload_then_user_side_resolves_then_delete_falls_back(cover_ctx):
    db, lib, img_root = cover_ctx
    res = portal.upload_library_cover(
        lib.id, _FakeUpload("cover.png", "image/png", _png_bytes()), None, db)
    assert res["success"] is True
    db.expire_all()
    lib = db.query(em.Library).filter_by(id=lib.id).first()
    assert lib.cover_path and lib.cover_path.startswith("library-covers/")
    assert (img_root / lib.cover_path).is_file()

    resolved = library_covers.resolve_library_cover(db, lib, "poster")
    assert resolved["source"] == "uploaded"
    assert resolved["url"] == f"/emby/Items/{lib.guid}/Images/Primary"

    out = portal.remove_library_cover(lib.id, None, db)
    assert out["success"] is True
    db.expire_all()
    lib = db.query(em.Library).filter_by(id=lib.id).first()
    assert lib.cover_path is None
    resolved = library_covers.resolve_library_cover(db, lib, "poster")
    assert resolved["source"] != "uploaded"


def test_admin_frontend_wires_cover_upload_and_delete():
    src = (REPO / "admin_frontend/src/views/EmbyAdmin.vue").read_text(encoding="utf-8")
    assert "uploadLibraryCover(lib.id, file)" in src, "封面区必须接上上传封装"
    assert "removeLibraryCover(lib.id)" in src, "封面区必须接上删除封装"
    assert "上传封面图" in src, "封面区必须有上传按钮"
    assert 'accept="image/jpeg,image/png,image/webp"' in src, "文件选择要限定图片类型"
    api = (REPO / "admin_frontend/src/api/admin.ts").read_text(encoding="utf-8")
    assert "export const uploadLibraryCover" in api
    assert "export const removeLibraryCover" in api


def test_admin_frontend_wires_server_display_name():
    src = (REPO / "admin_frontend/src/views/Servers.vue").read_text(encoding="utf-8")
    assert "服务器显示名" in src, "服务器页必须有显示名卡片"
    assert "saveEmbyServerName(name)" in src, "保存必须走改名封装"
    assert "fetchEmbyConnection()" in src, "进页必须回显当前生效的显示名"
    api = (REPO / "admin_frontend/src/api/admin.ts").read_text(encoding="utf-8")
    assert "export const saveEmbyServerName" in api
    assert "export const fetchEmbyConnection" in api
    assert "`${E}/server-name`" in api
