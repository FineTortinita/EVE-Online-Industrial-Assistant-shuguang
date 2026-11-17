"""人物规划 `domain/character_planning.py`：技能门槛、自动派人、按槽位排程、按时长上限定并行。"""

from __future__ import annotations

import pytest

from domain.character_planning import (
    Character,
    Job,
    assign,
    missing_skills,
    parallels_by_time_limit,
    schedule,
)

pytestmark = pytest.mark.fast

M = "manufacturing"


def test_missing_skills():
    assert missing_skills({"工业理论": 1, "机械工程学": 4}, {"工业理论": 5, "机械工程学": 3}) == {"机械工程学": (4, 3)}
    assert missing_skills({}, {}) == {}


def test_assign_respects_skills_slots_and_prefers_faster():
    """技能不达标不派；槽位不够不派；都行时选时长最短的；已指定且可行的保持。"""
    alice = Character("甲", {"工业理论": 5}, {M: 2})
    bob = Character("乙", {"工业理论": 5, "机械工程学": 4}, {M: 3})
    jobs = [
        Job(1, M, 2, 10, required={"机械工程学": 4}),  # 只有乙会
        Job(2, M, 2, 5),  # 甲乙都行；乙已被 1 占 2 条只剩 1 → 只能甲
        Job(3, M, 1, 1, char="乙"),  # 指定乙且可行 → 保持（处理顺序：已指定的先）
    ]
    res = assign(jobs, [alice, bob], hours_for=lambda j, c: j.hours * (0.9 if c.name == "乙" else 1.0))
    assert res[3] == ("乙", "保持指定")
    assert res[1] == ("乙", "自动分配")
    assert res[2][0] == "甲"
    # 等子项的母项不占槽：槽位 2 的人物接两条 1 线子项 + 一条依赖它们的母项
    kids = [Job(21, M, 1, 5), Job(22, M, 1, 5), Job(20, M, 1, 1, depends_on=(21, 22))]
    assert {k: v[0] for k, v in assign(kids, [alice]).items()} == {21: "甲", 22: "甲", 20: "甲"}
    # 槽位都满但上限够 → 排队；上限也不够 → 没人接并给原因
    res2 = assign([Job(8, M, 2, 3), Job(9, M, 2, 1), Job(7, M, 5, 1)], [alice])
    assert res2[8] == ("甲", "自动分配") and res2[9][0] == "甲" and "排队" in res2[9][1]
    assert res2[7][0] == "" and "剩余槽" in res2[7][1]


def test_schedule_waits_for_children_and_slot_capacity():
    """子项先、母项后；同一人物同线型槽位 2：三条 1 线作业里第三条要等最早那条结束。"""
    c = Character("甲", {}, {M: 2})
    jobs = [
        Job(1, M, 1, 4),
        Job(2, M, 1, 6),
        Job(3, M, 1, 3),
        Job(10, M, 1, 2, depends_on=(1, 2, 3)),  # 母项
    ]
    slots = {s.job_id: s for s in schedule(jobs, [c], {j.job_id: "甲" for j in jobs})}
    assert (slots[2].start, slots[1].start) == (0.0, 0.0)  # 先放长的
    assert slots[3].start == 4.0  # 等 1 号让出槽位
    assert slots[10].start == max(slots[1].end, slots[2].end, slots[3].end) == 7.0
    # 回归（真实库实测超槽 11/10）：后排但开工更早的短作业不能挤进已满的时段
    jobs2 = [Job(1, M, 1, 10), Job(2, M, 1, 10), Job(3, M, 1, 5, depends_on=(9,)), Job(9, M, 1, 1), Job(4, M, 1, 2)]
    got = {s.job_id: s for s in schedule(jobs2, [c], {j.job_id: "甲" for j in jobs2})}
    for t in [x / 2 for x in range(0, 60)]:
        assert sum(1 for s in got.values() if s.start <= t < s.end) <= 2, t
    # 未分配人物的作业不受槽位限制
    free = {s.job_id: s for s in schedule(jobs, [c], {})}
    assert free[3].start == 0.0 and free[10].start == 6.0


@pytest.mark.parametrize(
    ("runs", "hpr", "limit", "lines", "expect"),
    [
        (10, 2.0, 5.0, 11, 5),  # 每线 2 流程 = 4h ≤ 5h
        (10, 2.0, 100.0, 11, 1),
        (10, 8.0, 5.0, 11, 10),  # 单流程就超限 → 最多 10（不超过流程数），不死循环
        (10, 2.0, 1.0, 3, 3),  # 受可用线数限制
    ],
)
def test_parallels_by_time_limit_terminates(runs, hpr, limit, lines, expect):
    assert parallels_by_time_limit(runs, hpr, limit, lines) == expect
