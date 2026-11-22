# -*- coding: utf-8 -*-
"""远程探测第三跳（整文件兜底）流量闸收紧（2026-11-23，谷歌盘调用削减第一批第 2 项）。

钉住四件事：
1. 默认上限从 1 GiB 收紧到 200 MiB：生产实测 200MiB–1GiB 段终局成功率
   99.92% vs >1GiB 段（本就无第三跳）99.94%，第三跳对成功率没有可测贡献，
   而决策点到达约 4 次/分钟（活跃探测期），整拉流量与成功率不成比例。
2. 已知大小在上限内的文件，第三跳照常走（行为不倒退）。
3. 未知大小（size<=0）不许再无界整拉：改走有界整拉（拉到上限即停）；
   拉全了与旧行为等价，截断了拿前缀试探、但假的 size/bit_rate 不许落库。
4. 上限<=0（关闸）时回到旧行为，包括未知大小也走 ffprobe 自由 seek。

全部离线：ffprobe / 抓取 helper 全部替身，不做真实网络请求。
"""
import os

import httpx
import pytest

from backend.emby_server import scanner


URL = "https://rclone:5574/[drv:]/movies/Some Movie (2020).mkv"
CAP = 200 << 20  # 200 MiB


def _patch(monkeypatch, ffprobe_impl=None, pull_impl=None):
    """把 probe_metadata 的前几跳与外部依赖全部替身化，只留第三跳决策。"""
    calls = {"ffprobe": [], "pull": []}

    def fake_ffprobe(path, headers=None, size=0, ranged=True, local_path="",
                     **kwargs):
        calls["ffprobe"].append(
            {"ranged": ranged, "local_path": local_path, "size": size})
        if ffprobe_impl is not None:
            return ffprobe_impl(path, headers=headers, size=size,
                                ranged=ranged, local_path=local_path)
        return {}

    def fake_pull(path, headers, max_bytes):
        calls["pull"].append({"path": path, "max_bytes": max_bytes})
        if pull_impl is not None:
            return pull_impl(path, headers, max_bytes)
        return None

    monkeypatch.setattr(scanner, "_remote_returns_login_page",
                        lambda *a, **k: False)
    monkeypatch.setattr(scanner, "_headtail_sparse_temp", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_dual_range_probe", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_mediainfo", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_last_ffprobe_timed_out", lambda: False)
    monkeypatch.setattr(scanner, "_ffprobe", fake_ffprobe)
    monkeypatch.setattr(scanner, "_bounded_whole_file_temp", fake_pull,
                        raising=False)
    return calls


def _unbounded_calls(calls):
    """ffprobe 自由 seek（ranged=False 且不是本地文件）＝无界整拉。"""
    return [c for c in calls["ffprobe"]
            if c["ranged"] is False and not c["local_path"]]


# ---------- 1. 默认阈值收紧到 200 MiB ----------

def test_default_cap_is_200_mib():
    assert scanner.PROBE_WHOLE_FILE_MAX_BYTES == CAP


def test_known_500mib_skips_third_hop_under_default_cap(monkeypatch):
    """500 MiB：旧闸（1 GiB）内会整拉，新闸下必须跳过、落 degraded。"""
    calls = _patch(monkeypatch)
    info = scanner.probe_metadata(URL, size=500 << 20, container="mkv")
    assert info.get("_degraded") is True
    assert _unbounded_calls(calls) == []
    assert calls["pull"] == []


# ---------- 2. 上限内照常第三跳 ----------

def test_known_small_file_still_takes_third_hop(monkeypatch):
    monkeypatch.setattr(scanner, "PROBE_WHOLE_FILE_MAX_BYTES", CAP)

    def impl(path, headers=None, size=0, ranged=True, local_path=""):
        if ranged is False:
            return {"format": {"duration": "600.0"}, "streams": []}
        return {}

    calls = _patch(monkeypatch, ffprobe_impl=impl)
    info = scanner.probe_metadata(URL, size=100 << 20, container="mkv")
    assert info["duration_ticks"] == 600 * 10_000_000
    assert not info.get("_degraded")
    assert len(_unbounded_calls(calls)) == 1
    assert calls["pull"] == []


# ---------- 3. 未知大小走有界整拉 ----------

def test_unknown_size_uses_bounded_pull(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, "PROBE_WHOLE_FILE_MAX_BYTES", CAP)
    tmp = str(tmp_path / "whole.bin")
    with open(tmp, "wb") as fh:
        fh.write(b"x" * 1234)

    def pull_impl(path, headers, max_bytes):
        return (tmp, 1234, True)

    def ff_impl(path, headers=None, size=0, ranged=True, local_path=""):
        if local_path:
            return {"format": {"duration": "700.0", "size": "1234",
                               "bit_rate": "1000"}, "streams": []}
        return {}

    calls = _patch(monkeypatch, ffprobe_impl=ff_impl, pull_impl=pull_impl)
    info = scanner.probe_metadata(URL, size=0, container="mkv")
    assert calls["pull"] and calls["pull"][0]["max_bytes"] == CAP
    assert _unbounded_calls(calls) == [], "未知大小不许再走无界自由 seek"
    assert info["duration_ticks"] == 700 * 10_000_000
    assert not info.get("_degraded")
    # 拉全了＝真文件：size/bit_rate 照常采信
    assert info["size"] == 1234
    assert info["bitrate"] == 1000


def test_unknown_size_truncated_pull_strips_fake_size_bitrate(
        monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, "PROBE_WHOLE_FILE_MAX_BYTES", CAP)
    tmp = str(tmp_path / "whole.bin")
    with open(tmp, "wb") as fh:
        fh.write(b"x" * 4096)

    def pull_impl(path, headers, max_bytes):
        return (tmp, CAP, False)  # 到上限截断

    def ff_impl(path, headers=None, size=0, ranged=True, local_path=""):
        if local_path:
            return {"format": {"duration": "800.0", "size": str(CAP),
                               "bit_rate": "999999"}, "streams": []}
        return {}

    calls = _patch(monkeypatch, ffprobe_impl=ff_impl, pull_impl=pull_impl)
    info = scanner.probe_metadata(URL, size=0, container="mkv")
    # 前缀探出的时长照常采信
    assert info["duration_ticks"] == 800 * 10_000_000
    assert not info.get("_degraded")
    # 但截断文件的 size/bit_rate 是假的，不许落库
    assert info["size"] == 0
    assert info["bitrate"] == 0


def test_unknown_size_pull_failure_degrades_without_unbounded(monkeypatch):
    monkeypatch.setattr(scanner, "PROBE_WHOLE_FILE_MAX_BYTES", CAP)
    calls = _patch(monkeypatch)  # pull 默认返回 None
    info = scanner.probe_metadata(URL, size=0, container="mkv")
    assert info.get("_degraded") is True
    assert calls["pull"], "未知大小必须先试有界整拉"
    assert _unbounded_calls(calls) == []


# ---------- 4. 关闸（<=0）回到旧行为 ----------

def test_cap_disabled_restores_unbounded_for_unknown_size(monkeypatch):
    monkeypatch.setattr(scanner, "PROBE_WHOLE_FILE_MAX_BYTES", 0)

    def impl(path, headers=None, size=0, ranged=True, local_path=""):
        if ranged is False:
            return {"format": {"duration": "60.0"}, "streams": []}
        return {}

    calls = _patch(monkeypatch, ffprobe_impl=impl)
    info = scanner.probe_metadata(URL, size=0, container="mkv")
    assert info["duration_ticks"] == 60 * 10_000_000
    assert calls["pull"] == []
    assert len(_unbounded_calls(calls)) == 1


# ---------- _bounded_whole_file_temp 本体 ----------

class _FakeResp:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, n):
        yield from self._chunks


