import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/* 单行价格来源设置：<标签> [Hub▾] [价格类型▾] [倍率]。
 *
 * 材料行与成品行各一个实例，标签不同。原实现是 `price_source_widget.py`
 * 的 `PriceSourceRow`（阶段 2b 删除）。
 *
 * **不使用属性绑定同步状态**：`hub`/`priceType`/`mult` 是「入参」，
 * 用 `applySettings()` 赋值即可；若写成 `currentIndex: model.indexOf(root.hub)`
 * 这类绑定，用户在界面上的选择会被绑定立刻冲回去（切不动的经典表现）。
 * 用户改动走 `activated` / `valueModified`，只在这两个信号里对外汇报。
 */
RowLayout {
    id: root

    property string label: ""
    property var hubs: []
    property var priceTypes: []
    property string hub: ""
    property string priceType: "sell"
    property real mult: 1.0
    /* 本行三个控件的统一高度。
     *
     * 工具栏里按钮走 `FButton.compact`（28px）时，下拉框还是 26、微调框还是 32，
     * 一行里三种高度看着就是「大小不一」。由调用方传同一个值即可对齐；
     * 默认 32 = 各控件的历史高度，不影响其它调用方。 */
    property int controlHeight: 32

    signal hubEdited(string value)
    signal priceTypeEdited(string value)
    signal multEdited(real value)

    spacing: Theme.spacingXs

    readonly property int longestHubChars: {
        var n = 0
        for (var i = 0; i < hubs.length; ++i) {
            var h = hubs[i]
            n = Math.max(n, String(typeof h === "object" ? h.label : h).length)
        }
        return n
    }

    function hubValue(i: int): string {
        var h = root.hubs[i]
        return typeof h === "object" ? h.value : h
    }

    function applySettings(hubValue: string, typeValue: string, multValue: real): void {
        var hi = -1
        for (var j = 0; j < root.hubs.length; ++j) {
            if (root.hubValue(j) === hubValue) {
                hi = j
                break
            }
        }
        if (hi >= 0 && hi !== hubCombo.currentIndex)
            hubCombo.currentIndex = hi
        var ti = 0
        for (var i = 0; i < root.priceTypes.length; ++i) {
            if (root.priceTypes[i].value === typeValue) {
                ti = i
                break
            }
        }
        if (ti !== typeCombo.currentIndex)
            typeCombo.currentIndex = ti
        if (Math.abs(multSpin.value - multValue) > 0.0001)
            multSpin.value = multValue
    }

    Text {
        text: root.label
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: Math.round(12 * Theme.fontScale)
        font.bold: true
        // 行高由三个控件决定（被拉满），文字必须自己居中 —— 否则标签贴在行顶，
        // 与右侧控件的中线错开半行
        verticalAlignment: Text.AlignVCenter
    }

    FComboBox {
        id: hubCombo
        model: root.hubs
        textRole: root.hubs.length && typeof root.hubs[0] === "object" ? "label" : ""
        implicitHeight: root.controlHeight
        // 中文中心名（如「索巴色基」）按最长条目留宽，避免测宽时 textRole 尚未生效导致截断
        // 按最长标签字符数估宽（不在绑定里改 TextMetrics.text，避免绑定循环）
        implicitWidth: Math.max(80, maxItemWidth, root.longestHubChars * Theme.fs(13)) + 2 * Theme.spacingLg + Theme.fs(16)
        onActivated: root.hubEdited(root.hubValue(currentIndex))
    }

    FComboBox {
        id: typeCombo
        model: root.priceTypes
        textRole: "label"
        implicitHeight: root.controlHeight
        implicitWidth: Math.max(72, maxItemWidth + 2 * Theme.spacingLg)
        onActivated: root.priceTypeEdited(root.priceTypes[currentIndex].value)
    }

    FDoubleSpinBox {
        id: multSpin
        from: 0.1
        to: 10.0
        stepSize: 0.05
        decimals: 2
        implicitHeight: root.controlHeight
        implicitWidth: 84
        onValueModified: root.multEdited(value)
    }
}
