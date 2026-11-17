"""改单价计算 —— 纯函数（参考 T2 商人助手「改单小助手」：被压单后给出抢回首位的价格）。

EVE 的挂单价只允许 **4 位有效数字**（且最小单位 0.01 ISK）。实测 2026-11-17 曙光 Lonetrek
7 种物品买卖共 224 笔实时挂单，有效数字 1–4 位各有，**没有一笔超过 4 位**。
所以「比最优价低 0.01」在价格 ≥ 100 时是非法价，游戏会拒绝 —— 抢首位的合法价是
「最优价往有利方向移动一个最小价位」，价位大小随数量级变化（如 1,234,000 的价位是 1,000）。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

SIGNIFICANT_DIGITS = 4
MIN_TICK = Decimal("0.01")


def price_tick(price: float) -> Decimal:
    """该价位上的最小价格步长（≥ 0.01）。"""
    value = Decimal(str(price))
    if value <= 0:
        return MIN_TICK
    exponent = value.adjusted() - (SIGNIFICANT_DIGITS - 1)
    return max(Decimal(1).scaleb(exponent), MIN_TICK)


def undercut_price(best: float, is_buy: bool) -> float | None:
    """抢首位的合法价：卖单 = 严格低于 `best` 的最大合法价；买单 = 严格高于 `best` 的最小合法价。

    跨数量级时步长会变（卖单从 1,000 往下压，合法价是 999.9 而不是 990）；所以按「目标价」所在
    数量级重新取步长，再对齐到步长网格。`best` ≤ 0 或压到 0 以下 → None。
    """
    value = Decimal(str(best))
    if value <= 0:
        return None
    tick = price_tick(best)
    if is_buy:
        candidate = value + tick
        step = price_tick(float(candidate))
        result = (candidate / step).to_integral_value(rounding=ROUND_CEILING) * step
        if result <= value:
            result += step
    else:
        candidate = value - tick
        if candidate <= 0:
            return None
        step = price_tick(float(candidate))
        result = (candidate / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if result >= value:
            result -= step
        # 往下跨数量级后网格变细，贴着 best 的那个合法价可能更高：取严格低于 best 的最大值
        finer = ((value - step) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if result < finer < value:
            result = finer
        if result <= 0:
            return None
    return float(result)
