"""填料总表对话框的桥（阶段 4 → 重构阶段 4a 改为 T2 式备料占用 + 按材料机库分组）。

外加「行内复制采购」与「一键复制全部」。

展示走通用的 `SummaryTableBridge`（多了一个行内动作列与一个顶栏动作按钮）。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QApplication

from core.container import get_container
from ui_qml.bridge.summary_dialog import SummaryTableBridge, SummaryTableQmlDialog, cell

__all__ = ["MaterialsSummaryBridge", "MaterialsSummaryQmlDialog"]

_COLUMNS = [
    {"title": "材料机库", "width": 130},
    {"title": "材料名称", "width": 0},
    {"title": "需求", "width": 100},
    {"title": "库存占用", "width": 100},
    {"title": "子项冻结", "width": 90},
    {"title": "子项将产出", "width": 100},
    {"title": "需填", "width": 100},
    {"title": "充裕度", "width": 90},
    {"title": "状态", "width": 110},
    {"title": "复制采购", "width": 80},
]

#: 三态（T2 商人助手同款语义）：蓝 = 现有够；绿 = 暂缺但子项会造出来补上；红 = 缺
_STATES = {
    "ready": ("够了", "PRIMARY"),
    "coming": ("子项会补上", "ACCENT_GREEN"),
    "short": ("缺", "ACCENT_RED"),
}


class MaterialsSummaryBridge(SummaryTableBridge):
    """填料总表的 QML 后端（阶段 4a：按材料机库分组 + 备料占用三态）。

    数量来自 `services.industry_dialog_queries.get_fill_sheet`：勾了备料的待生产计划按
    「组号升序 → 子级降序」分配各自材料机库的库存（低组号先占），已完成子项的产出优先留给引用它的母项。
    「需填」= 仍缺的量；充裕度 = 现有（库存 + 冻结）÷ 需求。
    """

    def __init__(self) -> None:
        super().__init__(title="填料总表", columns=[dict(c) for c in _COLUMNS])
        self._top_action_text = "一键复制全部"
        self._has_action_column = True
        #: 供「复制」用的行数据
        self._data_rows: list[dict] = []

    @Slot()
    def reload(self) -> None:
        # 函数内导入：与本项目其他桥一致，也让调用方/测试好替换数据源
        from services.industry_dialog_queries import get_fill_sheet

        sheet = get_fill_sheet(get_container().db)
        if not sheet:
            self.set_content([], "没有勾选备料的待生产计划")
            return

        rows: list[dict] = []
        self._data_rows = []
        counts = {"ready": 0, "coming": 0, "short": 0}
        for group in sheet:
            for item in group["rows"]:
                need = int(item["need"])
                now = int(item["stock"]) + int(item["frozen"])
                ratio = now / need if need else 1.0
                label, token = _STATES[item["state"]]
                counts[item["state"]] += 1
                rows.append(
                    {
                        "cells": [
                            cell(group["hangar_name"]),
                            cell(item["name"]),
                            cell(f"{need:,}"),
                            cell(f"{item['stock']:,}"),
                            cell(f"{item['frozen']:,}" if item["frozen"] else "—"),
                            cell(f"{item['coming']:,}" if item["coming"] else "—"),
                            cell(f"{item['short']:,}", "ACCENT_RED" if item["short"] else ""),
                            cell(f"{ratio * 100:.0f}%", token),
                            cell(label, token),
                            cell(""),  # 行内动作列（QML 渲染成「复制」按钮）
                        ]
                    }
                )
                self._data_rows.append({"name": item["name"], "gap": int(item["short"])})

        self.set_content(
            rows,
            f"共 {len(rows)} 行（{len(sheet)} 个材料机库）：够了 {counts['ready']}，"
            f"子项会补上 {counts['coming']}，缺 {counts['short']}。"
            "按备料占用分配：组号小的计划先占库存。",
        )

    @Slot(int)
    def copyRow(self, row: int) -> None:
        """行内「复制」：把「名称 + 需填量」写进剪贴板（制表符分隔）。"""
        if not 0 <= row < len(self._data_rows):
            return
        item = self._data_rows[row]
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(f"{item['name']}\t{item['gap']:,.0f}")
        self.set_error(f"已复制: {item['name']} 需填 {item['gap']:,.0f}")

    @Slot()
    def topAction(self) -> None:
        """一键复制全部仍缺的材料（同名跨机库合并）。"""
        merged: dict[str, int] = {}
        for r in self._data_rows:
            if r["gap"] > 0:
                merged[r["name"]] = merged.get(r["name"], 0) + r["gap"]
        if not merged:
            self.set_error("所有材料已到位或会由子项补上，无需采购")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(f"{n}* {q}" for n, q in merged.items()))
        self.set_error(f"已复制 {len(merged)} 种待采购材料（共 {sum(merged.values()):,} 个）")


class MaterialsSummaryQmlDialog(SummaryTableQmlDialog):
    """QML 版「填料总表」。`MaterialsSummaryDialog(parent)` 的调用方原样可用。

    只读查看器 → **非模态独立窗**（`modeless=True`），调用方用 `show()` 而非 `exec()`。
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(MaterialsSummaryBridge(), parent=parent, size=(1100, 600), modeless=True)
