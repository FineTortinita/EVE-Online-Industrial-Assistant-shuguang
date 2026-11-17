"""LP 兑换：从曙光 ESI 拉取 NPC 公司 LP 商店，并按本地价格算每 LP 收益。

参考 T2 商人助手「忠诚点价值」与开明网「LP 兑换」。兑换项来自公开接口
`/loyalty/stores/{corporation_id}/offers/`（无需授权，曙光实测可用）；
价格取本地 market.db 的指定贸易中心（默认首项索巴色基），不另发行情请求。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.constants import TRADE_HUB_IDS, TRADE_HUBS, esi_url
from domain.lp import rank_lp_offers


async def _fetch_offers_async(corporation_id: int) -> list[dict] | None:
    from services.client import APIClient

    async with APIClient(concurrency=1) as client:
        data = await client.fetch_raw(esi_url(f"/loyalty/stores/{int(corporation_id)}/offers/"))
    return [o for o in data if isinstance(o, dict)] if data else None


def fetch_lp_offers(corporation_id: int) -> list[dict]:
    """同步入口（供 QThread 调用）。网络失败/404 返回空列表。"""
    return asyncio.run(_fetch_offers_async(corporation_id)) or []


def list_npc_corporations(db) -> list[dict]:
    """reference.db 的 NPC 公司 → [{id, name}]，按名称排序。

    曙光快照里 `zh_name` 与英文相同（实测 286/286），中文名由 `fetch_corporation_zh_names` 补。
    """
    with db.connect("ref") as conn:
        rows = conn.execute("SELECT corporation_id, zh_name, en_name FROM npc_corporation").fetchall()
    corps = [{"id": int(cid), "name": zh or en or str(cid)} for cid, zh, en in rows]
    return sorted(corps, key=lambda c: c["name"])


async def _fetch_zh_names_async(ids: list[int]) -> dict[int, str]:
    from services.client import APIClient

    names: dict[int, str] = {}
    async with APIClient(concurrency=1) as client:
        for i in range(0, len(ids), 1000):
            data = await client.post(esi_url("/universe/names/", language="zh"), json=ids[i : i + 1000])
            for row in data or []:
                if row.get("category") == "corporation" and row.get("name"):
                    names[int(row["id"])] = str(row["name"])
    return names


def fetch_corporation_zh_names(ids: list[int]) -> dict[int, str]:
    """曙光 ESI 中文公司名（如 1000035 → 加达里海军）；失败返回空 dict。"""
    return asyncio.run(_fetch_zh_names_async([int(i) for i in ids])) if ids else {}


def value_lp_offers(db, offers: list[dict], hub: str | None = None, price_type: str = "sell") -> list[dict[str, Any]]:
    """兑换项 → 按每 LP 收益排序的估值行（含物品名）。"""
    from services.repositories.market_repository import MarketRepository
    from services.ui_data_service import get_item_names_batch

    hub = hub if hub in TRADE_HUB_IDS else TRADE_HUBS[0]
    type_ids = {int(o["type_id"]) for o in offers}
    for o in offers:
        type_ids.update(int(r["type_id"]) for r in o.get("required_items") or [])
    repo = MarketRepository(db)
    region_id = TRADE_HUB_IDS[hub]
    prices = repo.get_prices_by_region(sorted(type_ids), region_id, price_type)
    # 卖单挂单量：盘口太薄（如只挂 5 件）时每 LP 收益不可信，界面要能看到
    depth = repo.get_batch_market_snapshot(sorted(int(o["type_id"]) for o in offers), region_id)
    names = get_item_names_batch(sorted(type_ids), db=db)
    ranked = rank_lp_offers(offers, prices)
    for row in ranked:
        row["name"] = names.get(row["type_id"], str(row["type_id"]))
        row["required_names"] = [f"{names.get(t, t)}×{q:,}" for t, q in row["required_items"]]
        row["sell_volume"] = int((depth.get(row["type_id"]) or {}).get("sv") or 0)
    return ranked


def hub_has_prices(db, hub: str) -> bool:
    """该贸易中心在本地 market.db 是否已有价格（新加入的索巴色基首次使用前可能为空）。"""
    with db.connect("mkt") as conn:
        row = conn.execute(
            "SELECT 1 FROM market_prices WHERE region_id = ? LIMIT 1", (TRADE_HUB_IDS.get(hub, 0),)
        ).fetchone()
    return row is not None
