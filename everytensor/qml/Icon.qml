import QtQuick

Canvas {
    id: icon
    property string name: "search"
    property color color: Theme.secondary
    implicitWidth: 20
    implicitHeight: 20
    onNameChanged: requestPaint()
    onColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onAvailableChanged: if (available) requestPaint()
    onVisibleChanged: if (visible && available) requestPaint()
    Component.onCompleted: requestPaint()
    onPaint: {
        var c = getContext("2d"); c.reset(); c.scale(width / 24, height / 24);
        c.strokeStyle = color; c.fillStyle = color; c.lineWidth = 1.6;
        c.lineCap = "round"; c.lineJoin = "round";
        function line(p) { c.beginPath(); c.moveTo(p[0],p[1]); for(var i=2;i<p.length;i+=2)c.lineTo(p[i],p[i+1]); c.stroke(); }
        function circle(x,y,r){c.beginPath();c.arc(x,y,r,0,Math.PI*2);c.stroke();}
        if(name==="search"){circle(10.5,10.5,6.5);line([15.5,15.5,21,21]);}
        else if(name==="close"){line([6,6,18,18]);line([18,6,6,18]);}
        else if(name==="arrow"){line([5,12,19,12]);line([13,6,19,12,13,18]);}
        else if(name==="chevron"){line([9,6,15,12,9,18]);}
        else if(name==="down"){line([6,9,12,15,18,9]);}
        else if(name==="folder"){line([3,19,3,6,9,6,11,8,21,8,21,19,3,19]);}
        else if(name==="file"){line([6,3,14,3,19,8,19,21,6,21,6,3]);line([14,3,14,8,19,8]);line([9,12,16,12]);line([9,16,14,16]);}
        else if(name==="image"){line([3,4,21,4,21,20,3,20,3,4]);circle(8,9,1.5);line([4,18,10,12,14,16,17,13,21,17]);}
        else if(name==="video"){line([3,5,21,5,21,19,3,19,3,5]);c.beginPath();c.moveTo(10,9);c.lineTo(16,12);c.lineTo(10,15);c.closePath();c.fill();}
        else if(name==="code"){line([8,6,2,12,8,18]);line([16,6,22,12,16,18]);line([14,4,10,20]);}
        else if(name==="play"){c.beginPath();c.moveTo(8,5);c.lineTo(20,12);c.lineTo(8,19);c.closePath();c.fill();}
        else if(name==="pause"){line([8,5,8,19]);line([16,5,16,19]);}
        else if(name==="plus"){line([5,12,19,12]);line([12,5,12,19]);}
        else if(name==="check"){line([5,12,10,17,20,7]);}
        else if(name==="fit"){line([8,3,3,3,3,8]);line([16,3,21,3,21,8]);line([3,16,3,21,8,21]);line([16,21,21,21,21,16]);}
        else if(name==="copy"){line([8,8,20,8,20,21,8,21,8,8]);line([15,4,4,4,4,16]);}
        else if(name==="refresh"){c.beginPath();c.arc(12,12,8,.4,5.4);c.stroke();line([17,3,18,8,13,8]);}
        else if(name==="settings"){
            circle(12,12,3);c.beginPath();for(var j=0;j<=24;j++){var a=j*Math.PI/12;var r=(j%3===0)?10:8;var x=12+Math.cos(a)*r,y=12+Math.sin(a)*r;if(j===0)c.moveTo(x,y);else c.lineTo(x,y);}c.closePath();c.stroke();
        } else if(name==="lock"){line([6,10,18,10,18,21,6,21,6,10]);c.beginPath();c.arc(12,9,4,Math.PI,0);c.stroke();line([12,14,12,17]);}
    }
}
