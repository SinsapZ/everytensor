"""Local JSON-lines worker transport, independent of either desktop toolkit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from . import media


class Backend:
    def __init__(self, db_path, events):
        log_path = Path(db_path).parent/'backend.log'
        if log_path.exists() and log_path.stat().st_size > 4*1024*1024:
            log_path.replace(log_path.with_suffix('.previous.log'))
        self.log = log_path.open('a', encoding='utf-8')
        self.closing = False
        self._outgoing = queue.Queue()
        executable = Path(sys.executable)
        if executable.stem.lower() == 'pythonw':
            executable = executable.with_name('python.exe')
        self.process = subprocess.Popen([str(executable), '-u', '-m', 'everytensor.engine', str(db_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', bufsize=1, creationflags=media.FLAGS,
            cwd=Path(__file__).resolve().parent.parent)

        def read():
            try:
                for line in self.process.stdout:
                    try:
                        events.put(json.loads(line))
                    except (ValueError, TypeError):
                        pass
            finally:
                if not self.closing:
                    events.put({'type': 'fatal', 'text': '搜索后台已退出，请重新打开应用。'})
        self.reader = threading.Thread(target=read, daemon=True, name='desktop-worker-output')
        self.reader.start()

        def write():
            while True:
                command = self._outgoing.get()
                if command is None:
                    return
                try:
                    self.process.stdin.write(json.dumps(command, ensure_ascii=False)+'\n')
                    self.process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    return
        # Even a stopped child or full OS pipe cannot stall a Qt input event.
        self.writer = threading.Thread(target=write, daemon=True, name='desktop-worker-input')
        self.writer.start()

    def put(self, command):
        if not self.closing:
            self._outgoing.put(command)

    def close(self, *, wait=False):
        if self.closing:
            return
        self.put({'action': 'stop'})
        self.closing = True
        self._outgoing.put(None)

        def finish():
            try:
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                            capture_output=True, creationflags=media.FLAGS, timeout=5)
                    else:
                        self.process.terminate()
                    self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                except OSError:
                    pass
            finally:
                self.writer.join(timeout=2)
                self.reader.join(timeout=2)
                for handle in (self.process.stdin, self.process.stdout, self.log):
                    try:
                        handle.close()
                    except (OSError, ValueError):
                        pass
        if wait:
            finish()
        else:
            # Non-daemon: Python waits for deterministic child cleanup after the
            # window closes, without blocking the GUI event loop for ten seconds.
            threading.Thread(target=finish, name='desktop-worker-close').start()


class NullBackend:
    """Explicitly disabled transport for isolated client tests and screenshots."""
    def __init__(self):
        self.messages = []

    def put(self, command):
        self.messages.append(command)

    def close(self, **kwargs):
        pass
