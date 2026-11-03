import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "modelSetupDialog"
    property var state: modelSetup.state
    readonly property bool busy: modelSetup.active
    readonly property string phase: state.phase || "needed"
    function bytes(value) {
        return value >= 1000000000 ? (value / 1000000000).toFixed(2) + " GB" : (value / 1000000).toFixed(1) + " MB"
    }
    function remaining(seconds) {
        if (seconds < 60) return "预计还需不到 1 分钟"
        if (seconds < 3600) return "预计还需约 " + Math.ceil(seconds / 60) + " 分钟"
        return "预计还需约 " + (seconds / 3600).toFixed(1) + " 小时"
    }
    modal: true
    focus: true
    dim: true
    padding: 28
    height: Math.min(dialog.phase === "needed" ? 610 : 410, window.height - 48)
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { radius: 16; color: Theme.paper; border.color: Theme.line }
    Overlay.modal: Rectangle { color: "#50252722" }
    onClosed: modelSetup.dismiss()
    contentItem: ColumnLayout {
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Text { text: modelSetup.ready ? "多模态搜索已就绪" : "让搜索看懂图片和音视频"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 22; font.weight: Font.DemiBold; Layout.fillWidth: true }
            DeskButton { objectName: "closeModelSetupButton"; text: "关闭"; compact: true; onClicked: dialog.close() }
        }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: parent.width; spacing: 16
                Text { text: "安装后，可以用一句话寻找照片、录音和视频。文件名和文字搜索现在就能使用。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Rectangle {
                    objectName: "modelInstallExamples"
                    visible: dialog.phase === "needed"
                    Layout.fillWidth: true; implicitHeight: examples.implicitHeight + 28
                    radius: 10; color: Theme.background
                    ColumnLayout {
                        id: examples; anchors.fill: parent; anchors.margins: 14; spacing: 10
                        Text { text: "试着描述你想找的内容"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 13; font.weight: Font.Medium }
                        Text { text: "照片    海边的一只狗\n录音    讨论项目预算的部分\n视频    把杯子放在桌上的画面"; lineHeight: 1.5; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }
                Text { text: "下载约 " + modelSetup.downloadLabel + "。安装时需联网；之后的搜索和内容分析均在本机完成，文件不会上传。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                ColumnLayout {
                    visible: dialog.phase !== "needed"; Layout.fillWidth: true; spacing: 8
                    Text { objectName: "modelInstallDetail"; text: dialog.state.detail || (modelSetup.ready ? "模型和组件已通过验证，可以开始搜索。" : "继续安装会保留已下载的进度。"); color: Theme.ink; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                    ProgressBar { objectName: "modelInstallProgress"; Layout.fillWidth: true; from: 0; to: Math.max(1, Number(dialog.state.total || 1)); value: modelSetup.ready ? to : Math.min(to, Number(dialog.state.received || 0)); indeterminate: dialog.phase === "installing" || dialog.phase === "verifying" }
                    Text {
                        objectName: "modelInstallBytes"
                        visible: ["downloading", "paused", "pausing", "error"].indexOf(dialog.phase) >= 0
                        text: dialog.bytes(Number(dialog.state.received || 0)) + " / " + dialog.bytes(Number(dialog.state.total || 0))
                              + "  ·  " + Math.min(100, Math.floor(Number(dialog.state.received || 0) * 100 / Math.max(1, Number(dialog.state.total || 1)))) + "%"
                        color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11
                    }
                    Text {
                        objectName: "modelInstallSpeed"
                        visible: dialog.phase === "downloading"
                        text: dialog.state.stalled ? "暂未收到新数据，正在等待连接恢复；可暂停后继续。"
                              : dialog.state.transfer_state === "retrying" ? "正在自动重试，已下载部分会保留。"
                              : Number(dialog.state.speed_bps || 0) > 0 ? (Number(dialog.state.speed_bps) < 1000000
                                ? (Number(dialog.state.speed_bps) / 1000).toFixed(1) + " KB" : dialog.bytes(Number(dialog.state.speed_bps))) + "/s"
                                + (Number(dialog.state.eta_seconds) >= 0 ? "  ·  " + dialog.remaining(Number(dialog.state.eta_seconds)) : "")
                              : dialog.state.transfer_state === "checking" ? "校验文件时不占用下载带宽。"
                              : "连接后显示下载速度和预计剩余时间。"
                        color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12
                        wrapMode: Text.Wrap; Layout.fillWidth: true
                    }
                    Text {
                        visible: dialog.busy
                        text: "关闭此窗口可在后台继续；退出应用会保留下载进度。预计时间仅包含下载，之后还需安装和验证。"
                        color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true
                    }
                }
                Text { objectName: "modelInstallError"; visible: text.length > 0; text: dialog.state.error || ""; color: Theme.accent; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                DeskButton { visible: dialog.phase === "error" && !!dialog.state.local_model; text: "改为下载模型"; onClicked: modelSetup.downloadInstead() }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            DeskButton { objectName: "importModelButton"; visible: !dialog.busy && !modelSetup.ready; text: "使用已有模型…"; onClicked: { var path = desktop.chooseFolder(); if(path) modelSetup.useLocalModel(path); } }
            Item { Layout.fillWidth: true }
            DeskButton { objectName: "deferModelButton"; visible: !modelSetup.ready; text: dialog.busy ? "在后台继续" : "稍后再说"; onClicked: dialog.close() }
            DeskButton { objectName: "pauseModelButton"; visible: dialog.busy; enabled: dialog.phase !== "pausing"; text: dialog.phase === "pausing" ? "正在暂停…" : "暂停"; onClicked: modelSetup.pause() }
            DeskButton { objectName: "startModelButton"; visible: !dialog.busy; primary: true; text: modelSetup.ready ? "开始搜索" : dialog.phase === "paused" ? "继续安装" : dialog.phase === "error" ? "重试" : "安装多模态模型"; onClicked: { if(modelSetup.ready) dialog.close(); else modelSetup.start(); } }
        }
    }
}
