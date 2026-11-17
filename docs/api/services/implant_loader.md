# services.implant_loader

> 源文件 `services/implant_loader.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

工业植入体数据加载 — 从 reference.db 读取并解析加成描述。

## 函数

### `_parse_implant_bonus`

```python
def _parse_implant_bonus(attrs: list) -> str
```

从 dogma 属性解析人类可读的加成描述。

定义行：`16`

### `_implant_slot`

```python
def _implant_slot(attrs: list) -> str
```

按 dogma 加成属性判断该植入体归哪个插槽：A 生产与研究 / B 精炼与采矿 / C 通用。

定义行：`62`

### `load_implants`

```python
def load_implants() -> list[dict]
```

从 item_dogma 表加载所有工业植入体。

定义行：`74`

### `manufacturing_time_bonuses`

```python
def manufacturing_time_bonuses(implant_ids: list | None) -> list[float]
```

人物配置里的植入体 → 各自的制造时间加成百分比列表（无此属性 / 查不到的不计）。

定义行：`124`
