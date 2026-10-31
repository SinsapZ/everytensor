"""Asynchronous, bounded media previews for the Qt Quick desktop.

The scene graph only receives ready QImages. PDF parsing, disk access and
FFmpeg stay outside the GUI thread; a request token rejects late completions.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from . import media, store
from .pdf_render import PdfRenderer

log = logging.getLogger(__name__)


def qimage(image):
    image = image.convert('RGBA')
    # QImage must own its bytes after the worker's PIL buffer is released.
    return QImage(image.tobytes(), image.width, image.height,
                  image.width * 4, QImage.Format.Format_RGBA8888).copy()


def pdf_image(renderer, path, page, bounds):
    width, height, stride, pixels = renderer.render(str(path), page, bounds)
    return QImage(pixels, width, height, stride, QImage.Format.Format_RGB888).copy()


class ImageStore(QQuickImageProvider):
    def __init__(self, budget=48 * 1024 * 1024):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.images = OrderedDict()
        self.lock = threading.RLock()
        self.budget, self.used = budget, 0

    def put(self, key, image):
        with self.lock:
            old = self.images.pop(key, None)
            if old is not None:
                self.used -= old.sizeInBytes()
            self.images[key] = image
            self.used += image.sizeInBytes()
            while self.used > self.budget and len(self.images) > 1:
                _, old = self.images.popitem(last=False)
                self.used -= old.sizeInBytes()
        return 'image://preview/' + key

    def requestImage(self, image_id, size, requested_size):
        with self.lock:
            image = self.images.get(image_id.split('?')[0])
            if image is None:
                return QImage()
            self.images.move_to_end(image_id.split('?')[0])
            if size is not None:
                size.setWidth(image.width())
                size.setHeight(image.height())
            return image


class ThumbnailProvider(QQuickImageProvider):
    """Qt's image-loading thread requests only visible delegate thumbnails."""
    def __init__(self, pdf_renderer):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self.images = OrderedDict()
        self.lock = threading.RLock()
        self.pdf_renderer = pdf_renderer

    def requestImage(self, image_id, size, requested_size):
        try:
            with self.lock:
                image = self.images.get(image_id)
            if image is None:
                value = image_id.split('?')[0]
                info = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
                path, length, modified = info
                current = Path(path).stat()
                if (current.st_size, current.st_mtime_ns) != (length, modified):
                    return QImage()
                if Path(path).suffix.lower() == '.pdf':
                    image = pdf_image(self.pdf_renderer, path, 1, (176, 132))
                else:
                    location = {'start': 1.0} if media.kind(path) == 'video' else {}
                    image = qimage(media.preview_image(path, location, (176, 132)))
                with self.lock:
                    self.images[image_id] = image
                    self.images.move_to_end(image_id)
                    while len(self.images) > 96:
                        self.images.popitem(last=False)
            if size is not None:
                size.setWidth(image.width())
                size.setHeight(image.height())
            return image
        except Exception:
            return QImage()


