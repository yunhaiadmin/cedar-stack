"""求片减负（2026-11-10 用户亲点全套）：到货盯梢工人 + 列表在库标记
+ 批量批准/批准全部待审 + 自动批准开关。

覆盖：
1. **盯梢工人**：处理中求片 tmdb 对上库内 → 后台一轮自动翻 completed，
   到货私聊只入队一次（连跑两轮不重发）；对不上不翻；补缺集行
   （request_kind='missing_episodes'）绝不走这条联动；无 tmdb 行不翻；
   单行异常不拖垮整轮其余行。
2. **共享口径**：读时联动 _sync_row 与工人共用同一条判定
   （media_seek.sync_seek_library_row），不许两套口径漂移。
3. **列表在库标记**：管理端列表给处理中行带 in_library——有 tmdb 按
   tmdb 判定、无 tmdb 按片名判定（与「标记已入库」同一口径）；不在库
   为 False；已 completed 的行不带标记（不重复给一键按钮）。
4. **批量批准**：逐条走既有守卫——可批的批、终态/不存在的逐条回报
   失败原因，其余不受影响；「批准全部待审」只动 pending。
5. **自动批准开关**：默认关（落单 pending、与现行逐字一致）；开启后
   新建单落单即 approved；额度/去重判定不因此变化。键在
   config_schema 与运营设置注册表里都登记得上、存得进。

全部隔离内存 SQLite，不碰生产库、不发真通知（入队行即断言对象）。
"""

import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
# TG_PROACTIVE_FLUSHER 不许写在导入期（#1888/#1891 同形态守卫
# test_zz_env_import_hygiene）：刷盘器改用例期关，见下面的 autouse fixture。

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import config_schema, media_seek, models
from backend.api import admin as admin_api
from backend.api import admin_economy
from backend.api import user as user_api
from backend.api.store_requests import _sync_row
from backend.emby_server import models as em
from backend.emby_server import seek_fulfill_worker


@pytest.fixture(autouse=True)
def _no_proactive_flusher(monkeypatch):
    """用例期关掉主动通知刷盘器：入队后提交钩子会唤醒发送线程，
    本文件只断言入队行、不发真通知。_flusher_enabled() 是每次调用
    读环境变量（非导入期快照），用例期 monkeypatch 即生效。"""
    monkeypatch.setenv("TG_PROACTIVE_FLUSHER", "0")
    yield


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine)


# ==================== 工具 ====================

def _user(db, username="seeker", telegram_id=None, staff=False):
    user = models.WebUser(
        username=username, password_hash="x", is_active=True,
        is_staff=staff, telegram_id=telegram_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _cfg(db, key, value):
    db.add(models.SystemConfig(key=key, value=value, description=""))
    db.commit()


def _request(db, user, *, name="想看的片", status="pending", tmdb_id=None,
             kind="seek", mtype="movie"):
    row = models.MovieRequest(
        user_id=user.id, movie_name=name, type=mtype, status=status,
        tmdb_id=tmdb_id, request_kind=kind,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _media_item(db, *, name="想看的片", tmdb_id=None, item_type="movie"):
    lib = em.Library(guid=f"lib-{name}-{tmdb_id}", name="测试库",
                     collection_type="movies", paths="")
    db.add(lib)
    db.flush()
    item = em.MediaItem(
        guid=f"item-{name}-{tmdb_id}", library_id=lib.id,
        item_type=item_type, name=name, tmdb_id=tmdb_id,
        file_path=f"/media/{name}.mkv",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _arrival_notices(db, request_id=None):
    rows = db.query(models.TgProactiveNotice).filter(
        models.TgProactiveNotice.kind == "store_arrival")
    if request_id is not None:
        rows = rows.filter(
            models.TgProactiveNotice.subject_id == request_id)
    return rows.all()


# ==================== 1. 盯梢工人 ====================

def test_worker_flips_matched_and_notifies_once(engine, factory):
    db = sessionmaker(bind=engine)()
    owner = _user(db, "owner", telegram_id=1001)
    row = _request(db, owner, tmdb_id="t100", name="到货片")
    _media_item(db, name="到货片", tmdb_id="t100")
    row_id, owner_id = row.id, owner.id
    db.close()

    stats1 = seek_fulfill_worker.fulfill_once(factory)
    assert stats1["fulfilled"] == 1

    db = sessionmaker(bind=engine)()
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == row_id).first()
    assert row.status == "completed"
    notices = _arrival_notices(db, row_id)
    assert len(notices) == 1 and notices[0].user_id == owner_id
    db.close()

    # 第二轮：已 completed 不再处理，通知不重发
    stats2 = seek_fulfill_worker.fulfill_once(factory)
    assert stats2["fulfilled"] == 0
    db = sessionmaker(bind=engine)()
    assert len(_arrival_notices(db, row_id)) == 1
    db.close()


def test_worker_leaves_unmatched_pending(engine, factory):
    db = sessionmaker(bind=engine)()
    owner = _user(db, "owner2", telegram_id=1002)
    row = _request(db, owner, tmdb_id="t404", name="没到货")
    row_id = row.id
    db.close()

    stats = seek_fulfill_worker.fulfill_once(factory)
    assert stats["fulfilled"] == 0
    db = sessionmaker(bind=engine)()
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == row_id).first()
    assert row.status == "pending"
    assert _arrival_notices(db, row_id) == []
    db.close()


def test_worker_never_flips_missing_episodes_rows(engine, factory):
    # 补缺集行：剧本来就在库、tmdb 必然对得上，翻完成等于假完成；
    # 它的完成只认缺集核算工人。工人与读时联动都不许碰。
    db = sessionmaker(bind=engine)()
    owner = _user(db, "owner3", telegram_id=1003)
    row = _request(db, owner, tmdb_id="t200", name="在库剧",
                   kind="missing_episodes", mtype="series")
    _media_item(db, name="在库剧", tmdb_id="t200", item_type="series")
    row_id = row.id
    db.close()

    stats = seek_fulfill_worker.fulfill_once(factory)
    assert stats["fulfilled"] == 0
    db = sessionmaker(bind=engine)()
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == row_id).first()
    assert row.status == "pending"
    assert _sync_row(db, row) is False
    db.close()


