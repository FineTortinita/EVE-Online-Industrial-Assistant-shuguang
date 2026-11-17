"""LP 兑换价值：后台拉取 → 估值 → 表格行（ESI 打桩，真 QThread 端到端一条）。"""

from __future__ import annotations

import pytest

from tests.qml_click import spin as _spin

pytestmark = pytest.mark.ui

_OFFERS = [
    {"offer_id": 1, "type_id": 27339, "quantity": 10, "lp_cost": 1000, "isk_cost": 100_000, "required_items": []},
    {"offer_id": 2, "type_id": 999, "quantity": 1, "lp_cost": 500, "isk_cost": 0, "required_items": []},
]


def test_fetch_values_and_renders_chinese_names(qapp, monkeypatch):
    import services.lp_service as lps
    from ui_qml.bridge.lp_store_bridge import LpStoreBridge

    monkeypatch.setattr(lps, "list_npc_corporations", lambda db: [{"id": 1000035, "name": "Caldari Navy"}])
    monkeypatch.setattr(lps, "fetch_lp_offers", lambda corp: list(_OFFERS))
    monkeypatch.setattr(lps, "fetch_corporation_zh_names", lambda ids: {1000035: "加达里海军"})
    calls = []

    def _value(db, offers, hub=None, price_type="sell"):
        calls.append(hub)
        from domain.lp import rank_lp_offers

        rows = rank_lp_offers(offers, {27339: 50_000.0})
        for r in rows:
            r["name"], r["required_names"] = str(r["type_id"]), []
        return rows

    monkeypatch.setattr(lps, "value_lp_offers", _value)
    monkeypatch.setattr(lps, "hub_has_prices", lambda db, hub: True)

    import ui_qml.bridge.lp_store_bridge as lsb

    bridge = LpStoreBridge(db=object())
    bridge.reload()
    for _ in range(100):
        _spin(20)
        if not bridge.busy and not lsb._LIVE:
            break
    # 线程必须在主线程里等完才释放（见 `_release_worker`）；残留会在后续用例 GC 时崩
    assert not lsb._LIVE

    assert bridge.corporations == ["加达里海军"]
    assert calls == ["Sobaseki"]
    first, second = (r["cells"] for r in bridge.rows)
    assert first[0]["text"] == "27339" and first[-1]["text"] == "400"  # (10×50000−100000)/1000
    assert second[-1]["text"] == "缺价"
    assert "可估值 1 项" in bridge.statusText


def test_missing_hub_prices_explains_how_to_fetch(qapp, monkeypatch):
    """索巴色基是新加的默认中心：本地还没拉过它的价格时要给出操作提示，而不是整表「缺价」。"""
    import services.lp_service as lps
    from ui_qml.bridge.lp_store_bridge import LpStoreBridge

    monkeypatch.setattr(lps, "list_npc_corporations", lambda db: [{"id": 1000035, "name": "加达里海军"}])
    monkeypatch.setattr(lps, "hub_has_prices", lambda db, hub: False)
    bridge = LpStoreBridge(db=object())
    bridge._on_offers(1000035, list(_OFFERS), [])
    assert bridge.rows == []
    assert "更新价格" in bridge.emptyText
