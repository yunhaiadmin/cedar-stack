"""标题置信门放宽（2026-11-12 用户亲批）：繁简归一 / 别名候选 / 数字与后缀归一。

背景：存量重认第二轮（1,186→1,160）复盘——没认上的主导失败模式是
译名/繁简/措辞差被 ``_hit_score`` 置信门驳回，候选其实是正片
（如《夺宝奇兵1：法柜奇兵》→ TMDB 85 被拒）。本单只放宽归一化与
候选标题集，**不许动** Tier 阈值、年份校验与多源一致口径：
- 繁→简归一进 ``_norm_text``（比对两侧同表折叠）；
- ``_hit_score`` 的可命中标题集纳入 TMDB alternative_titles；
- ``search()`` 全灭时对最像的候选补拉一次 details（带别名）再打分；
- 数字写法归一（全角数字/中文数字/罗马数字→阿拉伯）、无实义后缀
  （篇/之）剥离，同样只进归一化。
搜索缓存键继续用旧口径归一（``_norm_text_base``）：繁体查询候选
（WS6）不能被繁简折叠短路成简体缓存命中。
"""
import pytest

from backend.emby_server import tmdb as tmdb_mod
from backend.emby_server.tmdb import TmdbClient, _hit_score, _norm_text


# ---------------- 归一化：繁简 ----------------

def test_norm_folds_traditional_to_simplified():
    assert _norm_text("讓子彈飛") == _norm_text("让子弹飞")
    assert _norm_text("電視劇") == _norm_text("电视剧")


def test_hit_traditional_name_matches_simplified_hit_without_year():
    # 无年份兜底（Tier 1.2）时也必须靠繁简归一本身命中，而不是被拒。
    hit = {"title": "让子弹飞", "release_date": "2010-12-16"}
    sc = _hit_score("讓子彈飛", "讓子彈飛", hit, True, year=None)
    assert sc is not None and sc[0] == 2


# ---------------- 归一化：数字写法 ----------------

def test_norm_chinese_numerals():
    assert _norm_text("第十二季") == _norm_text("第12季")
    assert _norm_text("二十三") == _norm_text("23")
    assert _norm_text("一九八四") == _norm_text("1984")


def test_norm_fullwidth_and_roman_digits():
    assert _norm_text("速度与激情１０") == _norm_text("速度与激情10")
    assert _norm_text("星球大战Ⅸ") == _norm_text("星球大战9")


def test_norm_numeral_words_untouched():
    # 「千」「百」作实义字（千与千寻/百鸟朝凤）不许被数字归一误伤。
    assert _norm_text("千与千寻") == "千与千寻"
    assert _norm_text("百鸟朝凤") == "百鸟朝凤"


def test_hit_chinese_numeral_title_matches_arabic_hit():
    hit = {"title": "12怒汉", "release_date": "1957-04-10"}
    sc = _hit_score("十二怒汉", "十二怒汉", hit, True, year=None)
    assert sc is not None and sc[0] == 2


# ---------------- 归一化：无实义后缀 ----------------

def test_norm_meaningless_suffix():
    assert _norm_text("青云志篇") == _norm_text("青云志")
    assert _norm_text("大决战之") == _norm_text("大决战")


# ---------------- 别名候选 ----------------

_HIT_85 = {
    "id": 85,
    "title": "夺宝奇兵",
    "original_title": "Raiders of the Lost Ark",
    "release_date": "1981-06-12",
    "alternative_titles": {"titles": [
        {"title": "法柜奇兵"},
        {"title": "夺宝奇兵：法柜奇兵"},
    ]},
}


def test_hit_score_uses_alternative_titles():
    # 第二轮真实被拒例：本地名带序号「1」+ 副标题，只有别名全称能对上。
    # 无年份时 Tier 1.2 不可用；别名进标题集后，查询的 CJK 变体（数字
    # 本就不在 CJK 段里）与别名全称归一化精确相等 → Tier 2。
    sc = _hit_score("夺宝奇兵1：法柜奇兵", "夺宝奇兵1：法柜奇兵",
                    dict(_HIT_85), True, year=None)
    assert sc is not None and sc[0] == 2


def test_hit_score_without_aliases_still_rejects_same_case():
    hit = {k: v for k, v in _HIT_85.items() if k != "alternative_titles"}
    sc = _hit_score("夺宝奇兵1：法柜奇兵", "夺宝奇兵1：法柜奇兵",
                    hit, True, year=None)
    assert sc is None


def test_search_alias_pass_rescues_via_details(monkeypatch):
    """search() 全灭时补拉 details（带别名）再打分，能救回别名命中。

    本地名是台版译名《法柜奇兵》：与 TMDB 主标题《夺宝奇兵》毫无
    字符交集，所有既有通道（精确/模糊/包含/别名兜底）都过不了，
    只有把 TMDB 别名并进标题集才认得。
    """
    monkeypatch.setattr(tmdb_mod, "language_fallback_chain",
                        lambda db=None: ["zh-CN"])
    client = TmdbClient()
    raw_hit = {k: v for k, v in _HIT_85.items()
               if k != "alternative_titles"}
    monkeypatch.setattr(client, "_search_raw",
                        lambda q, y, k, lang=None: [dict(raw_hit)])
    calls = []

    def _details(tid, kind):
        calls.append(tid)
        return {"id": 85, "alternative_titles":
                _HIT_85["alternative_titles"]}

    monkeypatch.setattr(client, "details", _details)
    got = client.search("法柜奇兵", None, "movie")
    assert got is not None and got.get("id") == 85
    assert calls == ["85"]


def test_search_alias_pass_respects_year_guard(monkeypatch):
    """别名补拉不许绕过年份：候选年份与条目年份对不上仍不许直认。

    条目年份 1999、候选实为 1981 年片——别名再像也只能按既有口径
    降级/拒绝，不许因为走了别名通道就无条件接受（Tier 1 别名全称
    命中在年份不符时 rank 降级后仍可能胜出，这里用「同名不同年还有
    正确年份候选在场」验证赢家必须是年份对的那个）。
    """
    monkeypatch.setattr(tmdb_mod, "language_fallback_chain",
                        lambda db=None: ["zh-CN"])
    client = TmdbClient()
    wrong_year = dict(_HIT_85)  # 1981，带别名
    right = {"id": 999, "title": "夺宝奇兵1：法柜奇兵",
             "release_date": "1999-01-01"}
    monkeypatch.setattr(
        client, "_search_raw",
        lambda q, y, k, lang=None: [dict(wrong_year), dict(right)])
    monkeypatch.setattr(
        client, "details",
        lambda tid, kind: {"id": int(tid),
                           "alternative_titles":
                           _HIT_85["alternative_titles"]})
    got = client.search("夺宝奇兵1：法柜奇兵", 1999, "movie")
    assert got is not None and got.get("id") == 999


# ---------------- 护栏：阈值不许降 ----------------

def test_tier1_threshold_not_lowered():
    # LCS=5（<6）仍然拒绝：放宽只许走归一化，不许降相似度门槛。
    hit = {"title": "一二三四五九", "release_date": "2020-01-01"}
    sc = _hit_score("一二三四五六", "一二三四五六", hit, True, year=None)
    assert sc is None


def test_search_cache_key_norm_keeps_traditional_distinct():
    # 搜索缓存键不许繁简折叠：繁体候选查询（WS6）必须与简体不同键，
    # 否则繁体检索会被简体缓存短路、永远发不出请求。
    assert (tmdb_mod._norm_text_base("電視劇")
            != tmdb_mod._norm_text_base("电视剧"))
    assert tmdb_mod._norm_text_base("電視劇") == "電視劇"
