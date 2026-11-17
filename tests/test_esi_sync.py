"""ESI 资产按位置汇总 / 工业作业对账 `domain/esi_sync.py`（阶段 6）。"""

from __future__ import annotations

import pytest

from domain.esi_sync import group_assets_by_location, match_jobs, plan_job_summary

pytestmark = pytest.mark.fast

STATION, OTHER = 60003760, 1035466617946


def test_group_assets_rolls_up_containers_and_skips_non_stock():
    assets = [
        {"item_id": 1, "type_id": 34, "quantity": 100, "location_id": STATION, "location_flag": "Hangar"},
        {"item_id": 2, "type_id": 17366, "quantity": 1, "location_id": STATION, "location_flag": "Hangar",
         "is_singleton": True},  # 容器
        {"item_id": 3, "type_id": 34, "quantity": 50, "location_id": 2, "location_flag": "Unlocked"},  # 锁定标记 → 不算
        {"item_id": 4, "type_id": 35, "quantity": 7, "location_id": 2, "location_flag": "Hangar"},  # 容器内 → 归 STATION
        {"item_id": 5, "type_id": 638, "quantity": 1, "location_id": OTHER, "location_flag": "Hangar",
         "is_singleton": True},  # 组装好的乌鸦级（里面有货）
        {"item_id": 6, "type_id": 2048, "quantity": 1, "location_id": 5, "location_flag": "HiSlot0"},  # 装配的模块
        {"item_id": 7, "type_id": 36, "quantity": 9, "location_id": 5, "location_flag": "Cargo"},  # 船舱货物 → 归 OTHER
        {"item_id": 8, "type_id": 688, "quantity": 1, "location_id": STATION, "location_flag": "Hangar",
         "is_blueprint_copy": True},  # 蓝图拷贝跳过
    ]  # fmt: skip
    assert group_assets_by_location(assets) == {STATION: {34: 100, 35: 7}, OTHER: {36: 9}}


def test_match_jobs_by_product_runs_char_and_time():
    plans = [
        {"id": 10, "product_type_id": 638, "activity": "manufacturing", "runs": 2, "parallels": 2,
         "char_name": "凋零", "started_at": "2026-11-17 08:00:00"},
        {"id": 11, "product_type_id": 638, "activity": "manufacturing", "runs": 2, "parallels": 1,
         "char_name": "main", "started_at": "2026-11-16 08:00:00"},
    ]  # fmt: skip
    job = {"activity_id": 1, "product_type_id": 638, "runs": 2, "installer_id": 7, "status": "active",
           "end_date": "2026-11-18T08:00:00Z"}  # fmt: skip
    jobs = [
        {**job, "job_id": 1, "start_date": "2026-11-17T08:03:00Z"},
        {**job, "job_id": 2, "start_date": "2026-11-17T08:04:00Z", "status": "ready"},
        {**job, "job_id": 3, "start_date": "2026-11-17T08:05:00Z"},  # 计划 10 已满 2 条 → 落到 11（人物不同）
        {**job, "job_id": 4, "runs": 5, "start_date": "2026-11-17T09:00:00Z"},  # 流程数对不上 → 无对应
    ]
    m = {x.job_id: x for x in match_jobs(jobs, plans, char_names={7: "凋零"})}
    assert (m[1].plan_id, m[2].plan_id, m[3].plan_id, m[4].plan_id) == (10, 10, 11, None)
    summary = plan_job_summary(m.values())
    assert summary[10] == {"jobs": 2, "finished": 1, "latest_end": "2026-11-18T08:00:00Z"}