def test_worker_skips_rows_without_tmdb(engine, factory):
    db = sessionmaker(bind=engine)()
    owner = _user(db, "owner4", telegram_id=1004)
    row = _request(db, owner, tmdb_id=None, name="只有片名")
    _media_item(db, name="只有片名", tmdb_id="t999")
    row_id = row.id
    db.close()

    stats = seek_fulfill_worker.fulfill_once(factory)
    assert stats["fulfilled"] == 0
    db = sessionmaker(bind=engine)()
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == row_id).first()
    assert row.status == "pending"
    db.close()


def test_worker_isolates_row_errors(engine, factory, monkeypatch):
    db = sessionmaker(bind=engine)()
    owner = _user(db, "owner5", telegram_id=1005)
    bad = _request(db, owner, tmdb_id="boom", name="坏行")
    good = _request(db, owner, tmdb_id="t300", name="好行")
    bad_id, good_id = bad.id, good.id
    _media_item(db, name="好行", tmdb_id="t300")
    db.close()

    import backend.api.store as store_api

    real_find = store_api.find_library_item

    def flaky(db_, tmdb_id):
        if tmdb_id == "boom":
            raise RuntimeError("模拟单行判定爆炸")
        return real_find(db_, tmdb_id)

    monkeypatch.setattr(store_api, "find_library_item", flaky)

    stats = seek_fulfill_worker.fulfill_once(factory)
    assert stats["fulfilled"] == 1
    assert stats["failed_rows"] >= 1

    db = sessionmaker(bind=engine)()
    good = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == good_id).first()
    bad = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == bad_id).first()
    assert good.status == "completed"
    assert bad.status == "pending"
    db.close()


def test_shared_sync_function_is_the_read_path(db):
    # 读时联动与工人共用 media_seek.sync_seek_library_row 同一条判定：
    # 直接验共享函数本身翻/不翻，再验 _sync_row 仍旧同口径。
    owner = _user(db, "owner6", telegram_id=1006)
    hit = _request(db, owner, tmdb_id="t400", name="共享片")
    miss = _request(db, owner, tmdb_id="t401", name="共享没到")
    _media_item(db, name="共享片", tmdb_id="t400")

    assert media_seek.sync_seek_library_row(db, hit) is True
    assert media_seek.sync_seek_library_row(db, miss) is False
    db.commit()
    db.refresh(hit)
    assert hit.status == "completed"
    # _sync_row 包装不漂移：已翻的行再喂回去是 False
    assert _sync_row(db, hit) is False


def test_fulfill_interval_config(db):
    # 缺行回默认 5 分钟；脏值回默认；越界钳制
    assert media_seek.fulfill_interval_minutes(db) == 5
    _cfg(db, media_seek.CONFIG_FULFILL_INTERVAL, "10")
    assert media_seek.fulfill_interval_minutes(db) == 10
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == media_seek.CONFIG_FULFILL_INTERVAL).first()
    row.value = "abc"
    db.commit()
    assert media_seek.fulfill_interval_minutes(db) == 5
    row.value = "0"
    db.commit()
    assert media_seek.fulfill_interval_minutes(db) == 1
    row.value = "99999"
    db.commit()
    assert media_seek.fulfill_interval_minutes(db) == 1440


# ==================== 2. 列表在库标记 ====================

def _list_rows(db, admin):
    return admin_api.get_media_seeks(None, 0, None, admin, db)


def test_admin_list_marks_in_library(db):
    admin = _user(db, "admin", staff=True)
    owner = _user(db, "owner7")
    by_tmdb = _request(db, owner, tmdb_id="t500", name="代号片")
    by_name = _request(db, owner, tmdb_id=None, name="片名片")
    not_in = _request(db, owner, tmdb_id="t501", name="不在库")
    done = _request(db, owner, tmdb_id="t500", name="已完成行",
                    status="completed")
    _media_item(db, name="完全不同的名字", tmdb_id="t500")
    _media_item(db, name="片名片", tmdb_id="t777")

    rows = {r["id"]: r for r in _list_rows(db, admin)}
    assert rows[by_tmdb.id]["in_library"] is True
    assert rows[by_name.id]["in_library"] is True
    assert rows[not_in.id]["in_library"] is False
    # 已 completed：不再标记（前端不重复给一键「标记已入库」）
    assert rows[done.id]["in_library"] is False


