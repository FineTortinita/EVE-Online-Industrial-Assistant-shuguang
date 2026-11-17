"""工业计算引擎 — 多成品 BOM 的「分层合并取整」（纯函数，无 DB / Qt / 缓存）。

取代此前三套各算各的展开器（已删除的 `domain/bom.py` DFS、`services/bom_expander._expand`、
`services/plan_rebuild.compute_child_forest` 的迭代传播），见 BL-052 / `docs/dev/industry-refactor-plan.md` 阶段 2。

算法（开明网「逐级分解」与 T2 商人助手需求总账同一思想，见计划文档 1.3-A）：

1. **发现**：从全部成品出发找出所有「自己造」的中间件，建 BOM 有向图；环上的回边按「买」切断。
2. **分层**：每个中间件的层级 = 从任一成品出发的**最长路径**。某件在浅分支、深分支都要用时，
   推迟到最深那层统一开工 —— 这样处理到它时所有上游需求已经收齐，**一次**取整即可。
3. **逐层合并取整**：由浅到深，每个中间件把所有上游（所有成品、所有分支）的需求合在一起 →
   交给排产回调定 (并行线数, 每线流程) → 按「每条线一个作业、作业内整批取整」展开下级需求。
   整流程多出来的量记为边角料。

数量口径全部走 `domain.formulas.calc_material_for_runs`（游戏取整 + 单件豁免）。
成本 / 时间不在这里算 —— 本模块只回答「造什么、造几轮、买多少」。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from domain.formulas import calc_material_for_runs

ACTIVITY_MANUFACTURING = "manufacturing"
ACTIVITY_REACTION = "reaction"
#: 按这个顺序找配方：同一产物既有制造又有反应配方时取制造（曙光数据里不存在这种物品，只是定个确定性）。
RECIPE_ACTIVITIES: tuple[str, ...] = (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION)


class BlueprintReader(Protocol):
    """蓝图数据访问协议 —— 由 services 层适配实现（`services.blueprint_reader.SqliteBlueprintReader`）。"""

    def product(self, product_type_id: int, activity: str = ACTIVITY_MANUFACTURING) -> tuple[int, int] | None:
        """返回 (blueprint_type_id, 每流程产出)；该活动无配方返回 None。"""
        ...

    def materials(self, blueprint_type_id: int, activity: str = ACTIVITY_MANUFACTURING) -> list[tuple[int, int, int]]:
        """返回 [(material_type_id, 每流程基础量, wastefactor)]（wastefactor 已不参与计算，保留兼容）。"""
        ...


@dataclass(frozen=True)
class Target:
    """一个成品需求。

    两种给法：
    - `quantity`：要多少个成品，流程数由排产回调决定（制造计算页、查询页）；
    - `runs` (+ `parallels`)：流程已定（生产计划的母项），只展开它的材料。
    `key` 是调用方的标识（计划 id 等），会出现在下游中间件的 `roots` 里。
    `activity` 不给时按 `RECIPE_ACTIVITIES` 顺序找配方。
    """

    type_id: int
    quantity: int = 0
    runs: int | None = None
    parallels: int = 1
    me_level: int = 0
    key: Hashable = None
    activity: str | None = None


@dataclass
class Node:
    """一个「自己造」的中间件（合并后的全部需求）。"""

    type_id: int
    activity: str
    blueprint_type_id: int
    output_qty: int
    depth: int = 0  # 最长路径层级（成品的直接材料 = 1）
    demand: int = 0  # 毛需求（全部上游之和，未扣库存）
    parallels: int = 1
    runs: int = 0  # **每条线**的流程数
    me_level: int = 0
    parents: set[int] = field(default_factory=set)  # 直接上游的 type_id（成品或中间件）
    roots: set[Hashable] = field(default_factory=set)  # 引用到它的成品 key
    first_root: Hashable = None  # 引用它的成品里 targets 顺序最靠前的那个（决定机库 / 组号归属）
    inputs: dict[int, int] = field(default_factory=dict)  # 本节点按定稿流程消耗的各材料量

    @property
    def produced(self) -> int:
        return self.parallels * self.runs * self.output_qty

    @property
    def leftover(self) -> int:
        """整流程多出来的量（边角料）；扣库存的排产（见 `plan_net_runs`）会让它带上被库存顶掉的部分，按 0 夹取。"""
        return max(0, self.produced - self.demand)


@dataclass
class RootResult:
    """一个成品的展开结果。"""

    target: Target
    activity: str | None
    blueprint_type_id: int | None
    output_qty: int
    parallels: int
    runs: int
    inputs: dict[int, int] = field(default_factory=dict)


@dataclass
class BuildResult:
    roots: list[RootResult]
    nodes: dict[int, Node]  # 只含「自己造」且有需求的中间件
    raws: dict[int, int]  # 要买的东西（原料 + 选择买入的中间件 + 环 / 深度封顶处）

    def waves(self) -> list[list[Node]]:
        """按层级分组（第 1 层 = 成品的直接中间件），层内按 type_id 排序 —— 「逐级分解」视图用。"""
        if not self.nodes:
            return []
        top = max(n.depth for n in self.nodes.values())
        return [
            sorted((n for n in self.nodes.values() if n.depth == d), key=lambda n: n.type_id) for d in range(1, top + 1)
        ]


#: 是否自己造：(type_id, 活动, 从哪个父件来) → bool。返回 False 的件进 `raws`。
BuildPolicy = Callable[[int, str], bool]
#: 排产：(节点, 合并后的毛需求) → (并行线数, 每线流程)。
Scheduler = Callable[[Node, int], tuple[int, int]]
#: 材料倍率（建筑 × 改装件 × 安等）：(作业的产物 type_id, 活动, 该作业的 first_root) → 倍率。
SavingFn = Callable[[int, str, Hashable], float]
#: 中间件用的 ME：(type_id, blueprint_type_id, 活动) → ME。
MeFn = Callable[[int, int, str], int]


def build_manufacturing_only(_type_id: int, activity: str) -> bool:
    """默认买 / 造策略：制造件自己造，反应产物买入（生产计划拆解的现行口径）。"""
    return activity == ACTIVITY_MANUFACTURING


def default_scheduler(node: Node, demand: int) -> tuple[int, int]:
    """不扣库存、单线：流程 = ceil(需求 / 每流程产出)。"""
    return 1, (math.ceil(demand / max(node.output_qty, 1)) if demand > 0 else 0)


def job_material_qty(base_qty: int, me_level: int, runs: int, parallels: int, saving: float = 1.0) -> int:
    """`parallels` 条并行线、每条 `runs` 流程的总材料量：每条线是一个独立作业，作业内整批取整。

    反应没有 ME（调用方传 0），单件豁免与游戏取整由 `calc_material_for_runs` 负责。
    """
    if runs <= 0 or parallels <= 0:
        return 0
    return calc_material_for_runs(base_qty, 10, me_level, runs, saving) * parallels


def calculate(
    reader: BlueprintReader,
    targets: Iterable[Target],
    *,
    build: BuildPolicy = build_manufacturing_only,
    scheduler: Scheduler = default_scheduler,
    saving: SavingFn | None = None,
    me_for: MeFn | None = None,
    max_depth: int = 20,
) -> BuildResult:
    """展开全部成品 → `BuildResult`（中间件节点 + 原料清单 + 每个成品的流程）。

    `build` 决定中间件造还是买；`scheduler` 决定中间件的并行与流程（生产计划传扣库存的
    `plan_net_runs` 版本）；`saving` 给出作业所在设施的材料倍率；`me_for` 给出中间件蓝图的 ME
    （不给按 0）。深度超过 `max_depth` 的中间件按买入处理（防呆）。
    """
    targets = list(targets)
    order = {t.key: i for i, t in reversed(list(enumerate(targets)))}  # 同 key 取最靠前的位置
    recipe_cache: dict[int, tuple[str, int, int] | None] = {}
    mats_cache: dict[tuple[int, str], list[tuple[int, int]]] = {}

    def recipe(type_id: int, activity: str | None = None) -> tuple[str, int, int] | None:
        """(活动, 蓝图, 每流程产出)；无配方 → None。"""
        if activity is not None:
            got = reader.product(type_id, activity)
            return (activity, int(got[0]), max(int(got[1] or 1), 1)) if got else None
        if type_id not in recipe_cache:
            recipe_cache[type_id] = None
            for act in RECIPE_ACTIVITIES:
                got = reader.product(type_id, act)
                if got:
                    recipe_cache[type_id] = (act, int(got[0]), max(int(got[1] or 1), 1))
                    break
        return recipe_cache[type_id]

    def materials(bp_id: int, activity: str) -> list[tuple[int, int]]:
        key = (bp_id, activity)
        if key not in mats_cache:
            mats_cache[key] = [(int(m), int(q)) for m, q, *_ in reader.materials(bp_id, activity) if int(q) > 0]
        return mats_cache[key]

    def is_built(type_id: int) -> tuple[str, int, int] | None:
        r = recipe(type_id)
        return r if r is not None and build(type_id, r[0]) else None

    # ── 1. 发现：自己造的中间件 + 有向边（父 → [(子, 每流程基础量)]），回边切断 ──
    edges: dict[int, list[tuple[int, int]]] = {}
    cut: set[tuple[int, int]] = set()
    state: dict[int, int] = {}  # 0/缺省 未访问、1 在栈上、2 完成

    def visit(type_id: int) -> None:
        r = is_built(type_id)
        if r is None:
            return
        state[type_id] = 1
        mats = materials(r[1], r[0])
        edges[type_id] = mats
        for child, _q in mats:
            if state.get(child) == 1:
                cut.add((type_id, child))  # 环：这条边上的子件按买入
            elif state.get(child) is None:
                visit(child)
        state[type_id] = 2

    root_recipes: list[tuple[str, int, int] | None] = []
    for t in targets:
        r = recipe(t.type_id, t.activity)
        root_recipes.append(r)
        if r is None:
            continue
        # 成品在展开自己的子树期间也「在栈上」：子件回指成品的边（环）被切断、按买入，
        # 成品不会反过来变成自己的中间件。
        prev = state.get(t.type_id)
        state[t.type_id] = 1
        for child, _q in materials(r[1], r[0]):
            if state.get(child) is None:
                visit(child)
        state[t.type_id] = 2 if prev is None else prev

    # ── 2. 分层：最长路径（拓扑序上松弛） ──
    depth: dict[int, int] = {}
    indeg: dict[int, int] = dict.fromkeys(edges, 0)
    for parent, mats in edges.items():
        for child, _q in mats:
            if child in edges and (parent, child) not in cut:
                indeg[child] += 1
    for r in root_recipes:
        if r is None:
            continue
        for child, _q in materials(r[1], r[0]):
            if child in edges:
                depth[child] = max(depth.get(child, 0), 1)
    ready = [t for t, d in indeg.items() if d == 0]
    while ready:
        cur = ready.pop()
        for child, _q in edges[cur]:
            if child not in edges or (cur, child) in cut:
                continue
            depth[child] = max(depth.get(child, 0), depth.get(cur, 0) + 1)
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)

    # ── 3. 逐层合并取整 ──
    nodes: dict[int, Node] = {}
    raws: dict[int, int] = {}

    def node_for(type_id: int) -> Node:
        n = nodes.get(type_id)
        if n is None:
            act, bp_id, out = is_built(type_id)  # type: ignore[misc]  # 只对 edges 里的件调用
            me = me_for(type_id, bp_id, act) if (me_for and act == ACTIVITY_MANUFACTURING) else 0
            n = nodes[type_id] = Node(type_id, act, bp_id, out, depth=depth.get(type_id, 1), me_level=me)
        return n

    def push(parent_type: int, parent_roots: set[Hashable], first_root: Hashable, child: int, qty: int) -> None:
        if qty <= 0:
            return
        if child in edges and (parent_type, child) not in cut and depth.get(child, 1) <= max_depth:
            n = node_for(child)
            n.demand += qty
            n.parents.add(parent_type)
            n.roots |= parent_roots
            if n.first_root is None or order.get(first_root, 1 << 30) < order.get(n.first_root, 1 << 30):
                n.first_root = first_root
        else:
            raws[child] = raws.get(child, 0) + qty

    def expand(type_id: int, activity: str, bp_id: int, me: int, runs: int, par: int, roots, first) -> dict[int, int]:
        mult = saving(type_id, activity, first) if saving else 1.0
        me_eff = me if activity == ACTIVITY_MANUFACTURING else 0
        used: dict[int, int] = {}
        for child, base in materials(bp_id, activity):
            q = job_material_qty(base, me_eff, runs, par, mult)
            used[child] = used.get(child, 0) + q
            push(type_id, roots, first, child, q)
        return used

    root_results: list[RootResult] = []
    for t, r in zip(targets, root_recipes, strict=True):
        if r is None:
            root_results.append(RootResult(t, None, None, 1, 0, 0))
            continue
        act, bp_id, out = r
        if t.runs is not None:
            par, runs = max(int(t.parallels or 1), 1), max(int(t.runs), 0)
        else:
            par, runs = 1, math.ceil(max(int(t.quantity), 0) / out)
        rr = RootResult(t, act, bp_id, out, par, runs)
        if runs > 0:
            rr.inputs = expand(t.type_id, act, bp_id, int(t.me_level), runs, par, {t.key}, t.key)
        root_results.append(rr)

    for type_id in sorted(edges, key=lambda x: (depth.get(x, 1), x)):
        n = nodes.get(type_id)
        if n is None or n.demand <= 0:
            continue
        n.parallels, n.runs = scheduler(n, n.demand)
        if n.runs > 0:
            n.inputs = expand(
                type_id, n.activity, n.blueprint_type_id, n.me_level, n.runs, n.parallels, n.roots, n.first_root
            )

    return BuildResult(roots=root_results, nodes={t: n for t, n in nodes.items() if n.demand > 0}, raws=raws)
