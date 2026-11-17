"""行星加工利润：真实配方文件 + 打桩价格 → 表格行与筛选。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui


def test_rank_uses_bundled_schematics_and_filters_tier(qapp, monkeypatch):
    """`data/pi_schematics.json` 是发行包里的真实文件：68 条、每条一个产物；
    筛 P2 后只剩 P2 行，且每小时利润按 cycle_time 折算。"""
    import services.lp_service as lps
    import services.pi_service as pis
    from ui_qml.bridge.pi_bridge import PiBridge

    schematics = pis.load_schematics()
    assert len(schematics) == 68
    assert all(len(s["outputs"]) == 1 for s in schematics.values())

    monkeypatch.setattr(lps, "hub_has_prices", lambda db, hub: True)
    captured = {}

    def _rank(db, hub=None, **kw):
        captured.update(hub=hub, **kw)
        return [
            {
                "name": "超导体",
                "tier": 2,
                "cycle_time": 3600,
                "input_names": ["等离子体团×40"],
                "output_qty": 5,
                "revenue": 300_000.0,
                "input_cost": 80_000.0,
                "tax": 5_200.0,
                "profit": 214_800.0,
                "profit_per_hour": 214_800.0,
                "margin_pct": 268.5,
                "missing": [],
            },
            {
                "name": "水",
                "tier": 1,
                "cycle_time": 1800,
                "input_names": ["水基液体×3,000"],
                "output_qty": 20,
                "revenue": None,
                "input_cost": None,
                "tax": None,
                "profit": None,
                "profit_per_hour": None,
                "margin_pct": None,
                "missing": [2268],
            },
        ]

    monkeypatch.setattr(pis, "rank_schematics", _rank)
    bridge = PiBridge(db=object())
    bridge.reload()
    assert captured == {"hub": "Sobaseki", "tax_rate": 0.10, "import_inputs": True}
    assert [r["cells"][-2]["text"] for r in bridge.rows] == ["214.8K", "缺价"]

    bridge.setTierIndex(2)  # P2 精炼
    assert [r["cells"][0]["text"] for r in bridge.rows] == ["超导体"]
    bridge.setTaxPct(0.0)
    bridge.setImportInputs(False)
    assert captured["tax_rate"] == 0.0 and captured["import_inputs"] is False
