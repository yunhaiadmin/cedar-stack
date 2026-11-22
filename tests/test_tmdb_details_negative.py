"""坏 tmdb_id 治理（S6）：详情真实 HTTP 404 落持久阴性标记，其余一律不落。

口径（防 2026-10-18 阴性缓存毒化事故重演）：
- **只有真实 HTTP 404** 才落盘标记（TMDB 明确说该 id 不存在）；
- 瞬态（5xx/网络/全 key 429/熔断/解析失败）照旧抛 ``TmdbTransientError``，不落；
- 错误载荷（200 带 success:false、无 id）不落；
- 401（key 配置坏了，不是 id 不存在）不落；
- 标记按阴性 TTL（默认 24h）过期，过期后重新发请求确认。

全部在假会话上跑，不发真请求；磁盘缓存指到 tmp_path。
"""
import json
import os

import pytest

from backend.emby_server import tmdb as tmdb_mod
from backend.emby_server import tmdb_cache
from backend.emby_server.tmdb import TmdbClient, TmdbTransientError

LANG = "zh-CN"


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "tmdb-cache"
    monkeypatch.setenv("EMBY_TMDB_CACHE_DIR", str(d))
    monkeypatch.setattr(tmdb_cache, "_disabled", False)
    monkeypatch.setattr(tmdb_cache, "_warned", False)
    return d


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from backend.emby_server.metasources import breaker as provider_breaker
    monkeypatch.setattr(tmdb_mod, "preferred_language",
                        lambda db=None, **k: LANG)
    monkeypatch.setattr(tmdb_mod.time, "sleep", lambda s: None)
    TmdbClient._cache.clear()
    provider_breaker.reset_all()
    yield
    TmdbClient._cache.clear()
    provider_breaker.reset_all()


class _Session:
    def __init__(self, answer):
        self.calls = []
        self._answer = answer

    def get(self, url, params):
        self.calls.append((url, dict(params)))
        return self._answer(url, params)


def _resp(status=200, payload=None, headers=None):
    class _R:
        status_code = status

        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return {} if payload is None else payload

    _R.headers = headers or {}
    return _R()


def _client(monkeypatch, answer) -> TmdbClient:
    monkeypatch.delenv("TMDB_API_KEYS", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb_mod, "_read_config_value", lambda db: "")
    client = TmdbClient()
    client._set_keys(["k1"], "env")
    client._limiter = tmdb_mod._RequestLimiter(rate=1_000_000.0, min_rate=1.0)
    client.session = _Session(answer)
    monkeypatch.setattr(client, "_ensure_session", lambda: None)
    return client


# ---------- _get_with_status 状态口径 ----------

def test_get_with_status_success_and_404_and_401(monkeypatch):
    client = _client(monkeypatch, lambda u, p: _resp(payload={"id": 1}))
    assert client._get_with_status("/movie/1", {}) == ({"id": 1}, 200)
    # 薄封装 _get 口径不变
    assert client._get("/movie/1", {}) == {"id": 1}

    client = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client._get_with_status("/movie/999", {}) == (None, 404)

    client = _client(monkeypatch, lambda u, p: _resp(status=401))
    assert client._get_with_status("/movie/1", {}) == (None, 401)


def test_get_with_status_no_session_is_zero(monkeypatch):
    client = _client(monkeypatch, lambda u, p: _resp())
    client.session = None
    monkeypatch.setattr(client, "_ensure_session", lambda: None)
    assert client._get_with_status("/movie/1", {}) == (None, 0)


# ---------- details() 阴性标记 ----------

def test_details_404_saves_negative_marker(cache_dir, monkeypatch):
    """① 详情 404 → 返回 None 且阴性标记落盘（load_details 能读到标记）"""
    client = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client.details("999001", "series") is None
    hit, data = tmdb_cache.load_details("tv", "999001", LANG)
    assert hit and data == {"_negative": True}


def test_details_negative_marker_short_circuits_second_call(cache_dir, monkeypatch):
    """② 标记命中后第二次 details() 不再发 HTTP 请求且返回 None。

    清掉 L1 再换新实例，强制走 L2 标记短路（跨进程/重启口径）。
    """
    client = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client.details("999002", "series") is None
    calls_after_first = len(client.session.calls)
    assert calls_after_first == 1

    TmdbClient._cache.clear()
    client2 = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client2.details("999002", "series") is None
    assert len(client2.session.calls) == 0, "标记命中必须零请求"
    # L1 已回填阴性（None）：同实例再调也不发请求
    assert client2.details("999002", "series") is None
    assert len(client2.session.calls) == 0


