# core.net_proxy

> 源文件 `core/net_proxy.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

系统代理自发现 —— 给所有 aiohttp 会话用的「按请求选代理」中间件。

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

## 函数

### `_env_proxy`

```python
def _env_proxy(url: URL) -> tuple[bool, URL | None]
```

环境变量代理：`(是否设置过, 代理)`。设置过但被 NO_PROXY 排除 → `(True, None)`。

定义行：`47`

### `_system_proxy`

```python
def _system_proxy(url: URL) -> URL | None
```

系统代理（WinHTTP：手动 / PAC / WPAD / 例外列表）。没有或不可用 → None。

定义行：`59`

### `resolve_proxy`

```python
def resolve_proxy(url: URL) -> URL | None
```

给一个请求 URL 选代理（None = 直连）。线程安全，结果按 scheme+host+port 缓存。

定义行：`77`

### `system_proxy_middleware`

```python
async def system_proxy_middleware(req: aiohttp.ClientRequest, handler: Callable[[aiohttp.ClientRequest], Awaitable[aiohttp.ClientResponse]]) -> aiohttp.ClientResponse
```

aiohttp 客户端中间件：请求里没显式指定代理时，按系统设置补上。

定义行：`105`
