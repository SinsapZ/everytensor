import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

AbstractButton {
    id: control
    property string iconName: ""
    property bool primary: false
    property bool subtle: false
    property bool compact: false
    property string tooltip: ""
    property bool tooltipSuppressed: false
    property bool allowTooltipsDuringModal: false
    readonly property bool tooltipBlocked: !control.Window.window || (control.Window.window.tooltipsBlocked === true && !allowTooltipsDuringModal)
    implicitWidth: Math.max(compact ? 34 : 38, contentRow.implicitWidth + (text.length ? 28 : 18))
    implicitHeight: compact ? 34 : 38
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text.length ? text : tooltip
    onPressed: tooltipSuppressed = true
    onHoveredChanged: if (!hovered) tooltipSuppressed = false
    opacity: enabled ? 1 : 0.45
    background: Rectangle {
        radius: 8
        color: control.primary ? (control.down || control.hovered ? Theme.accentHover : Theme.accent)
             : control.down ? Theme.line : control.checked ? Theme.selection : control.hovered ? Theme.hover : control.subtle ? "transparent" : Theme.paper
        border.color: control.visualFocus ? Theme.accent : control.primary || control.subtle ? "transparent" : Theme.line
        border.width: control.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: RowLayout {
        id: contentRow
        spacing: 7
        Item { Layout.fillWidth: true }
        Icon { visible: control.iconName.length > 0; name: control.iconName; color: control.primary ? "white" : Theme.ink; Layout.preferredWidth: 17; Layout.preferredHeight: 17 }
        Text { visible: control.text.length > 0; text: control.text; font.family: Theme.family; font.pixelSize: 13; font.weight: Font.Medium; color: control.primary ? "white" : Theme.ink }
        Item { Layout.fillWidth: true }
    }
    Hint {
        objectName: control.objectName.length ? control.objectName + "_tooltip" : ""
        parent: control
        visible: control.hovered && control.enabled && !control.down && !control.tooltipSuppressed
                 && !control.tooltipBlocked && control.tooltip.length > 0
        text: control.tooltip
    }
}
