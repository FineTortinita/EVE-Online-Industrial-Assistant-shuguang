"""制造计算（开明网式）—— 选成品、填数量 → 原料汇总 / 逐级分解 / 成本与利润 / 采购清单。

计算本体是 `domain.industry_calc.calculate`（与生产计划拆解同一个引擎）；本模块只做三件事：
1. 把用户在参数条上的选择（制造 / 反应设施、类别造买开关、逐项造买、ME）装配成引擎的回调；
2. 给引擎结果标价：原料市价、每个作业的 EIV / 指数 / 作业费（`domain.formulas.calc_job_cost_fees`）/ 时长；
3. 归类与汇总，交给界面直接显示（`ui_qml/bridge/industry_calc_bridge.py`）。

口径说明（与开明网对照，见 `docs/dev/industry-refactor-plan.md` 1.1）：
- 「自采 / 自种 / 自造」的类别按**现金口径**不计价（旁边仍给市价参考）；改成买入则按市价计；
- 边角料按 `min(自制分摊成本, 卖价)` 抵扣，不扣变现税费；
- 作业费 = EIV × (指数 × 建筑成本加成 + 设施税 + SCC 4%)，制造与反应同一公式；
- 步骤按合并需求估算，未按游戏单工单流程上限拆批。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from core.container import get_container
from domain.formulas import (
    FACILITY_TAX_NPC,
    calc_eiv,
    calc_job_cost_fees,
    calc_production_time,
    implant_time_multiplier,
)
from domain.industry_calc import ACTIVITY_MANUFACTURING, ACTIVITY_REACTION, Target, calculate
from services.blueprint_reader import SqliteBlueprintReader

# ═══════════════════════════════════════════════════════════
#  物品类别（原料汇总分组 + 类别造买开关）
# ═══════════════════════════════════════════════════════════

#: 类别 key → (显示名, 归属判定的 group_id 集合)。不在任何集合里的归「其他」。
#: group_id 取自曙光 reference.db 实测（2026-11-17）。
CATEGORY_GROUPS: dict[str, tuple[str, frozenset[int]]] = {
    "mineral": ("矿物", frozenset({18})),
    "ice": ("冰矿产物", frozenset({423})),
    "moon": ("卫星原料", frozenset({427})),
    "gas": ("气云", frozenset({711})),
    "reaction": ("反应材料", frozenset({428, 429, 974, 712, 4096, 4932})),
    "fuel": ("燃料块", frozenset({1136})),
    "pi_raw": ("行星原料 P0", frozenset({1032, 1033, 1035})),
    "pi_p1": ("行星产物 P1", frozenset({1042})),
    "pi_p2": ("行星产物 P2", frozenset({1034})),
    "pi_p3": ("行星产物 P3", frozenset({1040})),
    "pi_p4": ("行星产物 P4", frozenset({1041})),
    "salvage": ("打捞件", frozenset({754})),
    "component": ("组件", frozenset({334, 873, 913, 536})),
}
_OTHER = ("other", "其他")
_GROUP_TO_CATEGORY = {g: key for key, (_l, groups) in CATEGORY_GROUPS.items() for g in groups}

#: 类别造买开关（参数条四个总开关，与开明网同名同义）。值 True = 自己做（不计价 / 继续分解）。
TOGGLE_REACTION = "reaction"  # 反应材料：自己做（分解到卫星原料 + 气云）/ 买入
TOGGLE_MOON = "moon"  # 卫星原料与气云：自己采（不计价）/ 买入
TOGGLE_PI = "pi"  # 行星产物：自己种（不计价）/ 买入
TOGGLE_FUEL = "fuel"  # 燃料块：自己造 / 买入
DEFAULT_TOGGLES: dict[str, bool] = {TOGGLE_REACTION: True, TOGGLE_MOON: True, TOGGLE_PI: False, TOGGLE_FUEL: False}

#: 「自采不计价」的类别（受 TOGGLE_MOON / TOGGLE_PI 控制）
_FREE_BY_TOGGLE: dict[str, frozenset[str]] = {
    TOGGLE_MOON: frozenset({"moon", "gas"}),
    TOGGLE_PI: frozenset({"pi_raw", "pi_p1", "pi_p2", "pi_p3", "pi_p4"}),
}


# ═══════════════════════════════════════════════════════════
#  输入
# ═══════════════════════════════════════════════════════════


@dataclass
class Facility:
    """一处作业地点：星系（定指数）+ 设施倍率 + 设施税。由机库配置或手填得来。"""

    solar_system_id: int | None = None
    cost_index: float | None = None  # 手填指数（小数，0.0014 = 0.14%）；None = 按星系查
    mat_saving: float = 1.0
    time_mod: float = 1.0
    cost_mult: float = 1.0
    tax_pct: float = FACILITY_TAX_NPC * 100

    @classmethod
    def from_hangar(cls, hangar_id: int | None, activity: str) -> Facility:
        """按机库配置（建筑 / 改装件 / 安等 / 税 / 星系）构造；无机库 → NPC 默认。"""
        if not hangar_id:
            return cls()
        from services.hangar_industry_config import resolve_hangar_industry_config
        from services.inventory_manager import get_hangar_system_id

        cfg = resolve_hangar_industry_config(int(hangar_id), activity=activity)
        tax = cfg.get("facility_tax")
        return cls(
            solar_system_id=get_hangar_system_id(int(hangar_id)),
            mat_saving=float(cfg["structure_mat_saving"]),
            time_mod=float(cfg["structure_time_mod"]),
            cost_mult=float(cfg["structure_cost_mult"]),
            tax_pct=float(tax) if tax is not None else FACILITY_TAX_NPC * 100,
        )


@dataclass
class CalcRequest:
    targets: list[tuple[int, int]]  # [(成品 type_id, 数量)]
    me_level: int = 10
    te_level: int = 20
    manufacturing: Facility = field(default_factory=Facility)
    reaction: Facility = field(default_factory=Facility)
    toggles: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_TOGGLES))
    overrides: dict[int, bool] = field(default_factory=dict)  # {type_id: True=自己做 / False=买入}
    price_hub: str = "Sobaseki"
    price_type: str = "sell"
    sales_tax_pct: float = 3.6
    broker_pct: float = 1.5
    skills: dict = field(default_factory=dict)
    implants: list | None = None


# ═══════════════════════════════════════════════════════════
#  输出
# ═══════════════════════════════════════════════════════════


@dataclass
class RawLine:
    type_id: int
    name: str
    category: str
    qty: int
    unit_price: float
    free: bool  # 自采 / 自种：现金口径不计价
    buildable: bool  # 有配方（可改「自己做」）

    @property
    def cost(self) -> float:
        return 0.0 if self.free else self.qty * self.unit_price


@dataclass
class JobLine:
    type_id: int
    name: str
    activity: str
    depth: int
    runs: int
    parallels: int
    demand: int
    produced: int
    leftover: int
    eiv: float
    cost_index: float
    fee: float
    fee_detail: dict
    seconds: float  # 单条线总时长（runs 个流程）


@dataclass
class CalcResult:
    targets: list[dict]
    raws: list[RawLine]
    jobs: list[JobLine]
    waves: dict[str, list[list[JobLine]]]  # {"manufacturing": [[第1层], ...], "reaction": [...]}
    material_cost: float
    fee_total: float
    fee_manufacturing: float
    fee_reaction: float
    fee_parts: dict[str, float]  # system_cost / facility_tax / scc / alpha_tax 合计
    leftover_credit: float
    revenue_sell: float  # 成品按卖价（扣销售税 + 中介费）
    revenue_buy: float  # 成品按买价（扣销售税）
    longest_seconds: float
    total_line_seconds: float
    warnings: list[str] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return self.material_cost + self.fee_total

    @property
    def net_cost(self) -> float:
        return self.total_cost - self.leftover_credit


# ═══════════════════════════════════════════════════════════
#  计算
# ═══════════════════════════════════════════════════════════


def category_of(group_id: int | None) -> str:
    return _GROUP_TO_CATEGORY.get(int(group_id or 0), _OTHER[0])


def category_label(key: str) -> str:
    return CATEGORY_GROUPS[key][0] if key in CATEGORY_GROUPS else _OTHER[1]


def make_build_policy(
    toggles: dict[str, bool], overrides: dict[int, bool], category: Callable[[int], str]
) -> Callable[[int, str], bool]:
    """(type_id, 活动) → 是否自己做。逐项覆盖 > 类别开关 > 默认（制造件自己造）。

    - 反应产物：跟「反应材料」开关；
    - 燃料块（制造配方）：跟「燃料块」开关；
    - 其余制造件：自己造。
    """

    def build(type_id: int, activity: str) -> bool:
        if type_id in overrides:
            return bool(overrides[type_id])
        if activity == ACTIVITY_REACTION:
            return bool(toggles.get(TOGGLE_REACTION, True))
        if category(type_id) == "fuel":
            return bool(toggles.get(TOGGLE_FUEL, False))
        return True

    return build


def _is_free(category: str, toggles: dict[str, bool]) -> bool:
    return any(category in cats and toggles.get(key, False) for key, cats in _FREE_BY_TOGGLE.items())


def run(request: CalcRequest, *, db=None) -> CalcResult:
    """执行一次制造计算。`db` 不给用容器里的库。"""
    import services.scoring_service as ss

    db = db or get_container().db
    with db.connect("ref", "bp", "mkt") as conn:
        meta: dict[int, tuple[str, int | None, float]] = {}

        def item_meta(tid: int) -> tuple[str, int | None, float]:
            if tid not in meta:
                r = conn.execute(
                    "SELECT zh_name, en_name, group_id, volume FROM item WHERE type_id=?", (tid,)
                ).fetchone()
                meta[tid] = (
                    (r[0] or r[1] or str(tid)) if r else str(tid),
                    r[2] if r else None,
                    float(r[3] or 0) if r else 0.0,
                )
            return meta[tid]

        def cat(tid: int) -> str:
            return category_of(item_meta(tid)[1])

        reader = SqliteBlueprintReader(conn)
        build = make_build_policy(request.toggles, request.overrides, cat)
        fac = {ACTIVITY_MANUFACTURING: request.manufacturing, ACTIVITY_REACTION: request.reaction}
        targets = [
            Target(int(tid), quantity=max(int(q), 0), me_level=int(request.me_level), key=i)
            for i, (tid, q) in enumerate(request.targets)
            if tid and q
        ]
        result = calculate(
            reader,
            targets,
            build=build,
            saving=lambda _t, act, _r: fac[act].mat_saving,
            me_for=lambda *_: int(request.me_level),
        )

        price_cache: dict[tuple[int, str], float] = {}

        def price(tid: int, ptype: str | None = None) -> float:
            key = (tid, ptype or request.price_type)
            if key not in price_cache:
                price_cache[key] = float(ss.get_price(tid, key[1], request.price_hub, _db=db) or 0.0)
            return price_cache[key]

        index_cache: dict[tuple[str, int | None], float] = {}

        def cost_index(act: str) -> float:
            f = fac[act]
            if f.cost_index is not None:
                return float(f.cost_index)
            key = (act, f.solar_system_id)
            if key not in index_cache:
                index_cache[key] = float(
                    ss.get_system_cost_index(f.solar_system_id, act, _db=db, hub=request.price_hub)
                )
            return index_cache[key]

        def eiv_of(bp_id: int, act: str) -> float:
            mats = reader.materials(bp_id, act)
            pairs = []
            for m, q, *_ in mats:
                adj = ss.get_adjusted_price(int(m), _db=db)
                pairs.append((int(q), float(adj if adj is not None else price(int(m), "sell"))))
            return calc_eiv(pairs)

        skills = request.skills or {}
        imp_mult = implant_time_multiplier(_implant_bonuses(request.implants))

        def job_seconds(bp_id: int, act: str, runs: int) -> float:
            row = conn.execute(
                "SELECT time FROM blueprint_activities WHERE blueprint_type_id=? AND activity=?", (bp_id, act)
            ).fetchone()
            base = float(row[0] or 0) if row else 0.0
            if act == ACTIVITY_REACTION:
                from core.skill_keys import REACTIONS
                from domain.formulas import REACTIONS_SKILL_MULT

                per = base * (1 - REACTIONS_SKILL_MULT * int(skills.get(REACTIONS, 0) or 0)) * fac[act].time_mod
            else:
                from core.skill_keys import ADVANCED_INDUSTRY, INDUSTRY

                per = calc_production_time(
                    int(base),
                    industry_skill=int(skills.get(INDUSTRY, 0) or 0),
                    adv_industry_skill=int(skills.get(ADVANCED_INDUSTRY, 0) or 0),
                    te_level=int(request.te_level),
                    structure_time_mod=fac[act].time_mod,
                    implant_time_mult=imp_mult,
                )
            return per * runs

        def job_line(tid, act, bp_id, depth, runs, par, demand, produced, leftover) -> JobLine:
            idx = cost_index(act)
            eiv = eiv_of(bp_id, act) * runs * par
            f = fac[act]
            fee = calc_job_cost_fees(eiv, idx, structure_mult=f.cost_mult, facility_tax=f.tax_pct / 100)
            return JobLine(
                tid, item_meta(tid)[0], act, depth, runs, par, demand, produced, leftover,
                eiv, idx, fee["total_fee"], fee, job_seconds(bp_id, act, runs),
            )  # fmt: skip

        jobs: list[JobLine] = []
        target_rows: list[dict] = []
        warnings: list[str] = []
        for rr in result.roots:
            tid = rr.target.type_id
            name = item_meta(tid)[0]
            if rr.blueprint_type_id is None:
                warnings.append(f"{name} 没有制造 / 反应配方")
                continue
            produced = rr.runs * rr.output_qty
            jobs.append(
                job_line(tid, rr.activity, rr.blueprint_type_id, 0, rr.runs, rr.parallels, rr.target.quantity, produced,
                         produced - rr.target.quantity)
            )  # fmt: skip
            target_rows.append(
                {
                    "type_id": tid,
                    "name": name,
                    "activity": rr.activity,
                    "quantity": rr.target.quantity,
                    "runs": rr.runs,
                    "sell": price(tid, "sell"),
                    "buy": price(tid, "buy"),
                }
            )
        for n in sorted(result.nodes.values(), key=lambda n: (n.depth, n.type_id)):
            jobs.append(
                job_line(n.type_id, n.activity, n.blueprint_type_id, n.depth, n.runs, n.parallels, n.demand,
                         n.produced, n.leftover)
            )  # fmt: skip

        raws: list[RawLine] = []
        for tid, qty in sorted(result.raws.items()):
            name, _gid, _vol = item_meta(tid)
            c = cat(tid)
            buildable = any(reader.product(tid, a) for a in (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION))
            raws.append(RawLine(tid, name, c, int(qty), price(tid), _is_free(c, request.toggles), bool(buildable)))
        price_after = {j.type_id: price(j.type_id, "sell") for j in jobs if j.depth > 0 and j.leftover > 0}
        missing = [r.name for r in raws if not r.free and r.unit_price <= 0]
        if missing:
            warnings.append("无价格：" + "、".join(missing[:5]) + ("…" if len(missing) > 5 else ""))

    material_cost = sum(r.cost for r in raws)
    fee_total = sum(j.fee for j in jobs)
    parts = {
        k: sum(float(j.fee_detail.get(k, 0)) for j in jobs) for k in ("system_cost", "facility_tax", "scc", "alpha_tax")
    }
    # 边角料抵扣：每个中间件的「自制单价」= (本作业费 + 投入的成本) ÷ 产出件数，由深到浅逐层算
    # （投入里的原料按市价、自采不计价；投入里的中间件取它自己的自制单价）。
    # 抵扣额 = 边角料件数 × min(自制单价, 卖价) —— 与开明网同口径，未扣变现税费。
    raw_unit = {r.type_id: (0.0 if r.free else r.unit_price) for r in raws}
    job_by_type = {j.type_id: j for j in jobs if j.depth > 0}
    unit_make: dict[int, float] = {}
    for j in sorted(job_by_type.values(), key=lambda j: -j.depth):
        node = result.nodes[j.type_id]
        inputs = sum(q * unit_make.get(t, raw_unit.get(t, 0.0)) for t, q in node.inputs.items())
        unit_make[j.type_id] = (j.fee + inputs) / j.produced if j.produced else 0.0
    leftover_credit = sum(
        j.leftover * min(unit_make[j.type_id], price_after.get(j.type_id, 0.0))
        for j in job_by_type.values()
        if j.leftover > 0
    )
    sales = request.sales_tax_pct / 100
    broker = request.broker_pct / 100
    revenue_sell = sum(t["sell"] * t["quantity"] for t in target_rows) * (1 - sales - broker)
    revenue_buy = sum(t["buy"] * t["quantity"] for t in target_rows) * (1 - sales)
    waves: dict[str, list[list[JobLine]]] = {}
    for act in (ACTIVITY_MANUFACTURING, ACTIVITY_REACTION):
        acts = [j for j in jobs if j.activity == act]
        if acts:
            depths = sorted({j.depth for j in acts})
            waves[act] = [[j for j in acts if j.depth == d] for d in depths]
    return CalcResult(
        targets=target_rows,
        raws=raws,
        jobs=jobs,
        waves=waves,
        material_cost=material_cost,
        fee_total=fee_total,
        fee_manufacturing=sum(j.fee for j in jobs if j.activity == ACTIVITY_MANUFACTURING),
        fee_reaction=sum(j.fee for j in jobs if j.activity == ACTIVITY_REACTION),
        fee_parts=parts,
        leftover_credit=leftover_credit,
        revenue_sell=revenue_sell,
        revenue_buy=revenue_buy,
        longest_seconds=max((j.seconds for j in jobs), default=0.0),
        total_line_seconds=sum(j.seconds * j.parallels for j in jobs),
        warnings=warnings,
    )


def _implant_bonuses(implants: Iterable | None) -> list[float]:
    if not implants:
        return []
    from services.implant_loader import manufacturing_time_bonuses

    return manufacturing_time_bonuses(list(implants))


def multibuy_text(raws: Iterable[RawLine], *, include_free: bool = False) -> str:
    """游戏市场「批量购买」粘贴格式：每行「物品名<Tab>数量」。"""
    return "\n".join(f"{r.name}\t{r.qty}" for r in raws if r.qty > 0 and (include_free or not r.free))


def fmt_duration(seconds: float) -> str:
    """秒 → 「3天 17小时」/「14小时 24分」/「12分」。"""
    s = int(math.ceil(max(0.0, seconds)))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}天 {h}小时"
    if h:
        return f"{h}小时 {m}分"
    return f"{m}分"
