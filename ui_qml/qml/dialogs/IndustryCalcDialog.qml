import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

/* 制造计算（重构阶段 3，开明网式）。
 *
 * 上：成品选择（搜索 + 已选成品数量）→ 参数条（制造 / 反应机库、ME/TE、价格中心、人物、
 *     手填指数、税率、四个类别造买开关）→ 一行汇总。
 * 下：四个页签 —— 原料汇总（按类别分组，可复制本组）/ 逐级分解（制造、反应两条轨道逐层列）/
 *     成本与利润（分项 + 各步骤费用表）/ 采购清单（游戏「批量购买」格式）。
 *
 * 所有数字、颜色、分组都由 `ui_qml/bridge/industry_calc_bridge.py` 算好，这里只排版。
 * 原料行 / 逐级分解里的中间件**点一下 = 切换「直接买 / 自己做」**（只对有配方的物品生效）。
 */

FDialogFrame {
    id: frame

    readonly property var ic: typeof bridge !== "undefined" ? bridge : null
    readonly property int fnt: Math.round(12 * Theme.fontScale)
    readonly property int fntSmall: Math.round(11 * Theme.fontScale)
    readonly property int rowH: Math.round(24 * Theme.fontScale)

    dlg: frame.ic
    acceptText: qsTr("加入规划")
    acceptEnabled: frame.ic ? frame.ic.targets.length > 0 : false
    cancelText: qsTr("关闭")

    // ── 成品选择 ──────────────────────────────────────────────
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingSm

        FTextField {
            id: searchField
            objectName: "searchField"
            Layout.preferredWidth: Math.round(260 * Theme.fontScale)
            placeholderText: qsTr("搜索成品（中文 / 英文）…")
            onTextChanged: if (frame.ic)
                frame.ic.setQuery(text)
        }

        Flow {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Repeater {
                model: frame.ic ? frame.ic.targets : []

                Rectangle {
                    id: chip
                    required property var modelData
                    required property int index

                    height: Math.round(32 * Theme.fontScale)
                    width: chipRow.implicitWidth + Theme.spacingSm * 2
                    radius: Theme.radius
                    color: Theme.bgSurface
                    border.width: 1
                    border.color: Theme.border

                    RowLayout {
                        id: chipRow
                        anchors.centerIn: parent
                        spacing: Theme.spacingXs

                        Text {
                            text: chip.modelData.name
                            color: Theme.textPrimary
                            font.family: Theme.fontFamily
                            font.pixelSize: frame.fnt
                        }
                        FSpinBox {
                            Layout.preferredWidth: Math.round(96 * Theme.fontScale)
                            from: 1
                            to: 100000
                            value: chip.modelData.qty
                            onValueModified: frame.ic.setTargetQty(chip.index, value)
                        }
                        FButton {
                            compact: true
                            text: "×"
                            onClicked: frame.ic.removeTarget(chip.index)
                        }
                    }
                }
            }
        }
    }

    // 搜索候选（输入后出现，点一条加入成品）
    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(sugList.contentHeight + 2, Math.round(180 * Theme.fontScale))
        visible: frame.ic ? frame.ic.suggestions.length > 0 && searchField.text.length > 0 : false
        color: Theme.bgSurface
        radius: Theme.radius
        border.width: 1
        border.color: Theme.border

        ListView {
            id: sugList
            objectName: "suggestionList"
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            model: frame.ic ? frame.ic.suggestions : []
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: ItemDelegate {
                required property var modelData
                required property int index
                width: sugList.width
                height: frame.rowH
                contentItem: Text {
                    text: modelData.name
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: frame.fnt
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: {
                    frame.ic.pickSuggestion(index)
                    searchField.text = ""
                }
            }
        }
    }

    // ── 参数条 ────────────────────────────────────────────────
    GridLayout {
        Layout.fillWidth: true
        columns: 8
        columnSpacing: Theme.spacingSm
        rowSpacing: Theme.spacingXs

        component Lbl: Text {
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: frame.fnt
        }

        Lbl { text: qsTr("制造地点") }
        FComboBox {
            objectName: "mfgHangar"
            Layout.fillWidth: true
            model: frame.ic ? frame.ic.hangarOptions : []
            currentIndex: frame.ic ? frame.ic.mfgIndex : 0
            onActivated: frame.ic.setMfgIndex(currentIndex)
        }
        Lbl { text: qsTr("反应地点") }
        FComboBox {
            objectName: "reactHangar"
            Layout.fillWidth: true
            model: frame.ic ? frame.ic.hangarOptions : []
            currentIndex: frame.ic ? frame.ic.reactIndex : 0
            onActivated: frame.ic.setReactIndex(currentIndex)
        }
        Lbl { text: qsTr("价格中心") }
        FComboBox {
            Layout.fillWidth: true
            model: frame.ic ? frame.ic.hubOptions : []
            currentIndex: frame.ic ? frame.ic.hubIndex : 0
            onActivated: frame.ic.setHubIndex(currentIndex)
        }
        Lbl { text: qsTr("人物") }
        FComboBox {
            Layout.fillWidth: true
            model: frame.ic ? frame.ic.charOptions : []
            currentIndex: frame.ic ? frame.ic.charIndex : 0
            onActivated: frame.ic.setCharIndex(currentIndex)
        }

        Lbl { text: qsTr("蓝图 ME") }
        FSpinBox {
            Layout.fillWidth: true
            from: 0
            to: 10
            value: frame.ic ? frame.ic.me : 10
            onValueModified: frame.ic.setMe(value)
        }
        Lbl { text: qsTr("蓝图 TE") }
        FSpinBox {
            Layout.fillWidth: true
            from: 0
            to: 20
            value: frame.ic ? frame.ic.te : 20
            onValueModified: frame.ic.setTe(value)
        }
        Lbl { text: qsTr("制造指数 %") }
        FDoubleSpinBox {
            Layout.fillWidth: true
            decimals: 2
            from: 0
            to: 100
            value: frame.ic ? frame.ic.mfgCostIndex : 0
            onValueModified: frame.ic.setMfgCostIndex(value)
        }
        Lbl { text: qsTr("反应指数 %") }
        FDoubleSpinBox {
            Layout.fillWidth: true
            decimals: 2
            from: 0
            to: 100
            value: frame.ic ? frame.ic.reactCostIndex : 0
            onValueModified: frame.ic.setReactCostIndex(value)
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingMd

        Repeater {
            model: frame.ic ? frame.ic.toggleRows : []

            FCheckBox {
                required property var modelData
                text: modelData.label + "：" + (modelData.checked ? modelData.on : modelData.off)
                checked: modelData.checked
                onToggled: frame.ic.setToggle(modelData.key, checked)
            }
        }
        Text {
            text: qsTr("销售税 %")
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: frame.fnt
        }
        FDoubleSpinBox {
            Layout.preferredWidth: Math.round(90 * Theme.fontScale)
            decimals: 2
            from: 0
            to: 20
            value: frame.ic ? frame.ic.salesTax : 3.6
            onValueModified: frame.ic.setSalesTax(value)
        }
        Text {
            text: qsTr("中介费 %")
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: frame.fnt
        }
        FDoubleSpinBox {
            Layout.preferredWidth: Math.round(90 * Theme.fontScale)
            decimals: 2
            from: 0
            to: 20
            value: frame.ic ? frame.ic.brokerFee : 1.5
            onValueModified: frame.ic.setBrokerFee(value)
        }
        Item {
            Layout.fillWidth: true
        }
        FButton {
            text: qsTr("恢复默认")
            onClicked: frame.ic.resetDefaults()
        }
    }

    Text {
        objectName: "summaryText"
        Layout.fillWidth: true
        text: frame.ic ? frame.ic.summaryText : ""
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: frame.fnt
        font.bold: true
        wrapMode: Text.WordWrap
    }

    // ── 页签 ──────────────────────────────────────────────────
    RowLayout {
        Layout.fillWidth: true
        FTabBar {
            id: tabs
            TabButton { text: qsTr("原料汇总") }
            TabButton { text: qsTr("逐级分解") }
            TabButton { text: qsTr("成本与利润") }
            TabButton { text: qsTr("采购清单") }
        }
        Item {
            Layout.fillWidth: true
        }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: tabs.currentIndex

        // ① 原料汇总
        ScrollView {
            id: rawView
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: rawView.availableWidth
                spacing: Theme.spacingSm

                Repeater {
                    model: frame.ic ? frame.ic.rawGroups : []

                    FSection {
                        id: grp
                        required property var modelData
                        Layout.fillWidth: true
                        title: modelData.title + "　" + modelData.subtitle

                        RowLayout {
                            Layout.fillWidth: true
                            Item {
                                Layout.fillWidth: true
                            }
                            FButton {
                                compact: true
                                text: qsTr("复制本组")
                                onClicked: frame.ic.copyText(grp.modelData.copyText)
                            }
                        }

                        Repeater {
                            model: grp.modelData.rows

                            Rectangle {
                                id: rawRow
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                implicitHeight: frame.rowH
                                color: rawRow.index % 2 === 0 ? Theme.bgSurface : Theme.bgDark

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: Theme.spacingSm
                                    anchors.rightMargin: Theme.spacingSm
                                    spacing: Theme.spacingSm

                                    Repeater {
                                        model: rawRow.modelData.cells
                                        Text {
                                            required property var modelData
                                            required property int index
                                            Layout.fillWidth: index === 0
                                            Layout.preferredWidth: index === 0 ? -1 : Math.round(120 * Theme.fontScale)
                                            horizontalAlignment: index === 0 ? Text.AlignLeft : Text.AlignRight
                                            text: modelData.text
                                            color: modelData.color !== "" ? modelData.color : Theme.textPrimary
                                            font.family: index === 0 ? Theme.fontFamily : "Consolas"
                                            font.pixelSize: frame.fnt
                                            elide: Text.ElideRight
                                        }
                                    }
                                    // 始终占位（无配方的行只是透明不可点）：否则有按钮的行列宽变窄、数字列对不齐
                                    FButton {
                                        compact: true
                                        opacity: rawRow.modelData.buildable ? 1 : 0
                                        enabled: rawRow.modelData.buildable
                                        text: qsTr("改自己做")
                                        onClicked: frame.ic.toggleBuild(rawRow.modelData.typeId)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ② 逐级分解：每层一列，横向滚动
        ScrollView {
            id: waveView
            clip: true

            Row {
                spacing: Theme.spacingSm

                Repeater {
                    model: frame.ic ? frame.ic.waveColumns : []

                    Rectangle {
                        id: col
                        required property var modelData
                        width: Math.round(230 * Theme.fontScale)
                        height: waveView.availableHeight
                        color: Theme.bgSurface
                        radius: Theme.radius
                        border.width: 1
                        border.color: Theme.border

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingSm
                            spacing: Theme.spacingXs

                            Text {
                                text: col.modelData.title
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: frame.fnt
                                font.bold: true
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                model: col.modelData.rows
                                spacing: 2
                                delegate: ItemDelegate {
                                    required property var modelData
                                    width: ListView.view.width
                                    height: Math.round(38 * Theme.fontScale)
                                    ToolTip.visible: hovered
                                    ToolTip.text: qsTr("点击：改为直接买")
                                    onClicked: frame.ic.toggleBuild(modelData.typeId)
                                    contentItem: Column {
                                        Text {
                                            width: parent.width
                                            text: modelData.text
                                            color: modelData.color
                                            font.family: Theme.fontFamily
                                            font.pixelSize: frame.fnt
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            text: modelData.detail
                                            color: Theme.textSecondary
                                            font.family: Theme.fontFamily
                                            font.pixelSize: frame.fntSmall
                                        }
                                    }
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: col.modelData.footer
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: frame.fntSmall
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }

        // ③ 成本与利润
        RowLayout {
            spacing: Theme.spacingSm

            ColumnLayout {
                Layout.preferredWidth: Math.round(420 * Theme.fontScale)
                Layout.fillHeight: true
                FSection {
                    Layout.fillWidth: true
                    title: qsTr("成本")
                    FFieldList {
                        fields: frame.ic ? frame.ic.costFields : []
                        labelWidth: Math.round(150 * Theme.fontScale)
                    }
                }
                FSection {
                    Layout.fillWidth: true
                    title: qsTr("收入与现金结余")
                    FFieldList {
                        fields: frame.ic ? frame.ic.profitFields : []
                        labelWidth: Math.round(150 * Theme.fontScale)
                    }
                }
                Item {
                    Layout.fillHeight: true
                }
            }

            FPanel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: qsTr("各生产步骤费用估算")
                PanelTable {
                    anchors.fill: parent
                    headers: [qsTr("作业"), qsTr("线×流程"), qsTr("物品估价"), qsTr("指数"), qsTr("作业费"), qsTr("单线用时"), qsTr("边角料")]
                    ratios: [2.2, 0.9, 1.1, 0.8, 1, 1.5, 0.8]
                    rows: frame.ic ? frame.ic.jobRows : []
                    emptyText: qsTr("没有生产步骤")
                }
            }
        }

        // ④ 采购清单
        ColumnLayout {
            spacing: Theme.spacingSm
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: qsTr("复制后在游戏市场打开「批量购买」直接粘贴。格式：物品名 + Tab + 数量（自采 / 自种的不列）。")
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: frame.fnt
                    wrapMode: Text.WordWrap
                }
                FButton {
                    primary: true
                    text: qsTr("复制全部")
                    onClicked: frame.ic.copyText(frame.ic.multibuyText)
                }
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    objectName: "multibuyText"
                    readOnly: true
                    text: frame.ic ? frame.ic.multibuyText : ""
                    color: Theme.textPrimary
                    font.family: "Consolas"
                    font.pixelSize: frame.fnt
                    background: Rectangle {
                        color: Theme.bgSurface
                        radius: Theme.radius
                        border.width: 1
                        border.color: Theme.border
                    }
                }
            }
        }
    }
}
