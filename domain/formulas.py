"""
制造计算器 — 所有制造相关公式的唯一存放地。

纯函数，无状态，无 DB 依赖，可直接用 pytest 测试。

公式来源:
    - 客户端 SDE 技能描述（第一方文案，`data/typeIDs.yaml`）：技能系数以它为准
    - EVE University Wiki: https://wiki.eveuniversity.org/Manufacturing
    - fuzzwork industry.py（生产环境参考实现）
    - Viridian 税改后公式
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any

# ═══════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════

DEFAULT_WASTEFACTOR = 10  # T1 兜底

SCC_SURCHARGE = 0.04  # 固定 4%，Viridian 引入
FACILITY_TAX_NPC = 0.0025  # NPC 空间站设施税率 0.25%
#: Alpha 克隆额外税（Omega=0）；`domain/scoring` 的制造安装费与这里共用同一常量。
ALPHA_TAX = 0.0025

#: 结构/改装件的材料与时间倍率唯一来源是 `services.hangar_industry_config.STRUCTURE_BASE`
#: （值来自 SDE 属性 2600/2601/2602）。这里不再保留 Athanor/Tatara 等历史死常量：
#: 它们零引用，且 Athanor/Tatara 在 SDE 里没有 `strEngTimeBonus` 属性（属精炼结构）。
INDUSTRY_SKILL_MULT = 0.04  # 工业理论 (3380) 每级 -4% 时间
ADV_INDUSTRY_SKILL_MULT = 0.03  # 高级工业理论 (3388) 每级 -3% 时间
TE_MULT_PER_LEVEL = 0.01  # TE 每级 -1% 时间（TE 存 0-20 的百分比，故等价于游戏里每级 2%×10 级）
# 科研/反应系数取自客户端 SDE 技能描述（第一方文案）：
#   3403 研究概论「每升一级，蓝图时间效率研究的速度提升 5%」
#   3409 冶金学「每升一级，材料效率研究的速度提升 5%」
#   3402 科学原理「每升一级，蓝图复制的速度提升 5%」
#   45746 反应理论「每升一级，反应时间减少 4%」
RESEARCH_SKILL_MULT = 0.05
METALLURGY_SKILL_MULT = 0.05
SCIENCE_SKILL_MULT = 0.05  # 科学原理（仅复制）
REACTIONS_SKILL_MULT = 0.04  # 反应理论（仅反应）
# 蓝图所需技能：11452 机械工程学「每升一级，所有需要机械工程学技能的物品的生产时间减少 1%」
BP_SKILL_TIME_MULT = 0.01
#: 「蓝图技能每级 −1% 生产时间」只对**技能自身 dogma 带属性 `1982 manufactureTimePerLevel`
#: 的技能**生效。3380 工业理论 / 3388 高级工业理论 的减免已由 `INDUSTRY_SKILL_MULT` /
#: `ADV_INDUSTRY_SKILL_MULT` 计过，不能再按「蓝图所需技能」二次计入。
#: 来源：SDE `data/sde.zip → fsd/typeDogma.yaml` 全表扫描（2026-07 快照）——24 个技能
#: 值 `-1.0`、81896「突变稳定」`-2.0`，共 25 个。3380/3388/22242 均**不在**表中。
BP_TIME_SKILL_TYPE_IDS: frozenset[int] = frozenset(
    {
        3395,  # 高级小型舰船建造研究
        3396,  # 高级工业舰船建造研究
        3397,  # 高级中型舰船建造研究
        3398,  # 高级大型舰船建造研究
        11433,  # 高能物理学
        11441,  # 等离子物理学
        11442,  # 纳米工程学
        11443,  # 磁流体物理学
        11444,  # 艾玛星舰工程学
        11445,  # 米玛塔尔星舰工程学
        11446,  # 引力子物理学
        11447,  # 激光物理学
        11448,  # 电磁物理学
        11449,  # 火箭科学
        11450,  # 盖伦特星舰工程学
        11451,  # 核芯物理学
        11452,  # 机械工程学
        11453,  # 电子工程学
        11454,  # 加达里星舰工程学
        11455,  # 量子物理学
        11529,  # 分子工程学
        52307,  # 三神裔量子工程学
        77725,  # 高级旗舰建造研究
        81050,  # 昇威星舰工程学
        81896,  # 突变稳定（每级 −2%）
    }
)


#: 结构改装件按所在星系安等放大加成（SDE 属性 2355 hiSecModifier / 2356 lowSecModifier /
#: 2357 nullSecModifier；曙光 ESI 2026-11-17 实测制造 / 科研改装件为 1.0 / 1.9 / 2.1）。
#: 反应改装件（组 1933-1939）只有 00/虫洞 1.1、高低安 1.0 —— 由调用方按改装件自身数值传入。
RIG_SECURITY_MULT_MANUFACTURING: dict[str, float] = {"high": 1.0, "low": 1.9, "null": 2.1}


def security_band(security: float | None) -> str:
    """星系安等 → 改装件系数档位：≥0.45 高安、>0.0 低安、其余（含虫洞负值/未知）00。

    `None`（机库没设星系）按高安处理 —— 系数 1.0 等于不放大，与旧行为一致。
    游戏取「四舍五入到 1 位小数后的显示安等」：0.45 显示 0.5 属高安，0.04 显示 0.0 属 00。
    """
    if security is None:
        return "high"
    shown = round(float(security), 1)
    if shown >= 0.5:
        return "high"
    if shown > 0.0:
        return "low"
    return "null"


def rig_bonus_multiplier(bonus_pct: float, security_mult: float = 1.0) -> float:
    """单个改装件加成（百分比，负数为减免）→ 乘数 `1 + bonus × 安等系数 / 100`。"""
    return 1.0 + float(bonus_pct) * float(security_mult) / 100.0


#: 植入体制造时间加成（SDE 属性 440 manufacturingTimeBonus，百分比，-4 = −4%）。
IMPLANT_MANUFACTURING_TIME_ATTR = 440


def implant_time_multiplier(bonus_pcts: Sequence[float]) -> float:
    """植入体时间加成（百分比列表，负数为减免）→ 乘数 Π(1 + b/100)。空 → 1.0。"""
    mult = 1.0
    for b in bonus_pcts:
        mult *= 1.0 + float(b) / 100.0
    return mult


# ═══════════════════════════════════════════════════════════
#  材料浪费
# ═══════════════════════════════════════════════════════════


def _game_ceil(x: float) -> int:
    """游戏口径取整：先 round 到 2 位小数再向上取整（`ceil(round(x, 2))`）。

    多个乘数（ME × 建筑 × 改装件）叠加后的浮点尾差（如 100.00000000001）会先被 round 吃掉；
    而真实的小数尾数（100.004）也按游戏规则抹平到 100 —— 旧口径 `ceil(x − 1e-10)` 给 101，
    每作业多要 1 个（BL-052-4）。来源：EVE University Industry 页的官方材料公式
    `max(runs, ceil(round(base × runs × ME × 建筑 × 改装件, 2)))`。
    """
    return math.ceil(round(x, 2))


def calc_material_per_run(
    db_qty: int,
    wastefactor: int = 10,
    me_level: int = 0,
    structure_mat_saving: float = 1.0,
) -> int:
    """计算每轮次制造所需材料数量。

    公式: max(1, ceil(round(db_qty × (100 - ME) / 100 × 结构减免, 2)))
    ME 每级减 1%，最高 10 级（ME 10 = 基础量的 90%）。
    wastefactor 参数保留仅做兼容，不再参与计算。
    """
    if db_qty <= 0:
        return 0
    if me_level < 0:
        me_level = 0
    me_level = min(me_level, 10)
    result = db_qty * (100.0 - me_level) / 100.0 * structure_mat_saving
    return max(1, _game_ceil(result))


#: 单件材料（每轮基础量 ≤ 1）豁免材料效率。
#: 官方口径（CCP《Material Efficiency Research》）：「Whole and single items, as in
#: "1 unit needed per production run", will not be affected by this calculation.
#: … building 10 Paladins will always require 10 Apocalypse, independent of any
#: Material Efficiency bonus.」——即按 `基础量 × 作业数` 计，不减 ME。
_SINGLE_UNIT_EXEMPT = True


def calc_material_for_runs(
    db_qty: int,
    wastefactor: int = 10,
    me_level: int = 0,
    runs: int = 1,
    structure_mat_saving: float = 1.0,
) -> int:
    """计算多轮次制造所需材料总量。

    材料效率适用于整个项目总量（不逐轮次取整），游戏口径:
    max(runs, ceil(round(db_qty × runs × (100 - ME) / 100 × 结构减免, 2)))

    单件材料（基础量 ≤ 1）按 `基础量 × 作业数` 计，不减 ME（见 `_SINGLE_UNIT_EXEMPT`）——
    这正是游戏公式里 `max(runs, …)` 的效果。

    **本函数是「一批要多少料」的单一定义处**：豁免以前只写在 `material_total_for_runs` 里，
    而 BOM 展开、计划分解、采购聚合、`domain/industry_calc.py` 都直接调本函数 —— 同一个蓝图会因为
    走不同代码路径而要出不同的料（`calc_material_for_runs(1, 10, 10, 10)` 曾算出 9，
    官方口径是 10，差的就是 T2 组件那类单件料）。
    """
    if db_qty <= 0:
        return 0
    n = max(1, runs)
    if db_qty <= 1 and _SINGLE_UNIT_EXEMPT:
        return db_qty * n
    me_level = min(max(me_level, 0), 10)
    total = db_qty * n * (100.0 - me_level) / 100.0 * structure_mat_saving
    return max(n, _game_ceil(total))


def material_total_for_runs(
    material: Mapping[str, Any] | Any,
    total_runs: int,
    *,
    me_level: int = 0,
    structure_mat_saving: float = 1.0,
) -> int:
    """单条材料明细 → 整批（`total_runs` 次作业）需求量（明细形态的薄封装）。

    `services/scoring_service.py` 的两处缩放、`services/plan_execution.material_requirements`
    与 `ui_qml/bridge/cost_breakdown_bridge._build_material_rows` 走它；
    取整与豁免口径的单一定义处在 `calc_material_for_runs`（本函数只做字段提取）。

    口径（EVE）：对**整批**取一次整 —— `ceil(基础量 × 作业数 × (100-ME)/100 × 结构减免)`。
    逐轮取整后再乘作业数（`ceil(基础量 × …) × 作业数`）会**系统性地多要货**：
    基础量 22、ME10、2510 次作业时，逐轮口径要 50,200，整批口径只要 49,698。

    `material` 可以是 dict（取 `base_qty` / `wastefactor`）或带同名属性的对象。
    """
    base = int(_field(material, "base_qty") or 0)
    if base <= 0:
        return 0
    return calc_material_for_runs(
        base,
        int(_field(material, "wastefactor") or DEFAULT_WASTEFACTOR),
        me_level,
        runs=max(1, int(total_runs)),
        structure_mat_saving=structure_mat_saving,
    )


def _field(material: Mapping[str, Any] | Any, key: str) -> Any:
    """dict / 对象两吃 —— 材料明细在评分链路里两种形态都有。"""
    if isinstance(material, Mapping):
        return material.get(key)
    return getattr(material, key, None)


# ═══════════════════════════════════════════════════════════
#  安装费（Job Cost）
# ═══════════════════════════════════════════════════════════


def calc_eiv(
    materials: list[tuple[int, float]],
) -> float:
    """计算 Estimated Item Value (EIV)。

    公式: Σ(material_quantity × adjusted_price)，ME0 无浪费状态

    参数:
        materials: [(material_qty, adjusted_price), ...]
                   material_qty = 蓝图基础数量（不含浪费）
                   adjusted_price = ESI adjusted price（或兜底 sell_price）

    返回:
        EIV 总和
    """
    return sum(qty * price for qty, price in materials)


def calc_job_cost_fees(
    eiv: float,
    sci: float,
    structure_mult: float = 1.0,
    facility_tax: float = FACILITY_TAX_NPC,
    scc: float = SCC_SURCHARGE,
    alpha_tax: float = 0.0,
) -> dict[str, float]:
    """计算制造安装费（加法结构）。

    公式: job_cost = EIV × (SCI × SB + FT + SCC + AT)

    参数:
        eiv: Estimated Item Value（所有材料 adjusted_price × 基础数量）
        sci: 系统成本指数（System Cost Index）
        structure_mult: 设施加成系数（NPC ≈1.1, Upwell <1=折扣）
        facility_tax: 设施税率（NPC 0.25% = 0.0025）
        scc: SCC surcharge（固定 4% = 0.04，Viridian 引入）
        alpha_tax: Alpha 账号额外税（0.25% = 0.0025, Omega=0）

    返回:
        {
            "system_cost": float,    # SCI × SB 部分
            "facility_tax": float,   # FT 部分
            "scc": float,            # SCC 部分（固定 4%）
            "alpha_tax": float,      # Alpha 税（如有）
            "total_fee": float,      # 安装费总和
        }
    """
    system_cost = eiv * sci * structure_mult
    facility = eiv * facility_tax
    surcharge = eiv * scc
    alpha = eiv * alpha_tax if alpha_tax > 0 else 0.0

    return {
        "system_cost": round(system_cost, 2),
        "facility_tax": round(facility, 2),
        "scc": round(surcharge, 2),
        "alpha_tax": round(alpha, 2),
        "total_fee": round(system_cost + facility + surcharge + alpha, 2),
    }


# ═══════════════════════════════════════════════════════════
#  生产时间
# ═══════════════════════════════════════════════════════════


def calc_production_time(
    base_time: int,
    industry_skill: int = 0,
    adv_industry_skill: int = 0,
    te_level: int = 0,
    structure_time_mod: float = 1.0,
    required_skill_levels: Sequence[int] | None = None,
    implant_time_mult: float = 1.0,
) -> float:
    """计算实际制造时间（秒）。

    公式:
        bp_mod = Π(1 - 0.01 × 蓝图所需技能等级)      ← 客户端文案：机械工程学等每级 -1%
        skill_mod = (1 - 0.04 × industry) × (1 - 0.03 × adv_industry)
        actual_time = base_time × skill_mod × bp_mod × implant × (1 - 0.01 × TE) × structure_time_mod

    参数:
        base_time: 蓝图基础制造时间（秒）
        industry_skill: 工业理论等级（0-5）
        adv_industry_skill: 高级工业理论等级（0-5）
        te_level: TE 研究等级（0-20）
        structure_time_mod: 工程站时间减免（NPC=1.0, Raitaru=0.85 等）
        required_skill_levels: 该蓝图**制造活动所需且带 `1982 manufactureTimePerLevel`**
            的技能角色等级列表（如机械工程学 L5；过滤后由调用方传入，白名单见
            `BP_TIME_SKILL_TYPE_IDS`）。SDE 技能描述原文：「每升一级，所有需要机械工程学
            技能的物品的生产时间减少 1%」。None/空 → 不计该项（保持旧调用方行为）。
        implant_time_mult: 植入体制造时间乘数（`implant_time_multiplier`，如 BX-804 → 0.96）。

    返回:
        实际时间（秒）
    """
    skill_mod = (1.0 - INDUSTRY_SKILL_MULT * max(0, industry_skill)) * (
        1.0 - ADV_INDUSTRY_SKILL_MULT * max(0, adv_industry_skill)
    )
    bp_mod = 1.0
    for lvl in required_skill_levels or ():
        bp_mod *= 1.0 - BP_SKILL_TIME_MULT * min(max(0, int(lvl)), 5)
    te_mod = 1.0 - TE_MULT_PER_LEVEL * max(0, te_level)
    return base_time * skill_mod * bp_mod * implant_time_mult * te_mod * structure_time_mod
