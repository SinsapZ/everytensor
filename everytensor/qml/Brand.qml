import QtQuick

Item {
    implicitWidth: 34
    implicitHeight: 34
    Rectangle {
        width: parent.width * .70; height: width; radius: width / 2
        x: 0; y: parent.height * .03
        gradient: Gradient { GradientStop { position: 0; color: "#d18c79" } GradientStop { position: 1; color: "#a74435" } }
    }
    Rectangle {
        width: parent.width * .70; height: width; radius: width / 2
        x: parent.width * .30; y: parent.height * .27
        opacity: .90
        gradient: Gradient { GradientStop { position: 0; color: "#373d37" } GradientStop { position: 1; color: "#858a80" } }
    }
}
