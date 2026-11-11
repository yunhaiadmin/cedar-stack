# -*- coding: utf-8 -*-
"""sender 图片 file_id 复用（2026-11-11 盲盒面板点击卡顿修复）。

钉死口径：
- 同一张本地图（品牌图/面板图）首次发送走 multipart 整图上传，
  成功后从响应里记下 Telegram file_id；后续发送同一张图直接按
  file_id 引用（JSON sendPhoto），不再整图重传——旧实现每发一张
  结果卡都要整图上传一次，是点击后等很久的主导耗时。
- file_id 发送被 API 拒绝（失效/换了 bot）时，本次自动回退重新
  上传并刷新缓存，用户无感、图片照常发出。
- send_photo_ex、edit_message_media 同口径复用 file_id。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.tg_bot import sender


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    sender._PHOTO_FILE_ID_CACHE.clear()
    try:
        yield session
    finally:
        sender._PHOTO_FILE_ID_CACHE.clear()
        session.close()
        engine.dispose()


class _Transport:
    """打桩 _call/_call_ex/_call_multipart_ex，记录每次发送形态。"""

    def __init__(self, fail_file_id=False):
        self.uploads = []      # (method, payload, files)
        self.json_calls = []   # (method, payload)
        self.fail_file_id = fail_file_id

    def install(self, monkeypatch):
        def fake_call(db_, method, payload):
            self.json_calls.append((method, payload))
            if self.fail_file_id and payload.get("photo"):
                return False, "Bad Request: wrong file_id"
            media = payload.get("media")
            if self.fail_file_id and isinstance(media, dict) \
                    and media.get("media"):
                return False, "Bad Request: wrong file_id"
            return True, None

        def fake_call_ex(db_, method, payload):
            self.json_calls.append((method, payload))
            if self.fail_file_id and payload.get("photo"):
                return False, "Bad Request: wrong file_id", None
            return True, None, {"message_id": 777}

        def fake_upload(db_, method, payload, files):
            self.uploads.append((method, payload, files))
            result = {"message_id": 555,
                      "photo": [{"file_id": "small-id", "width": 90},
                                {"file_id": "big-id", "width": 800}]}
            return True, None, result

        monkeypatch.setattr(sender, "_call", fake_call)
        monkeypatch.setattr(sender, "_call_ex", fake_call_ex)
        monkeypatch.setattr(sender, "_call_multipart_ex", fake_upload)


def test_send_photo_uploads_once_then_reuses_file_id(db, monkeypatch):
    tp = _Transport()
    tp.install(monkeypatch)
    ok, err = sender.send_photo(db, 123, "▎标题")
    assert ok and err is None
    ok, err = sender.send_photo(db, 123, "▎标题")
    assert ok and err is None
    assert len(tp.uploads) == 1                   # 只整图上传一次
    assert len(tp.json_calls) == 1                # 第二次走 file_id
    method, payload = tp.json_calls[0]
    assert method == "sendPhoto"
    assert payload["photo"] == "big-id"           # 取最大尺寸 file_id
    assert payload["caption"] == "▎标题"


def test_send_photo_stale_file_id_falls_back_to_upload(db, monkeypatch):
    tp = _Transport(fail_file_id=True)
    tp.install(monkeypatch)
    assert sender.send_photo(db, 123, "▎标题")[0]  # 上传并缓存
    assert sender.send_photo(db, 123, "▎标题")[0]  # file_id 被拒→回退重传
    assert len(tp.uploads) == 2
    # 缓存已按新上传刷新：第三次仍先试 file_id，被拒继续回退
    assert sender.send_photo(db, 123, "▎标题")[0]
    assert len(tp.uploads) == 3


def test_send_photo_ex_reuses_file_id_and_returns_message_id(db, monkeypatch):
    tp = _Transport()
    tp.install(monkeypatch)
    ok, err, mid = sender.send_photo_ex(db, 123, "▎标题")
    assert (ok, err, mid) == (True, None, 555)
    ok, err, mid = sender.send_photo_ex(db, 123, "▎标题")
    assert (ok, err, mid) == (True, None, 777)    # file_id 路径的 message_id
    assert len(tp.uploads) == 1


def test_edit_message_media_reuses_file_id(db, monkeypatch):
    tp = _Transport()
    tp.install(monkeypatch)
    assert sender.send_photo(db, 123, "▎标题")[0]  # 先缓存 file_id
    ok, err = sender.edit_message_media(db, 123, 456, "▎新标题", None)
    assert ok and err is None
    assert len(tp.uploads) == 1                   # 换图没再整图上传
    method, payload = tp.json_calls[-1]
    assert method == "editMessageMedia"
    assert payload["media"]["media"] == "big-id"
    assert payload["media"]["caption"] == "▎新标题"
