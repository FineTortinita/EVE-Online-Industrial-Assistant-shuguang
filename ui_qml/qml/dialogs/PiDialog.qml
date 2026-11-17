import QtQuick
import QtQuick.Layouts
import "../components"

/* 行星加工利润（参考 T2 商人助手「行星加工链」与开明网「行星开发」）。
 *
 * 顶栏：层级 / 价格中心 / 海关税率 / 原料是否经海关进口；表格沿用 `FSummaryTable`，
 * 双击单元格复制文字。全部计算在桥里（`ui_qml/bridge/pi_bridge.py`）。
 */

Item {
    id: page

    readonly property var table: typeof bridge !== "undefined" ? bridge : null

    Rectangle {
        anchors.fill: parent
        color: Theme.bgDark
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingSm
        spacing: Theme.spacingSm

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            FComboBox {
                objectName: "piTierCombo"
                implicitWidth: 120
                model: page.table ? page.table.tierFilters : []
                currentIndex: page.table ? page.table.tierIndex : 0
                onActivated: if (page.table)
                    page.table.setTierIndex(currentIndex)
            }
            Text {
                text: qsTr("价格中心:")
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fs(12)
            }
            FComboBox {
                objectName: "piHubCombo"
                implicitWidth: 120
                model: page.table ? page.table.hubs : []
                textRole: "label"
                currentIndex: page.table ? page.table.hubIndex : 0
                onActivated: if (page.table)
                    page.table.setHubIndex(currentIndex)
            }
            Text {
                text: qsTr("海关税 %:")
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fs(12)
            }
            FDoubleSpinBox {
                objectName: "piTaxSpin"
                from: 0.0
                to: 100.0
                stepSize: 0.5
                decimals: 1
                implicitWidth: 96
                value: page.table ? page.table.taxPct : 10.0
                onValueModified: if (page.table)
                    page.table.setTaxPct(value)
            }
            FCheckBox {
                objectName: "piImportCheck"
                text: qsTr("原料经海关进口")
                checked: page.table ? page.table.importInputs : true
                onToggled: if (page.table)
                    page.table.setImportInputs(checked)
            }
            FButton {
                text: qsTr("刷新")
                onClicked: if (page.table)
                    page.table.reload()
            }
            Item {
                Layout.fillWidth: true
            }
        }

        FSummaryTable {
            objectName: "summaryTable"
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: page.table ? page.table.columns : []
            rows: page.table ? page.table.rows : []
            emptyText: page.table ? page.table.emptyText : ""
            onRowDoubleClicked: function (row, column) {
                if (page.table)
                    page.table.copyCell(row, column)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Text {
                Layout.fillWidth: true
                text: page.table ? page.table.statusText : ""
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Math.round(11 * Theme.fontScale)
                elide: Text.ElideRight
            }
            Text {
                visible: page.table ? page.table.error !== "" : false
                text: page.table ? page.table.error : ""
                color: Theme.accentYellow
                font.family: Theme.fontFamily
                font.pixelSize: Math.round(11 * Theme.fontScale)
            }
            FButton {
                text: qsTr("关闭")
                onClicked: if (page.table)
                    page.table.reject()
            }
        }
    }
}
