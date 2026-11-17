"""ESI 资产 / 工业作业 → 本地库存与计划（纯函数，无 DB / Qt）—— 重构阶段 6。

曙光公共客户端实测（2026-11-17）：放行 `esi-assets.read_assets.v1` 与 `esi-industry.read_character_jobs.v1`，
**不放行**蓝图（`invalid_scope`）—— 蓝图继续走剪贴板导入。

两件事：
1. **资产**：ESI 资产是「物品 → 位置」的扁平列表，容器里的东西 `location_id` 指向容器的 `item_id`；
   按顶层位置（空间站 / 建筑）汇总成 `{位置: {type_id: 数量}}`，喂给现有的「导入预览」对话框做对账。
2. **作业对账**：ESI 进行中作业 ↔ 本地「生产中」计划，按 产物 + 活动 + 流程 + 人物 + 开工时间 匹配，
   给出真实完工时刻、已交付 / 已完成可下线的状态，以及没有对应计划的游戏内作业。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

#: 不算「库存」的位置标记（ESI `location_flag`）：装在船上 / 植入体 / 技能 / 克隆等
_NON_STOCK_FLAGS = frozenset(
    {
        "Implant",
        "Skill",
        "SkillInTraining",
        "Booster",
        "QuafeBay",
        "Wardrobe",
        "Unlocked",
        "Locked",
        "AutoFit",
        "AssetSafety",
    }
)
#: 船只的装配槽（HiSlot0…、Rig…、SubSystem…）—— 装着的模块不是可用库存
_FIT_PREFIXES = ("HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "FighterTube")


def _is_fitted(flag: str) -> bool:
    return flag.startswith(_FIT_PREFIXES)


def group_assets_by_location(assets: Iterable[Mapping]) -> dict[int, dict[int, int]]:
    """ESI `/characters/{id}/assets/` 全部页 → `{顶层位置 id: {type_id: 数量}}`。

    - 容器 / 船里的物品沿 `location_id → item_id` 往上找到顶层（空间站 / 建筑 / 太空），归到顶层位置；
    - 装配在船上的模块、植入体、技能等不算库存（见 `_NON_STOCK_FLAGS` / `_FIT_PREFIXES`）；
    - 蓝图原图 / 拷贝也会出现在资产里（`is_blueprint_copy` 只出现在拷贝上）：**跳过**，与剪贴板导入
      「材料仓库只导入材料」一致 —— 蓝图另有导入入口（ESI 不放行蓝图 scope，拿不到 ME/TE）。
    - 被组装的船本身（`is_singleton` 且有货在里面）也跳过：它是工具不是库存。
    """
    rows = [dict(a) for a in assets]
    by_item = {int(a["item_id"]): a for a in rows if a.get("item_id")}
    holders = {int(a["location_id"]) for a in rows if a.get("location_id")}

    def root(a: dict) -> int:
        seen: set[int] = set()
        loc = int(a.get("location_id") or 0)
        while loc in by_item and loc not in seen:
            seen.add(loc)
            loc = int(by_item[loc].get("location_id") or 0)
        return loc

    out: dict[int, dict[int, int]] = {}
    for a in rows:
        flag = str(a.get("location_flag") or "")
        if flag in _NON_STOCK_FLAGS or _is_fitted(flag):
            continue
        if "is_blueprint_copy" in a or str(a.get("type_category") or "") == "blueprint":
            continue
        if a.get("is_singleton") and int(a.get("item_id") or 0) in holders:
            continue  # 装着东西的组装船 / 容器本身
        tid = int(a.get("type_id") or 0)
        qty = int(a.get("quantity") or 0)
        if tid <= 0 or qty <= 0:
            continue
        bucket = out.setdefault(root(a), {})
        bucket[tid] = bucket.get(tid, 0) + qty
    return out


# ── 工业作业对账 ─────────────────────────────────────────────

#: ESI `activity_id` → 本地计划 `activity`
ACTIVITY_BY_ID = {
    1: "manufacturing",
    3: "researching_time_efficiency",
    4: "researching_material_efficiency",
    5: "copying",
    8: "invention",
    9: "reaction",
    11: "reaction",
}


@dataclass(frozen=True)
class JobMatch:
    plan_id: int | None  # None = 游戏里有、本地没有对应计划
    job_id: int
    status: str  # ESI：active / paused / ready / delivered / cancelled / reverted
    end_date: str  # ESI ISO8601 UTC
    note: str


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def match_jobs(jobs: Iterable[Mapping], plans: Iterable[Mapping], *, char_names: Mapping[int, str]) -> list[JobMatch]:
    """ESI 作业 ↔ 本地在产计划（`in_progress` / `running` / `ready`）。

    计划一条 = 若干条并行线（每线一个游戏作业）；匹配键：产物 type_id + 活动 + 每线流程数，
    同键多候选时优先 人物名相同 → 开工时间最接近（本地 `started_at` 是点「EVE 中已启动」的时刻，
    与游戏开工通常差几分钟）。一条计划最多吃 `parallels` 个作业。
    """
    pool: list[dict] = [dict(p) for p in plans]
    taken: dict[int, int] = {}
    out: list[JobMatch] = []
    for j in sorted(jobs, key=lambda j: (str(j.get("start_date") or ""), int(j.get("job_id") or 0))):
        act = ACTIVITY_BY_ID.get(int(j.get("activity_id") or 0), "")
        product = int(j.get("product_type_id") or j.get("blueprint_type_id") or 0)
        runs = int(j.get("runs") or 0)
        who = char_names.get(int(j.get("installer_id") or 0), "")
        start = _ts(j.get("start_date"))
        cands = []
        for p in pool:
            pid = int(p["id"])
            if taken.get(pid, 0) >= max(int(p.get("parallels") or 1), 1):
                continue
            p_act = str(p.get("activity") or "manufacturing")
            if p_act != act or int(p.get("product_type_id") or 0) != product:
                continue
            if act in ("manufacturing", "reaction") and int(p.get("runs") or 0) != runs:
                continue
            ps = _ts(str(p.get("started_at") or "").replace(" ", "T") + "Z") if p.get("started_at") else None
            gap = abs((ps or 0) - (start or 0)) if ps and start else 1e12
            cands.append(((p.get("char_name") or "") != who, gap, pid))
        status = str(j.get("status") or "")
        end = str(j.get("end_date") or "")
        if not cands:
            out.append(JobMatch(None, int(j.get("job_id") or 0), status, end, "游戏里有、本地没有对应计划"))
            continue
        _diff, gap, pid = min(cands)
        taken[pid] = taken.get(pid, 0) + 1
        note = "已匹配" if gap < 6 * 3600 else "已匹配（开工时间相差较大，请核对）"
        out.append(JobMatch(pid, int(j.get("job_id") or 0), status, end, note))
    return out


def plan_job_summary(matches: Iterable[JobMatch]) -> dict[int, dict]:
    """按计划汇总：`{plan_id: {jobs, finished, latest_end}}`；`finished` = ESI 说已完成（ready / delivered）的线数。"""
    out: dict[int, dict] = {}
    for m in matches:
        if m.plan_id is None:
            continue
        s = out.setdefault(m.plan_id, {"jobs": 0, "finished": 0, "latest_end": ""})
        s["jobs"] += 1
        s["finished"] += int(m.status in ("ready", "delivered"))
        s["latest_end"] = max(s["latest_end"], m.end_date)
    return out
