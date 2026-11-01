import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Rectangle {
    id: panel
    objectName: "previewPane"
    color: Theme.paper
    radius: 12
    border.color: Theme.line
    property var row: client.selected
    readonly property bool hasRow: row && !!row.name
    readonly property bool isVideo: hasRow && row.kind === "video"
    readonly property bool isMedia: hasRow && (row.kind === "video" || row.kind === "audio")
    readonly property bool isDocument: hasRow && row.kind === "document"
    readonly property bool isPdf: hasRow && String(row.name).toLowerCase().endsWith(".pdf")
    readonly property string path: hasRow ? String(row.path || row.fullPath || "") : ""
    readonly property bool compactHeader: width < 700
    onPathChanged: {
        imageScroll.contentY = 0;
        if(textScroll.contentItem) textScroll.contentItem.contentY = 0;
    }
    readonly property bool requestLoading: preview.loading
    property bool showLoading: false
    onRequestLoadingChanged: {
        if (requestLoading) loadingDelay.restart();
        else { loadingDelay.stop(); showLoading = false; }
    }
    Timer { id: loadingDelay; interval: 150; onTriggered: panel.showLoading = panel.requestLoading }
    function timeLabel(seconds) {
        var t = Math.max(0, Math.floor(seconds || 0));
        return Math.floor(t / 60) + ":" + (t % 60 < 10 ? "0" : "") + t % 60;
    }
    function resizePreview() { preview.setBounds(Math.max(100, Math.floor(viewport.width - 40)), Math.max(100, Math.floor(viewport.height - 40))); }
    onWidthChanged: boundsTimer.restart()
    onHeightChanged: boundsTimer.restart()
    Timer { id: boundsTimer; interval: 100; onTriggered: panel.resizePreview() }
    Component.onCompleted: boundsTimer.start()

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Item {
            objectName: "previewHeader"
            Layout.fillWidth: true
            Layout.preferredHeight: panel.compactHeader ? 124 : 88
            visible: panel.hasRow
            Column {
                id: fileHeading
                anchors.left: parent.left; anchors.leftMargin: 22
                anchors.right: panel.compactHeader ? parent.right : actions.left; anchors.rightMargin: 22
                y: panel.compactHeader ? 17 : (parent.height - height) / 2
                spacing: 6
                Text { objectName: "previewTitle"; width: parent.width; text: panel.hasRow ? panel.row.name : ""; color: Theme.ink; font.family: Theme.family; font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideMiddle }
                Text { objectName: "previewPath"; width: parent.width; text: panel.hasRow && panel.row.parentPath ? panel.row.parentPath : panel.path.replace(/[^\\/]+$/, "").replace(/[\\/]$/, ""); color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; elide: Text.ElideMiddle }
                HoverHandler { id: headingHover }
                Hint { visible: headingHover.hovered && !panel.Window.window.tooltipsBlocked; text: panel.path; delay: 900 }
            }
            Row {
                id: actions
                objectName: "previewActions"
                anchors.right: parent.right; anchors.rightMargin: 18
                y: panel.compactHeader ? parent.height - height - 12 : (parent.height - height) / 2
                spacing: 5
                DeskButton { objectName: "copyLocationButton"; text: "复制位置"; iconName: "copy"; subtle: true; tooltip: "复制文件路径及匹配位置"; onClicked: client.copyLocation() }
                DeskButton { objectName: "revealButton"; iconName: "folder"; subtle: true; tooltip: "在文件夹中显示"; onClicked: client.revealSelected() }
                DeskButton { objectName: "fitButton"; visible: panel.isPdf; iconName: "fit"; subtle: true; checked: preview.fitWidth; Accessible.checkable: true; Accessible.checked: checked; tooltip: preview.fitWidth ? "适合整页" : "适合宽度"; onClicked: preview.setFitWidth(!preview.fitWidth) }
                DeskButton { objectName: "openButton"; text: panel.isMedia ? "播放" : "打开"; iconName: panel.isMedia ? "play" : "arrow"; primary: true; onClicked: panel.isMedia ? preview.open() : client.openSelected() }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line; visible: panel.hasRow }
        Rectangle {
            id: viewport
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 1
            color: Theme.soft
            clip: true
            onWidthChanged: boundsTimer.restart()
            onHeightChanged: boundsTimer.restart()

            Flickable {
                id: imageScroll
                objectName: "previewImageScroll"
                anchors.fill: parent; anchors.margins: 20
                visible: panel.hasRow && preview.source.length > 0
                contentWidth: width
                contentHeight: imageItem.height
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                clip: true
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                Image {
                    id: imageItem
                    objectName: "previewImage"
                    readonly property int loadStatus: status
                    source: preview.source
                    asynchronous: true
                    cache: true
                    width: imageScroll.width
                    height: panel.isPdf && preview.fitWidth && preview.width > 0
                            ? Math.max(imageScroll.height, width * preview.height / preview.width)
                            : imageScroll.height
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                }
            }
            ScrollView {
                id: textScroll
                objectName: "previewTextScroll"
                anchors.fill: parent; anchors.margins: 24
                visible: panel.hasRow && !preview.source && preview.text.length > 0
                clip: true
                TextArea {
                    objectName: "previewText"
                    text: preview.text
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: Theme.ink
                    font.family: panel.row && panel.row.kind === "code" ? "Cascadia Code" : Theme.family
                    font.pixelSize: 14
                    background: null
                    padding: 0
                }
            }
            Column {
                anchors.centerIn: parent
                width: Math.min(300, parent.width - 64)
                spacing: 14
                visible: !panel.hasRow || (!preview.loading && !preview.source && !preview.text)
                Icon { anchors.horizontalCenter: parent.horizontalCenter; width: 34; height: 34; name: panel.hasRow ? "file" : "search"; color: Theme.quiet }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 14; wrapMode: Text.Wrap; text: !panel.hasRow ? "选择一个文件，在这里预览" : preview.error || "这个文件暂不支持预览" }
                DeskButton { anchors.horizontalCenter: parent.horizontalCenter; visible: panel.hasRow; text: "打开原文件"; onClicked: client.openSelected() }
            }
            Rectangle {
                anchors.top: parent.top; anchors.topMargin: 14
                anchors.horizontalCenter: parent.horizontalCenter
                width: loadingText.implicitWidth + 28; height: 30; radius: 15
                color: Theme.paper; border.color: Theme.line
                visible: panel.hasRow && panel.showLoading
                Text { id: loadingText; anchors.centerIn: parent; text: "正在准备预览…"; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12 }
            }
            DeskButton {
                anchors.centerIn: parent
                visible: panel.isMedia && (preview.source.length > 0 || row.kind === "audio") && !preview.loading
                iconName: "play"
                primary: true
                width: 56; height: 56
                tooltip: "从这里开始播放"
                onClicked: preview.open()
            }
        }
        Item {
            visible: panel.isMedia && preview.duration > 0
            Layout.fillWidth: true
            Layout.preferredHeight: panel.height < 480 ? 118 : 130
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16
                spacing: 8
                ListView {
                    id: frameList
                    objectName: "videoFrames"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    visible: count > 0
                    orientation: ListView.Horizontal
                    spacing: 5; clip: true
                    model: preview.frames
                    ScrollBar.horizontal: ScrollBar {
                        policy: frameList.contentWidth > frameList.width + 1 ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                        height: 5
                    }
                    delegate: Rectangle {
                        required property var modelData
                        width: Math.max(78, (frameList.width - 35) / 8)
                        height: frameList.height - (frameList.contentWidth > frameList.width + 1 ? 8 : 0)
                        radius: 5
                        color: Math.abs(modelData.second - preview.position) < Math.max(1, preview.duration / 16) ? Theme.accent : Theme.line
                        Image { anchors.fill: parent; anchors.margins: 2; source: modelData.source; fillMode: Image.PreserveAspectCrop; asynchronous: true; smooth: true; clip: true }
                        Rectangle { anchors.bottom: parent.bottom; anchors.right: parent.right; anchors.margins: 4; width: frameStamp.implicitWidth + 8; height: 18; radius: 3; color: "#b0252722"; Text { id: frameStamp; anchors.centerIn: parent; text: modelData.label || panel.timeLabel(modelData.second); color: "white"; font.pixelSize: 10; font.family: Theme.family } }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { client.markInteraction(); preview.seek(modelData.second); } }
                    }
                }
                ListView {
                    Layout.fillWidth: true; Layout.preferredHeight: 24
                    visible: count > 0
                    orientation: ListView.Horizontal; spacing: 6; clip: true
                    model: panel.row && panel.row.hits ? panel.row.hits : []
                    delegate: DeskButton {
                        required property var modelData
                        text: panel.timeLabel(modelData.start) + "–" + panel.timeLabel(modelData.end)
                        compact: true; height: 24
                        primary: preview.position >= modelData.start && preview.position <= modelData.end
                        tooltip: "查看匹配片段"
                        onClicked: { client.markInteraction(); preview.seek(modelData.start); }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    Text { text: panel.timeLabel(preview.position); color: Theme.ink; font.pixelSize: 12; font.family: Theme.family; Layout.preferredWidth: 38 }
                    Slider {
                        id: seekSlider
                        objectName: "videoTimeline"
                        Layout.fillWidth: true; implicitHeight: 24
                        from: 0; to: Math.max(1, preview.duration); value: preview.position
                        Accessible.name: "播放位置"
                        onMoved: { client.markInteraction(); seekTimer.seekTo = value; seekTimer.restart(); }
                        background: Rectangle {
                            x: seekSlider.leftPadding; y: seekSlider.topPadding + seekSlider.availableHeight / 2 - 2
                            width: seekSlider.availableWidth; height: 4; radius: 2; color: Theme.line
                            Repeater {
                                model: panel.row && panel.row.hits ? panel.row.hits : []
                                delegate: Rectangle {
                                    required property var modelData
                                    x: modelData.start / Math.max(1, preview.duration) * parent.width
                                    width: Math.max(3, (modelData.end - modelData.start) / Math.max(1, preview.duration) * parent.width)
                                    y: -2; height: 8; radius: 2; color: Theme.selectionLine
                                }
                            }
                            Rectangle { width: seekSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: Theme.accent }
                        }
                        handle: Rectangle { x: seekSlider.leftPadding + seekSlider.visualPosition * (seekSlider.availableWidth - width); y: seekSlider.topPadding + seekSlider.availableHeight / 2 - height / 2; width: 12; height: 12; radius: 6; color: Theme.accent; border.width: 2; border.color: "white" }
                    }
                    Text { text: panel.timeLabel(preview.duration); color: Theme.secondary; font.pixelSize: 12; font.family: Theme.family }
                }
            }
            Timer { id: seekTimer; property real seekTo: 0; interval: 90; onTriggered: preview.seek(seekTo) }
        }
        Item {
            visible: panel.hasRow && !panel.isMedia
            Layout.fillWidth: true; Layout.preferredHeight: 40
            Text { anchors.left: parent.left; anchors.leftMargin: 22; anchors.verticalCenter: parent.verticalCenter; text: preview.caption; color: Theme.secondary; font.family: Theme.family; font.pixelSize: 12; width: parent.width - 130; elide: Text.ElideRight }
            Text { anchors.right: parent.right; anchors.rightMargin: 22; anchors.verticalCenter: parent.verticalCenter; text: "Enter  打开"; color: Theme.quiet; font.family: Theme.family; font.pixelSize: 11 }
        }
    }
}
