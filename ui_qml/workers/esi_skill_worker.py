"""从曙光 ESI 导入角色技能 —— OAuth PKCE + 手动粘贴回跳地址 + 拉取。

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
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
from PySide6.QtCore import QThread, Signal

from core.constants import SSO_AUTHORIZE_URL, SSO_ISSUER, SSO_TOKEN_URL, esi_url
from core.logger import log

#: 网易官方 ESI 文档页（https://ali-esi.evepc.163.com/ui/）内置的公共客户端 ID。
#: 曙光 / 晨曦共用；文档页源码里公开可见，属于 PKCE 公共客户端，不涉及 secret。
CLIENT_ID = "bc90aa496a404724a93f41b4f4e97761"

#: 该公共客户端登记的回调页（官方文档页的 OAuth 回跳页）。换令牌时必须原样带上。
CALLBACK_URL = "https://ali-esi.evepc.163.com/ui/oauth2-redirect.html"

#: 网易登录要求授权地址带 `device_id`，缺了授权页直接回 `{"Message":"An error has occurred."}`
#: （2026-11-16 用户实测）。官方文档页在 configureAuth 里先向这个接口申请（参数照抄其 bundle）；
#: 接口只认官方页面的 Origin —— 浏览器里做不到，桌面程序直接带上即可。
DEVICE_INIT_URL = "https://mpay-web.g.mkey.163.com/device/init"
DEVICE_GAME_ID = "aecfu6bgiuaaaal2-g-ma79"
DEVICE_ORIGIN = "https://ali-esi.evepc.163.com"

AUTHORIZE_URL = SSO_AUTHORIZE_URL
TOKEN_URL = SSO_TOKEN_URL

#: 只请求本功能要用的；应用上另勾的权限是给后续功能预留的，这里不请求也能用
#: （refresh 时只能传原子集的子集，传了没勾的反而会失败）。
#: ⚠️ 加 scope 后**老 token 不会刷新失败**（`_refresh` 不传 scope，拿到的是原子集），
#: 而是打新接口时 403 → 用户要重新授权一次；且**必须先在本应用的开发者门户勾选**，
#: 否则授权直接 `invalid_scope`，代码侧无解。
SCOPES = (
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-clones.read_implants.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-markets.read_character_orders.v1",
    # 重构阶段 6（2026-11-17 用户实测曙光公共客户端放行）：资产同步 + 进行中工业作业对账。
    # 蓝图 `esi-characters.read_blueprints.v1` 实测 `invalid_scope`，**不能**加进来（会让整条授权失败）。
    "esi-assets.read_assets.v1",
    "esi-industry.read_character_jobs.v1",
)

#: 可选能力「含军团钱包」要的 scope，**不进 `SCOPES`**（见 `_extra_scopes`）。
CORP_WALLET_SCOPE = "esi-wallet.read_corporation_wallets.v1"

#: access token 官方寿命 1200s（20 分钟）。剩不足这个裕量就提前刷新。
_EXPIRY_MARGIN_S = 60
_DEFAULT_EXPIRES_IN = 1200

#: 等用户粘贴回跳地址的上限。超时后得重新点导入（PKCE 的 verifier 与这次 state 一起作废）。
#: 给到 10 分钟：网易登录可能要短信 / 扫码，复制地址再回来很容易超过 3 分钟。
_AUTH_TIMEOUT_S = 600

#: 单请求超时
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: token 端点的重试次数与退避基数（秒）。**只对 5xx 生效** —— 见 `_post_token`。
_TOKEN_RETRIES = 3
_TOKEN_RETRY_BASE_S = 1.0


class _Interrupted(Exception):
    """用户关掉了对话框 —— 不是错误，不要发信号。"""


class _TokenError(RuntimeError):
    """token 端点返回非 200。`code` 是 OAuth 的 error 字段（如 invalid_grant）。"""

    def __init__(self, code: str, desc: str, status: int) -> None:
        super().__init__(desc or code or f"token 端点返回 {status}")
        self.code = code
        #: HTTP 状态码。调用方靠它区分「授权坏了（4xx invalid_grant）」与
        #: 「CCP 服务端故障（5xx）」—— 两者对用户的含义完全相反。
        self.status = status


class EsiAuthRevoked(RuntimeError):
    """401：令牌失效或授权已被撤销，需要用户重新授权。"""


class EsiScopeMissing(RuntimeError):
    """403：令牌有效但缺 scope。"""


# ── 时间 ──


def _utc_now() -> str:
    """ESI 同款格式的 UTC 时间串。同格式 ISO8601 按字典序比较即等价于按时间比较。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 令牌表（user.db，v19 迁移建的）──