# ==================== 3. 批量批准 ====================

def test_batch_approve_partial_failures(db):
    admin = _user(db, "admin2", staff=True)
    owner = _user(db, "owner8")
    ok1 = _request(db, owner, name="可批一")
    ok2 = _request(db, owner, name="可批二", status="approved")
    done = _request(db, owner, name="已入库行", status="completed")
    rejected = _request(db, owner, name="已拒绝行", status="rejected")

    result = admin_api._batch_approve_media_seeks_sync(
        db, admin.id, [ok1.id, done.id, 999999, ok2.id, rejected.id, ok1.id])

    approved_ids = {entry["id"] for entry in result["approved"]}
    assert approved_ids == {ok1.id, ok2.id}
    failed = {entry["id"]: entry["reason"] for entry in result["failed"]}
    assert set(failed) == {done.id, 999999, rejected.id}
    assert "已入库" in failed[done.id]
    assert "不存在" in failed[999999] or "404" in failed[999999]

    db.refresh(ok1)
    db.refresh(done)
    assert ok1.status == "approved"
    assert done.status == "completed", "终态行不许被批量批准翻动"


def test_approve_all_pending_only_touches_pending(db):
    admin = _user(db, "admin3", staff=True)
    owner = _user(db, "owner9")
    p1 = _request(db, owner, name="待审一")
    p2 = _request(db, owner, name="待审二")
    a1 = _request(db, owner, name="已批行", status="approved")
    c1 = _request(db, owner, name="已入库行", status="completed")

    result = admin_api._approve_all_pending_media_seeks_sync(db, admin.id, 0)
    assert {e["id"] for e in result["approved"]} == {p1.id, p2.id}
    assert result["failed"] == []
    for row in (p1, p2, a1, c1):
        db.refresh(row)
    assert p1.status == "approved" and p2.status == "approved"
    assert a1.status == "approved" and c1.status == "completed"


# ==================== 4. 自动批准开关 ====================

def test_auto_approve_defaults_off(db):
    assert media_seek.auto_approve_enabled(db) is False
    _cfg(db, media_seek.CONFIG_AUTO_APPROVE, "true")
    assert media_seek.auto_approve_enabled(db) is True
    row = db.query(models.SystemConfig).filter(
        models.SystemConfig.key == media_seek.CONFIG_AUTO_APPROVE).first()
    row.value = "false"
    db.commit()
    assert media_seek.auto_approve_enabled(db) is False


def _payload(**overrides):
    data = {"movie_name": "自动批准片"}
    data.update(overrides)
    return user_api.MediaSeekRequest(**data)


def test_create_pending_when_auto_approve_off(db):
    user = _user(db, "creator1")
    rid = user_api._create_media_seek_sync(
        db, user, "自动批准片", _payload(tmdb_id="880001", type="movie"))
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == rid).first()
    assert row.status == "pending"


def test_create_approved_when_auto_approve_on(db):
    user = _user(db, "creator2")
    _cfg(db, media_seek.CONFIG_AUTO_APPROVE, "true")
    rid = user_api._create_media_seek_sync(
        db, user, "自动批准片", _payload(tmdb_id="880002", type="movie"))
    row = db.query(models.MovieRequest).filter(
        models.MovieRequest.id == rid).first()
    assert row.status == "approved"
    # 自动批准不改额度/去重口径：同一片再提交仍撞「已在处理中」
    with pytest.raises(Exception) as exc:
        user_api._create_media_seek_sync(
            db, user, "自动批准片", _payload(tmdb_id="880002", type="movie"))
    assert getattr(exc.value, "status_code", None) == 409


def test_new_keys_registered_in_config_schema():
    assert config_schema.ECONOMY_CONFIG_SCHEMA[
        "media_seek_auto_approve_enabled"] == ("bool",)
    assert config_schema.ECONOMY_CONFIG_SCHEMA[
        "media_seek_fulfill_interval_minutes"] == ("int", 1, 1440)
    assert admin_economy.ECONOMY_CONFIG_KEYS[
        "media_seek_auto_approve_enabled"] == "bool"
    assert admin_economy.ECONOMY_CONFIG_KEYS[
        "media_seek_fulfill_interval_minutes"] == "int"
    # schema 归一：越界值必须被拒（运营设置保存路径靠它把关）
    spec = config_schema.ECONOMY_CONFIG_SCHEMA[
        "media_seek_fulfill_interval_minutes"]
    assert config_schema.normalize(
        "media_seek_fulfill_interval_minutes", spec, " 5 ") == "5"
    with pytest.raises(config_schema.ConfigValueError):
        config_schema.normalize(
            "media_seek_fulfill_interval_minutes", spec, "0")
