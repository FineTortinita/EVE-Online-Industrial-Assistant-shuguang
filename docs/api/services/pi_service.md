# services.pi_service

> 源文件 `services/pi_service.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

行星工业加工利润：读曙光配方表 + 本地价格 → 每个配方的利润排行。

配方来自曙光客户端 `res:/staticdata/schematics.fsdbinary`，由
`python -m scripts.infinity_fsd schematics <client_root> data/pi_schematics.json` 导出并随发行包分发
（build 3684550：68 条，cycle_time 与曙光 ESI `/universe/schematics/&#123;id&#125;/` 全部一致）。
价格读本地 market.db，不另发行情请求。

## 函数

### `load_schematics`

```python
def load_schematics() -> dict[int, dict]
```

配方表；文件缺失返回空 dict（界面据此提示），不猜数据。

定义行：`24`

### `rank_schematics`

```python
def rank_schematics(db, hub: str | None=None, *, sell_type: str='sell', buy_type: str='sell', tax_rate: float=0.0, import_inputs: bool=True) -> list[dict[str, Any]]
```

全部配方按每小时利润降序（缺价排最后）。

定义行：`33`
