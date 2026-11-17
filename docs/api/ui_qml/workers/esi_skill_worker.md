# ui_qml.workers.esi_skill_worker

> 源文件 `ui_qml/workers/esi_skill_worker.py` · 由 `scripts/gen_api_docs.py` 自动生成，请勿手改

> 模块说明：

从曙光 ESI 导入角色技能 —— OAuth PKCE + 手动粘贴回跳地址 + 拉取。

**为什么在 `ui_qml/workers/` 而不是 `services/`**：整套流程只有一个调用方
（人物设置对话框的导入按钮），按本仓克制条款第 1 条不足以新开服务模块；而
「UI 异步 = QThread + Signal」的既定位置就是这里，且已有 worker 直连 ESI 的先例
（`ui_qml/workers/order_workers.py`）。

**为什么是「粘贴回跳地址」而不是本地回环回调**（2026-11-16 调研，见 docs/dev/decisions.md）：
网易没有公开的曙光开发者门户，无法为本应用注册 client_id 和 localhost 回调。现有曙光
第三方工具（开明网、eve.navexor.top）都复用网易官方 ESI 文档页内置的公共客户端
`bc90aa49…`，其回调固定为官方的 `https://ali-esi.evepc.163.com/ui/oauth2-redirect.html`。
那个页面脱离文档页打开时是空白页，授权码留在地址栏里 —— 用户把地址栏复制回来即可。

**安全口径**：走 PKCE 公共客户端，**不带 client_secret**；`state` 在本地生成并逐字校验，
粘贴进来的地址必须是官方回调页，否则拒绝（防止把别处的 code 塞进来）。

**一次授权 = 一个角色**：令牌 `sub` 形如 `CHARACTER:<租户>:<id>`，用户在网易页面上选角色。
多账号 / 一账号多角色 = 每个角色各点一次导入。

## 函数

### `_utc_now`

```python
def _utc_now() -> str
```

ESI 同款格式的 UTC 时间串。同格式 ISO8601 按字典序比较即等价于按时间比较。

定义行：`115`

### `_utc_after`

```python
def _utc_after(seconds: int) -> str
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`120`

### `_token_conn`

```python
def _token_conn()
```

打开 esi_tokens 所在连接。

定义行：`127`

### `load_token_row`

```python
def load_token_row(character_name: str) -> dict | None
```

按角色名取绑定行（token 按角色绑定，名字是配置里的匹配键）。

定义行：`146`

### `list_token_rows`

```python
def list_token_rows() -> list[dict]
```

全部已绑定角色 —— ESI 钱包/挂单同步要遍历每个角色各拉一份。

定义行：`159`

### `save_token_row`

```python
def save_token_row(character_id: int, character_name: str, refresh_token: str, access_token: str, access_expires_at: str) -> None
```

写入/更新绑定行。

定义行：`169`

### `delete_token_row`

```python
def delete_token_row(character_id: int) -> None
```

授权被撤销时清掉这一行（留着只会每次刷新都失败）。

定义行：`200`

### `_pkce_pair`

```python
def _pkce_pair() -> tuple[str, str]
```

生成 (code_verifier, code_challenge)，与官方 PKCE 示例一致。

定义行：`213`

### `_jwt_claims`

```python
def _jwt_claims(access_token: str) -> dict
```

解出 JWT 载荷，**不验签**。

定义行：`220`

### `_check_claims`

```python
def _check_claims(claims: dict, now_ts: float | None=None) -> None
```

校验曙光签发方、有效期与客户端声明；声明检查不等同于签名验证。

定义行：`237`

### `_character_from_claims`

```python
def _character_from_claims(claims: dict, fallback_id: int | None, fallback_name: str | None) -> tuple[int, str]
```

`sub` 形如 `CHARACTER:<租户>:<id>`（国际服租户是 EVE；曙光的租户名以实测为准，只要求 CHARACTER 前缀）。

定义行：`256`

### `parse_redirect`

```python
def parse_redirect(pasted: str, expected_state: str) -> str
```

校验用户粘贴的回跳地址并取出授权码。

定义行：`273`

### `_fetch_device_id`

```python
async def _fetch_device_id(session: aiohttp.ClientSession) -> str
```

向网易申请一次性设备 ID（授权地址必带）。失败抛 RuntimeError（文案给用户看）。

定义行：`304`

### `_authorize_url`

```python
def _authorize_url(code_challenge: str, state: str, extra_scopes: tuple[str, ...]=(), *, device_id: str='') -> str
```

浏览器要打开的地址：先经网易 `/account/logoff` 登出残留会话，再回跳授权页。

定义行：`330`

### `_post_token`

```python
async def _post_token(client, data: dict) -> dict
```

POST 到 token 端点。

定义行：`358`

### `_get_json`

```python
async def _get_json(client, url: str, token: str, *, scope_hint: str='技能/增效体')
```

带 Bearer 的 GET。自己看状态码 —— `APIClient.fetch` 把 401/403 都吞成 None，
而我们必须把「授权失效」和「缺 scope」分开告诉用户。

定义行：`391`

### `_resolve_skill_names`

```python
def _resolve_skill_names(skill_ids: list[int]) -> dict[int, str]
```

skill_id → 中文名（ESI 的 skill_id 就是 SDE 的 type_id）。

定义行：`418`

## 类

### `class _Interrupted`（继承 `Exception`）

用户关掉了对话框 —— 不是错误，不要发信号。

定义行：`89`

### `class _TokenError`（继承 `RuntimeError`）

token 端点返回非 200。`code` 是 OAuth 的 error 字段（如 invalid_grant）。

定义行：`93`

#### 方法

##### `__init__`

```python
def __init__(self, code: str, desc: str, status: int) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`96`

