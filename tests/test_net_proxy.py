"""系统代理自发现：选择优先级与降级（Qt 系统代理查询打桩，不依赖本机网络设置）。"""

import pytest
from yarl import URL

import core.net_proxy as np

_GH = URL("https://github.com/x/releases/download/a.zip")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        np.DISABLE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    np._cache.clear()


@pytest.mark.parametrize(
    ("env", "system", "expected"),
    [
        ({}, "http://127.0.0.1:7890", "http://127.0.0.1:7890"),  # Clash 写的系统代理
        ({}, None, None),  # 没有系统代理 → 直连
        ({"HTTPS_PROXY": "http://10.0.0.1:3128"}, "http://127.0.0.1:7890", "http://10.0.0.1:3128"),  # 环境变量优先
        ({"HTTPS_PROXY": "http://10.0.0.1:3128", "NO_PROXY": "github.com"}, "http://127.0.0.1:7890", None),
        ({np.DISABLE_ENV: "1"}, "http://127.0.0.1:7890", None),  # 用户关掉自动发现
    ],
    ids=["system", "none", "env-wins", "no-proxy-wins", "disabled"],
)
def test_resolve_priority(monkeypatch, env, system, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(np, "_system_proxy", lambda url: URL(system) if system else None)
    got = np.resolve_proxy(_GH)
    assert (str(got) if got else None) == expected


def test_system_lookup_cached_and_failure_falls_back_to_direct(monkeypatch):
    """PAC 求值可能耗时数秒：同主机只查一次；查询抛错 → 直连，不让下载整体失败。"""
    calls: list[str] = []

    def flaky(url):
        calls.append(url.host)
        raise OSError("WinHTTP 服务不可用")

    monkeypatch.setattr(np, "_system_proxy", flaky)
    assert np.resolve_proxy(_GH) is None
    assert np.resolve_proxy(URL("https://github.com/other")) is None
    assert calls == ["github.com"]


def test_qt_socks_proxy_is_skipped(qapp):
    """系统代理是 SOCKS（未接入）→ 直连而不是把 SOCKS 地址当 HTTP 代理用。"""
    from PySide6.QtNetwork import QNetworkProxy, QNetworkProxyFactory

    original = QNetworkProxyFactory.systemProxyForQuery
    try:
        QNetworkProxyFactory.systemProxyForQuery = staticmethod(  # type: ignore[method-assign]
            lambda q: [QNetworkProxy(QNetworkProxy.ProxyType.Socks5Proxy, "127.0.0.1", 7891)]
        )
        assert np._system_proxy(_GH) is None
        QNetworkProxyFactory.systemProxyForQuery = staticmethod(  # type: ignore[method-assign]
            lambda q: [QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, "127.0.0.1", 7890)]
        )
        assert np._system_proxy(_GH) == URL("http://127.0.0.1:7890")
    finally:
        QNetworkProxyFactory.systemProxyForQuery = original  # type: ignore[method-assign]
