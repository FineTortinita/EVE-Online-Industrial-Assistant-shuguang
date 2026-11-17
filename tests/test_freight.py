"""体积运费纯函数（domain/freight.py）。"""

import pytest

from domain.freight import net_after_freight

pytestmark = pytest.mark.fast


@pytest.mark.parametrize(
    "spread, volume, rate, expected",
    [
        (5000.0, 10.0, 300.0, (2000.0, 200.0)),  # 扣 3000 运费
        (5000.0, 10.0, 0.0, (5000.0, 500.0)),  # 不计运费
        (5000.0, 0.0, 0.0, (5000.0, None)),  # 体积未知、不计运费：价差照旧，每方算不了
        (5000.0, 0.0, 300.0, (None, None)),  # 体积未知但要计运费：扣不了，不当成 0 运费
        (5000.0, 10.0, -1.0, (5000.0, 500.0)),  # 负运费按 0
    ],
)
def test_net_after_freight(spread, volume, rate, expected):
    assert net_after_freight(spread, volume, rate) == expected
