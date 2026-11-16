"""曙光 ESI → 扁平 SDE YAML（`scripts/build_infinity_sde.py` 的完整快照数据源）。

只在开发机手动运行（全量约 2 万次 GET，不进 CI）。产出的结构严格贴合现有导入器读取的字段：

- `getitems.write_items`：types 的 `name.{en,zh}` / groupID / marketGroupID / volume / iconID；
  groups、marketGroups 的 `name.{en,zh}`
- `sde_loader`：metaGroupID、categoryID、parentGroupID、dogma 名称、NPC 公司名、
  `npcStations`（solarSystemID / typeID / ownerID）、`map*`（name / regionID / constellationID / securityStatus）

两条原则：
1. **只收已发布（published）物品**——ESI 的 types 列表含大量未发布 ID，入库只会制造噪声；
   但蓝图引用到的未发布 type 由调用方经 `extra_type_ids` 补进来，保证配方材料都有名字。
2. **任何一次请求最终失败都整体失败**（`ExportError`），绝不产出缺条目的「完整」快照。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

ESI_BASE = "https://ali-esi.evepc.163.com/latest"
DATASOURCE = "infinity"
LANGS = ("en", "zh")
#: `metaGroupID` 不在 ESI type 顶层字段里，只能从 dogma 属性 1692 取
META_GROUP_ATTR = 1692

#: 曙光 ESI 是旧式错误限额（窗口内 100 次非 2xx → 420）；并发 10 时实测 40 请求 ≈ 0.4s、余量不降
DEFAULT_CONCURRENCY = 10
_RETRIES = 4


class ExportError(RuntimeError):
    """导出不完整：任一必需请求最终失败。"""


def esi_url(path: str, **params: object) -> str:
    query = {"datasource": DATASOURCE, **{k: v for k, v in params.items() if v is not None}}
    return f"{ESI_BASE}{path}?{urlencode(query)}"


Fetch = Callable[[str], Awaitable[Any]]


class EsiFetcher:
    """带重试 / 错误限额自适应 / 可选磁盘缓存（断点续拉）的 GET 客户端。"""

    def __init__(self, session: aiohttp.ClientSession, cache_dir: Path | None, concurrency: int) -> None:
        self._session = session
        self._cache_dir = cache_dir
        self._sem = asyncio.Semaphore(concurrency)
        self._pause_until = 0.0
        self.requests = 0

    def _cache_file(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        import hashlib

        return self._cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    async def get(self, url: str) -> Any:
        cache = self._cache_file(url)
        if cache is not None and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        last = ""
        for attempt in range(_RETRIES):
            delay = self._pause_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            async with self._sem:
                self.requests += 1
                try:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        self._note_error_limit(resp.headers)
                        if resp.status == 200:
                            data = await resp.json()
                            if cache is not None:
                                cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                            return data
                        last = f"HTTP {resp.status}"
                        if resp.status in (420, 429):
                            reset = _int_header(resp.headers, "X-Esi-Error-Limit-Reset") or 30
                            self._pause_until = max(self._pause_until, time.monotonic() + reset + 1)
                        elif resp.status < 500:
                            break  # 4xx（404 等）重试无意义
                except (TimeoutError, aiohttp.ClientError) as exc:
                    last = repr(exc)
            await asyncio.sleep(min(2**attempt, 8))
        raise ExportError(f"请求失败（{last}）：{url}")

    def _note_error_limit(self, headers: Any) -> None:
        remain = _int_header(headers, "X-Esi-Error-Limit-Remain")
        reset = _int_header(headers, "X-Esi-Error-Limit-Reset")
        if remain is not None and reset is not None and remain <= 10:
            self._pause_until = max(self._pause_until, time.monotonic() + reset + 1)


def _int_header(headers: Any, name: str) -> int | None:
    try:
        return int(headers.get(name))
    except (TypeError, ValueError):
        return None


async def _gather(fetch: Fetch, urls: Iterable[str]) -> list[Any]:
    return list(await asyncio.gather(*(fetch(u) for u in urls)))


async def _localized(fetch: Fetch, path: str, ids: Iterable[int]) -> dict[int, dict[str, dict]]:
    """对每个 id 拉 en/zh 两份详情 → `{id: {"en": {...}, "zh": {...}}}`。"""
    ids = sorted(set(ids))
    out: dict[int, dict[str, dict]] = {i: {} for i in ids}
    for lang in LANGS:
        rows = await _gather(fetch, (esi_url(path.format(id=i), language=lang) for i in ids))
        for i, row in zip(ids, rows, strict=True):
            out[i][lang] = row
    return out


def _name(pair: dict[str, dict], key: str = "name") -> dict[str, str]:
    return {lang: str(pair[lang].get(key) or "") for lang in LANGS}


async def _paged_ids(fetch: Fetch, path: str, pages: int) -> list[int]:
    chunks = await _gather(fetch, (esi_url(path, page=p) for p in range(1, pages + 1)))
    return sorted({int(i) for chunk in chunks for i in chunk})


def convert_type(type_id: int, pair: dict[str, dict]) -> dict[str, Any]:
    """ESI type 详情（en/zh）→ SDE types.yaml 条目。"""
    en = pair["en"]
    entry: dict[str, Any] = {
        "name": _name(pair),
        "groupID": en.get("group_id"),
        "volume": en.get("packaged_volume", en.get("volume", 0.0)),
        "portionSize": en.get("portion_size", 1),
        "published": bool(en.get("published")),
    }
    if en.get("market_group_id") is not None:
        entry["marketGroupID"] = en["market_group_id"]
    if en.get("icon_id") is not None:
        entry["iconID"] = en["icon_id"]
    for attr in en.get("dogma_attributes") or []:
        if attr.get("attribute_id") == META_GROUP_ATTR:
            entry["metaGroupID"] = int(attr["value"])
    return entry


async def export_all(
    fetch: Fetch,
    *,
    type_pages: int,
    group_pages: int,
    extra_type_ids: Iterable[int] = (),
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """拉取并转换全部扁平 YAML 数据（返回 `{成员文件名: 可 safe_dump 的对象}`）。"""
    files: dict[str, Any] = {}

    log("categories / groups / marketGroups ...")
    cat_ids = [int(i) for i in await fetch(esi_url("/universe/categories/"))]
    cats = await _localized(fetch, "/universe/categories/{id}/", cat_ids)
    files["categories.yaml"] = {
        i: {"name": _name(p), "published": bool(p["en"].get("published"))} for i, p in cats.items()
    }
    group_ids = await _paged_ids(fetch, "/universe/groups/", pages=group_pages)
    groups = await _localized(fetch, "/universe/groups/{id}/", group_ids)
    files["groups.yaml"] = {
        i: {"name": _name(p), "categoryID": p["en"].get("category_id"), "published": bool(p["en"].get("published"))}
        for i, p in groups.items()
    }
    mg_ids = [int(i) for i in await fetch(esi_url("/markets/groups/"))]
    mgs = await _localized(fetch, "/markets/groups/{id}/", mg_ids)
    files["marketGroups.yaml"] = {
        i: {"name": _name(p), "parentGroupID": p["en"].get("parent_group_id")} for i, p in mgs.items()
    }

    log(f"types（{type_pages} 页，逐个详情 ×2 语言）...")
    all_type_ids = await _paged_ids(fetch, "/universe/types/", type_pages)
    en_rows = await _gather(fetch, (esi_url(f"/universe/types/{i}/", language="en") for i in all_type_ids))
    wanted = {i for i, row in zip(all_type_ids, en_rows, strict=True) if row.get("published")}
    wanted |= {int(i) for i in extra_type_ids} & set(all_type_ids)
    keep = sorted(wanted)
    en_by_id = dict(zip(all_type_ids, en_rows, strict=True))
    zh_rows = await _gather(fetch, (esi_url(f"/universe/types/{i}/", language="zh") for i in keep))
    files["types.yaml"] = {
        i: convert_type(i, {"en": en_by_id[i], "zh": zh}) for i, zh in zip(keep, zh_rows, strict=True)
    }
    log(f"  types: 列表 {len(all_type_ids)}，收录 {len(keep)}（已发布 + 蓝图引用）")

    log("metaGroups / dogma ...")
    meta_ids = sorted({t["metaGroupID"] for t in files["types.yaml"].values() if "metaGroupID" in t})
    # ESI 没有 metaGroup 名称接口：名称由客户端 metagroups 资源补（缺失时留空，不影响导入）
    files["metaGroups.yaml"] = {i: {"name": {"en": "", "zh": ""}} for i in meta_ids}
    attr_ids = [int(i) for i in await fetch(esi_url("/dogma/attributes/"))]
    attrs = await _gather(fetch, (esi_url(f"/dogma/attributes/{i}/") for i in attr_ids))
    files["dogmaAttributes.yaml"] = {
        i: {
            "name": a.get("name", ""),
            "displayName": a.get("display_name") or "",
            "unitID": a.get("unit_id"),
            "iconID": a.get("icon_id"),
        }
        for i, a in zip(attr_ids, attrs, strict=True)
    }
    eff_ids = [int(i) for i in await fetch(esi_url("/dogma/effects/"))]
    effs = await _gather(fetch, (esi_url(f"/dogma/effects/{i}/") for i in eff_ids))
    files["dogmaEffects.yaml"] = {
        i: {"effectName": e.get("name", ""), "description": e.get("description") or "", "iconID": e.get("icon_id")}
        for i, e in zip(eff_ids, effs, strict=True)
    }

    log("universe（星域 / 星座 / 星系 / 星门 / NPC 空间站）...")
    region_ids = [int(i) for i in await fetch(esi_url("/universe/regions/"))]
    regions = await _localized(fetch, "/universe/regions/{id}/", region_ids)
    files["mapRegions.yaml"] = {i: {"name": _name(p)} for i, p in regions.items()}
    const_ids = [int(i) for i in await fetch(esi_url("/universe/constellations/"))]
    consts = await _localized(fetch, "/universe/constellations/{id}/", const_ids)
    files["mapConstellations.yaml"] = {
        i: {"name": _name(p), "regionID": p["en"].get("region_id")} for i, p in consts.items()
    }
    const_region = {i: v["regionID"] for i, v in files["mapConstellations.yaml"].items()}
    system_ids = [int(i) for i in await fetch(esi_url("/universe/systems/"))]
    systems = await _localized(fetch, "/universe/systems/{id}/", system_ids)
    files["mapSolarSystems.yaml"] = {
        i: {
            "name": _name(p),
            "constellationID": p["en"].get("constellation_id"),
            "regionID": const_region.get(p["en"].get("constellation_id")),
            "securityStatus": p["en"].get("security_status", 0.0),
        }
        for i, p in systems.items()
    }
    # 星门：官方扁平 SDE 的 mapStargates.yaml 同形（destination 同时给目标星门与目标星系）。
    # 运输合同跳数走 reference.db.stargate 的星门图 BFS，缺了只能退回内置距离表。
    gate_ids = sorted({int(g) for p in systems.values() for g in (p["en"].get("stargates") or [])})
    gates = await _gather(fetch, (esi_url(f"/universe/stargates/{i}/") for i in gate_ids))
    files["mapStargates.yaml"] = {
        i: {
            "solarSystemID": g.get("system_id"),
            "typeID": g.get("type_id"),
            "destination": {
                "stargateID": (g.get("destination") or {}).get("stargate_id"),
                "solarSystemID": (g.get("destination") or {}).get("system_id"),
            },
        }
        for i, g in zip(gate_ids, gates, strict=True)
    }
    station_ids = sorted({int(s) for p in systems.values() for s in (p["en"].get("stations") or [])})
    stations = await _gather(fetch, (esi_url(f"/universe/stations/{i}/", language="zh") for i in station_ids))
    # 扁平布局的 npcStations 没有站名；这里额外带 stationName（中文），订单详情 / 合同的
    # 站名解析依赖它（`sde_loader._station_rows` 读字典格式时取该键）。
    files["npcStations.yaml"] = {
        i: {
            "solarSystemID": s.get("system_id"),
            "typeID": s.get("type_id"),
            "ownerID": s.get("owner"),
            "stationName": s.get("name", ""),
        }
        for i, s in zip(station_ids, stations, strict=True)
    }

    log("NPC 公司 ...")
    corp_ids = [int(i) for i in await fetch(esi_url("/corporations/npccorps/"))]
    corps = await _gather(fetch, (esi_url(f"/corporations/{i}/") for i in corp_ids))
    files["npcCorporations.yaml"] = {
        i: {"name": {"en": c.get("name", ""), "zh": c.get("name", "")}} for i, c in zip(corp_ids, corps, strict=True)
    }

    # ESI 不提供以下数据；导入器对空字典是「跳过并告警」，不会覆盖成错误值
    files["icons.yaml"] = {}
    files["stationOperations.yaml"] = {}
    files["stationServices.yaml"] = {}
    return files


async def list_pages(session: aiohttp.ClientSession, path: str) -> int:
    """读列表接口第一页的 `X-Pages`（types 64 页、groups 2 页会随版本变）。"""
    async with session.get(esi_url(path, page=1), timeout=aiohttp.ClientTimeout(total=60)) as resp:
        if resp.status != 200:
            raise ExportError(f"页数探测失败（HTTP {resp.status}）：{path}")
        return max(1, int(resp.headers.get("X-Pages", 1)))