class PreviewService(QObject):
    changed = Signal()
    completed = Signal(int, object)
    frameCompleted = Signal(int, object)
    locationChanged = Signal(dict)
    locationResolved = Signal(dict)
    openRequested = Signal()

    def __init__(self, database, images, parent=None, pdf_renderer=None):
        super().__init__(parent)
        self.database, self.images = Path(database), images
        self.pdf_renderer = pdf_renderer or PdfRenderer()
        self._row = {}
        self._token = 0
        self._bounds = (720, 650)
        self._scale = 1.
        self._fit = True
        self._source = self._caption = self._text = self._error = ''
        self._width = self._height = 0
        self._loading = False
        self._duration = self._position = 0.
        self._frames = []
        self._closed = False
        self._cancel = threading.Event()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix='qt-preview')
        self._frame_pool = ThreadPoolExecutor(2, thread_name_prefix='qt-filmstrip')
        self._jobs = []
        self._frame_identity = None
        self.completed.connect(self._complete)
        self.frameCompleted.connect(self._frame_complete)
        self._resize = QTimer(self)
        self._resize.setSingleShot(True)
        self._resize.setInterval(100)
        self._resize.timeout.connect(self._request)

    source = Property(str, lambda s: s._source, notify=changed)
    caption = Property(str, lambda s: s._caption, notify=changed)
    text = Property(str, lambda s: s._text, notify=changed)
    error = Property(str, lambda s: s._error, notify=changed)
    loading = Property(bool, lambda s: s._loading, notify=changed)
    width = Property(int, lambda s: s._width, notify=changed)
    height = Property(int, lambda s: s._height, notify=changed)
    frames = Property('QVariantList', lambda s: s._frames, notify=changed)
    duration = Property(float, lambda s: s._duration, notify=changed)
    position = Property(float, lambda s: s._position, notify=changed)
    fitWidth = Property(bool, lambda s: s._fit, notify=changed)

    @Slot('QVariantMap')
    def setRow(self, row):
        row = dict(row or {})
        # Keep a user-selected time while only ranking/evidence is refreshed.
        same = row.get('file_id') == self._row.get('file_id') and row.get('path') == self._row.get('path')
        if row == self._row and self._source:
            return
        self._row = row
        if not same:
            self._source = self._text = self._error = self._caption = ''
            self._width = self._height = 0
            self._duration = self._position = 0.
            self._frames, self._frame_identity = [], None
        self._request()

    @Slot(int, int)
    def setBounds(self, width, height):
        bounds = (max(160, min(2400, width)), max(100, min(2000, height)))
        if bounds != self._bounds:
            self._bounds = bounds
            if self._row:
                self._resize.start()

    @Slot(bool)
    def setFitWidth(self, value):
        if value != self._fit:
            self._fit = value
            self.changed.emit()
            self._request()

    def setScale(self, value):
        value = max(1., min(2., float(value)))
        if value != self._scale:
            self._scale = value
            if self._row:
                self._resize.start()

    @Slot(float)
    def seek(self, second):
        if not self._row or self._row.get('kind') not in ('video', 'audio'):
            return
        second = max(0., min(max(0., self._duration - .05), second))
        loc = dict(self._row.get('location', {}), start=second,
                   end=min(self._duration, second + 8), duration=self._duration)
        self.locationChanged.emit(loc)

    @Slot()
    def open(self):
        self.openRequested.emit()

    def _request(self):
        self._resize.stop()
        self._token += 1
        self._cancel.set()
        self._cancel = cancel = threading.Event()
        for job in self._jobs:
            job.cancel()
        self._jobs.clear()
        if self._closed or not self._row:
            self._loading = False
            self.changed.emit()
            return
        token, row = self._token, dict(self._row)
        self._loading, self._error = True, ''
        self.changed.emit()
        bounds = tuple(min(3200, round(size * self._scale)) for size in self._bounds)
        if Path(row['path']).suffix.lower() == '.pdf' and self._fit:
            bounds = (bounds[0], 4000)
        future = self._pool.submit(self._render, row, bounds, cancel)
        self._jobs.append(future)
        def ready(job):
            if cancel.is_set() or self._closed:
                return
            try:
                result = job.result()
            except Exception as exc:
                log.info('Preview unavailable: %s', exc)
                result = {'error': '无法预览此文件，仍可用默认应用打开。'}
            self.completed.emit(token, result)
        future.add_done_callback(ready)

    def _render(self, row, bounds, cancel):
        path = Path(row['path'])
        current = path.stat()
        version = (current.st_size, current.st_mtime_ns)
        loc, text = dict(row.get('location') or {}), row.get('text') or ''
        if not loc or (not text and row.get('kind') in ('document', 'code')):
            db = store.reader(self.database)
            try:
                unit = db.execute('SELECT text,location FROM units WHERE file_id=? ORDER BY ordinal LIMIT 1',
                                  (row['file_id'],)).fetchone()
                if unit:
                    if not loc:
                        loc = json.loads(unit['location'])
                    if not text:
                        text = unit['text']
            finally:
                db.close()
        if cancel.is_set():
            return {}
        result = {'text': text, 'location': loc, 'version': version, 'path': str(path)}
        if row.get('kind') in ('video', 'audio') and not loc.get('duration'):
            loc['duration'] = media.probe(path)
        if row.get('kind') == 'audio':
            result['text'] = '音频内容 · 从命中位置播放'
        if path.suffix.lower() == '.pdf':
            result['image'] = pdf_image(self.pdf_renderer, path, loc.get('page', 1), bounds)
        elif row.get('kind') in ('image', 'video'):
            result['image'] = qimage(media.preview_image(path, loc, bounds))
        elif row.get('kind') == 'audio':
            result['text'] = '音频内容 · 从命中位置播放'
        elif not text and row.get('kind') in ('document', 'code'):
            try:
                text, default_loc = next(media.extract(path))
                result.update(text=text, location=loc or default_loc)
            except StopIteration:
                result['text'] = '此文件没有可显示的正文。'
        elif not text:
            result['text'] = '使用默认应用打开此文件。'
        if path.stat().st_mtime_ns != version[1] or path.stat().st_size != version[0]:
            raise ValueError('Source changed while rendering')
        return result

    @Slot(int, object)
    def _complete(self, token, result):
        if token != self._token or self._closed:
            return
        self._loading = False
        self._error = result.get('error', '')
        if self._error:
            self._source = ''
            self._text = ''
            self.changed.emit()
            return
        self._text = result.get('text', '')
        loc = result.get('location', {})
        if loc != self._row.get('location', {}):
            self._row['location'] = dict(loc)
            self.locationResolved.emit(dict(loc))
        self._duration = float(loc.get('duration', 0))
        self._position = float(loc.get('start', 0))
        from .locations import label
        self._caption = label(loc)
        image = result.get('image')
        if image is not None:
            self._text = ''
            self._width, self._height = image.width(), image.height()
            self._source = self.images.put('main-' + str(token), image)
        else:
            self._source = ''
        self.changed.emit()
        if self._row.get('kind') == 'video':
            identity = (result['path'], result['version'])
            # Scrubbing does not restart eight already completed decodes.
            if identity != self._frame_identity or len(self._frames) != 8:
                self._frame_identity = identity
                self._frames = []
                self._make_frames(token, identity, self._duration, self._cancel)

    def _make_frames(self, token, identity, duration, cancel):
        path, version = identity
        def decode(index):
            if cancel.is_set():
                return None
            second = max(0., duration * index / 8)
            image = qimage(media.preview_image(path, {'start': second}, (176, 100)))
            return index, second, image
        for index in range(8):
            future = self._frame_pool.submit(decode, index)
            self._jobs.append(future)
            def ready(job):
                if cancel.is_set() or self._closed:
                    return
                try:
                    value = job.result()
                    if value is not None:
                        self.frameCompleted.emit(token, value)
                except Exception:
                    log.debug('Filmstrip frame unavailable', exc_info=True)
            future.add_done_callback(ready)

    @Slot(int, object)
    def _frame_complete(self, token, value):
        if token != self._token or self._closed:
            return
        index, second, image = value
        source = self.images.put(f'frame-{token}-{index}', image)
        self._frames.append({'index': index, 'second': second,
                             'label': media.timestamp(second), 'source': source})
        self._frames.sort(key=lambda f: f['index'])
        self.changed.emit()

    def close(self):
        self._closed = True
        self._resize.stop()
        self._cancel.set()
        self.pdf_renderer.close()
        for future in self._jobs:
            future.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._frame_pool.shutdown(wait=False, cancel_futures=True)
