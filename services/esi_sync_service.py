"""ESI 资产 / 工业作业同步的落地（重构阶段 6）：把 worker 拉回来的数据接到现有的库存审阅与计划状态上。

调用方：仓库页「从 ESI 同步资产」与产线启动小助手「从 ESI 对账作业」（`ui_qml/bridge/inventory_bridge.py`、
`ui_qml/views/industry/production_launcher.py`）。纯逻辑在 `domain.esi_sync`。
"""

from __future__ import annotations

from typing import Any

from core.constants import PLAN_LOCKED_STATUSES
from core.container import get_container
from domain.esi_sync import group_assets_by_location, match_jobs, plan_job_summary


def location_choices(payload: dict) -> list[dict]:
    """同步结果 → 可选的位置 `[{location_id, name, types, units}]`（按物品种数降序）。

    多个角色在同一空间站的资产合并（一个机库对应一个游戏位置）。
    """
    merged: dict[int, dict[int, int]] = {}
    for rows in (payload.get("assets") or {}).values():
        for loc, per in group_assets_by_location(rows).items():
            bucket = merged.setdefault(loc, {})
            for tid, q in per.items():
                bucket[tid] = bucket.get(tid, 0) + q
    names = payload.get("locations") or {}
    out = [
        {
            "location_id": loc,
            "name": names.get(loc, f"位置 {loc}"),
            "types": len(per),
            "units": sum(per.values()),
            "items": per,
        }
        for loc, per in merged.items()
    ]
    out.sort(key=lambda x: (-x["types"], x["name"]))
    return out


def review_items(per_type: dict[int, int]) -> list[dict]:
    """某位置的 `{type_id: 数量}` → 导入预览对话框的行（与剪贴板解析同形：type_id / raw_name / zh_name / en_name / qty / status）。"""
    from services.name_resolver import resolve_item_name

    rows: list[dict] = []
    with get_container().db.connect("ref") as conn:
        for tid, qty in sorted(per_type.items()):
            r = conn.execute("SELECT zh_name, en_name FROM item WHERE type_id=?", (tid,)).fetchone()
            name = resolve_item_name(conn, tid)
            rows.append(
                {
                    "type_id": tid,
                    "raw_name": name,
                    "zh_name": (r[0] if r else "") or name,
                    "en_name": (r[1] if r else "") or "",
                    "qty": int(qty),
                    "status": "matched",
                }
            )
    return rows


def reconcile_jobs(payload: dict) -> dict[str, Any]:
    """ESI 进行中作业 ↔ 本地在产计划 → `{matches, summary, unmatched, ready_ids}`。

    `ready_ids`：ESI 说该计划**全部线**都已完成（ready / delivered），本地却还在「生产中」的计划 ——
    调用方可把它们置为「待下线」（与 `plan_execution.expire_overdue_plans` 同一状态）。
    """
    with get_container().db.connect("user") as conn:
        cur = conn.execute(
            f"SELECT * FROM production_plans WHERE status IN ({','.join('?' for _ in PLAN_LOCKED_STATUSES)})",
            PLAN_LOCKED_STATUSES,
        )
        cols = [d[0] for d in cur.description]
        plans = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    matches = match_jobs(payload.get("jobs") or [], plans, char_names=payload.get("char_names") or {})
    summary = plan_job_summary(matches)
    by_id = {int(p["id"]): p for p in plans}
    ready_ids = [
        pid
        for pid, s in summary.items()
        if s["finished"] >= max(int(by_id[pid].get("parallels") or 1), 1)
        and (by_id[pid].get("status") or "").lower() != "ready"
    ]
    return {
        "matches": matches,
        "summary": summary,
        "unmatched": [m for m in matches if m.plan_id is None],
        "ready_ids": ready_ids,
        "plans": by_id,
    }


def mark_ready(plan_ids: list[int]) -> int:
    """把 ESI 已完成的计划置为「待下线」（只动在产行），返回改动数。"""
    if not plan_ids:
        return 0
    from core.constants import PLAN_RUNNING_STATUSES

    ph = ",".join("?" for _ in plan_ids)
    sp = ",".join("?" for _ in PLAN_RUNNING_STATUSES)
    with get_container().db.connect("user") as conn:
        cur = conn.execute(
            f"UPDATE production_plans SET status='ready' WHERE id IN ({ph}) AND status IN ({sp})",
            [*plan_ids, *PLAN_RUNNING_STATUSES],
        )
        return int(cur.rowcount)
