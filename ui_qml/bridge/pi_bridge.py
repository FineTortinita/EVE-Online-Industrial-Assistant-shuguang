"""行星加工利润对话框（参考 T2 商人助手「行星加工链」、开明网「行星开发」）。

只读本地数据（配方 JSON + market.db），同步计算（68 条配方，毫秒级），不开线程。
表格沿用通用 `FSummaryTable`；顶栏：层级筛选、价格中心、海关税率、原料是否进口。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from core.constants import TRADE_HUBS
from core.container import get_container
from services.name_resolver import hub_options
from ui_qml.bridge.summary_dialog import SummaryTableBridge, SummaryTableQmlDialog, cell, fmt_isk

__all__ = ["PiBridge", "PiQmlDialog"]

_QML = "dialogs/PiDialog.qml"
_COLUMNS = [
    {"title": "产物", "width": 0},
    {"title": "层级", "width": 50},
    {"title": "周期", "width": 60},
    {"title": "原料（每周期）", "width": 300},
    {"title": "产出", "width": 60},
    {"title": "收入", "width": 90},
    {"title": "原料成本", "width": 90},
    {"title": "海关税", "width": 80},
    {"title": "利润/周期", "width": 90},
    {"title": "利润/小时", "width": 90},
    {"title": "利润率", "width": 70},
]
#: 层级筛选：下拉项 → 允许的层级集合（None = 全部）
_TIER_FILTERS: list[tuple[str, set[int] | None]] = [
    ("全部层级", None),
    ("P1 基础", {1}),
    ("P2 精炼", {2}),
    ("P3 专业", {3}),
    ("P4 高级", {4}),
]


class PiBridge(SummaryTableBridge):
    optionsChanged = Signal()

    def __init__(self, db: Any = None) -> None:
        super().__init__(title="行星加工利润", columns=[dict(c) for c in _COLUMNS])
        self._db = db or get_container().db
        self._hub_index = 0
        self._tier_index = 0
        self._tax_pct = 10.0  # 高安 NPC 海关基础税率 10%（见 domain/pi.py 来源）
        self._import_inputs = True
        self.set_empty_text("缺少行星配方数据（data/pi_schematics.json）")

    hubs = Property(list, lambda self: hub_options(TRADE_HUBS), constant=True)
    tierFilters = Property(list, lambda self: [label for label, _ in _TIER_FILTERS], constant=True)
    hubIndex = Property(int, lambda self: self._hub_index, notify=optionsChanged)
    tierIndex = Property(int, lambda self: self._tier_index, notify=optionsChanged)
    taxPct = Property(float, lambda self: self._tax_pct, notify=optionsChanged)
    importInputs = Property(bool, lambda self: self._import_inputs, notify=optionsChanged)

    @Slot(int)
    def setHubIndex(self, index: int) -> None:
        if 0 <= index < len(TRADE_HUBS) and index != self._hub_index:
            self._hub_index = index
            self.optionsChanged.emit()
            self.reload()

    @Slot(int)
    def setTierIndex(self, index: int) -> None:
        if 0 <= index < len(_TIER_FILTERS) and index != self._tier_index:
            self._tier_index = index
            self.optionsChanged.emit()
            self.reload()

    @Slot(float)
    def setTaxPct(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        if abs(value - self._tax_pct) > 1e-9:
            self._tax_pct = value
            self.optionsChanged.emit()
            self.reload()

    @Slot(bool)
    def setImportInputs(self, value: bool) -> None:
        if bool(value) != self._import_inputs:
            self._import_inputs = bool(value)
            self.optionsChanged.emit()
            self.reload()

    @Slot()
    def reload(self) -> None:
        from services.lp_service import hub_has_prices
        from services.pi_service import load_schematics, rank_schematics

        hub = TRADE_HUBS[self._hub_index]
        hub_label = hub_options([hub])[0]["label"]
        if not load_schematics():
            self.set_content([], "共 0 条配方")
            return
        if not hub_has_prices(self._db, hub):
            self.set_empty_text(f"本地还没有{hub_label}的价格：请在顶栏「区域」勾选 {hub} 后点「更新价格」")
            self.set_content([], f"缺{hub_label}价格")
            return
        ranked = rank_schematics(self._db, hub, tax_rate=self._tax_pct / 100.0, import_inputs=self._import_inputs)
        allowed = _TIER_FILTERS[self._tier_index][1]
        shown = [v for v in ranked if allowed is None or v["tier"] in allowed]
        valued = sum(1 for v in shown if v["profit"] is not None)
        self.set_content(
            [self._row(v) for v in shown],
            f"共 {len(shown)} 条配方，可估值 {valued} 条；原料与产物都按{hub_label}卖单价，"
            f"海关税 {self._tax_pct:g}%（{'原料经海关进口' if self._import_inputs else '原料本星球自产'}），"
            "未扣销售税与经纪人费",
        )

    @staticmethod
    def _row(v: dict) -> dict:
        def isk(value: float | None) -> str:
            return "—" if value is None else fmt_isk(value)

        profit = v["profit"]
        token = "" if profit is None else ("ACCENT_GREEN" if profit > 0 else "ACCENT_RED")
        margin = v["margin_pct"]
        cycle = int(v["cycle_time"])
        return {
            "cells": [
                cell(v["name"]),
                cell("—" if v["tier"] is None else f"P{v['tier']}"),
                cell(f"{cycle // 60} 分" if cycle % 3600 else f"{cycle // 3600} 时"),
                cell("、".join(v["input_names"]), "TEXT_SECONDARY"),
                cell(f"{v['output_qty']:,}"),
                cell(isk(v["revenue"])),
                cell(isk(v["input_cost"])),
                cell(isk(v["tax"])),
                cell(isk(profit), token),
                cell("缺价" if v["missing"] else isk(v["profit_per_hour"]), token),
                cell("—" if margin is None else f"{margin:.1f}%", token),
            ]
        }


class PiQmlDialog(SummaryTableQmlDialog):
    """非模态独立窗；调用方用 `show()`。"""

    def __init__(self, parent: Any = None, db: Any = None) -> None:
        super().__init__(PiBridge(db), parent=parent, size=(1380, 700), qml_file=_QML, modeless=True)
