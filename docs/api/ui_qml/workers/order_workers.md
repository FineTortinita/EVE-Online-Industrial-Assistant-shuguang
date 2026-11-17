# ui_qml.workers.order_workers

> 源文件 `ui_qml/workers/order_workers.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

订单取数的共享部分：名称缓存 + ESI 线程。

原先是 Widgets 版订单弹窗的配套部分，QML 迁移时拆到这里。
订单弹窗已删除，本模块仍服务物品查询页的「订单列表」详情面板
（`QueryDetailBridge`）。`order_cache` 的**唯一写入方**是
`QueryDetailBridge._on_orders_fetched`。

## 函数

### `_shared_loop`

```python
def _shared_loop() -> asyncio.AbstractEventLoop
```

常驻事件循环（懒建，daemon 线程里 `run_forever()`）。

定义行：`59`

### `_run_loop_forever`

```python
def _run_loop_forever(loop: asyncio.AbstractEventLoop, running: threading.Event) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`76`

### `_shared_client`

```python
async def _shared_client() -> APIClient
```

**只在常驻循环里调用**：懒建并进入共享 `APIClient`（session 保持打开）。

定义行：`82`

## 类

### `class OrderFetchWorker`（继承 `QThread`）

后台获取 ESI 订单数据

定义行：`95`

#### 方法

##### `__init__`

```python
def __init__(self, type_id: int, region_id: int=10000002, parent=None)
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`101`
##### `run`

```python
def run(self)
```

把取数交给常驻循环（共享 session），**边等边轮询中断**。

定义行：`106`
##### `_await_interruptible`

```python
def _await_interruptible(self, future: concurrent.futures.Future[tuple[list[Any], list[Any]]]) -> tuple[list[Any], list[Any]] | None
```

轮询等结果；期间 `requestInterruption()` 就取消并返回 None。

定义行：`123`
##### `_fetch_shared`

```python
async def _fetch_shared(self) -> tuple[list, list]
```

常驻循环里跑：拿共享 client（懒建）→ 取数。session 不随本次取数关闭。

定义行：`139`
##### `_fetch`

```python
async def _fetch(self, client: APIClient | None=None) -> tuple[list, list]
```

取买单 / 卖单。

定义行：`143`
##### `_fetch_side`

```python
async def _fetch_side(self, client: APIClient, url: str, order_type: str) -> tuple[int | None, list]
```

取一个方向的**全部页**；返回 `(status, rows)`（02-08/02-11）。

定义行：`156`
##### `_fetch_orders`

```python
async def _fetch_orders(self, client: APIClient) -> tuple[list, list]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`181`
##### `_resolve_names`

```python
async def _resolve_names(self, location_ids: list[int], client: APIClient | None=None) -> None
```

location_id → 站名：**先查本地 SDE，只有本地没有的才打 ESI**。

定义行：`215`
##### `_resolve_names_remote`

```python
async def _resolve_names_remote(self, client: APIClient, ids: list[int]) -> None
```

ESI `/universe/names/` 兜底（实际只会走到玩家建筑）。

定义行：`250`
##### `_resolve_names_one_by_one`

```python
async def _resolve_names_one_by_one(self, client, ids: list[int], url: str) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`281`
##### `_absorb`

```python
def _absorb(payload: list) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`298`
