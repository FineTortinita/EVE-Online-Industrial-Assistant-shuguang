"""真实鼠标拖动必须改变单列，而不是只让整条表头回弹。"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.qml_click import spin
from ui_qml.host import PageHost

pytestmark = pytest.mark.ui


def _find(root: QQuickItem, name: str) -> QQuickItem | None:
    if root.objectName() == name:
        return root
    for child in root.childItems():
        found = _find(child, name)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("kind", ["plan", "trade", "mfg"])
def test_drag_changes_only_target_column(qapp: QApplication, kind: str) -> None:
    from ui_qml.bridge.manufacturable_items_bridge import ManufacturableItemsBridge
    from ui_qml.bridge.trade_bridge import TradeBridge
    from ui_qml.models.industry_models import PlanTableModel
    from ui_qml.views.industry.plan_table import PlanTable

    if kind == "plan":
        owner = PlanTable()
        model = PlanTableModel([{"id": 1, "product_name": "长名称产品交互验证", "status": "pending", "runs": 1}])
        owner.set_model(model)
        bridge, file, col = owner.bridge, "pages/PlanTablePane.qml", 3
    elif kind == "trade":
        bridge, file, col = TradeBridge(None), "pages/TradePage.qml", 1
        bridge._model.set_rows(
            [{"id": 34, "z": "三钛合金交互验证", "e": "hidden English", "pa": 12345, "pb": 23456, "pm3": 99}]
        )
    else:
        bridge, file, col = ManufacturableItemsBridge(), "dialogs/ManufacturableItemsDialog.qml", 1
        from ui_qml.bridge.manufacturable_items_bridge import _MFG_BCOLS, _MFG_MCOLS

        bridge._set_columns(_MFG_BCOLS + _MFG_MCOLS)
        bridge._set_rows([{"id": 34, "z": "可制造物品交互验证", "e": "hidden English", "bp": 12345, "sp": 23456}])
    host = PageHost(file, context={"planTableBridge" if kind == "plan" else "bridge": bridge})
    try:
        host.resize(1100, 650)
        host.show()
        spin(300)
        root = host.rootObject()
        handle = _find(root, f"{kind}Resize{col}")
        neighbor = _find(root, f"{kind}Resize{col + (2 if kind != 'plan' else 1)}")
        table = _find(root, "horizontalScroll").parentItem()
        cell_width = QQmlExpression(qmlContext(table), table, f"itemAtCell({col}, 0).width")
        before, other = cell_width.evaluate()[0], neighbor.property("columnWidth")
        for _ in range(2):
            start = handle.mapToItem(None, 4, handle.height() / 2).toPoint()
            QTest.mousePress(host, Qt.LeftButton, Qt.NoModifier, start)
            QTest.mouseMove(host, start + QPoint(75, 0), 30)
            QTest.mouseRelease(host, Qt.LeftButton, Qt.NoModifier, start + QPoint(75, 0))
            spin(100)
        assert cell_width.evaluate()[0] == pytest.approx(before + 150, abs=2)
        assert handle.property("columnWidth") == pytest.approx(before + 150, abs=2)
        assert neighbor.property("columnWidth") == other
        if kind != "plan":
            assert _find(root, f"{kind}Resize2") is None
        out = Path(".claude/ui-snapshots")
        out.mkdir(parents=True, exist_ok=True)
        host.grab().save(str(out / f"resize-{kind}.png"))
        host.resize(700, 650)
        spin(150)
        bar = _find(root, "horizontalScroll")
        table = bar.parentItem()
        before_x = table.property("contentX")
        start = bar.mapToItem(None, bar.width() * bar.property("size") / 2, bar.height() / 2).toPoint()
        QTest.mousePress(host, Qt.LeftButton, Qt.NoModifier, start)
        end = bar.mapToItem(None, bar.width() - 2, bar.height() / 2).toPoint()
        QTest.mouseMove(host, end, 30)
        QTest.mouseRelease(host, Qt.LeftButton, Qt.NoModifier, end)
        spin(100)
        assert table.property("contentX") > before_x
        host.grab().save(str(out / f"scroll-{kind}.png"))
    finally:
        host.close()
        host.deleteLater()
        spin(60)
