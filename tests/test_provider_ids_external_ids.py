# -*- coding: utf-8 -*-
"""ProviderIds 解析 external_ids + 单集 Genres 继承（2026-11-24 诊断报告缺口 #2/#5）。

背景（metadata-client-matrix-20261124.md 三.2）：
- ``_provider_ids`` 旧实现只读 tmdb_id/imdb_id 两列，外加 getattr 恒为
  None 的 douban_id/bangumi_id 属性（emby_items 根本没有这两列），且从不
  解析 external_ids JSON——2,098 个身份只存在于 external_ids 的条目在
  客户端眼里 ProviderIds 为空，靠它匹配弹幕/评分的播放器等于无元数据。
- ProviderIds/People 都做了集→剧继承，唯独 Genres 只读单集自身列（恒空）。

钉死口径：
- ProviderIds 优先级：专列（tmdb_id/imdb_id）> external_ids 同名键 >
  单集时按字段继承所属剧（剧自身也走专列 > external_ids 同口径解析）；
- external_ids 七键（identity_gate.EXTERNAL_ID_KEYS）全下发，键名用
  Emby 惯例名（Tmdb/Imdb/Tvdb/TvMaze/Douban/Bangumi/AniList），值转字符串、
  空白跳过、坏 JSON 不崩；
- Genres：单集自身有则用自身，为空才继承剧集；非单集不继承。
"""
import json
from types import SimpleNamespace

from backend.emby_server.api import _genres_of, _provider_ids


def _item(item_type="series", tmdb=None, imdb=None, ext=None, series=None,
          genres=None):
    return SimpleNamespace(
        item_type=item_type, tmdb_id=tmdb, imdb_id=imdb,
        external_ids=ext, series=series, genres=genres,
    )


def _ext(**kw):
    return json.dumps(kw)


# ---------- ProviderIds：external_ids 解析 ----------

def test_bangumi_only_in_external_ids():
    # 报告实测样本形态：tmdb/imdb 列空、external_ids 只有 bangumi
    item = _item(ext=_ext(bangumi="385209"))
    assert _provider_ids(item) == {"Bangumi": "385209"}


def test_douban_only_in_external_ids():
    item = _item(item_type="movie", ext=_ext(douban="1292052"))
    assert _provider_ids(item) == {"Douban": "1292052"}


def test_all_external_keys_emitted():
    item = _item(ext=_ext(tmdb="100", imdb="tt100", tvdb="200",
                          tvmaze="300", douban="400", bangumi="500",
                          anilist="600"))
    assert _provider_ids(item) == {
        "Tmdb": "100", "Imdb": "tt100", "Tvdb": "200", "TvMaze": "300",
        "Douban": "400", "Bangumi": "500", "AniList": "600",
    }


def test_column_wins_over_external_ids():
    item = _item(tmdb="999", imdb="tt999",
                 ext=_ext(tmdb="100", imdb="tt100", bangumi="500"))
    assert _provider_ids(item) == {
        "Tmdb": "999", "Imdb": "tt999", "Bangumi": "500"}


def test_external_ids_fill_when_columns_empty():
    item = _item(ext=_ext(tmdb="100", imdb="tt100"))
    assert _provider_ids(item) == {"Tmdb": "100", "Imdb": "tt100"}


def test_external_numeric_value_coerced_to_str():
    item = _item(ext=_ext(tmdb=12345, bangumi=385209))
    assert _provider_ids(item) == {"Tmdb": "12345", "Bangumi": "385209"}


def test_external_blank_values_skipped():
    item = _item(ext=_ext(bangumi="  ", douban=""))
    assert _provider_ids(item) == {}


def test_external_bad_json_does_not_crash():
    item = _item(tmdb="999", ext="{not json")
    assert _provider_ids(item) == {"Tmdb": "999"}


def test_external_ids_dict_form_accepted():
    # 内存中的条目对象可能直接挂 dict（与 identity_gate._external_ids_map 同口径）
    item = _item(ext={"bangumi": "385209"})
    assert _provider_ids(item) == {"Bangumi": "385209"}


def test_unknown_external_keys_ignored():
    item = _item(ext=_ext(mal="123", something="x"))
    assert _provider_ids(item) == {}


# ---------- ProviderIds：单集继承（含 external_ids 同口径） ----------

def test_episode_inherits_bangumi_from_series_external_ids():
    series = _item(ext=_ext(bangumi="385209", tmdb="100"))
    ep = _item(item_type="episode", series=series)
    assert _provider_ids(ep) == {"Bangumi": "385209", "Tmdb": "100"}


def test_episode_own_external_ids_win_over_series():
    series = _item(ext=_ext(bangumi="111"))
    ep = _item(item_type="episode", ext=_ext(bangumi="222"), series=series)
    assert _provider_ids(ep) == {"Bangumi": "222"}


def test_episode_mixed_sources_partial_inherit():
    # 集自身专列有 imdb、external 有 douban；剧有 tmdb 专列 + bangumi external
    series = _item(tmdb="100", ext=_ext(bangumi="500"))
    ep = _item(item_type="episode", imdb="tt999",
               ext=_ext(douban="400"), series=series)
    assert _provider_ids(ep) == {
        "Imdb": "tt999", "Douban": "400", "Tmdb": "100", "Bangumi": "500"}


# ---------- Genres：单集继承 ----------

def test_episode_own_genres_win():
    series = _item(genres="剧情,悬疑")
    ep = _item(item_type="episode", genres="动画,冒险", series=series)
    assert _genres_of(ep) == ["动画", "冒险"]


def test_episode_empty_genres_inherit_series():
    series = _item(genres="动画,冒险")
    ep = _item(item_type="episode", genres="", series=series)
    assert _genres_of(ep) == ["动画", "冒险"]


def test_episode_none_genres_inherit_series():
    series = _item(genres="动画,冒险")
    ep = _item(item_type="episode", genres=None, series=series)
    assert _genres_of(ep) == ["动画", "冒险"]


def test_episode_no_series_no_genres():
    ep = _item(item_type="episode", genres=None, series=None)
    assert _genres_of(ep) == []


def test_movie_does_not_inherit_genres():
    series = _item(genres="剧情")
    movie = _item(item_type="movie", genres=None, series=series)
    assert _genres_of(movie) == []


def test_series_uses_own_genres():
    series = _item(genres="剧情,悬疑")
    assert _genres_of(series) == ["剧情", "悬疑"]
