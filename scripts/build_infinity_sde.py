"""Build the Infinity static-data snapshot consumed by `services/importers/sde_cache.py`.

两种模式：

- `--blueprints-only`：只读客户端蓝图，产出 `complete: false` 的包（运行时会拒绝用于初始化）。
- 完整模式（默认）：客户端蓝图 + 客户端精炼产物（typematerials.fsdbinary）+ 曙光 ESI 导出的
  物品/分组/市场分组/星图/NPC 站/dogma/NPC 公司，产出 `complete: true` 的包。约 2 万次 ESI
  请求，`--esi-cache` 指向一个目录可断点续拉。

在仓库根以模块方式运行（脚本间互相 import）：
    .venv/Scripts/python.exe -m scripts.build_infinity_sde --client-root C:/Games/EVE/SharedCache/infinity \
        --output <客户端目录外的新 zip> --esi-cache <缓存目录>

客户端只读：start.ini、resfileindex.txt 与索引引用的资源；不加载客户端代码、不读 pickle。
ZIP 成员固定时间戳、STORED 存储，同样输入产出逐字节相同的包。
"""

from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

RESOURCE = "res:/staticdata/blueprints.static"
# Flat-layout members consumed by sde_cache / sde_loader / getitems.
MISSING_FILES = (
    "types.yaml",
    "groups.yaml",
    "marketGroups.yaml",
    "metaGroups.yaml",
    "typeMaterials.yaml",
    "dogmaAttributes.yaml",
    "dogmaEffects.yaml",
    "icons.yaml",
    "categories.yaml",
    "npcStations.yaml",
    "stationOperations.yaml",
    "stationServices.yaml",
    "npcCorporations.yaml",
    "mapRegions.yaml",
    "mapConstellations.yaml",
    "mapSolarSystems.yaml",
    "mapStargates.yaml",
)
ACTIVITIES = frozenset(
    {
        "manufacturing",
        "research_material",
        "research_time",
        "copying",
        "invention",
        "reaction",
    }
)


def confined_path(root: Path, relative: str) -> Path:
    """Reject traversal, Windows drive/ADS paths and escaping symlinks."""
    normalized = relative.replace("\\", "/")
    parts = PurePosixPath(normalized)
    if parts.is_absolute() or ":" in normalized or ".." in parts.parts:
        raise ValueError(f"Unsafe resource path: {relative}")
    resolved = (root / normalized).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Resource escapes root: {relative}")
    return resolved


def client_build(client_root: Path) -> int:
    config = configparser.ConfigParser(interpolation=None)
    config.read(confined_path(client_root, "start.ini"), encoding="utf-8-sig")
    branch = config.get("main", "branch")
    if not re.search(r"(?:^|[/\\-])infinity(?:$|[/\\])", branch, re.IGNORECASE):
        raise ValueError(f"Not an Infinity client branch: {branch}")
    build = config.getint("main", "build")
    if build <= 0:
        raise ValueError("Client build must be positive")
    return build


def indexed_resource(client_root: Path) -> tuple[Path, dict[str, Any]]:
    matches = []
    with confined_path(client_root, "resfileindex.txt").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.reader(stream):
            if row and row[0].strip().lower() == RESOURCE:
                matches.append(row)
    if len(matches) != 1 or len(matches[0]) != 5:
        raise ValueError("Expected exactly one five-column blueprint resource index entry")
    _, relative, expected_md5, size, _compressed_size = matches[0]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise ValueError("Invalid index MD5")
    expected_size = int(size)
    if expected_size <= 0:
        raise ValueError("Invalid index resource size")
    resource_root = client_root.parent / "ResFiles"
    path = confined_path(resource_root, relative)
    data = path.read_bytes()
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
    if len(data) != expected_size:
        raise ValueError(f"Resource size mismatch: {len(data)} != {expected_size}")
    if digest != expected_md5.lower():
        raise ValueError(f"Resource MD5 mismatch: {digest} != {expected_md5}")
    return path, {"resource": RESOURCE, "path": relative, "size": len(data), "md5": digest}


