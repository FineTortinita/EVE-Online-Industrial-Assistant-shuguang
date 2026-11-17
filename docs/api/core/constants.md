# core.constants

> 源文件 `core/constants.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

全局常量 — 服务器端点、贸易中心 ID 等

## 函数

### `esi_url`

```python
def esi_url(path: str, **params: object) -> str
```

拼曙光 ESI URL：总是附带 datasource=infinity；值为 None 的参数丢弃。

定义行：`21`
