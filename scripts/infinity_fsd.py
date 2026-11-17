"""只读解析曙光客户端 res:/staticdata/typematerials.fsdbinary（纯 Python，不加载客户端代码）。

布局（小端，偏移相对 payload 基址 B=0x20，除特别说明）：
  文件 0x00..0x18  24 字节头（未解释，不校验）
  文件 0x18        u64 payload 长度 = 文件长 - 0x20
  B+0x00 u64 桶数组偏移（=0x18）   B+0x08 u64 条目数   B+0x10 u64 桶数 nb
  B+0x18 nb 个 u64 桶偏移；桶偏移-8 处 u64 = 桶内节点数，节点 32B 连续排列
  节点 {u64 typeID, u64 materials, u64 randomizedMaterials, u64 hasRandomized}
    typeID % nb == 桶序号；两个列表指针各自 -8 处 u64 = 元素数
    materials 元素 8B {u32 materialTypeID, u32 quantity}
    randomizedMaterials 元素 12B {u32 materialTypeID, u32 quantityMin, u32 quantityMax}
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

PAYLOAD_BASE = 0x20
RESOURCE = "res:/staticdata/typematerials.fsdbinary"


def _parse(data: bytes) -> tuple[dict[int, list[tuple[int, int]]], dict[int, list[tuple[int, int, int]]]]:
    if len(data) < PAYLOAD_BASE + 0x18:
        raise ValueError("文件过短")
    B = PAYLOAD_BASE
    (plen,) = struct.unpack_from("<Q", data, 0x18)
    if plen != len(data) - B:
        raise ValueError(f"payload 长度不符: {plen} != {len(data) - B}")
    table_off, n_entries, nb = struct.unpack_from("<3Q", data, B)
    if table_off != 0x18 or nb == 0 or B + table_off + 8 * nb > len(data):
        raise ValueError("桶表头非法")

    def u64(rel: int) -> int:
        if rel < 0 or B + rel + 8 > len(data):
            raise ValueError(f"越界指针 0x{rel:x}")
        value: int = struct.unpack_from("<Q", data, B + rel)[0]
        return value

    def array(rel: int, size: int) -> int:
        count = u64(rel - 8)
        if B + rel + count * size > len(data):
            raise ValueError(f"数组越界 0x{rel:x} count={count}")
        return count

    materials: dict[int, list[tuple[int, int]]] = {}
    randomized: dict[int, list[tuple[int, int, int]]] = {}
    for bucket in range(nb):
        node_rel = u64(table_off + 8 * bucket)
        for j in range(array(node_rel, 32)):
            type_id, mat_rel, rnd_rel, has_rnd = struct.unpack_from("<4Q", data, B + node_rel + 32 * j)
            if type_id % nb != bucket:
                raise ValueError(f"typeID {type_id} 不属于桶 {bucket}")
            if type_id in materials:
                raise ValueError(f"重复 typeID {type_id}")
            mc = array(mat_rel, 8)
            rc = array(rnd_rel, 12)
            if has_rnd != (1 if rc else 0):
                raise ValueError(f"typeID {type_id} randomized 标志 {has_rnd} 与元素数 {rc} 不符")
            materials[type_id] = [struct.unpack_from("<II", data, B + mat_rel + 8 * i) for i in range(mc)]
            if rc:
                randomized[type_id] = [struct.unpack_from("<3I", data, B + rnd_rel + 12 * i) for i in range(rc)]
    if len(materials) != n_entries:
        raise ValueError(f"条目数不符: 头部 {n_entries}, 遍历 {len(materials)}")
    return materials, randomized


def parse_type_materials(data: bytes) -> dict[int, list[tuple[int, int]]]:
    """typeID -> [(materialTypeID, quantity)]；含空列表条目（仅 randomized 或无材料）。"""
    return _parse(data)[0]


def parse_randomized_materials(data: bytes) -> dict[int, list[tuple[int, int, int]]]:
    """typeID -> [(materialTypeID, quantityMin, quantityMax)]，仅非空者。"""
    return _parse(data)[1]


def _locate(client_root: Path, resource: str) -> tuple[Path, int, str]:
    index = client_root / "resfileindex.txt"
    res_root = (client_root.parent / "ResFiles").resolve()
    for line in index.read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if len(parts) >= 4 and parts[0].lower() == resource:
            path = (res_root / parts[1]).resolve()
            if not path.is_relative_to(res_root):
                raise ValueError(f"资源路径越界: {parts[1]}")
            return path, int(parts[3]), parts[2].lower()
    raise ValueError(f"索引中未找到 {resource}")


def load_type_materials(client_root: Path) -> tuple[dict[int, list[tuple[int, int]]], dict]:
    """client_root 指向 SharedCache/infinity（含 resfileindex.txt）。"""
    path, size, md5 = _locate(Path(client_root), RESOURCE)
    data = path.read_bytes()
    if len(data) != size or hashlib.md5(data, usedforsecurity=False).hexdigest() != md5:
        raise ValueError(f"{path} size/MD5 与索引不符")
    result = parse_type_materials(data)
    relative = path.relative_to((Path(client_root).parent / "ResFiles").resolve()).as_posix()
    return result, {"resource": RESOURCE, "path": relative, "size": size, "md5": md5, "entryCount": len(result)}


def to_sde_yaml(materials: dict[int, list[tuple[int, int]]]) -> dict[int, dict]:
    """→ 扁平 SDE `typeMaterials.yaml` 结构（`sde_loader.write_type_materials` 读取的字段）。

    空材料条目（仅 randomized 或两者皆空）不输出：导入器只消费 materials 列表。
    """
    return {
        tid: {"materials": [{"materialTypeID": m, "quantity": q} for m, q in mats]}
        for tid, mats in sorted(materials.items())
        if mats
    }


# ── 行星工业配方 res:/staticdata/schematics.fsdbinary ───────────────────────
#
# 与 typematerials 同一套 FSD 布局：B+0x00 u64 桶表偏移 / u64 条目数 / u64 桶数，
# 桶偏移 -8 处 u64 = 桶内节点数。2026-11-17 对 build 3684550 逐字节验证（68 条，
# 68 条的 cycle_time 与曙光 ESI `/universe/schematics/{id}/` 全部一致）：
#   节点 40B {u64 schematicID, u64 pins, u64 types, u64 未解释, u32 cycleTime, u32 nameID}
#     pins  → 数组（-8 处 u64 = 个数），元素 u32 = 可运行该配方的设施 typeID
#     types → **嵌套哈希表**（-8 处 u64 = 桶数，桶内节点 12B {u32 typeID, u32 quantity, u32 isInput}），
#             typeID % 桶数 == 桶序号；isInput=1 是原料，0 是产物
SCHEMATICS_RESOURCE = "res:/staticdata/schematics.fsdbinary"


def parse_schematics(data: bytes) -> dict[int, dict]:
    """schematicID → {cycle_time, inputs: [(typeID, qty)], outputs: [(typeID, qty)], pins: [typeID]}。"""
    if len(data) < PAYLOAD_BASE + 0x18:
        raise ValueError("文件过短")
    B = PAYLOAD_BASE
    (plen,) = struct.unpack_from("<Q", data, 0x18)
    if plen != len(data) - B:
        raise ValueError(f"payload 长度不符: {plen} != {len(data) - B}")

    def u64(rel: int) -> int:
        if rel < 0 or B + rel + 8 > len(data):
            raise ValueError(f"越界指针 0x{rel:x}")
        value: int = struct.unpack_from("<Q", data, B + rel)[0]
        return value

    def hashed(rel: int, fmt: str) -> list[tuple]:
        size = struct.calcsize(fmt)
        buckets = u64(rel - 8)
        rows = []
        for b in range(buckets):
            node_rel = u64(rel + 8 * b)
            count = u64(node_rel - 8)
            if B + node_rel + count * size > len(data):
                raise ValueError(f"桶越界 0x{node_rel:x}")
            for j in range(count):
                row = struct.unpack_from(fmt, data, B + node_rel + size * j)
                if row[0] % buckets != b:
                    raise ValueError(f"键 {row[0]} 不属于桶 {b}")
                rows.append(row)
        return rows

    table_off, n_entries, _nb = struct.unpack_from("<3Q", data, B)
    if table_off != 0x18:
        raise ValueError("桶表头非法")
    result: dict[int, dict] = {}
    for sid, pins_rel, types_rel, _unknown, cycle, _name_id in hashed(table_off, "<4Q2I"):
        if sid in result:
            raise ValueError(f"重复 schematicID {sid}")
        entries = hashed(types_rel, "<3I")
        if any(flag not in (0, 1) for _t, _q, flag in entries):
            raise ValueError(f"schematic {sid} isInput 标志非法")
        inputs = sorted((t, q) for t, q, flag in entries if flag == 1)
        outputs = sorted((t, q) for t, q, flag in entries if flag == 0)
        if len(outputs) != 1 or not inputs or cycle <= 0:
            raise ValueError(f"schematic {sid} 结构异常: in={inputs} out={outputs} cycle={cycle}")
        pins = sorted(struct.unpack_from("<I", data, B + pins_rel + 4 * i)[0] for i in range(u64(pins_rel - 8)))
        result[sid] = {"cycle_time": cycle, "inputs": inputs, "outputs": outputs, "pins": pins}
    if len(result) != n_entries:
        raise ValueError(f"条目数不符: 头部 {n_entries}, 遍历 {len(result)}")
    return result


def load_schematics(client_root: Path) -> tuple[dict[int, dict], dict]:
    """client_root 指向 SharedCache/infinity；校验索引 size/MD5 后解析。"""
    path, size, md5 = _locate(Path(client_root), SCHEMATICS_RESOURCE)
    data = path.read_bytes()
    if len(data) != size or hashlib.md5(data, usedforsecurity=False).hexdigest() != md5:
        raise ValueError(f"{path} size/MD5 与索引不符")
    result = parse_schematics(data)
    return result, {"resource": SCHEMATICS_RESOURCE, "size": size, "md5": md5, "entryCount": len(result)}


if __name__ == "__main__":
    import json
    import sys

    args = sys.argv[1:]
    if args and args[0] == "schematics":
        # 用法：python -m scripts.infinity_fsd schematics <client_root> <输出 json>
        from scripts.build_infinity_sde import client_build

        client = Path(args[1] if len(args) > 1 else r"C:\Games\EVE\SharedCache\infinity")
        schematics, meta = load_schematics(client)
        meta["build"] = client_build(client)
        payload = {"source": meta, "schematics": {str(k): v for k, v in sorted(schematics.items())}}
        out = Path(args[2]) if len(args) > 2 else None
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        if out:
            out.write_text(text + "\n", encoding="utf-8")
        print(meta)
    else:
        mats, meta = load_type_materials(Path(args[0] if args else r"C:\Games\EVE\SharedCache\infinity"))
        print(meta, sum(map(len, mats.values())))
