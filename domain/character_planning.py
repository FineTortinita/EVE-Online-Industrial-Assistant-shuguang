"""人物规划（纯函数，无 DB / Qt）—— 重构阶段 5，借鉴 T2 商人助手（见 `docs/dev/industry-refactor-plan.md` 1.3-C）。

三件事：
1. **技能门槛**：蓝图该活动要求的技能（SDE `blueprint_skills`）人物是否都达到；
2. **自动派人**：贪心 —— 先试已指定的人物，再在「技能达标且该线型剩余槽位够」的人物里选**时长最短**的（同时长取剩余槽多的）；
3. **按人物槽位排程**：每个人物每种线型有槽位上限，作业按依赖（子项先、母项后）与槽位排开始时间，
   得出「人物 × 时间」的泳道。T2 的甘特图**不**受槽位约束，这是我们补上的部分。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field


def missing_skills(required: Mapping[str, int], skills: Mapping[str, int]) -> dict[str, tuple[int, int]]:
    """未达标的技能 → `{技能名: (要求等级, 当前等级)}`；空 dict = 全部达标。"""
    out: dict[str, tuple[int, int]] = {}
    for name, level in required.items():
        have = int(skills.get(name, 0) or 0)
        if have < int(level):
            out[name] = (int(level), have)
    return out


@dataclass(frozen=True)
class Job:
    """一条待排的作业（一条计划行）。`lines` = 并行线数；`hours` = 单线时长。"""

    job_id: int
    line_type: str  # manufacturing / research / reaction
    lines: int
    hours: float
    depends_on: tuple[int, ...] = ()  # 必须先完成的作业（子项）
    char: str = ""  # 已指定人物（空 = 待分配）
    required: Mapping[str, int] = field(default_factory=dict)  # 该活动要求的技能
    started: bool = False  # 已在游戏里开工：钉在 0 时刻先占槽，待排作业只能排在它之后


@dataclass(frozen=True)
class Character:
    name: str
    skills: Mapping[str, int]
    slots: Mapping[str, int]  # {线型: 上限}
    busy: Mapping[str, int] = field(default_factory=dict)  # {线型: 已被在产作业占用的线数}


def assign(
    jobs: Iterable[Job],
    chars: Iterable[Character],
    *,
    hours_for: Callable[[Job, Character], float] | None = None,
) -> dict[int, tuple[str, str]]:
    """贪心派人 → `{job_id: (人物名, 说明)}`；人物名为空 = 没人能接（说明里给原因）。

    处理顺序：已指定人物的先保留，再长作业先（大石头先放）。槽位按「同时占用」近似：
    一个人物某线型的已派线数不超过上限（与 T2「技能达标且空闲槽位够」同口径；真实时间轴上的错峰交给 `schedule`）；
    **有前置作业（等子项）的不占槽**（T2 同口径）。
    `hours_for(job, char)` 给出该人物做这条作业的单线时长（技能 / 植入体不同，时长不同）；不给按 `job.hours`。
    """
    roster = list(chars)
    used: dict[tuple[str, str], int] = {}
    for c in roster:
        for lt, n in c.busy.items():
            used[(c.name, lt)] = int(n)
    out: dict[int, tuple[str, str]] = {}

    def free(c: Character, lt: str) -> int:
        return int(c.slots.get(lt, 0)) - used.get((c.name, lt), 0)

    for job in sorted(jobs, key=lambda j: (not j.char, bool(j.depends_on), -j.hours * j.lines, j.job_id)):
        candidates = []
        reasons = []
        for c in roster:
            gap = missing_skills(job.required, c.skills)
            if gap:
                reasons.append(f"{c.name} 缺技能 " + "、".join(f"{k} {v[0]}（现 {v[1]}）" for k, v in gap.items()))
                continue
            # 等子项的作业开工时子项的线已腾出：只要求上限够；其余要求「现在」剩余槽够
            room = int(c.slots.get(job.line_type, 0)) if job.depends_on else free(c, job.line_type)
            if room < job.lines:
                reasons.append(f"{c.name} 剩余槽 {max(room, 0)} < {job.lines}")
                continue
            hours = hours_for(job, c) if hours_for else job.hours
            candidates.append((job.char != c.name, hours, -free(c, job.line_type), c.name, c))
        if not candidates:
            # 槽位都满时退一步：技能达标、上限够的人物里选**排队**（时间线上等前面的线腾出来再开，
            # 由 `schedule` 排开始时间）—— 比「没人接」有用；仍是技能不够 / 上限不够才真的没人接。
            queued = [
                (hours_for(job, c) if hours_for else job.hours, c.name)
                for c in roster
                if not missing_skills(job.required, c.skills) and int(c.slots.get(job.line_type, 0)) >= job.lines
            ]
            if queued:
                out[job.job_id] = (min(queued)[1], "排队（槽位已满，等前面的线腾出）")
            else:
                out[job.job_id] = ("", "；".join(reasons) or "没有人物")
            continue
        _pref, _h, _f, name, chosen = min(candidates)
        # 「等待子项」不占槽（T2 口径）：有前置作业的母项要等子项全部完成才开工，那时子项的线已经腾出来了。
        if not job.depends_on:
            used[(name, job.line_type)] = used.get((name, job.line_type), 0) + job.lines
        note = "保持指定" if name == job.char else ("改派" if job.char else "自动分配")
        out[job.job_id] = (name, note)
    return out


@dataclass
class Slot:
    job_id: int
    char: str
    line_type: str
    start: float
    end: float


def schedule(
    jobs: Iterable[Job],
    chars: Iterable[Character],
    assignment: Mapping[int, str],
) -> list[Slot]:
    """按人物槽位排程 → 每条作业的 (人物, 开始, 结束)（小时，相对现在）。

    规则：作业要等它依赖的作业全部结束；同一人物同一线型同时运行的线数不超过槽位上限
    （一条作业的 `lines` 条并行线一起开、一起结束）。按「最早可开工」贪心逐个放入（列表调度），
    未分配人物的作业不受槽位限制（只受依赖），放在空串人物下。
    """
    jobs = list(jobs)
    caps = {(c.name, lt): int(n) for c in chars for lt, n in c.slots.items()}
    by_id = {j.job_id: j for j in jobs}
    end_at: dict[int, float] = {}
    # 每个 (人物, 线型) 已排的区间 [(开始, 结束, 线数)] —— 不能用「按当前开工时刻弹出已结束」的堆：
    # 后排的作业可能开工更早（依赖短），弹出会把仍在占线的区间丢掉，导致超槽（真实库实测 11/10）。
    running: dict[tuple[str, str], list[tuple[float, float, int]]] = {}
    pending = {j.job_id for j in jobs}
    out: list[Slot] = []

    def deps_done(j: Job) -> float | None:
        ends = []
        for d in j.depends_on:
            if d not in by_id:
                continue
            if d not in end_at:
                return None
            ends.append(end_at[d])
        return max(ends, default=0.0)

    guard = 0
    while pending and guard <= len(jobs) * 4:
        guard += 1
        ready: list[tuple[float, int]] = []
        for i in pending:
            t = deps_done(by_id[i])
            if t is not None:
                ready.append((t, i))
        if not ready:  # 依赖成环：剩下的一律从 0 开
            ready = [(0.0, i) for i in pending]
        # 已开工的作业先占槽（PR #2 review：否则长的待排作业会被排到在产作业前面），
        # 其余先排「能最早开工」的，其次长作业
        ready.sort(key=lambda x: (not by_id[x[1]].started, x[0], -by_id[x[1]].hours, x[1]))
        t0, jid = ready[0]
        job = by_id[jid]
        char = assignment.get(jid, "") or ""
        key = (char, job.line_type)
        start = t0
        if char and key in caps:
            booked = running.setdefault(key, [])
            if not job.started:  # 在产作业已经在跑，不挪
                start = _earliest_fit(booked, t0, job.hours, job.lines, max(caps[key], job.lines))
            booked.append((start, start + job.hours, job.lines))
        end = start + job.hours
        end_at[jid] = end
        out.append(Slot(jid, char, job.line_type, start, end))
        pending.discard(jid)
    return out


def _earliest_fit(booked: list[tuple[float, float, int]], t0: float, hours: float, lines: int, cap: int) -> float:
    """≥ t0 的最早开工时刻，使 [开工, 开工 + hours) 内任一时刻的占用线数 + lines ≤ cap。"""

    def peak(s: float, e: float) -> int:
        points = [s] + [b0 for b0, _b1, _n in booked if s < b0 < e]
        return max(sum(n for b0, b1, n in booked if b0 <= p < b1) for p in points)

    candidates = sorted({t0} | {b1 for _b0, b1, _n in booked if b1 > t0})
    for s in candidates:
        if hours <= 0 or peak(s, s + hours) + lines <= cap:
            return s
    return candidates[-1]


def parallels_by_time_limit(runs_needed: int, hours_per_run: float, limit_hours: float, max_lines: int) -> int:
    """「按子线时长上限」定并行数：最少的并行使单线时长 ≤ 上限，**有终止**（T2 实现在单流程就超限时死循环）。

    并行数不超过所需流程数（一条线至少一个流程）与可用线数 `max_lines`；单流程本身超限时
    返回能用的最大并行（再加线也缩不短单流程）。
    """
    runs_needed = max(int(runs_needed), 0)
    if runs_needed == 0:
        return 1
    cap = max(1, min(runs_needed, int(max_lines) if max_lines > 0 else runs_needed))
    if hours_per_run <= 0 or limit_hours <= 0:
        return 1
    for p in range(1, cap + 1):
        per_line_runs = -(-runs_needed // p)
        if per_line_runs * hours_per_run <= limit_hours:
            return p
    return cap
