"""RegistrationCode.__table_args__ 合并声明钉住测试。

存量问题：该类曾写了两遍 __table_args__，后者覆盖前者，导致
idx_reg_code / idx_reg_code_type 两个索引声明实际从未生效
（生效的只有第二遍的 idx_reg_code_agent 与 code 列 index=True
自带的 ix_registration_codes_code）。

本测试只钉代码声明，不要求存量库真有这些索引：生产补建与否
另行定夺（见 PR 说明）。
"""
from backend.models import RegistrationCode


def _declared_index_names():
    args = RegistrationCode.__table_args__
    # __table_args__ 须是单一 tuple（类里只许一份声明）
    assert isinstance(args, tuple)
    return {getattr(a, "name", None) for a in args}


def test_registration_code_table_args_contains_all_expected_indexes():
    names = _declared_index_names()
    assert "idx_reg_code" in names
    assert "idx_reg_code_type" in names
    assert "idx_reg_code_agent" in names


def test_registration_code_table_indexes_match_declaration():
    table_indexes = {i.name for i in RegistrationCode.__table__.indexes}
    # 声明的三条都应进表索引集合；code 列 index=True 的唯一索引也在
    assert {"idx_reg_code", "idx_reg_code_type", "idx_reg_code_agent"} <= table_indexes
    assert "ix_registration_codes_code" in table_indexes
