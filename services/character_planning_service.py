"""人物规划装配（重构阶段 5）：计划行 + 人物配置 + 蓝图技能要求 → `domain.character_planning`。

调用方：人物占用对话框的「自动派人 / 人物时间线」（`ui_qml/bridge/char_usage_bridge.py`）
与「执行派人」写回（同文件）。算法全在 domain，这里只取数与落库。
"""

from __future__ import annotations

from collections.abc import Iterable

from core.constants import PLAN_RUNNING_STATUSES, PLAN_STATUS_PENDING
from core.container import get_container
from domain.character_planning import Character, Job, assign, schedule
from services.char_capacity import LINE_TYPES, capacity_line_for_category, max_lines_for_category
from services.char_config_resolver import get_character_list, load_all_data

#: 计划 activity → blueprint_skills 的活动名（科研行要的是输入蓝图的科研技能）
SKILL_ACTIVITY = {
    "manufacturing": "manufacturing",
    "reaction": "reaction",
    "copying": "copying",
    "invention": "invention",
    "researching_material_efficiency": "research_material",
    "researching_time_efficiency": "research_time",
}


def required_skills(blueprint_ids: Iterable[int], *, db=None) -> dict[tuple[int, str], dict[str, int]]:
    """{(蓝图, 活动): {技能中文名: 等级}}（技能名与 char_config.json 的 skills 键同源：item.zh_name）。"""
    ids = sorted({int(b) for b in blueprint_ids if b})
    if not ids:
        return {}
    db = db or get_container().db
    out: dict[tuple[int, str], dict[str, int]] = {}
    with db.connect("ref", "bp") as conn:
        ph = ",".join("?" * len(ids))
        for bp, act, sid, lvl, zh, en in conn.execute(
            "SELECT bs.blueprint_type_id, bs.activity, bs.skill_type_id, bs.level, i.zh_name, i.en_name "
            f"FROM blueprint_skills bs LEFT JOIN item i ON i.type_id = bs.skill_type_id WHERE bs.blueprint_type_id IN ({ph})",
            ids,
        ):
            out.setdefault((int(bp), str(act)), {})[str(zh or en or sid)] = int(lvl or 0)
    return out


def _plan_hours(p: dict) -> float:
    """单线时长（小时）：`calculated_time` 是整条计划（单线 runs 个流程）的秒数。"""
    secs = float(p.get("calculated_time") or 0)
    return secs / 3600 if secs > 0 else 2.0 * max(int(p.get("runs") or 1), 1)


