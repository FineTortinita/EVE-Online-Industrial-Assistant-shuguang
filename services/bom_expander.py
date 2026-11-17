"""
BOM 递归展开 — 支持 T2/T3 产业链的完整材料树

从目标成品 type_id 开始，递归查找蓝图 → 材料 → 子蓝图 → 子材料，
构建完整的制造材料树。叶子节点为可直接购买的原材料。

用法:
    from services.bom_expander import expand_bom

    result = expand_bom(type_id=30013, quantity=10, bp_me=10)
    print(result["full_cost"])          # 总材料成本
    print(result["raw_materials"])      # 叶子节点列表
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.container import get_container


def _default_db():
    """惰性获取 DatabaseManager（经容器）。"""
    return get_container().db


def _default_pricing():
    """惰性获取 PricingService（经容器）。"""
    return get_container().pricing_service


# ════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════


@dataclass
class BomNode:
    """BOM 树节点 — 描述一个材料/中间产品的层级信息"""

    type_id: int
    name: str
    quantity: float  # 所需数量（已含 ME 浪费因子 × 产量倍数调整）
    base_quantity: int  # 蓝图中的原始数量
    is_intermediate: bool  # 是否是中间产品（可自己制造）
    children: list[BomNode] = field(default_factory=list)
    depth: int = 0
    unit_price: float = 0.0
    subtotal: float = 0.0
    blueprint_type_id: int | None = None


# ════════════════════════════════════════════════════
#  内部辅助函数
# ════════════════════════════════════════════════════


def _resolve_name(c, type_id: int) -> str:
    """解析物品名称 — 委托给 name_resolver"""
    from services.name_resolver import resolve_item_name

    return resolve_item_name(c, type_id)


def _find_blueprint_for_product(conn, product_type_id: int, activity: str = "manufacturing"):
    """查找产出指定物品的蓝图 → (bp_id, output_qty, base_time)。

    走统一入口 `blueprint_reader.get_blueprint_products`：同一产物可能挂多张蓝图
    （实测制造 4 个），而且 SDE 里有两张 CCP **测试蓝图**（碳化钨 16672 挂
    45732「Test Reaction Blueprint」20/轮、以及 26843）—— 手写 `... LIMIT 1`
    无排序会命中测试蓝图，与 `market_chain_service` / 评分链路查到的蓝图不一致。
    连接带 `ref` 时才会排除测试蓝图（不带就退化成按 type_id 取，见该函数说明）。
    """
    from services.blueprint_reader import get_blueprint_products

    return get_blueprint_products(conn, product_type_id, activity)  # (bp_id, qty, time) or None


def _get_materials(conn, bp_id: int, activity: str = "manufacturing"):
    """获取蓝图材料列表 → [(material_type_id, quantity), ...]"""
    rows = conn.execute(
        """
        SELECT material_type_id, quantity
        FROM blueprint_materials
        WHERE blueprint_type_id = ? AND activity = ?
        """,
        (bp_id, activity),
    ).fetchall()
    return rows


# ════════════════════════════════════════════════════
#  公开 API（计算走统一引擎 `domain.industry_calc`）
# ════════════════════════════════════════════════════


def _build_tree(
    conn, result, type_id: int, quantity: float, price_hub: str, price_type: str, max_depth: int
) -> BomNode:
    """引擎结果（合并后的 DAG）→ 展示用的树。

    中间件在 DAG 里只有一个节点（全部上游需求合并取整一次）；树里它可能挂在多个父项下，
    每处的数量按「该父项实际消耗 ÷ 合并总需求」比例分摊其子材料 —— 各处子树数量之和等于合并后的真值。
    """
    pricing = _default_pricing()
    price_cache: dict[int, float] = {}
    name_cache: dict[int, str] = {}

    def price(tid: int) -> float:
        if tid not in price_cache:
            price_cache[tid] = float(pricing.get_price(tid, price_type, price_hub) or 0.0)
        return price_cache[tid]

    def name(tid: int) -> str:
        if tid not in name_cache:
            name_cache[tid] = _resolve_name(conn, tid)
        return name_cache[tid]

    def make(tid: int, qty: float, depth: int, inputs: dict[int, int] | None, scale: float, bp_id, out_qty) -> BomNode:
        children: list[BomNode] = []
        if inputs and depth < max_depth + 1:
            for child, child_qty in inputs.items():
                q = child_qty * scale
                node = result.nodes.get(child)
                if node is not None:
                    children.append(
                        make(child, q, depth + 1, node.inputs, q / node.demand, node.blueprint_type_id, node.output_qty)
                    )
                else:
                    children.append(make(child, q, depth + 1, None, 0.0, None, 0))
        if children:
            subtotal = sum(c.subtotal for c in children)
        else:
            subtotal = price(tid) * qty
        return BomNode(
            type_id=tid,
            name=name(tid),
            quantity=round(qty, 2),
            base_quantity=int(out_qty or 0),
            is_intermediate=bool(children),
            children=children,
            depth=depth,
            unit_price=price(tid),
            subtotal=round(subtotal, 2),
            blueprint_type_id=bp_id,
        )

    root = result.roots[0]
    if root.blueprint_type_id is None or not root.inputs:
        return make(type_id, float(quantity), 0, None, 0.0, root.blueprint_type_id, root.output_qty)
    return make(type_id, float(quantity), 0, root.inputs, 1.0, root.blueprint_type_id, root.output_qty)


def expand_bom(
    type_id: int,
    quantity: int = 1,
    bp_me: int = 0,
    price_hub: str = "Jita",
    price_type: str = "sell",
    max_depth: int = 5,
    char_config: dict | None = None,
) -> dict:
    """
    展开 BOM，返回材料层级结构（全部制造件自己造、反应产物与原料买入；所有层级用同一个 `bp_me`）。

    数量来自统一引擎 `domain.industry_calc.calculate`：共享中间件**合并取整一次**、
    `raw_materials` 按物品合并（以前同一种矿在不同分支各列一行、各自取整）。

    Args:
        type_id: 目标成品 type_id
        quantity: 需要多少个成品
        bp_me: 蓝图 ME 等级 (0-10)，各层共用
        price_hub: 价格查询的贸易中心 (Jita, Amarr, ...)
        price_type: 价格类型 'buy' / 'sell'
        max_depth: 中间件最大层级（防呆，默认 5 层足够覆盖 T3；更深的件按买入）
        char_config: 角色配置（预留，暂未使用）

    Returns:
        {
            "tree": BomNode,                  # 根节点（成品）
            "full_cost": float,               # 总材料成本（= 按合并后的原料清单买齐）
            "leaf_only_cost": float,          # 同上（保留键名兼容）
            "depth": int,                     # 实际最大展开深度
            "raw_materials": [...],           # 合并后的要买的东西
            "intermediates": [...],           # 自己造的中间件（合并后）
        }
    """
    from domain.industry_calc import ACTIVITY_MANUFACTURING, Target, calculate
    from services.blueprint_reader import SqliteBlueprintReader

    with _default_db().connect("ref", "mkt", "bp") as conn:
        result = calculate(
            SqliteBlueprintReader(conn),
            [
                Target(
                    int(type_id),
                    quantity=max(int(quantity), 0),
                    me_level=int(bp_me),
                    key="root",
                    activity=ACTIVITY_MANUFACTURING,
                )
            ],
            me_for=lambda *_: int(bp_me),
            max_depth=max_depth,
        )
        tree = _build_tree(conn, result, int(type_id), float(quantity), price_hub, price_type, max_depth)
        pricing = _default_pricing()

        if result.roots[0].blueprint_type_id is None:
            raws = {int(type_id): int(quantity)}  # 无配方：把它自己当唯一材料（既有调用方依赖这个兜底）
        else:
            raws = result.raws
        raw_materials: list[dict] = []
        for tid, qty in sorted(raws.items()):
            unit = float(pricing.get_price(tid, price_type, price_hub) or 0.0)
            raw_materials.append(
                {
                    "type_id": tid,
                    "name": _resolve_name(conn, tid),
                    "total_qty": qty,
                    "unit_price": round(unit, 2),
                    "subtotal": round(unit * qty, 2),
                }
            )
        intermediates = [
            {
                "type_id": n.type_id,
                "name": _resolve_name(conn, n.type_id),
                "quantity": n.demand,
                "runs": n.runs,
                "leftover": n.leftover,
                "blueprint_type_id": n.blueprint_type_id,
                "depth": n.depth,
            }
            for n in sorted(result.nodes.values(), key=lambda n: (n.depth, n.type_id))
        ]

    leaf_cost = round(sum(m["subtotal"] for m in raw_materials), 2)
    return {
        "tree": tree,
        "full_cost": leaf_cost,
        "leaf_only_cost": leaf_cost,
        "depth": max((n.depth for n in result.nodes.values()), default=0)
        + (1 if raw_materials and result.roots[0].inputs else 0),
        "raw_materials": raw_materials,
        "intermediates": intermediates,
    }


# ════════════════════════════════════════════════════
#  便捷函数
# ════════════════════════════════════════════════════


def get_material_tree(
    type_id: int,
    quantity: int = 1,
    bp_me: int = 0,
    price_hub: str = "Jita",
    price_type: str = "sell",
) -> BomNode:
    """返回 BOM 树根节点（简洁接口）"""
    result = expand_bom(
        type_id=type_id,
        quantity=quantity,
        bp_me=bp_me,
        price_hub=price_hub,
        price_type=price_type,
    )
    return result["tree"]  # type: ignore[no-any-return]


def get_flat_materials(
    type_id: int,
    quantity: int = 1,
    bp_me: int = 0,
    price_hub: str = "Jita",
    price_type: str = "sell",
) -> list[dict]:
    """返回扁平化的所有叶子材料列表（购物清单）"""
    result = expand_bom(
        type_id=type_id,
        quantity=quantity,
        bp_me=bp_me,
        price_hub=price_hub,
        price_type=price_type,
    )
    return result["raw_materials"]  # type: ignore[no-any-return]


def print_tree(node: BomNode, indent: int = 0) -> str:
    """调试用：打印 BOM 树结构"""
    prefix = "  " * indent
    tag = "[造]" if node.is_intermediate else "[买]"
    line = f"{prefix}{tag} {node.name} (×{node.quantity:.0f}) — {node.subtotal:,.2f} ISK"
    lines = [line]
    for child in node.children:
        lines.append(print_tree(child, indent + 1))
    return "\n".join(lines)
