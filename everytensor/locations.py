"""Shared hit locations and shell-free external application commands."""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys


def label(loc):
    if 'page' in loc:
        return f"第 {loc['page']} 页"
    if 'slide' in loc:
        return f"第 {loc['slide']} 张幻灯片" + (' · 备注' if loc.get('part') == 'notes' else '')
    if 'sheet' in loc:
        return f"{loc['sheet']} · 第 {loc.get('row', 1)} 行"
    if 'table' in loc:
        return f"表格 {loc['table']} · 第 {loc.get('row', 1)} 行"
    if 'paragraph' in loc:
        return f"第 {loc['paragraph']} 段"
    if 'chapter' in loc:
        return f"第 {loc['chapter']} 章"
    if 'start' in loc:
        from .media import timestamp
        return timestamp(loc['start']) + ('–'+timestamp(loc['end']) if 'end' in loc else '')
    if 'line' in loc:
        return f"第 {loc['line']} 行起"
    return ''


def reference(row):
    where = label(row.get('location') or {})
    return str(row['path']) + (' · '+where if where else '')


def find_app(*names):
    for name in names:
        if name in ('ffplay', 'ffplay.exe'):
            from .media import executable
            managed = executable('ffplay')
            if Path(managed).is_file():
                return managed
        found = shutil.which(name)
        if found and Path(found).suffix.lower() not in ('.cmd', '.bat'):
            return found
        if os.name == 'nt':
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\'+name) as key:
                        value = winreg.QueryValue(key, None).strip('"')
                        if Path(value).is_file():
                            return value
                except OSError:
                    pass
    return None


@dataclass(frozen=True)
class OpenPlan:
    command: tuple[str, ...] | None
    positioned: bool


def plan(row):
    path = str(Path(row['path']).resolve())
    loc = row.get('location') or {}
    kind = row.get('kind')
    if kind in ('video', 'audio'):
        second = str(max(0., float(loc.get('start', 0))))
        if app := find_app('mpv.exe', 'mpv'):
            return OpenPlan((app, '--start='+second, '--', path), True)
        if app := find_app('vlc.exe', 'vlc'):
            return OpenPlan((app, '--start-time='+second, '--', path), True)
        if app := find_app('ffplay.exe', 'ffplay'):
            return OpenPlan((app, '-loglevel', 'error', '-autoexit', '-ss', second, '-i', path), True)
    if Path(path).suffix.lower() == '.pdf' and 'page' in loc:
        page = str(max(1, int(loc['page'])))
        if app := find_app('SumatraPDF.exe'):
            return OpenPlan((app, '-page', page, path), True)
        if app := find_app('AcroRd32.exe', 'Acrobat.exe'):
            return OpenPlan((app, '/A', 'page='+page, path), True)
        if app := find_app('msedge.exe', 'chrome.exe', 'firefox.exe', 'firefox', 'chromium', 'google-chrome'):
            return OpenPlan((app, Path(path).as_uri()+'#page='+page), True)
        if app := find_app('okular'):
            return OpenPlan((app, '--page', page, path), True)
        if app := find_app('evince'):
            return OpenPlan((app, '--page-label='+page, path), True)
    if kind == 'code' and 'line' in loc:
        line = max(1, int(loc['line']))
        if app := find_app('Code.exe', 'Cursor.exe', 'code', 'cursor'):
            return OpenPlan((app, '--goto', path+f':{line}:1'), True)
    return OpenPlan(None, not bool(label(loc)))


def open_hit(row):
    """Return a visible fallback explanation when exact positioning is unavailable."""
    path = str(Path(row['path']).resolve())
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    if os.name == 'nt' and Path(path).suffix.lower() in ('.docx', '.pptx', '.xlsx') and row.get('location'):
        import json
        # A fixed script receives JSON through stdin. Paths and document text
        # are never interpolated into PowerShell code or a shell command.
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                     '-File', str(Path(__file__).with_name('office_open.ps1'))],
                                    input=json.dumps(row, ensure_ascii=False), encoding='utf-8',
                                    capture_output=True, timeout=35,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0:
                return ''
        except (OSError, subprocess.TimeoutExpired):
            pass
    target = plan(row)
    if target.command:
        subprocess.Popen(list(target.command), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    elif os.name == 'nt':
        os.startfile(path)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])
    return '' if target.positioned else '已打开原文件；当前外部应用无法直接跳转，请按预览中的位置查看：'+label(row.get('location') or {})