def _token_conn():
    """打开 esi_tokens 所在连接。

    这里再执行一次 DDL 兜底：`sqlite3.connect` 会创建空库文件，全新安装时
    user.db 可能晚于启动时的 schema 迁移才出现（`ensure_all_schemas` 对不存在的
    库直接跳过）。DDL 是 `CREATE TABLE IF NOT EXISTS`，幂等。
    """
    from core.container import get_container
    from services.schema_migrations import ESI_TOKENS_SQL

    conn = get_container().db.direct_connect("user")
    conn.executescript(ESI_TOKENS_SQL)
    return conn


#: 绑定行的列（`load_token_row` / `list_token_rows` 共用，避免两处漂移）
_TOKEN_COLUMNS = "character_id, character_name, refresh_token, access_token, access_expires_at"


def load_token_row(character_name: str) -> dict | None:
    """按角色名取绑定行（token 按角色绑定，名字是配置里的匹配键）。"""
    conn = _token_conn()
    try:
        row = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM esi_tokens WHERE character_name = ?",
            (character_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_token_rows() -> list[dict]:
    """全部已绑定角色 —— ESI 钱包/挂单同步要遍历每个角色各拉一份。"""
    conn = _token_conn()
    try:
        rows = conn.execute(f"SELECT {_TOKEN_COLUMNS} FROM esi_tokens ORDER BY character_name").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_token_row(
    character_id: int,
    character_name: str,
    refresh_token: str,
    access_token: str,
    access_expires_at: str,
) -> None:
    """写入/更新绑定行。

    **每次刷新后都必须原样保存响应里的 refresh_token** —— CCP 文档明说返回的
    可能和提交的不同（会启用轮换），只留第一次那个迟早失效，用户就得重新授权。
    """
    conn = _token_conn()
    try:
        conn.execute(
            "INSERT INTO esi_tokens"
            " (character_id, character_name, refresh_token, access_token, access_expires_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))"
            " ON CONFLICT(character_id) DO UPDATE SET"
            "   character_name = excluded.character_name,"
            "   refresh_token = excluded.refresh_token,"
            "   access_token = excluded.access_token,"
            "   access_expires_at = excluded.access_expires_at,"
            "   updated_at = excluded.updated_at",
            (character_id, character_name, refresh_token, access_token, access_expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def delete_token_row(character_id: int) -> None:
    """授权被撤销时清掉这一行（留着只会每次刷新都失败）。"""
    conn = _token_conn()
    try:
        conn.execute("DELETE FROM esi_tokens WHERE character_id = ?", (character_id,))
        conn.commit()
    finally:
        conn.close()


# ── PKCE / JWT ──


def _pkce_pair() -> tuple[str, str]:
    """生成 (code_verifier, code_challenge)，与官方 PKCE 示例一致。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _jwt_claims(access_token: str) -> dict:
    """解出 JWT 载荷，**不验签**。

    不验签的前提：令牌是刚经 TLS 从曙光 token 端点直接拿到的，没有第三方注入路径。
    但仍校验 iss / exp / aud（`_character_from_claims`），防止把别的服务器或别的应用的
    令牌当成本应用的身份。
    """
    try:
        seg = access_token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg))
    except (IndexError, ValueError):
        # 吞的是「不是 JWT / base64 或 JSON 损坏」：按无身份处理，由调用方报重新授权
        return {}
    return claims if isinstance(claims, dict) else {}


def _check_claims(claims: dict, now_ts: float | None = None) -> None:
    """校验曙光签发方、有效期与客户端声明；声明检查不等同于签名验证。"""
    iss = str(claims.get("iss") or "")
    if iss.removeprefix("https://").rstrip("/") != SSO_ISSUER:
        raise RuntimeError("令牌不是曙光登录服务签发的，请重新授权")
    exp = claims.get("exp")
    now = time.time() if now_ts is None else now_ts
    if not isinstance(exp, int | float) or exp <= now:
        raise RuntimeError("令牌已过期，请重新授权")
    # 2026-11-16 实测曙光令牌：iss=login-infinity.evepc.163.com、azp=<client_id>、tenant=infinity、
    # 无 aud。所以客户端归属看 azp；带了 tenant 就必须是 infinity（挡住晨曦 serenity 令牌）。
    azp = claims.get("azp")
    if not CLIENT_ID or (azp is not None and azp != CLIENT_ID):
        raise RuntimeError("令牌不属于本应用，请重新授权")
    tenant = claims.get("tenant")
    if tenant is not None and tenant != "infinity":
        raise RuntimeError(f"令牌属于 {tenant} 服务器，不是曙光，请重新授权")


def _character_from_claims(claims: dict, fallback_id: int | None, fallback_name: str | None) -> tuple[int, str]:
    """`sub` 形如 `CHARACTER:<租户>:<id>`（国际服租户是 EVE；曙光的租户名以实测为准，只要求 CHARACTER 前缀）。"""
    sub = str(claims.get("sub") or "")
    char_id: int | None = fallback_id
    if sub.startswith("CHARACTER:"):
        tail = sub.rsplit(":", 1)[-1]
        if tail.isdigit():
            char_id = int(tail)
    char_name = str(claims.get("name") or fallback_name or "")
    if char_id is None or not char_name:
        raise RuntimeError("无法从令牌里确定角色身份，请重新授权")
    return int(char_id), char_name


# ── 回跳地址解析 ──


def parse_redirect(pasted: str, expected_state: str) -> str:
    """校验用户粘贴的回跳地址并取出授权码。

    官方回调页把参数放在 query（授权码模式）或 fragment（隐式模式）里，两处都看。
    拒绝条件（任一即抛 RuntimeError，文案直接给用户看）：
    - 不是官方回调页（主机或路径不符）—— 防止把别的站点的 code 喂进来；
    - 带 `error` —— 用户在网易页面上点了拒绝；
    - `state` 与本次发起的不一致 —— CSRF / 粘了上一次的旧地址；
    - 没有 `code`。
    """
    text = (pasted or "").strip()
    if not text:
        raise RuntimeError("没有粘贴回跳地址")
    parsed = urlparse(text)
    expected = urlparse(CALLBACK_URL)
    if (parsed.scheme, parsed.netloc.lower(), parsed.path) != (expected.scheme, expected.netloc, expected.path):
        raise RuntimeError(
            "粘贴的不是网易授权回跳页地址（应以 https://ali-esi.evepc.163.com/ui/oauth2-redirect.html 开头）"
        )
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    params.update({k: v[0] for k, v in parse_qs(parsed.fragment).items()})
    if params.get("error"):
        raise RuntimeError(f"授权被拒绝：{params.get('error_description') or params['error']}")
    if params.get("state") != expected_state:
        raise RuntimeError("回跳地址的 state 与本次授权不一致（可能粘贴了旧地址），已中止")
    code = params.get("code")
    if not code:
        raise RuntimeError("回跳地址里没有授权码")
    return str(code)


async def _fetch_device_id(session: aiohttp.ClientSession) -> str:
    """向网易申请一次性设备 ID（授权地址必带）。失败抛 RuntimeError（文案给用户看）。"""
    query = {
        "game_id": DEVICE_GAME_ID,
        "device_type": "PC",
        "system_name": "Windows",
        "system_version": "10",
        "resolution": "1920*1080",
        "device_model": "x86_64",
    }
    headers = {"Origin": DEVICE_ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}
    try:
        async with session.get(DEVICE_INIT_URL, params=query, headers=headers, timeout=_TIMEOUT) as resp:
            text = await resp.text()
            status = resp.status
    except (TimeoutError, aiohttp.ClientError) as e:
        raise RuntimeError(f"网易登录初始化失败（网络）：{e}") from e
    try:
        device_id = (json.loads(text).get("device") or {}).get("id")
    except (ValueError, AttributeError):
        device_id = None
    if status != 200 or not device_id:
        raise RuntimeError(f"网易登录初始化失败（HTTP {status}），请稍后重试")
    return str(device_id)


def _authorize_url(code_challenge: str, state: str, extra_scopes: tuple[str, ...] = (), *, device_id: str = "") -> str:
    """浏览器要打开的地址：先经网易 `/account/logoff` 登出残留会话，再回跳授权页。

    - `device_id`：网易登录必带（见 `DEVICE_INIT_URL`）。
    - 先登出：浏览器里残留的网易登录态会让授权页报 `Missing Netease token`，
      现有曙光工具（eve.navexor.top、ceve-market）都是这样绕过的。
    - `extra_scopes` 给「按开关才要的权限」用；默认只要 `SCOPES`。
    """
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "scope": " ".join((*SCOPES, *extra_scopes)),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "realm": "ESI",  # 网易 SSO 的授权页参数（官方文档页 configureAuth 里写的就是它）
    }
    if device_id:
        params["device_id"] = device_id
    authorize_path = "/v2/oauth/authorize?" + urlencode(params)
    sso_root = AUTHORIZE_URL.removesuffix("/v2/oauth/authorize")
    return f"{sso_root}/account/logoff?{urlencode({'returnUrl': authorize_path})}"


# ── HTTP ──


async def _post_token(client, data: dict) -> dict:
    """POST 到 token 端点。

    注意：OAuth 的 token 端点要 **form 编码**，`APIClient.post` 发的是 JSON 体，
    所以这里直接用 session 发，只用它的共享限流器。

    **5xx 会退避重试**（`_TOKEN_RETRY_BASE_S` × 轮次，最多 `_TOKEN_RETRIES` 次）。
    原因（用户实测）：CCP 的 token 端点偶尔整段返回 500，三个角色一起失败，而调用方
    把任何非 `invalid_grant` 都当「刷新授权失败」上报 —— 用户读到的是「我的授权坏了」，
    会去删令牌/重新授权，白折腾。**授权没坏，是服务端瞬时故障**，重试一次多半就过。
    4xx 不重试：那是请求/授权本身的问题，重试没有意义。
    """
    last_status = 0
    for attempt in range(_TOKEN_RETRIES):
        await client.limiter.acquire()
        async with client.session.post(TOKEN_URL, data=data, timeout=_TIMEOUT) as resp:
            text = await resp.text()
            try:
                payload = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                payload = {}
            if resp.status == 200:
                return payload if isinstance(payload, dict) else {}
            last_status = resp.status
            if resp.status < 500 or attempt + 1 >= _TOKEN_RETRIES:
                raise _TokenError(
                    str(payload.get("error") or ""), str(payload.get("error_description") or ""), resp.status
                )
        # 退避要放在 `async with` **之外**：连接已经关掉，别把等待算进请求时间
        await asyncio.sleep(_TOKEN_RETRY_BASE_S * (attempt + 1))
    raise _TokenError("", "", last_status)  # pragma: no cover - 上面的循环必然 return 或 raise


async def _get_json(client, url: str, token: str, *, scope_hint: str = "技能/增效体"):
    """带 Bearer 的 GET。自己看状态码 —— `APIClient.fetch` 把 401/403 都吞成 None，
    而我们必须把「授权失效」和「缺 scope」分开告诉用户。

    `scope_hint` 决定 403 的文案。**必须按接口传**：写死「技能/增效体」的话，
    钱包/挂单 403 时会把用户引去勾错的权限，怎么试都不成功。
    """
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(2):
        await client.limiter.acquire()
        async with client.session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status == 401:
                raise EsiAuthRevoked("授权已失效，请重新授权")
            if resp.status == 403:
                raise EsiScopeMissing(f"授权缺少{scope_hint}权限，请在开发者应用勾选对应 scope 后重新授权")
            if resp.status == 429 and attempt == 0:
                retry_after = resp.headers.get("Retry-After", "5")
                await asyncio.sleep(min(int(retry_after) if retry_after.isdigit() else 5, 60))
                continue
            resp.raise_for_status()
            text = await resp.text()
            return json.loads(text) if text.strip() else None


# ── 名字解析 ──


def _resolve_skill_names(skill_ids: list[int]) -> dict[int, str]:
    """skill_id → 中文名（ESI 的 skill_id 就是 SDE 的 type_id）。

    复用既有批量解析。**它要的是 reference.db 连接**，传 user 连接会 no such table: item。
    未命中时它回退返回 `str(type_id)`，所以纯数字串就是「没匹配上」。
    """
    if not skill_ids:
        return {}
    from core.container import get_container
    from services.name_resolver import resolve_item_names_batch

    conn = get_container().db.direct_connect("ref")
    try:
        return resolve_item_names_batch(conn, skill_ids)
    finally:
        conn.close()


class EsiSkillImportWorker(QThread):
    """拉取一个角色的技能 / 技能队列 / 植入体。

    产出 `result_signal` 的载荷：`{character_id, character_name, skills: {中文名: 等级}, implants: [type_id]}`。
    **worker 不写 char_config** —— 只回数据，由主线程的桥合并落盘，保证配置只有一个写者。
    """

    finished_signal = Signal(bool, str)  # success, 错误信息
    result_signal = Signal(dict)
    #: 浏览器已打开授权页，请主线程弹框让用户粘贴回跳地址（参数 = 授权页 URL，供「重新打开」）。
    #: 主线程拿到文本后调 `submit_redirect()`；用户取消则调 `submit_redirect(None)`。
    redirect_requested = Signal(str)

    def __init__(self, character_name: str | None = None, *, force_browser: bool = False, parent=None) -> None:
        super().__init__(parent)
        #: 当前选中的配置角色名。有绑定就走静默刷新，没有才开浏览器。
        self._character_name = character_name
        #: 「添加角色」用：**总是**开浏览器让用户选角色。
        #: 少了这个开关，导完 A 之后 current 变成 A，再点就命中 A 的绑定静默刷 A，
        #: 用户永远没机会选 B（一账号多角色的常见场景）。
        self._force_browser = force_browser
        #: 主线程交回的粘贴内容（None = 用户取消）；Event 置位表示已交回。
        self._redirect_text: str | None = None
        self._redirect_ready = threading.Event()

    def submit_redirect(self, text: str | None) -> None:
        """主线程调用：交回用户粘贴的回跳地址（None = 取消）。线程安全。"""
        self._redirect_text = text
        self._redirect_ready.set()

    # ── 线程体 ──

    def run(self) -> None:
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            payload = loop.run_until_complete(self._import())
            # 被请求中断时不再发信号：接收方的 C++ 对象可能已析构，emit 会踩空
            if self.isInterruptionRequested():
                return
            self.result_signal.emit(payload)
            self.finished_signal.emit(True, "")
        except _Interrupted:
            return
        except (TimeoutError, aiohttp.ClientError) as e:
            log.warning("ESI 导入网络失败: %s", e)
            if not self.isInterruptionRequested():
                self.finished_signal.emit(False, "网络不可用，请稍后重试")
        except Exception as e:
            log.exception("ESI 技能导入失败")
            if not self.isInterruptionRequested():
                self.finished_signal.emit(False, str(e) or "导入失败")
        finally:
            if loop is not None:
                loop.close()

    async def _import(self) -> dict:
        from services.client import APIClient

        async with APIClient(timeout=30) as client:
            access, char_id, char_name = await self._obtain_token(client)
            if self.isInterruptionRequested():
                raise _Interrupted()
            return await self._pull(client, access, char_id, char_name)

    # ── 令牌 ──

    def _extra_scopes(self) -> tuple[str, ...]:
        """「带开关的可选能力」按需追加的 scope。目前只有军团钱包一项。

        为什么钩子开在**基类**而不是钱包 worker 上：重新授权的入口是人物设置对话框
        （走的就是本类），而钱包 worker 走 `allow_browser=False`、压根不开浏览器。
        两边都只做子类钩子的话，这个 scope **任何路径都拿不到**，功能直接不可达
        —— 实测踩过：门户勾了权限，重新授权却不带上它。

        也正因为是「按开关追加」而不是塞进 `SCOPES`：没开开关的人不必为此多授权一次，
        且门户没勾该 scope 时授权会 `invalid_scope`，把整条 ESI 链路一起弄挂。
        """
        from services.user_settings import get_include_corp_wallet

        return (CORP_WALLET_SCOPE,) if get_include_corp_wallet() else ()

    async def _obtain_token(
        self, client, character_name: str | None = None, *, allow_browser: bool = True
    ) -> tuple[str, int, str]:
        """有可用绑定就静默刷新，否则开浏览器授权。`force_browser` 时跳过静默路径。

        `character_name` 省略时用构造期那个（`EsiSkillImportWorker` 的原行为）；
        多角色同步的调用方逐个显式传入 —— 这样不必把这段逻辑复制第二遍。
        `allow_browser=False` 时**不弹浏览器**，直接按授权失效报错：批量同步里
        一个角色掉线不该让用户连着走三次授权流程（每次最长等 10 分钟）。
        """
        if not CLIENT_ID:
            raise RuntimeError("曙光角色授权尚未启用：本版本还没有配置曙光 SSO 客户端 ID")
        name = character_name or self._character_name
        if name and not self._force_browser:
            row = load_token_row(name)
            if row:
                # 刷新前也核对来源；误复制来的国际服 token 不得发送给曙光服务。
                _check_claims(_jwt_claims(str(row.get("access_token") or "")), now_ts=0)
                if self._still_valid(row.get("access_expires_at")) and row.get("access_token"):
                    _check_claims(_jwt_claims(str(row["access_token"])))
                    return str(row["access_token"]), int(row["character_id"]), str(row["character_name"])
                try:
                    return await self._refresh(client, row)
                except _TokenError as e:
                    if e.status >= 500:
                        # 5xx：CCP 的登录服务故障（`_post_token` 已退避重试过）。
                        # **授权本身没坏** —— 文案必须说清楚，否则用户会去删令牌、重新
                        # 授权，白折腾一圈还是不通（用户截图里的三个角色一起 500 就是这个）。
                        raise RuntimeError(
                            f"ESI 登录服务暂时不可用（HTTP {e.status}），你的授权没有失效，请稍后重试"
                        ) from e
                    if e.code != "invalid_grant":
                        raise RuntimeError(f"刷新授权失败：{e}") from e
                    # invalid_grant = 用户在 support 站点撤销了（或令牌已作废）。
                    # 删掉死行，然后**直接接着走浏览器授权** —— 用户点的就是「导入」，
                    # 这里报个错让他再点一次没有意义。
                    log.info("ESI 授权已撤销，转入重新授权: %s", row.get("character_name"))
                    delete_token_row(int(row["character_id"]))
        if not allow_browser:
            raise EsiAuthRevoked(f"{name or '该角色'} 的绑定已失效，请重新授权")
        return await self._browser_authorize(client)

    @staticmethod
    def _still_valid(expires_at: str | None) -> bool:
        """同格式 ISO8601 UTC 串，字典序即时间序；留 `_EXPIRY_MARGIN_S` 裕量。"""
        if not expires_at:
            return False
        deadline = datetime.now(UTC) + timedelta(seconds=_EXPIRY_MARGIN_S)
        return expires_at > deadline.strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _refresh(self, client, row: dict) -> tuple[str, int, str]:
        payload = await _post_token(
            client,
            {
                "grant_type": "refresh_token",
                "refresh_token": str(row["refresh_token"]),
                "client_id": CLIENT_ID,
                "redirect_uri": CALLBACK_URL,  # 现有曙光工具刷新时同样带上（网易 SSO 的要求）
            },
        )
        return self._persist(
            payload,
            previous_refresh=str(row["refresh_token"]),
            fallback_id=int(row["character_id"]),
            fallback_name=str(row["character_name"]),
        )

    async def _browser_authorize(self, client) -> tuple[str, int, str]:
        if not CLIENT_ID:
            raise RuntimeError("曙光角色授权尚未启用：本版本还没有配置曙光 SSO 客户端 ID")
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        device_id = await _fetch_device_id(client.session)
        url = _authorize_url(challenge, state, self._extra_scopes(), device_id=device_id)
        self._redirect_ready.clear()
        self._redirect_text = None
        webbrowser.open(url)
        self.redirect_requested.emit(url)
        code = parse_redirect(self._await_redirect(), state)
        try:
            payload = await _post_token(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": CLIENT_ID,
                    "code_verifier": verifier,
                    # 网易换令牌要求带回调地址（与授权请求里的完全一致）
                    "redirect_uri": CALLBACK_URL,
                },
            )
        except _TokenError as e:
            raise RuntimeError(f"换取令牌失败：{e}") from e
        return self._persist(payload)

    def _await_redirect(self) -> str:
        """阻塞等主线程交回粘贴内容；0.5s 一查中断（`detach_worker` 只等 500ms）。"""
        deadline = time.monotonic() + _AUTH_TIMEOUT_S
        while not self._redirect_ready.wait(0.5):
            if self.isInterruptionRequested():
                raise _Interrupted()
            if time.monotonic() > deadline:
                raise RuntimeError(f"授权超时（{_AUTH_TIMEOUT_S} 秒内没有粘贴回跳地址），请重试")
        if self._redirect_text is None:
            raise _Interrupted()  # 用户在粘贴框点了取消：不算错误
        return self._redirect_text

    def _persist(
        self,
        payload: dict,
        *,
        previous_refresh: str | None = None,
        fallback_id: int | None = None,
        fallback_name: str | None = None,
    ) -> tuple[str, int, str]:
        access = str(payload.get("access_token") or "")
        if not access:
            raise RuntimeError("token 端点没有返回 access_token")
        # 刷新响应可能不带 refresh_token（官方说「返回的可能与提交的不同」，没说一定返回）
        refresh = str(payload.get("refresh_token") or previous_refresh or "")
        if not refresh:
            raise RuntimeError("token 端点没有返回 refresh_token，无法保存授权")
        expires_at = _utc_after(int(payload.get("expires_in") or _DEFAULT_EXPIRES_IN))
        claims = _jwt_claims(access)
        _check_claims(claims)
        char_id, char_name = _character_from_claims(claims, fallback_id, fallback_name)
        save_token_row(char_id, char_name, refresh, access, expires_at)
        return access, char_id, char_name

    # ── 拉数 ──

    async def _pull(self, client, access: str, char_id: int, char_name: str) -> dict:
        from core.char_settings_common import apply_skill_queue_finished

        base = f"/characters/{char_id}"
        skills_raw = await _get_json(client, esi_url(f"{base}/skills/"), access) or {}
        queue_raw = await _get_json(client, esi_url(f"{base}/skillqueue/"), access) or []
        implants_raw = await _get_json(client, esi_url(f"{base}/implants/"), access) or []

        levels: dict[int, int] = {}
        for entry in skills_raw.get("skills", []) if isinstance(skills_raw, dict) else []:
            skill_id = int(entry.get("skill_id") or 0)
            if skill_id:
                # active_skill_level = 当前**生效**等级（制造公式吃的是这个）；
                # trained_skill_level 是已训练等级，两者仅在个别情形下不同。
                levels[skill_id] = int(entry.get("active_skill_level") or entry.get("trained_skill_level") or 0)

        # `/skills` 对角色离线期间练完的技能是过期的，必须叠加队列里已完成的条目
        if isinstance(queue_raw, list):
            levels = apply_skill_queue_finished(levels, queue_raw, _utc_now())

        names = _resolve_skill_names(list(levels))
        esi_skills = {name: levels[sid] for sid, name in names.items() if not name.isdigit()}

        implants = [int(t) for t in implants_raw if isinstance(t, int)] if isinstance(implants_raw, list) else []
        return {
            "character_id": char_id,
            "character_name": char_name,
            "skills": esi_skills,
            "implants": implants,
        }
