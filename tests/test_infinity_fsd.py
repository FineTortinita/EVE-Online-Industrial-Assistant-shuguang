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
