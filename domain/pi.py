"""行星工业（PI）加工利润 —— 纯函数，无 DB/Qt。

参考 T2 商人助手「行星加工链」与开明网「行星开发」：按配方每个周期的原料与产物，
用市场价算加工利润；海关税按「每单位计税基准 × 税率」计（出口原价、进口半价）。

计税基准与公式来源：EVE University Wiki「Planetary Industry」
（https://wiki.eveuniversity.org/Planetary_Industry）：
    出口费 = 基准 × 税率（指挥中心直接发射 ×1.5）
    进口费 = 基准 × 税率 × 0.5
本模块按「原料从海关进口、产物经海关出口」口径算（买料加工再卖的场景）；
在本星球自产的原料不交进口税，由调用方把 `import_inputs` 设为 False。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 层级 → 每单位计税基准（ISK）
TAX_BASE = {0: 5.0, 1: 400.0, 2: 7_200.0, 3: 60_000.0, 4: 1_200_000.0}

#: SDE `en_group_name` → 层级（P0 为三类行星原料组）
_TIER_BY_GROUP = {
    "Basic Commodities - Tier 1": 1,
    "Refined Commodities - Tier 2": 2,
    "Specialized Commodities - Tier 3": 3,
    "Advanced Commodities - Tier 4": 4,
}


def pi_tier(en_group_name: str | None) -> int | None:
    """物品组名 → 层级 0..4；不是行星产物返回 None。"""
    name = str(en_group_name or "")
    if name in _TIER_BY_GROUP:
        return _TIER_BY_GROUP[name]
    if name.startswith("Planet ") and name.endswith("Raw Resource"):
        return 0
    return None


def schematic_profit(
    schematic: Mapping[str, Any],
    prices: Mapping[int, float],
    tiers: Mapping[int, int | None],
    *,
    input_prices: Mapping[int, float] | None = None,
    tax_rate: float = 0.0,
    import_inputs: bool = True,
) -> dict[str, Any]:
    """单个配方 → 每周期 / 每小时的收入、成本、税费与利润。

    `prices` 是产物卖价，`input_prices` 是原料买入价（缺省同 `prices`）。
    任一原料或产物缺价 → `missing` 非空，金额字段为 None（不按 0 计，避免把利润算虚高）。
    """
    input_prices = prices if input_prices is None else input_prices
    cycle = int(schematic["cycle_time"])
    inputs = [(int(t), int(q)) for t, q in schematic["inputs"]]
    (out_id, out_qty), *_ = [(int(t), int(q)) for t, q in schematic["outputs"]]

    missing = [t for t, _ in inputs if not input_prices.get(t)]
    if not prices.get(out_id):
        missing.append(out_id)
    result: dict[str, Any] = {
        "output_type_id": out_id,
        "output_qty": out_qty,
        "tier": tiers.get(out_id),
        "inputs": inputs,
        "cycle_time": cycle,
        "missing": missing,
        "revenue": None,
        "input_cost": None,
        "tax": None,
        "profit": None,
        "profit_per_hour": None,
        "margin_pct": None,
    }
    if missing:
        return result

    def base(type_id: int) -> float:
        tier = tiers.get(type_id)
        return TAX_BASE.get(tier, 0.0) if tier is not None else 0.0

    revenue = out_qty * float(prices[out_id])
    input_cost = sum(q * float(input_prices[t]) for t, q in inputs)
    export_tax = out_qty * base(out_id) * tax_rate
    import_tax = sum(q * base(t) for t, q in inputs) * tax_rate * 0.5 if import_inputs else 0.0
    tax = export_tax + import_tax
    profit = revenue - input_cost - tax
    result.update(
        revenue=revenue,
        input_cost=input_cost,
        tax=tax,
        profit=profit,
        profit_per_hour=profit * 3600.0 / cycle,
        margin_pct=profit / input_cost * 100.0 if input_cost > 0 else None,
    )
    return result
