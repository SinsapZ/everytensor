import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    visible: true
    width: 1280; height: 820
    minimumWidth: 920; minimumHeight: 620
    title: "EveryTensor"
    color: Theme.background
    font.family: Theme.family
    font.pixelSize: 14
    property bool browsing: client.query.trim().length > 0 || client.kind.length > 0
    property real preferredListWidth: 360
    property string toastMessage: ""
    property string dismissedWarning: ""
    readonly property bool tooltipsBlocked: settingsDialog.visible
    readonly property string currentNotice: toastMessage || (client.warning !== dismissedWarning ? client.warning : "")
    property int anchorFileId: -1
    property real anchorOffset: 0
    property int restoreRevision: 0
    readonly property bool singleVideo: client.kind === "video" && results.count === 1
    readonly property bool hasSelected: client.selected && !!client.selected.name
    function focusSearch() { search.forceActiveFocus(); search.selectAll(); }
    function goHome() { client.setQuery(""); client.setKind(""); search.forceActiveFocus(); }
    function openCurrent() { if(hasSelected && client.selected.kind === "video") preview.open(); else client.openSelected(); }
    function moveSelection(delta) {
        if(!results.count) return;
        client.markInteraction();
        results.currentIndex = Math.max(0, Math.min(results.count - 1, results.currentIndex + delta));
        results.positionViewAtIndex(results.currentIndex, ListView.Contain);
        results.forceActiveFocus();
        Qt.callLater(function(){ if(results.currentItem) client.selectFile(results.currentItem.fileId, true); });
    }
    Connections {
        target: client
        function onErrorOccurred(message) { window.toastMessage = message; window.dismissedWarning = ""; }
        function onRowsAboutToChange() {
            window.restoreRevision += 1;
            window.anchorFileId = -1;
            if(!client.manualSelection || !results.count) return;
            var first = results.indexAt(8, results.contentY + 1);
            if(first < 0) first = results.indexAt(8, results.contentY + 6);
            var item = results.itemAtIndex(first);
            if(first >= 0 && item) {
                window.anchorFileId = client.results.get(first).fileId;
                window.anchorOffset = results.contentY - item.y;
            }
        }
        function onRowsChanged() {
            var revision = window.restoreRevision;
            Qt.callLater(function() {
                if(revision !== window.restoreRevision) return;
                if(!client.manualSelection) { results.positionViewAtBeginning(); return; }
                var index = client.results.indexOf(window.anchorFileId);
                if(index < 0) return;
                results.positionViewAtIndex(index, ListView.Beginning);
                var item = results.itemAtIndex(index);
                if(item) results.contentY = Math.max(results.originY, Math.min(results.originY + Math.max(0, results.contentHeight - results.height), item.y + window.anchorOffset));
            });
        }
    }
    Shortcut { sequence: "Ctrl+K"; enabled: !settingsDialog.opened; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+L"; enabled: !settingsDialog.opened; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+F"; enabled: !settingsDialog.opened; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+,"; onActivated: settingsDialog.open() }
    Shortcut { sequence: "Escape"; enabled: !settingsDialog.opened; onActivated: { if(client.query.length) client.setQuery(""); else client.setKind(""); search.forceActiveFocus(); } }
    onClosing: client.close()

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 94
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 26; anchors.rightMargin: 26
                spacing: 24
                AbstractButton {
                    id: homeButton
                    objectName: "homeButton"
                    Layout.preferredWidth: window.width < 1040 ? 44 : 170
                    Layout.preferredHeight: 48
                    hoverEnabled: true
                    Accessible.name: "EveryTensor 首页"
                    onClicked: window.goHome()
                    background: Rectangle { color: homeButton.hovered ? Theme.hover : "transparent"; radius: 8; border.color: homeButton.visualFocus ? Theme.accent : "transparent" }
                    contentItem: RowLayout {
                        spacing: 10
                        Brand { Layout.preferredWidth: 32; Layout.preferredHeight: 32 }
                        Text { visible: window.width >= 1040; text: "EveryTensor"; color: Theme.ink; font.family: "Segoe UI"; font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    }
                }
                Rectangle {
                    id: searchSurface
                    Layout.fillWidth: true
                    Layout.preferredHeight: 54
                    radius: 11
                    color: Theme.paper
                    border.color: search.activeFocus ? "#be887b" : Theme.line
                    border.width: 1
                    Behavior on border.color { ColorAnimation { duration: Theme.fast } }
                    Rectangle { anchors.fill: parent; anchors.margins: -3; radius: 14; color: "transparent"; border.color: "#21a53f36"; border.width: search.activeFocus ? 3 : 0 }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 17; anchors.rightMargin: 9
                        spacing: 12
                        Icon { name: "search"; color: search.activeFocus ? Theme.accent : Theme.secondary; Layout.preferredWidth: 21; Layout.preferredHeight: 21 }
                        TextField {
                            id: search
                            objectName: "searchField"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            padding: 0
                            background: null
                            color: Theme.ink
                            font.family: Theme.family
                            font.pixelSize: 16
                            selectByMouse: true
                            selectionColor: Theme.selection
                            selectedTextColor: Theme.ink
                            placeholderText: "搜索文件名，或描述你记得的内容"
                            placeholderTextColor: Theme.quiet
                            text: client.query
                            Accessible.name: "搜索文件"
                            onTextEdited: if(!inputMethodComposing) client.setQuery(text)
                            onInputMethodComposingChanged: if(!inputMethodComposing) client.setQuery(text)
                            Keys.onDownPressed: window.moveSelection(results.currentIndex < 0 ? 1 : 0)
                            Keys.onReturnPressed: window.openCurrent()
                            Component.onCompleted: forceActiveFocus()
                        }
                        Text { visible: !search.text.length; text: "Ctrl K"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; Layout.rightMargin: 8 }
                        DeskButton { objectName: "clearSearchButton"; visible: search.text.length > 0; iconName: "close"; compact: true; subtle: true; tooltip: "清空搜索"; onClicked: { client.setQuery(""); search.forceActiveFocus(); } }
                    }
                }
                DeskButton { id: settingsButton; objectName: "settingsButton"; iconName: "settings"; subtle: true; tooltip: "设置  Ctrl ,"; onClicked: settingsDialog.open() }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            visible: window.browsing
            RowLayout {
                anchors.left: parent.left; anchors.leftMargin: 26
                anchors.top: parent.top; spacing: 5
                Repeater {
                    id: tabsRepeater
                    model: [{label:"全部", key:""}, {label:"文档", key:"document"}, {label:"图片", key:"image"}, {label:"视频", key:"video"}, {label:"音频", key:"audio"}, {label:"代码", key:"code"}]
                    delegate: AbstractButton {
                        id: tab
                        required property var modelData
                        required property int index
                        objectName: "tab_" + (modelData.key || "all")
                        implicitWidth: 64; implicitHeight: 34
                        property bool selected: client.kind === modelData.key
                        hoverEnabled: true
                        Accessible.name: modelData.label
                        Accessible.role: Accessible.PageTab
                        Accessible.selected: selected
                        onClicked: client.setKind(modelData.key)
                        Keys.onRightPressed: { var next = tabsRepeater.itemAt((index+1)%5); client.setKind(next.modelData.key); next.forceActiveFocus(); }
                        Keys.onLeftPressed: { var next = tabsRepeater.itemAt((index+4)%5); client.setKind(next.modelData.key); next.forceActiveFocus(); }
                        background: Rectangle { radius: 7; color: tab.selected ? Theme.paper : tab.hovered ? Theme.hover : "transparent"; border.color: tab.visualFocus ? Theme.accent : tab.selected ? Theme.line : "transparent"; Behavior on color { ColorAnimation { duration: Theme.fast } } }
                        contentItem: Text { text: tab.modelData.label; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: Theme.family; font.pixelSize: 13; font.weight: tab.selected ? Font.DemiBold : Font.Normal; color: tab.selected ? Theme.accent : Theme.secondary }
                    }
                }
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 30
                anchors.top: parent.top; anchors.topMargin: 9
                spacing: 8
                Rectangle { visible: client.pending; width: 5; height: 5; radius: 2.5; y: 5; color: Theme.accent }
                Text { text: client.resultLabel; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12 }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 26; Layout.rightMargin: 26
            Layout.bottomMargin: 12
            Layout.preferredHeight: noticeText.implicitHeight + 20
            visible: window.currentNotice.length > 0
            radius: 8; color: Theme.selection
            RowLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 10
                Text { id: noticeText; text: window.currentNotice; color: Theme.accent; font.family: Theme.family; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                DeskButton { text: "查看设置"; compact: true; subtle: true; onClicked: settingsDialog.open() }
                DeskButton { iconName: "close"; compact: true; subtle: true; tooltip: "关闭提示"; onClicked: { window.toastMessage = ""; window.dismissedWarning = client.warning; } }
            }
        }
        Item {
            id: home
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !window.browsing
            Column {
                width: Math.min(640, parent.width - 100)
                anchors.horizontalCenter: parent.horizontalCenter
                y: Math.max(50, (parent.height - height) * .36)
                spacing: 0
                Text { anchors.horizontalCenter: parent.horizontalCenter; bottomPadding: 12; text: "从记得的一点开始"; color: Theme.ink; font.family: Theme.family; font.pixelSize: 24; font.weight: Font.Medium }
                Item { width: 1; height: 20 }
                Rectangle {
                    width: parent.width; height: examples.implicitHeight + 8; radius: 12
                    color: "#80ffffff"; border.color: Theme.line
                    Column {
                        id: examples
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 4
                        Repeater {
                            model: [{icon:"file",label:"关于世界模型的论文"}, {icon:"video",label:"机器人把杯子放到桌上"}, {icon:"image",label:"那张正交投影示意图"}]
                            delegate: AbstractButton {
                                id: example
                                required property var modelData
                                objectName: "homeExample_" + modelData.icon
                                width: examples.width; height: 54
                                hoverEnabled: true
                                Accessible.name: modelData.label
                                onClicked: { client.setQuery(modelData.label); search.forceActiveFocus(); }
                                background: Rectangle {
                                    radius: 8
                                    color: example.hovered ? Theme.paper : "transparent"
                                    border.color: example.visualFocus ? Theme.accent : "transparent"
                                    // Switch immediately so adjacent rows never cross-fade.
                                }
                                contentItem: RowLayout {
                                    spacing: 13
                                    Item { Layout.preferredWidth: 12 }
                                    Icon { name: example.modelData.icon; color: Theme.secondary; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
                                    Text { text: example.modelData.label; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; Layout.fillWidth: true }
                                    Icon { name: "arrow"; color: example.hovered ? Theme.accent : Theme.quiet; Layout.preferredWidth: 17; Layout.preferredHeight: 17 }
                                    Item { Layout.preferredWidth: 12 }
                                }
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            id: workspace
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: 26; Layout.rightMargin: 26
            Layout.bottomMargin: 14
            visible: window.browsing
            spacing: 0
            Rectangle {
                id: resultsPanel
                visible: !window.singleVideo
                Layout.preferredWidth: Math.min(window.preferredListWidth, (window.width - 52) * (window.width < 1040 ? .35 : .44))
                Layout.minimumWidth: 270
                Layout.fillHeight: true
                color: "transparent"
                ListView {
                    id: results
                    objectName: "resultsList"
                    anchors.fill: parent
                    model: client.results
                    clip: true
                    spacing: 4
                    currentIndex: -1
                    reuseItems: true
                    cacheBuffer: 200
                    boundsBehavior: Flickable.StopAtBounds
                    onMovementStarted: client.markInteraction()
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    Keys.onDownPressed: window.moveSelection(1)
                    Keys.onUpPressed: window.moveSelection(-1)
                    Keys.onReturnPressed: window.openCurrent()
                    Keys.onPressed: function(event) {
                        if(event.key === Qt.Key_Home) { window.moveSelection(-results.count); event.accepted = true; }
                        else if(event.key === Qt.Key_End) { window.moveSelection(results.count); event.accepted = true; }
                        else if(event.key === Qt.Key_PageDown) { window.moveSelection(7); event.accepted = true; }
                        else if(event.key === Qt.Key_PageUp) { window.moveSelection(-7); event.accepted = true; }
                    }
                    delegate: Rectangle {
                        id: resultRow
                        required property int index
                        required property int fileId
                        required property string name
                        required property string parentPath
                        required property string fullPath
                        required property string kind
                        required property string suffix
                        required property string locationLabel
                        required property string thumbnailSource
                        readonly property bool selected: client.selectedId === fileId
                        property bool tooltipSuppressed: false
                        Accessible.role: Accessible.ListItem
                        Accessible.name: name
                        Accessible.description: fullPath + (locationLabel ? " · " + locationLabel : "")
                        Accessible.selected: selected
                        Accessible.focusable: true
                        Accessible.focused: results.activeFocus && results.currentIndex === index
                        Accessible.onPressAction: { results.currentIndex = index; results.forceActiveFocus(); client.selectFile(fileId, true); }
                        width: results.width - 8
                        height: 76
                        radius: 9
                        color: selected ? Theme.selection : pointer.containsMouse ? "#b0ffffff" : "transparent"
                        border.color: selected && results.activeFocus ? Theme.selectionLine : "transparent"
                        onSelectedChanged: if(selected) results.currentIndex = index
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        Rectangle { anchors.left: parent.left; anchors.leftMargin: 1; anchors.verticalCenter: parent.verticalCenter; width: 3; height: 29; radius: 1.5; color: Theme.accent; visible: resultRow.selected }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 15; anchors.rightMargin: 14
                            spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 38; Layout.preferredHeight: 42
                                radius: 7
                                color: resultRow.selected ? "#caffffff" : "#a0ffffff"
                                Icon { anchors.centerIn: parent; width: 22; height: 22; visible: !thumbnail.visible; name: resultRow.kind === "image" ? "image" : resultRow.kind === "video" ? "video" : resultRow.kind === "code" ? "code" : "file"; color: resultRow.selected ? Theme.accent : Theme.secondary }
                                Image { id: thumbnail; anchors.fill: parent; anchors.margins: 2; source: resultRow.thumbnailSource; visible: source.toString().length > 0 && status === Image.Ready; fillMode: Image.PreserveAspectCrop; asynchronous: true; smooth: true; clip: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                Text { text: resultRow.name; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; font.weight: resultRow.selected ? Font.DemiBold : Font.Normal; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                Text { text: resultRow.parentPath; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                            }
                            Text { visible: resultRow.locationLabel.length > 0; text: resultRow.locationLabel; color: resultRow.selected ? Theme.accent : Theme.quiet; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 65 }
                        }
                        MouseArea {
                            id: pointer
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onPressed: resultRow.tooltipSuppressed = true
                            onExited: resultRow.tooltipSuppressed = false
                            onClicked: { results.currentIndex = resultRow.index; results.forceActiveFocus(); client.selectFile(resultRow.fileId, true); }
                            onDoubleClicked: { client.selectFile(resultRow.fileId, true); window.openCurrent(); }
                        }
                        Hint {
                            parent: resultRow
                            visible: pointer.containsMouse && !resultRow.tooltipSuppressed && !window.tooltipsBlocked
                            delay: 900
                            text: resultRow.fullPath
                        }
                    }
                    footer: Item {
                        width: results.width; height: client.canLoadMore ? 56 : 8
                        DeskButton { anchors.centerIn: parent; visible: client.canLoadMore; text: "显示更多结果"; onClicked: client.loadMore() }
                    }
                }
                Column {
                    anchors.centerIn: parent; width: parent.width - 36; spacing: 13
                    visible: results.count === 0
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "search"; width: 30; height: 30; color: Theme.quiet }
                    Text { width: parent.width; text: client.emptyMessage || (client.pending ? "正在查找…" : "没有找到相关文件"); color: Theme.secondary; font.family: Theme.family; font.pixelSize: 14; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                    Text { width: parent.width; visible: !client.pending; text: "试试其他描述，或检查搜索范围。"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                }
            }
            Item {
                visible: !window.singleVideo
                Layout.preferredWidth: 20; Layout.fillHeight: true
                Rectangle { anchors.centerIn: parent; width: 1; height: 30; color: splitter.containsMouse || splitter.pressed ? Theme.selectionLine : "transparent" }
                MouseArea {
                    id: splitter
                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SplitHCursor
                    property real start: 0
                    property real startWidth: 0
                    onPressed: function(mouse) { start = mapToItem(workspace, mouse.x, mouse.y).x; startWidth = resultsPanel.width; }
                    onPositionChanged: function(mouse) { if(pressed) window.preferredListWidth = Math.max(270, Math.min(workspace.width * .44, startWidth + mapToItem(workspace, mouse.x, mouse.y).x - start)); }
                }
            }
            PreviewPane { Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 350 }
        }

        Item {
            Layout.fillWidth: true; Layout.preferredHeight: 34
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 30; anchors.rightMargin: 30
                spacing: 7
                Rectangle { width: 5; height: 5; radius: 2.5; color: client.modelState === "error" ? Theme.accent : Theme.success }
                Text { text: "本地搜索"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 11 }
                Text { visible: client.semanticStage.length > 0 && window.browsing; text: "·  " + (client.semanticStage === "quick" ? "快速语义（文字）" : "内容语义"); color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Text { text: window.browsing ? "↑ ↓  选择     Enter  打开     Esc  清空" : "Ctrl K  搜索     ·     文件始终保留在本机"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
            }
        }
    }
    SettingsDialog {
        id: settingsDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(690, window.width - 72)
        height: Math.min(720, window.height - 64)
        onClosed: settingsButton.forceActiveFocus()
    }
}
