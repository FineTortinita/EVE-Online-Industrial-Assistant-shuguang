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


if __name__ == "__main__":
    import sys

    mats, meta = load_type_materials(Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Games\EVE\SharedCache\infinity"))
    print(meta, sum(map(len, mats.values())))
