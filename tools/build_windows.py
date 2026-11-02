"""Build a self-contained x64 desktop, installer, source archive and checksums.

Run with the isolated Python 3.11 environment created by build-windows.cmd.
Only the pinned lightweight text model is included. No personal index is read.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from everytensor import RELEASE_VERSION

BUILD = ROOT / 'build'
ASSETS = BUILD / 'windows-assets'
PAYLOAD = ROOT / 'dist' / 'EveryTensor'
OUTPUT = ROOT / 'release' / 'windows'
INNO_URL = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
INNO_SHA256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, **kwargs):
    print('Running:', ' '.join(str(arg) for arg in command), flush=True)
    subprocess.run([str(arg) for arg in command], cwd=ROOT, check=True, **kwargs)


def compiler(explicit):
    if explicit:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    path = BUILD / 'toolchain' / 'inno' / 'ISCC.exe'
    if path.is_file():
        return path
    installer = path.parent.parent / 'innosetup-6.7.3.exe'
    installer.parent.mkdir(parents=True, exist_ok=True)
    if not installer.is_file() or sha256(installer) != INNO_SHA256:
        print('Downloading pinned Inno Setup compiler...', flush=True)
        with urllib.request.urlopen(INNO_URL, timeout=120) as response:
            installer.write_bytes(response.read())
    if sha256(installer) != INNO_SHA256:
        raise RuntimeError('Inno Setup download checksum mismatch')
    run([installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
         '/PORTABLE=1', '/DIR=' + str(path.parent)], timeout=120)
    if not path.is_file():
        raise RuntimeError('Inno Setup compiler was not extracted')
    return path


def prepare_assets():
    from everytensor.quick_embeddings import QuickEmbedder
    model = QuickEmbedder()
    model.encode(query='安装包验证')
    del model
    ASSETS.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from everytensor.desktop import application_icon
    from PIL import Image
    app = QApplication.instance() or QApplication([])
    icon = ASSETS / 'EveryTensor.png'
    if not application_icon().pixmap(128, 128).save(str(icon)):
        raise RuntimeError('Could not export application icon')
    with Image.open(icon) as source:
        source.save(ASSETS / 'EveryTensor.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)])
    version = f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers=(0,1,0,3), prodvers=(0,1,0,3), mask=0x3f,
                       flags=0x2, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)),
      kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'EveryTensor contributors'),
        StringStruct('FileDescription', 'EveryTensor local file search'),
        StringStruct('FileVersion', '{RELEASE_VERSION}'),
        StringStruct('ProductName', 'EveryTensor'),
        StringStruct('ProductVersion', '{RELEASE_VERSION}'),
        StringStruct('LegalCopyright', 'Copyright (C) 2026 EveryTensor contributors')
      ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])'''
    (ASSETS / 'version.txt').write_text(version, 'utf-8')
    worker = ASSETS / 'model-worker'
    worker.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'tools/model_worker_entry.py', worker / 'model_worker_entry.py')
    with zipfile.ZipFile(worker / 'application.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / 'everytensor').glob('*.py')):
            archive.write(path, path.relative_to(ROOT))


def source_archive():
    path = OUTPUT / f'everytensor-{RELEASE_VERSION}-source.zip'
    files = [p for p in ROOT.iterdir() if p.is_file() and
             (p.suffix in ('.md', '.txt', '.cmd') or p.name in ('LICENSE', '.gitignore'))
             and p.name != '产品设计.txt']
    for folder in ('everytensor', 'tools', 'packaging', '.github'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, Path(f'everytensor-{RELEASE_VERSION}') / file.relative_to(ROOT))
    return path


def bundle_notices(source):
    licenses = PAYLOAD / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    distributions = sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower())
    packages = {}
    for dist in distributions:
        name = dist.metadata['Name']
        packages[name] = dist.version
        for file in dist.files or []:
            if not any(any(word in part.lower() for word in ('license', 'copying', 'notice', 'copyright'))
                       for part in file.parts):
                continue
            original = Path(dist.locate_file(file))
            if not original.is_file() or '..' in file.parts:
                continue
            target = licenses / name / Path(file)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    shutil.copy2(ROOT / 'packaging/windows/model-LICENSE.txt', licenses / 'quick-model-Apache-2.0.txt')
    shutil.copy2(ROOT / 'packaging/windows/InnoSetup-LICENSE.txt', licenses / 'InnoSetup-LICENSE.txt')
    shutil.copytree(ROOT / 'packaging/windows/qt-licenses', licenses / 'Qt', dirs_exist_ok=True)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md'):
        shutil.copy2(ROOT / name, PAYLOAD / name)
    shutil.copy2(source, PAYLOAD / 'corresponding-source.zip')
    manifest = dict(version=RELEASE_VERSION, python=platform.python_version(),
                    architecture=platform.machine(), packages=packages,
                    features=['filename-search', 'content-search', 'quick-text-semantics', 'pdf-preview', 'optional-multimodal-setup'],
                    optional_components=json.loads((ROOT / 'everytensor/model-assets.json').read_text('utf-8')),
                    source_sha256=sha256(source),
                    model=json.loads((ROOT / 'models/quick-semantic/manifest.json').read_text('utf-8')))
    (PAYLOAD / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), 'utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc', help='Use an existing Inno Setup 6.7+ ISCC.exe')
    parser.add_argument('--reuse-bundle', action='store_true', help='Skip freezing; rerun notices, self-test and installer')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build on Windows x64 with Python 3.11 x64')
    if sys.version_info[:2] != (3, 11):
        raise SystemExit('Release builds require Python 3.11')
    if (Path(sys.prefix) / 'pyvenv.cfg').read_text().lower().find('include-system-site-packages = false') < 0:
        raise SystemExit('Use an isolated venv without system site packages')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    iscc = compiler(args.iscc)
    prepare_assets()
    source = source_archive()
    if not args.reuse_bundle:
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
             '--distpath', ROOT / 'dist', '--workpath', BUILD / 'pyinstaller',
             ROOT / 'packaging/windows/EveryTensor.spec'])
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            local = ROOT.joinpath(*Path(entry.filename).parts[1:])
            if local.read_bytes() != archive.read(entry):
                raise RuntimeError('Source changed during build; rebuild before distributing: ' + str(local))
    bundle_notices(source)
    # Strip developer Python paths from the launch environment. The installed
    # executable and both real workers must resolve everything from the bundle.
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    report = OUTPUT / 'self-test.json'
    run([PAYLOAD / 'EveryTensor-console.exe', '--self-test', report], env=environment, timeout=180)
    if not json.loads(report.read_text('utf-8'))['ok']:
        raise RuntimeError('Bundle self-test failed')
    run([iscc, '/DAppVersion=' + RELEASE_VERSION, '/DPayloadDir=' + str(PAYLOAD),
         '/DOutputDir=' + str(OUTPUT), ROOT / 'packaging/windows/installer.iss'])
    installer = OUTPUT / f'EveryTensor-{RELEASE_VERSION}-windows-x64-setup.exe'
    for path in (installer, source):
        path.with_suffix(path.suffix + '.sha256').write_text(f'{sha256(path)}  {path.name}\n', 'ascii')
    shutil.copy2(PAYLOAD / 'build-manifest.json', OUTPUT / 'build-manifest.json')
    print(f'Windows installer ready: {installer} ({installer.stat().st_size / 1024**2:.1f} MiB)', flush=True)


if __name__ == '__main__':
    main()
