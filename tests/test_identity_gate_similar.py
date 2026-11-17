"""Similar 推荐面与案 A 货架口径对齐（审计 P3 后端缺口）。

详情页「更多推荐」与 Emby 客户端的 Similar 都是系统主动枚举的推荐面，
属货架面：必须与 Latest / getItems 浏览同口径——
- 身份门开时只露已绑定条目，且条件进 SQL、不许先占 Limit 名额；
- 物理合并副本（merged_into_id 非空）永不出现；
- 同片多行只留代表（dedup 代表层，含每库 merge_same 开关）；
- 候选行按用户可见库范围收（_library_scope/_scope_items）。
门关时身份维度回到旧口径（未绑定照常出现）；入参条目本身走
_require_visible_item（不存在 404 / 库不可见 403），本单不动。
搜索面不收紧、直达放行两条定版与本端点无关，不许波及。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models  # noqa: F401  注册全部表
from backend.database import Base
from backend.emby_server import api, compat_routes, identity_gate
from backend.emby_server import models as em


@pytest.fixture()
def scene():
    # 内存库：本测试只验查询口径，不依赖落盘（StaticPool 保证同一连接）
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    lib_a = em.Library(guid=uuid.uuid4().hex, name="库A",
                       collection_type="movies", is_enabled=True)
    lib_b = em.Library(guid=uuid.uuid4().hex, name="库B",
                       collection_type="movies", is_enabled=True)
    db.add_all([lib_a, lib_b])
    db.flush()
    now = datetime(2026, 11, 17, 12, 0, 0)

    def mk(lib, name, rating, **kw):
        kw.setdefault("genres", "动作")
        it = em.MediaItem(
            guid=uuid.uuid4().hex, library_id=lib.id, item_type="movie",
            name=name, sort_name=name,
            community_rating=rating,
            file_path=f"/m/{uuid.uuid4().hex}.mkv",
            date_added=now, **kw)
        db.add(it)
        db.flush()
        return it

    m = {"lib_a": lib_a, "lib_b": lib_b}
    m["src"] = mk(lib_a, "源片", 7.0, tmdb_id="100")
    # 未绑定候选评分最高：门开时不许出现、更不许占 Limit 名额
    m["unbound"] = mk(lib_a, "未绑定候选", 9.9)
    m["bound"] = mk(lib_a, "已绑定候选", 8.0, tmdb_id="101")
    # 物理合并副本：评分第二高，永不许出现；正主保留
    m["merged_primary"] = mk(lib_a, "正主", 6.0, tmdb_id="102")
    m["merged_copy"] = mk(lib_a, "合并副本", 9.5, tmdb_id="102",
                          merged_into_id=m["merged_primary"].id)
    # 同片两行（同库同 tmdb、均未物理合并）：只留代表（有海报者胜，
    # 与 dedup._primary_score 同口径）；另一份评分更高也不能整份留下
    m["dup_primary"] = mk(lib_a, "同片代表", 7.5, tmdb_id="103",
                          poster_path="/p/poster.jpg")
    m["dup_shadow"] = mk(lib_a, "同片另一份", 7.8, tmdb_id="103")
    # 他库候选：可见范围外不许出现
    m["other_lib"] = mk(lib_b, "他库候选", 7.0, tmdb_id="104")
    # 异流派：任何口径下都不该命中
    m["other_genre"] = mk(lib_a, "异流派片", 10.0, tmdb_id="105",
                          genres="剧情")
    user = models.WebUser(username="sim", password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    data = SimpleNamespace(db=db, m=m, user=user)
    yield data
    db.close()


@pytest.fixture()
def gate_on(monkeypatch):
    monkeypatch.setattr(identity_gate, "IDENTITY_SHELF_GATE", 1)


def _similar(scene, src=None, limit="12", scope=None):
    src = src or scene.m["src"]
    req = SimpleNamespace(query_params={"Limit": limit})
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(compat_routes, "_base_url", lambda request: "http://test")
        mp.setattr(
            compat_routes, "_query_result",
            lambda rows, user, db, base: {
                "Items": [{"Id": r.guid, "Name": r.name} for r in rows],
                "TotalRecordCount": len(rows)})
        if scope is not None:
            # 可见范围：端点自身的候选查询与入参校验（api._require_visible_item
            # 内部走 api._library_scope）必须同口径
            mp.setattr(compat_routes, "_library_scope",
                       lambda db, user: scope, raising=False)
            mp.setattr(api, "_library_scope", lambda db, user: scope)
        return compat_routes.similar_items(src.guid, req, scene.user, scene.db)


def _names(res):
    return [i["Name"] for i in res["Items"]]


def test_similar_gate_off_keeps_unbound(scene):
    """门关 = 身份维度旧口径：未绑定候选照常出现。"""
    names = _names(_similar(scene))
    assert "未绑定候选" in names
    assert "已绑定候选" in names
    assert "异流派片" not in names


def test_similar_gate_on_hides_unbound(scene, gate_on):
    names = _names(_similar(scene))
    assert "未绑定候选" not in names
    assert "已绑定候选" in names


def test_similar_gate_on_limit_not_consumed_by_unbound(scene, gate_on):
    """名额钉：未绑定评分最高，Limit=1 仍须拿到已绑定候选
    （身份条件进 SQL 先过滤、再排序取 Limit，不许后置剔除）。"""
    names = _names(_similar(scene, limit="1"))
    assert names == ["已绑定候选"]


def test_similar_never_returns_merged_copy(scene):
    """物理合并副本永不出现（门关口径，与 Latest 一致）；正主保留。"""
    names = _names(_similar(scene))
    assert "合并副本" not in names
    assert "正主" in names


def test_similar_never_returns_merged_copy_gate_on(scene, gate_on):
    names = _names(_similar(scene))
    assert "合并副本" not in names
    assert "正主" in names


def test_similar_dedup_keeps_representative_only(scene):
    """同片两行只留代表：有海报的「同片代表」在，评分更高的另一份不在。"""
    names = _names(_similar(scene))
    assert "同片代表" in names
    assert "同片另一份" not in names


def test_similar_respects_library_scope(scene):
    """候选按可见库范围收：范围外库的候选不出现；不限范围时照常出现。"""
    names_all = _names(_similar(scene))
    assert "他库候选" in names_all
    names_scoped = _names(_similar(scene, scope={scene.m["lib_a"].id}))
    assert "他库候选" not in names_scoped
    assert "已绑定候选" in names_scoped


def test_similar_source_item_invisible_still_403(scene):
    """入参条目本身的现行行为保持：所在库不可见 → 403（不因本单改变）。"""
    with pytest.raises(HTTPException) as exc:
        _similar(scene, src=scene.m["other_lib"],
                 scope={scene.m["lib_a"].id})
    assert exc.value.status_code == 403
