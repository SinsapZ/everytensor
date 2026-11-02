"""Separate bundled, read-only resources from each user's writable state."""
from pathlib import Path
import os
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def resource_root():
    if os.environ.get('EVERYTENSOR_RESOURCE_ROOT'):
        return Path(os.environ['EVERYTENSOR_RESOURCE_ROOT']).resolve()
    return Path(__file__).resolve().parent.parent


def default_data():
    if frozen() and sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        return local / 'EveryTensor' / '.everytensor'
    return resource_root() / '.everytensor'


def worker_command(kind, *args):
    if kind not in ('engine', 'pdf'):
        raise ValueError('Unknown worker: ' + kind)
    executable = Path(sys.executable)
    if kind == 'engine' and args:
        from .model_install import installed_configuration, worker_entry
        installed = installed_configuration(Path(args[0]).parent)
        if installed:
            return [str(installed['python']), '-u', str(worker_entry()), str(resource_root()), *map(str, args)]
    if frozen():
        # A console-subsystem helper retains stdin/stdout for the JSON/binary
        # protocols. The parent always starts it with CREATE_NO_WINDOW.
        command = [str(executable.with_name('EveryTensor-console.exe')), '--worker', kind]
    else:
        if executable.stem.lower() == 'pythonw':
            executable = executable.with_name('python.exe')
        module = 'everytensor.engine' if kind == 'engine' else 'everytensor.pdf_render'
        command = [str(executable), '-u', '-m', module]
    return command + [str(arg) for arg in args]
