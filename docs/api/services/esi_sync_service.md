# services.esi_sync_service

> 源文件 `services/esi_sync_service.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

ESI 资产 / 工业作业同步的落地（重构阶段 6）：把 worker 拉回来的数据接到现有的库存审阅与计划状态上。

调用方：仓库页「从 ESI 同步资产」与产线启动小助手「从 ESI 对账作业」（`ui_qml/bridge/inventory_bridge.py`、
`ui_qml/views/industry/production_launcher.py`）。纯逻辑在 `domain.esi_sync`。

## 函数

### `location_choices`

```python
def location_choices(payload: dict) -> list[dict]
```

同步结果 → 可选的位置 `[&#123;location_id, name, types, units&#125;]`（按物品种数降序）。

定义行：`16`

### `review_items`

```python
def review_items(per_type: dict[int, int]) -> list[dict]
```

某位置的 `&#123;type_id: 数量&#125;` → 导入预览对话框的行（与剪贴板解析同形：type_id / raw_name / zh_name / en_name / qty / status）。

定义行：`42`

### `reconcile_jobs`

```python
def reconcile_jobs(payload: dict) -> dict[str, Any]
```

ESI 进行中作业 ↔ 本地在产计划 → `&#123;matches, summary, unmatched, ready_ids&#125;`。

定义行：`64`

### `mark_ready`

```python
def mark_ready(plan_ids: list[int]) -> int
```

把 ESI 已完成的计划置为「待下线」（只动在产行），返回改动数。

定义行：`95`
