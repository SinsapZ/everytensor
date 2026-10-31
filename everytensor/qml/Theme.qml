pragma Singleton
import QtQuick

QtObject {
    readonly property color background: "#f4f3ef"
    readonly property color paper: "#ffffff"
    readonly property color soft: "#f8f7f4"
    readonly property color ink: "#252722"
    readonly property color secondary: "#656b61"
    readonly property color quiet: "#6a6f65"
    readonly property color line: "#e2e3dc"
    readonly property color hover: "#eeeee8"
    readonly property color accent: "#a53f36"
    readonly property color accentHover: "#8e332c"
    readonly property color selection: "#f3e7e2"
    readonly property color selectionLine: "#ddbbb0"
    readonly property color success: "#68846a"
    readonly property string family: desktop.fontFamily
    readonly property int fast: desktop.reducedMotion ? 0 : 110
    readonly property int normal: desktop.reducedMotion ? 0 : 180
}
