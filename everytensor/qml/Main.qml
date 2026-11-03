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
    property Item settingsReturnFocus: null
    property string feedbackMessage: ""
    readonly property bool tooltipsBlocked: settingsDialog.visible || modelSetupDialog.visible || resultMenu.visible
    readonly property string currentNotice: toastMessage || (client.warning !== dismissedWarning ? client.warning : "")
    property int anchorFileId: -1
    property real anchorOffset: 0
    property int restoreRevision: 0
    readonly property bool singleVideo: client.kind === "video" && results.count === 1 && (!client.pending || client.refreshing) && !client.searchError
    readonly property bool hasSelected: client.selected && !!client.selected.name
    function focusSearch() { search.forceActiveFocus(); search.selectAll(); }
    function openModelSetup() { modelSetupDialog.open(); }
    Component.onCompleted: { if(modelSetup.shouldOffer) Qt.callLater(window.openModelSetup); }
    function goHome() { client.setQuery(""); client.setKind(""); search.forceActiveFocus(); }
    function openSettings() {
        if(settingsDialog.visible) return;
        settingsReturnFocus = window.activeFocusItem;
        settingsDialog.open();
    }
    function openCurrent() { if(hasSelected && (client.selected.kind === "video" || client.selected.kind === "audio")) preview.open(); else client.openSelected(); }
    function showResultMenu() {
        if(!hasSelected) return;
        var item = results.currentItem;
        resultMenu.popup(item || results, item ? Math.min(item.width - 10, 160) : 20, item ? item.height / 2 : 20);
    }
    function moveSelection(delta) {
        if(!results.count) return;
        client.markInteraction();
        results.currentIndex = Math.max(0, Math.min(results.count - 1, results.currentIndex + delta));
        // Read the model directly: a virtualized delegate may not exist yet.
        client.selectFile(client.results.get(results.currentIndex).fileId, true);
        results.positionViewAtIndex(results.currentIndex, ListView.Contain);
        results.forceActiveFocus();
    }
    Connections {
        target: client
        function onErrorOccurred(message) { window.toastMessage = message; window.dismissedWarning = ""; }
        function onNotification(message) { window.feedbackMessage = message; feedbackTimer.restart(); }
        function onSelectedChanged() { results.currentIndex = client.results.indexOf(client.selectedId); }
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
    Shortcut { sequence: "Ctrl+K"; enabled: !window.tooltipsBlocked; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+L"; enabled: !window.tooltipsBlocked; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+F"; enabled: !window.tooltipsBlocked; onActivated: window.focusSearch() }
    Shortcut { sequence: "Ctrl+,"; enabled: !resultMenu.visible && !modelSetupDialog.visible; onActivated: window.openSettings() }
    Shortcut { sequence: "Escape"; enabled: !window.tooltipsBlocked && !search.inputMethodComposing; onActivated: { if(client.query.length) client.setQuery(""); else client.setKind(""); search.forceActiveFocus(); } }
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
                            Keys.onDownPressed: function(event) { if(!inputMethodComposing) window.moveSelection(results.currentIndex < 0 ? 1 : 0); else event.accepted = false; }
                            Keys.onReturnPressed: function(event) { if(!inputMethodComposing) window.openCurrent(); else event.accepted = false; }
                            Keys.onEnterPressed: function(event) { if(!inputMethodComposing) window.openCurrent(); else event.accepted = false; }
                            Component.onCompleted: forceActiveFocus()
                        }
                        Text { visible: !search.text.length; text: "Ctrl K"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11; Layout.rightMargin: 8 }
                        DeskButton { objectName: "clearSearchButton"; visible: search.text.length > 0; iconName: "close"; compact: true; subtle: true; tooltip: "清空搜索"; onClicked: { client.setQuery(""); search.forceActiveFocus(); } }
                    }
                }
                DeskButton { id: settingsButton; objectName: "settingsButton"; iconName: "settings"; subtle: true; tooltip: "设置  Ctrl ,"; onClicked: window.openSettings() }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            visible: window.browsing
            RowLayout {
                id: categoryTabs
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
                        Keys.onRightPressed: { var next = tabsRepeater.itemAt((index+1)%tabsRepeater.count); client.setKind(next.modelData.key); next.forceActiveFocus(); }
                        Keys.onLeftPressed: { var next = tabsRepeater.itemAt((index+tabsRepeater.count-1)%tabsRepeater.count); client.setKind(next.modelData.key); next.forceActiveFocus(); }
                        Keys.onDownPressed: window.moveSelection(0)
                        background: Rectangle { radius: 7; color: tab.selected ? Theme.paper : tab.hovered ? Theme.hover : "transparent"; border.color: tab.visualFocus ? Theme.accent : tab.selected ? Theme.line : "transparent"; Behavior on color { ColorAnimation { duration: Theme.fast } } }
                        contentItem: Text { text: tab.modelData.label; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: Theme.family; font.pixelSize: 13; font.weight: tab.selected ? Font.DemiBold : Font.Normal; color: tab.selected ? Theme.accent : Theme.secondary }
                    }
                }
            }
            RowLayout {
                objectName: "resultStatus"
                anchors.left: categoryTabs.right; anchors.leftMargin: 20
                anchors.right: parent.right; anchors.rightMargin: 30
                anchors.top: parent.top; anchors.topMargin: 9
                spacing: 8
                Rectangle { visible: client.pending; Layout.preferredWidth: 5; Layout.preferredHeight: 5; radius: 2.5; color: Theme.accent }
                Text {
                    objectName: "resultStatusText"
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignRight
                    text: client.resultLabel; textFormat: Text.PlainText; elide: Text.ElideRight
                    color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12
                    HoverHandler { id: statusHover }
                    Hint { visible: statusHover.hovered && !window.tooltipsBlocked; text: client.resultLabel }
                }
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
                DeskButton { text: "查看设置"; compact: true; subtle: true; onClicked: window.openSettings() }
                DeskButton { iconName: "close"; compact: true; subtle: true; tooltip: "关闭提示"; onClicked: { window.toastMessage = ""; window.dismissedWarning = client.warning; } }
            }
        }
        Rectangle {
            objectName: "searchErrorNotice"
            Layout.fillWidth: true; Layout.leftMargin: 26; Layout.rightMargin: 26
            Layout.bottomMargin: 12
            Layout.preferredHeight: Math.max(34, searchErrorText.implicitHeight) + 20
            visible: window.browsing && client.searchError.length > 0
            radius: 8; color: Theme.selection
            RowLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 12
                Text {
                    id: searchErrorText
                    Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: client.searchError + (results.count ? "。已找到的结果仍可查看。" : "")
                    color: Theme.accent; font.family: Theme.family; font.pixelSize: 12
                    Accessible.role: Accessible.AlertMessage
                }
                DeskButton { objectName: "retrySearchButton"; text: "重试搜索"; compact: true; enabled: !client.pending; onClicked: client.retrySearch() }
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
                    Keys.onUpPressed: { if(currentIndex <= 0) search.forceActiveFocus(); else window.moveSelection(-1); }
                    Keys.onReturnPressed: function(event) { if(event.modifiers & Qt.ShiftModifier) client.revealSelected(); else window.openCurrent(); }
                    Keys.onEnterPressed: function(event) { if(event.modifiers & Qt.ShiftModifier) client.revealSelected(); else window.openCurrent(); }
                    Keys.onPressed: function(event) {
                        if(event.key === Qt.Key_Home) { window.moveSelection(-results.count); event.accepted = true; }
                        else if(event.key === Qt.Key_End) { window.moveSelection(results.count); event.accepted = true; }
                        else if(event.key === Qt.Key_PageDown) { window.moveSelection(Math.max(1, Math.floor(results.height / 96))); event.accepted = true; }
                        else if(event.key === Qt.Key_PageUp) { window.moveSelection(-Math.max(1, Math.floor(results.height / 96))); event.accepted = true; }
                        else if(event.key === Qt.Key_C && (event.modifiers & Qt.ControlModifier)) { client.copyLocation(); event.accepted = true; }
                        else if(event.key === Qt.Key_Menu || (event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier))) { window.showResultMenu(); event.accepted = true; }
                    }
                    delegate: Rectangle {
                        id: resultRow
                        objectName: "resultRow_" + fileId
                        required property int index
                        required property int fileId
                        required property string name
                        required property string parentPath
                        required property string fullPath
                        required property string kind
                        required property string suffix
                        required property string locationLabel
                        required property string thumbnailSource
                        required property string matchLabel
                        required property string excerpt
                        readonly property bool selected: client.selectedId === fileId
                        property bool tooltipSuppressed: false
                        Accessible.role: Accessible.ListItem
                        Accessible.name: name
                        Accessible.description: fullPath + (locationLabel ? " · " + locationLabel : "") + (matchLabel ? " · " + matchLabel : "") + (excerpt ? " · " + excerpt : "")
                        Accessible.selected: selected
                        Accessible.focusable: true
                        Accessible.focused: results.activeFocus && results.currentIndex === index
                        Accessible.onPressAction: { results.currentIndex = index; results.forceActiveFocus(); client.selectFile(fileId, true); }
                        width: results.width - 8
                        height: 92
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
                                Icon { anchors.centerIn: parent; width: 22; height: 22; visible: !thumbnail.visible; name: resultRow.kind === "image" ? "image" : resultRow.kind === "video" ? "video" : resultRow.kind === "audio" ? "audio" : resultRow.kind === "code" ? "code" : "file"; color: resultRow.selected ? Theme.accent : Theme.secondary }
                                Image { id: thumbnail; anchors.fill: parent; anchors.margins: 2; source: resultRow.thumbnailSource; visible: source.toString().length > 0 && status === Image.Ready; fillMode: Image.PreserveAspectCrop; asynchronous: true; smooth: true; clip: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 5
                                Text { text: resultRow.name; textFormat: Text.PlainText; color: Theme.ink; font.family: Theme.family; font.pixelSize: 14; font.weight: resultRow.selected ? Font.DemiBold : Font.Normal; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    Text { text: resultRow.parentPath; textFormat: Text.PlainText; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                    Text { visible: resultRow.locationLabel.length > 0; text: resultRow.locationLabel; textFormat: Text.PlainText; color: resultRow.selected ? Theme.accent : Theme.quiet; font.family: Theme.family; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 100 }
                                }
                                Text {
                                    objectName: "resultExcerpt_" + resultRow.fileId
                                    Layout.fillWidth: true; textFormat: Text.PlainText; elide: Text.ElideRight
                                    text: resultRow.excerpt || resultRow.matchLabel || resultRow.suffix
                                    color: Theme.secondary; font.family: Theme.family; font.pixelSize: 11
                                }
                            }
                        }
                        MouseArea {
                            id: pointer
                            anchors.fill: parent; hoverEnabled: true
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            cursorShape: Qt.PointingHandCursor
                            onPressed: resultRow.tooltipSuppressed = true
                            onExited: resultRow.tooltipSuppressed = false
                            onClicked: function(mouse) {
                                results.currentIndex = resultRow.index; results.forceActiveFocus(); client.selectFile(resultRow.fileId, true);
                                if(mouse.button === Qt.RightButton) resultMenu.popup(resultRow, mouse.x, mouse.y);
                            }
                            onDoubleClicked: function(mouse) { if(mouse.button === Qt.LeftButton) { client.selectFile(resultRow.fileId, true); window.openCurrent(); } }
                        }
                        Hint {
                            id: resultHint
                            parent: resultRow
                            visible: pointer.containsMouse && !resultRow.tooltipSuppressed && !window.tooltipsBlocked
                            delay: 900
                            text: resultRow.fullPath + (resultRow.matchLabel ? "\n" + resultRow.matchLabel : "") + (resultRow.excerpt ? "\n" + resultRow.excerpt : "")
                            contentItem: Text { text: resultHint.text; textFormat: Text.PlainText; font: resultHint.font; color: "white"; wrapMode: Text.Wrap }
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
                    Column {
                        anchors.horizontalCenter: parent.horizontalCenter; spacing: 4; visible: !client.pending
                        DeskButton { objectName: "clearFilterButton"; visible: client.kind.length > 0; text: "搜索全部类型"; compact: true; onClicked: { client.setKind(""); search.forceActiveFocus(); } }
                        DeskButton { objectName: "searchScopeButton"; text: "检查搜索范围"; compact: true; subtle: true; onClicked: window.openSettings() }
                    }
                }
            }
            Item {
                visible: !window.singleVideo
                Layout.preferredWidth: 20; Layout.fillHeight: true
                Rectangle { anchors.centerIn: parent; width: 1; height: 30; color: splitter.containsMouse || splitter.pressed ? Theme.selectionLine : "transparent" }
                MouseArea {
                    id: splitter
                    objectName: "resultsSplitter"
                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SplitHCursor
                    property real start: 0
                    property real startWidth: 0
                    onPressed: function(mouse) { start = mapToItem(workspace, mouse.x, mouse.y).x; startWidth = resultsPanel.width; }
                    onPositionChanged: function(mouse) { if(pressed) window.preferredListWidth = Math.max(270, Math.min(workspace.width * .44, startWidth + mapToItem(workspace, mouse.x, mouse.y).x - start)); }
                    onDoubleClicked: window.preferredListWidth = 360
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
                Text { text: window.browsing ? "↑ ↓  选择     Enter  打开     右键  更多     Esc  清空" : "Ctrl K  搜索     ·     文件始终保留在本机"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
            }
        }
    }
    SettingsDialog {
        id: settingsDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(690, window.width - 72)
        height: Math.min(720, window.height - 64)
        onClosed: {
            var target = window.settingsReturnFocus;
            if(target && target.visible && target.enabled) target.forceActiveFocus();
            else window.focusSearch();
        }
    }
    ModelSetupDialog {
        id: modelSetupDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(680, window.width - 72)
        onClosed: { if(settingsDialog.visible) settingsDialog.forceActiveFocus(); else window.focusSearch(); }
    }
    Menu {
        id: resultMenu
        objectName: "resultMenu"
        width: 244
        padding: 5
        modal: true
        dim: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.paper; radius: 10; border.color: Theme.line }
        delegate: MenuItem {
            id: menuItem
            implicitHeight: 38
            Accessible.name: text.split("\t")[0]
            contentItem: RowLayout {
                spacing: 18
                Text { text: menuItem.text.split("\t")[0]; color: Theme.ink; font.family: Theme.family; font.pixelSize: 13; Layout.fillWidth: true; Layout.leftMargin: 10 }
                Text { text: menuItem.text.split("\t")[1] || ""; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 11; Layout.rightMargin: 10 }
            }
            background: Rectangle { radius: 6; color: menuItem.highlighted ? Theme.selection : "transparent" }
        }
        Action { text: "打开原文件\tEnter"; onTriggered: window.openCurrent() }
        Action { text: "在文件夹中显示\tShift+Enter"; onTriggered: client.revealSelected() }
        Action { text: "复制文件位置\tCtrl+C"; onTriggered: client.copyLocation() }
        onClosed: if(window.browsing) results.forceActiveFocus()
    }
    Timer { id: feedbackTimer; interval: 2200; onTriggered: window.feedbackMessage = "" }
    Rectangle {
        objectName: "feedbackToast"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 48
        width: feedbackRow.implicitWidth + 32; height: 42; radius: 10
        color: Theme.ink
        visible: window.feedbackMessage.length > 0
        Accessible.role: Accessible.AlertMessage
        Accessible.name: window.feedbackMessage
        RowLayout {
            id: feedbackRow
            anchors.centerIn: parent; spacing: 9
            Icon { name: "check"; color: "#b9d5bb"; Layout.preferredWidth: 17; Layout.preferredHeight: 17 }
            Text { text: window.feedbackMessage; color: "white"; font.family: Theme.family; font.pixelSize: 13 }
        }
    }
}