def _integer(value: Any, label: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid {label}: {value!r}")


def validate_blueprint(key: Any, data: Any) -> tuple[int, dict[str, Any]]:
    """Validate fields consumed by getblueprints without renaming activities."""
    if not re.fullmatch(r"[1-9][0-9]*", str(key)):
        raise ValueError(f"Invalid blueprint key: {key!r}")
    bp_id = int(key)
    if not isinstance(data, dict) or data.get("blueprintTypeID") != bp_id:
        raise ValueError(f"Blueprint key/type mismatch: {key}")
    _integer(data["blueprintTypeID"], "blueprintTypeID", 1)
    _integer(data.get("maxProductionLimit"), "maxProductionLimit", 1)
    activities = data.get("activities")
    # Blueprint 86487 is an empty placeholder in build 3684550; retain its row.
    if not isinstance(activities, dict):
        raise ValueError(f"Missing blueprint activities: {key}")
    for activity, detail in activities.items():
        if activity not in ACTIVITIES or not isinstance(detail, dict):
            raise ValueError(f"Unsupported blueprint activity: {activity}")
        _integer(detail.get("time"), "activity time")
        for field in ("materials", "products", "skills"):
            entries = detail.get(field, [])
            if not isinstance(entries, list):
                raise ValueError(f"Invalid {field} list: {key}")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(f"Invalid {field} entry: {key}")
                _integer(entry.get("typeID"), "typeID", 1)
                # The client contains eight level-zero skill requirements; preserve them.
                _integer(entry.get("level" if field == "skills" else "quantity"), field, 0 if field == "skills" else 1)
                if "probability" in entry:
                    probability = entry["probability"]
                    if type(probability) not in (int, float) or not 0 <= probability <= 1:
                        raise ValueError(f"Invalid probability: {probability!r}")
    return bp_id, data


def read_blueprints(path: Path) -> dict[int, dict[str, Any]]:
    result = {}
    # immutable avoids journal creation; mode=ro additionally forbids SQL writes.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        for key, value in db.execute("SELECT key, value FROM cache"):
            bp_id, data = validate_blueprint(key, json.loads(value))
            if bp_id in result:
                raise ValueError(f"Duplicate blueprint: {bp_id}")
            result[bp_id] = data
    if not result:
        raise ValueError("Blueprint resource is empty")
    return dict(sorted(result.items()))


def snapshot_bytes(
    client_root: Path,
    *,
    extra_files: dict[str, Any] | None = None,
    extra_sources: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Return deterministic ZIP bytes and a manifest; never write client files.

    `extra_files`（成员名 → 可 YAML 序列化对象）为空时是 blueprints-only 包（complete=false）；
    给齐 `MISSING_FILES` 全部成员才标 complete=true —— 少一个都不行。
    """
    client_root = client_root.resolve()
    build = client_build(client_root)
    path, source = indexed_resource(client_root)
    blueprints = read_blueprints(path)
    # Detect changes during extraction rather than publishing mixed client state.
    _, after = indexed_resource(client_root)
    if after != source or client_build(client_root) != build:
        raise ValueError("Client changed during extraction; retry after update completes")
    extra = dict(extra_files or {})
    unknown = set(extra) - set(MISSING_FILES)
    if unknown:
        raise ValueError(f"Unexpected snapshot members: {sorted(unknown)}")
    missing = sorted(set(MISSING_FILES) - set(extra))
    complete = not missing
    metadata = {"sde": {"server": "infinity", "buildNumber": build, "complete": complete}}
    files = {
        "_sde.yaml": yaml.safe_dump(metadata, sort_keys=True).encode("utf-8"),
        "blueprints.yaml": yaml.safe_dump(blueprints, sort_keys=True, allow_unicode=True).encode("utf-8"),
    }
    for name, obj in sorted(extra.items()):
        files[name] = yaml.safe_dump(obj, sort_keys=True, allow_unicode=True).encode("utf-8")
    manifest = {
        "formatVersion": 1,
        "server": "infinity",
        "buildNumber": build,
        "scope": "full" if complete else "blueprints-only" if not extra else "partial",
        "complete": complete,
        "missingFiles": missing,
        "unavailableFiles": ["agents.yaml", "researchAgents.yaml"],
        "blueprintCount": len(blueprints),
        "source": source,
        "extraSources": extra_sources or {},
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in sorted(files.items())
        },
    }
    if not complete:
        manifest["reason"] = "Not a complete initialization SDE; runtime verification rejects it."
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buffer.getvalue(), manifest


def blueprint_type_ids(blueprints: dict[int, dict[str, Any]]) -> set[int]:
    """蓝图自身 + 所有活动的材料/产物/技能 typeID —— 未发布的也要进 types.yaml，配方才有名字。"""
    ids: set[int] = set(blueprints)
    for bp in blueprints.values():
        for detail in (bp.get("activities") or {}).values():
            for field in ("materials", "products", "skills"):
                ids.update(int(e["typeID"]) for e in detail.get(field, []))
    return ids


async def collect_full(client_root: Path, esi_cache: Path | None, concurrency: int) -> tuple[dict, dict]:
    """客户端精炼产物 + 曙光 ESI 导出 → `(extra_files, extra_sources)`。"""
    import aiohttp

    from scripts import infinity_esi_export as esi
    from scripts.infinity_fsd import load_type_materials, to_sde_yaml

    materials, mat_source = load_type_materials(client_root)
    path, _ = indexed_resource(client_root)
    extra_ids = blueprint_type_ids(read_blueprints(path))
    if esi_cache is not None:
        esi_cache.mkdir(parents=True, exist_ok=True)
    headers = {"Accept": "application/json", "User-Agent": "EVE-Industrial-Assistant-Shuguang/snapshot"}
    async with aiohttp.ClientSession(headers=headers) as session:
        status = await esi.EsiFetcher(session, None, 1).get(esi.esi_url("/status/"))
        fetcher = esi.EsiFetcher(session, esi_cache, concurrency)
        files = await esi.export_all(
            fetcher.get,
            type_pages=await esi.list_pages(session, "/universe/types/"),
            group_pages=await esi.list_pages(session, "/universe/groups/"),
            extra_type_ids=extra_ids,
        )
    files["typeMaterials.yaml"] = to_sde_yaml(materials)
    sources = {
        "typeMaterials": mat_source,
        "esi": {
            "base": esi.ESI_BASE,
            "datasource": esi.DATASOURCE,
            "serverVersion": status.get("server_version"),
            "requests": fetcher.requests,
        },
    }
    return files, sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-root", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New ZIP path (existing files are never overwritten)"
    )
    parser.add_argument(
        "--blueprints-only",
        action="store_true",
        help="Only client blueprints (complete=false, rejected for initialization)",
    )
    parser.add_argument("--esi-cache", type=Path, help="Directory for resumable ESI response cache")
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args(argv)
    client_root = args.client_root.resolve()
    output = args.output.resolve()
    protected = (client_root, (client_root.parent / "ResFiles").resolve())
    for target in (output, args.esi_cache.resolve() if args.esi_cache else None):
        if target is not None and any(target.is_relative_to(p) for p in protected):
            parser.error("Output and ESI cache must be outside the client and ResFiles trees")
    if output.exists():
        parser.error(f"Output already exists: {output}")
    try:
        extra: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        if not args.blueprints_only:
            import asyncio

            extra, sources = asyncio.run(collect_full(client_root, args.esi_cache, args.concurrency))
            server_version = sources["esi"]["serverVersion"]
            if server_version is not None and int(server_version) != client_build(client_root):
                raise ValueError(
                    f"Client build {client_build(client_root)} != server version {server_version}; "
                    "update the client before building a snapshot"
                )
        data, manifest = snapshot_bytes(client_root, extra_files=extra, extra_sources=sources)
        # Exclusive creation protects existing snapshots and client input files.
        with output.open("xb") as stream:
            stream.write(data)
    except Exception as exc:  # 网络/解析/IO 任一失败：整体失败，不留半成品
        parser.exit(1, f"Snapshot failed: {type(exc).__name__}: {exc}\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": hashlib.sha256(data).hexdigest(),
                "blueprintCount": manifest["blueprintCount"],
                "complete": manifest["complete"],
                "missingFiles": manifest["missingFiles"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
