"""Isolate PyMuPDF's GIL-holding work from the desktop process.

One lazy child serves serialized JSON-line requests and bounded RGB responses.
The desktop can share a PdfRenderer between its preview and thumbnail workers.
Only the child imports fitz; this module never imports a GUI or model library.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

MAX_BYTES = 40 * 1024 * 1024
MAX_HEADER = 128 * 1024


def _header(stream):
    line = stream.readline(MAX_HEADER + 1)
    if not line:
        raise EOFError('PDF renderer exited before returning an image')
    if len(line) > MAX_HEADER or not line.endswith(b'\n'):
        raise ValueError('Invalid PDF renderer response header')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError('Invalid PDF renderer response')
    return value


def _shape(header):
    values = tuple(header.get(name) for name in ('width', 'height', 'stride', 'length'))
    if not all(type(value) is int and value > 0 for value in values):
        raise ValueError('Invalid PDF image dimensions')
    width, height, stride, length = values
    if width > 20000 or height > 20000 or stride != width * 3 or length != stride * height or length > MAX_BYTES:
        raise ValueError('PDF image response exceeds its RGB size bounds')
    return width, height, stride, length


def _exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(min(size - len(data), 1024 * 1024))
        if not chunk:
            raise EOFError('PDF renderer returned a truncated image')
        data.extend(chunk)
    return bytes(data)


class _Session:
    def __init__(self):
        executable = Path(sys.executable)
        if executable.stem.lower() == 'pythonw':
            executable = executable.with_name('python.exe')
        self.process = subprocess.Popen(
            [str(executable), '-u', '-m', 'everytensor.pdf_render'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            cwd=Path(__file__).resolve().parent.parent,
        )
        self.responses = queue.Queue()
        self.requests = queue.Queue()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self.reader = threading.Thread(target=self._read, name='pdf-render-output', daemon=True)
        self.writer = threading.Thread(target=self._write, name='pdf-render-input', daemon=True)
        self.reader.start()
        self.writer.start()

    def _read(self):
        try:
            while True:
                header = _header(self.process.stdout)
                if 'error' in header:
                    self.responses.put((header, None))
                    continue
                _, _, _, length = _shape(header)
                self.responses.put((header, _exact(self.process.stdout, length)))
        except Exception as exc:
            self.responses.put(exc)

    def _write(self):
        try:
            while True:
                data = self.requests.get()
                if data is None:
                    return
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except Exception as exc:
            self.responses.put(exc)

    def stop(self):
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        self.requests.put(None)
        try:
            if self.process.poll() is None:
                if os.name == 'nt':
                    # Windows venv python.exe can be a redirector. Target only
                    # this renderer's known process tree, including its child.
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
                else:
                    self.process.terminate()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.reader.join(timeout=1)
        self.writer.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class PdfRenderer:
    def __init__(self, timeout=30.):
        self.timeout = float(timeout)
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._session = None
        self._closed = False
        self._serial = 0

    @property
    def pid(self):
        with self._state_lock:
            return self._session.process.pid if self._session is not None else None

    def _discard(self, session):
        with self._state_lock:
            if self._session is session:
                self._session = None
        session.stop()

    def render(self, path, page, bounds):
        """Return (width, height, RGB stride, bytes), or raise without hanging.

        Call from an existing media worker thread. A request that times out or
        violates the protocol discards its process; the next call starts fresh.
        """
        width, height = (int(value) for value in bounds)
        if width < 1 or height < 1 or width > 20000 or height > 20000:
            raise ValueError('PDF rendering bounds must be between 1 and 20000 pixels')
        request = dict(path=str(Path(path).resolve()), page=int(page), bounds=[width, height])
        with self._request_lock:
            with self._state_lock:
                if self._closed:
                    raise RuntimeError('PDF renderer is closed')
                session = self._session
                if session is None or session.process.poll() is not None:
                    if session is not None:
                        threading.Thread(target=session.stop, name='pdf-render-reap', daemon=True).start()
                    session = self._session = _Session()
                self._serial += 1
                request['id'] = self._serial
            encoded = json.dumps(request, ensure_ascii=False).encode('utf-8') + b'\n'
            if len(encoded) > MAX_HEADER:
                raise ValueError('PDF render request is too large')
            session.requests.put(encoded)
            try:
                response = session.responses.get(timeout=self.timeout)
                if isinstance(response, Exception):
                    raise response
                header, data = response
                if header.get('id') != request['id']:
                    raise ValueError('PDF renderer returned an out-of-order response')
                if 'error' not in header:
                    image_width, image_height, stride, length = _shape(header)
                    if len(data) != length:
                        raise ValueError('PDF renderer returned an invalid image length')
            except queue.Empty as exc:
                self._discard(session)
                raise TimeoutError(f'PDF rendering exceeded {self.timeout:g} seconds') from exc
            except Exception:
                self._discard(session)
                raise
            with self._state_lock:
                if self._closed:
                    raise RuntimeError('PDF renderer was closed during rendering')
            if 'error' in header:
                raise RuntimeError(str(header['error']))
            return image_width, image_height, stride, data

    def close(self):
        """Prevent new children immediately; reap the exact existing child off-GUI."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            session, self._session = self._session, None
        if session is not None:
            # Non-daemon cleanup keeps interpreter exit from abandoning a child.
            threading.Thread(target=session.stop, name='pdf-render-close').start()


def worker():
    protocol = sys.stdout.buffer
    sys.stdout = sys.stderr
    import fitz
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
    stream = sys.stdin.buffer
    while True:
        try:
            request = _header(stream)
        except EOFError:
            return
        except Exception:
            return
        serial = request.get('id')
        try:
            bounds = request['bounds']
            width, height = (int(value) for value in bounds)
            if width < 1 or height < 1 or width > 20000 or height > 20000:
                raise ValueError('Invalid PDF rendering bounds')
            with fitz.open(request['path']) as document:
                if document.needs_pass:
                    raise ValueError('Encrypted PDF requires a password')
                if not len(document):
                    raise ValueError('PDF has no pages')
                page = document[max(0, min(len(document) - 1, int(request['page']) - 1))]
                if page.rect.width <= 0 or page.rect.height <= 0:
                    raise ValueError('PDF page has invalid dimensions')
                scale = min(width / page.rect.width, height / page.rect.height)
                if math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale) * 3 > MAX_BYTES:
                    raise ValueError('PDF rendering would exceed the 40 MiB image limit')
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                header = dict(id=serial, width=pixmap.width, height=pixmap.height,
                              stride=pixmap.stride, length=pixmap.stride * pixmap.height)
                _shape(header)
                data = pixmap.samples
                if len(data) != header['length']:
                    raise ValueError('PDF renderer produced an invalid RGB image')
            protocol.write(json.dumps(header).encode('ascii') + b'\n')
            protocol.write(data)
            protocol.flush()
        except Exception as exc:
            protocol.write(json.dumps(dict(id=serial, error=str(exc)[:600])).encode('utf-8') + b'\n')
            protocol.flush()


if __name__ == '__main__':
    worker()
