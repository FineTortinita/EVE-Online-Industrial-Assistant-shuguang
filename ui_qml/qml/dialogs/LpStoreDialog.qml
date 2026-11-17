import QtQuick
import QtQuick.Layouts
import "../components"

/* LP 兑换价值（参考 T2 商人助手「忠诚点价值」与开明网「LP 兑换」）。
 *
 * 顶栏：NPC 公司 + 贸易中心；表格沿用 `FSummaryTable`，双击单元格复制文字。
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

            Text {
                text: qsTr("公司:")
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fs(12)
            }
            FComboBox {
                objectName: "lpCorpCombo"
                implicitWidth: 220
                model: page.table ? page.table.corporations : []
                currentIndex: page.table ? page.table.corpIndex : 0
                enabled: page.table ? !page.table.busy : false
                onActivated: if (page.table)
                    page.table.setCorpIndex(currentIndex)
            }
            Text {
                text: qsTr("价格中心:")
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fs(12)
            }
            FComboBox {
                objectName: "lpHubCombo"
                implicitWidth: 120
                model: page.table ? page.table.hubs : []
                textRole: "label"
                currentIndex: page.table ? page.table.hubIndex : 0
                onActivated: if (page.table)
                    page.table.setHubIndex(currentIndex)
            }
            FButton {
                text: qsTr("刷新")
                enabled: page.table ? !page.table.busy : false
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
