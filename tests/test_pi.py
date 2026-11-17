"""行星加工利润纯函数（domain/pi.py）。"""

import pytest

from domain.pi import pi_tier, schematic_profit

pytestmark = pytest.mark.fast

# 曙光客户端 schematics.fsdbinary 实测：65 超导体 = 40 等离子体团(P1 2389) + 40 水(P1 3645) → 5，3600 秒
_SUPERCONDUCTOR = {"cycle_time": 3600, "inputs": [[2389, 40], [3645, 40]], "outputs": [[9838, 5]]}
_TIERS = {2389: 1, 3645: 1, 9838: 2}


@pytest.mark.parametrize(
    "group, tier",
    [
        ("Planet Solid - Raw Resource", 0),
        ("Basic Commodities - Tier 1", 1),
        ("Advanced Commodities - Tier 4", 4),
        ("Mineral", None),
        (None, None),
    ],
)
def test_pi_tier(group, tier):
    assert pi_tier(group) == tier


def test_profit_with_customs_tax():
    """收入 5×60,000；原料 80×1,000；出口税 5×7,200×10%；进口税 80×400×10%×0.5。"""
    v = schematic_profit(_SUPERCONDUCTOR, {2389: 1000.0, 3645: 1000.0, 9838: 60_000.0}, _TIERS, tax_rate=0.10)
    assert v["revenue"] == 300_000
    assert v["input_cost"] == 80_000
    assert v["tax"] == pytest.approx(3_600 + 1_600)
    assert v["profit"] == pytest.approx(300_000 - 80_000 - 5_200)
    assert v["profit_per_hour"] == pytest.approx(v["profit"])
    no_import = schematic_profit(
        _SUPERCONDUCTOR, {2389: 1000.0, 3645: 1000.0, 9838: 60_000.0}, _TIERS, tax_rate=0.10, import_inputs=False
    )
    assert no_import["tax"] == pytest.approx(3_600)


def test_missing_price_is_not_zero():
    v = schematic_profit(_SUPERCONDUCTOR, {2389: 1000.0, 9838: 60_000.0}, _TIERS)
    assert v["missing"] == [3645]
    assert v["profit"] is None and v["profit_per_hour"] is None


def test_half_hour_cycle_doubles_hourly_profit():
    p1 = {"cycle_time": 1800, "inputs": [[2268, 3000]], "outputs": [[3645, 20]]}
    v = schematic_profit(p1, {2268: 1.0, 3645: 500.0}, {2268: 0, 3645: 1})
    assert v["profit"] == pytest.approx(20 * 500 - 3000)
    assert v["profit_per_hour"] == pytest.approx(2 * v["profit"])
