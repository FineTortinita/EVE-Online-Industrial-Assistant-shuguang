"""LP 兑换收益纯函数（domain/lp.py）。"""

import pytest

from domain.lp import lp_offer_value, rank_lp_offers

pytestmark = pytest.mark.fast

# 曙光 ESI 实测加达里海军兑换项的形状（2026-11-17）
_OFFER = {
    "offer_id": 3587,
    "type_id": 27339,
    "quantity": 5000,
    "lp_cost": 5500,
    "isk_cost": 5_500_000,
    "ak_cost": 0,
    "required_items": [{"type_id": 2506, "quantity": 5000}],
}


def test_isk_per_lp_subtracts_isk_and_required_items():
    v = lp_offer_value(_OFFER, {27339: 3000.0, 2506: 1000.0})
    assert v["revenue"] == 15_000_000
    assert v["material_cost"] == 5_000_000
    assert v["profit"] == 15_000_000 - 5_500_000 - 5_000_000
    assert v["isk_per_lp"] == pytest.approx(4_500_000 / 5500)
    assert v["missing"] == []


@pytest.mark.parametrize("prices, missing", [({2506: 1000.0}, [27339]), ({27339: 3000.0}, [2506])])
def test_missing_price_is_not_treated_as_zero(prices, missing):
    v = lp_offer_value(_OFFER, prices)
    assert v["missing"] == missing
    assert v["profit"] is None and v["isk_per_lp"] is None


def test_rank_sorts_by_isk_per_lp_and_puts_unvalued_last():
    offers = [
        {**_OFFER, "offer_id": 1, "type_id": 1, "required_items": []},
        {**_OFFER, "offer_id": 2, "type_id": 2, "required_items": []},
        {**_OFFER, "offer_id": 3, "type_id": 3, "required_items": []},
        {**_OFFER, "offer_id": 4, "type_id": 4, "lp_cost": 0, "required_items": []},
    ]
    ranked = rank_lp_offers(offers, {1: 1000.0, 2: 5000.0, 4: 9000.0})
    assert [r["type_id"] for r in ranked] == [2, 1, 3, 4]
