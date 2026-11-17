"""ESI 资产 + 进行中工业作业：一次拉**全部已绑定角色**（重构阶段 6）。

继承 `esi_skill_worker.EsiSkillImportWorker`，**只重写 `_import()`**（同 `esi_wallet_worker`）：
令牌获取 / 静默刷新 / 过期与撤销处理全部复用基类，批量路径不弹浏览器（`allow_browser=False`）。

载荷（`result_signal`）：
- `assets`：`{角色 id: [ESI 资产行]}`（已翻完 `X-Pages` 全部页，实测 凋零 3 页 × 1000 条）；
- `locations`：`{位置 id: 显示名}`（NPC 空间站查 reference.db；玩家建筑走 `/universe/structures/{id}/`，
  要 `esi-universe.read_structures.v1`，未授权时显示「建筑 <id>」）；
- `jobs`：全部角色的进行中作业（`include_completed=false`）；
- `char_names`：`{角色 id: 名字}`（作业 `installer_id` → 人物名，对账用）；
- `errors`：单角色失败的说明，不影响其余角色。
"""

from __future__ import annotations

from core.constants import esi_url
from core.logger import log
from ui_qml.workers.esi_skill_worker import (
    EsiAuthRevoked,
    EsiScopeMissing,
    EsiSkillImportWorker,
    _get_json,
    _Interrupted,
    list_token_rows,
)

#: 资产接口每页 1000 条；保险上限（防异常的 X-Pages 让循环跑飞）
_MAX_PAGES = 50


class EsiIndustrySyncWorker(EsiSkillImportWorker):
    """一次性线程：逐角色拉资产（全部页）与进行中工业作业。"""

    async def _import(self) -> dict:
        from services.client import APIClient

        rows = list_token_rows()
        if not rows:
            raise RuntimeError("还没有绑定任何角色 —— 请先在人物设置里「从 ESI 添加角色」")
        assets: dict[int, list] = {}
        jobs: list[dict] = []
        char_names: dict[int, str] = {}
        errors: list[str] = []
        async with APIClient(timeout=30) as client:
            for row in rows:
                name = str(row.get("character_name") or "")
                try:
                    access, char_id, char_name = await self._obtain_token(client, name, allow_browser=False)
                    if self.isInterruptionRequested():
                        raise _Interrupted()
                    assets[int(char_id)] = await self._pull_assets(client, access, int(char_id))
                    char_jobs = await _get_json(
                        client,
                        esi_url(f"/characters/{char_id}/industry/jobs/", include_completed="false"),
                        access,
                        scope_hint="工业作业",
                    )
                    jobs.extend(dict(j) for j in char_jobs or [])
                    char_names[int(char_id)] = char_name
                except _Interrupted:
                    raise
                except EsiScopeMissing:
                    errors.append(f"{name}：授权里没有资产 / 工业作业权限 —— 请在人物设置里点「+ 从 ESI」重新授权一次")
                except EsiAuthRevoked as e:
                    errors.append(f"{name}：{e}")
                except Exception as e:
                    # 吞的是「这一个角色拉取失败」（网络超时、单条接口 5xx）：其余角色照常
                    log.exception("ESI 资产/作业同步失败 name=%s", name)
                    errors.append(f"{name}：{e}")
            if not char_names:
                raise RuntimeError("；".join(errors) or "没有可同步的角色")
            location_ids = {int(a.get("location_id") or 0) for rows_ in assets.values() for a in rows_}
            locations = await self._location_names(client, location_ids, assets)
        return {"assets": assets, "jobs": jobs, "char_names": char_names, "locations": locations, "errors": errors}

    async def _pull_assets(self, client, access: str, char_id: int) -> list:
        """翻完全部页（`X-Pages`）。`_get_json` 不回响应头，所以按「不满 1000 条即最后一页」判停。"""
        out: list = []
        for page in range(1, _MAX_PAGES + 1):
            chunk = await _get_json(
                client,
                esi_url(f"/characters/{char_id}/assets/", page=page),
                access,
                scope_hint="资产",
            )
            chunk = list(chunk or [])
            out.extend(chunk)
            if len(chunk) < 1000:
                break
        return out

    async def _location_names(self, client, ids: set[int], assets: dict[int, list]) -> dict[int, str]:
        """位置 id → 显示名。只解析**顶层**位置（不是某个物品的 item_id）。"""
        from core.container import get_container

        item_ids = {int(a.get("item_id") or 0) for rows_ in assets.values() for a in rows_}
        top = sorted(i for i in ids if i and i not in item_ids)
        names: dict[int, str] = {}
        with get_container().db.connect("ref") as conn:
            for sid in top:
                r = conn.execute("SELECT station_name FROM station WHERE station_id=?", (sid,)).fetchone()
                if r and r[0]:
                    names[sid] = str(r[0])
        for sid in top:
            if sid in names:
                continue
            names[sid] = f"建筑 {sid}" if sid > 1_000_000_000_000 else f"位置 {sid}"
        return names
