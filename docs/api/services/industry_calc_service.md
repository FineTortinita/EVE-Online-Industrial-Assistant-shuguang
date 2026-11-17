# services.industry_calc_service

> 源文件 `services/industry_calc_service.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

制造计算（开明网式）—— 选成品、填数量 → 原料汇总 / 逐级分解 / 成本与利润 / 采购清单。

计算本体是 `domain.industry_calc.calculate`（与生产计划拆解同一个引擎）；本模块只做三件事：
1. 把用户在参数条上的选择（制造 / 反应设施、类别造买开关、逐项造买、ME）装配成引擎的回调；
2. 给引擎结果标价：原料市价、每个作业的 EIV / 指数 / 作业费（`domain.formulas.calc_job_cost_fees`）/ 时长；
3. 归类与汇总，交给界面直接显示（`ui_qml/bridge/industry_calc_bridge.py`）。

口径说明（与开明网对照，见 `docs/dev/industry-refactor-plan.md` 1.1）：
- 「自采 / 自种 / 自造」的类别按**现金口径**不计价（旁边仍给市价参考）；改成买入则按市价计；
- 边角料按 `min(自制分摊成本, 卖价)` 抵扣，不扣变现税费；
- 作业费 = EIV × (指数 × 建筑成本加成 + 设施税 + SCC 4%)，制造与反应同一公式；
- 步骤按合并需求估算，未按游戏单工单流程上限拆批。

## 函数

### `category_of`

```python
def category_of(group_id: int | None) -> str
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`192`

### `category_label`

```python
def category_label(key: str) -> str
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`196`

### `make_build_policy`

```python
def make_build_policy(toggles: dict[str, bool], overrides: dict[int, bool], category: Callable[[int], str]) -> Callable[[int, str], bool]
```

(type_id, 活动) → 是否自己做。逐项覆盖 > 类别开关 > 默认（制造件自己造）。

定义行：`200`

### `_is_free`

```python
def _is_free(category: str, toggles: dict[str, bool]) -> bool
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`222`

### `run`

```python
def run(request: CalcRequest, *, db=None) -> CalcResult
```

执行一次制造计算。`db` 不给用容器里的库。

定义行：`226`

### `_implant_bonuses`

```python
def _implant_bonuses(implants: Iterable | None) -> list[float]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`421`

### `multibuy_text`

```python
def multibuy_text(raws: Iterable[RawLine], *, include_free: bool=False) -> str
```

游戏市场「批量购买」粘贴格式：每行「物品名&lt;Tab>数量」。

定义行：`429`

### `fmt_duration`

```python
def fmt_duration(seconds: float) -> str
```

秒 → 「3天 17小时」/「14小时 24分」/「12分」。

定义行：`434`

## 类

### `class Facility`

一处作业地点：星系（定指数）+ 设施倍率 + 设施税。由机库配置或手填得来。

定义行：`76`

#### 方法

##### `from_hangar`

```python
def from_hangar(cls, hangar_id: int | None, activity: str) -> Facility
```

按机库配置（建筑 / 改装件 / 安等 / 税 / 星系）构造；无机库 → NPC 默认。

定义行：`87`

### `class CalcRequest`

::: warning ⚠️ 待补 docstring
此类暂无 docstring，欢迎补充。
:::

定义行：`106`

### `class RawLine`

::: warning ⚠️ 待补 docstring
此类暂无 docstring，欢迎补充。
:::

定义行：`128`

#### 方法

##### `cost`

```python
def cost(self) -> float
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`138`

### `class JobLine`

::: warning ⚠️ 待补 docstring
此类暂无 docstring，欢迎补充。
:::

定义行：`143`

### `class CalcResult`

::: warning ⚠️ 待补 docstring
此类暂无 docstring，欢迎补充。
:::

定义行：`161`

#### 方法

##### `total_cost`

```python
def total_cost(self) -> float
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`179`
##### `net_cost`

```python
def net_cost(self) -> float
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`183`
