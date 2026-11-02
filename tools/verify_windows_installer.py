"""Install, upgrade and uninstall the real setup in an isolated workspace path.

Uses a per-user install without shortcuts. Refuses to touch an existing
registered EveryTensor installation. All test data remains in build/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]
KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{84C1B332-EF73-4B4B-98D5-CBF2BE719471}_is1'


def registration():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return {name: winreg.QueryValueEx(key, name)[0] for name in ('DisplayName', 'DisplayVersion', 'InstallLocation')}
    except FileNotFoundError:
        return None


def execute(command, **kwargs):
    subprocess.run([str(p) for p in command], check=True, timeout=180,
                   creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    parser.add_argument('--isolated', action='store_true', help='Skip shortcuts and Windows uninstall registration in restricted build environments')
    args = parser.parse_args()
    installer = args.installer.resolve()
    if registration():
        raise SystemExit('An existing EveryTensor installation is registered; use a clean Windows account for this test.')
    root = ROOT / 'build' / 'installer-verification' / uuid.uuid4().hex[:8]
    app = root / '中文安装路径 with spaces' / 'EveryTensor'
    root.mkdir(parents=True)
    report = dict(ok=False, checks=[], installer=installer.name, isolated=args.isolated,
                  limitations=['Windows uninstall registration and Start menu are not tested'] if args.isolated else [])
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    environment['LOCALAPPDATA'] = str(root / 'LocalAppData')
    data = Path(environment['LOCALAPPDATA']) / 'EveryTensor' / '.everytensor'
    uninstall = app / 'unins000.exe'
    def install(log):
        execute([installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
                 '/NOICONS', '/TASKS=', '/LANG=chinesesimplified', '/DIR=' + str(app), '/LOG=' + str(root / log)]
                + (['/ISOLATED=1'] if args.isolated else []))
    try:
        install('install.log')
        registered = registration()
        if args.isolated:
            assert uninstall.is_file() and not registered
            report['checks'].append('isolated_install_with_local_uninstaller')
        else:
            assert registered and Path(registered['InstallLocation']).resolve() == app.resolve()
            report['checks'].append('per_user_install_and_uninstall_registration')
        execute([app / 'EveryTensor-console.exe', '--doctor'], env=environment, capture_output=True)
        assert (data / 'index.sqlite3').is_file(), 'Default data was not written under LOCALAPPDATA'
        sentinel = data / 'preserve-on-upgrade.txt'
        sentinel.write_text('user data must survive', 'utf-8')
        report['checks'].append('writable_per_user_data_without_python_on_path')
        execute([app / 'EveryTensor-console.exe', '--self-test', root / 'installed-self-test.json'], env=environment)
        result = json.loads((root / 'installed-self-test.json').read_text('utf-8'))
        assert result['ok'] and result['frozen'], result
        report['checks'].append('installed_unicode_path_search_semantics_pdf_qml')
        # Exercise the windowless entry point too, with an explicit report.
        execute([app / 'EveryTensor.exe', '--self-test', root / 'gui-self-test.json'], env=environment)
        assert json.loads((root / 'gui-self-test.json').read_text('utf-8'))['ok']
        report['checks'].append('windowless_entry_point')
        install('upgrade.log')
        assert sentinel.read_text('utf-8') == 'user data must survive'
        execute([app / 'EveryTensor-console.exe', '--doctor'], env=environment, capture_output=True)
        report['checks'].append('reinstall_preserves_data_and_starts')
        execute([uninstall, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(root / 'uninstall.log')])
        deadline = time.monotonic() + 30
        while (uninstall.exists() or registration()) and time.monotonic() < deadline:
            time.sleep(.2)
        assert not registration(), 'Uninstall registry entry remains'
        assert not (app / 'EveryTensor.exe').exists(), 'Application binary remains'
        assert sentinel.read_text('utf-8') == 'user data must survive'
        assert (data / 'index.sqlite3').exists()
        report['checks'].append('uninstall_removes_app_and_preserves_user_data')
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        # Clean up only this test's verified installation, never any other app.
        registered = registration()
        owns_registration = registered and Path(registered['InstallLocation']).resolve() == app.resolve()
        if (owns_registration or args.isolated) and uninstall.exists():
            execute([uninstall, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
        (root / 'verification.json').write_text(json.dumps(report, indent=2), 'utf-8')
        output = ROOT / 'release' / 'windows' / 'installer-verification.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
