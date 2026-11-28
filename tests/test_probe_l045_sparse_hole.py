"""L-045: 头尾探测稀疏临时文件零空洞卡死 ffprobe（P2）。

生产实证（2026-11-28 簇 2 轮巡）：大 REMUX MKV（首簇跨过 4MiB 头窗）走头尾探测
时，ffprobe 在稀疏临时文件的全零空洞里做 EBML resync，30s 超时；超时被计为
挂载超时，3 次即熔断、条目 3 次后进 24h 长退避，6h 约 225 条。

复现夹具 tests/fixtures/l045_remux_head.mkv：真实 ffmpeg 产 MKV（H.264+AAC，
5s），挂了一个 3.9MB 附件把首个 Cluster 推到 3,900,837 处（与生产 REMUX
“首簇在 2MB 处、帧数据跨过头窗”同类形态）。把它当成 10GB 稀疏原文件
（头 4MB 真实、中间全零、尾 1KB 真实）喂给头尾探测：修前 ffprobe 在空洞里
超时，修后（EBML 临时文件截断为头窗）秒级返回 format。

真 ffprobe 用例在无 ffprobe 的环境自动跳过（CI 的 ubuntu-latest 无 ffprobe）；
mock 级用例（布局、超时计数）处处可跑。
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

from backend.emby_server import proc_util, scanner

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "l045_remux_head.mkv"
TOTAL = 10 * 1024 * 1024 * 1024  # 模拟 10GB REMUX
HEAD = int(scanner.PROBE_HEADTAIL_HEAD_BYTES)
TAIL = int(scanner.PROBE_HEADTAIL_TAIL_BYTES)
_HAS_FFPROBE = shutil.which("ffprobe") is not None
needs_ffprobe = pytest.mark.skipif(not _HAS_FFPROBE, reason="needs real ffprobe (L-045)")


def _sparse_remux_source(tmp_path):
    """10GB 稀疏原文件：头=夹具真实字节，中间全零，尾=夹具末尾 1KB。"""
    data = _FIXTURE.read_bytes()
    src = tmp_path / "remux.mkv"
    fd = os.open(src, os.O_CREAT | os.O_RDWR | os.O_TRUNC)
    try:
        os.ftruncate(fd, TOTAL)
        os.pwrite(fd, data, 0)
        os.pwrite(fd, data[-1024:], TOTAL - 1024)
    finally:
        os.close(fd)
    return str(src)


# ---------- 真 ffprobe：挂死复现 -> 修后秒级返回 ----------

@needs_ffprobe
def test_probe_metadata_sparse_hole_returns_format_fast(monkeypatch, tmp_path):
    """修前：头尾 8s 超时 + 第一跳 8s 超时 -> degraded、耗时 ~16s；
    修后：头尾截断文件秒级返回 format，_probe_backend=headtail。"""
    monkeypatch.setattr(scanner, "PROBE_FILE_TIMEOUT", 8.0)
    src = _sparse_remux_source(tmp_path)
    t0 = time.monotonic()
    info = scanner.probe_metadata(src, {}, size=TOTAL, container="mkv")
    el = time.monotonic() - t0
    assert info.get("_probe_backend") == "headtail", info
    assert info["duration_ticks"] > 0, info
    assert info.get("video_codec") == "H264", info
    assert el < 5.0, "headtail probe took %.1fs, expected <5s" % el


@needs_ffprobe
def test_headtail_temp_mkv_is_probeable_without_hole(monkeypatch, tmp_path):
    """临时文件这一层：修前是 10GB 稀疏文件（ffprobe 8s 超时），
    修后是截断为头窗的文件（ffprobe 秒级出 format）。"""
    monkeypatch.setattr(scanner, "PROBE_FILE_TIMEOUT", 8.0)
    src = _sparse_remux_source(tmp_path)
    tmp = scanner._headtail_sparse_temp(src, {}, TOTAL)
    assert tmp is not None
    try:
        t0 = time.monotonic()
        data = scanner._ffprobe(src, {}, size=TOTAL, local_path=tmp,
                                probesize=scanner.PROBE_HEADTAIL_PROBESIZE,
                                analyzeduration=scanner.PROBE_HEADTAIL_ANALYZEDURATION)
        el = time.monotonic() - t0
        assert data and data.get("format"), "ffprobe should read format from the headtail temp"
        assert el < 5.0, "ffprobe on headtail temp took %.1fs, expected <5s" % el
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# ---------- mock 级：EBML 只取头窗（布局） ----------

def test_headtail_ebml_truncates_to_head(monkeypatch, tmp_path):
    """EBML 容器（mkv/webm）：只取头窗，临时文件截断为头窗大小，不取尾、不留空洞。"""
    seen = []

    def fake_fetch(path, headers, start, end):
        seen.append((start, end))
        return b"H" * (end - start + 1)

    monkeypatch.setattr(scanner, "_http_fetch_range", fake_fetch)
    monkeypatch.setattr(scanner.tempfile, "mkstemp",
                        lambda prefix, suffix: (os.open(str(tmp_path / "ht.mkv"),
                                                       os.O_CREAT | os.O_RDWR),
                                                str(tmp_path / "ht.mkv")))
    tmp = scanner._headtail_sparse_temp("https://x/movie.mkv", {}, TOTAL, container="mkv")
    try:
        assert tmp is not None
        assert os.path.getsize(tmp) == HEAD, "EBML temp must be truncated to the head window"
        assert seen == [(0, HEAD - 1)], "EBML must fetch head only, never the tail: %s" % (seen,)
        with open(tmp, "rb") as fh:
            assert fh.read(HEAD) == b"H" * HEAD
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def test_headtail_ebml_detected_from_suffix(monkeypatch, tmp_path):
    """container 为空时以后缀兜底：.mkv 路径同样只取头窗。"""
    seen = []

    def fake_fetch(path, headers, start, end):
        seen.append((start, end))
        return b"H" * (end - start + 1)

    monkeypatch.setattr(scanner, "_http_fetch_range", fake_fetch)
    monkeypatch.setattr(scanner.tempfile, "mkstemp",
                        lambda prefix, suffix: (os.open(str(tmp_path / "ht.mkv"),
                                                       os.O_CREAT | os.O_RDWR),
                                                str(tmp_path / "ht.mkv")))
    tmp = scanner._headtail_sparse_temp("https://x/movie.mkv?sig=abc", {}, TOTAL)
    try:
        assert tmp is not None
        assert os.path.getsize(tmp) == HEAD
        assert seen == [(0, HEAD - 1)]
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def test_headtail_mp4_keeps_sparse_layout(monkeypatch, tmp_path):
    """非 EBML（mp4：moov 可能在尾）保持旧口径：头尾双取、同尺寸稀疏文件。"""
    seen = []

    def fake_fetch(path, headers, start, end):
        seen.append((start, end))
        return b"H" * (end - start + 1)

    monkeypatch.setattr(scanner, "_http_fetch_range", fake_fetch)
    monkeypatch.setattr(scanner.tempfile, "mkstemp",
                        lambda prefix, suffix: (os.open(str(tmp_path / "ht.mp4"),
                                                       os.O_CREAT | os.O_RDWR),
                                                str(tmp_path / "ht.mp4")))
    tmp = scanner._headtail_sparse_temp("https://x/movie.mp4", {}, TOTAL, container="mp4")
    try:
        assert tmp is not None
        assert os.path.getsize(tmp) == TOTAL
        assert sorted(seen) == sorted([(0, HEAD - 1), (TOTAL - TAIL, TOTAL - 1)])
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# ---------- mock 级：头尾超时不计入挂载熔断 ----------

def test_headtail_timeout_excluded_from_mount_timeout_count(monkeypatch):
    """头尾 ffprobe 超时是探测方法失效（本地临时文件），挂载已靠头尾下载自证存活：
    不应计入 worker 的超时计数（否则 3 次即误触发 MountBreaker）。"""
    monkeypatch.setattr(scanner, "shutil_which", lambda c: "/usr/bin/ffprobe")
    monkeypatch.setattr(scanner, "_remote_returns_login_page", lambda *a, **k: False)
    monkeypatch.setattr(scanner, "_http_fetch_range",
                        lambda path, headers, start, end: b"H" * (end - start + 1))

    class _Empty:
        stdout = "{}"
        stderr = ""
        returncode = 0

    def fake_run(cmd, timeout):
        argv = [str(a) for a in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
        if any("probe-headtail-" in a for a in argv):
            # 走真实 bounded_run 的一次短超时：计数器确会 +1，
            # 验的是 probe_metadata 把它摘掉（只测这一层的归属，不测 ffprobe 本身）。
            return proc_util.bounded_run(["sleep", "5"], timeout=0.01)
        return _Empty()

    monkeypatch.setattr(scanner, "_run_probe_cmd", fake_run)
    before = proc_util.timeout_count()
    info = scanner.probe_metadata("https://x/movie.mkv", {}, size=TOTAL, container="mkv")
    assert proc_util.timeout_count() - before == 0, (
        "headtail timeout must not count toward mount timeouts")
    assert info.get("_degraded") is True


def test_discard_timeouts_floors_at_zero():
    """proc_util.discard_timeouts：减超不下穿 0，减 0/负数无动作。"""
    before = proc_util.timeout_count()
    proc_util.discard_timeouts(10 ** 9)
    assert proc_util.timeout_count() == 0
    proc_util.discard_timeouts(0)
    proc_util.discard_timeouts(-5)
    assert proc_util.timeout_count() == 0
    assert before >= 0
