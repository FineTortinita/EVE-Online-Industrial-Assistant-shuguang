"""曙光 ESI 导出：字段映射贴合导入器、未发布物品过滤、请求失败整体失败（全程假 fetch，不联网）。"""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import infinity_esi_export as esi


def _fake_esi(overrides: dict | None = None):
    """按路径 + language 返回最小数据集；`overrides[path] = Exception` 模拟最终失败。"""
    types: dict[int, dict] = {
        34: {
            "published": True,
            "group_id": 18,
            "market_group_id": 1857,
            "packaged_volume": 0.01,
            "portion_size": 1,
            "icon_id": 22,
            "dogma_attributes": [{"attribute_id": 1692, "value": 1.0}],
        },
        98001: {"published": True, "group_id": 5120, "market_group_id": 3890, "volume": 1e6, "portion_size": 1},
        77: {"published": False, "group_id": 18},  # 未发布、也不被蓝图引用 → 不收录
        42226: {"published": False, "group_id": 18},  # 未发布但蓝图引用 → 收录
    }
    names = {
        "en": {34: "Tritanium", 98001: "Zhuri", 77: "Junk", 42226: "Part"},
        "zh": {34: "三钛合金", 98001: "诛日级", 77: "废料", 42226: "部件"},
    }

    async def fetch(url: str):
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        assert q["datasource"] == ["infinity"], "每个请求都必须锁曙光 datasource"
        path = parsed.path.removeprefix("/latest")
        lang = q.get("language", ["en"])[0]
        if overrides and path in overrides:
            raise overrides[path]
        parts = [p for p in path.split("/") if p]
        tail = int(parts[-1]) if parts[-1].isdigit() else None
        if path == "/universe/types/":
            return list(types) if q.get("page") == ["1"] else []
        if path.startswith("/universe/types/"):
            assert tail is not None
            return {**types[tail], "type_id": tail, "name": names[lang][tail]}
        table = {
            "/universe/categories/": [4],
            "/universe/groups/": [18, 5120],
            "/markets/groups/": [1857],
            "/dogma/attributes/": [1692],
            "/dogma/effects/": [11],
            "/universe/regions/": [10000002],
            "/universe/constellations/": [20000020],
            "/universe/systems/": [30000142],
            "/corporations/npccorps/": [1000035],
        }
        if path in table:
            return table[path] if q.get("page", ["1"]) == ["1"] else []
        detail = {
            "categories": {"name": f"cat-{lang}", "published": True},
            "groups": {"name": f"grp-{lang}", "category_id": 4, "published": True},
            "markets": {"name": f"mg-{lang}", "parent_group_id": None},
            "attributes": {"name": "metaGroupID", "display_name": "", "unit_id": None},
            "effects": {"name": "online", "description": ""},
            "regions": {"name": f"伏尔戈-{lang}"},
            "constellations": {"name": f"c-{lang}", "region_id": 10000002},
            "systems": {
                "name": f"吉他-{lang}",
                "constellation_id": 20000020,
                "security_status": 0.946,
                "stations": [60003760],
                "stargates": [50001248],
            },
            "stargates": {
                "system_id": 30000142,
                "type_id": 16,
                "destination": {"stargate_id": 50000802, "system_id": 30000140},
            },
            "stations": {"name": "吉他 IV - 卫星 4", "system_id": 30000142, "type_id": 1531, "owner": 1000035},
            "corporations": {"name": "加达里海军"},
        }
        return detail["markets" if parts[0] == "markets" else parts[-2]]

    return fetch


def test_export_maps_fields_for_importers():
    files = asyncio.run(esi.export_all(_fake_esi(), type_pages=1, group_pages=1, extra_type_ids=[42226]))
    types = files["types.yaml"]
    assert sorted(types) == [34, 42226, 98001], "只收已发布 + 蓝图引用的物品"
    t = types[34]
    assert t["name"] == {"en": "Tritanium", "zh": "三钛合金"}
    assert (t["groupID"], t["marketGroupID"], t["volume"], t["iconID"], t["metaGroupID"]) == (18, 1857, 0.01, 22, 1)
    assert types[98001]["volume"] == 1e6, "没有 packaged_volume 时回落 volume"
    assert files["groups.yaml"][18]["categoryID"] == 4
    assert files["marketGroups.yaml"][1857]["name"] == {"en": "mg-en", "zh": "mg-zh"}
    assert files["mapSolarSystems.yaml"][30000142] == {
        "name": {"en": "吉他-en", "zh": "吉他-zh"},
        "constellationID": 20000020,
        "regionID": 10000002,
        "securityStatus": 0.946,
    }
    assert files["npcStations.yaml"][60003760] == {
        "solarSystemID": 30000142,
        "typeID": 1531,
        "ownerID": 1000035,
        "stationName": "吉他 IV - 卫星 4",
    }
    assert files["metaGroups.yaml"] == {1: {"name": {"en": "", "zh": ""}}}
    assert files["mapStargates.yaml"][50001248] == {
        "solarSystemID": 30000142,
        "typeID": 16,
        "destination": {"stargateID": 50000802, "solarSystemID": 30000140},
    }


@pytest.mark.parametrize("path", ["/universe/types/34/", "/universe/systems/30000142/"])
def test_any_final_failure_aborts_export(path):
    """任一请求最终失败 → 整体抛错，不产出缺条目的「完整」数据。"""
    with pytest.raises(esi.ExportError):
        asyncio.run(esi.export_all(_fake_esi({path: esi.ExportError("boom")}), type_pages=1, group_pages=1))
