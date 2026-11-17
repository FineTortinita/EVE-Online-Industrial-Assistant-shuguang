"""行星工业加工利润：读曙光配方表 + 本地价格 → 每个配方的利润排行。

配方来自曙光客户端 `res:/staticdata/schematics.fsdbinary`，由
`python -m scripts.infinity_fsd schematics <client_root> data/pi_schematics.json` 导出并随发行包分发
（build 3684550：68 条，cycle_time 与曙光 ESI `/universe/schematics/{id}/` 全部一致）。
价格读本地 market.db，不另发行情请求。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.constants import TRADE_HUB_IDS, TRADE_HUBS
from core.paths import data_dir
from domain.pi import pi_tier, schematic_profit

SCHEMATICS_FILE = "pi_schematics.json"


@lru_cache(maxsize=1)
def load_schematics() -> dict[int, dict]:
    """配方表；文件缺失返回空 dict（界面据此提示），不猜数据。"""
    path = Path(data_dir()) / SCHEMATICS_FILE
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in (payload.get("schematics") or {}).items()}


def rank_schematics(
    db,
    hub: str | None = None,
    *,
    sell_type: str = "sell",
    buy_type: str = "sell",
    tax_rate: float = 0.0,
    import_inputs: bool = True,
) -> list[dict[str, Any]]:
    """全部配方按每小时利润降序（缺价排最后）。

    默认口径：原料按卖单价买入（直接吃卖单）、产物按卖单价卖出（挂卖单）。
    """
    from services.repositories.market_repository import MarketRepository

    schematics = load_schematics()
    if not schematics:
        return []
    hub = hub if hub in TRADE_HUB_IDS else TRADE_HUBS[0]
    type_ids = sorted(
        {int(t) for s in schematics.values() for t, _ in s["inputs"]}
        | {int(t) for s in schematics.values() for t, _ in s["outputs"]}
    )
    with db.connect("ref") as conn:
        rows = conn.execute(
            f"SELECT type_id, zh_name, en_name, en_group_name FROM item WHERE type_id IN ({','.join('?' * len(type_ids))})",
            type_ids,
        ).fetchall()
    names = {int(t): zh or en or str(t) for t, zh, en, _g in rows}
    tiers = {int(t): pi_tier(g) for t, _zh, _en, g in rows}

    repo = MarketRepository(db)
    region_id = TRADE_HUB_IDS[hub]
    sell_prices = repo.get_prices_by_region(type_ids, region_id, sell_type)
    buy_prices = sell_prices if buy_type == sell_type else repo.get_prices_by_region(type_ids, region_id, buy_type)

    ranked = []
    for sid, schematic in schematics.items():
        v = schematic_profit(
            schematic, sell_prices, tiers, input_prices=buy_prices, tax_rate=tax_rate, import_inputs=import_inputs
        )
        v["schematic_id"] = sid
        v["name"] = names.get(v["output_type_id"], str(v["output_type_id"]))
        v["input_names"] = [f"{names.get(t, t)}×{q:,}" for t, q in v["inputs"]]
        ranked.append(v)
    ranked.sort(key=lambda v: (v["profit_per_hour"] is None, -(v["profit_per_hour"] or 0.0)))
    return ranked
