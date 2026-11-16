"""曙光端点与贸易中心回归。"""

from urllib.parse import parse_qs, urlsplit

import pytest

from core.constants import HUB_NAMES, TRADE_HUB_IDS, esi_url

pytestmark = pytest.mark.fast


@pytest.mark.parametrize("params", [{}, {"datasource": "serenity"}, {"datasource": None}])
def test_esi_url_locks_infinity(params: dict[str, object]) -> None:
    url = urlsplit(esi_url("/markets/10000002/orders/", page=2, type_id=None, **params))
    assert (url.scheme, url.netloc, url.path) == ("https", "ali-esi.evepc.163.com", "/latest/markets/10000002/orders/")
    assert parse_qs(url.query) == {"datasource": ["infinity"], "page": ["2"]}


def test_hek_region_is_metropolis() -> None:
    assert TRADE_HUB_IDS["Hek"] == 10000042
    assert HUB_NAMES[10000042] == "Hek"
