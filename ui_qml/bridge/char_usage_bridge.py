"""人物占用情况对话框的桥（阶段 4）。

对照旧 Widgets 版，但**展示换了**：
原先是一张五列表（「队列时长 / 技能等级」两列常年 N/A），现在直接渲染查询页空闲态
仪表盘左栏那块「产线详情」面板（`qml/components/OccupancyPanel.qml`）—— 每人物一块、
块内制造 / 科研 / 反应三行容量条 + 「待下线 N」，一眼看出谁的线快满了。

与仪表盘的**唯一差别是口径**：仪表盘只算**正在生产**，本对话框算**已规划**
（`PLANNED_STATUSES`：待生产的也先把线占上，待下线的也算 —— 成品没下线那条线腾不出来）。
排产阶段要看的正是这个「按现在的规划，谁的线不够」，只算在跑的等于把待排的线全漏掉。

**顺带修掉一处会崩的调用**：Widgets 版 `CharacterUsageDialog` 继承的是 `QWidget`，
而调用方 `industry_view.open_char_usage()` 对它调 `.exec()` —— `QWidget` 没有 `exec`，
点那个按钮必然 AttributeError。迁到 `QmlDialog`（真 QDialog）后这条路径才成立。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from core.logger import log
from ui_qml.dialog_host import DialogBridge, QmlDialog

__all__ = ["CharacterUsageBridge", "CharacterUsageQmlDialog"]

_QML_FILE = "dialogs/CharUsageDialog.qml"


class CharacterUsageBridge(DialogBridge):
    """人物占用情况的 QML 后端。"""

    #: 单一通知源（QML 侧一次重读两个属性，与 `SummaryTableBridge.contentChanged` 同款）
    contentChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._blocks: list[dict] = []
        self._summary = ""
        self._plan_rows: list[dict] = []
        self._lanes: list[dict] = []
        self._lane_hours = 24.0
        self._plan_summary = ""
        self._suggested: dict[int, str] = {}
        self.set_title("人物占用情况")

    #: 每人物一块（形状见 `ui_qml.bridge.occupancy`）
    occupancyByChar = Property(list, lambda self: list(self._blocks), notify=contentChanged)
    occupancySummary = Property(str, lambda self: self._summary, notify=contentChanged)

    # ── 人物规划（重构阶段 5）：自动派人预览 + 按人物槽位排程的时间线 ──
    #: 派人表 [{cells: [{text, color}]}]：计划 / 线型 / 线数 / 当前人物 / 建议人物 / 说明 / 开工 / 完工
    planRows = Property(list, lambda self: list(self._plan_rows), notify=contentChanged)
    #: 时间线泳道 [{name, bars: [{label, start, duration, color}]}]（小时，相对现在）
    lanes = Property(list, lambda self: list(self._lanes), notify=contentChanged)
    laneHours = Property(float, lambda self: self._lane_hours, notify=contentChanged)
    planSummary = Property(str, lambda self: self._plan_summary, notify=contentChanged)

    @Slot()
    def applyAssignments(self) -> None:
        """把建议人物写回待生产计划（只改与当前不同的行）。"""
        from services.character_planning_service import apply_assignments

        n = apply_assignments({pid: name for pid, name in self._suggested.items() if name})
        self.set_error(f"已改派 {n} 条计划" if n else "没有需要改派的计划")
        self.reload()
        self.plansChanged.emit()

    plansChanged = Signal()

    def _load_planning(self, plans: list[dict]) -> None:
        from services.character_planning_service import plan_assignments
        from ui_qml.bridge.summary_dialog import cell
        from ui_qml.theme.registry import token_color

        self._plan_rows, self._lanes, self._suggested = [], [], {}
        self._lane_hours, self._plan_summary = 24.0, ""
        try:
            res = plan_assignments(plans)
        except Exception:
            # 吞的是派人 / 排程取数异常（蓝图库缺表等）：占用面板照常显示，规划区给出原因
            log.exception("人物规划计算失败")
            self._plan_summary = "人物规划计算失败，详见日志"
            return
        rows, assign, slots = res["rows"], res["assign"], {s.job_id: s for s in res["slots"]}
        colors = {"manufacturing": "ACCENT_CYAN", "research": "ACCENT_YELLOW", "reaction": "ACCENT_PURPLE"}
        from services.char_capacity import line_label
        from services.industry_calc_service import fmt_duration

        changed = unassigned = 0
        for pid, p in sorted(rows.items(), key=lambda kv: (slots[kv[0]].start if kv[0] in slots else 0, kv[0])):
            name, note = assign.get(pid, ("", ""))
            cur = str(p.get("char_name") or "")
            slot = slots.get(pid)
            running = note == "生产中"
            if not running:
                self._suggested[pid] = name
                changed += int(bool(name) and name != cur)
                unassigned += int(not name)
            token = "ACCENT_RED" if not name else ("ACCENT_GREEN" if name != cur and not running else "")
            self._plan_rows.append(
                {
                    "cells": [
                        cell(p.get("product_name") or f"计划#{pid}"),
                        cell(line_label(slot.line_type) if slot else ""),
                        cell(str(max(int(p.get("parallels") or 1), 1))),
                        cell(cur or "—"),
                        cell(name or "无人可接", token),
                        cell(note),
                        cell(fmt_duration(slot.start * 3600) if slot and slot.start else "现在"),
                        cell(fmt_duration(slot.end * 3600) if slot else ""),
                    ]
                }
            )
        by_char: dict[str, list[dict]] = {}
        for s in res["slots"]:
            p = rows.get(s.job_id) or {}
            by_char.setdefault(s.char or "（未分配）", []).append(
                {
                    "label": f"{p.get('product_name') or s.job_id} ×{max(int(p.get('parallels') or 1), 1)}",
                    "start": s.start,
                    "duration": max(s.end - s.start, 0.05),
                    "color": token_color(colors.get(s.line_type, "ACCENT_CYAN")),
                }
            )
        order = [c for c in res["chars"] if c in by_char] + [c for c in by_char if c not in res["chars"]]
        self._lanes = [{"name": c, "bars": by_char[c]} for c in order]
        self._lane_hours = max((s.end for s in res["slots"]), default=24.0) or 24.0
        self._plan_summary = (
            f"建议改派 {changed} 条、无人可接 {unassigned} 条；全部完工约 {fmt_duration(self._lane_hours * 3600)}"
            "（按人物槽位与子项依赖排程，技能不达标的人物不派）"
        )

    @Slot()
    def reload(self) -> None:
        """读全部「已规划」计划 → 算块数据。同步取数（一次查询 + 一次技能配置读），不起线程。"""
        from services.char_capacity import PLANNED_STATUSES, char_line_usage
        from services.plan_service import load_plans_for_wizard
        from ui_qml.bridge.occupancy import build_occupancy_blocks, occupancy_summary

        try:
            plans = list(load_plans_for_wizard() or [])
        except Exception:
            # 吞的是「读 user.db 失败」（库被占用 / 表结构不符）—— 对话框不该整块空白，
            # 至少把原因摆在摘要行上，别让用户对着一个没人的空面板猜。
            log.exception("人物占用读取失败")
            self._blocks = []
            self._summary = "读取生产计划失败，详见日志"
            self.contentChanged.emit()
            return

        per_char, _line_caps = char_line_usage(plans, statuses=PLANNED_STATUSES)
        if not per_char:
            self._blocks = []
            self._summary = "无人物配置，请在人物设置中添加"
        else:
            self._blocks = build_occupancy_blocks(per_char, plans)
            self._summary = occupancy_summary(per_char, label="已规划")
        self._load_planning(plans)
        self.contentChanged.emit()


class CharacterUsageQmlDialog(QmlDialog):
    """QML 版「人物占用情况」。`CharacterUsageDialog(parent)` 的调用方原样可用。

    只读查看器 → **非模态独立窗**（`modeless=True`），调用方用 `show()` 而非 `exec()`。
    """

    def __init__(self, parent: Any = None) -> None:
        bridge = CharacterUsageBridge()
        super().__init__(_QML_FILE, bridge, parent=parent, size=(1180, 820), modeless=True)
        # `QmlDialog` 不替子类加载数据（通用汇总表那条路才在构造末尾 reload）——
        # 这里自己调一次，QML 侧的绑定会随 `contentChanged` 拿到块数据。
        bridge.reload()
