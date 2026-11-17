"""LP（忠诚点）兑换收益 —— 纯函数，无 DB/Qt。

口径参考 T2 商人助手「忠诚点价值」与开明网「LP 兑换」：
    每 LP 收益 = (产出数量 × 产出单价 − 星币成本 − Σ所需物品数量 × 所需物品单价) / LP 成本

价格缺失时不猜：产出无价 → 整条无法估值；所需物品无价 → 同样无法估值，
否则会把「拿不到价格的材料」当成 0 成本，把收益虚高。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def lp_offer_value(offer: Mapping[str, Any], prices: Mapping[int, float]) -> dict[str, Any]:
    """单条兑换项 → {type_id, quantity, lp_cost, isk_cost, revenue, material_cost, profit, isk_per_lp, missing}。

    `missing` 为缺价物品 type_id 列表；非空时 revenue/profit/isk_per_lp 为 None。
    `lp_cost <= 0` 的兑换项（纯星币兑换）没有「每 LP」口径，`isk_per_lp` 为 None。
    """
    type_id = int(offer["type_id"])
    quantity = int(offer.get("quantity") or 0)
    lp_cost = int(offer.get("lp_cost") or 0)
    isk_cost = float(offer.get("isk_cost") or 0.0)
    required = [(int(r["type_id"]), int(r.get("quantity") or 0)) for r in offer.get("required_items") or []]

    missing = [tid for tid in [type_id, *(t for t, _ in required)] if not prices.get(tid)]
    result: dict[str, Any] = {
        "type_id": type_id,
        "quantity": quantity,
        "lp_cost": lp_cost,
        "isk_cost": isk_cost,
        "required_items": required,
        "missing": missing,
        "revenue": None,
        "material_cost": None,
        "profit": None,
        "isk_per_lp": None,
    }
    if missing:
        return result
    revenue = quantity * float(prices[type_id])
    material_cost = sum(qty * float(prices[tid]) for tid, qty in required)
    profit = revenue - isk_cost - material_cost
    result.update(
        revenue=revenue,
        material_cost=material_cost,
        profit=profit,
        isk_per_lp=profit / lp_cost if lp_cost > 0 else None,
    )
    return result


def rank_lp_offers(offers: Iterable[Mapping[str, Any]], prices: Mapping[int, float]) -> list[dict[str, Any]]:
    """按每 LP 收益降序；无法估值的排在最后（保持原相对顺序）。"""
    valued = [lp_offer_value(o, prices) for o in offers]
    return sorted(valued, key=lambda v: (v["isk_per_lp"] is None, -(v["isk_per_lp"] or 0.0)))
