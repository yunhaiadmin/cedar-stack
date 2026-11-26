"""内封字幕抽取命令回归（2026-11-26 用户实报，条目 416741 内封 subrip 恒 404）。

根因：抽取输出的 tmp 名以 .part 结尾（A-121 唯一后缀），ffmpeg 无法
按扩展名猜测输出格式，而命令只给了编码 `-c:s webvtt`、没给封装
格式 `-f webvtt`——真实 ffmpeg 以 "Unable to find a suitable output
format" 退出 1，端点对所有内封文本字幕恒回 404「该字幕轨无法抽取
为文本字幕」。既有并发测试用假 subprocess.run 只写文件、不校验
命令，故未拦住；本测试钉住命令契约。

另钉超时下限：抽取须解封装整片，远程挂载整读以分钟计——实测
1h50m 1080p 条目经 rcd 抽取约 190 秒，旧 180 秒上限会让长片在
抽完前先超时 504。
"""
from __future__ import annotations

import os
import types

import pytest

from backend.emby_server import subtitles


@pytest.fixture
def _cold_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles, "SUB_CACHE_DIR", str(tmp_path / "subs"))
    monkeypatch.setattr(subtitles.shutil, "which", lambda *_a, **_k: "/bin/ffmpeg")
    media = tmp_path / "media.mkv"
    media.write_bytes(b"\x00" * 64)
    return str(media)


def test_extract_cmd_declares_webvtt_format(_cold_cache, monkeypatch):
    seen = {}

    def _fake_run(cmd, capture_output=False, timeout=None):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        with open(cmd[-1], "wb") as f:
            f.write(b"WEBVTT\n")
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subtitles.subprocess, "run", _fake_run)
    out = subtitles.extract_embedded(_cold_cache, "guid-fmt", 2, 0)
    assert os.path.isfile(out)
    cmd = seen["cmd"]
    # 输出 tmp 以 .part 结尾：格式必须显式声明，不能指望扩展名猜测
    assert cmd[-1].endswith(".part")
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "webvtt"
    # 超时必须留足远程整片抽取的量级（实测约 190 秒 / 1h50m）
    assert seen["timeout"] is not None and seen["timeout"] >= 600
