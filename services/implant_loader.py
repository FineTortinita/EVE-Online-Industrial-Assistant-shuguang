"""工业植入体数据加载 — 从 reference.db 读取并解析加成描述。"""

from __future__ import annotations

import json
import os
import sqlite3

from core.container import get_container
from core.logger import log
from core.paths import REF_DB_PATH

IMPLANT_CACHE: list[dict] = []


def _parse_implant_bonus(attrs: list) -> str:
    """从 dogma 属性解析人类可读的加成描述。"""
    # attribute_id -> (显示名称, 是否减少型)
    # 减少型: 负值=收益 (如 -1% 制造时间)
    # 增加型: 正值=收益 (如 +1% 采矿量)
    KNOWN = {
        440: ("制造时间", True),  # manufacturingTimeBonus
        452: ("复制速度", True),  # copySpeedBonus
        453: ("蓝图制造时间", True),  # blueprintmanufactureTimeBonus
        468: ("材料需求研究", True),  # mineralNeedResearchBonus
        379: ("精炼产出", False),  # refiningYieldMutator
        434: ("采矿量", False),  # miningAmountBonus
        927: ("采矿升级CPU", True),  # miningUpgradeCPUReductionBonus
        780: ("冰矿采集周期", True),  # iceHarvestCycleBonus
        66: ("循环时间", True),  # durationBonus
    }
    descs = []
    for attr in attrs:
        aid = attr["attribute_id"]
        val = attr["value"]
        if aid in KNOWN:
            name, is_reduction = KNOWN[aid]
            if val != 0:
                if is_reduction:
                    descs.append(f"{name} -{abs(int(val))}%")
                else:
                    descs.append(f"{name} +{int(val)}%")
    return ", ".join(descs) if descs else ""


#: 加成属性 → 人物设置里的插槽（对应 `ui_qml/bridge/char_settings_bridge._SLOT_TITLES`）。
#: 三个插槽是本应用自造的分类，不是 EVE 的真实植入体槽位 —— 这里只是替用户
#: 把从 ESI 拉回来的植入体摆到语义最接近的那一格。
_SLOT_BY_ATTRIBUTE = {
    440: "A",  # manufacturingTimeBonus 制造时间
    452: "A",  # copySpeedBonus 复制速度
    453: "A",  # blueprintmanufactureTimeBonus 蓝图制造时间
    468: "A",  # mineralNeedResearchBonus 材料需求研究
    379: "B",  # refiningYieldMutator 精炼产出
    434: "B",  # miningAmountBonus 采矿量
    927: "B",  # miningUpgradeCPUReductionBonus 采矿升级CPU
    780: "B",  # iceHarvestCycleBonus 冰矿采集周期
    66: "C",  # durationBonus 循环时间
}


def _implant_slot(attrs: list) -> str:
    """按 dogma 加成属性判断该植入体归哪个插槽：A 生产与研究 / B 精炼与采矿 / C 通用。

    命中多组时按 A → B → C 取第一个（与插槽标题的顺序一致）；一个都不认识归 C。
    """
    hit = {_SLOT_BY_ATTRIBUTE[a["attribute_id"]] for a in attrs if a.get("attribute_id") in _SLOT_BY_ATTRIBUTE}
    for slot in ("A", "B", "C"):
        if slot in hit:
            return slot
    return "C"


def load_implants() -> list[dict]:
    """从 item_dogma 表加载所有工业植入体。"""
    global IMPLANT_CACHE
    if IMPLANT_CACHE:
        return IMPLANT_CACHE

    if not os.path.exists(REF_DB_PATH):
        return []

    conn = get_container().db.direct_connect("ref")
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT i.type_id, i.en_name, i.zh_name, d.dogma_attrs
                FROM item i
                JOIN item_dogma d ON i.type_id = d.type_id
                ORDER BY i.en_name
                """
            )
        except sqlite3.OperationalError:
            # 吞的是「item / item_dogma 表还没建」（静态数据或植入体步骤未初始化）：
            # 返回空清单且**不缓存**，初始化补齐后下次打开即可拿到；不能让人物设置打不开
            log.warning("植入体数据表不存在（初始化未完成），增效体清单为空")
            return []
        results = []
        for row in cur.fetchall():
            type_id, en_name, zh_name, dogma_json = row
            attrs = json.loads(dogma_json) if dogma_json else []
            bonus_desc = _parse_implant_bonus(attrs)
            results.append(
                {
                    "type_id": type_id,
                    "en_name": en_name,
                    "zh_name": zh_name or en_name,
                    "bonus_desc": bonus_desc,
                    "slot": _implant_slot(attrs),
                }
            )
    finally:
        conn.close()
    IMPLANT_CACHE = results
    return results


#: {植入体 type_id: 制造时间加成 %}（属性 440，-4 = −4%）；按需从 item_dogma 读，进程内缓存。
_MFG_TIME_BONUS_CACHE: dict[int, float] = {}


def manufacturing_time_bonuses(implant_ids: list | None) -> list[float]:
    """人物配置里的植入体 → 各自的制造时间加成百分比列表（无此属性 / 查不到的不计）。

    供评分把「BX-80x 系列」这类制造时间植入体乘进生产时间（BL-052-5）；
    纯乘数换算在 `domain.formulas.implant_time_multiplier`。
    """
    from domain.formulas import IMPLANT_MANUFACTURING_TIME_ATTR

    ids = [int(i) for i in (implant_ids or []) if i]
    missing = [i for i in ids if i not in _MFG_TIME_BONUS_CACHE]
    if missing:
        conn = get_container().db.direct_connect("ref")
        try:
            ph = ",".join("?" * len(missing))
            rows = conn.execute(
                f"SELECT type_id, dogma_attrs FROM item_dogma WHERE type_id IN ({ph})", missing
            ).fetchall()
        except sqlite3.OperationalError:
            # 吞的是「item_dogma 表未建」（初始化未完成）：本次按无植入体加成，不缓存
            log.warning("植入体数据表不存在，制造时间不计植入体加成")
            return []
        finally:
            conn.close()
        found = {int(r[0]): r[1] for r in rows}
        for i in missing:
            attrs = json.loads(found[i]) if found.get(i) else []
            _MFG_TIME_BONUS_CACHE[i] = next(
                (float(a["value"]) for a in attrs if a.get("attribute_id") == IMPLANT_MANUFACTURING_TIME_ATTR), 0.0
            )
    return [_MFG_TIME_BONUS_CACHE[i] for i in ids if _MFG_TIME_BONUS_CACHE.get(i)]
