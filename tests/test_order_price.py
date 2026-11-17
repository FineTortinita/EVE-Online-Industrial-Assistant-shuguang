"""改单价（domain/order_price.py）：4 位有效数字的价格网格。"""

import pytest

from domain.order_price import undercut_price

pytestmark = pytest.mark.fast


@pytest.mark.parametrize(
    "best, is_buy, expected",
    [
        (5.55, False, 5.54),  # 小于 10：最小单位 0.01
        (5.55, True, 5.56),
        (1234.0, False, 1233.0),  # 4 位有效数字：步长 1
        (1234.0, True, 1235.0),
        (1_234_000.0, False, 1_233_000.0),  # 步长 1,000（不是 0.01）
        (1000.0, False, 999.9),  # 往下跨数量级：网格变细
        (9999.0, True, 10_000.0),  # 往上跨数量级
        (0.01, False, None),  # 压不下去
        (0.0, True, None),
    ],
)
def test_undercut_price(best, is_buy, expected):
    assert undercut_price(best, is_buy) == expected


def test_result_has_at_most_four_significant_digits():
    from decimal import Decimal

    for best in (12.34, 98_765.0, 3_601_268.19, 436_414.63, 1.0, 100.0):
        for is_buy in (False, True):
            price = undercut_price(best, is_buy)
            assert price is not None
            assert len(Decimal(str(price)).normalize().as_tuple().digits) <= 4
            assert (price < best) if not is_buy else (price > best)
