"""Qt Quick entry point. Search and indexing remain in the local Python engine."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Property, Slot, QUrl, Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap, QPainter, QLinearGradient, QColor
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog

from .client import Controller
from .preview_service import ImageStore, ThumbnailProvider, PreviewService
from .pdf_render import PdfRenderer


def reduced_motion():
    if os.environ.get('EVERYTENSOR_REDUCED_MOTION', '').lower() in ('1', 'true', 'yes'):
        return True
    if sys.platform == 'win32':
        import ctypes
        enabled = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    return False


def application_icon():
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    red = QLinearGradient(12, 10, 85, 90)
    red.setColorAt(0, QColor('#da9985'))
    red.setColorAt(1, QColor('#a43d35'))
    painter.setBrush(red)
    painter.drawEllipse(8, 8, 80, 80)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    gray = QLinearGradient(45, 45, 116, 124)
    gray.setColorAt(0, QColor('#4d5148'))
    gray.setColorAt(1, QColor('#92998a'))
    painter.setBrush(gray)
    painter.drawEllipse(40, 40, 80, 80)
    painter.end()
    return QIcon(pixmap)


class DesktopActions(QObject):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._reduced = reduced_motion()
        families = set(QFontDatabase.families())
        self._family = next((font for font in ('Microsoft YaHei UI', 'Noto Sans CJK SC', 'Segoe UI')
                             if font in families), app.font().family())

    reducedMotion = Property(bool, lambda s: s._reduced, constant=True)
    fontFamily = Property(str, lambda s: s._family, constant=True)

    @Slot(result=str)
    def chooseFolder(self):
        return QFileDialog.getExistingDirectory(None, '选择文件夹', str(Path.home()))

    @Slot(str)
    def copyPath(self, path):
        self.app.clipboard().setText(path)


@dataclass
class DesktopRuntime:
    app: QApplication
    engine: QQmlApplicationEngine
    client: Controller
    preview: PreviewService
    desktop: DesktopActions
    window: object
    warnings: list

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self.preview.close()
        self.client.close()
        # QML bindings must be destroyed while their Python context objects are
        # still alive. This also makes isolated window tests release log files.
        from shiboken6 import delete, isValid
        if isValid(self.engine):
            delete(self.engine)
        if getattr(self, '_log_handler', None):
            self._logger.removeHandler(self._log_handler)
            self._log_handler.close()


def create_desktop(data, *, start_backend=True, restore_geometry=True):
    data = Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle('Basic')
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('EveryTensor')
    app.setOrganizationName('EveryTensor')
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(True)
    handler = RotatingFileHandler(data / 'desktop.log', maxBytes=2 * 1024 * 1024,
                                  backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    log = logging.getLogger('everytensor')
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    engine = QQmlApplicationEngine()
    desktop = DesktopActions(app, engine)
    font = QFont(desktop.fontFamily)
    font.setPixelSize(14)
    app.setFont(font)
    client = Controller(data, start_backend=start_backend)
    images = ImageStore()
    pdf_renderer = PdfRenderer()
    thumbnails = ThumbnailProvider(pdf_renderer)
    preview = PreviewService(data / 'index.sqlite3', images, engine, pdf_renderer=pdf_renderer)
    client.previewRequested.connect(lambda payload: preview.setRow(payload.get('row') or {}))
    preview.locationChanged.connect(client.setSelectedLocation)
    preview.locationResolved.connect(client.resolveSelectedLocation)
    preview.openRequested.connect(client.openSelected)
    engine.addImageProvider('preview', images)
    engine.addImageProvider('thumb', thumbnails)
    context = engine.rootContext()
    context.setContextProperty('client', client)
    context.setContextProperty('preview', preview)
    context.setContextProperty('desktop', desktop)
    warnings = []
    def qml_warnings(items):
        for item in items:
            warnings.append(item.toString())
            log.warning('QML %s', item.toString())
    engine.warnings.connect(qml_warnings)
    qml = Path(__file__).with_name('qml') / 'Main.qml'
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        preview.close()
        client.close()
        raise RuntimeError('Qt Quick 界面加载失败：' + '\n'.join(warnings))
    window = engine.rootObjects()[0]
    preview.setScale(window.devicePixelRatio())
    window.screenChanged.connect(lambda _: preview.setScale(window.devicePixelRatio()))
    runtime = DesktopRuntime(app, engine, client, preview, desktop, window, warnings)
    runtime._logger, runtime._log_handler = log, handler
    # Retain providers while Qt owns their C++ objects.
    runtime.images, runtime.thumbnails = images, thumbnails
    state_path = data / 'desktop-window.json'
    if restore_geometry:
        try:
            state = json.loads(state_path.read_text('utf-8')) if state_path.is_file() else {
                'width': window.width(), 'height': window.height()}
            screen = window.screen().availableGeometry()
            window.setWidth(max(920, min(screen.width(), int(state['width']))))
            window.setHeight(max(620, min(screen.height(), int(state['height']))))
            if 'list_width' in state:
                window.setProperty('preferredListWidth', max(270., min(540., float(state['list_width']))))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    def finish():
        if getattr(runtime, '_closed', False):
            return
        if restore_geometry:
            try:
                state_path.write_text(json.dumps({'width': window.width(), 'height': window.height(),
                                                  'list_width': window.property('preferredListWidth')}), 'utf-8')
            except OSError:
                log.warning('Could not save window size', exc_info=True)
        runtime.close()
    app.aboutToQuit.connect(finish)
    return runtime


def run(data):
    runtime = create_desktop(data)
    return runtime.app.exec()
