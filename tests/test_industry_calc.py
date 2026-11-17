"""`domain/industry_calc.py` 统一工业计算引擎 —— 分层合并取整（阶段 2）。

金样例取曙光 blueprint.db 实值与开明网「制造计算 · 乌鸦级」（2026-11-17）逐步骤流程数对照。
"""

from __future__ import annotations

import pytest

from domain.industry_calc import (
    ACTIVITY_REACTION,
    Target,
    calculate,
)

pytestmark = pytest.mark.fast


class _Reader:
    """{产物: (活动, 蓝图, 每流程产出, [(材料, 基础量)])}"""

    def __init__(self, recipes: dict[int, tuple[str, int, int, list[tuple[int, int]]]]):
        self._by_product = recipes
        self._by_bp = {bp: (act, mats) for _p, (act, bp, _o, mats) in recipes.items()}

    def product(self, type_id, activity="manufacturing"):
        r = self._by_product.get(type_id)
        return (r[1], r[2]) if r and r[0] == activity else None

    def materials(self, bp_id, activity="manufacturing"):
        act, mats = self._by_bp[bp_id]
        return [(m, q, 10) for m, q in mats] if act == activity else []


# 乌鸦级链（实值，只保留与流程数有关的那几条边；矿物等原料省略为 1 种）
RAVEN = 638
CTR, LSBU, AIPS = 57479, 57486, 57478  # 核心温度调节器 / 生命保障备份装置 / 自动修复储藏装置密封
RCF, PO = 57457, 57456  # 强化碳纤维 / 增压氧化剂（反应，200/轮）
CF, TP, CP, SA = 57453, 57455, 16659, 16661  # 碳纤维 / 热固性聚合物 / 碳聚合物 / 硫酸（反应，200/轮）
OOS = 57454  # 富氧有机溶剂（反应）
MOON = 1  # 卫星原料（买）
_RAVEN_BOM = {
    RAVEN: ("manufacturing", 688, 1, [(CTR, 1), (LSBU, 75), (AIPS, 150), (34, 5_200_000)]),
    CTR: ("manufacturing", 57516, 1, [(PO, 500), (RCF, 500)]),
    LSBU: ("manufacturing", 57523, 3, [(RCF, 10)]),
    AIPS: ("manufacturing", 57515, 3, [(RCF, 10)]),
    RCF: (ACTIVITY_REACTION, 57493, 200, [(CF, 200), (OOS, 1), (TP, 200)]),
    PO: (ACTIVITY_REACTION, 57492, 200, [(CP, 200), (SA, 200), (OOS, 1)]),
    CF: (ACTIVITY_REACTION, 900, 200, [(MOON, 100)]),
    TP: (ACTIVITY_REACTION, 901, 200, [(MOON, 100)]),
    CP: (ACTIVITY_REACTION, 902, 200, [(MOON, 100)]),
    SA: (ACTIVITY_REACTION, 903, 200, [(MOON, 100)]),
    OOS: (ACTIVITY_REACTION, 904, 10_000, [(MOON, 100)]),
}


def test_raven_chain_matches_kaiming_reference():
    """全造（含反应）、ME10 制造 / 反应无 ME：每一步流程数与开明网一致，
    富氧有机溶剂被浅层（强化碳纤维 / 增压氧化剂）和深层同时引用 → 推迟到最深层合并开工一次。"""
    res = calculate(
        _Reader(_RAVEN_BOM),
        [Target(RAVEN, quantity=1, me_level=10, key="raven")],
        build=lambda _t, _a: True,
        me_for=lambda *_: 10,
    )
    runs = {t: n.runs for t, n in res.nodes.items()}
    assert runs == {CTR: 1, LSBU: 23, AIPS: 45, RCF: 6, PO: 3, CF: 6, TP: 6, CP: 3, SA: 3, OOS: 1}
    assert res.nodes[RCF].demand == 1062 and res.nodes[RCF].leftover == 138
    assert res.nodes[PO].demand == 450 and res.nodes[PO].leftover == 150
    assert res.nodes[LSBU].leftover == 1
    assert res.nodes[OOS].demand == 9  # 6 + 3：两个上游合并后**一次**取整
    assert res.nodes[OOS].depth == 3 and res.nodes[RCF].depth == 2
    assert [len(w) for w in res.waves()] == [3, 2, 5]
    assert res.raws[34] == 4_680_000  # 5.2M × 0.9


def test_reactions_bought_by_default_and_shared_intermediate_merged():
    """默认策略（生产计划口径）：反应产物买入；两个成品共用的中间件合成一个节点、需求相加、roots 记全。"""
    bom = {
        **_RAVEN_BOM,
        2: ("manufacturing", 2000, 1, [(LSBU, 30)]),
    }
    res = calculate(
        _Reader(bom),
        [Target(RAVEN, runs=1, key="m1"), Target(2, runs=2, parallels=3, key="m2")],
    )
    assert set(res.nodes) == {CTR, LSBU, AIPS}
    lsbu = res.nodes[LSBU]
    # 乌鸦级 75 + 第二个成品 3 条线 × ceil(30×2) = 180 → 255；first_root 取 targets 中靠前的
    assert (lsbu.demand, lsbu.roots, lsbu.first_root) == (255, {"m1", "m2"}, "m1")
    assert lsbu.runs == 85
    assert RCF in res.raws and PO in res.raws  # 反应产物进采购


def test_cycle_edge_is_bought_and_depth_cap():
    bom = {
        10: ("manufacturing", 100, 1, [(11, 2)]),
        11: ("manufacturing", 101, 1, [(10, 1), (99, 5)]),  # 11 → 10 是回边
    }
    res = calculate(_Reader(bom), [Target(10, quantity=1, key="r")])
    assert set(res.nodes) == {11}
    assert res.raws == {10: 2, 99: 10}


def test_scheduler_parallels_and_saving_feed_child_demand():
    """排产给出 5 线 × 10 流程时，下级需求按 5 个独立作业各自整批取整再相加；设施倍率按 first_root 取。"""
    bom = {
        1: ("manufacturing", 100, 1, [(2, 1)]),
        2: ("manufacturing", 101, 1, [(3, 3)]),
    }
    res = calculate(
        _Reader(bom),
        [Target(1, runs=50, key="m")],
        scheduler=lambda node, demand: (5, -(-demand // 5)),
        saving=lambda _t, _a, root: 0.99 if root == "m" else 1.0,
    )
    assert (res.nodes[2].parallels, res.nodes[2].runs) == (5, 10)
    assert res.raws[3] == 5 * 30  # ceil(round(3×10×0.99, 2)) = 30（29.7）/ 线


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_target_used_by_another_target_is_built_regardless_of_order(order):
    """PR #2 review：成品 A 又是成品 B 的材料时，A 要按中间件展开，结果与成品先后无关。

    A = 10 原料；B = 2 A。目标 A×1、B×1 → 一共造 3 个 A，买 30 原料，不买 A。
    """
    raw, a, b = 1, 10, 20
    reader = _Reader({a: ("manufacturing", 110, 1, [(raw, 10)]), b: ("manufacturing", 120, 1, [(a, 2)])})
    ta, tb = Target(a, quantity=1, key="a"), Target(b, quantity=1, key="b")
    res = calculate(reader, [ta, tb] if order == "ab" else [tb, ta])
    assert res.raws == {raw: 30}
    assert a not in res.raws
    assert res.nodes[a].demand == 2
