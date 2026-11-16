"""Offline snapshot boundaries: identity, integrity, traversal and reproducibility."""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts.build_infinity_sde import (
    RESOURCE,
    confined_path,
    indexed_resource,
    main,
    snapshot_bytes,
    validate_blueprint,
)


@pytest.fixture
def client(tmp_path: Path) -> Path:
    root = tmp_path / "infinity"
    root.mkdir()
    (root / "start.ini").write_text("[main]\nbranch=infinity\nbuild=3684550\n", encoding="utf-8")
    resource = tmp_path / "ResFiles" / "f0" / "blueprints"
    resource.parent.mkdir(parents=True)
    with sqlite3.connect(resource) as db:
        db.execute("CREATE TABLE cache (key TEXT, value TEXT, time INTEGER)")
        data = {
            "blueprintTypeID": 98105,
            "maxProductionLimit": 1,
            "activities": {
                "manufacturing": {"time": 5, "products": [{"typeID": 98001, "quantity": 1}]},
                "research_material": {"time": 1, "skills": [{"typeID": 3409, "level": 0}]},
                "research_time": {"time": 2},
            },
        }
        db.execute("INSERT INTO cache VALUES (?, ?, ?)", ("98105", json.dumps(data), 0))
        empty = {"blueprintTypeID": 86487, "maxProductionLimit": 1, "activities": {}}
        db.execute("INSERT INTO cache VALUES (?, ?, ?)", ("86487", json.dumps(empty), 0))
    raw = resource.read_bytes()
    digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    (root / "resfileindex.txt").write_text(f"{RESOURCE},f0/blueprints,{digest},{len(raw)},1\n", encoding="utf-8")
    return root


def test_reproducible_snapshot_and_import_contract(client: Path) -> None:
    source = client.parent / "ResFiles" / "f0" / "blueprints"
    before = source.read_bytes()
    first, manifest = snapshot_bytes(client)
    second, _ = snapshot_bytes(client)
    assert first == second
    assert source.read_bytes() == before
    assert sorted(p.name for p in source.parent.iterdir()) == ["blueprints"]
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        blueprints = yaml.safe_load(archive.read("blueprints.yaml"))
        assert blueprints[86487]["activities"] == {}
        bp = blueprints[98105]
        assert bp["activities"]["research_material"]["skills"][0]["level"] == 0
        assert bp["activities"]["manufacturing"]["products"] == [{"typeID": 98001, "quantity": 1}]
        assert {"research_material", "research_time"} <= bp["activities"].keys()
        assert yaml.safe_load(archive.read("_sde.yaml"))["sde"] == {
            "server": "infinity",
            "buildNumber": 3684550,
            "complete": False,
        }
        assert json.loads(archive.read("manifest.json")) == manifest
    assert manifest["complete"] is False and "types.yaml" in manifest["missingFiles"]


@pytest.mark.parametrize("drop", [None, "typeMaterials.yaml"])
def test_complete_flag_requires_every_member(client: Path, drop: str | None) -> None:
    """只有给齐全部扁平成员才标 complete=true；少一个就是 partial，运行时校验会拒绝。"""
    from scripts.build_infinity_sde import MISSING_FILES
    from services.importers.sde_cache import verify_snapshot

    extra = {name: {1: {"k": "v"}} for name in MISSING_FILES if name != drop}
    data, manifest = snapshot_bytes(client, extra_files=extra)
    assert manifest["complete"] is (drop is None)
    zip_path = client.parent / "snap.zip"
    zip_path.write_bytes(data)
    if drop is None:
        verify_snapshot(str(zip_path), expected_sha256=hashlib.sha256(data).hexdigest())
    else:
        assert manifest["missingFiles"] == [drop]
        with pytest.raises(RuntimeError, match="不完整"):
            verify_snapshot(str(zip_path), expected_sha256=None)


def test_reject_unknown_member(client: Path) -> None:
    with pytest.raises(ValueError, match="Unexpected"):
        snapshot_bytes(client, extra_files={"evil.yaml": {}})


@pytest.mark.parametrize(
    "relative", ["../outside", "f0/../../outside", r"..\outside", "C:/outside", "/outside", "f0/x:stream"]
)
def test_reject_resource_traversal(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        confined_path(tmp_path, relative)


@pytest.mark.parametrize(
    "column,value,error", [(2, "0" * 32, "MD5 mismatch"), (3, "1", "size mismatch"), (1, "../outside", "Unsafe")]
)
def test_index_integrity(client: Path, column: int, value: str, error: str) -> None:
    index = client / "resfileindex.txt"
    row = index.read_text(encoding="utf-8").strip().split(",")
    row[column] = value
    index.write_text(",".join(row), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        indexed_resource(client)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"blueprintTypeID": 2},
        {"blueprintTypeID": 1, "maxProductionLimit": 1, "activities": {"researchMaterial": {"time": 1}}},
    ],
)
def test_reject_invalid_blueprint(data: object) -> None:
    with pytest.raises(ValueError):
        validate_blueprint("1", data)


@pytest.mark.parametrize("mode", ["client-output", "client-cache", "existing-output"])
def test_cli_refuses_unsafe_publication(client: Path, mode: str) -> None:
    output = client / "snapshot.zip" if mode == "client-output" else client.parent / "snapshot.zip"
    args = ["--client-root", str(client), "--output", str(output), "--blueprints-only"]
    if mode == "client-cache":
        args += ["--esi-cache", str(client.parent / "ResFiles" / "cache")]
    if mode == "existing-output":
        output.write_bytes(b"existing")
    with pytest.raises(SystemExit):
        main(args)
    assert output.read_bytes() == b"existing" if mode == "existing-output" else not output.exists()
