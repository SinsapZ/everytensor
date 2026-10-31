import QtQuick
import QtQuick.Controls

ToolTip {
    id: hint
    delay: 600
    timeout: 4000
    padding: 9
    leftPadding: 11
    rightPadding: 11
    margins: 10
    y: parent ? parent.height + 7 : 0
    implicitWidth: Math.min(420, Math.max(40, implicitContentWidth + leftPadding + rightPadding))
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    font.family: Theme.family
    font.pixelSize: 12
    contentItem: Text {
        text: hint.text
        font: hint.font
        color: "#ffffff"
        wrapMode: Text.Wrap
    }
    background: Rectangle {
        radius: 7
        color: Theme.ink
    }
}
