"""高级刮削：人物刮削（FMBY 候选 4）。

钉死：人物刮削开关关时不问源；只填空缺永不覆盖；锚点从严
（双方 tmdb id 俱在且不同时跳过，哪怕名字相同）；单人失败
不拖垮整批。

（同批的 AI 参谋已随 AI 整链删除，其用例一并下线。）
"""


from backend.emby_server.metasources import people_scrape as people


def _people_on(monkeypatch):
    monkeypatch.setattr(people, "PEOPLE_SCRAPE_ENABLED", 1)


# ---------------- 人物刮削 ----------------

def test_people_switch_off_never_calls_fetcher(monkeypatch):
    monkeypatch.setattr(people, "PEOPLE_SCRAPE_ENABLED", 0)
    called = []
    result = people.scrape_people(
        [{"name": "甲"}],
        fetcher=lambda r: called.append(r) or {"name": "甲", "role": "主演"})
    assert result == {"processed": 0, "filled": [], "skipped": [],
                      "failed": []}
    assert called == []


def test_people_fill_only_never_overwrites(monkeypatch):
    _people_on(monkeypatch)
    record = {"name": "甲", "role": "已有角色", "image": "",
              "person_tmdb_id": "11"}
    result = people.scrape_people(
        [record],
        fetcher=lambda r: {"name": "甲", "role": "新角色",
                           "image": "https://img/x.jpg",
                           "person_tmdb_id": "11",
                           "sources": ["tmdb"]})
    assert len(result["filled"]) == 1
    filled = result["filled"][0]
    assert filled["filled"] == ["image"]
    assert filled["record"]["role"] == "已有角色"  # 未覆盖
    assert filled["record"]["person_tmdb_id"] == "11"  # 未覆盖
    assert filled["record"]["image"] == "https://img/x.jpg"
    assert filled["evidence"] == {"sources": ["tmdb"]}


def test_people_anchor_strict_id_mismatch_skipped_even_if_name_matches(monkeypatch):
    _people_on(monkeypatch)
    result = people.scrape_people(
        [{"name": "甲", "person_tmdb_id": "11"}],
        fetcher=lambda r: {"name": "甲", "person_tmdb_id": "22",
                           "role": "主演"})
    assert result["filled"] == []
    assert result["skipped"][0]["reason"] == "anchor_mismatch"


def test_people_name_match_acquires_tmdb_id(monkeypatch):
    _people_on(monkeypatch)
    result = people.scrape_people(
        [{"name": "甲", "role": None, "image": None,
          "person_tmdb_id": None}],
        fetcher=lambda r: {"name": "甲", "person_tmdb_id": "33",
                           "role": "主演"})
    assert result["filled"][0]["filled"] == ["role", "person_tmdb_id"]


def test_people_single_failure_does_not_break_batch(monkeypatch):
    _people_on(monkeypatch)

    def _fetcher(record):
        if record["name"] == "坏人":
            raise RuntimeError("源挂了")
        if record["name"] == "空人":
            return None
        return {"name": "好人", "role": "主演"}

    result = people.scrape_people(
        [{"name": "坏人"}, {"name": "空人"}, {"name": "好人"}],
        fetcher=_fetcher)
    assert result["processed"] == 3
    assert len(result["failed"]) == 1
    assert result["failed"][0]["record"]["name"] == "坏人"
    assert result["skipped"][0]["reason"] == "empty_fetch"
    assert len(result["filled"]) == 1


def test_people_whitespace_counts_as_empty_and_object_adapter(monkeypatch):
    _people_on(monkeypatch)
    outcome = people.fill_person(
        {"name": "甲", "role": "   ", "image": None},
        {"name": "甲", "role": "主演"})
    assert outcome["filled"] == ["role"]

    class _Person:
        role = "已有"
        image = None
        person_tmdb_id = ""

    person = _Person()
    changed = people.apply_filled_to_person(
        person, {"role": "新角色", "image": "https://img/y.jpg",
                 "person_tmdb_id": "55"})
    assert changed == ["image", "person_tmdb_id"]
    assert person.role == "已有"
    assert person.image == "https://img/y.jpg"
