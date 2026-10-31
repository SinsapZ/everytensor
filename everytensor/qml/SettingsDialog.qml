import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "settingsDialog"
    property var draft: ({})
    property bool submitting: false
    property bool advanced: false
    property string localError: ""
    modal: true
    dim: true
    padding: 28
    closePolicy: submitting ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { radius: 16; color: Theme.paper; border.color: Theme.line }
    Overlay.modal: Rectangle { color: "#50252722"; Behavior on opacity { NumberAnimation { duration: Theme.normal } } }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
    onOpened: {
        draft = JSON.parse(JSON.stringify(client.settings || {}));
        localError = ""; submitting = false;
        budgetField.text = String(draft.budget_gib || 8);
    }
    onClosed: appliedTimer.stop()
    function edit(key, value) { var next = Object.assign({}, draft); next[key] = value; draft = next; }
    function addFolder(key) { var path = desktop.chooseFolder(); if(path) { var values = (draft[key] || []).slice(); if(values.indexOf(path) < 0) values.push(path); edit(key, values); } }
    function removeFolder(key, index) { var values = (draft[key] || []).slice(); values.splice(index, 1); edit(key, values); }
    function save() {
        var amount = Number(budgetField.text);
        if(!isFinite(amount) || amount < .125 || amount > 1024) { localError = "索引空间请输入 0.125 至 1024 GiB。"; return; }
        if(draft.scope === "folders" && !(draft.roots || []).length) { localError = "请至少添加一个搜索文件夹。"; return; }
        edit("budget_gib", amount); localError = ""; submitting = true;
        client.applySettings({scope: draft.scope || "computer", roots: draft.roots || [], exclusions: draft.exclusions || [], dimensions: draft.dimensions || 256, budget_gib: amount, model: draft.model || ""});
        appliedTimer.start();
    }
    function number(value) { return Number(value || 0).toLocaleString(Qt.locale(), "f", 0); }
    Timer {
        id: appliedTimer; interval: 100; repeat: true
        onTriggered: if(!client.settingsApplying) { stop(); dialog.submitting = false; if(!client.settingsError) dialog.close(); }
    }
    contentItem: ColumnLayout {
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Text { text: "搜索与索引"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 23; font.weight: Font.DemiBold; Layout.fillWidth: true }
            DeskButton { iconName: "close"; subtle: true; tooltip: "关闭设置"; allowTooltipsDuringModal: true; enabled: !dialog.submitting; onClicked: dialog.close() }
        }
        Text { text: "让搜索按你的习惯工作。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13; Layout.topMargin: 6; Layout.bottomMargin: 24 }
        ScrollView {
            id: settingsViewport
            objectName: "settingsScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.vertical: ScrollBar {
                id: settingsScrollbar
                policy: settingsContent.implicitHeight > settingsViewport.availableHeight ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                width: 6
                contentItem: Rectangle { implicitWidth: 6; radius: 3; color: settingsScrollbar.pressed ? Theme.secondary : Theme.line }
            }
            ColumnLayout {
                id: settingsContent
                width: settingsViewport.availableWidth - 16
                spacing: 16
                Text { text: "搜索范围"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; font.weight: Font.DemiBold }
                RowLayout {
                    spacing: 8
                    Repeater {
                        model: [{key:"computer", label:"这台电脑"}, {key:"folders", label:"指定文件夹"}]
                        delegate: DeskButton { required property var modelData; text: modelData.label; primary: (dialog.draft.scope || "computer") === modelData.key; onClicked: dialog.edit("scope", modelData.key) }
                    }
                }
                Text { text: dialog.draft.scope === "folders" ? "仅搜索下方文件夹。" : "搜索本机固定磁盘，也可以添加其他文件夹。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Repeater {
                    model: [{key:"roots", label:"搜索文件夹"}, {key:"exclusions", label:"排除文件夹"}]
                    delegate: ColumnLayout {
                        id: folderSection
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: folderSection.modelData.label; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; Layout.fillWidth: true }
                            DeskButton { text: "添加"; iconName: "plus"; subtle: true; compact: true; onClicked: dialog.addFolder(folderSection.modelData.key) }
                        }
                        Repeater {
                            model: dialog.draft[folderSection.modelData.key] || []
                            delegate: Rectangle {
                                required property string modelData
                                required property int index
                                Layout.fillWidth: true; implicitHeight: 38
                                color: Theme.soft; radius: 6
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 4; spacing: 8
                                    Icon { name: "folder"; color: Theme.secondary; Layout.preferredWidth: 15; Layout.preferredHeight: 15 }
                                    Text { text: modelData; color: Theme.ink; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                    DeskButton { iconName: "close"; subtle: true; compact: true; tooltip: "移除文件夹"; allowTooltipsDuringModal: true; onClicked: dialog.removeFolder(folderSection.modelData.key, index) }
                                }
                            }
                        }
                        Text { visible: !(dialog.draft[folderSection.modelData.key] || []).length; text: folderSection.modelData.key === "roots" ? "尚未添加额外文件夹" : "尚未添加排除项"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 12 }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        Text { text: "索引空间"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; font.weight: Font.DemiBold }
                        Text { text: "达到上限后暂停扩建，已有搜索仍可使用。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12 }
                    }
                    TextField { id: budgetField; objectName: "budgetField"; Layout.preferredWidth: 74; implicitHeight: 38; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; selectByMouse: true; horizontalAlignment: Text.AlignHCenter; validator: DoubleValidator { bottom: .125; top: 1024; notation: DoubleValidator.StandardNotation } background: Rectangle { radius: 7; color: Theme.soft; border.color: budgetField.activeFocus ? Theme.accent : Theme.line } }
                    Text { text: "GiB"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 13 }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: indexState.implicitHeight + 28
                    radius: 10; color: Theme.soft
                    ColumnLayout {
                        id: indexState
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                        spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            Rectangle { width: 6; height: 6; radius: 3; color: client.paused ? Theme.quiet : Theme.success }
                            Text { text: client.paused ? "索引已暂停" : "本地索引"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true }
                            Text { text: client.stats && client.stats._cached ? "上次记录" : ""; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
                        }
                        Text { text: "已发现 " + dialog.number(client.stats.files) + " 个文件 · 内容语义覆盖 " + dialog.number(client.stats.semantic_files) + " 个文件"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Text { text: client.modelStatus; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        RowLayout {
                            spacing: 5
                            DeskButton { text: client.paused ? "继续索引" : "暂停索引"; iconName: client.paused ? "play" : "pause"; compact: true; onClicked: client.pauseIndexing(!client.paused) }
                            DeskButton { text: "检查更新"; iconName: "refresh"; compact: true; onClicked: client.rescan() }
                        }
                    }
                }
                DeskButton { objectName: "advancedSettingsButton"; text: dialog.advanced ? "收起高级设置" : "高级设置"; iconName: "down"; subtle: true; onClicked: dialog.advanced = !dialog.advanced }
                ColumnLayout {
                    visible: dialog.advanced; Layout.fillWidth: true; spacing: 12
                    RowLayout {
                        Text { text: "向量维度"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 13; Layout.fillWidth: true }
                        Repeater { model: [256, 512, 768]; delegate: DeskButton { required property int modelData; text: String(modelData); compact: true; primary: Number(dialog.draft.dimensions || 256) === modelData; onClicked: dialog.edit("dimensions", modelData) } }
                    }
                    Text { text: "256 维占用更少空间。提高维度需要在后台重新编码。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    DeskButton { text: "选择本地模型"; iconName: "folder"; onClicked: { var path = desktop.chooseFolder(); if(path) dialog.edit("model", path); } }
                    Text { text: dialog.draft.model || "尚未指定模型文件夹"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                    Text { text: "搜索和内容分析在本机完成，原文件始终只读。"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                    Text { text: "覆盖详情"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12
                        text: {
                            var s = client.stats || {}, q = s.quick || {}, c = s.content_states || {}, st = s.storage || {};
                            return "快速语义 · 文件提示 " + dialog.number(q.covered_files) + " / " + dialog.number(q.eligible_files) + " 个文件\n"
                                + "快速语义 · 正文 " + dialog.number(q.text_fragments) + " 个片段\n"
                                + "完整模型 · 已编码 " + dialog.number(s.covered_units) + " / " + dialog.number(s.units) + " 个已提取片段\n"
                                + "长文件继续补齐 " + dialog.number(s.partial_files) + " 个 · 仅文字匹配 " + dialog.number(s.semantic_skipped) + " 个片段\n"
                                + "待分析 " + dialog.number(c.pending) + " 个文件 · 文件失败 " + dialog.number(c.error) + " 个\n"
                                + "索引占用 " + (Number(st.bytes || 0) / 1073741824).toFixed(2) + " / " + (Number(st.limit || 8589934592) / 1073741824).toFixed(1) + " GiB";
                        }
                    }
                    Text { text: "文件提示与正文覆盖不代表已理解图片或视频画面。模型文件另占空间。"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: client.indexStatus || ""; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: ((client.stats || {}).errors || []).concat(((client.stats || {}).embedding_errors || [])).slice(0, 12)
                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            text: (modelData.name || modelData.path || "文件") + "\n" + (modelData.error || "处理失败")
                            color: Theme.accent; font.family: Theme.family; font.pixelSize: 12
                        }
                    }
                }
                Item { Layout.preferredHeight: 8 }
            }
        }
        Text { text: dialog.localError || client.settingsError || ""; visible: text.length > 0; color: Theme.accent; wrapMode: Text.Wrap; font.family: Theme.family; font.pixelSize: 12; Layout.fillWidth: true; Layout.topMargin: 10 }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line; Layout.topMargin: 16; Layout.bottomMargin: 16 }
        RowLayout {
            Layout.fillWidth: true
            Icon { name: "lock"; color: Theme.quiet; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
            Text { text: "仅保存在这台电脑"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; Layout.fillWidth: true }
            DeskButton { text: "取消"; enabled: !dialog.submitting; onClicked: dialog.close() }
            DeskButton { objectName: "saveSettingsButton"; text: dialog.submitting ? "正在保存…" : "保存设置"; primary: true; enabled: !dialog.submitting; onClicked: dialog.save() }
        }
    }
}
