"""备料占用分配 `domain/reservation.py`（T2 商人助手式预定）。"""

from __future__ import annotations

import pytest

from domain.reservation import PlanNeed, allocate, summarize

pytestmark = pytest.mark.fast

A, B = 100, 200


def _p(pid, group, level=0, hangar=1, **needs):
    return PlanNeed(pid, group, level, hangar, {int(k[1:]): v for k, v in needs.items()})


def test_t2_faq_example_lower_group_reserves_first():
    """组 1 要 30、组 2 要 20、库存 15 → 组 1 占 15 缺 15，组 2 缺 20：合计缺 35（不是 20）。"""
    res = allocate([_p(2, 2, t100=20), _p(1, 1, t100=30)], {1: {A: 15}})
    assert (res[1][A].taken, res[1][A].short) == ({"stock": 15}, 15)
    assert (res[2][A].covered, res[2][A].short) == (0, 20)
    assert res[1][A].short + res[2][A].short == 35


@pytest.mark.parametrize(
    ("kw", "expect_taken", "state"),
    [
        # 冻结产出先于库存（库存留给别人）
        ({"frozen": {1: {A: 6}}}, {"frozen": 6, "stock": 4}, "ready"),
        # 在产子项先于冻结与库存
        ({"child_running": {1: {A: 10}}, "frozen": {1: {A: 6}}}, {"child_running": 10}, "coming"),
        # 库存不够、计划中子项补齐 → 绿（将由子项补）
        ({"child_planned": {1: {A: 5}}}, {"stock": 8, "child_planned": 2}, "coming"),
        # 子项产出只给本组
        ({"frozen": {9: {A: 6}}}, {"stock": 8}, "short"),
    ],
)
def test_source_order_and_states(kw, expect_taken, state):
    res = allocate([_p(1, 1, t100=10)], {1: {A: 8}}, **kw)
    assert res[1][A].taken == expect_taken
    assert res[1][A].state == state


def test_children_before_mother_within_group_and_hangar_isolation():
    """同组子级降序（子项先拿）；只从自己的材料机库取。"""
    res = allocate(
        [_p(1, 1, 0, t100=5), _p(2, 1, 2, t100=5), _p(3, 1, 1, hangar=2, t100=5)],
        {1: {A: 6}, 2: {A: 1}},
    )
    assert res[2][A].taken == {"stock": 5}  # 子级 2 先
    assert res[1][A].taken == {"stock": 1}  # 母项后
    assert res[3][A].taken == {"stock": 1}  # 机库 2 只有 1


def test_summarize_by_hangar():
    res = allocate([_p(1, 1, t100=10, t200=1), _p(2, 2, t100=4)], {1: {A: 12, B: 5}})
    agg = summarize(res, {1: 1, 2: 1})[1]
    assert (agg[A].need, agg[A].covered, agg[A].state) == (14, 12, "short")
    assert agg[B].state == "ready"
