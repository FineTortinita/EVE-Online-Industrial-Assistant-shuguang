"""备料占用分配（纯函数，无 DB / Qt）—— T2 商人助手式「预定」，见 `docs/dev/industry-refactor-plan.md` 1.3-B / 阶段 4。

占用**不落库**：每次刷新按当前计划与库存重算（T2 同款设计，避免占用表与计划表不同步）。

规则：
- 只有勾了「备料」的待生产计划参与（没勾 = 不预定，把料让给真正要造的）；
- 分配顺序：组号升序（组号即优先级）→ 组内子级降序（子项先拿，母项后拿）→ 计划 id；
- 每个计划只从**自己的材料机库**取库存；
- 单个计划每种材料按以下来源依次取，剩下的是缺口：
  ① 本组在产子项将产出 → ② 本组子项已完成的冻结产出 → ③ 机库闲置库存 → ④ 本组计划中子项将产出。
  ①②④ 是「给本组母项留的」，按组记账；③ 是共享池，先到先得。

例（T2 FAQ）：组 1 要 30 个 A、组 2 要 20 个 A、库存 15 → 组 1 占 15 缺 15、组 2 缺 20，合计缺 35（而不是 20）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: 子项产出池的键：单个组号，或共享该产出的组号元组（升序）。
GroupKey = int | tuple[int, ...]

SOURCES = ("child_running", "frozen", "stock", "child_planned")


@dataclass(frozen=True)
class PlanNeed:
    """一个参与分配的计划：它要哪些材料、从哪个机库取。"""

    plan_id: int
    group: int
    sub_level: int
    hangar_id: int | None
    needs: Mapping[int, int]  # {type_id: 需求量}


@dataclass
class Allocation:
    """一个计划一种材料的分配结果。"""

    need: int
    taken: dict[str, int] = field(default_factory=dict)  # {来源: 数量}

    @property
    def covered(self) -> int:
        return sum(self.taken.values())

    @property
    def short(self) -> int:
        return max(0, self.need - self.covered)

    @property
    def now(self) -> int:
        """现在就到位的量（库存 + 冻结产出），不含子项将产出。"""
        return self.taken.get("stock", 0) + self.taken.get("frozen", 0)

    @property
    def state(self) -> str:
        """`ready` 现有够 / `coming` 加上子项将产出才够 / `short` 仍缺。填料总表三色用。"""
        if self.now >= self.need:
            return "ready"
        if self.short <= 0:
            return "coming"
        return "short"


def allocate(
    plans: Iterable[PlanNeed],
    stock: Mapping[int | None, Mapping[int, int]],
    *,
    child_running: Mapping[GroupKey, Mapping[int, int]] | None = None,
    frozen: Mapping[GroupKey, Mapping[int, int]] | None = None,
    child_planned: Mapping[GroupKey, Mapping[int, int]] | None = None,
) -> dict[int, dict[int, Allocation]]:
    """按规则分配 → `{plan_id: {type_id: Allocation}}`。

    `stock` 按机库：`{hangar_id: {type_id: 数量}}`；
    `child_running` / `frozen` / `child_planned` 按组：`{group: {type_id: 数量}}`（子项产出只给本组）。
    组键也可以是**组号元组**：被多个组的母项共同引用的子项产出只有一份，放在一个共享池里，
    按「组号升序」先到先得 —— 同一份产出不会在两个组里各算一次。
    输入映射不会被修改。
    """
    pools: dict[str, dict] = {
        "child_running": {g: dict(m) for g, m in (child_running or {}).items()},
        "frozen": {g: dict(m) for g, m in (frozen or {}).items()},
        "stock": {h: dict(m) for h, m in stock.items()},
        "child_planned": {g: dict(m) for g, m in (child_planned or {}).items()},
    }
    ordered = sorted(plans, key=lambda p: (p.group, -p.sub_level, p.plan_id))
    out: dict[int, dict[int, Allocation]] = {}
    for p in ordered:
        mine = out.setdefault(p.plan_id, {})
        for tid, need in p.needs.items():
            alloc = mine.setdefault(int(tid), Allocation(need=0))
            alloc.need += int(need)
            left = alloc.need - alloc.covered
            for src in SOURCES:
                if left <= 0:
                    break
                if src == "stock":
                    candidates = [pools[src].get(p.hangar_id)]
                elif not p.group:
                    continue  # 没有组号的独立计划没有子项可依赖
                else:
                    # 先本组独占池，再本组参与的共享池
                    candidates = [pools[src].get(p.group)] + [
                        pool for key, pool in pools[src].items() if isinstance(key, tuple) and p.group in key
                    ]
                for pool in candidates:
                    if left <= 0:
                        break
                    if not pool:
                        continue
                    have = int(pool.get(int(tid), 0))
                    take = min(have, left)
                    if take > 0:
                        pool[int(tid)] = have - take
                        alloc.taken[src] = alloc.taken.get(src, 0) + take
                        left -= take
    return out


def summarize(
    allocations: Mapping[int, Mapping[int, Allocation]], hangar_of: Mapping[int, int | None]
) -> dict[int | None, dict[int, Allocation]]:
    """按材料机库汇总（填料总表「按设施分组」）→ `{hangar_id: {type_id: 合计 Allocation}}`。"""
    out: dict[int | None, dict[int, Allocation]] = {}
    for pid, per in allocations.items():
        bucket = out.setdefault(hangar_of.get(pid), {})
        for tid, a in per.items():
            agg = bucket.setdefault(tid, Allocation(need=0))
            agg.need += a.need
            for src, q in a.taken.items():
                agg.taken[src] = agg.taken.get(src, 0) + q
    return out
