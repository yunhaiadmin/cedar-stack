# -*- coding: utf-8 -*-
"""站内用户公开 ID（public_id）回归。

口径（2026-11-15 用户亲点：站内数字 ID 太简单、要复杂一点）：
- ``web_users.public_id``：10 位、大写字母＋数字、剔除易混字符
  （0/O/1/I/L），secrets 生成、只许一处实现（backend.models），
  一旦分配永不变更；
- 注册建号后 public_id 非空；资料接口（/auth/me 出参）带 public_id，
  原数字 id 字段保留不变；
- 管理端用户列表带 public_id，搜索支持按 public_id 精确查
  （用户名/邮箱模糊查与数字 id 原行为不变）；
- 回填脚本只填 NULL 行、幂等、dry-run 不写库。
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_user(db, username, **kw):
    u = models.WebUser(username=username, password_hash="x", **kw)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _assert_format(pid):
    assert isinstance(pid, str)
    assert len(pid) == 10
    assert set(pid) <= set(models.PUBLIC_ID_ALPHABET)
    assert not (set(pid) & set("0O1IL"))


# ---------------- 生成格式与唯一性 ----------------

class TestGenerateFormat:
    def test_format_over_many_draws(self):
        for _ in range(500):
            _assert_format(models.generate_public_id())

    def test_alphabet_excludes_confusables(self):
        for ch in "0O1IL":
            assert ch not in models.PUBLIC_ID_ALPHABET
        # 字母表只许大写字母＋数字
        assert all(c.isupper() or c.isdigit() for c in models.PUBLIC_ID_ALPHABET)


class TestUniqueness:
    def test_batch_unique_via_helper(self, db):
        seen = set()
        for i in range(200):
            pid = models.new_public_id(db)
            assert pid not in seen
            seen.add(pid)
            _make_user(db, f"batch_{i}", public_id=pid)
        assert len(seen) == 200

    def test_orm_insert_default_fills(self, db):
        """不显式给 public_id 的 ORM 建号也必须非空（列默认兜底）。"""
        u = _make_user(db, "default_fill")
        _assert_format(u.public_id)

    def test_collision_retries_then_succeeds(self, db, monkeypatch):
        taken = _make_user(db, "taken_user").public_id
        fresh = "ZZZZZZZZZZ" if taken != "ZZZZZZZZZZ" else "YYYYYYYYYY"
        draws = iter([taken, taken, fresh])
        monkeypatch.setattr(models, "generate_public_id", lambda: next(draws))
        assert models.new_public_id(db) == fresh

    def test_collision_exhaustion_raises(self, db, monkeypatch):
        taken = _make_user(db, "taken_user2").public_id
        monkeypatch.setattr(models, "generate_public_id", lambda: taken)
        with pytest.raises(RuntimeError):
            models.new_public_id(db)


# ---------------- 注册建号与资料接口 ----------------

def _make_request(direct_ip):
    req = MagicMock()
    req.client.host = direct_ip
    req.headers.get = lambda k, default="": default
    return req


class TestRegisterAndProfile:
    def test_register_assigns_public_id(self, db):
        from backend.api.emby_portal import RegisterRequest, register

        resp = register(
            _make_request("198.51.100.77"),
            RegisterRequest(username="pubid_reg", password="secret1"),
            db,
        )
        _assert_format(resp.user.public_id)
        row = db.query(models.WebUser).filter_by(username="pubid_reg").one()
        assert row.public_id == resp.user.public_id
        # 数字 id 原样保留
        assert resp.user.id == row.id

    def test_me_payload_includes_public_id(self, db):
        from backend.api.emby_portal import me

        u = _make_user(db, "pubid_me")
        out = me(current_user=u, db=db)
        assert out.public_id == u.public_id
        _assert_format(out.public_id)
        assert out.id == u.id


# ---------------- 管理端用户列表 ----------------

class TestAdminList:
    def test_list_includes_public_id(self, db):
        from backend.api.admin import list_users

        u = _make_user(db, "pubid_admin_list")
        res = list_users(db=db, current_admin=None)
        item = next(i for i in res["users"] if i["id"] == u.id)
        assert item["public_id"] == u.public_id
        assert item["id"] == u.id

    def test_search_by_public_id_exact(self, db):
        from backend.api.admin import list_users

        target = _make_user(db, "pubid_search_target")
        _make_user(db, "pubid_search_other")
        res = list_users(search=target.public_id, db=db, current_admin=None)
        assert [i["id"] for i in res["users"]] == [target.id]

    def test_search_by_public_id_case_insensitive(self, db):
        from backend.api.admin import list_users

        target = _make_user(db, "pubid_search_case")
        res = list_users(search=target.public_id.lower(), db=db,
                         current_admin=None)
        assert [i["id"] for i in res["users"]] == [target.id]

    def test_search_partial_public_id_no_exact_hit(self, db):
        from backend.api.admin import list_users

        target = _make_user(db, "pubid_search_partial")
        res = list_users(search=target.public_id[:6], db=db,
                         current_admin=None)
        assert all(i["id"] != target.id for i in res["users"])


# ---------------- 回填脚本 ----------------

class TestBackfill:
    def _legacy_user(self, db, username):
        """存量行：先正常建号再把 public_id 置 NULL，模拟升级前老行
        （ORM 显式传 None 会被列默认兜底填上，造不出 NULL 行）。"""
        from sqlalchemy import text

        u = _make_user(db, username)
        db.execute(text("UPDATE web_users SET public_id = NULL "
                        "WHERE id = :id"), {"id": u.id})
        db.commit()
        db.refresh(u)
        assert u.public_id is None
        return u

    def test_dry_run_counts_only_and_writes_nothing(self, db):
        from scripts.backfill_user_public_ids import backfill_user_public_ids

        self._legacy_user(db, "legacy_a")
        self._legacy_user(db, "legacy_b")
        _make_user(db, "already_has")
        stats = backfill_user_public_ids(db, dry_run=True)
        assert stats["pending"] == 2
        assert stats["filled"] == 0
        rows = db.query(models.WebUser).filter(
            models.WebUser.username.like("legacy_%")).all()
        assert all(r.public_id is None for r in rows)

    def test_commit_fills_only_null_rows_uniquely(self, db):
        from scripts.backfill_user_public_ids import backfill_user_public_ids

        a = self._legacy_user(db, "legacy_c")
        b = self._legacy_user(db, "legacy_d")
        keep = _make_user(db, "already_has2")
        keep_pid = keep.public_id
        stats = backfill_user_public_ids(db, dry_run=False)
        assert stats["pending"] == 2
        assert stats["filled"] == 2
        db.refresh(a)
        db.refresh(b)
        db.refresh(keep)
        _assert_format(a.public_id)
        _assert_format(b.public_id)
        assert a.public_id != b.public_id
        assert keep.public_id == keep_pid  # 已有值永不覆盖

    def test_commit_idempotent(self, db):
        from scripts.backfill_user_public_ids import backfill_user_public_ids

        self._legacy_user(db, "legacy_e")
        first = backfill_user_public_ids(db, dry_run=False)
        assert first["filled"] == 1
        second = backfill_user_public_ids(db, dry_run=False)
        assert second["pending"] == 0
        assert second["filled"] == 0
