"""体积运费 —— 纯函数（参考 T2 商人助手 2024-03-25「体积运费」）。

跑货的成本主要按体积计：货运合同 / 自运都按「每立方米多少 ISK」估。
净价差 = 价差 − 单件体积 × 每方运费；体积未知（≤0）时无法扣运费 → None，不当成 0 运费。
"""

from __future__ import annotations


def net_after_freight(spread: float, volume_m3: float, rate_per_m3: float) -> tuple[float | None, float | None]:
    """→ (每件净价差, 每方净利润)。`rate_per_m3` ≤ 0 视为不计运费。"""
    volume = float(volume_m3 or 0.0)
    rate = max(float(rate_per_m3 or 0.0), 0.0)
    if volume <= 0:
        return (float(spread) if rate == 0 else None), None
    net = float(spread) - volume * rate
    return net, net / volume
