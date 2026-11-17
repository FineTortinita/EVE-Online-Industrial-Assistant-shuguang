import struct

import pytest

from scripts.infinity_fsd import parse_type_materials


def _build(nodes: list[tuple[int, list[tuple[int, int]]]], nb: int = 2, n_entries: int | None = None) -> bytes:
    """合成最小结构：payload = 头 + 桶表 + 每桶 [count, 节点...] + 每节点 [count, mats] [count=0]。"""
    buckets: list[list] = [[] for _ in range(nb)]
    for t, m in nodes:
        buckets[t % nb].append((t, m))
    p = bytearray(struct.pack("<3Q", 0x18, len(nodes) if n_entries is None else n_entries, nb))
    p += b"\0" * (8 * nb)
    for b, items in enumerate(buckets):
        p += struct.pack("<Q", len(items))
        struct.pack_into("<Q", p, 0x18 + 8 * b, len(p))
        node_at = len(p)
        p += b"\0" * (32 * len(items))
        for j, (t, mats) in enumerate(items):
            p += struct.pack("<Q", len(mats))
            mat = len(p)
            p += b"".join(struct.pack("<II", *x) for x in mats)
            p += struct.pack("<Q", 0)
            struct.pack_into("<4Q", p, node_at + 32 * j, t, mat, len(p), 0)
    return b"\0" * 0x18 + struct.pack("<Q", len(p)) + bytes(p)


NODES = [(18, [(34, 175), (36, 70)]), (19, [(34, 48000)]), (20, [])]


def test_parse_minimal() -> None:
    assert parse_type_materials(_build(NODES)) == {18: [(34, 175), (36, 70)], 19: [(34, 48000)], 20: []}


@pytest.mark.parametrize(
    "data",
    [
        _build(NODES)[:-8],  # 截断
        _build(NODES, n_entries=4),  # 头部计数不符
        _build(NODES)[:0x38] + struct.pack("<Q", 1 << 40) + _build(NODES)[0x40:],  # 越界桶指针
    ],
    ids=["truncated", "count-mismatch", "oob-pointer"],
)
def test_parse_rejects_corrupt(data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_type_materials(data)


def _hashed(p: bytearray, rows: list[tuple], fmt: str, nb: int) -> int:
    """在 p 末尾写一个 FSD 哈希表（-8 = 桶数，桶 -8 = 节点数），返回表起点相对 payload 的偏移。"""
    p += struct.pack("<Q", nb)
    table = len(p)
    p += b"\0" * (8 * nb)
    for b in range(nb):
        items = [r for r in rows if r[0] % nb == b]
        p += struct.pack("<Q", len(items))
        struct.pack_into("<Q", p, table + 8 * b, len(p))
        p += b"".join(struct.pack(fmt, *r) for r in items)
    return table


def _build_schematics(types: list[tuple[int, int, int]], cycle: int = 3600, n_entries: int = 1) -> bytes:
    """一条配方 65：pins [2470]，types 嵌套哈希表 {typeID, qty, isInput}（与曙光 build 3684550 同布局）。"""
    p = bytearray(struct.pack("<3Q", 0x18, n_entries, 1))
    p += b"\0" * 8  # 外层桶表（1 桶），稍后回填
    p += struct.pack("<Q", 1)  # 桶内 1 个节点
    struct.pack_into("<Q", p, 0x18, len(p))
    node = len(p)
    p += b"\0" * 40
    p += struct.pack("<Q", 1)
    pins = len(p)
    p += struct.pack("<I", 2470) + b"\0" * 4
    types_rel = _hashed(p, types, "<3I", 2)
    struct.pack_into("<4Q2I", p, node, 65, pins, types_rel, 0, cycle, 0)
    return b"\0" * 0x18 + struct.pack("<Q", len(p)) + bytes(p)


def test_parse_schematics_minimal() -> None:
    from scripts.infinity_fsd import parse_schematics

    data = _build_schematics([(2389, 40, 1), (3645, 40, 1), (9838, 5, 0)])
    assert parse_schematics(data) == {
        65: {"cycle_time": 3600, "inputs": [(2389, 40), (3645, 40)], "outputs": [(9838, 5)], "pins": [2470]}
    }


@pytest.mark.parametrize(
    "data",
    [
        _build_schematics([(2389, 40, 1), (9838, 5, 0)])[:-4],  # 截断
        _build_schematics([(2389, 40, 1), (9838, 5, 0)], n_entries=2),  # 头部计数不符
        _build_schematics([(2389, 40, 1), (9838, 5, 2)]),  # isInput 非 0/1
        _build_schematics([(2389, 40, 1)]),  # 没有产物
        _build_schematics([(2389, 40, 1), (9838, 5, 0)], cycle=0),  # 周期为 0
    ],
    ids=["truncated", "count-mismatch", "bad-flag", "no-output", "zero-cycle"],
)
def test_parse_schematics_rejects_corrupt(data: bytes) -> None:
    from scripts.infinity_fsd import parse_schematics

    with pytest.raises(ValueError):
        parse_schematics(data)
