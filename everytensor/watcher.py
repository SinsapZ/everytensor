"""Native Windows directory notifications with bounded queues owned by the caller."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import threading


class DirectoryWatcher:
    def __init__(self, root, changed):
        self.root, self.changed = root, changed
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.active = False
        self.thread = threading.Thread(target=self._run, name='directory-watch', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)

    def _run(self):
        if os.name != 'nt':
            self.ready.set()
            return
        import ctypes as c
        from ctypes import wintypes as w
        class Overlapped(c.Structure):
            _fields_ = [('Internal', c.c_size_t), ('InternalHigh', c.c_size_t),
                        ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]
        k = c.WinDLL('kernel32', use_last_error=True)
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        k.CreateEventW.restype = w.HANDLE
        k.ReadDirectoryChangesW.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, w.BOOL, w.DWORD,
                                          c.POINTER(w.DWORD), c.POINTER(Overlapped), c.c_void_p]
        k.GetOverlappedResult.argtypes = [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL]
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.CancelIoEx.argtypes = [w.HANDLE, c.POINTER(Overlapped)]
        k.ResetEvent.argtypes = [w.HANDLE]
        k.CloseHandle.argtypes = [w.HANDLE]
        handle = k.CreateFileW(self.root, 1, 7, None, 3, 0x02000000 | 0x40000000, None)
        if handle == c.c_void_p(-1).value:
            self.ready.set()
            return
        event = k.CreateEventW(None, True, False, None)
        if not event:
            k.CloseHandle(handle)
            self.ready.set()
            return
        buffer = (w.DWORD * (65536//4))()
        pending = False
        try:
            while not self.stop.is_set():
                overlap = Overlapped(hEvent=event)
                k.ResetEvent(event)
                if not k.ReadDirectoryChangesW(handle, buffer, 65536, True, 0x1B, None, c.byref(overlap), None):
                    if c.get_last_error() != 997:
                        break
                pending = True
                self.active = True
                self.ready.set()
                while k.WaitForSingleObject(event, 200) == 258:
                    if self.stop.is_set():
                        k.CancelIoEx(handle, c.byref(overlap))
                        break
                count = w.DWORD()
                ok = k.GetOverlappedResult(handle, c.byref(overlap), c.byref(count), True)
                pending = False
                if self.stop.is_set():
                    break
                if not ok or not count.value:
                    self.changed(self.root, None)
                    if not ok:
                        break
                    continue
                data, offset = c.string_at(buffer, count.value), 0
                while offset+12 <= len(data):
                    step, action, size = struct.unpack_from('<III', data, offset)
                    name = data[offset+12:offset+12+size].decode('utf-16-le', 'replace')
                    self.changed(self.root, str(Path(self.root)/name))
                    if not step:
                        break
                    offset += step
        finally:
            if pending:
                k.CancelIoEx(handle, c.byref(overlap))
                k.GetOverlappedResult(handle, c.byref(overlap), c.byref(w.DWORD()), True)
            self.active = False
            self.ready.set()
            k.CloseHandle(event)
            k.CloseHandle(handle)