### `class EsiAuthRevoked`（继承 `RuntimeError`）

401：令牌失效或授权已被撤销，需要用户重新授权。

定义行：`104`

### `class EsiScopeMissing`（继承 `RuntimeError`）

403：令牌有效但缺 scope。

定义行：`108`

### `class EsiSkillImportWorker`（继承 `QThread`）

拉取一个角色的技能 / 技能队列 / 植入体。

产出 `result_signal` 的载荷：`&#123;character_id, character_name, skills: &#123;中文名: 等级&#125;, implants: [type_id]&#125;`。
**worker 不写 char_config** —— 只回数据，由主线程的桥合并落盘，保证配置只有一个写者。

定义行：`436`

#### 方法

##### `__init__`

```python
def __init__(self, character_name: str | None=None, *, force_browser: bool=False, parent=None) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`449`
##### `submit_redirect`

```python
def submit_redirect(self, text: str | None) -> None
```

主线程调用：交回用户粘贴的回跳地址（None = 取消）。线程安全。

定义行：`461`
##### `run`

```python
def run(self) -> None
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`468`
##### `_import`

```python
async def _import(self) -> dict
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`493`
##### `_extra_scopes`

```python
def _extra_scopes(self) -> tuple[str, ...]
```

「带开关的可选能力」按需追加的 scope。目前只有军团钱包一项。

定义行：`504`
##### `_obtain_token`

```python
async def _obtain_token(self, client, character_name: str | None=None, *, allow_browser: bool=True) -> tuple[str, int, str]
```

有可用绑定就静默刷新，否则开浏览器授权。`force_browser` 时跳过静默路径。

定义行：`519`
##### `_still_valid`

```python
def _still_valid(expires_at: str | None) -> bool
```

同格式 ISO8601 UTC 串，字典序即时间序；留 `_EXPIRY_MARGIN_S` 裕量。

定义行：`562`
##### `_refresh`

```python
async def _refresh(self, client, row: dict) -> tuple[str, int, str]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`569`
##### `_browser_authorize`

```python
async def _browser_authorize(self, client) -> tuple[str, int, str]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`586`
##### `_await_redirect`

```python
def _await_redirect(self) -> str
```

阻塞等主线程交回粘贴内容；0.5s 一查中断（`detach_worker` 只等 500ms）。

定义行：`614`
##### `_persist`

```python
def _persist(self, payload: dict, *, previous_refresh: str | None=None, fallback_id: int | None=None, fallback_name: str | None=None) -> tuple[str, int, str]
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`626`
##### `_pull`

```python
async def _pull(self, client, access: str, char_id: int, char_name: str) -> dict
```

::: warning ⚠️ 待补 docstring
此函数暂无 docstring，欢迎补充。
:::

定义行：`650`
