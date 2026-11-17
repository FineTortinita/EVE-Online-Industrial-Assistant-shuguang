# ui_qml.workers.esi_industry_worker

> 源文件 `ui_qml/workers/esi_industry_worker.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

ESI 资产 + 进行中工业作业：一次拉**全部已绑定角色**（重构阶段 6）。

继承 `esi_skill_worker.EsiSkillImportWorker`，**只重写 `_import()`**（同 `esi_wallet_worker`）：
令牌获取 / 静默刷新 / 过期与撤销处理全部复用基类，批量路径不弹浏览器（`allow_browser=False`）。

载荷（`result_signal`）：
- `assets`：`&#123;角色 id: [ESI 资产行]&#125;`（已翻完 `X-Pages` 全部页，实测 凋零 3 页 × 1000 条）；
- `locations`：`&#123;位置 id: 显示名&#125;`（NPC 空间站查 reference.db；玩家建筑走 `/universe/structures/&#123;id&#125;/`，
  要 `esi-universe.read_structures.v1`，未授权时显示「建筑 &lt;id>」）；
- `jobs`：全部角色的进行中作业（`include_completed=false`）；
- `char_names`：`&#123;角色 id: 名字&#125;`（作业 `installer_id` → 人物名，对账用）；
- `errors`：单角色失败的说明，不影响其余角色。

## 类

### `class EsiIndustrySyncWorker`（继承 `EsiSkillImportWorker`）

一次性线程：逐角色拉资产（全部页）与进行中工业作业。

定义行：`32`

#### 方法

##### `_import`

```python
async def _import(self) -> dict
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`35`
##### `_pull_assets`

```python
async def _pull_assets(self, client, access: str, char_id: int) -> list
```

翻完全部页（`X-Pages`）。`_get_json` 不回响应头，所以按「不满 1000 条即最后一页」判停。

定义行：`77`
##### `_location_names`

```python
async def _location_names(self, client, ids: set[int], assets: dict[int, list]) -> dict[int, str]
```

位置 id → 显示名。只解析**顶层**位置（不是某个物品的 item_id）。

定义行：`93`