class _FakeClient:
    resp = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, headers=None):
        return type(self).resp


def _use_fake_http(monkeypatch, resp):
    _FakeClient.resp = resp
    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_bounded_pull_complete(monkeypatch):
    _use_fake_http(monkeypatch, _FakeResp([b"ab", b"cde"]))
    out = scanner._bounded_whole_file_temp(URL, None, 1000)
    assert out is not None
    tmp, written, complete = out
    try:
        assert written == 5 and complete is True
        with open(tmp, "rb") as fh:
            assert fh.read() == b"abcde"
    finally:
        os.unlink(tmp)


def test_bounded_pull_truncates_at_cap(monkeypatch):
    _use_fake_http(monkeypatch, _FakeResp([b"a" * 600, b"b" * 600]))
    out = scanner._bounded_whole_file_temp(URL, None, 1000)
    assert out is not None
    tmp, written, complete = out
    try:
        assert written == 1000 and complete is False
        assert os.path.getsize(tmp) == 1000
    finally:
        os.unlink(tmp)


def test_bounded_pull_http_error_returns_none_and_cleans_up(monkeypatch):
    import tempfile as _tf
    made = []
    real_mkstemp = _tf.mkstemp

    def recording_mkstemp(*a, **k):
        fd, p = real_mkstemp(*a, **k)
        made.append(p)
        return fd, p

    monkeypatch.setattr(_tf, "mkstemp", recording_mkstemp)
    _use_fake_http(monkeypatch, _FakeResp([b"x"], status=403))
    assert scanner._bounded_whole_file_temp(URL, None, 1000) is None
    assert made and not os.path.exists(made[0]), "失败必须删掉临时文件"
