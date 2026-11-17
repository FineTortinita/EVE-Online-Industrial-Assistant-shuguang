# services.lp_service

> 源文件 `services/lp_service.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

LP 兑换：从曙光 ESI 拉取 NPC 公司 LP 商店，并按本地价格算每 LP 收益。

参考 T2 商人助手「忠诚点价值」与开明网「LP 兑换」。兑换项来自公开接口
`/loyalty/stores/&#123;corporation_id&#125;/offers/`（无需授权，曙光实测可用）；
价格取本地 market.db 的指定贸易中心（默认首项索巴色基），不另发行情请求。

## 函数

### `_fetch_offers_async`

```python
async def _fetch_offers_async(corporation_id: int) -> list[dict] | None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`17`

### `fetch_lp_offers`

```python
def fetch_lp_offers(corporation_id: int) -> list[dict]
```

同步入口（供 QThread 调用）。网络失败/404 返回空列表。

定义行：`25`

### `list_npc_corporations`

```python
def list_npc_corporations(db) -> list[dict]
```

reference.db 的 NPC 公司 → [&#123;id, name&#125;]，按名称排序。

定义行：`30`

### `_fetch_zh_names_async`

```python
async def _fetch_zh_names_async(ids: list[int]) -> dict[int, str]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`41`

### `fetch_corporation_zh_names`

```python
def fetch_corporation_zh_names(ids: list[int]) -> dict[int, str]
```

曙光 ESI 中文公司名（如 1000035 → 加达里海军）；失败返回空 dict。

定义行：`54`

### `value_lp_offers`

```python
def value_lp_offers(db, offers: list[dict], hub: str | None=None, price_type: str='sell') -> list[dict[str, Any]]
```

兑换项 → 按每 LP 收益排序的估值行（含物品名）。

定义行：`59`

### `hub_has_prices`

```python
def hub_has_prices(db, hub: str) -> bool
```

该贸易中心在本地 market.db 是否已有价格（新加入的索巴色基首次使用前可能为空）。

定义行：`82`
