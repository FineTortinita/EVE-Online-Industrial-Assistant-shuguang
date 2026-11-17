"""BOM 展开逻辑单元测试 — services/bom_expander.py

测试覆盖:
  - _resolve_name: 物品名称解析
  - _find_blueprint_for_product: 蓝图查找
  - _get_materials: 材料列表获取
  - expand_bom 走统一引擎：共享中间件合并、原料按物品合并（环 / 深度见 tests/test_industry_calc.py）
  - expand_bom: 完整入口
  - 便捷函数: get_material_tree, get_flat_materials
"""

from unittest.mock import MagicMock

import pytest

from services.bom_expander import (
    _find_blueprint_for_product,
    _get_materials,
    _resolve_name,
    expand_bom,
)

pytestmark = pytest.mark.fast


class TestResolveName:
    """物品名称解析"""

    def test_mineral_name(self):
        """矿物 type_id 使用硬编码名称"""
        c = MagicMock()
        name = _resolve_name(c, 34)
        assert name == "三钛合金"
        c.execute.assert_not_called()

    def test_item_table_name(self):
        """从 item 表查询名称"""
        c = MagicMock()
        c.execute.return_value.fetchone.return_value = ("渡鸦级", "Raven")
        name = _resolve_name(c, 2001)
        assert name == "渡鸦级"

    def test_fallback_to_en_name(self):
        c = MagicMock()
        c.execute.return_value.fetchone.return_value = (None, "Raven")
        name = _resolve_name(c, 2001)
        assert name == "Raven"

    def test_fallback_to_type_id(self):
        c = MagicMock()
        c.execute.return_value.fetchone.return_value = (None, None)
        name = _resolve_name(c, 99999)
        assert name == "99999"


class TestFindBlueprintForProduct:
    """查找产出指定物品的蓝图"""

    def test_finds_blueprint(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (3001, 1, 3600)
        result = _find_blueprint_for_product(conn, 2001, "manufacturing")
        assert result == (3001, 1, 3600)

    def test_returns_none_when_not_found(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        result = _find_blueprint_for_product(conn, 99999, "manufacturing")
        assert result is None


class TestGetMaterials:
    """获取蓝图材料列表"""

    def test_returns_materials(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(34, 100), (35, 50)]
        result = _get_materials(conn, 3001, "manufacturing")
        assert len(result) == 2
        assert result[0] == (34, 100)

    def test_returns_empty(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        result = _get_materials(conn, 99999, "manufacturing")
        assert result == []


class TestExpandOnEngine:
    """`expand_bom` 走统一引擎后的口径（真库：conftest 的 temp_db + 一条两层 BOM）。"""

    def test_shared_intermediate_merged_and_raws_summed(self, temp_db, monkeypatch):
        """2001 吃 2003×5 与 1001×1000；2003 吃 1001×100 —— 1001 在两层都出现，原料清单只列一行、数量相加。

        ME10：2003 需 ceil(5×2×0.9)=9；1001 = 1000×2×0.9 + 100×9×0.9 = 1800 + 810。
        """
        with temp_db.connect("bp") as conn:
            conn.execute("DELETE FROM blueprint_activities WHERE blueprint_type_id IN (3001, 3003)")
            conn.execute("DELETE FROM blueprint_products WHERE blueprint_type_id IN (3001, 3003)")
            conn.execute("DELETE FROM blueprint_materials WHERE blueprint_type_id IN (3001, 3003)")
            conn.executemany(
                "INSERT INTO blueprint_activities VALUES (?,?,?)",
                [(3001, "manufacturing", 60), (3003, "manufacturing", 60)],
            )
            conn.executemany(
                "INSERT INTO blueprint_products VALUES (?,?,?,?)",
                [(3001, "manufacturing", 2001, 1), (3003, "manufacturing", 2003, 1)],
            )
            conn.executemany(
                "INSERT INTO blueprint_materials VALUES (?,?,?,?,?)",
                [
                    (3001, "manufacturing", 2003, 5, 10),
                    (3001, "manufacturing", 1001, 1000, 10),
                    (3003, "manufacturing", 1001, 100, 10),
                ],
            )

        class _P:
            def get_price(self, tid, *_a):
                return 2.0

        monkeypatch.setattr("services.bom_expander._default_db", lambda: temp_db)
        monkeypatch.setattr("services.bom_expander._default_pricing", lambda: _P())
        res = expand_bom(2001, quantity=2, bp_me=10)
        assert res["raw_materials"] == [
            {
                "type_id": 1001,
                "name": res["raw_materials"][0]["name"],
                "total_qty": 1800 + 810,
                "unit_price": 2.0,
                "subtotal": 5220.0,
            }
        ]
        assert [(i["type_id"], i["quantity"], i["runs"]) for i in res["intermediates"]] == [(2003, 9, 9)]
        tree = res["tree"]
        assert tree.is_intermediate and {c.type_id for c in tree.children} == {2003, 1001}
        assert res["full_cost"] == 5220.0
