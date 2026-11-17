import QtQuick

// 场景坐标不随列边界移动；按下即抓住鼠标，避免表头 Flickable 抢走拖动。
MouseArea {
    id: handle
    required property real columnWidth
    signal resized(real newWidth)
    property real pressX: 0
    property real pressWidth: 0
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    width: 8
    z: 2
    cursorShape: Qt.SplitHCursor
    acceptedButtons: Qt.LeftButton
    preventStealing: true
    onPressed: function(mouse) {
        pressX = mapToItem(null, mouse.x, mouse.y).x
        pressWidth = columnWidth
    }
    onPositionChanged: function(mouse) {
        if (pressed)
            resized(pressWidth + mapToItem(null, mouse.x, mouse.y).x - pressX)
    }
}
