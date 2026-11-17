import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

/* 「人物占用情况」对话框（工业页底部按钮「人物占用」）。
 *
 * 内容就是查询页空闲态仪表盘左栏那块「产线详情」—— 同一个 `OccupancyPanel`、
 * 同一套「每人物一块、块内制造 / 科研 / 反应容量条」的渲染，不另做一份表格：
 * 早先那张表有「队列时长 / 技能等级」两列常年 N/A，且只统计**正在生产**的计划。
 *
 * 口径由桥决定（`ui_qml.bridge.char_usage_bridge`）：这里按**已规划**算 ——
 * 待生产 / 生产中 / 待下线的计划都占着产线，排产阶段就能看出谁的线会超。
 */

Item {
    id: page

    readonly property var occ: typeof bridge !== "undefined" ? bridge : null

    Rectangle {
        anchors.fill: parent
        color: Theme.bgDark
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingSm
        spacing: Theme.spacingSm

        OccupancyPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(220 * Theme.fontScale)
            occupancyBridge: page.occ
        }

        // ── 人物规划（重构阶段 5）：自动派人 + 按人物槽位排程 ──
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            Text {
                Layout.fillWidth: true
                text: page.occ ? page.occ.planSummary : ""
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: Math.round(12 * Theme.fontScale)
                font.bold: true
                wrapMode: Text.WordWrap
            }
            FButton {
                objectName: "applyAssignButton"
                primary: true
                text: qsTr("按建议派人")
                onClicked: if (page.occ)
                    page.occ.applyAssignments()
            }
        }

        FPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(200 * Theme.fontScale)
            title: qsTr("派人建议（技能达标 → 剩余槽够 → 时长最短；等子项的母项不占槽）")
            PanelTable {
                anchors.fill: parent
                headers: [qsTr("计划"), qsTr("线型"), qsTr("线数"), qsTr("当前人物"), qsTr("建议人物"), qsTr("说明"), qsTr("开工"), qsTr("完工")]
                ratios: [2.2, 0.6, 0.5, 1, 1, 2.4, 0.9, 0.9]
                rows: page.occ ? page.occ.planRows : []
                emptyText: qsTr("没有待生产 / 生产中的计划")
            }
        }

        FPanel {
            id: lanePanel
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: qsTr("人物时间线（小时，从现在起）")

            Flickable {
                anchors.fill: parent
                clip: true
                contentHeight: laneCol.implicitHeight
                ScrollBar.vertical: ScrollBar {}

                Column {
                    id: laneCol
                    width: parent.width
                    spacing: Theme.spacingXs
                    readonly property int nameW: Math.round(90 * Theme.fontScale)
                    readonly property real scale: page.occ && page.occ.laneHours > 0
                                                  ? (width - nameW - Theme.spacingSm) / page.occ.laneHours : 1

                    Repeater {
                        model: page.occ ? page.occ.lanes : []

                        Item {
                            id: lane
                            required property var modelData
                            width: laneCol.width
                            height: Math.max(barRow.childrenRect.height, Math.round(26 * Theme.fontScale))

                            Text {
                                width: laneCol.nameW
                                text: lane.modelData.name
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Math.round(12 * Theme.fontScale)
                                elide: Text.ElideRight
                            }
                            Item {
                                id: barRow
                                x: laneCol.nameW + Theme.spacingSm
                                width: lane.width - x
                                // 同一泳道内重叠的作业错开成几行（按开工排、首个不冲突的行）
                                readonly property var rowsOf: {
                                    const ends = []
                                    const out = []
                                    const bars = lane.modelData.bars
                                    for (let i = 0; i < bars.length; ++i) {
                                        let r = 0
                                        while (r < ends.length && ends[r] > bars[i].start + 1e-6)
                                            ++r
                                        ends[r] = bars[i].start + bars[i].duration
                                        out.push(r)
                                    }
                                    return out
                                }
                                Repeater {
                                    model: lane.modelData.bars
                                    Rectangle {
                                        required property var modelData
                                        required property int index
                                        readonly property int rowH: Math.round(20 * Theme.fontScale)
                                        x: modelData.start * laneCol.scale
                                        y: barRow.rowsOf[index] * (rowH + 2)
                                        width: Math.max(2, modelData.duration * laneCol.scale)
                                        height: rowH
                                        radius: Theme.radiusSmall
                                        color: modelData.color
                                        opacity: 0.85
                                        Text {
                                            anchors.fill: parent
                                            anchors.leftMargin: 4
                                            verticalAlignment: Text.AlignVCenter
                                            text: parent.modelData.label
                                            color: Theme.bgDark
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Math.round(11 * Theme.fontScale)
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Text {
                Layout.fillWidth: true
                text: page.occ && page.occ.error !== "" ? page.occ.error
                      : qsTr("按「已规划」统计：待生产 / 生产中 / 待下线的计划都占着产线")
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Math.round(11 * Theme.fontScale)
                elide: Text.ElideRight
            }

            FButton {
                text: qsTr("关闭")
                onClicked: if (page.occ)
                    page.occ.reject()
            }
        }
    }
}
