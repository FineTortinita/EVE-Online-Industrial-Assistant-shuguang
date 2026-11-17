# services.character_planning_service

> 源文件 `services/character_planning_service.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

人物规划装配（重构阶段 5）：计划行 + 人物配置 + 蓝图技能要求 → `domain.character_planning`。

调用方：人物占用对话框的「自动派人 / 人物时间线」（`ui_qml/bridge/char_usage_bridge.py`）
与「执行派人」写回（同文件）。算法全在 domain，这里只取数与落库。

## 函数

### `required_skills`

```python
def required_skills(blueprint_ids: Iterable[int], *, db=None) -> dict[tuple[int, str], dict[str, int]]
```

&#123;(蓝图, 活动): &#123;技能中文名: 等级&#125;&#125;（技能名与 char_config.json 的 skills 键同源：item.zh_name）。

定义行：`28`

### `_plan_hours`

```python
def _plan_hours(p: dict) -> float
```

单线时长（小时）：`calculated_time` 是整条计划（单线 runs 个流程）的秒数。

定义行：`46`

### `build_inputs`

```python
def build_inputs(plans: list[dict], *, db=None) -> tuple[list[Job], list[Character], dict[int, dict]]
```

计划行（需已 enrich `category`）→ (待排作业, 人物, &#123;job_id: 计划行&#125;)。

定义行：`52`

### `plan_assignments`

```python
def plan_assignments(plans: list[dict], *, db=None) -> dict
```

自动派人 + 槽位排程 → 展示数据。

定义行：`134`

### `apply_assignments`

```python
def apply_assignments(assignments: dict[int, str]) -> int
```

把派人结果写回 `production_plans.char_name`（只改待生产行），返回改动行数。

定义行：`151`
