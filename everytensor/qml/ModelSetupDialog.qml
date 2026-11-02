import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "modelSetupDialog"
    property var state: modelSetup.state
    readonly property bool busy: modelSetup.active
    readonly property string phase: state.phase || "needed"
    modal: true
    focus: true
    dim: true
    padding: 28
    height: Math.min(610, window.height - 48)
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
                Text { text: "安装 EmbeddingGemma 2 后，可以用一句话寻找照片、录音和视频。文件名和轻量文字搜索现在就能使用。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: examples.implicitHeight + 28
                    radius: 10; color: Theme.background
                    ColumnLayout {
                        id: examples; anchors.fill: parent; anchors.margins: 14; spacing: 10
                        Text { text: "试着描述你想找的内容"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 13; font.weight: Font.Medium }
                        Text { text: "照片    海边的一只狗\n录音    讨论项目预算的部分\n视频    把杯子放在桌上的画面"; lineHeight: 1.5; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13 }
                    }
                }
                Text { text: "下载约 " + modelSetup.downloadLabel + "，包含模型和运行组件。安装时需联网；之后的搜索和内容分析均在本机完成，文件不会上传。Windows 版使用 CPU 推理。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { text: "保存位置：" + modelSetup.location; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                ColumnLayout {
                    visible: dialog.phase !== "needed"; Layout.fillWidth: true; spacing: 8
                    Text { objectName: "modelInstallDetail"; text: dialog.state.detail || (modelSetup.ready ? "模型和组件已通过验证，可以开始搜索。" : "继续安装会保留已下载的进度。"); color: Theme.ink; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                    ProgressBar { Layout.fillWidth: true; from: 0; to: Math.max(1, Number(dialog.state.total || 1)); value: modelSetup.ready ? to : Number(dialog.state.received || 0); indeterminate: dialog.phase === "installing" || dialog.phase === "verifying" }
                    Text { visible: dialog.phase === "downloading" || dialog.phase === "paused"; text: (Number(dialog.state.received || 0) / 1000000).toFixed(0) + " / " + (Number(dialog.state.total || 0) / 1000000).toFixed(0) + " MB"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
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
