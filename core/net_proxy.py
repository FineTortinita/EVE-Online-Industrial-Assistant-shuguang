"""系统代理自发现 —— 给所有 aiohttp 会话用的「按请求选代理」中间件。

**为什么要有**：国内访问 GitHub（曙光快照下载）常常很慢或不通，用户普遍开着 Clash /
v2rayN 之类把代理写进 Windows「系统代理」。aiohttp 默认 `trust_env=False`，根本不看系统
代理；即使开了 `trust_env`，它走的 `urllib.getproxies()` 也只认环境变量 + 注册表里的
手动代理，**PAC 脚本 / WPAD 自动检测一概不支持**，例外列表的匹配也只是简化版。

**参照的成熟做法**：
- Qt / Chromium 系应用：Windows 上经 WinHTTP（`WinHttpGetIEProxyConfigForCurrentUser` +
  `WinHttpGetProxyForUrl`）按 URL 解析，覆盖手动代理、PAC、WPAD、例外列表。
  这里直接用 `QNetworkProxyFactory.systemProxyForQuery` —— 本应用已依赖 PySide6，
  不需要自己写 ctypes 调 WinHTTP。macOS / libproxy 平台同样由 Qt 处理。
- requests / pip / conda：**环境变量优先**（`HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`），
  没设才看系统设置。这里沿用同一优先级：显式设置永远赢过自动发现。

**取舍**：
- 只接 HTTP 代理（含 CONNECT 隧道走 HTTPS）。SOCKS 需要额外依赖（aiohttp-socks），而
  Clash / v2rayN 写进系统代理的都是 HTTP 混合端口 —— 遇到 SOCKS 记一次日志后直连。
- 按「scheme://host:port」缓存结果：PAC 求值在 Windows 上可能耗时数秒（Qt 文档原话），
  不能每个请求都算；缓存 5 分钟，切换代理后最迟 5 分钟生效。
- 中间件在每次重定向后都会再跑一次（aiohttp 的重定向循环在中间件之内）：
  GitHub Release 下载会 302 到 `objects.githubusercontent.com`，它也能拿到正确代理。
"""

from __future__ import annotations

import os
import threading
import time
import urllib.request
from collections.abc import Awaitable, Callable

import aiohttp
from yarl import URL

from core.logger import log

#: 关掉自动发现（排查网络问题 / 用户明确要直连）
DISABLE_ENV = "EVE_NO_SYSTEM_PROXY"

_CACHE_TTL_S = 300.0
_cache: dict[str, tuple[float, URL | None]] = {}
_cache_lock = threading.Lock()
_warned: set[str] = set()


def _env_proxy(url: URL) -> tuple[bool, URL | None]:
    """环境变量代理：`(是否设置过, 代理)`。设置过但被 NO_PROXY 排除 → `(True, None)`。"""
    env = urllib.request.getproxies_environment()
    if not env:
        return False, None
    # 标准库有这个函数（requests 同样直接用它），只是 typeshed 存根没声明
    if url.host and urllib.request.proxy_bypass_environment(url.host, env):  # type: ignore[attr-defined]
        return True, None
    raw = env.get(url.scheme) or env.get("all")
    return True, (URL(raw) if raw else None)


def _system_proxy(url: URL) -> URL | None:
    """系统代理（WinHTTP：手动 / PAC / WPAD / 例外列表）。没有或不可用 → None。"""
    from PySide6.QtCore import QUrl
    from PySide6.QtNetwork import QNetworkProxy, QNetworkProxyFactory, QNetworkProxyQuery

    for proxy in QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(QUrl(str(url)))):
        kind = proxy.type()
        if kind == QNetworkProxy.ProxyType.NoProxy:
            return None
        if kind == QNetworkProxy.ProxyType.HttpProxy and proxy.hostName():
            return URL.build(scheme="http", host=proxy.hostName(), port=proxy.port() or 80)
        key = f"{kind.name}:{proxy.hostName()}:{proxy.port()}"
        if key not in _warned:
            _warned.add(key)
            log.warning("系统代理类型 %s（%s:%s）暂不支持，改为直连", kind.name, proxy.hostName(), proxy.port())
    return None


def resolve_proxy(url: URL) -> URL | None:
    """给一个请求 URL 选代理（None = 直连）。线程安全，结果按 scheme+host+port 缓存。"""
    if url.scheme not in ("http", "https") or not url.host:
        return None
    if os.environ.get(DISABLE_ENV):
        return None
    configured, proxy = _env_proxy(url)
    if configured:
        return proxy
    key = f"{url.scheme}://{url.host}:{url.port}"
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    try:
        proxy = _system_proxy(url)
    except Exception:
        # 吞的是 Qt 系统代理查询的任何失败（WinHTTP 服务异常 / PAC 脚本报错）：直连兜底，不能让下载整体失败
        log.warning("系统代理查询失败，%s 改为直连", url.host, exc_info=True)
        proxy = None
    with _cache_lock:
        _cache[key] = (now, proxy)
    if proxy is not None:
        log.info("网络请求 %s 经系统代理 %s", url.host, proxy)
    return proxy


async def system_proxy_middleware(
    req: aiohttp.ClientRequest,
    handler: Callable[[aiohttp.ClientRequest], Awaitable[aiohttp.ClientResponse]],
) -> aiohttp.ClientResponse:
    """aiohttp 客户端中间件：请求里没显式指定代理时，按系统设置补上。"""
    if req.proxy is None:
        # PAC 求值可能阻塞数秒：放线程池，不卡事件循环
        import asyncio

        proxy = await asyncio.to_thread(resolve_proxy, req.url)
        if proxy is not None:
            req.update_proxy(proxy, None, None)
    return await handler(req)


#: 所有 `aiohttp.ClientSession(...)` 都传 `middlewares=PROXY_MIDDLEWARES`
PROXY_MIDDLEWARES = (system_proxy_middleware,)
