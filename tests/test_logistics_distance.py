"""物流距离计算测试 —— 星门图 BFS 与贸易中心跳数

`get_distance_jumps` 自 2026-09-20 起改为走 `reference.db.stargate` 的**真实星门图**
（原先是一张硬编码表）。曙光分支的 reference.db 由曙光快照导入，期望值见 `HUB_JUMPS` 注释。

**必须用真实数据库**：本文件的全部意义就是「BFS 在真图上给出的跳数与游戏一致」，
桩上比对测不出星门图那个反直觉的 join（`destination_system_id` 存的是星门 id 而非星系 id）。
`database/` 是 gitignored、CI 上不生成 —— 缺库时整模块 skip。
"""

from pathlib import Path

import pytest

from core.constants import TRADE_HUB_SYSTEM_IDS
from core.paths import reference_db_path
from services.logistics import (
    TRADE_HUB_DISTANCES,
    compute_jumps,
    get_distance_jumps,
    list_trade_hub_distances,
)

pytestmark = pytest.mark.fast

if not Path(reference_db_path()).is_file():
    pytest.skip("需要真实 database/reference.db（星门图）；CI 上不生成这些库", allow_module_level=True)

_JITA = TRADE_HUB_SYSTEM_IDS["Jita"]
_AMARR = TRADE_HUB_SYSTEM_IDS["Amarr"]

# ── 参数化数据集 ──

#: `(起点, 终点, 最短, 高安)` —— **曙光**星门图（曙光 ESI 逐个 `/universe/stargates/{id}/` 导出，
#: 快照 build 3684550，10,837 个星门）上 BFS 推出的值，**未经游戏内核对**（2026-11-16）。
#: 曙光 ESI 的 `/route/` 对任意星系都返回 404，没法拿官方路线接口交叉验证。
#: 与国际服差异很大（国际服 Jita↔Amarr 是 11/45）：曙光的 Dodixie 有 8 个星门，比国际服多出直连
#: Sobaseki、Penirgman 的两条，把 The Forge / Domain / Sinq Laison 之间的高安路线大幅缩短。
HUB_JUMPS = [
    ("Jita", "Amarr", 11, 11),
    ("Jita", "Dodixie", 8, 8),
    ("Jita", "Rens", 7, 7),
    ("Jita", "Hek", 6, 6),
    ("Amarr", "Dodixie", 3, 3),
    ("Amarr", "Rens", 7, 7),
    ("Amarr", "Hek", 15, 16),
    ("Dodixie", "Rens", 6, 6),
    ("Dodixie", "Hek", 12, 13),
    ("Rens", "Hek", 11, 13),
]

UNKNOWN_PAIRS = [
    ("Jita", "Unknown"),
    ("Unknown", "Amarr"),
    ("Unknown", "Unknown"),
    ("Foo", "Bar"),
]


def _pair(hub_a: str, hub_b: str) -> tuple[int, int]:
    return TRADE_HUB_SYSTEM_IDS[hub_a], TRADE_HUB_SYSTEM_IDS[hub_b]


class TestComputeJumps:
    """星门图 BFS —— 两种口径 + 边界"""

    @pytest.mark.parametrize("hub_a, hub_b, shortest, highsec", HUB_JUMPS)
    def test_hub_pairs_match_game(self, hub_a, hub_b, shortest, highsec):
        src, dst = _pair(hub_a, hub_b)
        assert compute_jumps(src, dst, "shortest") == shortest
        assert compute_jumps(src, dst, "highsec") == highsec

    @pytest.mark.parametrize("hub_a, hub_b, shortest, highsec", HUB_JUMPS)
    def test_symmetric(self, hub_a, hub_b, shortest, highsec):
        """跳数必须对称 —— 星门图是无向图"""
        src, dst = _pair(hub_a, hub_b)
        assert compute_jumps(dst, src, "shortest") == shortest
        assert compute_jumps(dst, src, "highsec") == highsec

    def test_same_system_is_zero(self):
        assert compute_jumps(_JITA, _JITA) == 0

    @pytest.mark.parametrize("missing", [0, None, 999999999])
    def test_missing_system_returns_none(self, missing):
        assert compute_jumps(_JITA, missing) is None  # type: ignore[arg-type]
        assert compute_jumps(missing, _JITA) is None  # type: ignore[arg-type]

    def test_unreachable_returns_none(self):
        """虫洞星系不在星门图上 —— J 空间（31xxxxxx）跳不到"""
        assert compute_jumps(_JITA, 31000001, "shortest") is None

    def test_highsec_never_shorter_than_shortest(self):
        """限高安是加约束，结果不可能更短"""
        for hub_a, hub_b, shortest, highsec in HUB_JUMPS:
            src, dst = _pair(hub_a, hub_b)
            assert highsec >= shortest

    def test_custom_mode_respects_floor(self):
        """自定义安全下限：下限越高越绕，0.45 等价于 highsec"""
        src, dst = _pair("Jita", "Amarr")
        assert compute_jumps(src, dst, "custom", 0.45) == compute_jumps(src, dst, "highsec")
        assert compute_jumps(src, dst, "custom", 0.0) == compute_jumps(src, dst, "shortest")


class TestGetDistanceJumps:
    """贸易中心名 → 跳数（按高安路线，与跑货实际走法一致）"""

    @pytest.mark.parametrize("hub_a, hub_b, _shortest, highsec", HUB_JUMPS)
    def test_known_distances(self, hub_a, hub_b, _shortest, highsec):
        assert get_distance_jumps(hub_a, hub_b) == highsec

    @pytest.mark.parametrize("hub_a, hub_b", [("Jita", "Amarr"), ("Dodixie", "Rens"), ("Hek", "Jita")])
    def test_symmetry(self, hub_a, hub_b):
        assert get_distance_jumps(hub_a, hub_b) == get_distance_jumps(hub_b, hub_a)

    def test_same_hub_is_zero(self):
        assert get_distance_jumps("Jita", "Jita") == 0

    @pytest.mark.parametrize("hub_a, hub_b", UNKNOWN_PAIRS)
    def test_unknown_pair_returns_none(self, hub_a, hub_b):
        assert get_distance_jumps(hub_a, hub_b) is None


class TestFallbackTable:
    """内置距离表只在星门图不可用时兜底 —— 它本身的数值是错的，但必须完整且对称"""

    def test_all_pairs_symmetric(self):
        for (a, b), d in list(TRADE_HUB_DISTANCES.items()):
            assert TRADE_HUB_DISTANCES.get((b, a)) == d

    def test_covers_every_hub_pair(self):
        """6 个中心（含索巴色基）两两 = 15 对，双向齐全"""
        hubs = list(TRADE_HUB_SYSTEM_IDS)
        for a in hubs:
            for b in hubs:
                if a != b:
                    assert (a, b) in TRADE_HUB_DISTANCES


class TestListTradeHubDistances:
    def test_returns_unique_pairs(self):
        result = list_trade_hub_distances()
        assert len(result) == 15  # C(6,2) = 15

    def test_values_match_get_distance_jumps(self):
        """两条路径不能给出不同数字（原先前者读硬编码表、后者走星门图）"""
        for entry in list_trade_hub_distances():
            assert entry["jumps"] == get_distance_jumps(entry["from"], entry["to"])
            assert entry["jumps"] > 0
