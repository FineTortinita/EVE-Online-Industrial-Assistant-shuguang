"""LP 兑换价值对话框（参考 T2 商人助手「忠诚点价值」、开明网「LP 兑换」）。

选 NPC 公司 → 后台从曙光 ESI 拉兑换项 → 按本地贸易中心卖价算每 LP 收益并排序。
表格沿用通用 `FSummaryTable`；顶栏多一个公司下拉与贸易中心下拉。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QThread, Signal, Slot

from core.constants import TRADE_HUBS
from core.container import get_container
from core.logger import log
from services.name_resolver import hub_options
from ui_qml.bridge.summary_dialog import SummaryTableBridge, SummaryTableQmlDialog, cell, fmt_isk

__all__ = ["LpStoreBridge", "LpStoreQmlDialog"]

_QML = "dialogs/LpStoreDialog.qml"
_COLUMNS = [
    {"title": "兑换物品", "width": 0},
    {"title": "数量", "width": 70},
    {"title": "LP", "width": 80},
    {"title": "星币", "width": 80},
    {"title": "所需物品", "width": 200},
    {"title": "卖单挂量", "width": 90},
    {"title": "产出价值", "width": 100},
    {"title": "利润", "width": 100},
    {"title": "ISK/LP", "width": 90},
]
#: 默认展示加达里海军：曙光主贸易中心索巴色基所在的加达里势力 LP 最常用
_DEFAULT_CORP_ID = 1000035


class _LpFetchWorker(QThread):
    #: 名称以 [(corp_id, name)] 列表跨线程：int 键的 dict 无法转成 QVariantMap（实测被静默清空）
    done = Signal(int, list, list)

    def __init__(self, corporation_id: int, name_ids: list[int]) -> None:
        super().__init__(None)
        self._corp = corporation_id
        self._name_ids = name_ids

    def run(self) -> None:
        from services.lp_service import fetch_corporation_zh_names, fetch_lp_offers

        names: list = []
        offers: list = []
        try:
            if self._name_ids:
                names = list(fetch_corporation_zh_names(self._name_ids).items())
            offers = fetch_lp_offers(self._corp)
        except Exception:  # 吞的是 ESI 网络/解析异常：失败按空结果报给界面
            log.exception("LP 兑换项拉取失败 corp=%s", self._corp)
        self.done.emit(self._corp, offers, names)


_LIVE: set[QThread] = set()


class LpStoreBridge(SummaryTableBridge):
    optionsChanged = Signal()

    def __init__(self, db: Any = None) -> None:
        super().__init__(title="LP 兑换价值", columns=[dict(c) for c in _COLUMNS])
        from services.lp_service import list_npc_corporations

        self._db = db or get_container().db
        self._corps = list_npc_corporations(self._db)
        ids = [c["id"] for c in self._corps]
        self._corp_index = ids.index(_DEFAULT_CORP_ID) if _DEFAULT_CORP_ID in ids else 0
        self._hub_index = 0
        self._offers: list[dict] = []
        self._busy = False
        self._names_loaded = False
        self._worker: QThread | None = None
        self.set_empty_text("选择公司后读取 LP 兑换项")

    corporations = Property(list, lambda self: [c["name"] for c in self._corps], notify=optionsChanged)
    hubs = Property(list, lambda self: hub_options(TRADE_HUBS), constant=True)
    corpIndex = Property(int, lambda self: self._corp_index, notify=optionsChanged)
    hubIndex = Property(int, lambda self: self._hub_index, notify=optionsChanged)
    busy = Property(bool, lambda self: self._busy, notify=optionsChanged)

    @Slot(int)
    def setCorpIndex(self, index: int) -> None:
        if 0 <= index < len(self._corps) and index != self._corp_index:
            self._corp_index = index
            self.optionsChanged.emit()
            self.reload()

    @Slot(int)
    def setHubIndex(self, index: int) -> None:
        if 0 <= index < len(TRADE_HUBS) and index != self._hub_index:
            self._hub_index = index
            self.optionsChanged.emit()
            self._render()

    @Slot()
    def reload(self) -> None:
        if not self._corps or self._busy:
            return
        self._busy = True
        self.optionsChanged.emit()
        self.set_content([], "正在从曙光 ESI 读取 LP 兑换项…")
        name_ids = [] if self._names_loaded else [c["id"] for c in self._corps]
        worker = _LpFetchWorker(self._corps[self._corp_index]["id"], name_ids)
        worker.done.connect(self._on_offers)
        _LIVE.add(worker)
        self._worker = worker
        # 绑定到桥（主线程 QObject）的槽 → 排队到主线程执行。**不要用无接收者的 lambda**：
        # 它在工作线程里直接执行，`_LIVE.discard` 会在线程收尾途中丢掉最后一个引用，
        # QThread 随之析构 —— ui 档实测由此引发后续用例 access violation。
        # 桥先于线程销毁时这条连接自动断开，线程留在 `_LIVE` 里（只多占一个对象，不崩）。
        worker.finished.connect(self._release_worker)
        worker.start()

    @Slot()
    def _release_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait()
            _LIVE.discard(worker)

    def _on_offers(self, corp_id: int, offers: list, name_pairs: list) -> None:
        self._busy = False
        names = {int(cid): str(name) for cid, name in name_pairs}
        if names:
            # 换成中文名后重新排序，并保持当前选中的公司不变
            self._names_loaded = True
            current = self._corps[self._corp_index]["id"]
            for c in self._corps:
                c["name"] = names.get(c["id"], c["name"])
            self._corps.sort(key=lambda c: c["name"])
            self._corp_index = next(i for i, c in enumerate(self._corps) if c["id"] == current)
        self.optionsChanged.emit()
        if corp_id != self._corps[self._corp_index]["id"]:
            self.reload()  # 拉取期间切换了公司：按新公司重拉
            return
        self._offers = list(offers)
        if not offers:
            self.set_empty_text("该公司没有 LP 兑换项，或网络读取失败")
        self._render()

    def _render(self) -> None:
        from services.lp_service import hub_has_prices, value_lp_offers

        if not self._offers:
            self.set_content([], "共 0 项")
            return
        hub = TRADE_HUBS[self._hub_index]
        hub_label = hub_options([hub])[0]["label"]
        if not hub_has_prices(self._db, hub):
            self.set_empty_text(f"本地还没有{hub_label}的价格：请在顶栏「区域」勾选 {hub} 后点「更新价格」")
            self.set_content([], f"共 {len(self._offers)} 项，缺{hub_label}价格")
            return
        ranked = value_lp_offers(self._db, self._offers, hub)
        rows = [self._row(v) for v in ranked]
        valued = sum(1 for v in ranked if v["isk_per_lp"] is not None)
        self.set_content(
            rows,
            f"共 {len(ranked)} 项，可估值 {valued} 项；价格取本地{hub_label}卖价，未扣销售税与经纪人费；"
            "挂量标黄 = 卖单不足一次兑换的数量",
        )

    @staticmethod
    def _row(v: dict) -> dict:
        def isk(value: float | None) -> str:
            return "—" if value is None else fmt_isk(value)

        profit = v["profit"]
        token = "" if profit is None else ("ACCENT_GREEN" if profit > 0 else "ACCENT_RED")
        per_lp = v["isk_per_lp"]
        return {
            "cells": [
                cell(v["name"]),
                cell(f"{v['quantity']:,}"),
                cell(f"{v['lp_cost']:,}"),
                cell(isk(v["isk_cost"])),
                cell("、".join(v["required_names"]) or "—", "TEXT_SECONDARY"),
                # 挂量少于一次兑换的产出数量：按这个价卖不完，标黄提示
                cell(
                    f"{v.get('sell_volume', 0):,}",
                    "ACCENT_YELLOW" if v.get("sell_volume", 0) < v["quantity"] else "",
                ),
                cell(isk(v["revenue"])),
                cell(isk(profit), token),
                cell("缺价" if v["missing"] else ("—" if per_lp is None else f"{per_lp:,.0f}"), token),
            ]
        }


class LpStoreQmlDialog(SummaryTableQmlDialog):
    """非模态独立窗；调用方用 `show()`。"""

    def __init__(self, parent: Any = None, db: Any = None) -> None:
        super().__init__(LpStoreBridge(db), parent=parent, size=(1320, 680), qml_file=_QML, modeless=True)