def build_inputs(plans: list[dict], *, db=None) -> tuple[list[Job], list[Character], dict[int, dict]]:
    """计划行（需已 enrich `category`）→ (待排作业, 人物, {job_id: 计划行})。

    待排 = 待生产行；在产行只用来占人物的槽位（`busy`）并作为依赖的已知结束时间（剩余时长）。
    """
    from services.plan_execution import remaining_seconds

    data = (load_all_data() or {}).get("characters", {}) or {}
    names = list(get_character_list())
    busy: dict[str, dict[str, int]] = {}
    for p in plans:
        if (p.get("status") or "").lower() in PLAN_RUNNING_STATUSES and p.get("char_name"):
            lt = capacity_line_for_category(str(p.get("category") or ""))
            per = busy.setdefault(str(p["char_name"]), {})
            per[lt] = per.get(lt, 0) + max(int(p.get("parallels") or 1), 1)
    chars = [
        Character(
            n,
            (data.get(n, {}) or {}).get("skills", {}) or {},
            {
                lt: max_lines_for_category(n, lt, skills=(data.get(n, {}) or {}).get("skills", {}) or {})
                for lt in LINE_TYPES
            },
            busy.get(n, {}),
        )
        for n in names
    ]
    pending = [p for p in plans if (p.get("status") or "").lower() == PLAN_STATUS_PENDING and p.get("id")]
    reqs = required_skills([int(p.get("blueprint_type_id") or 0) for p in pending], db=db)
    by_group_tid: dict[tuple[int, int], int] = {}
    for p in plans:
        g = int(p.get("group_number") or p.get("group_id") or 0)
        if g and p.get("product_type_id") and (p.get("status") or "").lower() not in ("completed", "done"):
            by_group_tid.setdefault((g, int(p["product_type_id"])), int(p["id"]))
    jobs: list[Job] = []
    rows: dict[int, dict] = {}
    for p in pending:
        pid = int(p["id"])
        g = int(p.get("group_number") or p.get("group_id") or 0)
        level = int(p.get("sub_level") or p.get("child_level") or 0)
        deps: list[int] = []
        if g:
            for q in plans:
                if q is p or int(q.get("group_number") or q.get("group_id") or 0) != g:
                    continue
                qlevel = int(q.get("sub_level") or q.get("child_level") or 0)
                parent = q.get("component_parent_type_id")
                is_child = (parent and int(parent) == int(p.get("product_type_id") or 0)) or (level == 0 and qlevel > 0)
                if is_child and (q.get("status") or "").lower() not in ("completed", "done") and q.get("id"):
                    deps.append(int(q["id"]))
        act = SKILL_ACTIVITY.get(str(p.get("activity") or "manufacturing"), "manufacturing")
        jobs.append(
            Job(
                pid,
                capacity_line_for_category(str(p.get("category") or "")),
                max(int(p.get("parallels") or 1), 1),
                _plan_hours(p),
                tuple(deps),
                str(p.get("char_name") or ""),
                reqs.get((int(p.get("blueprint_type_id") or 0), act), {}),
            )
        )
        rows[pid] = p
    # 在产行也进排程（作为占槽的已知作业 + 依赖终点），时长取剩余
    for p in plans:
        if (p.get("status") or "").lower() in PLAN_RUNNING_STATUSES and p.get("id"):
            rem = remaining_seconds(p)
            jobs.append(
                Job(
                    int(p["id"]),
                    capacity_line_for_category(str(p.get("category") or "")),
                    max(int(p.get("parallels") or 1), 1),
                    max((rem or 0) / 3600, 0.0),
                    (),
                    str(p.get("char_name") or ""),
                    started=True,
                )
            )
            rows[int(p["id"])] = p
    return jobs, chars, rows


def plan_assignments(plans: list[dict], *, db=None) -> dict:
    """自动派人 + 槽位排程 → 展示数据。

    Returns {"assign": {plan_id: (人物, 说明)}, "slots": [Slot], "rows": {plan_id: 计划行}, "chars": [人物名]}
    在产行不改派（保留原人物），只参与排程。
    """
    jobs, chars, rows = build_inputs(plans, db=db)
    running_ids = {j.job_id for j in jobs if (rows[j.job_id].get("status") or "").lower() in PLAN_RUNNING_STATUSES}
    pending_jobs = [j for j in jobs if j.job_id not in running_ids]
    # 在产作业占的槽位已经在 Character.busy 里，assign 不再重复计
    result = assign(pending_jobs, chars)
    for jid in running_ids:
        result[jid] = (rows[jid].get("char_name") or "", "生产中")
    slots = schedule(jobs, chars, {jid: name for jid, (name, _n) in result.items()})
    return {"assign": result, "slots": slots, "rows": rows, "chars": [c.name for c in chars]}


def apply_assignments(assignments: dict[int, str]) -> int:
    """把派人结果写回 `production_plans.char_name`（只改待生产行），返回改动行数。"""
    if not assignments:
        return 0
    n = 0
    with get_container().db.connect("user") as conn:
        for pid, name in assignments.items():
            if not name:
                continue
            cur = conn.execute(
                "UPDATE production_plans SET char_name=? WHERE id=? AND status=? AND COALESCE(char_name,'')<>?",
                (name, int(pid), PLAN_STATUS_PENDING, name),
            )
            n += cur.rowcount
    return n