def test_details_all_keys_401_saves_no_marker(cache_dir, monkeypatch):
    """③ 全部 key 401 → 返回 None 且不落标记（401 是 key 坏了，不是 id 不存在）"""
    client = _client(monkeypatch, lambda u, p: _resp(status=401))
    assert client.details("123", "series") is None
    hit, _ = tmdb_cache.load_details("tv", "123", LANG)
    assert not hit


def test_details_5xx_raises_and_saves_no_marker(cache_dir, monkeypatch):
    """④ 5xx 瞬态 → 抛 TmdbTransientError 且不落标记"""
    client = _client(monkeypatch, lambda u, p: _resp(status=503))
    with pytest.raises(TmdbTransientError):
        client.details("124", "series")
    hit, _ = tmdb_cache.load_details("tv", "124", LANG)
    assert not hit


def test_details_error_payload_saves_no_marker(cache_dir, monkeypatch):
    """200 带 success:false 的错误载荷（无 id）→ 不落标记、不落正缓存"""
    payload = {"success": False, "status_code": 34,
               "status_message": "The resource you requested could not be found."}
    client = _client(monkeypatch, lambda u, p: _resp(payload=payload))
    assert client.details("125", "series") == payload  # 既有口径：原样返回
    hit, data = tmdb_cache.load_details("tv", "125", LANG)
    assert not hit, "错误载荷绝不落盘（防阴性毒化）"


def test_details_positive_still_uses_disk_cache(cache_dir, monkeypatch):
    """⑤ 正常详情仍走原缓存：正缓存命中不发请求、内容不变"""
    payload = {"id": 42, "name": "好剧", "credits": {"cast": []}}
    client = _client(monkeypatch, lambda u, p: _resp(payload=dict(payload)))
    assert client.details("42", "series") == payload
    assert len(client.session.calls) == 1

    TmdbClient._cache.clear()
    client2 = _client(monkeypatch, lambda u, p: _resp(
        payload={"id": 42, "name": "不该被读到", "credits": {"cast": []}}))
    assert client2.details("42", "series") == payload
    assert len(client2.session.calls) == 0


def test_details_negative_marker_expires_with_negative_ttl(cache_dir, monkeypatch):
    """⑥ 阴性标记按 negative TTL 过期：过期后重新发请求确认"""
    client = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client.details("999003", "series") is None
    assert len(client.session.calls) == 1

    # 把标记信封的时间戳拨到阴性 TTL 之外（但远在详情 TTL 之内）：
    # 若误用详情 TTL（30 天），这里仍会命中、不会重发——正是要防的。
    path = tmdb_cache._key_path("tv", "999003", 0, LANG)
    with open(path, encoding="utf-8") as f:
        envelope = json.load(f)
    envelope["at"] -= tmdb_cache._cfg_ttl_negative() + 10
    with open(path, "w", encoding="utf-8") as f:
        json.dump(envelope, f)
    hit, _ = tmdb_cache.load_details("tv", "999003", LANG)
    assert not hit, "阴性标记必须按阴性 TTL 过期"
    assert not os.path.exists(path), "过期标记文件应被顺手删除"

    TmdbClient._cache.clear()
    client2 = _client(monkeypatch, lambda u, p: _resp(status=404))
    assert client2.details("999003", "series") is None
    assert len(client2.session.calls) == 1, "过期后必须重新发请求"


def test_save_details_negative_roundtrip_and_save_details_unchanged(cache_dir):
    """标记存取原语：save_details_negative 落盘可读；save_details 拒绝空 data 的
    既有语义不变（新函数独立实现，不许改它）"""
    assert tmdb_cache.save_details_negative("movie", "777", LANG) is True
    hit, data = tmdb_cache.load_details("movie", "777", LANG)
    assert hit and data == {"_negative": True}
    assert tmdb_cache.save_details("movie", "778", None) is False
    assert tmdb_cache.save_details("movie", "778", {}) is False
